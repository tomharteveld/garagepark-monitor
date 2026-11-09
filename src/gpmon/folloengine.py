"""ChatGPT answers from FolloEngine, read in place from its BigQuery table (no scraping, no cost).

FolloEngine measures a fixed prompt set once a week. Each dashboard run gets the newest FolloEngine
measurement on or before its run date (at most `max_age_days` old), and the answers are turned into the
same ai_answers / ai_citations rows as AI Mode (engine 'cgpt'), so every ChatGPT view of the dashboard
works unchanged. Mentions and citations of GaragePark use FolloEngine's own flags; brands and sources
are derived with the same rules as the other engines.

Prompts "Waar kan ik een <garagebox|opslagruimte> huren in de omgeving van <plaats>?" get that city and
keyword; all other prompts get city 'Algemeen' and the prompt itself as keyword.
"""
import re

from . import config
from .transform import _ref, answer_row

CITY_RX = re.compile(r'een (?P<kw>.+?) in de omgeving van (?P<city>[^?]+)\??\s*$', re.I)
GENERAL = 'Algemeen'
UTM = re.compile(r'(?<=[?&])utm_source=chatgpt\.com&?')

SQL = """
SELECT scraped_date AS d, prompt, ANY_VALUE(model) AS model, ANY_VALUE(full_markdown) AS md,
       LOGICAL_OR(calc_our_mention_in_answer = 'yes') AS m, LOGICAL_OR(calc_our_citation_in_answer = 'yes') AS c,
       ARRAY_AGG(IF(is_citation_row = 1, STRUCT(citation_rank AS rnk, citation_url AS url, citation_title AS title,
                                                 citation_snippet AS snippet, citation_domain AS domain), NULL)
                 IGNORE NULLS ORDER BY citation_rank) AS cites
FROM `{table}` WHERE scraped_date IN UNNEST(@dates) AND prompt IS NOT NULL
GROUP BY 1, 2"""


def source():
    return (config.load().get('chatgpt_source') or {})


def map_runs(runs, fe_dates, max_age_days=10):
    """{run_date: folloengine_date}: the newest measurement on or before each run, if recent enough."""
    import datetime as dt
    fe = sorted(fe_dates)
    out = {}
    for r in runs:
        rd = dt.date.fromisoformat(r)
        prev = [d for d in fe if dt.date.fromisoformat(d) <= rd]
        if prev and (rd - dt.date.fromisoformat(prev[-1])).days <= max_age_days:
            out[r] = prev[-1]
    return out


def split_prompt(prompt, cfg):
    m = CITY_RX.search(prompt or '')
    if not m:
        return GENERAL, prompt.strip()
    city = m.group('city').strip()
    known = {c.lower(): c for c in cfg['cities']}
    city = known.get(city.lower(), city[:1].upper() + city[1:])
    return city, m.group('kw').strip().lower()


def to_tables(rows, run_map, detail):
    """rows: one dict per (FolloEngine date, prompt) as SQL returns them. Returns ai_answers, ai_citations."""
    cfg = config.load()
    by_date = {}
    for r in rows:
        by_date.setdefault(r['d'], []).append(r)
    answers, cites = [], []
    for run, fe_date in sorted(run_map.items()):
        for r in by_date.get(fe_date, []):
            city, kw = split_prompt(r['prompt'], cfg)
            refs, seen, pos = [], set(), 1
            for x in r.get('cites') or []:
                pos = _ref(cfg, dict(url=UTM.sub('', x.get('url') or '').rstrip('?&'), domain=x.get('domain'), title=x.get('title'), snippet=x.get('snippet')),
                           'ref', seen, refs, pos)
            rec = dict(run_date=run, city=city, keyword=kw, volume=int(cfg['keywords'].get(kw, 0) or 0), prompt=r['prompt'])
            a = answer_row(cfg, rec, 'cgpt', r.get('md') or '', refs, model=r.get('model'))
            # FolloEngine's own judgement of GaragePark in the answer is leading
            a['mentioned'], a['cited'] = bool(r['m']), bool(r['c'])
            if a['mentioned'] and cfg['client']['own_brand'] not in a['brands_mentioned']:
                a['brands_mentioned'] = sorted(a['brands_mentioned'] + [cfg['client']['own_brand']])
            # keep the split site/profile consistent with FolloEngine's citation flag
            a['cited_gbp'] = a['cited'] and a['cited_gbp']
            a['cited_web'] = a['cited'] and (a['cited_web'] or not a['cited_gbp'])
            a.pop('fetched_at', None)
            a['fe_date'] = fe_date
            if run not in detail:
                a['markdown'] = None
            else:
                cites += [dict(run_date=run, city=city, keyword=kw, engine='cgpt', **c) for c in refs]
            answers.append(a)
    return answers, cites


def fetch(client, runs, detail):
    """Read FolloEngine for the given dashboard runs. Returns (ai_answers, ai_citations, info)."""
    from google.cloud import bigquery as bq
    s = source()
    if not s.get('table'):
        return [], [], None
    t = s['table']
    fe_dates = [r['d'].isoformat() for r in client.query(
        f"SELECT DISTINCT scraped_date AS d FROM `{t}` WHERE scraped_date IS NOT NULL").result()]
    run_map = map_runs(runs, fe_dates, int(s.get('max_age_days', 10)))
    if not run_map:
        return [], [], dict(table=t, last=max(fe_dates) if fe_dates else None, mapped={})
    job = client.query(SQL.format(table=t), job_config=bq.QueryJobConfig(
        query_parameters=[bq.ArrayQueryParameter('dates', 'DATE', sorted(set(run_map.values())))]))
    rows = []
    for r in job.result():
        r = dict(r)
        r['d'] = r['d'].isoformat()
        r['cites'] = [dict(x) for x in r.get('cites') or []]
        rows.append(r)
    answers, cites = to_tables(rows, run_map, set(detail))
    return answers, cites, dict(table=t, last=max(fe_dates), mapped=run_map)
