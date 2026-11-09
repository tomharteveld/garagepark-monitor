"""Aggregators: visits and leads that come to garagepark.nl via third party platforms (huren.nl, Marktplaats,
Funda, Opslagmarkt ...), from the GA4 Data API into BigQuery.

Auth: the OAuth login of seodata@folloagency.com (secret GBP_OAUTH_JSON, scopes business.manage and
analytics.readonly), the account that was invited to the client's GA4. The Google Analytics Data API must be
enabled in the Cloud project of that OAuth client. Without that secret it falls back to the pipeline's own identity.

Every run stores a full snapshot of the last `days` days (default 400) under its run_date; the dashboard
reads the newest snapshot. Rows: one per date, aggregator, source, medium and landing page, plus a row per
date with aggregator '(site)' holding the totals of the whole site, for shares and conversion benchmarks.

Usage: python -m gpmon.ga [--date YYYY-MM-DD] [--days 400]     writes data/raw/ga_<date>.jsonl.gz
"""
import argparse
import datetime as dt
import gzip
import json
import re
from pathlib import Path

import requests

from . import config

TABLE = 'ga_aggregators'
API = 'https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport'
SITE = '(site)'
SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'


def session():
    """The login of seodata@folloagency.com (secret GBP_OAUTH_JSON, which also carries the analytics.readonly
    scope) when present, because that account has access to the client's GA4; else the pipeline's own identity."""
    from google.auth.transport.requests import AuthorizedSession
    if config.env('GBP_OAUTH_JSON'):
        from google.oauth2.credentials import Credentials
        from .gbp_perf import load_oauth
        o = load_oauth()
        creds = Credentials(None, refresh_token=o['refresh_token'], client_id=o['client_id'], client_secret=o['client_secret'],
                            token_uri='https://oauth2.googleapis.com/token', scopes=[SCOPE])
        return AuthorizedSession(creds)
    import google.auth
    creds, _ = google.auth.default(scopes=[SCOPE])
    return AuthorizedSession(creds)


def report(s, pid, body):
    rows, offset = [], 0
    while True:
        r = s.post(API.format(pid=pid), json=dict(body, limit=100000, offset=offset), timeout=120)
        if r.status_code in (401, 403):
            raise SystemExit('No access to GA4. Log in again with scripts/gbp_oauth.py (it now also asks for Analytics) '
                             f'and enable the Google Analytics Data API in the OAuth client\'s Cloud project. {r.text[:300]}')
        r.raise_for_status()
        d = r.json()
        dims = [h['name'] for h in d.get('dimensionHeaders', [])]
        mets = [h['name'] for h in d.get('metricHeaders', [])]
        for row in d.get('rows') or []:
            x = {k: v['value'] for k, v in zip(dims, row['dimensionValues'])}
            x.update({k: float(v['value']) for k, v in zip(mets, row['metricValues'])})
            rows.append(x)
        offset += 100000
        if offset >= int(d.get('rowCount') or 0):
            return rows


def classify_source(src, sites):
    for name, rx in sites.items():
        if re.search(rx, src or '', re.I):
            return name
    return None


def park_of(landing):
    m = re.match(r'^/locaties/([^/?#]+)', landing or '')
    return m.group(1) if m else None


def fetch(cfg, run_date, days=400):
    a = cfg['aggregators']
    pid, sites = a['property_id'], a['sites']
    leads = a.get('lead_events') or []
    end = dt.date.fromisoformat(run_date) - dt.timedelta(days=1)
    rng = [{'startDate': (end - dt.timedelta(days=days)).isoformat(), 'endDate': end.isoformat()}]
    mets = [{'name': 'sessions'}, {'name': 'engagedSessions'}, {'name': 'keyEvents'}] + [{'name': f'keyEvents:{e}'} for e in leads]
    s = session()
    rx = '|'.join(f'({v})' for v in sites.values())
    agg = report(s, pid, {'dateRanges': rng, 'dimensions': [{'name': n} for n in ('date', 'sessionSource', 'sessionMedium', 'landingPage')],
                          'metrics': mets, 'dimensionFilter': {'filter': {'fieldName': 'sessionSource',
                                                                           'stringFilter': {'matchType': 'PARTIAL_REGEXP', 'value': rx, 'caseSensitive': False}}}})
    tot = report(s, pid, {'dateRanges': rng, 'dimensions': [{'name': 'date'}], 'metrics': mets})
    out = []

    def row(x, aggregator, src=None, med=None, lp=None):
        d = x['date']
        return dict(run_date=run_date, date=f'{d[:4]}-{d[4:6]}-{d[6:]}', aggregator=aggregator, source=src, medium=med,
                    landing_page=lp, park=park_of(lp), sessions=int(x['sessions']), engaged=int(x['engagedSessions']),
                    key_events=int(x['keyEvents']), leads=int(sum(x.get(f'keyEvents:{e}', 0) for e in leads)),
                    purchases=int(x.get('keyEvents:purchase', 0)))
    for x in agg:
        name = classify_source(x['sessionSource'], sites)
        if name:
            out.append(row(x, name, x['sessionSource'], x['sessionMedium'], x['landingPage']))
    out += [row(x, SITE) for x in tot]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument('--days', type=int, default=400)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    cfg = config.load()
    rows = fetch(cfg, a.date, a.days)
    out = Path(a.out or config.ROOT / 'data' / 'raw' / f'ga_{a.date}.jsonl.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, 'wt', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    from collections import Counter
    c = Counter()
    for r in rows:
        if r['aggregator'] != SITE:
            c[r['aggregator']] += r['sessions']
    print(f'Wrote {out.name} | {len(rows)} rows | ' + ', '.join(f'{k} {v}' for k, v in c.most_common(8)))


def transform_files(paths):
    rows = []
    for p in paths:
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            rows += [json.loads(l) for l in fh if l.strip()]
    return {TABLE: rows}


def build(rows, cfg=None):
    """Dashboard payload from the newest snapshot: monthly series, 28 day windows, parks, landing pages."""
    from collections import defaultdict
    if not rows:
        return None
    to_d = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    last = max(to_d(r['run_date']) for r in rows)
    rows = [r for r in rows if to_d(r['run_date']) == last]
    end = max(dt.date.fromisoformat(to_d(r['date'])) for r in rows)
    K = ('sessions', 'engaged', 'leads', 'purchases', 'key_events')
    z = lambda: dict.fromkeys(K, 0)
    month, win, src, park = defaultdict(z), defaultdict(lambda: [z(), z()]), defaultdict(z), defaultdict(z)
    for r in rows:
        d = dt.date.fromisoformat(to_d(r['date']))
        age = (end - d).days
        a = r['aggregator']
        for k in K:
            month[(a, to_d(r['date'])[:7])][k] += r.get(k) or 0
            if age < 28:
                win[a][0][k] += r.get(k) or 0
            elif age < 56:
                win[a][1][k] += r.get(k) or 0
            if age < 90 and a != SITE:
                src[(a, r.get('source') or '', r.get('medium') or '')][k] += r.get(k) or 0
                if r.get('park'):
                    park[(r['park'], a)][k] += r.get(k) or 0
    aggs = sorted({a for a, _ in month if a != SITE}, key=lambda a: -sum(month[(a, m)]['sessions'] for aa, m in month if aa == a))
    months = sorted({m for _, m in month})[-13:]
    return dict(run=last, end=end.isoformat(), aggregators=aggs, months=months,
                monthly={a: [[month[(a, m)]['sessions'], month[(a, m)]['leads']] for m in months] for a in aggs + [SITE]},
                window={a: dict(cur=v[0], prev=v[1]) for a, v in win.items()},
                sources=[dict(a=a, s=s, m=m, **v) for (a, s, m), v in sorted(src.items(), key=lambda x: -x[1]['sessions'])][:40],
                parks=[dict(p=p, a=a, **v) for (p, a), v in sorted(park.items(), key=lambda x: -x[1]['sessions'])][:200],
                lead_events=(cfg or config.load())['aggregators'].get('lead_events') or [])


if __name__ == '__main__':
    main()
