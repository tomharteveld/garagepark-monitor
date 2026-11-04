"""Turn business profile tables into the compact structure the dashboard's 'Bedrijfsprofielen' tab reads.

Everything that is a judgement (profile score, review themes, actions with impact and effort) is computed
here, in one place, so it is testable and the same in every build.
"""
import datetime as dt
import re
import statistics as st
from collections import Counter, defaultdict

from . import config

# Review themes for self storage and garage boxes (Dutch reviews). One review can have several themes.
THEMES = [
    ('toegang', 'Toegang en poort', r'\b(poort|hek|toegang|code|pasje?|app|slot|sleutel|deur|openen|dicht)\w*'),
    ('contact', 'Contact en reactie', r'\b(contact|bereikba\w*|telefo\w*|bellen|gebeld|mail\w*|reactie|reageer\w*|antwoord\w*|communica\w*|klantenservice)'),
    ('prijs', 'Prijs en kosten', r'(\bprijs\w*|\bduur\b|\bkosten\b|\bhuurprijs|verhog\w*|€|\beuro|\bgoedkoop|\bbetaalbaar)'),
    ('contract', 'Contract, borg en betaling', r'\b(borg|contract\w*|opzeg\w*|factu\w*|incasso|betaling\w*|terugbeta\w*|administra\w*|beheer\w*|aanmanin\w*)'),
    ('staat', 'Staat en onderhoud', r'\b(lek\w*|vocht\w*|schimmel|kapot|onderhoud|vies|smerig|schoon\w*|stof\w*|water|netjes|verzorgd)'),
    ('veiligheid', 'Veiligheid', r'\b(inbra\w*|inbrek\w*|camera\w*|veilig\w*|beveilig\w*|diefstal|gestolen)'),
    ('ligging', 'Ligging en bereikbaarheid', r'\b(locatie|ligging|route|parkeer\w*|parkeren|bereikbaar|vinden|snelweg|centraal)'),
    ('ruimte', 'Ruimte en formaat', r'\b(klein\w*|ruim\w*|groot|grote|formaat|m2|m²|hoogte|afmeting\w*|ruimte)'),
    ('service', 'Service en personeel', r'\b(vriendelijk\w*|service|behulpza\w*|medewerk\w*|beheerder\w*|personeel|geholpen|snel geregeld|klantvriendelijk)'),
]
THEME_RES = [(k, l, re.compile(p, re.I)) for k, l, p in THEMES]

TOP_LABEL = {
    'claim': 'Profielen claimen of verifiëren', 'neg_reply': 'Reageren op negatieve reviews', 'reply': 'Elke review beantwoorden',
    'review_volume': 'Reviewvolume omhoog met een vaste reviewroute', 'rating': 'Score verbeteren', 'stale_reviews': 'Weer nieuwe reviews krijgen',
    'description': 'Beschrijving toevoegen', 'description_template': 'Beschrijvingen per park lokaal maken', 'photos': "Meer foto's per park",
    'hours': 'Openingstijden invullen', 'categories': 'Extra categorie toevoegen', 'posts': 'Elke maand een post per profiel',
    'booking': 'Afspraak- of huurlink toevoegen', 'name': 'Bedrijfsnamen zonder zoekwoorden', 'url': 'Website-link naar de vestigingspagina',
    'theme': 'Terugkerende klachten oplossen',
}
IMPACT_ORDER = {'Hoog': 0, 'Middel': 1, 'Laag': 2}
EFFORT_ORDER = {'Laag': 0, 'Middel': 1, 'Hoog': 2}

# Fields of the profile score (same for own profiles and competitors, so they compare 1:1)
SCORE_FIELDS = [
    ('claimed', 'Geclaimd'), ('description', 'Beschrijving'), ('extra_category', 'Extra categorie'), ('photos', '10+ foto\'s'),
    ('hours', 'Openingstijden'), ('website', 'Website'), ('phone', 'Telefoon'), ('attributes', 'Kenmerken'),
    ('logo', 'Logo'), ('booking', 'Afspraak- of boekingslink'), ('reviews', '10+ reviews'), ('posts', 'Post in 90 dagen'),
]


def _d(x):
    if x is None:
        return None
    if isinstance(x, (dt.date, dt.datetime)):
        return x if isinstance(x, dt.datetime) else dt.datetime.combine(x, dt.time(), dt.timezone.utc)
    try:
        return dt.datetime.fromisoformat(str(x).replace('Z', '+00:00'))
    except ValueError:
        return None


def themes_of(text):
    return [k for k, _, rx in THEME_RES if rx.search(text or '')]


def _template_key(desc, city):
    """Description with the place name removed: equal keys across locations mean a copied template."""
    t = (desc or '').lower()
    for w in re.split(r'[\s\-]+', (city or '').lower()):
        if len(w) > 2:
            t = t.replace(w, '#')
    return re.sub(r'\W+', ' ', t).strip()[:400]


def build(T, run_date=None):
    cfg = config.load()
    brands = cfg.get('brands') or {}
    dom2brand = {(b.get('domain') or '').replace('www.', ''): n for n, b in brands.items() if b.get('domain')}
    profiles_all = T.get('gbp_profiles') or []
    if not profiles_all:
        return None
    runs = sorted({str(p['run_date'])[:10] for p in profiles_all})
    last = runs[-1]
    prev = runs[-2] if len(runs) > 1 else None
    now = _d(last + 'T23:59:59+00:00')
    d90, d365 = now - dt.timedelta(days=90), now - dt.timedelta(days=365)

    prof = {p['cid']: p for p in profiles_all if str(p['run_date'])[:10] == last}
    hist = defaultdict(dict)  # cid -> run -> {rating, votes, photos}
    for p in profiles_all:
        hist[p['cid']][str(p['run_date'])[:10]] = dict(r=p.get('rating'), v=p.get('votes'), ph=p.get('total_photos'))
    links = defaultdict(list)
    for l in sorted(T.get('gbp_competitors') or [], key=lambda l: l['rank']):
        if l['comp_cid'] in prof:
            links[l['own_cid']].append(l['comp_cid'])

    reviews = defaultdict(list)
    for r in T.get('gbp_reviews') or []:
        reviews[r['cid']].append(r)
    posts = defaultdict(list)
    for p in T.get('gbp_posts') or []:
        posts[p['cid']].append(p)

    # per profile statistics (own and competitor alike)
    stats = {}
    for cid, p in prof.items():
        rv = reviews.get(cid, [])
        dates = [_d(r.get('posted_at')) for r in rv]
        answered = [bool(r.get('owner_answer') or r.get('answered')) for r in rv]
        n90 = sum(1 for d in dates if d and d >= d90)
        n365 = sum(1 for d in dates if d and d >= d365)
        r365 = [r['rating'] for r, d in zip(rv, dates) if d and d >= d365 and r.get('rating')]
        neg_open = sum(1 for r, a in zip(rv, answered) if (r.get('rating') or 5) <= 2 and not a)
        pdates = [_d(x.get('posted_at')) for x in posts.get(cid, [])]
        last_post = max([d for d in pdates if d], default=None)
        score = {
            'claimed': bool(p.get('is_claimed')), 'description': len(p.get('description') or '') >= 150,
            'extra_category': bool(p.get('additional_categories')), 'photos': (p.get('total_photos') or 0) >= 10,
            'hours': bool(p.get('has_hours')), 'website': bool(p.get('url')), 'phone': bool(p.get('phone')),
            'attributes': bool(p.get('attributes')), 'logo': bool(p.get('has_logo')),
            'booking': bool(p.get('book_online_url') or p.get('contact_url')), 'reviews': (p.get('votes') or 0) >= 10,
            'posts': bool(last_post and last_post >= d90),
        }
        brand = None
        if not p.get('is_own'):
            dom = (p.get('domain') or '').replace('www.', '')
            brand = dom2brand.get(dom) or (dom.split('.')[0].capitalize() if dom else None) or (p.get('title') or '').split(' ')[0]
        stats[cid] = dict(
            n_fetched=len(rv), n90=n90, n365=n365, avg365=round(st.mean(r365), 2) if r365 else None,
            reply=round(sum(answered) / len(answered), 3) if answered else None, unanswered=answered.count(False),
            neg_open=neg_open, posts90=sum(1 for d in pdates if d and d >= d90),
            last_post=last_post.date().isoformat() if last_post else None,
            last_review=max([d for d in dates if d], default=None),
            score=round(sum(score.values()) / len(score), 3), checks=score, brand=brand)
        stats[cid]['last_review'] = stats[cid]['last_review'].date().isoformat() if stats[cid]['last_review'] else None

    own = [c for c, p in prof.items() if p.get('is_own')]
    comps = [c for c, p in prof.items() if not p.get('is_own')]

    # duplicate description templates among own profiles
    tkeys = Counter(_template_key(prof[c].get('description'), prof[c].get('city')) for c in own if prof[c].get('description'))

    # themes in own reviews (positive >= 4, negative <= 2) and competitor reviews
    theme_rows = {k: dict(key=k, label=l, own_neg=0, own_pos=0, comp_neg=0, comp_pos=0) for k, l, _ in THEMES}
    own_reviews = []
    for cid in own:
        for r in reviews.get(cid, []):
            th = themes_of(r.get('text'))
            for k in th:
                if (r.get('rating') or 0) <= 2:
                    theme_rows[k]['own_neg'] += 1
                elif (r.get('rating') or 0) >= 4:
                    theme_rows[k]['own_pos'] += 1
            d = _d(r.get('posted_at'))
            own_reviews.append(dict(cid=cid, s=r.get('rating'), d=d.date().isoformat() if d else None, ago=r.get('time_ago'),
                                    t=r.get('text') or '', a=r.get('owner_answer') or '', ans=bool(r.get('owner_answer') or r.get('answered')),
                                    th=th, u=r.get('review_url')))
    for cid in comps:
        for r in reviews.get(cid, []):
            for k in themes_of(r.get('text')):
                if (r.get('rating') or 0) <= 2:
                    theme_rows[k]['comp_neg'] += 1
                elif (r.get('rating') or 0) >= 4:
                    theme_rows[k]['comp_pos'] += 1

    # reviews per month (own, last 24 months)
    months = Counter()
    month_neg = Counter()
    for r in own_reviews:
        if r['d'] and r['d'] >= (now - dt.timedelta(days=730)).date().isoformat():
            months[r['d'][:7]] += 1
            if (r['s'] or 5) <= 2:
                month_neg[r['d'][:7]] += 1

    # actions per own profile
    actions = []

    def act(cid, key, title, why, impact, effort, group):
        actions.append(dict(id=f'{cid}:{key}', cid=cid, key=key, title=title, why=why, impact=impact, effort=effort, group=group))

    for cid in own:
        p, s = prof[cid], stats[cid]
        cs = [stats[c] for c in links.get(cid, [])]
        cp = [prof[c] for c in links.get(cid, [])]
        med = lambda xs: st.median(xs) if xs else None
        comp_votes = med([x.get('votes') or 0 for x in cp])
        comp_rating = med([x.get('rating') for x in cp if x.get('rating')])
        comp_photos = med([x.get('total_photos') or 0 for x in cp])
        comp_posts = sum(1 for x in cs if x['posts90'])
        votes, rating, photos = p.get('votes') or 0, p.get('rating'), p.get('total_photos') or 0
        top = max(cp, key=lambda x: x.get('votes') or 0, default=None)
        if not p.get('is_claimed'):
            act(cid, 'claim', 'Profiel claimen of verifiëren', 'Google toont dit profiel als niet geclaimd. Zonder beheer kan iedereen wijzigingen voorstellen.', 'Hoog', 'Laag', 'Beheer')
        if s['neg_open']:
            act(cid, 'neg_reply', f"Reageer op {s['neg_open']} negatieve review{'s' if s['neg_open'] > 1 else ''}",
                'Negatieve reviews zonder reactie wegen zwaar voor nieuwe huurders, die lezen vooral de slechte.', 'Hoog', 'Laag', 'Reviews')
        if s['unanswered'] - s['neg_open'] > 0 and (s['reply'] or 0) < 0.9:
            n = s['unanswered'] - s['neg_open']
            act(cid, 'reply', f'Beantwoord {n} overige review{"s" if n > 1 else ""}',
                f"Reactiepercentage {round(100 * (s['reply'] or 0))}%. Een vaste reactie op elke review laat zien dat het park beheerd wordt.", 'Middel', 'Laag', 'Reviews')
        if comp_votes and votes < 0.5 * comp_votes:
            act(cid, 'review_volume', 'Reviewvolume omhoog',
                f"{votes} reviews tegenover een mediaan van {round(comp_votes)} bij de concurrenten in de buurt"
                + (f", {top.get('title')} heeft er {top.get('votes')}." if top and top.get('votes') else '.')
                + ' Vraag nieuwe huurders na de eerste maand om een review (mail met directe reviewlink).', 'Hoog', 'Middel', 'Reviews')
        elif votes < 10:
            act(cid, 'review_volume', 'Eerste tien reviews verzamelen', f'{votes} reviews. Onder de tien reviews toont Google het profiel minder vaak en wegen losse slechte reviews zwaar.', 'Hoog', 'Middel', 'Reviews')
        if rating and ((comp_rating and rating < comp_rating - 0.3) or rating < 4.0):
            act(cid, 'rating', 'Score verbeteren', f"Score {str(rating).replace('.', ',')}"
                + (f" tegenover {str(round(comp_rating, 1)).replace('.', ',')} bij concurrenten." if comp_rating else '.')
                + ' Kijk naar de terugkerende klachten in de reviews van dit park.', 'Hoog', 'Middel', 'Reviews')
        if s['n365'] == 0 and votes:
            act(cid, 'stale_reviews', 'Geen nieuwe reviews in 12 maanden', 'Recente reviews tellen zwaarder voor Google en voor huurders.', 'Middel', 'Middel', 'Reviews')
        if not p.get('description'):
            act(cid, 'description', 'Beschrijving toevoegen', 'Het profiel heeft geen beschrijving. Noem formaten, toegang (24/7), beveiliging en de wijk of het bedrijventerrein.', 'Hoog', 'Laag', 'Profiel')
        elif tkeys[_template_key(p.get('description'), p.get('city'))] >= 3:
            act(cid, 'description_template', 'Beschrijving lokaal maken',
                f"Dezelfde tekst staat op {tkeys[_template_key(p.get('description'), p.get('city'))]} profielen, alleen de plaatsnaam verschilt. "
                'Voeg toe wat dit park uniek maakt: formaten, voorzieningen, ligging en bereikbaarheid.', 'Middel', 'Laag', 'Profiel')
        if photos < 10 or (comp_photos and photos < 0.5 * comp_photos):
            act(cid, 'photos', 'Meer foto\'s plaatsen', f"{photos} foto's" + (f", concurrenten hebben er gemiddeld {round(comp_photos)}." if comp_photos else '.')
                + ' Toon de poort, de boxen van binnen, de toegang en de formaten.', 'Middel', 'Laag', 'Profiel')
        if not p.get('has_hours'):
            act(cid, 'hours', 'Openingstijden invullen', 'Geen openingstijden. Vul 24/7 toegang in als dat klopt: dat is een belangrijk keuzecriterium.', 'Middel', 'Laag', 'Profiel')
        if not p.get('additional_categories'):
            cats = Counter(c for x in cp for c in ([x.get('category')] + list(x.get('additional_categories') or [])) if c and c != p.get('category'))
            sugg = ', '.join(c for c, _ in cats.most_common(2))
            act(cid, 'categories', 'Extra categorie toevoegen', 'Alleen een hoofdcategorie.' + (f' Concurrenten gebruiken ook: {sugg}.' if sugg else ''), 'Middel', 'Laag', 'Profiel')
        if not s['posts90']:
            act(cid, 'posts', 'Elke maand een post plaatsen',
                'Geen post in de laatste 90 dagen' + (f'; {comp_posts} van {len(cs)} concurrenten in de buurt wel.' if cs else '.')
                + ' Bijvoorbeeld beschikbare boxen, nieuwe formaten of een actie.', 'Laag', 'Laag', 'Profiel')
        if not (p.get('book_online_url') or p.get('contact_url')):
            act(cid, 'booking', 'Afspraak- of huurlink toevoegen', 'Geen afspraak- of boekingslink in het profiel. Een directe link naar huren of een bezichtiging verkort de route.', 'Middel', 'Laag', 'Profiel')
        if re.search(r'\s[-|]\s', p.get('title') or ''):
            act(cid, 'name', 'Bedrijfsnaam zonder zoekwoorden',
                f"De naam \"{p.get('title')}\" bevat zoekwoorden. Google staat alleen de echte bedrijfsnaam toe; zoekwoorden in de naam kunnen tot een schorsing leiden.", 'Middel', 'Laag', 'Beheer')
        if p.get('url') and not re.search(r'/locaties?/', p.get('url') or ''):
            act(cid, 'url', 'Website-link naar de vestigingspagina', f"Het profiel linkt naar {p.get('url')}. Link naar de eigen locatiepagina, zodat profiel en pagina elkaar versterken.", 'Middel', 'Laag', 'Profiel')
        neg_th = Counter(k for r in reviews.get(cid, []) if (r.get('rating') or 5) <= 2 for k in themes_of(r.get('text')))
        if neg_th:
            k, n = neg_th.most_common(1)[0]
            if n >= 2:
                lab = next(l for kk, l, _ in THEMES if kk == k)
                act(cid, f'theme_{k}', f'Terugkerende klacht: {lab.lower()}', f'{n} negatieve reviews noemen {lab.lower()}. Los de oorzaak op en verwijs in de reacties naar wat er veranderd is.', 'Hoog', 'Middel', 'Reviews')

    actions.sort(key=lambda a: (IMPACT_ORDER[a['impact']], EFFORT_ORDER[a['effort']], a['group'], a['cid']))

    # account-wide top actions: same action key on many profiles
    by_key = defaultdict(list)
    for a in actions:
        by_key[re.sub(r'^theme_.*', 'theme', a['key'])].append(a)
    top = []
    for key, items in by_key.items():
        a0 = items[0]
        top.append(dict(key=key, title=TOP_LABEL.get(key, a0['title']),
                        n=len(items), impact=a0['impact'], effort=a0['effort'], cids=[a['cid'] for a in items]))
    top.sort(key=lambda t: (IMPACT_ORDER[t['impact']], EFFORT_ORDER[t['effort']], -t['n']))

    perf = build_perf(T, prof, cfg)
    prev_cids = {p['cid'] for p in profiles_all if prev and str(p['run_date'])[:10] == prev}

    def slim(cid):
        p = prof[cid]
        h = hist.get(cid, {})
        pv = h.get(prev) if prev else None
        return dict(cid=cid, own=bool(p.get('is_own')), t=p.get('title'), c=p.get('city'), a=p.get('address'),
                    lat=p.get('latitude'), lon=p.get('longitude'), dom=p.get('domain'), url=p.get('url'),
                    cat=p.get('category'), cats=list(p.get('additional_categories') or []), desc=p.get('description') or '',
                    cl=p.get('is_claimed'), r=p.get('rating'), v=p.get('votes') or 0, st=list(p.get('stars') or []),
                    ph=p.get('total_photos') or 0, h=p.get('has_hours'), h247=p.get('open_247'), at=list(p.get('attributes') or []),
                    sv=list(p.get('services') or []), tp=list(p.get('place_topics') or []),
                    bk=p.get('book_online_url') or p.get('contact_url'), mu=p.get('check_url'),
                    comps=links.get(cid, []), pv=pv, hist=[dict(run=k, **v) for k, v in sorted(h.items())],
                    new=bool(prev and p.get('is_own') and cid not in prev_cids), src=p.get('source'),
                    pf=(perf or {}).get('by_cid', {}).get(cid),
                    **{k: v for k, v in stats[cid].items() if k != 'checks'}, chk=stats[cid]['checks'])

    tracked = {c.lower() for c in cfg.get('cities', {})} | {"'s-hertogenbosch", 's-hertogenbosch', "'s-gravenhage", 'the hague'}
    untracked = sorted({prof[c].get('city') for c in own if prof[c].get('city') and prof[c]['city'].lower() not in tracked})

    return dict(run=last, prev=prev, runs=runs, own=[slim(c) for c in sorted(own, key=lambda c: prof[c].get('city') or '')],
                comp=[slim(c) for c in comps], reviews=sorted(own_reviews, key=lambda r: r['d'] or '', reverse=True),
                themes=list(theme_rows.values()), months=[dict(m=m, n=months[m], neg=month_neg[m]) for m in sorted(months)],
                actions=actions, top=top[:8], score_fields=SCORE_FIELDS,
                perf={k: v for k, v in perf.items() if k != 'by_cid'} if perf else None, untracked_cities=untracked,
                note='Bron: Google Bedrijfsprofielen via DataForSEO (openbare profieldata en reviews) en, voor weergaven, '
                     'zoektermen en acties, de Business Profile API van de beheerde profielen.')


# ==== performance (Business Profile Performance API) ====

VIEW_METRICS = {'BUSINESS_IMPRESSIONS_MOBILE_SEARCH': ('Zoeken', 'Mobiel'), 'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH': ('Zoeken', 'Desktop'),
                'BUSINESS_IMPRESSIONS_MOBILE_MAPS': ('Maps', 'Mobiel'), 'BUSINESS_IMPRESSIONS_DESKTOP_MAPS': ('Maps', 'Desktop')}
ACTION_METRICS = {'CALL_CLICKS': 'Gesprekken', 'BUSINESS_DIRECTION_REQUESTS': 'Routes', 'WEBSITE_CLICKS': 'Websiteklikken',
                  'BUSINESS_CONVERSATIONS': 'Berichten', 'BUSINESS_BOOKINGS': 'Afspraken'}
KW_CATS = [('merk', 'Merk', r'garage ?park'), ('garagebox', 'Garagebox', r'garage|box'), ('opslag', 'Opslag', r'opslag|storage|stalling|loods|magazijn'),
           ('werkruimte', 'Werk- en bedrijfsruimte', r'werkruimte|bedrijfsruimte|werkplaats|hobby|kantoor|unit')]


def kw_cat(kw, city):
    k = (kw or '').lower()
    for key, _, rx in KW_CATS:
        if re.search(rx, k):
            return key
    if city and city.lower() in k:
        return 'plaats'
    return 'overig'


def build_perf(T, prof, cfg):
    locs = T.get('gbp_locations') or []
    if not locs or not T.get('gbp_perf_window'):
        return {'locations': [dict(id=l['location_id'], t=l.get('title'), c=l.get('city'), cid=l.get('cid'), visible=bool(l.get('cid') and l['cid'] in prof))
                              for l in locs]} if locs else None
    lid2cid = {l['location_id']: l.get('cid') for l in locs}
    by = defaultdict(lambda: dict(views=0, views_prev=0, views_yoy=0, actions={}, actions_prev={}, split={}))
    end = None
    for w in T['gbp_perf_window']:
        cid = lid2cid.get(w['location_id']) or 'loc:' + w['location_id']
        b = by[cid]
        end = str(w.get('end_date'))[:10]
        if w['metric'] in VIEW_METRICS:
            b['views'] += w['cur'] or 0
            b['views_prev'] += w['prev'] or 0
            b['views_yoy'] += w['yoy'] or 0
            pl, dev = VIEW_METRICS[w['metric']]
            b['split'][f'{pl} {dev.lower()}'] = w['cur'] or 0
        elif w['metric'] in ACTION_METRICS:
            b['actions'][ACTION_METRICS[w['metric']]] = w['cur'] or 0
            b['actions_prev'][ACTION_METRICS[w['metric']]] = w['prev'] or 0
    for b in by.values():
        b['act'] = sum(b['actions'].values())
        b['act_prev'] = sum(b['actions_prev'].values())
        b['conv'] = round(b['act'] / b['views'], 4) if b['views'] else None

    months = defaultdict(lambda: defaultdict(int))
    mon_by = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for m in T.get('gbp_perf_monthly') or []:
        lab = (' '.join(VIEW_METRICS[m['metric']]) if m['metric'] in VIEW_METRICS else ACTION_METRICS.get(m['metric']))
        if not lab:
            continue
        months[m['month']][lab] += m['value'] or 0
        cid = lid2cid.get(m['location_id']) or 'loc:' + m['location_id']
        mon_by[cid][m['month']]['views' if m['metric'] in VIEW_METRICS else 'act'] += m['value'] or 0
    for cid, b in by.items():
        b['months'] = [dict(m=k, **v) for k, v in sorted(mon_by[cid].items())]

    city_of = {l['location_id']: l.get('city') for l in locs}
    kw_tot, kw_loc, kw_month = defaultdict(int), defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    kw_thr = defaultdict(int)
    for k in T.get('gbp_keywords') or []:
        v = k.get('value')
        cid = lid2cid.get(k['location_id']) or 'loc:' + k['location_id']
        if v is None:
            kw_thr[k['keyword']] += 1
            continue
        kw_tot[k['keyword']] += v
        kw_loc[cid][k['keyword']] += v
        cat = kw_cat(k['keyword'], city_of.get(k['location_id']))
        kw_month[str(k['month'])[:7]][cat] += v
    for cid, kws in kw_loc.items():
        city = (prof.get(cid) or {}).get('city')
        by[cid]['kw'] = [dict(k=k, v=v, cat=kw_cat(k, city)) for k, v in sorted(kws.items(), key=lambda x: -x[1])[:25]]
    cats = defaultdict(int)
    for kw, v in kw_tot.items():
        cats[kw_cat(kw, None)] += v
    return dict(
        end=end, by_cid=dict(by),
        months=[dict(m=k, **v) for k, v in sorted(months.items())],
        keywords=[dict(k=k, v=v, cat=kw_cat(k, None), n=sum(1 for c in kw_loc if k in kw_loc[c])) for k, v in sorted(kw_tot.items(), key=lambda x: -x[1])[:150]],
        kw_cats=[dict(key=k, label=next((l for kk, l, _ in KW_CATS if kk == k), {'plaats': 'Plaats zonder product', 'overig': 'Overig'}.get(k, k)), v=v)
                 for k, v in sorted(cats.items(), key=lambda x: -x[1])],
        kw_months=[dict(m=m, **v) for m, v in sorted(kw_month.items())],
        locations=[dict(id=l['location_id'], t=l.get('title'), c=l.get('city'), cid=l.get('cid'), visible=bool(l.get('cid') and l['cid'] in prof),
                        status=l.get('status')) for l in locs])
