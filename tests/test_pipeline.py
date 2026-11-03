"""Offline tests: transform output matches the BigQuery schemas, multi-week history builds, dashboard renders.

Run: PYTHONPATH=src python -m pytest -q tests
"""
import copy
import json
import random
from pathlib import Path

import pytest

from gpmon import dashboard, transform

ROOT = Path(__file__).resolve().parents[1]
RAW = sorted((ROOT / 'data' / 'raw').glob('run_*.jsonl.gz')) or sorted((ROOT / 'tests' / 'fixtures').glob('run_*.jsonl.gz'))
pytestmark = pytest.mark.skipif(not RAW, reason='no raw run in data/raw')


@pytest.fixture(scope='module')
def records():
    return list(transform.read_raw(RAW[-1]))


def test_rows_fit_bigquery_schemas(records):
    bq = pytest.importorskip('gpmon.bq')
    T = transform.transform(records[:60], keep_raw=True)
    for name, rows in T.items():
        fields = {f.name: f for f in bq.SCHEMAS[name]}
        for r in rows:
            extra = set(r) - set(fields)
            assert not extra, f'{name}: {extra} not in schema'
            for k, v in r.items():
                if fields[k].mode == 'REPEATED':
                    assert isinstance(v, list), (name, k)
        if name == 'raw_tasks':
            assert all(isinstance(json.loads(r['task']), dict) for r in rows)


def test_counts(records):
    T = transform.transform(records, keep_raw=False)
    assert len(T['serp_summary']) == sum(1 for r in records if r['engine'] == 'google_organic' and not r['error'])
    assert len(T['run_log']) == len(records)
    assert all(a['engine'] in ('aio', 'aim', 'cgpt') for a in T['ai_answers'])


def _shift(records, date, seed):
    """Fake a later week: same data, new date, a few positions shuffled."""
    rnd = random.Random(seed)
    out = []
    for r in copy.deepcopy(records):
        r['run_date'] = date
        if r['task'] and r['engine'] == 'google_organic' and rnd.random() < .3:
            org = [i for i in r['task']['result'][0]['items'] if i['type'] == 'organic']
            rnd.shuffle(org)
            for n, i in enumerate(org, 1):
                i['rank_group'] = n
        out.append(r)
    return out


def test_multi_week_dashboard(records, tmp_path):
    recs = _shift(records, '2026-10-19', 1) + _shift(records, '2026-10-26', 2) + records
    T = transform.transform(recs, keep_raw=False)
    data = dashboard.build_data(T, detail_runs=2)
    assert data['meta']['runs'] == ['2026-10-19', '2026-10-26', '2026-11-02']
    assert data['meta']['detail_runs'] == ['2026-10-26', '2026-11-02']
    old = [s for s in data['serps'] if s['r'] == '2026-10-19']
    assert old and all('t' not in o for s in old for o in s['org'])  # slim history
    page = dashboard.render(data, tmp_path, 'abc123secret')
    html = page.read_text()
    assert page == tmp_path / 'abc123secret' / 'index.html'
    assert html.startswith('<!doctype html>') and 'noindex' in html
    assert '__DATA__' not in html and '__NLMAP__' not in html and '__LOGO__' not in html
    assert 'Disallow: /' in (tmp_path / 'robots.txt').read_text()
    assert 'GaragePark' not in (tmp_path / 'index.html').read_text()  # root reveals nothing


def test_scrape_against_fake_api(records, tmp_path, monkeypatch):
    """Runs the real scraper end to end with the HTTP layer replaced by recorded responses."""
    from gpmon import scrape
    by = {(r['engine'], r['city'], r['keyword']): r for r in records if r['task']}

    class Resp:
        def __init__(self, data): self.data = data
        def json(self): return self.data

    class Sess:
        headers = {}
        def get(self, url, timeout): return Resp({'status_code': 20000, 'tasks': [{'result': [{'money': {'balance': 99}}]}]})
        def post(self, url, json, timeout):
            eng = 'google_ai_mode' if 'ai_mode' in url else 'google_organic'
            task = next(r['task'] for (e, c, k), r in by.items() if e == eng)
            return Resp({'status_code': 20000, 'tasks': [task]})

    monkeypatch.setenv('DATAFORSEO_LOGIN', 'x')
    monkeypatch.setenv('DATAFORSEO_PASSWORD', 'y')
    monkeypatch.setattr(scrape.Client, 'session', lambda self: Sess())
    out = tmp_path / 'run.jsonl.gz'
    scrape.main(['--limit', '4', '--date', '2026-11-09', '--out', str(out)])
    got = list(transform.read_raw(out))
    assert len(got) == 4 and all(g['task'] and g['run_date'] == '2026-11-09' for g in got)


def test_bq_load_with_fake_client(records):
    """Every table goes to its own run_date partition with WRITE_TRUNCATE; rows are JSON-serialisable."""
    bq = pytest.importorskip('gpmon.bq')
    calls = []

    class Job:
        def result(self): return None

    class Fake:
        def load_table_from_json(self, rows, dest, job_config):
            json.dumps(rows)
            calls.append((dest, len(rows), job_config.write_disposition))
            return Job()
        def query(self, sql):
            calls.append((sql, 0, 'DML'))
            return Job()

    import os
    os.environ.setdefault('GCP_PROJECT_ID', 'test-project')
    bq.load_tables(transform.transform(records, keep_raw=True), Fake())
    dests = {c[0] for c in calls}
    assert any(d.endswith('serp_summary$20261102') for d in dests)
    assert any(d.endswith('raw_tasks$20261102') for d in dests)
    assert all(c[2] in ('WRITE_TRUNCATE', 'DML') for c in calls)
