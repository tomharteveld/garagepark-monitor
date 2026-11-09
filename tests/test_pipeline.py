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
    scrape.main(['--limit', '4', '--date', '2026-11-09', '--out', str(out), '--queue', 'live'])
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
            assert job_config.time_partitioning is None and job_config.clustering_fields is None  # must match the table, so leave out
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


def test_standard_queue_with_fake_api(records):
    """task_post in batches, one task still queued on the first poll, one failing task falls back to Live."""
    from gpmon import config, scrape
    tasks = {e: next(r['task'] for r in records if r['task'] and r['engine'] == e) for e in ('google_organic', 'google_ai_mode')}
    polls = {}

    class Resp:
        def __init__(self, data): self.data = data
        def json(self): return self.data

    class Sess:
        def post(self, url, json, timeout):
            eng = 'google_ai_mode' if 'ai_mode' in url else 'google_organic'
            out = []
            for t in json:
                n = int(t['tag'])
                code = 40501 if (eng == 'google_ai_mode' and n == 0) else 20100  # one rejected task
                out.append({'id': f"{eng}-{n}", 'status_code': code, 'status_message': 'x', 'data': {'tag': t['tag']}})
            return Resp({'status_code': 20000, 'tasks': out})
        def get(self, url, timeout):
            tid = url.rsplit('/', 1)[-1]
            polls[tid] = polls.get(tid, 0) + 1
            if tid.endswith('-1') and polls[tid] == 1:
                return Resp({'tasks': [{'status_code': 40602, 'status_message': 'Task In Queue'}]})
            eng = tid.rsplit('-', 1)[0]
            return Resp({'tasks': [dict(tasks[eng], status_code=20000)]})

    cli = scrape.Client('x', 'y')
    cli.session = lambda: Sess()
    jobs = scrape.build_jobs(config.load())[:6]  # 3 organic + 3 AI Mode
    done, fallback = scrape.run_standard(cli, jobs, '2026-11-09', workers=4, max_wait_min=1, poll_s=0)
    assert len(done) == 5 and all(r['queue'] == 'standard' and r['task'] for r in done)
    assert len(fallback) == 1 and fallback[0]['engine'] == 'google_ai_mode'


GBP_FIX = sorted((ROOT / 'tests' / 'fixtures').glob('gbp_*.jsonl.gz'))


@pytest.mark.skipif(not GBP_FIX, reason='no business profile fixture')
def test_gbp_transform_view_and_schema():
    bq = pytest.importorskip('gpmon.bq')
    from gpmon import gbp, gbp_view
    T = gbp.transform_files(GBP_FIX)
    for name, rows in T.items():
        fields = {f.name: f for f in bq.SCHEMAS[name]}
        assert rows, name
        for r in rows:
            assert not set(r) - set(fields), (name, set(r) - set(fields))
            json.dumps(r)
    own = [p for p in T['gbp_profiles'] if p['is_own']]
    assert len(own) == 3 and all(p['parent_cid'] is None for p in own)
    assert all(p['parent_cid'] for p in T['gbp_profiles'] if not p['is_own'])
    V = gbp_view.build(T)
    assert len(V['own']) == 3 and V['actions'] and all(a['impact'] in ('Hoog', 'Middel', 'Laag') for a in V['actions'])
    assert all(0 <= p['score'] <= 1 for p in V['own'] + V['comp'])
    assert {t['key'] for t in V['themes']} == {k for k, _, _ in gbp_view.THEMES}


def test_gbp_competitor_pick_skips_own_and_caps():
    from gpmon import gbp
    g = {'own_name_pattern': 'garage ?park'}
    own = [{'cid': '1', 'people_also_search': [{'cid': '2', 'title': 'GaragePark X'}, {'cid': '3', 'title': 'Shurgard'},
                                               {'cid': '1', 'title': 'self'}, {'cid': '4', 'title': 'Garage Park Y'}, {'cid': '5', 'title': '1BOX'}, {'cid': '6', 'title': 'Allsafe'}]},
           {'cid': '2', 'people_also_search': []}]
    links, comps = gbp.pick_competitors(own, g, 2)
    assert [l['comp_cid'] for l in links] == ['3', '5'] and set(comps) == {'3', '5'}


def test_gbp_perf_transform_aggregate_and_view():
    bq = pytest.importorskip('gpmon.bq')
    import datetime as dt
    from gpmon import gbp, gbp_perf, gbp_view
    T = gbp.transform_files(GBP_FIX)
    own = [p for p in T['gbp_profiles'] if p['is_own']]
    end = dt.date(2026, 11, 3)
    recs = [dict(kind='location', run_date='2026-11-04', location_id=f'L{i}', account='accounts/1', account_name='Follo',
                 title=p['title'], cid=p['cid'], place_id=None, website='https://garagepark.nl/x', city=p.get('city'),
                 address='', latitude=None, longitude=None, status='OPEN', has_voice_of_merchant=True) for i, p in enumerate(own)]
    recs.append(dict(kind='location', run_date='2026-11-04', location_id='Lnew', account='accounts/1', account_name='Follo',
                     title='GaragePark Nieuw', cid=None, place_id=None, website='https://garagepark.nl/n', city='Ergens',
                     address='', latitude=None, longitude=None, status='OPEN', has_voice_of_merchant=False))
    for i in range(len(own)):
        for d in range(400):
            day = (end - dt.timedelta(days=d)).isoformat()
            recs.append(dict(kind='daily', run_date='2026-11-04', location_id=f'L{i}', date=day, metric='BUSINESS_IMPRESSIONS_MOBILE_SEARCH', value=10))
            recs.append(dict(kind='daily', run_date='2026-11-04', location_id=f'L{i}', date=day, metric='WEBSITE_CLICKS', value=1 if d < 28 else 0))
        recs.append(dict(kind='keyword', run_date='2026-11-04', location_id=f'L{i}', month='2026-10-01', keyword='garagebox huren', value=100, threshold=None))
        recs.append(dict(kind='keyword', run_date='2026-11-04', location_id=f'L{i}', month='2026-10-01', keyword='zeldzaam', value=None, threshold=15))
    P = gbp_perf.transform([dict(r) for r in recs])
    for name, rows in P.items():
        fields = {f.name for f in bq.SCHEMAS[name]}
        assert rows and all(not set(r) - fields for r in rows), name
    assert all(r['value'] for r in P['gbp_perf_daily'])  # zero days dropped
    T.update(gbp_perf.aggregate(P))
    w = {(r['location_id'], r['metric']): r for r in T['gbp_perf_window']}
    assert w[('L0', 'BUSINESS_IMPRESSIONS_MOBILE_SEARCH')]['cur'] == 280 and w[('L0', 'BUSINESS_IMPRESSIONS_MOBILE_SEARCH')]['prev'] == 280
    assert w[('L0', 'BUSINESS_IMPRESSIONS_MOBILE_SEARCH')]['yoy'] == 280
    V = gbp_view.build(T)
    pf = V['own'][0]['pf']
    assert pf['views'] == 280 and pf['act'] == 28 and pf['conv'] == 0.1
    assert V['perf']['keywords'][0] == dict(k='garagebox huren', v=100 * len(own), cat=V['perf']['keywords'][0]['cat'], n=len(own))
    hidden = [l for l in V['perf']['locations'] if not l['visible']]
    assert [l['id'] for l in hidden] == ['Lnew']
    json.dumps(V)


def test_folloengine_chatgpt_mapping():
    from gpmon import folloengine as fe
    m = fe.map_runs(['2026-11-02', '2026-11-09', '2026-12-01'], ['2026-10-30', '2026-11-06'], 10)
    assert m == {'2026-11-02': '2026-10-30', '2026-11-09': '2026-11-06'}  # 1 Dec: newest is 25 days old
    rows = [dict(d='2026-11-06', prompt='Waar kan ik een garagebox huren in de omgeving van Den bosch?', model='gpt-6',
                 md='Probeer **GaragePark** of Shurgard.', m=True, c=True,
                 cites=[dict(rnk=1, url='https://garagepark.nl/locatie/den-bosch?utm_source=chatgpt.com', title='GP', snippet='', domain='garagepark.nl'),
                        dict(rnk=2, url='https://www.shurgard.com/nl-nl?utm_source=chatgpt.com', title='S', snippet='', domain='www.shurgard.com')]),
            dict(d='2026-11-06', prompt='Wat kost meubelopslag per maand?', model='gpt-6', md='Ongeveer 50 euro.', m=False, c=False, cites=[])]
    a, c = fe.to_tables(rows, {'2026-11-09': '2026-11-06'}, {'2026-11-09'})
    assert [(x['city'], x['keyword'], x['engine']) for x in a] == [('Den Bosch', 'garagebox huren', 'cgpt'),
                                                                 ('Algemeen', 'Wat kost meubelopslag per maand?', 'cgpt')]
    assert a[0]['mentioned'] and a[0]['cited_web'] and a[0]['volume'] == 5400 and 'Shurgard' in a[0]['brands_mentioned']
    assert a[0]['brands_cited'] == ['GaragePark', 'Shurgard'] and c[0]['url'] == 'https://garagepark.nl/locatie/den-bosch'
    assert not a[1]['mentioned'] and a[1]['markdown'] == 'Ongeveer 50 euro.'
    a2, c2 = fe.to_tables(rows, {'2026-11-02': '2026-11-06'}, set())   # slim run: no text, no citation rows
    assert a2[0]['markdown'] is None and not c2 and a2[0]['brands_cited']


def test_news_filters_market_news_only():
    import datetime as dt
    from gpmon import config, news
    cfg, today = config.load(), dt.date(2026, 11, 9)
    def c(title, url='https://www.ad.nl/x', snippet='', pub='2026-11-01'):
        return news.classify(dict(source='alerts', query='q', title=title, url=url, snippet=snippet, published=pub, publisher=None), cfg, today)
    ok = c('Shurgard opent nieuwe vestiging in Breda', snippet='Het opslagbedrijf opent volgende maand de deuren.')
    assert ok['keep'] and ok['grp'] == 'concurrent' and ok['brands'] == ['Shurgard'] and ok['cities'] == ['Breda'] and ok['topic'] == 'Nieuwe locatie'
    assert c('Inbrekers slaan toe bij garageboxen in Almelo', snippet='De politie onderzoekt de inbraak in de garageboxen.')['grp'] == 'lokaal'
    assert c('GaragePark opent nieuw park in Venlo', snippet='Het nieuwe park met garageboxen opent in de zomer.')['grp'] == 'eigen'
    assert c('Gemeente kiest locatie voor batterij-opslag bij Breda', snippet='De energieopslag moet netcongestie verminderen.')['reason'] == 'ander soort opslag'
    assert c('Global Self Storage reports earnings for the third quarter')['reason'] == 'niet Nederlands'
    assert c('Tips: zo richt je je opslagruimte in', url='https://www.shurgard.com/nl-nl/blog/tips', snippet='Met deze tips richt je de opslag in.')['reason'] == 'blog of kennisbank'
    assert c('Shurgard opent in Utrecht', url='https://www.shurgard.com/nl-nl/opslagruimte-utrecht', snippet='Huur een opslagruimte in de stad.')['reason'] == 'pagina op eigen site, geen nieuws'
    assert c('Shurgard opent in Utrecht', url='https://www.shurgard.com/nl-nl/pers/opening-utrecht', snippet='Persbericht over de opening van de opslag.')['keep']
    assert c('Koersdoel aandeel Shurgard verhoogd door analist', snippet='De analist ziet de koers van het aandeel stijgen.')['reason'] == 'beursinformatie'
    assert c('Nieuwe fietsbrug in Breda geopend voor het verkeer', snippet='De brug is open.')['reason'] == 'niet over de opslagmarkt'
    assert c('Shurgard opent in Breda', snippet='Opslag in de stad.', pub='2025-01-01')['reason'] == 'te oud'
    assert news.item_id('Shurgard opent in Breda - AD.nl') == news.item_id('Shurgard opent in Breda')
