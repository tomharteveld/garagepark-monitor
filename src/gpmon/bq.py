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
}
CLUSTER = {'serp_summary': ['city', 'keyword'], 'serp_results': ['domain', 'city'], 'local_pack': ['city'],
           'ai_answers': ['engine', 'city'], 'ai_citations': ['engine', 'domain'], 'run_log': ['engine'], 'raw_tasks': ['engine', 'city']}


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
    dates = sorted({r['run_date'] for r in T['run_log']})
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
            print(f'  {name:13s} {d}  {len(part):6d} rows')


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
    for rows in T.values():
        for r in rows:
            r['run_date'] = r['run_date'].isoformat() if hasattr(r['run_date'], 'isoformat') else r['run_date']
    return T


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup')
    l = sub.add_parser('load')
    l.add_argument('raw', nargs='+')
    a = ap.parse_args(argv)
    c = client()
    if a.cmd == 'setup':
        setup(c)
    else:
        setup(c)
        for p in a.raw:
            print(f'Loading {p}')
            load_tables(transform.transform_files([p], keep_raw=True), c)


if __name__ == '__main__':
    main()
