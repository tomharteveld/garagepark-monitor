// ==== Aggregators ====
const AG = D.agg;
const ast = { sort: ['sessions', -1] };
const ACOLS = ['var(--brand)', 'var(--accent)', 'var(--heat-3)', 'var(--accent-soft)', 'var(--heat-1)', 'var(--heat-2)', 'var(--subtle)', 'var(--line)'];

function aDelta(c, p) { if (!p) return ''; const d = (c - p) / p; return `<span class="pill2 ${d >= 0 ? 'ok' : 'bad'}">${d >= 0 ? '+' : ''}${nf(100 * d, 0)}%</span>`; }

function aMonthly() {
  const ms = AG.months, keys = AG.aggregators.slice(0, 7);
  const rows = ms.map((m, i) => { const r = { m }; keys.forEach(a => r[a] = (AG.monthly[a] || [])[i] ? AG.monthly[a][i][0] : 0); return r; });
  const W = 700, H = 220, L = 46, B = 26, T = 8, iw = W - L - 8, ih = H - T - B, bw = iw / rows.length;
  const tot = r => keys.reduce((s, k) => s + r[k], 0), mx = Math.max(1, ...rows.map(tot)), y = v => T + ih * (1 - v / mx);
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Bezoeken via aggregators per maand">
    ${[0, mx / 2, mx].map(t => `<line class="gl" x1="${L}" x2="${W - 8}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${Math.round(t).toLocaleString('nl-NL')}</text>`).join('')}
    ${rows.map((r, i) => { let acc = 0; const part = i === rows.length - 1; return `<g><title>${r.m}${part ? ' (lopende maand)' : ''}\n${keys.map(k => `${k}: ${r[k].toLocaleString('nl-NL')}`).join('\n')}</title>${keys.map((k, j) => { const v = r[k], h = ih * v / mx, yy = y(acc + v); acc += v; return v ? `<rect x="${L + i * bw + 3}" y="${yy}" width="${Math.max(1, bw - 6)}" height="${h}" fill="${ACOLS[j]}" opacity="${part ? .4 : 1}"/>` : ''; }).join('')}</g>`; }).join('')}
    ${rows.map((r, i) => i % 2 === 0 || i === rows.length - 1 ? `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${new Date(r.m + '-15').toLocaleDateString('nl-NL', { month: 'short', year: '2-digit' })}</text>` : '').join('')}
  </svg></div><div class="legend">${keys.map((k, j) => `<span><i style="background:${ACOLS[j]}"></i>${esc(k)}</span>`).join('')}<span class="of">Lichte balk: lopende maand</span></div>`;
}

function vAgg() {
  if (!AG) return `<section><div class="band"><div><p class="eyebrow">Aggregators</p><h2>Nog geen <span class="hl">data.</span></h2><p>Hier komt wat platforms als huren.nl, Marktplaats, Funda en Opslagmarkt opleveren: bezoeken, leads en conversie per platform en per park, uit Google Analytics.</p></div>
    <div><p class="eyebrow">Wat er nodig is</p><p>Het service-account van de monitor als <b>Lezer</b> op de GA4-property <b>garagepark.nl - GA4</b> en de <b>Google Analytics Data API</b> aan in het Cloud-project. Daarna komt de data wekelijks mee (zie README, Aggregators).</p></div></div></section>`;
  const W = AG.window, site = (W['(site)'] || {}).cur || {}, siteP = (W['(site)'] || {}).prev || {};
  const sum = (k, w = 'cur') => AG.aggregators.reduce((s, a) => s + ((W[a] || {})[w] || {})[k] || 0, 0);
  const s = sum('sessions'), sp = sum('sessions', 'prev'), l = sum('leads'), lp = sum('leads', 'prev');
  const conv = s ? l / s : null, sconv = site.sessions ? site.leads / site.sessions : null;
  const rows = AG.aggregators.map(a => { const c = (W[a] || {}).cur || {}, p = (W[a] || {}).prev || {}; return { a, c, p, conv: c.sessions ? c.leads / c.sessions : null, eng: c.sessions ? c.engaged / c.sessions : null, y: (AG.monthly[a] || []).slice(-13, -1).reduce((t, x) => t + x[0], 0) }; });
  const [k, d] = ast.sort, gv = r => k === 'a' ? r.a : k === 'conv' ? (r.conv ?? -1) : k === 'eng' ? (r.eng ?? -1) : k === 'y' ? r.y : (r.c[k] || 0);
  rows.sort((x, y) => d * ((gv(x) > gv(y)) - (gv(x) < gv(y))));
  const th = (key, l, r) => `<th class="${r ? 'r' : ''}" data-asort="${key}">${l} ${k === key ? (d < 0 ? '↓' : '↑') : '↕'}</th>`;
  const parks = new Map(); AG.parks.forEach(x => { const p = parks.get(x.p) || { p: x.p, t: 0, by: {} }; p.t += x.sessions; p.by[x.a] = (p.by[x.a] || 0) + x.sessions; p.l = (p.l || 0) + x.leads; parks.set(x.p, p); });
  const pl = [...parks.values()].sort((a, b) => b.t - a.t).slice(0, 25), top = AG.aggregators.slice(0, 4);
  const best = rows.slice().sort((a, b) => (b.c.sessions || 0) - (a.c.sessions || 0))[0];
  return `<section><div class="grid g4">
    <div class="card kpi"><span class="stat-l">Bezoeken via aggregators</span><span class="num">${s.toLocaleString('nl-NL')}</span><span class="of">laatste 28 dagen</span><div class="vs"><span>vs vorige 28 dagen</span>${aDelta(s, sp)}</div></div>
    <div class="card kpi"><span class="stat-l">Aandeel van alle bezoeken</span><span class="num">${pc(site.sessions ? s / site.sessions : null)}</span><span class="of">${(site.sessions || 0).toLocaleString('nl-NL')} bezoeken op garagepark.nl</span></div>
    <div class="card kpi"><span class="stat-l">Leads via aggregators</span><span class="num">${l}</span><span class="of">${AG.lead_events.length} soorten leads (huur, afspraak, bezichtiging, contact, brochure)</span><div class="vs"><span>vorige 28 dagen ${lp}</span></div></div>
    <div class="card kpi"><span class="stat-l">Conversie</span><span class="num">${pc(conv)}</span><span class="of">site gemiddeld ${pc(sconv)}</span></div>
  </div></section>
  ${best && best.c.sessions ? `<section><div class="note"><b>${esc(best.a)}</b> brengt de meeste bezoekers (${best.c.sessions.toLocaleString('nl-NL')} in 28 dagen) met een conversie van ${pc(best.conv)}${sconv != null && best.conv != null ? (best.conv < sconv / 2 ? ', ruim onder het sitegemiddelde: kijk naar de landingspagina of de prijsvermelding op het platform' : best.conv > sconv ? ', boven het sitegemiddelde' : '') : ''}.</div></section>` : ''}
  <section><div class="card"><h4>Bezoeken per maand</h4><p class="cap">Per platform, laatste 13 maanden</p>${aMonthly()}</div></section>
  <section><div class="tcard"><div class="thead"><div><h4>Per platform</h4><p>Laatste 28 dagen tot en met ${fmtDate(AG.end)}; jaar = laatste 12 volle maanden.</p></div></div>
    <div class="scroll"><table><thead><tr>${th('a', 'Platform')}${th('sessions', 'Bezoeken', 1)}<th class="r">vs vorige</th>${th('eng', 'Betrokken', 1)}${th('leads', 'Leads', 1)}${th('purchases', 'Huur', 1)}${th('conv', 'Conversie', 1)}${th('y', 'Bezoeken jaar', 1)}</tr></thead><tbody>
    ${rows.map(r => `<tr><td><b>${esc(r.a)}</b></td><td class="r">${(r.c.sessions || 0).toLocaleString('nl-NL')}</td><td class="r">${aDelta(r.c.sessions || 0, r.p.sessions || 0) || 'n.v.t.'}</td><td class="r">${pc(r.eng)}</td><td class="r">${r.c.leads || 0}</td><td class="r">${r.c.purchases || 0}</td><td class="r">${pc(r.conv)}</td><td class="r">${r.y.toLocaleString('nl-NL')}</td></tr>`).join('')}
    </tbody></table></div></div></section>
  <section><div class="grid g2 gtop">
    <div class="tcard"><div class="thead"><div><h4>Per park</h4><p>Landingspagina /locaties/…, laatste 90 dagen.</p></div></div>
      <div class="scroll"><table><thead><tr><th>Park</th>${top.map(a => `<th class="r">${esc(a)}</th>`).join('')}<th class="r">Leads</th></tr></thead><tbody>
      ${pl.map(p => `<tr><td>${esc(p.p.replace(/-\d+$/, '').replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()))}</td>${top.map(a => `<td class="r">${(p.by[a] || 0).toLocaleString('nl-NL')}</td>`).join('')}<td class="r">${p.l || 0}</td></tr>`).join('')}</tbody></table></div></div>
    <div class="tcard"><div class="thead"><div><h4>Bron en medium</h4><p>Betaald (cpc, advertentie) of verwijzing, laatste 90 dagen.</p></div></div>
      <div class="scroll"><table><thead><tr><th>Platform</th><th>Bron / medium</th><th class="r">Bezoeken</th><th class="r">Leads</th></tr></thead><tbody>
      ${AG.sources.map(x => `<tr><td>${esc(x.a)}</td><td class="of">${esc(x.s)} / ${esc(x.m)}</td><td class="r">${x.sessions.toLocaleString('nl-NL')}</td><td class="r">${x.leads}</td></tr>`).join('')}</tbody></table></div></div>
  </div></section>`;
}

function aggEvents() {
  document.addEventListener('click', ev => {
    const el = ev.target.closest('[data-asort]'); if (!el) return;
    const k = el.dataset.asort; ast.sort = ast.sort[0] === k ? [k, -ast.sort[1]] : [k, k === 'a' ? 1 : -1]; render();
  });
}
