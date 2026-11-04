// ==== Bedrijfsprofielen (Google Business Profiles) ====
// Data: D.gbp, built by src/gpmon/gbp_view.py. Everything here is presentation and filtering.
const G = D.gbp;
const GOWN = G ? G.own : [];
const GCOMP = G ? Object.fromEntries(G.comp.map(c => [c.cid, c])) : {};
const GALL = G ? Object.fromEntries([...G.own, ...G.comp].map(p => [p.cid, p])) : {};
const gsub = { v: 'overzicht' };
const gst = { q: '', sort: ['acties', -1], map: 'r', rvStars: 'all', rvAns: 'all', rvQ: '', rvAll: false, actGroup: 'all', actImpact: 'all', actView: 'actie', actKey: '', actQ: '', cmpAll: false };
const CITY_ALIAS = { 'Den Bosch': ["'s-Hertogenbosch", 'Den Bosch', 'Rosmalen'], 'Den Haag': ['Den Haag', "'s-Gravenhage", 'The Hague'] };
const gShort = t => String(t || '').replace(/\s+[-|]\s+Garageboxen en Opslagruimtes/i, '').replace(/\s+[-|]\s+.*$/, '');
const med = xs => { const a = xs.filter(x => x != null && !isNaN(x)).sort((a, b) => a - b); if (!a.length) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
const avg = xs => { const a = xs.filter(x => x != null && !isNaN(x)); return a.length ? a.reduce((s, x) => s + x, 0) / a.length : null; };
const pc = x => x == null ? 'n.v.t.' : Math.round(100 * x) + '%';
const sc = x => x == null ? 'n.v.t.' : nf(x, 1);
const stars = r => r == null ? '' : '★'.repeat(Math.round(r)) + '☆'.repeat(5 - Math.round(r));
const DONE_KEY = 'gp-gbp-done';
let gDone = {};
try { gDone = JSON.parse(localStorage.getItem(DONE_KEY) || '{}') || {}; } catch (e) { gDone = {}; }
const saveDone = () => { try { localStorage.setItem(DONE_KEY, JSON.stringify(gDone)); } catch (e) { /* private window: keep in memory */ } };
const actsOf = cid => G.actions.filter(a => a.cid === cid);
const openActs = cid => actsOf(cid).filter(a => !gDone[a.id]).length;
const chip = (txt, cls) => `<span class="chip ${cls}">${esc(txt)}</span>`;
const lvl = v => `<span class="lvl l-${v.toLowerCase()}">${v}</span>`;

function gProfiles() {
  let ps = GOWN;
  if (st.city) { const names = (CITY_ALIAS[st.city] || [st.city]).map(x => x.toLowerCase()); ps = ps.filter(p => names.some(n => (p.c || '').toLowerCase().includes(n) || (p.t || '').toLowerCase().includes(n))); }
  if (gst.q) { const q = gst.q.toLowerCase(); ps = ps.filter(p => (p.t + ' ' + p.c + ' ' + p.a).toLowerCase().includes(q)); }
  return ps;
}
const compsOf = p => (p.comps || []).map(c => GCOMP[c]).filter(Boolean);

function gKpis(ps) {
  const cs = [...new Map(ps.flatMap(compsOf).map(c => [c.cid, c])).values()];
  const reply = arr => { const f = arr.reduce((s, p) => s + (p.n_fetched || 0), 0), u = arr.reduce((s, p) => s + (p.unanswered || 0), 0); return f ? (f - u) / f : null; };
  return {
    own: { r: avg(ps.map(p => p.r)), v: med(ps.map(p => p.v)), vt: ps.reduce((s, p) => s + p.v, 0), n365: med(ps.map(p => p.n365)), reply: reply(ps), ph: med(ps.map(p => p.ph)), score: avg(ps.map(p => p.score)), posts: ps.length ? ps.filter(p => p.posts90).length / ps.length : null },
    comp: { r: avg(cs.map(p => p.r)), v: med(cs.map(p => p.v)), vt: cs.reduce((s, p) => s + p.v, 0), n365: med(cs.map(p => p.n365)), reply: reply(cs), ph: med(cs.map(p => p.ph)), score: avg(cs.map(p => p.score)), posts: cs.length ? cs.filter(p => p.posts90).length / cs.length : null },
    nComp: cs.length
  };
}

function vGBP() {
  if (!G) return `<section><div class="band"><div><p class="eyebrow">Bedrijfsprofielen</p><h2>Nog geen <span class="hl">profieldata.</span></h2><p>Na de eerste run met bedrijfsprofielen verschijnt hier de analyse per park.</p></div><div><p class="eyebrow">Starten</p><p>Draai de workflow met mode <b>gbp</b> of wacht op de wekelijkse run.</p></div></div></section>`;
  const body = { overzicht: gOverview, profielen: gTable, concurrenten: gCompetitors, reviews: gReviews, acties: gActions, prestaties: gPerformance }[gsub.v]();
  return `<div class="subtabs" role="tablist">${[['overzicht', 'Overzicht'], ['profielen', 'Profielen'], ['concurrenten', 'Concurrenten'], ['reviews', 'Reviews'], ['acties', 'Acties'], ['prestaties', 'Prestaties']].map(([id, l]) => `<button role="tab" aria-selected="${gsub.v === id}" class="${gsub.v === id ? 'on' : ''}" data-gsub="${id}">${l}${id === 'acties' ? ` <span class="cnt">${G.actions.filter(a => !gDone[a.id]).length}</span>` : ''}</button>`).join('')}</div>` + body;
}

function cmpTile(label, own, comp, fmt, better, sub) {
  const diff = own != null && comp != null ? own - comp : null;
  const good = diff == null ? null : better === 'hi' ? diff >= 0 : diff <= 0;
  return `<div class="card kpi"><span class="stat-l">${label}</span><span class="num">${fmt(own)}</span><span class="of">${sub}</span>
    <div class="vs"><span>Concurrenten ${fmt(comp)}</span>${good == null ? '' : `<span class="pill2 ${good ? 'ok' : 'bad'}">${good ? 'voor' : 'achter'}</span>`}</div></div>`;
}

function gOverview() {
  const ps = gProfiles(), k = gKpis(ps);
  const open = G.actions.filter(a => !gDone[a.id] && ps.some(p => p.cid === a.cid));
  const MAPG = [['r', 'Score'], ['v', 'Reviews'], ['score', 'Profielscore'], ['acts', 'Open acties']];
  const val = p => gst.map === 'acts' ? openActs(p.cid) : p[gst.map];
  const cls = p => { const v = val(p); if (gst.map === 'r') return v == null ? 'h0' : v >= 4.6 ? 'h5' : v >= 4.2 ? 'h4' : v >= 3.8 ? 'h3' : v >= 3 ? 'h2' : 'h1';
    if (gst.map === 'v') return !v ? 'h0' : v >= 20 ? 'h5' : v >= 10 ? 'h4' : v >= 5 ? 'h3' : v >= 2 ? 'h2' : 'h1';
    if (gst.map === 'score') return heatCls(v); return v >= 6 ? 'h1' : v >= 4 ? 'h2' : v >= 2 ? 'h3' : v >= 1 ? 'h4' : 'h5'; };
  const lab = p => { const v = val(p); return gst.map === 'r' ? sc(v) : gst.map === 'score' ? pc(v) : String(v ?? 'n.v.t.'); };
  const legend = { r: ['geen', '<3', '3 tot 3,8', '3,8 tot 4,2', '4,2 tot 4,6', '4,6+'], v: ['0', '1', '2 tot 4', '5 tot 9', '10 tot 19', '20+'], score: ['0%', '<20', '20 tot 40', '40 tot 60', '60 tot 80', '80+'], acts: ['', '6+', '4 tot 5', '2 tot 3', '1', '0'] }[gst.map];
  const dots = ps.filter(p => p.lat).map(p => { const [x, y] = prj(p.lat, p.lon); return `<g class="gdot" data-gp="${p.cid}" tabindex="0" role="button"><title>${esc(gShort(p.t))} · ${MAPG.find(m => m[0] === gst.map)[1]}: ${lab(p)}</title><circle cx="${x}" cy="${y}" r="7.5" class="m ${cls(p)}"/></g>`; }).join('');
  const top = G.top.map(t => ({ ...t, open: t.cids.filter(c => ps.some(p => p.cid === c) && !gDone[G.actions.find(a => a.cid === c && (a.key === t.key || (t.key === 'theme' && a.key.startsWith('theme_'))))?.id]).length })).filter(t => t.open);
  const rows = [['Score', k.own.r, k.comp.r, sc, 'hi'], ['Reviews per profiel (mediaan)', k.own.v, k.comp.v, x => nf(x, 0), 'hi'], ['Nieuwe reviews per jaar (mediaan)', k.own.n365, k.comp.n365, x => nf(x, 0), 'hi'],
    ['Reactiepercentage op reviews', k.own.reply, k.comp.reply, pc, 'hi'], ["Foto's per profiel (mediaan)", k.own.ph, k.comp.ph, x => nf(x, 0), 'hi'], ['Profielen met post in 90 dagen', k.own.posts, k.comp.posts, pc, 'hi'], ['Profielscore', k.own.score, k.comp.score, pc, 'hi']];
  const mx = (a, b) => Math.max(a || 0, b || 0) || 1;
  return `
  <section><div class="band"><div><p class="eyebrow">Google Bedrijfsprofielen · ${ps.length} parken${st.city ? ' · ' + esc(st.city) : ''}</p><h2>Wat moet er <span class="hl">per park</span> anders?</h2><p>Profiel, reviews en concurrentie van elk GaragePark, met de acties die het meeste opleveren.</p></div>
  <div><p class="eyebrow">Meting</p><div class="big">${fmtDate(G.run)}</div><p>${ps.length} eigen profielen · ${k.nComp} concurrenten in de buurt · ${open.length} open acties</p></div></div></section>
  <section><div class="grid g4">
    ${cmpTile('Gemiddelde score', k.own.r, k.comp.r, sc, 'hi', `over ${ps.filter(p => p.r).length} profielen met reviews`)}
    ${cmpTile('Reviews per profiel', k.own.v, k.comp.v, x => nf(x, 0), 'hi', `mediaan · ${k.own.vt.toLocaleString('nl-NL')} reviews in totaal`)}
    ${cmpTile('Reactiepercentage', k.own.reply, k.comp.reply, pc, 'hi', 'aandeel reviews met reactie van GaragePark')}
    ${cmpTile('Profielscore', k.own.score, k.comp.score, pc, 'hi', 'aandeel van 12 profielonderdelen ingevuld')}
  </div></section>
  <section><div class="grid g2 gtop">
    <div class="tcard"><div class="thead"><div><h4>De acties met de meeste impact</h4><p>Over alle parken, op volgorde van impact en moeite. Klik voor de parken waar het speelt.</p></div></div>
    <div class="scroll"><table><thead><tr><th>#</th><th>Actie</th><th class="r">Parken</th><th>Impact</th><th>Moeite</th></tr></thead><tbody>
    ${top.slice(0, 7).map((t, i) => `<tr class="click" data-gact="${t.key}" tabindex="0"><td><span class="rank">${i + 1}</span></td><td><b>${esc(t.title)}</b></td><td class="r">${t.open}</td><td>${lvl(t.impact)}</td><td>${lvl(t.effort)}</td></tr>`).join('') || '<tr><td colspan="5" class="of">Alle acties zijn afgevinkt.</td></tr>'}
    </tbody></table></div></div>
    <div class="card"><h4>GaragePark tegenover de concurrentie</h4><p class="cap">Mediaan of gemiddelde van de eigen profielen naast de ${k.nComp} concurrenten die Google bij die profielen toont</p>
    <div class="vsrows">${rows.map(([l, a, b, f]) => `<div class="vsrow"><span class="vl">${l}</span><div class="vb"><i class="o" style="width:${100 * (a || 0) / mx(a, b)}%"></i><i class="c" style="width:${100 * (b || 0) / mx(a, b)}%"></i></div><span class="vn"><b>${f(a)}</b><span>${f(b)}</span></span></div>`).join('')}</div>
    <div class="legend" style="margin-top:12px"><span><i style="background:var(--brand)"></i>GaragePark</span><span><i style="background:var(--accent-soft)"></i>Concurrenten</span></div></div>
  </div></section>
  <section><div class="card mapcard"><div class="maphead"><div><h4 style="display:block">Alle parken op de kaart</h4><p class="cap">Klik op een park voor het volledige profiel, de concurrenten en de acties.</p></div>${seg('gmap', MAPG, gst.map)}</div>
  <div class="mapgrid"><div class="mapwrap"><svg viewBox="0 0 ${NLMAP.W} ${NLMAP.H}" role="img" aria-label="Kaart met alle GaragePark-profielen">
    <path d="${NLMAP.de}" class="nb"/><path d="${NLMAP.be}" class="nb"/><path d="${NLMAP.nl}" class="land"/>${dots}</svg></div>
  <div class="maplist"><div class="legend">${legend.map((l, i) => l ? `<span><i class="h${i}"></i>${l}</span>` : '').join('')}</div>
  <ol class="rk">${[...ps].sort((a, b) => gst.map === 'acts' ? openActs(b.cid) - openActs(a.cid) : (val(a) ?? -1) - (val(b) ?? -1)).slice(0, 14).map((p, i) => `<li><button data-gp="${p.cid}"><span class="i">${i + 1}</span><span class="c">${esc(gShort(p.t).replace(/^GaragePark\s*/i, ''))}</span><span class="t"><i class="${cls(p)}" style="width:${gst.map === 'r' ? 20 * (p.r || 0) : gst.map === 'score' ? 100 * (p.score || 0) : gst.map === 'v' ? Math.min(100, 4 * p.v) : Math.min(100, 12 * openActs(p.cid))}%"></i></span><b>${lab(p)}</b></button></li>`).join('')}</ol>
  <p class="of">${gst.map === 'acts' ? 'Meeste open acties eerst' : 'Laagste eerst: hier is de meeste winst'} · 14 van ${ps.length}</p></div></div></div></section>
  <section><div class="note"><b>Prestaties per profiel</b> (weergaven, zoektermen, gesprekken, routeverzoeken, websiteklikken) staan in Bedrijfsprofielmanager en vragen een koppeling met de Business Profile API. Zie het tabblad Prestaties.</div></section>`;
}

function gTable() {
  const ps = gProfiles();
  const cols = [['t', 'Park'], ['r', 'Score', 1], ['v', 'Reviews', 1], ['n365', '12 mnd', 1], ['reply', 'Reactie', 1], ['ph', "Foto's", 1], ['posts90', 'Posts 90d', 1], ['score', 'Profielscore', 1], ['cmax', 'Grootste concurrent', 1], ['acties', 'Open acties', 1]];
  const rows = ps.map(p => ({ p, cmax: Math.max(0, ...compsOf(p).map(c => c.v || 0)), acties: openActs(p.cid) }));
  const [k, dir] = gst.sort;
  const get = r => k === 'cmax' || k === 'acties' ? r[k] : k === 't' ? gShort(r.p.t) : r.p[k];
  rows.sort((a, b) => { const x = get(a), y = get(b); return (x == null) - (y == null) || dir * (x > y ? 1 : x < y ? -1 : 0); });
  return `<section><div class="tcard"><div class="thead"><div><h4>Alle profielen</h4><p>${ps.length} GaragePark-profielen. Klik op een park voor profiel, concurrenten, reviews en acties.</p></div><input type="search" id="gq" placeholder="Zoek park of plaats…" value="${esc(gst.q)}" aria-label="Zoek park"></div>
  <div class="scroll"><table class="gtab"><thead><tr>${cols.map(([key, l, r]) => `<th class="${r ? 'r' : ''}" data-gsort="${key}">${l} ${k === key ? (dir < 0 ? '↓' : '↑') : '↕'}</th>`).join('')}</tr></thead><tbody>
  ${rows.map(({ p, cmax, acties }) => `<tr class="click" data-gp="${p.cid}" tabindex="0"><td><b>${esc(gShort(p.t))}</b><span class="of sub">${esc(p.c || '')}${p.cl === false ? ' · <b class="warn">niet geclaimd</b>' : ''}</span></td>
    <td class="r">${p.r ? sc(p.r) : '<span class="of">geen</span>'}</td><td class="r">${p.v}</td><td class="r">${p.n365}</td><td class="r">${pc(p.reply)}</td><td class="r">${p.ph}</td><td class="r">${p.posts90 || '<span class="of">0</span>'}</td>
    <td class="r"><div class="meter"><span class="t"><i style="width:${100 * p.score}%"></i></span><b>${pc(p.score)}</b></div></td><td class="r">${cmax}</td><td class="r">${acties ? `<span class="chip soft">${acties}</span>` : '<span class="of">0</span>'}</td></tr>`).join('')}
  </tbody></table></div></div></section>`;
}

function gCompetitors() {
  const ps = gProfiles();
  const cs = [...new Map(ps.flatMap(compsOf).map(c => [c.cid, c])).values()];
  const groups = new Map();
  cs.forEach(c => { const b = c.brand || gShort(c.t); if (!groups.has(b)) groups.set(b, []); groups.get(b).push(c); });
  const agg = (name, arr, own) => { const f = arr.reduce((s, p) => s + (p.n_fetched || 0), 0), u = arr.reduce((s, p) => s + (p.unanswered || 0), 0);
    return { name, own, n: arr.length, r: avg(arr.map(p => p.r)), v: med(arr.map(p => p.v)), n365: med(arr.map(p => p.n365)), reply: f ? (f - u) / f : null, posts: arr.filter(p => p.posts90).length / arr.length, score: avg(arr.map(p => p.score)), ph: med(arr.map(p => p.ph)) }; };
  let rows = [...groups].map(([b, arr]) => agg(b, arr, false)).sort((a, b) => b.n - a.n || (b.v || 0) - (a.v || 0));
  const lim = gst.cmpAll ? rows : rows.slice(0, 12);
  const all = [agg('GaragePark', ps, true), ...lim];
  const gap = ps.map(p => { const c = compsOf(p).sort((a, b) => (b.v || 0) - (a.v || 0))[0]; return { p, c }; }).filter(x => x.c).sort((a, b) => ((b.c.v || 0) - b.p.v) - ((a.c.v || 0) - a.p.v));
  return `<section><div class="tcard"><div class="thead"><div><h4>Merken rond de parken</h4><p>Concurrenten die Google bij de GaragePark-profielen toont onder "Mensen zoeken ook", per merk samengevoegd. Mediaan per profiel.</p></div></div>
  <div class="scroll"><table><thead><tr><th>Merk</th><th class="r">Profielen</th><th class="r">Score</th><th class="r">Reviews</th><th class="r">Nieuw 12 mnd</th><th class="r">Reactie</th><th class="r">Foto's</th><th class="r">Posts 90d</th><th class="r">Profielscore</th></tr></thead><tbody>
  ${all.map(r => `<tr class="${r.own ? 'own' : ''}"><td>${esc(r.name)}${r.own ? '<span class="wij">Wij</span>' : ''}</td><td class="r">${r.n}</td><td class="r">${sc(r.r)}</td><td class="r">${nf(r.v, 0)}</td><td class="r">${nf(r.n365, 0)}</td><td class="r">${pc(r.reply)}</td><td class="r">${nf(r.ph, 0)}</td><td class="r">${pc(r.posts)}</td><td class="r"><div class="meter"><span class="t"><i style="width:${100 * (r.score || 0)}%"></i></span><b>${pc(r.score)}</b></div></td></tr>`).join('')}
  </tbody></table></div><div class="tfoot"><span>${rows.length} merken en losse aanbieders</span>${rows.length > 12 ? `<button id="cmpall">${gst.cmpAll ? 'Toon top 12' : 'Toon alle ' + rows.length}</button>` : ''}</div></div></section>
  <section><div class="tcard"><div class="thead"><div><h4>Per park: de sterkste concurrent</h4><p>De concurrent met de meeste reviews naast elk park, grootste achterstand eerst. Klik voor de volledige vergelijking.</p></div></div>
  <div class="scroll"><table><thead><tr><th>Park</th><th class="r">Reviews · score</th><th>Sterkste concurrent</th><th class="r">Reviews · score</th><th class="r">Verschil in reviews</th></tr></thead><tbody>
  ${gap.map(({ p, c }) => `<tr class="click" data-gp="${p.cid}" tabindex="0"><td><b>${esc(gShort(p.t))}</b></td><td class="r">${p.v} · ${p.r ? sc(p.r) : 'n.v.t.'}</td><td>${esc(gShort(c.t))}</td><td class="r">${c.v} · ${c.r ? sc(c.r) : 'n.v.t.'}</td><td class="r"><b class="${(c.v || 0) > p.v ? 'warn' : ''}">${(c.v || 0) - p.v > 0 ? '+' : ''}${(c.v || 0) - p.v}</b></td></tr>`).join('')}
  </tbody></table></div></div></section>`;
}

function barMonths(ms) {
  if (!ms.length) return '<div class="empty">Geen reviews in de laatste 24 maanden.</div>';
  const W = 640, H = 190, L = 30, B = 26, T = 10, iw = W - L - 8, ih = H - T - B, mx = Math.max(...ms.map(m => m.n), 1), bw = iw / ms.length;
  const y = v => T + ih * (1 - v / mx);
  const ticks = [...new Set([0, Math.ceil(mx / 2), mx])];
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Reviews per maand">
    ${ticks.map(t => `<line class="gl" x1="${L}" x2="${W - 8}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join('')}
    ${ms.map((m, i) => `<g><title>${m.m}: ${m.n} reviews, ${m.neg} negatief</title><rect x="${L + i * bw + 1.5}" y="${y(m.n)}" width="${Math.max(1, bw - 3)}" height="${ih - (y(m.n) - T)}" rx="2" fill="var(--accent-soft)"/><rect x="${L + i * bw + 1.5}" y="${y(m.neg)}" width="${Math.max(1, bw - 3)}" height="${ih - (y(m.neg) - T)}" rx="2" fill="var(--bad)"/></g>`).join('')}
    ${ms.map((m, i) => (i === 0 || i === ms.length - 1 || m.m.endsWith('-01')) ? `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${m.m.slice(5) === '01' ? m.m.slice(0, 4) : new Date(m.m + '-15').toLocaleDateString('nl-NL', { month: 'short' })}</text>` : '').join('')}
  </svg></div><div class="legend"><span><i style="background:var(--accent-soft)"></i>Reviews</span><span><i style="background:var(--bad)"></i>Waarvan 1 of 2 sterren</span></div>`;
}

function gReviews() {
  const ps = gProfiles(), ids = new Set(ps.map(p => p.cid));
  const R = G.reviews.filter(r => ids.has(r.cid));
  const k = gKpis(ps);
  const negOpen = R.filter(r => r.s <= 2 && !r.ans).length;
  const distO = [1, 2, 3, 4, 5].map(s => ps.reduce((n, p) => n + ((p.st || [])[s - 1] || 0), 0));
  const cs = [...new Map(ps.flatMap(compsOf).map(c => [c.cid, c])).values()];
  const distC = [1, 2, 3, 4, 5].map(s => cs.reduce((n, p) => n + ((p.st || [])[s - 1] || 0), 0));
  const dist = (d, lab) => { const t = d.reduce((a, b) => a + b, 0) || 1; return `<div class="dist"><span class="dl">${lab}</span><div class="db">${[4, 3, 2, 1, 0].map(i => `<i class="s${i + 1}" style="width:${100 * d[i] / t}%" title="${i + 1} ster: ${d[i]}"></i>`).join('')}</div></div>`; };
  const ms = G.months.filter(m => true);
  let list = R.filter(r => (gst.rvStars === 'all' || (gst.rvStars === 'neg' ? r.s <= 2 : gst.rvStars === 'pos' ? r.s >= 4 : r.s === 3)) && (gst.rvAns === 'all' || (gst.rvAns === 'open' ? !r.ans : r.ans)));
  if (gst.rvQ) { const q = gst.rvQ.toLowerCase(); list = list.filter(r => (r.t + ' ' + gShort(GALL[r.cid]?.t)).toLowerCase().includes(q)); }
  const lim = gst.rvAll ? list : list.slice(0, 40);
  const th = G.themes.map(t => ({ ...t })).sort((a, b) => b.own_neg - a.own_neg);
  const mxT = Math.max(1, ...th.map(t => Math.max(t.own_neg, t.own_pos)));
  return `<section><div class="grid g4">
    <div class="card kpi"><span class="stat-l">Reviews opgehaald</span><span class="num">${R.length}</span><span class="of">van ${ps.reduce((s, p) => s + p.v, 0)} volgens Google · ${R.filter(r => r.t).length} met tekst</span></div>
    ${cmpTile('Nieuwe reviews per jaar', k.own.n365, k.comp.n365, x => nf(x, 0), 'hi', 'mediaan per profiel, laatste 12 maanden')}
    ${cmpTile('Reactiepercentage', k.own.reply, k.comp.reply, pc, 'hi', 'aandeel met reactie van de eigenaar')}
    <div class="card kpi"><span class="stat-l">Negatief zonder reactie</span><span class="num" style="color:${negOpen ? 'var(--bad)' : 'inherit'}">${negOpen}</span><span class="of">reviews met 1 of 2 sterren zonder antwoord</span></div>
  </div></section>
  <section><div class="grid g2">
    <div class="card"><h4>Reviews per maand</h4><p class="cap">Alle GaragePark-profielen samen, laatste 24 maanden</p>${barMonths(ms)}</div>
    <div class="card"><h4>Verdeling van de sterren</h4><p class="cap">Alle reviews volgens Google, GaragePark naast de concurrenten in de buurt</p>
      <div style="margin-top:14px;display:grid;gap:12px">${dist(distO, 'GaragePark')}${dist(distC, 'Concurrenten')}</div>
      <div class="legend" style="margin-top:12px">${[5, 4, 3, 2, 1].map(s => `<span><i class="s${s}"></i>${s} ster${s > 1 ? 'ren' : ''}</span>`).join('')}</div></div>
  </div></section>
  <section><div class="tcard"><div class="thead"><div><h4>Waar reviews over gaan</h4><p>Onderwerpen in de reviewteksten (indeling op trefwoorden). Negatief is 1 of 2 sterren, positief 4 of 5. Concurrenten: laatste 40 reviews per profiel.</p></div></div>
  <div class="scroll"><table><thead><tr><th>Onderwerp</th><th>GaragePark negatief</th><th>GaragePark positief</th><th class="r">Concurrenten negatief</th><th class="r">Concurrenten positief</th></tr></thead><tbody>
  ${th.map(t => `<tr class="click" data-gtheme="${t.key}" tabindex="0"><td><b>${esc(t.label)}</b></td><td><div class="tb"><i class="neg" style="width:${100 * t.own_neg / mxT}%"></i><b>${t.own_neg}</b></div></td><td><div class="tb"><i class="pos" style="width:${100 * t.own_pos / mxT}%"></i><b>${t.own_pos}</b></div></td><td class="r">${t.comp_neg}</td><td class="r">${t.comp_pos}</td></tr>`).join('')}
  </tbody></table></div></div></section>
  <section><div class="tcard"><div class="thead"><div><h4>Reviews</h4><p>Nieuwste eerst. Klik op een review voor de tekst en de reactie.</p></div>
  <div class="filters"><input type="search" id="grq" placeholder="Zoek in reviews of park…" value="${esc(gst.rvQ)}" aria-label="Zoek in reviews">
  ${seg('grs', [['all', 'Alle'], ['neg', '1 tot 2'], ['mid', '3'], ['pos', '4 tot 5']], gst.rvStars)}${seg('gra', [['all', 'Alle'], ['open', 'Zonder reactie'], ['done', 'Met reactie']], gst.rvAns)}</div></div>
  <div class="rvlist">${lim.map(r => `<details class="rv ${r.s <= 2 ? 'neg' : ''}"><summary><span class="stars">${stars(r.s)}</span><span class="rvt">${r.t ? esc(r.t.slice(0, 140)) + (r.t.length > 140 ? '…' : '') : '<span class="of">Alleen sterren, geen tekst</span>'}</span><span class="of">${esc(gShort(GALL[r.cid]?.t).replace(/^GaragePark\s*/i, ''))} · ${r.d ? fmtDate(r.d) : esc(r.ago || '')}</span>${r.ans ? chip('Beantwoord', 'soft') : chip('Geen reactie', r.s <= 2 ? 'bad' : 'no')}</summary>
    <div class="rvbody">${r.t ? `<p>${esc(r.t)}</p>` : ''}${r.th.length ? `<div class="chips">${r.th.map(k => chip(G.themes.find(t => t.key === k)?.label || k, 'soft')).join('')}</div>` : ''}${r.a ? `<div class="ans"><b>Reactie GaragePark</b><p>${esc(r.a)}</p></div>` : ''}${r.u ? `<a class="plain" href="${esc(r.u)}" target="_blank" rel="noopener">Open in Google${extIcon}</a>` : ''}</div></details>`).join('') || '<p class="of" style="padding:16px 24px">Geen reviews met deze filters.</p>'}</div>
  <div class="tfoot"><span>${lim.length} getoond · ${list.length} reviews</span>${list.length > 40 ? `<button id="rvall">${gst.rvAll ? 'Toon minder' : 'Toon alle ' + list.length}</button>` : ''}</div></div></section>`;
}

function gActions() {
  const ps = gProfiles(), ids = new Set(ps.map(p => p.cid));
  let A = G.actions.filter(a => ids.has(a.cid));
  const total = A.length, done = A.filter(a => gDone[a.id]).length;
  if (gst.actGroup !== 'all') A = A.filter(a => a.group === gst.actGroup);
  if (gst.actImpact !== 'all') A = A.filter(a => a.impact === gst.actImpact);
  if (gst.actKey) A = A.filter(a => a.key === gst.actKey || (gst.actKey === 'theme' && a.key.startsWith('theme_')));
  const item = a => `<li class="act ${gDone[a.id] ? 'done' : ''}"><label><input type="checkbox" data-gdone="${a.id}" ${gDone[a.id] ? 'checked' : ''}><span class="box"></span></label>
    <div><b>${esc(a.title)}</b><p>${esc(a.why)}</p></div><div class="lv">${lvl(a.impact)}${lvl(a.effort)}</div></li>`;
  let body;
  if (gst.actView === 'park') {
    const by = new Map(); A.forEach(a => { if (!by.has(a.cid)) by.set(a.cid, []); by.get(a.cid).push(a); });
    body = [...by].sort((x, y) => y[1].filter(a => !gDone[a.id]).length - x[1].filter(a => !gDone[a.id]).length).map(([cid, as]) => `<div class="agrp"><button class="aghead" data-gp="${cid}"><b>${esc(gShort(GALL[cid].t))}</b><span class="of">${as.filter(a => !gDone[a.id]).length} open van ${as.length}</span></button><ul class="acts">${as.map(item).join('')}</ul></div>`).join('');
  } else {
    const by = new Map(); A.forEach(a => { const k = a.key.startsWith('theme_') ? a.key : a.key; if (!by.has(k)) by.set(k, []); by.get(k).push(a); });
    body = [...by].map(([k, as]) => `<div class="agrp"><div class="aghead"><b>${esc(as[0].key.startsWith('theme_') ? as[0].title : (G.top.find(t => t.key === k)?.title || as[0].title))}</b><span class="of">${as.length} park${as.length > 1 ? 'en' : ''} · ${lvl(as[0].impact)} impact · ${lvl(as[0].effort)} moeite</span></div>
      <ul class="acts">${as.map(a => `<li class="act ${gDone[a.id] ? 'done' : ''}"><label><input type="checkbox" data-gdone="${a.id}" ${gDone[a.id] ? 'checked' : ''}><span class="box"></span></label><div><button class="lnk2" data-gp="${a.cid}">${esc(gShort(GALL[a.cid].t))}</button><p>${esc(a.why)}</p></div></li>`).join('')}</ul></div>`).join('');
  }
  return `<section><div class="card"><div class="aprog"><div><h4>Actielijst</h4><p class="cap">Afvinken wordt in deze browser onthouden, niet gedeeld met anderen. Een nieuwe meting maakt opgeloste punten vanzelf ongedaan.</p></div>
    <div class="pg"><span class="num sm">${done}<span class="of"> / ${total}</span></span><div class="bar"><i style="width:${total ? 100 * done / total : 0}%"></i></div><span class="of">afgevinkt</span></div></div>
    <div class="filters" style="margin-top:14px">${seg('gav', [['actie', 'Per actie'], ['park', 'Per park']], gst.actView)}${seg('gag', [['all', 'Alles'], ['Reviews', 'Reviews'], ['Profiel', 'Profiel'], ['Beheer', 'Beheer']], gst.actGroup)}${seg('gai', [['all', 'Alle impact'], ['Hoog', 'Hoog'], ['Middel', 'Middel'], ['Laag', 'Laag']], gst.actImpact)}
    ${gst.actKey ? `<button class="chip soft" id="gactclear">Filter: ${esc(G.top.find(t => t.key === gst.actKey)?.title || gst.actKey)} ✕</button>` : ''}<button class="x2" id="gcopy">Kopieer als tekst</button></div></div></section>
  <section class="alist">${body || '<p class="of">Geen acties met deze filters.</p>'}</section>`;
}

function gPerformance() {
  return `<section><div class="band"><div><p class="eyebrow">Prestaties uit Bedrijfsprofielmanager</p><h2>Wat de profielen <span class="hl">opleveren.</span></h2><p>Weergaven, zoektermen en acties per park komen alleen uit het beheeraccount van de profielen zelf.</p></div>
  <div><p class="eyebrow">Wat er nodig is</p><p>Toegang tot de Bedrijfsprofielen van GaragePark (beheerdersrol voor Follo) en een koppeling met de Business Profile Performance API. Daarna komt deze data wekelijks mee in dezelfde run.</p></div></div></section>
  <section><div class="grid g3">
    <div class="card"><h4>Weergaven</h4><p class="cap" style="margin-top:8px">Per maand en per park: Google Zoeken en Maps, mobiel en desktop.</p></div>
    <div class="card"><h4>Acties</h4><p class="cap" style="margin-top:8px">Gesprekken, routeverzoeken, websiteklikken en berichten per park, met de trend per maand.</p></div>
    <div class="card"><h4>Zoektermen</h4><p class="cap" style="margin-top:8px">Waarop elk profiel gevonden wordt, met het aandeel merk, garagebox, opslag en plaatsnaam.</p></div>
  </div></section>`;
}

function gpDrawer(cid) {
  const p = GALL[cid]; if (!p) return;
  const cs = compsOf(p), all = [p, ...cs];
  const R = G.reviews.filter(r => r.cid === cid);
  const acts = actsOf(cid);
  const mxv = Math.max(1, ...all.map(x => x.v || 0));
  const fields = G.score_fields.map(([k, l]) => ({ l, own: p.chk[k], share: cs.length ? cs.filter(c => c.chk[k]).length / cs.length : null }));
  const pr = p.pv;
  const summary = [];
  if (p.v < 10) summary.push(`${p.v} review${p.v === 1 ? '' : 's'}`); else summary.push(`${p.v} reviews met een ${sc(p.r)}`);
  const top = [...cs].sort((a, b) => (b.v || 0) - (a.v || 0))[0];
  if (top && (top.v || 0) > p.v) summary.push(`${gShort(top.t)} heeft er ${top.v}`);
  if (p.unanswered) summary.push(`${p.unanswered} zonder reactie`);
  openDrawer(`<div><p class="eyebrow">Bedrijfsprofiel · ${esc(p.c || '')}</p><h2>${esc(gShort(p.t))}</h2><p class="of">${esc(p.a || '')}</p></div>
  <div class="chips">${p.cl === false ? chip('Niet geclaimd', 'bad') : chip('Geclaimd', 'yes')}${chip(p.cat || 'Geen categorie', 'soft')}${p.cats.map(c => chip(c, 'soft')).join('')}${p.h247 ? chip('24/7 open', 'soft') : p.h ? chip('Openingstijden', 'soft') : chip('Geen openingstijden', 'no')}</div>
  <div class="mini">${[['Reviews', `${p.v} · ${p.r ? sc(p.r) : 'n.v.t.'}`], ['Nieuw 12 mnd', p.n365], ['Reactie', pc(p.reply)], ["Foto's", p.ph], ['Posts 90d', p.posts90], ['Profielscore', pc(p.score)]].map(([l, v]) => `<div><span>${l}</span><b>${v}</b></div>`).join('')}</div>
  ${pr ? `<p class="of">Vorige meting: ${pr.v ?? 'n.v.t.'} reviews · ${pr.r ? sc(pr.r) : 'n.v.t.'} · ${pr.ph ?? 'n.v.t.'} foto's</p>` : ''}
  <div class="quote">${esc(summary.join(', '))}.${acts.length ? ` ${acts.filter(a => a.impact === 'Hoog').length} acties met hoge impact.` : ' Geen open acties.'}</div>
  <div class="tcard"><div class="thead"><div><h4>Naast de concurrenten</h4><p>Profielen die Google bij dit park toont onder "Mensen zoeken ook".</p></div></div>
  <div class="scroll"><table><thead><tr><th>Profiel</th><th class="r">Score</th><th>Reviews</th><th class="r">12 mnd</th><th class="r">Reactie</th><th class="r">Foto's</th><th class="r">Posts</th><th class="r">Profielscore</th></tr></thead><tbody>
  ${all.map(x => `<tr class="${x.own ? 'own' : ''}"><td>${x.mu ? `<a class="plain" href="${esc(x.mu)}" target="_blank" rel="noopener">${esc(gShort(x.t))}${extIcon}</a>` : esc(gShort(x.t))}</td><td class="r">${x.r ? sc(x.r) : 'n.v.t.'}</td><td><div class="meter l"><span class="t"><i style="width:${100 * (x.v || 0) / mxv}%"></i></span><b>${x.v || 0}</b></div></td><td class="r">${x.n365}</td><td class="r">${pc(x.reply)}</td><td class="r">${x.ph}</td><td class="r">${x.posts90}</td><td class="r">${pc(x.score)}</td></tr>`).join('')}
  </tbody></table></div></div>
  <div class="tcard"><div class="thead"><div><h4>Profielonderdelen</h4><p>Wat dit profiel heeft, naast het aandeel concurrenten dat het ook heeft.</p></div></div>
  <div class="checks">${fields.map(f => `<div class="ck ${f.own ? 'on' : 'off'}"><span class="mk">${f.own ? '✓' : '–'}</span><span>${esc(f.l)}</span><span class="of">${f.share == null ? '' : 'concurrenten ' + pc(f.share)}</span></div>`).join('')}</div></div>
  <div><p class="eyebrow">Acties voor dit park</p><ul class="acts">${acts.map(a => `<li class="act ${gDone[a.id] ? 'done' : ''}"><label><input type="checkbox" data-gdone="${a.id}" ${gDone[a.id] ? 'checked' : ''}><span class="box"></span></label><div><b>${esc(a.title)}</b><p>${esc(a.why)}</p></div><div class="lv">${lvl(a.impact)}${lvl(a.effort)}</div></li>`).join('') || '<li class="of">Geen acties.</li>'}</ul></div>
  ${p.desc ? `<div><p class="eyebrow">Beschrijving in het profiel</p><div class="answer"><p>${esc(p.desc)}</p></div></div>` : ''}
  <div><p class="eyebrow">Laatste reviews (${R.length})</p><div class="rvlist drawer">${R.slice(0, 12).map(r => `<details class="rv ${r.s <= 2 ? 'neg' : ''}"><summary><span class="stars">${stars(r.s)}</span><span class="rvt">${r.t ? esc(r.t.slice(0, 110)) + (r.t.length > 110 ? '…' : '') : '<span class="of">Alleen sterren</span>'}</span><span class="of">${r.d ? fmtDate(r.d) : ''}</span>${r.ans ? chip('Beantwoord', 'soft') : chip('Geen reactie', r.s <= 2 ? 'bad' : 'no')}</summary><div class="rvbody">${r.t ? `<p>${esc(r.t)}</p>` : ''}${r.a ? `<div class="ans"><b>Reactie</b><p>${esc(r.a)}</p></div>` : ''}</div></details>`).join('') || '<p class="of">Geen reviews.</p>'}</div></div>
  <p class="of"><a href="${esc(p.mu)}" target="_blank" rel="noopener">Open in Google Maps</a>${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">Website</a>` : ''}</p>`);
}

function gCopy() {
  const ps = gProfiles(), ids = new Set(ps.map(p => p.cid));
  const A = G.actions.filter(a => ids.has(a.cid) && !gDone[a.id]);
  const by = new Map(); A.forEach(a => { if (!by.has(a.cid)) by.set(a.cid, []); by.get(a.cid).push(a); });
  const txt = [...by].map(([cid, as]) => gShort(GALL[cid].t) + '\n' + as.map(a => `- [ ] ${a.title} (impact ${a.impact.toLowerCase()}, moeite ${a.effort.toLowerCase()})`).join('\n')).join('\n\n');
  const done = () => { const b = $('#gcopy'); if (b) { b.textContent = 'Gekopieerd'; setTimeout(() => { b.textContent = 'Kopieer als tekst'; }, 1600); } };
  try { navigator.clipboard.writeText(txt).then(done, () => fallback()); } catch (e) { fallback(); }
  function fallback() { openDrawer(`<h2>Actielijst</h2><textarea id="gcopytxt" style="width:100%;min-height:60vh;font:inherit">${esc(txt)}</textarea>`); const t = $('#gcopytxt'); t.focus(); t.select(); }
}

function gbpOverviewCard() {
  if (!G) return '';
  const k = gKpis(GOWN), open = G.actions.filter(a => !gDone[a.id]).length;
  return `<section><div class="card"><h4>Google Bedrijfsprofielen<a class="go" href="#gbp" aria-label="Naar Bedrijfsprofielen">${arrow}</a></h4><p class="cap">${GOWN.length} eigen profielen naast ${k.nComp} concurrenten in de buurt · meting ${fmtDate(G.run)}</p>
  <div class="grid g4" style="margin-top:14px;gap:16px">${[['Score', sc(k.own.r), 'concurrenten ' + sc(k.comp.r)], ['Reviews per profiel', nf(k.own.v, 0), 'concurrenten ' + nf(k.comp.v, 0)], ['Reactiepercentage', pc(k.own.reply), 'concurrenten ' + pc(k.comp.reply)], ['Open acties', open, 'over ' + GOWN.length + ' profielen']].map(([l, v, s]) => `<div class="kpi"><span class="stat-l">${l}</span><span class="num sm">${v}</span><span class="of">${s}</span></div>`).join('')}</div></div></section>`;
}

function gbpEvents() {
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-gsub],[data-gp],[data-gsort],[data-gact],[data-gtheme],#cmpall,#rvall,#gactclear,#gcopy');
    if (!el) return;
    if (el.dataset.gsub) { gsub.v = el.dataset.gsub; render(); return; }
    if (el.dataset.gp) { ev.preventDefault(); gpDrawer(el.dataset.gp); return; }
    if (el.dataset.gsort) { const k = el.dataset.gsort; gst.sort = gst.sort[0] === k ? [k, -gst.sort[1]] : [k, k === 't' ? 1 : -1]; render(); return; }
    if (el.dataset.gact) { gst.actKey = el.dataset.gact; gsub.v = 'acties'; render(); scrollTo({ top: 0 }); return; }
    if (el.dataset.gtheme) { gst.rvQ = ''; gst.rvStars = 'neg'; gsub.v = 'reviews'; render(); return; }
    if (el.id === 'cmpall') { gst.cmpAll = !gst.cmpAll; render(); return; }
    if (el.id === 'rvall') { gst.rvAll = !gst.rvAll; render(); return; }
    if (el.id === 'gactclear') { gst.actKey = ''; render(); return; }
    if (el.id === 'gcopy') { gCopy(); return; }
  });
  document.addEventListener('change', ev => {
    const id = ev.target.dataset && ev.target.dataset.gdone;
    if (!id) return;
    if (ev.target.checked) gDone[id] = new Date().toISOString().slice(0, 10); else delete gDone[id];
    saveDone();
    const li = ev.target.closest('.act'); if (li) li.classList.toggle('done', ev.target.checked);
    if (!$('#ov').hidden) return;  // keep the drawer open; the list behind it refreshes on the next render
    render();
  });
  document.addEventListener('input', ev => {
    const keep = id => { render(); const i = $('#' + id); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } };
    if (ev.target.id === 'gq') { gst.q = ev.target.value; keep('gq'); }
    if (ev.target.id === 'grq') { gst.rvQ = ev.target.value; keep('grq'); }
  });
  document.addEventListener('keydown', ev => { if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches('g.gdot')) { ev.preventDefault(); gpDrawer(ev.target.dataset.gp); } });
}
// segmented controls of this tab (seg ids starting with g) are routed through gSeg from the main handler
function gSeg(k, v) {
  const map = { gmap: 'map', grs: 'rvStars', gra: 'rvAns', gav: 'actView', gag: 'actGroup', gai: 'actImpact' };
  if (!map[k]) return false;
  gst[map[k]] = v; render(); return true;
}
