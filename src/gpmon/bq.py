"""BigQuery: schemas, idempotent loads (one partition per run_date) and history queries for the dashboard.

Usage:
  python -m gpmon.bq setup                                  # create dataset + tables
  python -m gpmon.bq load data/raw/run_2026-11-02.jsonl.gz  # transform + load (replaces that run_date)
Env: GCP_PROJECT_ID, BQ_DATASET (default garagepark_monitor), BQ_LOCATION (default EU),
     credentials via GOOGLE_APPLICATION_CREDENTIALS or the GitHub OIDC auth action.
"""
import argparse
import datetime as dt
import json

from google.cloud import bigquery as bq

from . import config, transform

F = bq.SchemaField
S, I, B, FL, D, TS = 'STRING', 'INTEGER', 'BOOLEAN', 'FLOAT', 'DATE', 'TIMESTAMP'
KEY = [F('run_date', D, 'REQUIRED'), F('city', S), F('keyword', S)]

SCHEMAS = {
    'serp_summary': KEY + [F('location_code', I), F('volume', I), F('own_position', I), F('own_url', S), F('own_results', I),
                           F('in_local_pack', B), F('features', S, 'REPEATED'), F('has_ai_overview', B), F('check_url', S), F('fetched_at', TS)],
    'serp_results': KEY + [F('rank_group', I), F('domain', S), F('url', S), F('title', S), F('website_name', S), F('is_own', B)],
    'local_pack': KEY + [F('position', I), F('title', S), F('rating', FL), F('votes', I), F('is_own', B)],
    'ai_answers': KEY + [F('engine', S), F('volume', I), F('prompt', S), F('markdown', S), F('model', S), F('mentioned', B), F('cited', B),
                         F('cited_web', B), F('cited_gbp', B), F('brands_mentioned', S, 'REPEATED'), F('brands_cited', S, 'REPEATED'), F('fetched_at', TS)],
    'ai_citations': KEY + [F('engine', S), F('position', I), F('domain', S), F('url', S), F('title', S), F('snippet', S),
                           F('is_gbp', B), F('brand', S), F('kind', S)],
    'run_log': KEY + [F('engine', S), F('prompt', S), F('status', S), F('error', S), F('cost', FL), F('fetched_at', TS)],
    'raw_tasks': KEY + [F('engine', S), F('prompt', S), F('fetched_at', TS), F('task', 'JSON')],
    # Google Bedrijfsprofielen (own + competitors), see gbp.py
    'gbp_profiles': [F('run_date', D, 'REQUIRED'), F('cid', S), F('is_own', B), F('parent_cid', S), F('title', S), F('city', S),
                     F('address', S), F('latitude', FL), F('longitude', FL), F('domain', S), F('url', S), F('phone', S),
                     F('category', S), F('additional_categories', S, 'REPEATED'), F('description', S), F('is_claimed', B),
                     F('rating', FL), F('votes', I), F('stars', I, 'REPEATED'), F('total_photos', I), F('has_logo', B),
                     F('has_hours', B), F('open_247', B), F('attributes', S, 'REPEATED'), F('services', S, 'REPEATED'),
                     F('place_topics', S, 'REPEATED'), F('book_online_url', S), F('contact_url', S), F('from_listing_only', B),
                     F('check_url', S), F('listing_updated', TS), F('source', S)],
    'news_items': [F('run_date', D, 'REQUIRED'), F('id', S), F('source', S), F('query', S), F('grp', S), F('title', S),
                   F('url', S), F('domain', S), F('publisher', S), F('snippet', S), F('published', D),
                   F('brands', S, 'REPEATED'), F('cities', S, 'REPEATED'), F('topic', S), F('keep', B), F('reason', S),
                   F('fetched_at', TS)],
    'gbp_competitors': [F('run_date', D, 'REQUIRED'), F('own_cid', S), F('comp_cid', S), F('rank', I)],
    'gbp_reviews': [F('run_date', D, 'REQUIRED'), F('cid', S), F('is_own', B), F('review_id', S), F('rating', FL), F('posted_at', TS),
                    F('time_ago', S), F('text', S), F('language', S), F('has_text', B), F('photos', B), F('local_guide', B),
                    F('owner_answer', S), F('answered_at', TS), F('review_url', S)],
    'gbp_posts': [F('run_date', D, 'REQUIRED'), F('cid', S), F('is_own', B), F('posted_at', TS), F('text', S), F('url', S),
                  F('has_image', B), F('links', S, 'REPEATED')],
    'gbp_run_log': [F('run_date', D, 'REQUIRED'), F('kind', S), F('cid', S), F('is_own', B), F('status', S), F('error', S), F('cost', FL)],
    # Business Profile Performance API (own profiles, manager access), see gbp_perf.py
    'gbp_locations': [F('run_date', D, 'REQUIRED'), F('location_id', S), F('account', S), F('account_name', S), F('title', S), F('cid', S),
                      F('place_id', S), F('website', S), F('city', S), F('address', S), F('latitude', FL), F('longitude', FL),
                      F('status', S), F('has_voice_of_merchant', B)],
    'gbp_perf_daily': [F('run_date', D, 'REQUIRED'), F('location_id', S), F('date', D), F('metric', S), F('value', I)],
    'gbp_keywords': [F('run_date', D, 'REQUIRED'), F('location_id', S), F('month', D), F('keyword', S), F('value', I), F('threshold', I)],
}
CLUSTER = {'serp_summary': ['city', 'keyword'], 'serp_results': ['domain', 'city'], 'local_pack': ['city'],
           'ai_answers': ['engine', 'city'], 'ai_citations': ['engine', 'domain'], 'run_log': ['engine'], 'raw_tasks': ['engine', 'city'],
           'gbp_profiles': ['is_own', 'cid'], 'gbp_competitors': ['own_cid'], 'gbp_reviews': ['is_own', 'cid'],
           'gbp_posts': ['cid'], 'news_items': ['grp', 'keep'], 'gbp_run_log': ['kind'],
           'gbp_locations': ['location_id'], 'gbp_perf_daily': ['location_id', 'metric'], 'gbp_keywords': ['location_id']}


def client():
    return bq.Client(project=config.BQ_PROJECT(), location=config.BQ_LOCATION())


def tid(name):
    return f'{config.BQ_PROJECT()}.{config.BQ_DATASET()}.{name}'


def setup(c=None):
    c = c or client()
    from google.api_core.exceptions import NotFound
    ds_id = f'{config.BQ_PROJECT()}.{config.BQ_DATASET()}'
    try:  # check first: the service account may edit the dataset but not create datasets in the project
        c.get_dataset(ds_id)
    except NotFound:
        ds = bq.Dataset(ds_id)
        ds.location = config.BQ_LOCATION()
        c.create_dataset(ds)
    for name, schema in SCHEMAS.items():
        try:
            c.get_table(tid(name))
            continue
        except NotFound:
            pass
        t = bq.Table(tid(name), schema=schema)
        t.time_partitioning = bq.TimePartitioning(type_=bq.TimePartitioningType.DAY, field='run_date')
        t.clustering_fields = CLUSTER[name]
        c.create_table(t)
    print(f'Dataset {config.BQ_DATASET()} ready ({len(SCHEMAS)} tables)')


def _jsonable(rows):
    for r in rows:
        yield {k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in r.items()}


def load_tables(T, c=None):
    """Replace the run_date partition(s) of every table with the given rows (re-running a week is safe)."""
    c = c or client()
    dates = sorted({r['run_date'] for rows in T.values() for r in rows if r.get('run_date')})
    for name, rows in T.items():
        for d in dates:
            part = [r for r in rows if r['run_date'] == d]
            if name == 'raw_tasks':
                part = [dict(r, task=json.loads(r['task'])) for r in part]  # JSON column takes the object itself
            # no partitioning/clustering here: the table already defines them and BigQuery rejects a mismatch
            job_cfg = bq.LoadJobConfig(schema=SCHEMAS[name], write_disposition='WRITE_TRUNCATE',
                                       source_format=bq.SourceFormat.NEWLINE_DELIMITED_JSON)
            dest = f"{tid(name)}${d.replace('-', '')}"
            if not part:  # still clear the partition so a re-run without data does not leave stale rows
                c.query(f"DELETE FROM `{tid(name)}` WHERE run_date = '{d}'").result()
                continue
            c.load_table_from_json(list(_jsonable(part)), dest, job_config=job_cfg).result()
            print(f'  {name:15s} {d}  {len(part):6d} rows')


def fetch_tables(history_runs=26, detail_runs=2, c=None):
    """Read what the dashboard needs: full detail for the newest runs, slim rows for older ones."""
    c = c or client()
    q = lambda sql: [dict(r) for r in c.query(sql).result()]
    runs = [r['run_date'].isoformat() for r in q(f"SELECT DISTINCT run_date FROM `{tid('serp_summary')}` ORDER BY run_date DESC LIMIT {int(history_runs)}")]
    if not runs:
        raise SystemExit('No runs in BigQuery yet')
    detail, slim = runs[:detail_runs], runs[detail_runs:]
    inn = lambda ds: '(' + ','.join(f"DATE '{d}'" for d in ds) + ')' if ds else '(NULL)'
    T = {}
    T['serp_summary'] = q(f"SELECT * EXCEPT(fetched_at) FROM `{tid('serp_summary')}` WHERE run_date IN {inn(runs)}")
    T['serp_results'] = q(f"SELECT * FROM `{tid('serp_results')}` WHERE run_date IN {inn(detail)}") + \
        q(f"SELECT run_date, city, keyword, rank_group, domain FROM `{tid('serp_results')}` WHERE run_date IN {inn(slim)}")
    T['local_pack'] = q(f"SELECT * FROM `{tid('local_pack')}` WHERE run_date IN {inn(detail)}")
    T['ai_answers'] = q(f"SELECT * EXCEPT(fetched_at) FROM `{tid('ai_answers')}` WHERE run_date IN {inn(detail)}") + \
        q(f"SELECT * EXCEPT(fetched_at, markdown) FROM `{tid('ai_answers')}` WHERE run_date IN {inn(slim)}")
    T['ai_citations'] = q(f"SELECT * FROM `{tid('ai_citations')}` WHERE run_date IN {inn(detail)}")
    T['run_log'] = q(f"SELECT run_date, engine, city, keyword, status, error, cost FROM `{tid('run_log')}` WHERE run_date IN {inn(runs)}")
    # ChatGPT comes from FolloEngine (read in place), not from our own scrape
    from . import folloengine
    try:
        ga, gc, info = folloengine.fetch(c, runs, detail)
        T['ai_answers'] = [a for a in T['ai_answers'] if a['engine'] != 'cgpt'] + ga
        T['ai_citations'] = [a for a in T['ai_citations'] if a['engine'] != 'cgpt'] + gc
        T['_gpt'] = info
        print(f"ChatGPT (FolloEngine): {len(ga)} answers for {len((info or {}).get('mapped') or {})} runs", flush=True)
    except Exception as e:  # never block the dashboard on the FolloEngine source
        print(f'WARNING: FolloEngine ChatGPT not read: {e!r}', flush=True)
        T['_gpt'] = dict(error=str(e)[:200])
    T.update(fetch_gbp(c, q))
    try:  # kept news of the last 26 news runs; the dashboard dedupes and marks what is new
        T['news_items'] = q(f"SELECT * EXCEPT(fetched_at) FROM `{tid('news_items')}` WHERE keep AND run_date IN "
                            f"(SELECT DISTINCT run_date FROM `{tid('news_items')}` ORDER BY run_date DESC LIMIT {int(history_runs)})")
    except Exception as e:  # table missing before the first news run
        print(f'No news yet ({e.__class__.__name__})')
    for name, rows in T.items():
        if name.startswith('_'):
            continue
        for r in rows:
            r['run_date'] = r['run_date'].isoformat() if hasattr(r['run_date'], 'isoformat') else r['run_date']
    return T


def fetch_gbp(c, q, history_runs=26):
    """Business profile tables: everything for the newest run, profile history (slim) for older runs."""
    try:
        runs = [r['run_date'].isoformat() for r in q(f"SELECT DISTINCT run_date FROM `{tid('gbp_profiles')}` ORDER BY run_date DESC LIMIT {int(history_runs)}")]
    except Exception as e:  # table missing before the first business profile run
        print(f'No business profile data yet ({e.__class__.__name__})')
        return {}
    if not runs:
        return {}
    last = f"DATE '{runs[0]}'"
    inn = '(' + ','.join(f"DATE '{d}'" for d in runs) + ')'
    out = {
        'gbp_profiles': q(f"SELECT * FROM `{tid('gbp_profiles')}` WHERE run_date = {last}")
        + q(f"SELECT run_date, cid, is_own, rating, votes, total_photos FROM `{tid('gbp_profiles')}` WHERE run_date IN {inn} AND run_date < {last}"),
        'gbp_competitors': q(f"SELECT * FROM `{tid('gbp_competitors')}` WHERE run_date = {last}"),
        'gbp_reviews': q(f"SELECT * EXCEPT(text, owner_answer), IF(is_own, text, NULL) AS text, IF(is_own, owner_answer, NULL) AS owner_answer, "
                         f"owner_answer IS NOT NULL AS answered FROM `{tid('gbp_reviews')}` WHERE run_date = {last}"),
        'gbp_posts': q(f"SELECT run_date, cid, is_own, posted_at, IF(is_own, text, NULL) AS text, url FROM `{tid('gbp_posts')}` WHERE run_date = {last}"),
    }
    out.update(fetch_gbp_perf(q))
    return out


def fetch_gbp_perf(q):
    """Performance data, aggregated in BigQuery to months and 28-day windows so the page stays small."""
    try:
        pr = q(f"SELECT MAX(run_date) AS d FROM `{tid('gbp_locations')}`")[0]['d']
    except Exception as e:
        print(f'No performance data yet ({e.__class__.__name__})')
        return {}
    if not pr:
        return {}
    d = f"DATE '{pr.isoformat()}'"
    return {
        'gbp_locations': q(f"SELECT * FROM `{tid('gbp_locations')}` WHERE run_date = {d}"),
        'gbp_perf_monthly': q(f"SELECT location_id, FORMAT_DATE('%Y-%m', date) AS month, metric, SUM(value) AS value FROM `{tid('gbp_perf_daily')}` "
                              f"WHERE run_date = {d} GROUP BY 1, 2, 3"),
        'gbp_perf_window': q(f"WITH m AS (SELECT MAX(date) AS e FROM `{tid('gbp_perf_daily')}` WHERE run_date = {d}) "
                             f"SELECT location_id, metric, SUM(IF(date > e - 28, value, 0)) AS cur, SUM(IF(date <= e - 28 AND date > e - 56, value, 0)) AS prev, "
                             f"SUM(IF(date > e - 393 AND date <= e - 365, value, 0)) AS yoy, ANY_VALUE(e) AS end_date "
                             f"FROM `{tid('gbp_perf_daily')}`, m WHERE run_date = {d} GROUP BY 1, 2"),
        'gbp_keywords': q(f"SELECT location_id, keyword, month, value, threshold FROM `{tid('gbp_keywords')}` WHERE run_date = {d}"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup')
    l = sub.add_parser('load')
    l.add_argument('raw', nargs='+')
    lg = sub.add_parser('load-gbp')
    lg.add_argument('raw', nargs='+')
    ln = sub.add_parser('load-news')
    ln.add_argument('raw', nargs='+')
    a = ap.parse_args(argv)
    c = client()
    if a.cmd == 'setup':
        setup(c)
    elif a.cmd == 'load-news':
        from . import news
        setup(c)
        for p in a.raw:
            print(f'Loading {p}')
            load_tables(news.transform_files([p]), c)
    elif a.cmd == 'load-gbp':
        from . import gbp
        setup(c)
        from . import gbp_perf
        for p in a.raw:
            print(f'Loading {p}')
            load_tables((gbp_perf if 'gbpperf_' in p else gbp).transform_files([p]), c)
    else:
        setup(c)
        for p in a.raw:
            print(f'Loading {p}')
            load_tables(transform.transform_files([p], keep_raw=True), c)


if __name__ == '__main__':
    main()
