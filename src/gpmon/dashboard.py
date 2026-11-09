"""Build the dashboard (site/index.html) from tidy tables, read from BigQuery or from local raw files.

Usage:
  python -m gpmon.dashboard --source bq                 # production: history from BigQuery
  python -m gpmon.dashboard --source local              # all data/raw/run_*.jsonl.gz, no cloud needed
Output: site/<DASHBOARD_PATH>/index.html, plus robots.txt and an empty root page
"""
import argparse
import base64
import json
from collections import defaultdict
from pathlib import Path

from . import config, transform

DASH = config.ROOT / 'dashboard'


def _d(v):
    return v.isoformat() if hasattr(v, 'isoformat') else v


def build_data(T, detail_runs=2):
    cfg = config.load()
    runs = sorted({_d(r['run_date']) for r in T['serp_summary']})
    detail = set(runs[-detail_runs:])

    org, lp, refs = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in T['serp_results']:
        k = (_d(r['run_date']), r['city'], r['keyword'])
        o = dict(p=r['rank_group'], d=r['domain'])
        if k[0] in detail:
            o.update(u=r.get('url'), t=r.get('title') or '', w=r.get('website_name') or '')
        org[k].append(o)
    for r in T['local_pack']:
        lp[(_d(r['run_date']), r['city'], r['keyword'])].append(dict(t=r['title'], rt=r.get('rating'), n=r.get('votes'), o=1 if r['is_own'] else 0, _p=r['position']))
    for r in T['ai_citations']:
        refs[(r['engine'], _d(r['run_date']), r['city'], r['keyword'])].append(dict(
            d=r['domain'], u=r['url'], t=r.get('title') or '', x=r.get('snippet') or '', g=1 if r['is_gbp'] else 0, b=r.get('brand'), k=r.get('kind'), _p=r.get('position') or 0))

    serps = []
    for s in sorted(T['serp_summary'], key=lambda s: (_d(s['run_date']), s['city'], s['keyword'])):
        k = (_d(s['run_date']), s['city'], s['keyword'])
        serps.append(dict(r=k[0], c=s['city'], k=s['keyword'], v=s['volume'], pos=s.get('own_position'), url=s.get('own_url'),
                          n_own=s.get('own_results') or 0, f=list(s.get('features') or []),
                          org=sorted(org.get(k, []), key=lambda o: o['p']),
                          lp=[{kk: vv for kk, vv in x.items() if kk != '_p'} for x in sorted(lp.get(k, []), key=lambda x: x['_p'])],
                          lpo=1 if s.get('in_local_pack') else 0, chk=s.get('check_url'), dt=k[0] in detail))
    ai = []
    for a in sorted(T['ai_answers'], key=lambda a: (_d(a['run_date']), a['engine'], a['city'], a['keyword'])):
        k = (a['engine'], _d(a['run_date']), a['city'], a['keyword'])
        ai.append(dict(e=a['engine'], r=k[1], c=a['city'], k=a['keyword'], v=a['volume'], p=a.get('prompt') or a['keyword'],
                       md=a.get('markdown'), refs=[{kk: vv for kk, vv in x.items() if kk != '_p'} for x in sorted(refs.get(k, []), key=lambda x: x['_p'])],
                       m=1 if a['mentioned'] else 0, ci=1 if a['cited'] else 0, cw=1 if a['cited_web'] else 0, cg=1 if a['cited_gbp'] else 0,
                       mb=list(a.get('brands_mentioned') or []), cb=list(a.get('brands_cited') or []), dt=k[1] in detail))
    costs, errors = defaultdict(float), []
    for r in T['run_log']:
        costs[_d(r['run_date'])] += r.get('cost') or 0
        if r.get('status') == 'error':
            errors.append(dict(r=_d(r['run_date']), e=r['engine'], c=r['city'], k=r['keyword'], err=r.get('error')))
    meta = dict(runs=runs, detail_runs=sorted(detail), costs={k: round(v, 2) for k, v in costs.items()},
                cost=round(costs.get(runs[-1], 0), 2), errors=errors, own_domain=cfg['client']['own_domain'],
                own_brand=cfg['client']['own_brand'], engines=sorted({a['e'] for a in ai}),
                prompt_template=cfg['scrape']['prompt_template'],
                gpt=dict(source='FolloEngine', last=(T.get('_gpt') or {}).get('last'),
                         dates=(T.get('_gpt') or {}).get('mapped') or {}) if T.get('_gpt') else None)
    from . import gbp_view
    gbp = gbp_view.build(T) if T.get('gbp_profiles') else None
    meta['gbp_run'] = gbp['run'] if gbp else None
    return dict(meta=meta, serps=serps, ai=ai, gbp=gbp, news=build_news(T.get('news_items') or []),
                agg=ga_build(T.get('ga_aggregators') or []))


def ga_build(rows):
    from . import ga
    return ga.build(rows)


def build_news(rows, limit=400):
    """One entry per story (id), first_seen = first news run that found it; newest first."""
    if not rows:
        return None
    runs = sorted({_d(r['run_date']) for r in rows})
    by = {}
    for r in sorted(rows, key=lambda r: _d(r['run_date'])):
        x = by.get(r['id'])
        if x:
            x['q'] = sorted(set(x['q']) | {r.get('query') or ''})
            continue
        by[r['id']] = dict(id=r['id'], t=r['title'], u=r['url'], d=r.get('domain') or '', p=r.get('publisher') or '',
                           s=(r.get('snippet') or '')[:300], dt=_d(r.get('published')) or _d(r['run_date']),
                           g=r['grp'], b=list(r.get('brands') or []), c=list(r.get('cities') or []), tp=r.get('topic') or 'Overig',
                           src=r.get('source'), q=[r.get('query') or ''], fs=_d(r['run_date']))
    items = sorted(by.values(), key=lambda x: (x['dt'] or '', x['fs']), reverse=True)[:limit]
    return dict(runs=runs, last=runs[-1], items=items)


HEAD = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
"""

ROBOTS = "User-agent: *\nDisallow: /\n"
ROOT_PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="robots" content="noindex, nofollow">
<title>Niet gevonden</title></head><body style="font-family:system-ui;padding:40px;color:#4B4753">Deze pagina bestaat niet.</body></html>"""


def render(data, out_dir, secret_path=None):
    """Write the dashboard. With secret_path the page lives at <out>/<secret_path>/index.html and the
    site root only serves an empty page plus robots.txt, so the dashboard URL cannot be guessed."""
    out = Path(out_dir)
    page_dir = out / secret_path.strip('/') if secret_path else out
    page_dir.mkdir(parents=True, exist_ok=True)
    b64 = lambda p: 'data:image/png;base64,' + base64.b64encode((DASH / 'assets' / p).read_bytes()).decode()
    tpl = (DASH / 'template.html').read_text(encoding='utf-8')
    tpl = tpl.replace('__LOGO_WHITE__', b64('follo-logo-white.png')).replace('__LOGO__', b64('follo-logo-transparent.png'))
    tpl = tpl.replace('__NLMAP__', (DASH / 'nlmap.json').read_text(encoding='utf-8').strip())
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'), default=str).replace('</', '<\\/')
    tpl = tpl.replace('__DATA__', payload)
    tpl = tpl.replace('/*__GBP_JS__*/', (DASH / 'gbp_tab.js').read_text(encoding='utf-8'))
    tpl = tpl.replace('/*__NEWS_JS__*/', (DASH / 'news_tab.js').read_text(encoding='utf-8') + '\n' + (DASH / 'agg_tab.js').read_text(encoding='utf-8'))
    split = tpl.index('<div class="app">')  # title, fonts and styles go in <head>, the rest in <body>
    html = HEAD + tpl[:split] + '</head>\n<body>\n' + tpl[split:] + '\n</body>\n</html>\n'
    (page_dir / 'index.html').write_text(html, encoding='utf-8')
    (out / 'robots.txt').write_text(ROBOTS, encoding='utf-8')
    (out / '.nojekyll').write_text('', encoding='utf-8')
    if secret_path:
        (out / 'index.html').write_text(ROOT_PAGE, encoding='utf-8')
        (out / '404.html').write_text(ROOT_PAGE, encoding='utf-8')
    print(f"Wrote {page_dir / 'index.html'} ({len(html.encode()) / 1e6:.2f} MB, {len(data['meta']['runs'])} runs)")
    return page_dir / 'index.html'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', choices=['bq', 'local'], default='bq')
    ap.add_argument('--out', default=str(config.ROOT / 'site'))
    ap.add_argument('--path', default=config.env('DASHBOARD_PATH', ''),
                    help='secret sub path for the page (default: env DASHBOARD_PATH)')
    a = ap.parse_args(argv)
    cfg = config.load()
    dcfg = cfg['dashboard']
    if a.source == 'bq':
        from . import bq
        T = bq.fetch_tables(dcfg['history_runs'], dcfg['detail_runs'])
    else:
        files = sorted((config.ROOT / 'data' / 'raw').glob('run_*.jsonl.gz'))[-dcfg['history_runs']:]
        T = transform.transform_files(files)
        gfiles = sorted((config.ROOT / 'data' / 'raw').glob('gbp_*.jsonl.gz'))
        if gfiles:
            from . import gbp
            G = gbp.transform_files(gfiles[-1:])
            T.update({k: v for k, v in G.items() if k != 'gbp_run_log'})
        afiles = sorted((config.ROOT / 'data' / 'raw').glob('ga_*.jsonl.gz'))
        if afiles:
            from . import ga
            T['ga_aggregators'] = ga.transform_files(afiles[-1:])['ga_aggregators']
        nfiles = sorted((config.ROOT / 'data' / 'raw').glob('news_*.jsonl.gz'))
        if nfiles:
            from . import news
            T['news_items'] = [r for r in news.transform_files(nfiles)['news_items'] if r['keep']]
        pfiles = sorted((config.ROOT / 'data' / 'raw').glob('gbpperf_*.jsonl.gz'))
        if pfiles:
            from . import gbp_perf
            T.update(gbp_perf.aggregate(gbp_perf.transform_files(pfiles[-1:])))
    if not a.path:
        print('WARNING: DASHBOARD_PATH not set, dashboard is published at the site root')
    render(build_data(T, dcfg['detail_runs']), a.out, a.path or None)


if __name__ == '__main__':
    main()
