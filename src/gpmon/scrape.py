"""Scrape Google organic (incl. AI Overview), Google AI Mode and optionally ChatGPT via DataForSEO.

Writes one gzipped JSONL file per run: data/raw/run_<YYYY-MM-DD>.jsonl.gz
Each line: {engine, city, location_code, keyword, volume, prompt?, run_date, fetched_at, cost, task, error}

Usage:
  python -m gpmon.scrape                      # full run, today's UTC date
  python -m gpmon.scrape --limit 4            # smoke test: first 4 jobs only
  python -m gpmon.scrape --date 2026-11-09    # override run date label
  python -m gpmon.scrape --queue live         # Live endpoints (fast, about 3x the price)
Env: DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD
"""
import argparse
import base64
import datetime as dt
import gzip
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import config

API = 'https://api.dataforseo.com'
CHATGPT_LOCATION = 2528  # Netherlands (country level; ChatGPT has no city targeting)


def build_jobs(cfg):
    s = cfg['scrape']
    eng = s['engines']
    jobs = []
    for city, code in cfg['cities'].items():
        for kw, vol in cfg['keywords'].items():
            base = dict(city=city, location_code=code, keyword=kw, volume=vol)
            prompt = s['prompt_template'].format(keyword=kw, city=city)
            if eng.get('google_organic'):
                jobs.append(dict(base, engine='google_organic', path='/v3/serp/google/organic/live/advanced',
                                 payload=dict(keyword=kw, location_code=code, language_code=s['language_code'],
                                              device=s['device'], os=s['os'], depth=s['depth'], load_async_ai_overview=True)))
            if eng.get('google_ai_mode'):
                jobs.append(dict(base, engine='google_ai_mode', prompt=prompt, path='/v3/serp/google/ai_mode/live/advanced',
                                 payload=dict(keyword=prompt, location_code=code, language_code=s['language_code'],
                                              device=s['device'], os=s['os'])))
            if eng.get('chatgpt'):
                jobs.append(dict(base, engine='chatgpt', prompt=prompt, path='/v3/ai_optimization/chat_gpt/llm_scraper/live/advanced',
                                 payload=dict(keyword=prompt, location_code=CHATGPT_LOCATION, language_code=s['language_code'],
                                              force_web_search=True)))
    return jobs


class Client:
    def __init__(self, login, password):
        self.auth = 'Basic ' + base64.b64encode(f'{login}:{password}'.encode()).decode()
        self.local = threading.local()

    def session(self):
        if not hasattr(self.local, 's'):
            s = requests.Session()
            s.headers.update({'Authorization': self.auth, 'Content-Type': 'application/json'})
            self.local.s = s
        return self.local.s

    def balance(self):
        r = self.session().get(API + '/v3/appendix/user_data', timeout=30).json()
        if r.get('status_code') != 20000:
            raise SystemExit(f'DataForSEO login failed: {r.get("status_message")}')
        return (r['tasks'][0]['result'][0].get('money') or {}).get('balance')

    def run(self, job, run_date, tries=4):
        meta = {k: v for k, v in job.items() if k not in ('payload', 'path')}
        err = None
        timeout = 200 if job['engine'] == 'chatgpt' else 120
        for i in range(tries):
            try:
                resp = self.session().post(API + job['path'], json=[job['payload']], timeout=timeout).json()
                task = (resp.get('tasks') or [{}])[0]
                if task.get('status_code') == 20000 and task.get('result'):
                    return dict(meta, run_date=run_date, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                cost=task.get('cost'), task=task, error=None)
                err = f"{task.get('status_code') or resp.get('status_code')} {task.get('status_message') or resp.get('status_message')}"
            except Exception as e:  # network, timeout, bad JSON
                err = repr(e)
            time.sleep(3 * (i + 1))
        return dict(meta, run_date=run_date, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(), cost=0, task=None, error=err)


QUEUE_WAIT = {40601, 40602}  # Task Handed, Task In Queue: not ready yet
TASK_POST = {'google_organic': '/v3/serp/google/organic', 'google_ai_mode': '/v3/serp/google/ai_mode'}


def run_standard(cli, jobs, run_date, workers, max_wait_min=45, poll_s=45):
    """Standard Queue: post tasks in batches of 100, poll task_get/advanced until done.
    About 70% cheaper than Live. Jobs that are not done in time, or not supported, fall back to Live."""
    live_only = [j for j in jobs if j['engine'] not in TASK_POST]
    queued = [j for j in jobs if j['engine'] in TASK_POST]
    pending, results, fallback = {}, [], list(live_only)
    for eng, base in TASK_POST.items():
        group = [j for j in queued if j['engine'] == eng]
        for i in range(0, len(group), 100):
            batch = group[i:i + 100]
            body = [dict(j['payload'], tag=str(n)) for n, j in enumerate(batch)]
            try:
                resp = cli.session().post(API + base + '/task_post', json=body, timeout=120).json()
            except Exception as e:
                print(f'  task_post failed ({e!r}); {len(batch)} jobs go Live', flush=True)
                fallback += batch
                continue
            for t in resp.get('tasks') or []:
                j = batch[int((t.get('data') or {}).get('tag', -1))] if (t.get('data') or {}).get('tag') is not None else None
                if j is None:
                    continue
                if t.get('status_code') == 20100:  # Task Created
                    pending[t['id']] = (j, base)
                else:
                    fallback.append(j)
    print(f'Standard queue: {len(pending)} tasks posted, {len(fallback)} via Live', flush=True)

    def get(tid):
        j, base = pending[tid]
        try:
            resp = cli.session().get(f'{API}{base}/task_get/advanced/{tid}', timeout=60).json()
            t = (resp.get('tasks') or [{}])[0]
        except Exception as e:
            return tid, 'wait', repr(e)
        if t.get('status_code') == 20000 and t.get('result'):
            return tid, 'ok', t
        if t.get('status_code') in QUEUE_WAIT or t.get('status_code') is None:
            return tid, 'wait', None
        return tid, 'fail', f"{t.get('status_code')} {t.get('status_message')}"

    deadline = time.time() + max_wait_min * 60
    while pending and time.time() < deadline:
        time.sleep(poll_s)
        with ThreadPoolExecutor(workers) as ex:
            for tid, state, val in ex.map(get, list(pending)):
                if state == 'wait':
                    continue
                j, _ = pending.pop(tid)
                if state == 'ok':
                    meta = {k: v for k, v in j.items() if k not in ('payload', 'path')}
                    results.append(dict(meta, run_date=run_date, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                        cost=val.get('cost'), task=val, error=None, queue='standard'))
                else:
                    fallback.append(j)
        print(f'  queue: {len(results)} done, {len(pending)} waiting', flush=True)
    if pending:
        print(f'  {len(pending)} tasks not ready in {max_wait_min} min; fetching them Live', flush=True)
        fallback += [j for j, _ in pending.values()]
    return results, fallback


def run_live(cli, jobs, run_date, workers):
    results, done = [], 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(cli.run, j, run_date) for j in jobs]
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f'  live {done}/{len(jobs)}', flush=True)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument('--out', default=None)
    ap.add_argument('--limit', type=int, default=0, help='only run the first N jobs (smoke test)')
    ap.add_argument('--max-error-rate', type=float, default=0.10)
    ap.add_argument('--queue', choices=['standard', 'live'], default=None, help='override scrape.queue from the config')
    a = ap.parse_args(argv)

    cfg = config.load()
    cli = Client(config.env('DATAFORSEO_LOGIN', required=True), config.env('DATAFORSEO_PASSWORD', required=True))
    cli.balance()  # verifies the login; the balance itself is not printed (public logs)
    print('DataForSEO login ok')
    jobs = build_jobs(cfg)
    if a.limit:
        jobs = jobs[:a.limit]
    print(f'{len(jobs)} jobs for run {a.date}', flush=True)

    workers = cfg['scrape']['max_workers']
    mode = a.queue or cfg['scrape'].get('queue', 'standard')
    if mode == 'standard':
        results, fallback = run_standard(cli, jobs, a.date, workers, cfg['scrape'].get('max_queue_minutes', 45))
        results += run_live(cli, fallback, a.date, workers) if fallback else []
    else:
        results = run_live(cli, jobs, a.date, workers)

    # second pass for transient failures
    failed = [r for r in results if r['error']]
    if failed:
        print(f'Retrying {len(failed)} failed jobs', flush=True)
        by_key = {(j['engine'], j['city'], j['keyword']): j for j in jobs}
        results = [r for r in results if not r['error']]
        for r in failed:
            time.sleep(2)
            results.append(cli.run(by_key[(r['engine'], r['city'], r['keyword'])], a.date, tries=3))

    errors = [r for r in results if r['error']]
    cost = sum(r.get('cost') or 0 for r in results)
    out = Path(a.out or config.ROOT / 'data' / 'raw' / f'run_{a.date}.jsonl.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, 'wt', encoding='utf-8') as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'Wrote {out.name} | ok {len(results) - len(errors)} | errors {len(errors)} | cost ${cost:.2f}')
    for e in errors[:15]:
        print('  x', e['engine'], e['city'], e['keyword'], e['error'])
    if len(errors) > a.max_error_rate * max(len(results), 1):
        sys.exit(f'Error rate too high ({len(errors)}/{len(results)}); not loading this run')


if __name__ == '__main__':
    main()
