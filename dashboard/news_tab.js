// ==== Lokaal Nieuws ====
const NW = D.news;
const nst = { g: 'all', per: '90', q: '', tp: 'all', all: false };
const NGROUP = { concurrent: 'Concurrenten', eigen: 'GaragePark', lokaal: 'Lokaal', markt: 'Markt' };
const daysAgo = d => Math.round((Date.now() - new Date(d + 'T12:00:00')) / 864e5);

function nItems(ignoreGroup) {
  if (!NW) return [];
  const q = nst.q.toLowerCase();
  return NW.items.filter(x => (ignoreGroup || nst.g === 'all' || x.g === nst.g)
    && (nst.per === 'all' || daysAgo(x.dt) <= +nst.per)
    && (nst.tp === 'all' || x.tp === nst.tp)
    && (!st.city || x.c.includes(st.city))
    && (!q || (x.t + ' ' + x.s + ' ' + x.d + ' ' + x.b.join(' ')).toLowerCase().includes(q)));
}

function nCard(x) {
  const isNew = x.fs === NW.last && NW.runs.length > 1;
  const src = x.p || (x.d && x.d !== 'google.com' ? x.d.replace(/^www\./, '') : 'Google Nieuws');
  return `<article class="nitem${x.g === 'eigen' ? ' own' : ''}">
    <div class="nmeta"><span>${fmtDate(x.dt)}</span><span class="dot">·</span><span>${esc(src)}</span>${isNew ? '<span class="chip yes">Nieuw</span>' : ''}</div>
    <h4><a href="${esc(x.u)}" target="_blank" rel="noopener">${esc(x.t)}${extIcon}</a></h4>
    ${x.s ? `<p>${esc(x.s)}</p>` : ''}
    <div class="chips"><span class="chip ${x.g === 'eigen' ? 'yes' : 'soft'}">${NGROUP[x.g] || x.g}</span>${x.tp !== 'Overig' ? `<span class="chip soft">${esc(x.tp)}</span>` : ''}${x.b.filter(b => b !== OWNB).map(b => `<span class="chip">${esc(b)}</span>`).join('')}${x.c.map(c => `<span class="chip soft">${esc(c)}</span>`).join('')}</div>
  </article>`;
}

function nWeeks(items) {
  const wk = d => { const t = new Date(d + 'T12:00:00'); t.setDate(t.getDate() - ((t.getDay() + 6) % 7)); return t.toISOString().slice(0, 10); };
  const end = new Date(); const weeks = [];
  for (let i = 11; i >= 0; i--) { const t = new Date(end); t.setDate(t.getDate() - 7 * i); weeks.push(wk(t.toISOString().slice(0, 10))); }
  const m = new Map(weeks.map(w => [w, { concurrent: 0, eigen: 0, lokaal: 0, markt: 0 }]));
  items.forEach(x => { const w = m.get(wk(x.dt)); if (w) w[x.g] = (w[x.g] || 0) + 1; });
  const rows = weeks.map(w => ({ m: w, ...m.get(w) }));
  const keys = ['eigen', 'concurrent', 'lokaal', 'markt'], col = { eigen: 'var(--brand)', concurrent: 'var(--accent)', lokaal: 'var(--heat-3)', markt: 'var(--accent-soft)' };
  const W = 700, H = 200, L = 34, B = 26, T = 8, iw = W - L - 8, ih = H - T - B, bw = iw / rows.length;
  const tot = r => keys.reduce((s, k) => s + r[k], 0), mx = Math.max(1, ...rows.map(tot));
  const y = v => T + ih * (1 - v / mx);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Berichten per week">
    ${[0, mx].map(t => `<line class="gl" x1="${L}" x2="${W - 8}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join('')}
    ${rows.map((r, i) => { let acc = 0; return `<g><title>Week van ${fmtDate(r.m)}\n${keys.map(k => `${NGROUP[k]}: ${r[k]}`).join('\n')}</title>${keys.map(k => { const v = r[k]; const h = ih * v / mx; const yy = y(acc + v); acc += v; return v ? `<rect x="${L + i * bw + 3}" y="${yy}" width="${Math.max(1, bw - 6)}" height="${h}" fill="${col[k]}"/>` : ''; }).join('')}</g>`; }).join('')}
    ${rows.map((r, i) => i % 2 === 0 ? `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${new Date(r.m + 'T12:00:00').toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' })}</text>` : '').join('')}
  </svg></div><div class="legend">${keys.map(k => `<span><i style="background:${col[k]}"></i>${NGROUP[k]}</span>`).join('')}</div>`;
}

function vNews() {
  if (!NW || !NW.items.length) return `<section><div class="band"><div><p class="eyebrow">Lokaal Nieuws</p><h2>Nog geen <span class="hl">nieuws.</span></h2><p>Het nieuws komt mee in de wekelijkse run: Google Alerts en Google Nieuws voor de markt, de concurrenten, GaragePark zelf en de plaatsen met een park.</p></div><div><p class="eyebrow">Wat er nodig is</p><p>Run de workflow met mode <b>full</b> of <b>news</b>.</p></div></div></section>`;
  const base = nItems(true), list = nItems(false);
  const cnt = g => base.filter(x => x.g === g).length;
  const fresh = NW.runs.length > 1 ? NW.items.filter(x => x.fs === NW.last).length : null;
  const brands = new Map(); base.forEach(x => x.b.filter(b => b !== OWNB).forEach(b => brands.set(b, (brands.get(b) || 0) + 1)));
  const bl = [...brands].sort((a, b) => b[1] - a[1]);
  const topics = [...new Set(NW.items.map(x => x.tp))].sort();
  const per = { 30: 'laatste 30 dagen', 90: 'laatste 90 dagen', all: 'alles' }[nst.per];
  const shown = nst.all ? list : list.slice(0, 30);
  return `<section><div class="grid g4">
    <div class="card kpi"><span class="stat-l">Nieuw sinds vorige meting</span><span class="num">${fresh == null ? base.length : fresh}</span><span class="of">${fresh == null ? 'eerste meting, ' + per : 'berichten gevonden op ' + fmtDate(NW.last)}</span></div>
    <div class="card kpi"><span class="stat-l">Concurrenten in het nieuws</span><span class="num">${cnt('concurrent')}</span><span class="of">${bl.length ? bl.slice(0, 3).map(([b, n]) => esc(b) + ' ' + n).join(', ') : 'geen'}, ${per}</span></div>
    <div class="card kpi"><span class="stat-l">GaragePark in het nieuws</span><span class="num">${cnt('eigen')}</span><span class="of">${per}</span></div>
    <div class="card kpi"><span class="stat-l">Lokaal en markt</span><span class="num">${cnt('lokaal') + cnt('markt')}</span><span class="of">${cnt('lokaal')} lokaal, ${cnt('markt')} markt</span></div>
  </div></section>
  <section><div class="grid g21 gtop">
    <div class="card"><h4>Berichten per week</h4><p class="cap">Laatste 12 weken, op publicatiedatum${st.city ? ', ' + esc(st.city) : ''}</p>${nWeeks(nItems(true).filter(x => daysAgo(x.dt) <= 90))}</div>
    <div class="tcard"><div class="thead"><div><h4>Concurrenten</h4><p>Berichten per merk, ${per}.</p></div></div>
      <div class="scroll"><table><tbody>${bl.length ? bl.map(([b, n]) => `<tr class="click" data-nbrand="${esc(b)}" tabindex="0"><td>${esc(b)}</td><td class="r"><b>${n}</b></td></tr>`).join('') : '<tr><td class="of">Geen concurrentnieuws in deze periode.</td></tr>'}</tbody></table></div></div>
  </div></section>
  <section><div class="tcard"><div class="thead"><div><h4>Berichten</h4><p>Alleen nieuws over de Nederlandse opslagmarkt: geen blogs, kennisbanken of beurskoersen. Klik op een titel om het artikel te openen.</p></div>
    <div class="filters">${seg('ng', [['all', 'Alles'], ['concurrent', 'Concurrenten'], ['eigen', 'GaragePark'], ['lokaal', 'Lokaal'], ['markt', 'Markt']], nst.g)}
      ${seg('np', [['30', '30 dagen'], ['90', '90 dagen'], ['all', 'Alles']], nst.per)}
      <select id="ntp" aria-label="Onderwerp"><option value="all">Alle onderwerpen</option>${topics.map(t => `<option ${nst.tp === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
      <input type="search" id="nq" placeholder="Zoek in titel of bron…" value="${esc(nst.q)}" aria-label="Zoek nieuws"></div></div>
    <div class="nlist">${shown.length ? shown.map(nCard).join('') : '<div class="empty">Geen berichten met deze filters.</div>'}</div>
    <div class="tfoot"><span>${shown.length} van ${list.length} berichten</span>${list.length > 30 ? `<button id="nall">${nst.all ? 'Toon minder' : 'Toon alle ' + list.length}</button>` : ''}</div></div></section>`;
}

function nSeg(k, v) {
  if (k === 'ng') nst.g = v; else if (k === 'np') nst.per = v; else return false;
  nst.all = false; render(); return true;
}

function newsEvents() {
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-nbrand],#nall');
    if (!el) return;
    if (el.id === 'nall') { nst.all = !nst.all; render(); return; }
    nst.g = 'concurrent'; nst.q = el.dataset.nbrand; render();
  });
  document.addEventListener('input', ev => { if (ev.target.id === 'nq') { nst.q = ev.target.value; render(); const i = $('#nq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } });
  document.addEventListener('change', ev => { if (ev.target.id === 'ntp') { nst.tp = ev.target.value; render(); } });
}
