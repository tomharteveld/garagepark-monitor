"""Turn raw DataForSEO run records into tidy tables (one list of row dicts per BigQuery table)."""
import gzip
import json
import re
from collections import Counter
from urllib.parse import urlparse

from . import config

TABLES = ('serp_summary', 'serp_results', 'local_pack', 'ai_answers', 'ai_citations', 'run_log', 'raw_tasks')
ENGINE_CODE = {'google_organic': 'aio', 'google_ai_mode': 'aim', 'chatgpt': 'cgpt'}
SOCIAL = {'facebook': 'facebook.com', 'instagram': 'instagram.com', 'youtube': 'youtube.com', 'reddit': 'reddit.com',
          'x': 'x.com', 'linkedin': 'linkedin.com', 'tiktok': 'tiktok.com'}


def read_raw(path):
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def clean_domain(d):
    d = (d or '').lower().strip()
    return d[4:] if d.startswith('www.') else d


def organic_domain(i):
    """Mobile SERPs often return google.com/goto redirects; recover the domain from the breadcrumb."""
    d = clean_domain(i.get('domain'))
    url = i.get('url') or ''
    if d.endswith('google.com') and '/goto?' in url:
        bc = str(i.get('breadcrumb') or '')
        if bc.startswith('http'):
            return clean_domain(urlparse(bc).netloc), None
        wn = (i.get('website_name') or '').lower().replace('\xa0', ' ')
        site = wn.split('·')[0].strip()
        return SOCIAL.get(site, wn or 'onbekend'), None
    return d, url


def strip_tracking(u):
    return re.sub(r'[?&]srsltid=[^&#]*', '', u or '')


def brands_in(text, cfg):
    return sorted(b for b, rx in cfg['_brand_res'].items() if rx.search(text or ''))


def _ref(cfg, r, kind, seen, out, pos):
    u = r.get('url') or ''
    if not u or u in seen:
        return pos
    seen.add(u)
    d = clean_domain(r.get('domain') or urlparse(u).netloc)
    gbp = d.endswith('google.com') and ('searchviewer' in u or '/maps' in u)
    title = r.get('title') or r.get('source') or r.get('source_name') or ''
    if gbp:
        hits = brands_in(title, cfg)
        brand, d = (hits[0] if hits else None), 'Google Bedrijfsprofiel'
    else:
        brand = cfg['_domain_to_brand'].get(d)
    out.append(dict(position=pos, domain=d, url=strip_tracking(u), title=title[:200],
                    snippet=(r.get('text') or r.get('snippet') or r.get('description') or '')[:300],
                    is_gbp=gbp, brand=brand, kind=kind))
    return pos + 1


def google_ai_refs(cfg, ai_item):
    out, seen, pos = [], set(), 1
    for r in ai_item.get('references') or []:
        pos = _ref(cfg, r, 'ref', seen, out, pos)
    for el in ai_item.get('items') or []:
        for r in el.get('references') or []:
            pos = _ref(cfg, r, 'ref', seen, out, pos)
        for r in el.get('links') or []:
            pos = _ref(cfg, r, 'link', seen, out, pos)
        for comp in el.get('components') or []:
            for r in (comp.get('links') or []) + (comp.get('references') or []):
                pos = _ref(cfg, r, 'link', seen, out, pos)
    return out


def chatgpt_refs(cfg, res):
    out, seen, pos = [], set(), 1
    for r in res.get('sources') or []:
        pos = _ref(cfg, r, 'ref', seen, out, pos)
    for el in res.get('items') or []:
        for r in el.get('sources') or []:
            pos = _ref(cfg, r, 'ref', seen, out, pos)
    return out


def answer_row(cfg, rec, engine, md, refs, model=None):
    own = cfg['client']
    text_plain = re.sub(r'\]\([^)]*\)', ']', md)  # ignore link targets for brand mentions
    mentioned = brands_in(text_plain, cfg)
    own_web = any(r['domain'] == own['own_domain'] for r in refs)
    own_gbp = any(r['is_gbp'] and r['brand'] == own['own_brand'] for r in refs)
    return dict(run_date=rec['run_date'], engine=engine, city=rec['city'], keyword=rec['keyword'], volume=rec['volume'],
                prompt=rec.get('prompt') or rec['keyword'], markdown=md, model=model,
                mentioned=own['own_brand'] in mentioned, cited=own_web or own_gbp, cited_web=own_web, cited_gbp=own_gbp,
                brands_mentioned=mentioned, brands_cited=sorted({r['brand'] for r in refs if r['brand']}),
                fetched_at=rec.get('fetched_at'))


def transform(records, keep_raw=True):
    cfg = config.load()
    own = cfg['client']
    T = {t: [] for t in TABLES}
    for rec in records:
        base = dict(run_date=rec['run_date'], engine=rec['engine'], city=rec['city'], keyword=rec['keyword'])
        T['run_log'].append(dict(base, prompt=rec.get('prompt'), status='error' if rec.get('error') or not rec.get('task') else 'ok',
                                 error=rec.get('error'), cost=rec.get('cost') or 0, fetched_at=rec.get('fetched_at')))
        if rec.get('error') or not rec.get('task'):
            continue
        if keep_raw:
            T['raw_tasks'].append(dict(base, prompt=rec.get('prompt'), fetched_at=rec.get('fetched_at'),
                                       task=json.dumps(rec['task'], ensure_ascii=False)))
        res = rec['task']['result'][0]
        items = res.get('items') or []
        key = dict(run_date=rec['run_date'], city=rec['city'], keyword=rec['keyword'])

        if rec['engine'] == 'google_organic':
            feats, org, lp, aio = Counter(), [], [], None
            for i in items:
                feats[i['type']] += 1
                if i['type'] == 'organic':
                    d, u = organic_domain(i)
                    org.append(dict(key, rank_group=i['rank_group'], domain=d, url=strip_tracking(u) if u else None,
                                    title=(i.get('title') or '')[:200], website_name=i.get('website_name'), is_own=d == own['own_domain']))
                elif i['type'] == 'local_pack':
                    rt = i.get('rating') or {}
                    lp.append(dict(key, position=len(lp) + 1, title=i.get('title') or '', rating=rt.get('value'),
                                   votes=rt.get('votes_count'), is_own=bool(cfg['_own_re'].search(i.get('title') or ''))))
                elif i['type'] == 'ai_overview' and aio is None:
                    aio = i
            mine = sorted((o for o in org if o['is_own']), key=lambda o: o['rank_group'])
            T['serp_summary'].append(dict(key, location_code=rec['location_code'], volume=rec['volume'],
                                          own_position=mine[0]['rank_group'] if mine else None,
                                          own_url=next((o['url'] for o in mine if o['url']), None), own_results=len(mine),
                                          in_local_pack=any(x['is_own'] for x in lp), features=sorted(feats),
                                          has_ai_overview=aio is not None, check_url=res.get('check_url'), fetched_at=rec.get('fetched_at')))
            T['serp_results'] += org
            T['local_pack'] += lp
            if aio:
                md = aio.get('markdown') or '\n\n'.join((e.get('markdown') or e.get('text') or '') for e in aio.get('items') or [])
                refs = google_ai_refs(cfg, aio)
                T['ai_answers'].append(answer_row(cfg, rec, 'aio', md, refs))
                T['ai_citations'] += [dict(key, engine='aio', **r) for r in refs]

        elif rec['engine'] == 'google_ai_mode':
            ai = next((i for i in items if i['type'] == 'ai_overview'), None)
            if ai:
                md = ai.get('markdown') or '\n\n'.join((e.get('markdown') or e.get('text') or '') for e in ai.get('items') or [])
                refs = google_ai_refs(cfg, ai)
                T['ai_answers'].append(answer_row(cfg, rec, 'aim', md, refs))
                T['ai_citations'] += [dict(key, engine='aim', **r) for r in refs]

        elif rec['engine'] == 'chatgpt':
            md = res.get('markdown') or '\n\n'.join((i.get('markdown') or '') for i in items)
            refs = chatgpt_refs(cfg, res)
            T['ai_answers'].append(answer_row(cfg, rec, 'cgpt', md, refs, model=res.get('model')))
            T['ai_citations'] += [dict(key, engine='cgpt', **r) for r in refs]
    return T


def transform_files(paths, keep_raw=False):
    recs = []
    for p in paths:
        recs += list(read_raw(p))
    return transform(recs, keep_raw=keep_raw)
