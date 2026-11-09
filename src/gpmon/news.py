"""Local news: market, competitor and GaragePark news to react to (Google Alerts RSS + DataForSEO Google News).

Sources
  Google Alerts  RSS feeds of alerts in the Follo Google account (secret NEWS_ALERT_FEEDS, one URL per line).
                 Free, real article URLs.
  DataForSEO     Google News search per query from config `news.queries` plus one local query per park city.

Only news about the Dutch storage market survives: the item must be Dutch, mention the market (self storage,
opslag, garagebox ...) or a tracked brand in a market context, must not be about energy/data storage, and
pages on a competitor's or our own site only count when they sit in a news or press section (no blogs).
Every item is kept with keep/reason, so dropped items can be audited in BigQuery.

Usage: python -m gpmon.news [--limit N] [--date YYYY-MM-DD]   writes data/raw/news_<date>.jsonl.gz
"""
import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from . import config

API = 'https://api.dataforseo.com'
TABLE = 'news_items'

MARKET = re.compile(r'self[\s-]?storage|selfstorage|opslag\s?(ruimte|box|boxen|unit|units|centrum|bedrijf|loods|container)|'
                    r'opslag huren|garage\s?box|garageboxen|garage\s?park|bedrijfsunit|bedrijfsverzamel|mini[\s-]?opslag|'
                    r'opslagplaats|stallingsruimte|caravanstalling|bedrijfsruimte huren|opslag van (spullen|inboedel|meubels)|'
                    r'spullen opslaan|opslag opent|opslaglocaties?|opslagbedrijf|opslagruimtes|opslagunits|bergingen', re.I)
CONTEXT = re.compile(r'vestiging|locatie|opent|opening|bouw|vergunning|uitbreid|overname|neemt over|koopt|verkoopt|'
                     r'huur|tarief|prijs|klant|opslag|brand|inbraak|inbrekers|politie|gemeente|bestemmingsplan|ontwikkel', re.I)
OFF_TOPIC = re.compile(r'energieopslag|batterij|accu|thuisbatterij|stroomopslag|warmteopslag|waterstof|co2|koolstof|'
                       r'gasopslag|data[\s-]?opslag|cloudopslag|cloud|opslagcapaciteit (van )?(stroom|energie)|zonnepanel|'
                       r'netcongestie|kernafval|graanopslag|mestopslag|\d+\s?(gb|tb)\b|smartphone|telefoon|iphone|laptop|ssd|geheugen|'
                       r'game|playstation|xbox|console|icloud', re.I)
ROUNDUP = re.compile(r'niet missen|nieuwsoverzicht|nieuws overzicht|weekoverzicht|dagoverzicht|kort nieuws|liveblog|'
                     r'uitgaanstips|agenda|het nieuws van|in het kort|de ochtend|de avond|podcast', re.I)
FOREIGN = re.compile(r'nasdaq|nyse|\breit\b|hong kong|\bvs\b|amerika|verenigd koninkrijk|brits|engeland|manchester|londen|'
                     r'duitsland|frankrijk|spanje|belgi|zaventem|antwerpen|gent\b|brussel', re.I)
FINANCE_SITES = ('investing.com', 'beursgenoten.nl', 'iex.nl', 'marketscreener.com', 'abm.nl', 'businessinsider.nl')
FINANCE_ONLY = re.compile(r'koersdoel|beurskoers|aandeel|kerncijfers|zakelijk profiel|stock price|dividend|analist', re.I)
NEWS_PATH = re.compile(r'/(nieuws|news|pers|press|persberichten|media|actueel|newsroom)(/|$|-)', re.I)
BLOG_PATH = re.compile(r'/(blog|blogs|tips|kennisbank|inspiratie|magazine|artikelen?|gids|veelgestelde)(/|$|-)', re.I)
DUTCH = {'de', 'het', 'een', 'van', 'en', 'voor', 'op', 'in', 'met', 'is', 'te', 'bij', 'naar', 'niet', 'wordt', 'zijn',
         'nieuwe', 'aan', 'om', 'ook', 'uit', 'over', 'dat', 'die', 'wil', 'gaat', 'komt', 'hun', 'er'}
ENGLISH = {'the', 'and', 'of', 'for', 'with', 'to', 'is', 'its', 'from', 'on', 'reports', 'announces', 'results'}
TOPICS = [('Overname & cijfers', r'overname|neemt .{0,30}over|koopt|verkoopt|omzet|winst|resultaten|fonds|belegger'),
          ('Veiligheid', r'inbraak|inbrekers|brand|politie|diefstal|opengebroken|drugs|hennep'),
          ('Nieuwe locatie', r'opent|opening|nieuwe (vestiging|locatie|park)|bouw|vergunning|uitbreid|bestemmingsplan|ontwikkel'),
          ('Prijzen', r'tarief|prijs|duurder|goedkoper|kosten|huurprijs'),
          ('Markt & onderzoek', r'onderzoek|markt|vraag naar|tekort|trend|rapport|cijfers'),
          ('Gemeente', r'gemeente|raad|wethouder|college|handhaving')]


def norm_title(t):
    t = re.sub(r'\s+[-|]\s+[^-|]{2,40}$', '', t or '')  # " - AD.nl" publisher suffix
    return re.sub(r'[^a-z0-9]+', ' ', t.lower()).strip()


def item_id(title):
    return hashlib.sha1(norm_title(title).encode()).hexdigest()[:16]


def strip_html(s):
    return html.unescape(re.sub(r'<[^>]+>', '', s or '')).strip()


def domain_of(url):
    d = urlparse(url or '').netloc.lower()
    return d[4:] if d.startswith('www.') else d


def is_dutch(text):
    w = re.findall(r'[a-zà-ÿ]+', (text or '').lower())
    nl, en = sum(x in DUTCH for x in w), sum(x in ENGLISH for x in w)
    return nl >= 2 and nl > en


# -------------------------------------------------------------------- sources
def alert_feeds():
    raw = config.env('NEWS_ALERT_FEEDS') or ''
    return [u.strip() for u in re.split(r'[\s,]+', raw) if u.strip().startswith('http')]


def read_alert(url, s=None):
    s = s or requests.Session()
    r = s.get(url, timeout=30)
    r.raise_for_status()
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(r.content)
    feed_title = strip_html((root.findtext('a:title', default='', namespaces=ns) or '').replace('Google Alert -', ''))
    out = []
    for e in root.findall('a:entry', ns):
        link = (e.find('a:link', ns).get('href') if e.find('a:link', ns) is not None else '') or ''
        real = (parse_qs(urlparse(link).query).get('url') or [link])[0]
        out.append(dict(source='alerts', query=feed_title.strip(), title=strip_html(e.findtext('a:title', '', ns)),
                        url=real, snippet=strip_html(e.findtext('a:content', '', ns))[:400],
                        published=(e.findtext('a:published', '', ns) or '')[:10] or None, publisher=None))
    return out


GOOGLE_COOKIES = {'SOCS': 'CAESHAgBEhJnd3NfMjAyNTAxMDEtMF9SQzEaAmVuIAEaBgiA_LyaBg', 'CONSENT': 'YES+'}


def resolve(url, s, hops=6):
    """DataForSEO returns google.com/goto redirects. Follow them hop by hop (google.com -> www.google.com ->
    the article), with the consent cookie set so the EU consent page does not get in the way."""
    if 'google.com/goto' not in (url or ''):
        return url
    cur = url
    try:
        for _ in range(hops):
            r = s.get(cur, allow_redirects=False, timeout=10, cookies=GOOGLE_COOKIES,
                      headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36',
                               'Accept-Language': 'nl-NL,nl;q=0.9'})
            loc = r.headers.get('Location') or ''
            if not loc:
                m = re.search(r'(?:URL=\'?|href=")(https?://[^\'" >]+)', r.text or '')
                loc = html.unescape(m.group(1)) if m else ''
            if not loc:
                break
            if loc.startswith('/'):
                loc = 'https://www.google.com' + loc
            host = domain_of(loc)
            if 'consent.google' in host:  # follow the original target behind the consent page
                loc = (parse_qs(urlparse(loc).query).get('continue') or [''])[0]
                if not loc:
                    break
                host = domain_of(loc)
            if 'google.' not in host:
                return loc
            cur = loc
    except requests.RequestException:
        pass
    return url


def dfs_queries(cfg):
    n = cfg['news']
    q = []
    for group, items in (n.get('queries') or {}).items():
        q += [(group, x) for x in items]
    tpl = n.get('local_query')
    if tpl:
        cities = list(cfg['cities']) + list(n.get('extra_cities') or [])
        q += [('lokaal', tpl.format(city=c)) for c in dict.fromkeys(cities)]
    return q


def read_dfs(cli, queries, period, depth, workers=8):
    from .scrape import API as SAPI

    def one(gq):
        group, kw = gq
        payload = dict(keyword=kw, location_code=2528, language_code='nl', depth=depth, search_param=f'tbs=qdr:{period}')
        for i in range(3):
            try:
                t = (cli.session().post(SAPI + '/v3/serp/google/news/live/advanced', json=[payload], timeout=120).json().get('tasks') or [{}])[0]
                if t.get('status_code') == 20000:
                    items = ((t.get('result') or [{}])[0] or {}).get('items') or []
                    return [dict(source='dataforseo', query=kw, group_hint=group, title=i.get('title') or '', url=i.get('url'),
                                 snippet=(i.get('snippet') or '')[:400], published=(i.get('timestamp') or '')[:10] or None,
                                 publisher=i.get('source')) for i in items if i.get('type') == 'news_search'], t.get('cost') or 0, None
                err = f"{t.get('status_code')} {t.get('status_message')}"
            except Exception as e:
                err = repr(e)
        return [], 0, f'{kw}: {err}'

    items, cost, errors = [], 0.0, []
    with ThreadPoolExecutor(workers) as ex:
        for its, c, e in ex.map(one, queries):
            items += its
            cost += c
            if e:
                errors.append(e)
    return items, cost, errors


# -------------------------------------------------------------------- classification
def brand_res(cfg):
    out = {}
    for name, b in cfg['brands'].items():
        pat = b.get('pattern') or re.escape(name)
        out[name] = re.compile(pat, re.I)
    out[cfg['client']['own_brand']] = re.compile(cfg['client']['own_pattern'], re.I)
    for name, pat in (cfg['news'].get('extra_brands') or {}).items():
        out[name] = re.compile(pat, re.I)
    return out


def classify(it, cfg, today, B=None):
    B = B or brand_res(cfg)
    own = cfg['client']['own_brand']
    text = f"{it['title']} {it.get('snippet') or ''}"
    d = domain_of(it.get('url'))
    brands = sorted(n for n, rx in B.items() if rx.search(text) or (cfg['brands'].get(n, {}).get('domain') or '~') in d)
    cities = sorted({c for c in list(cfg['cities']) + list(cfg['news'].get('extra_cities') or [])
                     if re.search(rf'\b{re.escape(c)}\b', text)})
    topic = next((t for t, rx in TOPICS if re.search(rx, text, re.I)), 'Overig')
    group = 'eigen' if own in brands else 'concurrent' if brands else 'lokaal' if cities else 'markt'
    reason = None
    pub = it.get('published')
    brand_domains = {b.get('domain') for b in cfg['brands'].values() if b.get('domain')}
    if pub and (today - dt.date.fromisoformat(pub)).days > int(cfg['news'].get('max_age_days', 120)):
        reason = 'te oud'
    elif not is_dutch(text):
        reason = 'niet Nederlands'
    elif OFF_TOPIC.search(text) and not re.search(r'self[\s-]?storage|garage\s?box', text, re.I):
        reason = 'ander soort opslag'
    elif BLOG_PATH.search(urlparse(it.get('url') or '').path):
        reason = 'blog of kennisbank'
    elif any(bd and d.endswith(bd) for bd in brand_domains) and not NEWS_PATH.search(urlparse(it.get('url') or '').path):
        reason = 'pagina op eigen site, geen nieuws'
    elif (FINANCE_ONLY.search(it['title']) and not MARKET.search(it['title'])) or any(d.endswith(f) for f in FINANCE_SITES):
        reason = 'beursinformatie'
    elif ROUNDUP.search(it['title']):
        reason = 'nieuwsoverzicht'
    elif not is_dutch(it['title'] + ' ' + it['title']) and sum(w in ENGLISH for w in re.findall(r'[a-z]+', it['title'].lower())) >= 2:
        reason = 'niet Nederlands'
    elif FOREIGN.search(text) and not cities and own not in brands:
        reason = 'buitenlandse markt'
    elif any(B[b].search(it['title']) for b in brands if b in B) and is_dutch(text):
        reason = None  # a tracked brand in a Dutch headline is always relevant
    elif not brands and not MARKET.search(it['title']):
        reason = 'niet over de opslagmarkt'  # the market term must be in the headline, not only in a teaser
    elif not MARKET.search(text) and not (brands and CONTEXT.search(text)):
        reason = 'niet over de opslagmarkt'
    return dict(it, id=item_id(it['title']), domain=d, brands=brands, cities=cities, topic=topic, grp=group,
                keep=reason is None, reason=reason)


def fetch(cfg, run_date, limit=0):
    from .scrape import Client
    today = dt.date.fromisoformat(run_date)
    n = cfg['news']
    items, errors, cost = [], [], 0.0
    s = requests.Session()
    for url in alert_feeds():
        try:
            items += read_alert(url, s)
        except Exception as e:
            errors.append(f'alert feed: {e!r}'[:200])
    if os.environ.get('DATAFORSEO_LOGIN'):
        cli = Client(config.env('DATAFORSEO_LOGIN', required=True), config.env('DATAFORSEO_PASSWORD', required=True))
        qs = dfs_queries(cfg)
        qs = qs[:limit] if limit else qs
        d_items, cost, e = read_dfs(cli, qs, n.get('period', 'm'), int(n.get('depth', 20)))
        errors += e
        import time
        def slow(i):
            u = resolve(i['url'], s)
            if 'google.com/goto' in u:
                time.sleep(2)
                u = resolve(i['url'], s)
            return u
        with ThreadPoolExecutor(3) as ex:
            urls = list(ex.map(slow, d_items))
        print(f"Resolved {sum('google.com/goto' not in u for u in urls)} of {len(urls)} Google News links", flush=True)
        for i, u in [(i, u) for i, u in zip(d_items, urls) if 'google.com/goto' in u][:2]:  # diagnostics for unresolved links
            try:
                r = s.get(i['url'], allow_redirects=False, timeout=10, cookies=GOOGLE_COOKIES, headers={'User-Agent': 'Mozilla/5.0'})
                print(f"  unresolved: {r.status_code} loc={r.headers.get('Location', '')[:120]} body={re.sub(r'\\s+', ' ', r.text[:300])}", flush=True)
            except requests.RequestException as e:
                print(f'  unresolved: {e!r}', flush=True)
        for i, u in zip(d_items, urls):
            i['url'] = u
        items += d_items
    B = brand_res(cfg)
    seen, out = {}, []
    for it in items:
        c = classify(it, cfg, today, B)
        prev = seen.get(c['id'])
        if prev is not None:  # same story from two sources or queries: keep one, prefer a real article URL
            if 'google.com/goto' in (out[prev]['url'] or '') and 'google.com/goto' not in (c['url'] or ''):
                out[prev].update(url=c['url'], domain=c['domain'])
            continue
        seen[c['id']] = len(out)
        out.append(c)
    for c in out:
        c.update(run_date=run_date, fetched_at=dt.datetime.now(dt.timezone.utc).isoformat())
        c.pop('group_hint', None)
    return out, cost, errors


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument('--limit', type=int, default=0, help='only the first N DataForSEO queries (smoke test)')
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    cfg = config.load()
    rows, cost, errors = fetch(cfg, a.date, a.limit)
    out = Path(a.out or config.ROOT / 'data' / 'raw' / f'news_{a.date}.jsonl.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, 'wt', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    kept = [r for r in rows if r['keep']]
    from collections import Counter
    print(f"Wrote {out.name} | {len(rows)} articles, {len(kept)} kept | alerts feeds {len(alert_feeds())} | cost ${cost:.2f}")
    for k, v in Counter(r['reason'] for r in rows if not r['keep']).most_common():
        print(f'  dropped {v:>4}  {k}')
    for k, v in Counter(r['grp'] for r in kept).most_common():
        print(f'  kept    {v:>4}  {k}')
    for e in errors[:10]:
        print(f'  error: {e}')
    if rows == [] and errors:
        raise SystemExit('No news fetched')


def transform_files(paths):
    rows = []
    for p in paths:
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            rows += [json.loads(l) for l in fh if l.strip()]
    return {TABLE: rows}


if __name__ == '__main__':
    main()
