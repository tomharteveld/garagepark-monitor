"""Google Bedrijfsprofielen (Google Business Profiles) of GaragePark and its local competitors, via DataForSEO.

Fetch (writes data/raw/gbp_<YYYY-MM-DD>.jsonl.gz):
  1. Business Listings search: every own profile (domain or phone of GaragePark), with Google's
     "people also search" list per profile, which gives the nearby competitors.
  2. Standard Queue tasks per profile (own + competitors): profile info, reviews, posts.

Transform: gbp_profiles, gbp_competitors, gbp_reviews, gbp_posts, gbp_run_log (one row set per run_date).

Usage:
  python -m gpmon.gbp                    # full fetch for today's UTC date
  python -m gpmon.gbp --limit 3          # smoke test: 3 own profiles, their competitors capped at 3
Env: DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD
"""
import argparse
import datetime as dt
import gzip
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .scrape import API, QUEUE_WAIT, Client

TABLES = ('gbp_profiles', 'gbp_competitors', 'gbp_reviews', 'gbp_posts', 'gbp_run_log')
FN = {'info': 'my_business_info', 'reviews': 'reviews', 'posts': 'my_business_updates'}
NOW = lambda: dt.datetime.now(dt.timezone.utc).isoformat()


# ==== fetch ====

SEARCH_COST, POST_COST = [], {}  # costs of the live listing searches of this run (the queued tasks carry their own cost)


def search_own(cli, g):
    """All own profiles from the Business Listings database (paged)."""
    flt = [['domain', 'like', f"%{g['own_domain']}%"], 'or', ['phone', '=', g['own_phone']]]
    out, offset = [], 0
    while True:
        body = [{'filters': flt, 'limit': 100, 'offset': offset}]
        r = cli.session().post(API + '/v3/business_data/business_listings/search/live', json=body, timeout=120).json()
        t = (r.get('tasks') or [{}])[0]
        if t.get('status_code') != 20000:
            raise SystemExit(f"Business listings search failed: {t.get('status_code')} {t.get('status_message')}")
        SEARCH_COST.append(t.get('cost') or 0)
        res = (t.get('result') or [{}])[0]
        items = res.get('items') or []
        out += items
        offset += len(items)
        if not items or offset >= (res.get('total_count') or 0):
            break
    own = [i for i in out if (i.get('address_info') or {}).get('country_code', 'NL') == 'NL']
    print(f'Own profiles found: {len(own)}', flush=True)
    return own


def nearby_competitors(cli, lat, lon, exclude, n, radius_km=10):
    """Competitors around a park that has no 'people also search' list yet (for example a new park)."""
    body = [{'categories': ['self_storage_facility', 'storage_facility'], 'location_coordinate': f'{lat:.6f},{lon:.6f},{radius_km}',
             'order_by': ['rating.votes_count,desc'], 'limit': 20}]
    try:
        r = cli.session().post(API + '/v3/business_data/business_listings/search/live', json=body, timeout=120).json()
        SEARCH_COST.append((r.get('tasks') or [{}])[0].get('cost') or 0)
        items = (((r.get('tasks') or [{}])[0].get('result') or [{}])[0] or {}).get('items') or []
    except Exception as e:
        print(f'  nearby search failed: {e!r}', flush=True)
        return []
    return [dict(cid=i['cid'], title=i.get('title')) for i in items if i.get('cid') and i['cid'] not in exclude][:n]


def api_own_locations(g):
    """Locations in the Google Business Profile accounts Follo manages (source of truth for new parks)."""
    from . import gbp_perf
    if not config.env('GBP_OAUTH_JSON'):
        return []
    try:
        _, locs = gbp_perf.own_locations(gbp_perf.Api(gbp_perf.load_oauth()), g)
        print(f'Business Profile accounts: {len(locs)} GaragePark locations', flush=True)
        return locs
    except (SystemExit, Exception) as e:  # never let the API block the public profile data
        print(f'WARNING: Business Profile API not used ({e})', flush=True)
        return []


def is_own(item, g):
    dom = (item.get('domain') or '').lower()
    return g['own_domain'] in dom or (item.get('phone') or '') == g['own_phone']


def pick_competitors(own, g, per_location):
    """Nearby competitors per own profile from Google's 'people also search'. Own siblings are skipped."""
    own_cids = {o['cid'] for o in own}
    own_re = re.compile(g['own_name_pattern'], re.I)
    links, comps = [], {}
    for o in own:
        rank = 0
        for p in o.get('people_also_search') or []:
            cid = p.get('cid')
            if not cid or cid in own_cids or own_re.search(p.get('title') or ''):
                continue
            rank += 1
            links.append(dict(own_cid=o['cid'], comp_cid=cid, rank=rank))
            comps[cid] = p.get('title')
            if rank >= per_location:
                break
    return links, comps


def post_tasks(cli, kind, items, g):
    """items: list of (cid, extra_payload). Returns {task_id: (kind, cid)} and the list that failed to post."""
    path = f"/v3/business_data/google/{FN[kind]}/task_post"
    pending, failed = {}, []
    for i in range(0, len(items), 100):
        batch = items[i:i + 100]
        body = [dict(keyword=f'cid:{cid}', location_code=g['location_code'], language_code=g['language_code'], tag=str(n), **extra)
                for n, (cid, extra) in enumerate(batch)]
        try:
            resp = cli.session().post(API + path, json=body, timeout=120).json()
        except Exception as e:
            print(f'  {kind} task_post failed: {e!r}', flush=True)
            failed += [cid for cid, _ in batch]
            continue
        for t in resp.get('tasks') or []:
            n = (t.get('data') or {}).get('tag')
            if n is None:
                continue
            cid = batch[int(n)][0]
            if t.get('status_code') == 20100:
                pending[t['id']] = (kind, cid)
                POST_COST[t['id']] = t.get('cost') or 0  # DataForSEO charges at task_post; task_get reports 0
            else:
                failed.append(cid)
    return pending, failed


def collect(cli, pending, workers, max_wait_min, poll_s=45):
    """Poll task_get until every task is done or the deadline passes."""
    done, errors = [], []

    def get(tid):
        kind, cid = pending[tid]
        try:
            r = cli.session().get(f"{API}/v3/business_data/google/{FN[kind]}/task_get/{tid}", timeout=60).json()
            t = (r.get('tasks') or [{}])[0]
        except Exception as e:
            return tid, 'wait', repr(e)
        code = t.get('status_code')
        if code == 20000:
            return tid, 'ok', t
        if code == 40102:  # "No Search Results": a profile without reviews or posts, a valid empty answer
            return tid, 'ok', dict(t, result=[{'items': [], 'items_count': 0}])
        if code in QUEUE_WAIT or code is None:
            return tid, 'wait', None
        return tid, 'fail', f"{code} {t.get('status_message')}"

    deadline = time.time() + max_wait_min * 60
    while pending and time.time() < deadline:
        time.sleep(poll_s)
        with ThreadPoolExecutor(workers) as ex:
            for tid, state, val in ex.map(get, list(pending)):
                if state == 'wait':
                    continue
                kind, cid = pending.pop(tid)
                (done if state == 'ok' else errors).append((kind, cid, val))
        print(f'  gbp queue: {len(done)} done, {len(errors)} failed, {len(pending)} waiting', flush=True)
    for tid, (kind, cid) in pending.items():
        errors.append((kind, cid, f'not ready after {max_wait_min} min'))
    return done, errors


def fetch(cli, cfg, run_date, limit=0):
    g = cfg['gbp']
    own = search_own(cli, g)
    for o in own:
        o['_source'] = 'listings'
    api_locs = api_own_locations(g)
    known = {o['cid']: o for o in own}
    for l in api_locs:
        if not l.get('cid'):
            continue  # not (yet) published on Maps; shown in the dashboard from gbp_locations
        if l['cid'] in known:
            known[l['cid']]['_source'] = 'both'
        else:  # a park that is new, or missing from the listings database: still measured
            item = dict(cid=l['cid'], title=l['title'], latitude=l.get('latitude'), longitude=l.get('longitude'), url=l.get('website'),
                        address=l.get('address'), address_info={'city': l.get('city'), 'country_code': 'NL'}, people_also_search=None, _source='api')
            own.append(item)
            known[l['cid']] = item
    if api_locs:
        print(f"Own profiles: {sum(o['_source'] == 'both' for o in own)} in both sources, "
              f"{sum(o['_source'] == 'api' for o in own)} only in the accounts, {sum(o['_source'] == 'listings' for o in own)} only in listings", flush=True)
    if limit:
        own = own[:limit]
    per = 3 if limit else g['competitors_per_location']
    links, comps = pick_competitors(own, g, per)
    linked = {l['own_cid'] for l in links}
    own_cids_all = {o['cid'] for o in own}
    for o in own:
        if o['cid'] not in linked and o.get('latitude'):
            near = nearby_competitors(cli, o['latitude'], o['longitude'], own_cids_all, per)
            for rank, c in enumerate(near, 1):
                links.append(dict(own_cid=o['cid'], comp_cid=c['cid'], rank=rank))
                comps[c['cid']] = c['title']
    own_cids = [o['cid'] for o in own]
    all_cids = own_cids + [c for c in comps if c not in set(own_cids)]
    print(f'{len(own_cids)} own profiles, {len(comps)} competitors', flush=True)

    rd = g['reviews_depth']
    votes = {o['cid']: ((o.get('rating') or {}).get('votes_count') or 0) for o in own}
    jobs = {
        'info': [(c, {}) for c in all_cids],
        'reviews': [(c, {'depth': min(rd['own_max'], max(10, -(-votes[c] // 10) * 10)), 'sort_by': 'newest'}) for c in own_cids]
                   + [(c, {'depth': rd['competitor'], 'sort_by': 'newest'}) for c in comps],
        'posts': [(c, {'depth': g['posts_depth']}) for c in all_cids],
    }
    pending, post_failed = {}, []
    for kind, items in jobs.items():
        p, f = post_tasks(cli, kind, items, g)
        pending.update(p)
        post_failed += [(kind, c, 'task_post refused') for c in f]
    print(f'Posted {len(pending)} tasks ({len(post_failed)} refused)', flush=True)
    done, errors = collect(cli, pending, cfg['scrape']['max_workers'], g.get('max_queue_minutes', 50))
    errors += post_failed
    if errors:
        from collections import Counter
        summary = Counter((k, str(e)[:60]) for k, _, e in errors)
        print('Failed tasks by kind and reason:', flush=True)
        for (k, e), n in summary.most_common(10):
            print(f'  {n:>4}  {k:<8} {e}', flush=True)

    recs = [dict(kind='listing', cid=o['cid'], own=True, run_date=run_date, fetched_at=NOW(), item=o) for o in own]
    recs += [dict(kind='link', run_date=run_date, **l) for l in links]
    own_set = set(own_cids)
    for kind, cid, task in done:
        recs.append(dict(kind=kind, cid=cid, own=cid in own_set, run_date=run_date, fetched_at=NOW(),
                         cost=POST_COST.get(task.get('id'), task.get('cost') or 0), task=task))
    recs.append(dict(kind='search', cid=None, own=None, run_date=run_date, fetched_at=NOW(), cost=round(sum(SEARCH_COST), 5),
                     task={'searches': len(SEARCH_COST)}))
    for kind, cid, err in errors:
        recs.append(dict(kind=kind, cid=cid, own=cid in own_set, run_date=run_date, fetched_at=NOW(), cost=0, task=None, error=err))
    return recs, len(done), len(errors)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument('--out', default=None)
    ap.add_argument('--limit', type=int, default=0, help='only the first N own profiles (smoke test)')
    ap.add_argument('--max-error-rate', type=float, default=0.15)
    a = ap.parse_args(argv)
    cfg = config.load()
    cli = Client(config.env('DATAFORSEO_LOGIN', required=True), config.env('DATAFORSEO_PASSWORD', required=True))
    cli.balance()
    recs, ok, bad = fetch(cli, cfg, a.date, a.limit)
    out = Path(a.out or config.ROOT / 'data' / 'raw' / f'gbp_{a.date}.jsonl.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, 'wt', encoding='utf-8') as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    cost = sum(r.get('cost') or 0 for r in recs if r.get('kind') in ('info', 'reviews', 'posts', 'search'))
    print(f'Wrote {out.name} | tasks ok {ok} | failed {bad} | cost ${cost:.2f}')
    if bad > a.max_error_rate * max(ok + bad, 1):
        sys.exit(f'Error rate too high ({bad}/{ok + bad}); not loading this run')


# ==== transform ====

def _ts(s):
    """'2026-07-29 01:38:42 +00:00' -> ISO timestamp BigQuery accepts."""
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, '%Y-%m-%d %H:%M:%S %z').isoformat()
    except ValueError:
        return None


def _hours(info):
    tt = ((info.get('work_time') or {}).get('work_hours') or {}).get('timetable') or {}
    return bool(tt), all(any((s.get('close') or {}).get('hour') == 24 and not (s.get('open') or {}).get('hour') for s in (tt.get(d) or []))
                         for d in tt) if tt else False


def _attrs(info):
    av = ((info.get('attributes') or {}).get('available_attributes') or {})
    return sorted({f'{k}:{v}' for k, vs in av.items() for v in (vs or [])})


def transform(records):
    cfg = config.load()
    g = cfg['gbp']
    T = {t: [] for t in TABLES}
    listings = {r['cid']: r['item'] for r in records if r.get('kind') == 'listing'}
    own_cids = set(listings)
    city_of = {cid: (it.get('address_info') or {}).get('city') for cid, it in listings.items()}
    comp_parent = {}
    for r in records:
        if r.get('kind') == 'link':
            T['gbp_competitors'].append(dict(run_date=r['run_date'], own_cid=r['own_cid'], comp_cid=r['comp_cid'], rank=r['rank']))
            comp_parent.setdefault(r['comp_cid'], r['own_cid'])
    infos = {}
    for r in records:
        if r.get('kind') == 'search':
            T['gbp_run_log'].append(dict(run_date=r['run_date'], kind='search', cid=None, is_own=None, status='ok', error=None,
                                         cost=r.get('cost') or 0))
        if r.get('kind') not in FN:
            continue
        T['gbp_run_log'].append(dict(run_date=r['run_date'], kind=r['kind'], cid=r['cid'], is_own=r['cid'] in own_cids,
                                     status='ok' if r.get('task') else 'error', error=r.get('error'), cost=r.get('cost') or 0))
        if not r.get('task'):
            continue
        res = (r['task'].get('result') or [{}])[0] or {}
        if r['kind'] == 'info':
            it = next((i for i in res.get('items') or [] if i.get('type') == 'google_business_info'), None)
            if it:
                infos[r['cid']] = it
        elif r['kind'] == 'reviews':
            for i in res.get('items') or []:
                txt = (i.get('review_text') or '').strip()
                T['gbp_reviews'].append(dict(
                    run_date=r['run_date'], cid=r['cid'], is_own=r['cid'] in own_cids, review_id=i.get('review_id'),
                    rating=(i.get('rating') or {}).get('value'), posted_at=_ts(i.get('timestamp')), time_ago=i.get('time_ago'),
                    text=txt[:1200] if r['cid'] in own_cids else txt[:300], language=i.get('original_language'),
                    has_text=bool(txt), photos=bool(i.get('images')), local_guide=bool(i.get('local_guide')),
                    owner_answer=(i.get('owner_answer') or '')[:800] or None, answered_at=_ts(i.get('owner_timestamp')),
                    review_url=i.get('review_url')))
        elif r['kind'] == 'posts':
            for i in res.get('items') or []:
                T['gbp_posts'].append(dict(
                    run_date=r['run_date'], cid=r['cid'], is_own=r['cid'] in own_cids,
                    posted_at=_ts(i.get('timestamp')), text=(i.get('post_text') or '')[:600],
                    url=i.get('url'), has_image=bool(i.get('images_url') or i.get('image_url')),
                    links=[l.get('url') for l in (i.get('links') or []) if l.get('url')][:5]))

    run_dates = sorted({r['run_date'] for r in records if r.get('run_date')})
    rd = run_dates[-1] if run_dates else None
    for cid in sorted(set(infos) | own_cids | set(comp_parent)):
        it = infos.get(cid) or listings.get(cid)
        if not it:
            continue
        lst = listings.get(cid) or {}
        has_hours, open_247 = _hours(it)
        rating = it.get('rating') or {}
        dist = it.get('rating_distribution') or {}
        T['gbp_profiles'].append(dict(
            run_date=rd, cid=cid, is_own=cid in own_cids,
            parent_cid=None if cid in own_cids else comp_parent.get(cid),
            title=it.get('title'), city=(it.get('address_info') or {}).get('city') or city_of.get(comp_parent.get(cid)),
            address=it.get('address'), latitude=it.get('latitude'), longitude=it.get('longitude'),
            domain=it.get('domain'), url=it.get('url'), phone=it.get('phone'),
            category=it.get('category'), additional_categories=list(it.get('additional_categories') or []),
            description=it.get('description'), is_claimed=it.get('is_claimed'),
            rating=rating.get('value'), votes=rating.get('votes_count'),
            stars=[int(dist.get(str(s)) or 0) for s in range(1, 6)] if dist else [],
            total_photos=it.get('total_photos'), has_logo=bool(it.get('logo')), has_hours=has_hours, open_247=open_247,
            attributes=_attrs(it), services=[(s.get('title') or s.get('name') or str(s))[:80] for s in (it.get('services') or [])
                                               if isinstance(s, dict)] if isinstance(it.get('services'), list) else [],
            place_topics=[f'{k}:{v}' for k, v in (it.get('place_topics') or {}).items()],
            book_online_url=it.get('book_online_url'), contact_url=it.get('contact_url'),
            from_listing_only=cid not in infos, check_url=f'https://www.google.com/maps?cid={cid}',
            listing_updated=_ts(lst.get('last_updated_time')), source=lst.get('_source') if cid in own_cids else None))
    return T


def transform_files(paths):
    recs = []
    for p in paths:
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            recs += [json.loads(l) for l in fh if l.strip()]
    return transform(recs)


if __name__ == '__main__':
    main()
