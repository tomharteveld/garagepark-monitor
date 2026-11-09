"""Business Profile performance from Google's own APIs (Bedrijfsprofielmanager data), for the profiles the
connected Google account manages: views by platform and device, calls, routes, website clicks, bookings,
conversations, and the search terms people used.

Auth: OAuth refresh token of a manager account of the profiles (secret GBP_OAUTH_JSON, the
authorized_user JSON that `gcloud auth application-default login --client-id-file=...` writes).
The Google Cloud project of that OAuth client needs the Business Profile APIs enabled and approved.

Usage:
  python -m gpmon.gbp_perf --check      # list accounts and GaragePark locations, fetch nothing else
  python -m gpmon.gbp_perf              # fetch, write data/raw/gbpperf_<date>.jsonl.gz
Tables: gbp_locations, gbp_perf_daily, gbp_keywords (one snapshot per run_date).
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from . import config

SCOPE = 'https://www.googleapis.com/auth/business.manage'
ACC = 'https://mybusinessaccountmanagement.googleapis.com/v1'
INFO = 'https://mybusinessbusinessinformation.googleapis.com/v1'
PERF = 'https://businessprofileperformance.googleapis.com/v1'
METRICS = ['BUSINESS_IMPRESSIONS_DESKTOP_MAPS', 'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH', 'BUSINESS_IMPRESSIONS_MOBILE_MAPS',
           'BUSINESS_IMPRESSIONS_MOBILE_SEARCH', 'CALL_CLICKS', 'BUSINESS_DIRECTION_REQUESTS', 'WEBSITE_CLICKS',
           'BUSINESS_CONVERSATIONS', 'BUSINESS_BOOKINGS']
TABLES = ('gbp_locations', 'gbp_perf_daily', 'gbp_keywords')


class Api:
    def __init__(self, oauth):
        self.o = oauth
        self.token, self.exp = None, 0
        self.s = requests.Session()

    def auth(self):
        if time.time() > self.exp - 60:
            r = requests.post('https://oauth2.googleapis.com/token', timeout=30, data=dict(
                client_id=self.o['client_id'], client_secret=self.o['client_secret'],
                refresh_token=self.o['refresh_token'], grant_type='refresh_token')).json()
            if 'access_token' not in r:
                raise SystemExit(f"OAuth refresh failed: {r.get('error')} {r.get('error_description')}")
            if r.get('scope') and SCOPE not in r['scope']:
                raise SystemExit(f'The token lacks the scope {SCOPE}. Repeat the login with --scopes including it.')
            self.token, self.exp = r['access_token'], time.time() + int(r.get('expires_in', 3000))
        return {'Authorization': f'Bearer {self.token}'}

    def get(self, url, params=None, tries=5):
        r = None
        for i in range(tries):
            r = self.s.get(url, params=params, headers=self.auth(), timeout=60)
            if r.status_code == 200:
                return r.json()
            msg = r.text[:600]
            if r.status_code == 429 and ('limit: 0' in msg or "limit 'Requests per minute' of service" in msg and 'limit: 0' in msg):
                raise SystemExit('Quota of the Business Profile APIs is 0 for this Google Cloud project: API access is not approved '
                                 'for it yet (or the OAuth client belongs to another project). ' + msg)
            if r.status_code in (429, 500, 502, 503):
                time.sleep(5 * (i + 1))
                continue
            if r.status_code == 403 and 'SERVICE_DISABLED' in msg:
                raise SystemExit(f'Enable this API in the OAuth client\'s Google Cloud project: {msg}')
            if r.status_code == 429 or 'quota' in msg.lower():
                raise SystemExit(f'Quota for the Business Profile APIs is 0 or used up (access not approved yet?): {msg}')
            raise RuntimeError(f'{r.status_code} {url}: {msg}')
        raise RuntimeError(f'Giving up on {url} after {tries} tries, last answer {r.status_code}: {r.text[:600]}')

    def pages(self, url, key, params=None):
        params = dict(params or {})
        while True:
            d = self.get(url, params)
            yield from d.get(key) or []
            if not d.get('nextPageToken'):
                return
            params['pageToken'] = d['nextPageToken']


def load_oauth():
    raw = (config.env('GBP_OAUTH_JSON') or '').strip()
    m = re.search(r'\{.*\}', raw, re.S)  # tolerate extra text pasted around the JSON (headers, ==== lines)
    if m:
        try:  # a terminal wraps long lines when copying: drop line breaks (tokens never contain whitespace)
            o = json.loads(re.sub(r'[ \t]*[\r\n]+[ \t]*', '', m.group(0)))
        except json.JSONDecodeError as e:
            raise SystemExit(f'GBP_OAUTH_JSON is not valid JSON ({e.msg} at {e.pos}); paste only the line between the ==== lines')
        missing = [k for k in ('client_id', 'client_secret', 'refresh_token') if not o.get(k)]
        if missing:
            raise SystemExit(f'GBP_OAUTH_JSON lacks {", ".join(missing)}')
        return o
    if raw and not os.path.exists(raw):
        raise SystemExit(f'GBP_OAUTH_JSON is set ({len(raw)} characters) but contains no JSON object; paste the line between the ==== lines')
    path = raw or os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
    if not os.path.exists(path):
        raise SystemExit('No GBP_OAUTH_JSON secret and no gcloud application default credentials found')
    return json.load(open(path))


def cid_from(meta):
    m = re.search(r'cid=(\d+)', (meta or {}).get('mapsUri') or '')
    return m.group(1) if m else None


def own_locations(api, g):
    accounts = list(api.pages(f'{ACC}/accounts', 'accounts', {'pageSize': 20}))
    out = []
    for a in accounts:
        for loc in api.pages(f"{INFO}/{a['name']}/locations", 'locations', {
                'pageSize': 100, 'readMask': 'name,title,storefrontAddress,websiteUri,metadata,phoneNumbers,categories,latlng,openInfo'}):
            site = (loc.get('websiteUri') or '').lower()
            phone = ((loc.get('phoneNumbers') or {}).get('primaryPhone') or '').replace(' ', '')
            title = loc.get('title') or ''
            # the account manages several clients: match on GaragePark's own domain or phone, never on the name alone
            if g['own_domain'] in site or (phone and phone.endswith(g['own_phone'][-9:])):
                addr = loc.get('storefrontAddress') or {}
                out.append(dict(location_id=loc['name'].split('/')[-1], account=a['name'], account_name=a.get('accountName'),
                                title=title, cid=cid_from(loc.get('metadata')), place_id=(loc.get('metadata') or {}).get('placeId'),
                                website=loc.get('websiteUri'), city=addr.get('locality'),
                                latitude=(loc.get('latlng') or {}).get('latitude'), longitude=(loc.get('latlng') or {}).get('longitude'),
                                status=(loc.get('openInfo') or {}).get('status'), has_voice_of_merchant=(loc.get('metadata') or {}).get('hasVoiceOfMerchant'),
                                address=', '.join((addr.get('addressLines') or []) + [addr.get('postalCode') or '', addr.get('locality') or '']).strip(', ')))
    return accounts, out


def daily(api, loc_id, start, end):
    p = [('dailyMetrics', m) for m in METRICS] + [
        ('dailyRange.start_date.year', start.year), ('dailyRange.start_date.month', start.month), ('dailyRange.start_date.day', start.day),
        ('dailyRange.end_date.year', end.year), ('dailyRange.end_date.month', end.month), ('dailyRange.end_date.day', end.day)]
    d = api.get(f'{PERF}/locations/{loc_id}:fetchMultiDailyMetricsTimeSeries', p)
    rows = []
    for multi in d.get('multiDailyMetricTimeSeries') or []:
        for s in multi.get('dailyMetricTimeSeries') or []:
            for dv in (s.get('timeSeries') or {}).get('datedValues') or []:
                x = dv.get('date') or {}
                if x.get('year'):
                    rows.append(dict(date=f"{x['year']:04d}-{x['month']:02d}-{x['day']:02d}", metric=s.get('dailyMetric'), value=int(dv.get('value') or 0)))
    return rows


def keywords(api, loc_id, month):
    p = {'monthlyRange.start_month.year': month.year, 'monthlyRange.start_month.month': month.month,
         'monthlyRange.end_month.year': month.year, 'monthlyRange.end_month.month': month.month, 'pageSize': 100}
    out = []
    for k in api.pages(f'{PERF}/locations/{loc_id}/searchkeywords/impressions/monthly', 'searchKeywordsCounts', p):
        iv = k.get('insightsValue') or {}
        out.append(dict(month=month.isoformat(), keyword=k.get('searchKeyword'),
                        value=int(iv['value']) if 'value' in iv else None, threshold=int(iv['threshold']) if 'threshold' in iv else None))
    return out


def months_back(today, n):
    first = today.replace(day=1)
    out = []
    for _ in range(n):
        first = (first - dt.timedelta(days=1)).replace(day=1)
        out.append(first)
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    ap.add_argument('--check', action='store_true', help='only list accounts and own locations')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    cfg = config.load()
    g = cfg['gbp']
    api = Api(load_oauth())
    accounts, locs = own_locations(api, g)
    print(f'{len(accounts)} accounts, {len(locs)} GaragePark locations ({sum(1 for l in locs if l["cid"])} with a Maps cid)', flush=True)
    if a.check:
        for l in locs[:80]:
            print(f"  {l['location_id']:>22}  {l['cid'] or '-':>22}  {l['title']}")
        return
    if a.limit:
        locs = locs[:a.limit]
    today = dt.date.fromisoformat(a.date)
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(g.get('perf_days', 540)))
    months = months_back(today, int(g.get('keyword_months', 6)))
    recs, errors = [dict(kind='location', run_date=a.date, **l) for l in locs], 0
    for i, l in enumerate(locs, 1):
        try:
            for r in daily(api, l['location_id'], start, end):
                recs.append(dict(kind='daily', run_date=a.date, location_id=l['location_id'], **r))
            for m in months:
                for k in keywords(api, l['location_id'], m):
                    recs.append(dict(kind='keyword', run_date=a.date, location_id=l['location_id'], **k))
        except (RuntimeError, requests.RequestException) as e:
            errors += 1
            print(f'  x {l["title"]}: {e}', flush=True)
        if i % 10 == 0:
            print(f'  {i}/{len(locs)} locations', flush=True)
    out = Path(a.out or config.ROOT / 'data' / 'raw' / f'gbpperf_{a.date}.jsonl.gz')
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, 'wt', encoding='utf-8') as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(f'Wrote {out.name} | {len(locs)} locations | {sum(r["kind"] == "daily" for r in recs)} daily values | '
          f'{sum(r["kind"] == "keyword" for r in recs)} keyword rows | {errors} failed')
    if errors > 0.2 * max(len(locs), 1):
        sys.exit('Too many locations failed; not loading this run')


def transform(records):
    T = {t: [] for t in TABLES}
    for r in records:
        k = r.pop('kind', None)
        if k == 'location':
            T['gbp_locations'].append(r)
        elif k == 'daily' and r.get('value'):
            T['gbp_perf_daily'].append(r)  # zero days are implied, keeps the table small
        elif k == 'keyword':
            T['gbp_keywords'].append(r)
    return T


def transform_files(paths):
    recs = []
    for p in paths:
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            recs += [json.loads(l) for l in fh if l.strip()]
    return transform(recs)


if __name__ == '__main__':
    main()


def aggregate(T):
    """Same shapes as bq.fetch_gbp_perf, computed locally (dashboard --source local)."""
    from collections import defaultdict
    daily = T.get('gbp_perf_daily') or []
    if not daily:
        return {'gbp_locations': T.get('gbp_locations') or []}
    end = max(dt.date.fromisoformat(r['date']) for r in daily)
    mon, win = defaultdict(int), defaultdict(lambda: [0, 0, 0])
    for r in daily:
        d = dt.date.fromisoformat(r['date'])
        mon[(r['location_id'], r['date'][:7], r['metric'])] += r['value']
        age = (end - d).days
        w = win[(r['location_id'], r['metric'])]
        if age < 28:
            w[0] += r['value']
        elif age < 56:
            w[1] += r['value']
        if 365 <= age < 393:
            w[2] += r['value']
    return {
        'gbp_locations': T.get('gbp_locations') or [],
        'gbp_perf_monthly': [dict(location_id=l, month=m, metric=k, value=v) for (l, m, k), v in mon.items()],
        'gbp_perf_window': [dict(location_id=l, metric=k, cur=c, prev=p, yoy=y, end_date=end.isoformat()) for (l, k), (c, p, y) in win.items()],
        'gbp_keywords': T.get('gbp_keywords') or [],
    }
