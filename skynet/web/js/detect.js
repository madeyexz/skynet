// Skynet Detect: "Space layers" panel, vessel card, mismatch moments. Owner: D.
// Relies on ctx.detMap from map.js (layer toggles, live counts, per-vessel state at the clock time).

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SOURCE = { ais: 'AIS', sar: 'Satellite radar', rf: 'RF', registry: 'Registry', sanctions: 'Sanctions', fusion: 'Skynet fusion', operator: 'Operator' };
const STATE = {
  verified: { label: 'Verified', chip: 'blue', icon: 'circle-check' },
  dark: { label: 'Dark: radar only', chip: 'orange', icon: 'eye-off' },
  spoofing: { label: 'False position', chip: 'red', icon: 'map-pin-off' },
};
const LAYERS = [
  { key: 'claimed', title: 'Claimed', sub: 'AIS broadcasts', count: (c) => c.ais },
  { key: 'observed', title: 'Observed', sub: 'Satellite radar (SAR)', count: (c) => c.sar },
  { key: 'emitted', title: 'Emitted', sub: 'RF (radio/radar emissions)', count: (c) => c.rf },
];

function bisectT(arr, t, key) {
  let lo = 0, hi = arr.length - 1;
  if (!arr.length || key(arr[0]) > t) return -1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (key(arr[mid]) <= t) lo = mid; else hi = mid - 1; }
  return lo;
}
const lastAt = (arr, t, key, isStatic) => {
  if (!arr?.length) return null;
  if (isStatic) return arr[arr.length - 1];
  const i = bisectT(arr, t, key);
  return i >= 0 ? arr[i] : null;
};
function km(a, b) {
  const R = 6371, toR = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toR, dLon = (b[0] - a[0]) * toR;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toR) * Math.cos(b[1] * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function distToLineKm(p, coords) {
  const kx = 111.32 * Math.cos(p[1] * Math.PI / 180), ky = 110.57;
  let best = Infinity;
  for (let i = 0; i < coords.length - 1; i++) {
    const ax = (coords[i][0] - p[0]) * kx, ay = (coords[i][1] - p[1]) * ky;
    const bx = (coords[i + 1][0] - p[0]) * kx, by = (coords[i + 1][1] - p[1]) * ky;
    const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
    const f = L ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / L)) : 0;
    best = Math.min(best, Math.hypot(ax + f * dx, ay + f * dy));
  }
  return best;
}
const lines = (g) => (!g ? [] : g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : []);

export function init(ctx) {
  const root = document.getElementById('detect');
  if (!root) return;
  root.innerHTML = `
    <aside class="det-panel" aria-label="Space layers">
      <div class="det-panel-head">
        <span class="det-panel-title"><i data-lucide="layers"></i>Space layers</span>
        <span class="det-panel-time" id="det-panel-time"></span>
      </div>
      <ul class="det-rows" role="list">
        ${LAYERS.map((l) => `
        <li><button type="button" class="det-row" data-layer="${l.key}" aria-pressed="true">
          <span class="det-sw det-sw--${l.key}" aria-hidden="true"></span>
          <span class="det-row-text"><span class="det-row-title">${l.title}</span><span class="det-row-sub">${l.sub}</span></span>
          <span class="det-row-count" data-count="${l.key}">0</span>
          <span class="det-switch" aria-hidden="true"></span>
        </button></li>`).join('')}
      </ul>
      <div class="det-mismatch" id="det-mismatch">
        <span class="det-sw det-sw--mismatch" aria-hidden="true"></span>
        <span class="det-row-text"><span class="det-row-title">Mismatches</span><span class="det-row-sub" id="det-mismatch-sub">None right now</span></span>
        <span class="det-row-count" id="det-mismatch-count">0</span>
      </div>
      <div class="det-panel-foot" id="det-panel-foot"></div>
    </aside>
    <article id="vessel-card" class="card" hidden aria-live="polite"></article>`;
  const panel = root.querySelector('.det-panel');
  const card = root.querySelector('#vessel-card');
  let selected = null, cardKey = '';
  let lastToastAt = 0;

  const det = () => ctx.detMap;
  const world = () => ctx.world || {};
  const isStatic = () => world().scenario?.time_mode === 'static';
  const icons = () => window.lucide?.createIcons();

  // ---------- mode ----------
  const applyMode = (m) => { root.style.display = (m || ctx.mode) === 'detect' ? '' : 'none'; };
  ctx.bus.on('mode', ({ mode }) => applyMode(mode));
  applyMode(ctx.mode);

  // ---------- layer toggles ----------
  panel.querySelectorAll('.det-row').forEach((b) => b.addEventListener('click', () => {
    const on = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', String(on));
    det()?.setLayer(b.dataset.layer, on);
  }));

  // ---------- panel footer: case chip + sourced facts ----------
  function renderFoot() {
    const sc = world().scenario || {};
    const foot = panel.querySelector('#det-panel-foot');
    const real = sc.illustrative === false;
    const facts = (sc.facts || []).slice(0, 4).map((f) =>
      `${esc(f.label)} <span class="w500">${esc(f.value)}</span>${f.source ? ` <span class="det-src">(${esc(f.source)})</span>` : ''}`).join(' · ');
    foot.innerHTML = sc.label ? `
      <div class="det-case"><span class="chip ${real ? 'green' : ''}">${real ? 'Real data' : 'Illustrative'}</span><span class="det-case-label">${esc(sc.label)}</span></div>
      ${facts ? `<p class="det-facts">${facts}</p>` : ''}` : '';
    foot.hidden = !sc.label;
  }

  // ---------- live counts ----------
  const shown = {};
  const setText = (el, v) => { if (el && shown[el.id || el.dataset.count] !== v) { shown[el.id || el.dataset.count] = v; el.textContent = v; } };
  function renderCounts(c) {
    for (const l of LAYERS) setText(panel.querySelector(`[data-count="${l.key}"]`), String(l.count(c) ?? 0));
    const ids = c.mismatchIds || [];
    setText(panel.querySelector('#det-mismatch-count'), String(ids.length));
    const word = (id) => (det()?.stateOf(id) === 'spoofing' ? 'false position' : 'dark');
    const sub = !ids.length ? 'None right now'
      : ids.length <= 2 ? ids.map((id) => `${id} ${word(id)}`).join(' · ')
        : [c.dark ? `${c.dark} dark` : '', c.spoof ? `${c.spoof} false position` : ''].filter(Boolean).join(' · ');
    setText(panel.querySelector('#det-mismatch-sub'), sub);
    panel.querySelector('#det-mismatch').classList.toggle('is-active', ids.length > 0);
    setText(panel.querySelector('#det-panel-time'), ctx.clock?.t ? `${ctx.fmtTime(ctx.clock.t)} UTC` : '');
  }

  // ---------- mismatch moment ----------
  function overAsset(v, lonlat, t) {
    const ev = (world().events || []).find((e) => e.vessel_id === v.id && e.asset_id && Math.abs(e.t - t) < 3 * 3600);
    const assets = world().assets || [];
    if (ev) { const a = assets.find((x) => x.id === ev.asset_id); if (a) return a.alias || a.name; }
    if (!lonlat) return null;
    let best = null, bd = 5;
    for (const a of assets) for (const ln of lines(a.geometry)) { const d = distToLineKm(lonlat, ln); if (d < bd) { bd = d; best = a; } }
    return best ? best.alias || best.name : null;
  }
  function mismatchMoments(prevT, t) {
    if (prevT == null || t <= prevT || t - prevT > 15 * 60 || isStatic()) return;
    for (const v of world().vessels || []) {
      for (const s of v.states || []) {
        if (s.start <= prevT || s.start > t || (s.state !== 'dark' && s.state !== 'spoofing')) continue;
        det()?.flash(v.id);
        const p = det()?.positionOf(v.id);
        const now = performance.now();
        if (now - lastToastAt < 3500) continue;
        lastToastAt = now;
        let text;
        if (s.state === 'dark') {
          const over = overAsset(v, p && [p.lon, p.lat], t);
          text = `${v.id} went dark${over ? ` over ${over}` : ''} · radar still sees the hull`;
        } else {
          const d = p?.claimed ? km([p.claimed.lon, p.claimed.lat], [p.lon, p.lat]) : null;
          text = `${v.id} is broadcasting a false position${d ? ` · radar sees it ${d.toFixed(d < 10 ? 1 : 0)} km away` : ' · radar sees it elsewhere'}`;
        }
        ctx.bus.emit('toast', { text });
      }
    }
  }

  // ---------- vessel card ----------
  function evidence(v, t) {
    const st = isStatic();
    const ais = lastAt(v.claimed, t, (x) => x[0], st);
    const sar = lastAt([...(v.observed || [])].sort((a, b) => a.t - b.t), t, (x) => x.t, st);
    const rf = lastAt([...(v.emitted || [])].sort((a, b) => a.t - b.t), t, (x) => x.t, st);
    return [
      { icon: 'radio', label: 'Claimed · AIS', value: ais ? `Last ${ctx.fmtTime(ais[0])}` : 'No AIS received', off: !ais },
      { icon: 'satellite', label: 'Observed · Radar', value: sar ? `${ctx.fmtTime(sar.t)}${sar.length_m ? ` · ${Math.round(sar.length_m)} m hull` : ''}` : 'No radar pass yet', off: !sar },
      { icon: 'radio-tower', label: 'Emitted · RF', value: rf ? `${ctx.fmtTime(rf.t)}${rf.band ? ` · ${rf.band}` : ''}` : 'No emissions', off: !rf },
    ];
  }
  function renderCard(force = false) {
    const v = selected && ctx.vessel(selected);
    if (!v) { card.hidden = true; cardKey = ''; return; }
    const t = ctx.clock?.t ?? 0;
    const state = det()?.stateOf(v.id) || (v.states?.length ? null : null);
    const ev = evidence(v, t);
    const key = `${v.id}|${state}|${ev.map((e) => e.value).join('|')}|${v.risk?.score}`;
    if (!force && key === cardKey) return;
    cardKey = key;
    const S = STATE[state];
    const risk = v.risk || {};
    const reasons = [...(risk.reasons || [])].sort((a, b) => (b.points || 0) - (a.points || 0)).slice(0, 4);
    const meta = [v.id, v.flag, v.type, v.length_m ? `${Math.round(v.length_m)} m` : null].filter(Boolean).map(esc).join(' · ');
    card.innerHTML = `
      <header class="vc-head">
        <span class="vc-icon vc-icon--${state || 'none'}" aria-hidden="true"><i data-lucide="ship"></i></span>
        <div class="vc-title">
          <h2 class="vc-name">${esc(v.name || v.id)}</h2>
          <p class="vc-meta">${meta}</p>
        </div>
        <button type="button" class="vc-close" aria-label="Close vessel card"><i data-lucide="x"></i></button>
      </header>
      <div class="vc-state">
        ${S ? `<span class="chip ${S.chip}"><i data-lucide="${S.icon}"></i>${S.label}</span>` : '<span class="chip">Not in view now</span>'}
        ${v.illustrative ? '<span class="vc-note">Illustrative vessel</span>' : ''}
      </div>
      ${risk.score != null ? `
      <section class="vc-risk" aria-label="Risk">
        <div class="vc-score"><span class="vc-score-n">${Math.round(risk.score)}</span><span class="vc-score-of">/100</span>
          <span class="chip ${esc(risk.level || 'low')}">${esc((risk.level || 'low').replace(/^./, (c) => c.toUpperCase()))} risk</span></div>
        ${reasons.length ? `<ul class="vc-reasons" role="list">${reasons.map((r) => `
          <li><span class="vc-reason">${esc(r.text)}</span><span class="vc-src">${esc(SOURCE[r.source] || r.source || '')}</span></li>`).join('')}</ul>` : ''}
      </section>` : ''}
      <section class="vc-evidence" aria-label="Evidence from the three layers">
        ${ev.map((e) => `<div class="vc-ev${e.off ? ' is-off' : ''}"><i data-lucide="${e.icon}"></i><span class="vc-ev-label">${e.label}</span><span class="vc-ev-value">${esc(e.value)}</span></div>`).join('')}
      </section>
      <footer class="vc-actions">
        <button type="button" class="btn primary" data-act="report"><i data-lucide="file-text"></i>Open in Report</button>
        <button type="button" class="btn" data-act="watch"><i data-lucide="eye"></i>Watch this vessel</button>
      </footer>`;
    card.hidden = false;
    card.dataset.state = state || 'none';
    card.querySelector('.vc-close').onclick = closeCard;
    card.querySelector('[data-act="report"]').onclick = () => {
      ctx.bus.emit('open-report', { vessel_id: v.id, template: 'cable' });
      if (ctx.mode !== 'report') ctx.setMode('report');
    };
    card.querySelector('[data-act="watch"]').onclick = () => ctx.bus.emit('open-zones', { vessel_id: v.id });
    icons();
  }
  function closeCard() {
    selected = null; card.hidden = true; cardKey = '';
    ctx.bus.emit('deselect-vessel', {});
  }

  ctx.bus.on('select-vessel', ({ id }) => { selected = id || null; renderCard(true); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !card.hidden && ctx.mode === 'detect') closeCard(); });

  ctx.bus.on('world', () => {
    renderFoot();
    if (selected && !ctx.vessel(selected)) closeCard(); else renderCard(true);
  });

  const hook = () => det()?.onUpdate((info) => {
    if (info.counts) renderCounts(info.counts);
    mismatchMoments(info.prevT, info.t);
    if (selected) renderCard();
  });
  if (!hook()) console.warn('[skynet] detect.js: ctx.detMap missing; layer panel will not update');
  renderFoot();
  icons();
}
