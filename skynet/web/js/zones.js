// Watch zones: map outlines, drawing toolbar, 4-step wizard and zone summary sheet. Owner: G.
// Mount: #zones. Map layers: zn-*. Talks to /api/presets, /api/zones (POST/PUT/DELETE), /api/zones/{id}/summary.

const PURPOSES = [
  { id: 'cable', label: 'Cable / pipeline protection', icon: 'cable', desc: 'Watch a corridor over a cable or pipeline for dark, slow or anchor-dragging ships.' },
  { id: 'sanctions', label: 'Sanctions screening', icon: 'shield-alert', desc: 'Flag dark periods, ship-to-ship meetings, identity changes and spoofing.' },
  { id: 'insurance', label: 'Insurance / voyage risk', icon: 'umbrella', desc: 'Score a voyage for listed areas, jamming and incidents near the route.' },
  { id: 'custom', label: 'Custom', icon: 'sliders-horizontal', desc: 'Start empty and build your own rules, metrics and recipients.' },
];
const PURPOSE = Object.fromEntries(PURPOSES.map((p) => [p.id, p]));
const SEVS = ['low', 'medium', 'high'];
const SEV_LABEL = { low: 'Low', medium: 'Medium', high: 'High' };
const WINDOWS = ['1h', '6h', '24h', '30d'];
const WIN_LABEL = { '1h': '1 h', '6h': '6 h', '24h': '24 h', '30d': '30 d' };
const SIGNALS = {
  speed_below_kn: { label: 'Speed below', phrase: (p) => `vessels with speed < ${p} kn`, param: true, unit: 'kn' },
  dark_minutes: { label: 'Dark minutes', phrase: () => 'dark minutes' },
  risk_score: { label: 'Risk score', phrase: () => 'risk score' },
  distance_to_asset_km: { label: 'Distance to asset', phrase: () => 'distance to asset (km)' },
  in_zone: { label: 'Vessels in zone', phrase: () => 'vessels in zone' },
};
const AGGS = ['count', 'sum', 'max', 'avg'];
const CHANNELS = [['in_app', 'In-app'], ['push', 'Push'], ['email', 'Email'], ['webhook', 'Webhook']];
const CHANNEL_HINT = { in_app: 'Shown in the Skynet feed', push: 'Phone or device', email: 'name@example.com', webhook: 'https://…' };
const QUIET = ['', '22:00-06:00', '23:00-07:00', '20:00-08:00'];
const STEPS = ['Purpose', 'Rules', 'Metrics', 'Recipients'];

const P = (key, label, value, unit) => ({ key, label, value, unit });
const R = (id, label, severity, params = [], window = null) => ({ id, label, enabled: true, severity, window, params });
const M = (id, label, unit = '') => ({ id, label, enabled: true, unit });
// Used only if GET /api/presets is unavailable; mirrors the rule and metric ids in CONTRACT.md.
const FALLBACK_PRESETS = {
  cable: {
    label: PURPOSE.cable.label, description: PURPOSE.cable.desc,
    rules: [
      R('dark_in_zone', 'Tracker switched off inside zone', 'high', [P('min_minutes', 'Longer than', 20, 'min')]),
      R('slow_speed', 'Slow speed inside zone', 'medium', [P('min_kn', 'Slower than', 3, 'kn'), P('min_minutes', 'For longer than', 30, 'min')]),
      R('anchor_drag_pattern', 'Anchor-drag pattern over the asset', 'high'),
      R('no_anchor_area', 'Anchoring in a no-anchor area', 'high'),
      R('loitering', 'Loitering near the asset', 'medium', [P('minutes', 'Longer than', 60, 'min'), P('km', 'Within', 2, 'km')]),
      R('false_position', 'False AIS position', 'high'),
    ],
    metrics: [M('vessels_in_zone', 'Vessels currently in zone', 'vessels'), M('dark_minutes_in_zone', 'Dark minutes in zone', 'min'),
      M('closest_approach_to_asset', 'Closest approach to asset', 'km'), M('time_over_asset', 'Time over asset', 'min'),
      M('highest_risk_score', 'Highest risk score'), M('alert_to_ack_time', 'Alert to acknowledgement', 'min')],
  },
  sanctions: {
    label: PURPOSE.sanctions.label, description: PURPOSE.sanctions.desc,
    rules: [
      R('dark_period', 'Dark period', 'high', [P('hours', 'Longer than', 6, 'h')]),
      R('ship_to_ship_meeting', 'Ship-to-ship meeting', 'medium'),
      R('identity_change', 'Identity change (name, flag, MMSI)', 'medium'),
      R('spoofing', 'Spoofed AIS position', 'high'),
      R('flagged_port_call', 'Call at a flagged port', 'medium'),
      R('designated_owner', 'Designated owner or manager', 'high'),
    ],
    metrics: [M('dark_hours_30d', 'Dark hours, last 30 days', 'h'), M('sts_meeting_count', 'Ship-to-ship meetings'),
      M('identity_changes', 'Identity changes'), M('risk_score_trend', 'Risk score trend')],
  },
  insurance: {
    label: PURPOSE.insurance.label, description: PURPOSE.insurance.desc,
    rules: [
      R('listed_area_entry', 'Entry into a listed area', 'high'),
      R('jamming_exposure', 'GNSS jamming exposure', 'medium'),
      R('incidents_near_route', 'Incidents near the route', 'medium', [P('km', 'Within', 50, 'km')]),
      R('dark_during_policy', 'Dark during the policy period', 'high'),
    ],
    metrics: [M('voyage_risk_score', 'Voyage risk score'), M('hours_in_high_risk_zones', 'Hours in high-risk zones', 'h'),
      M('insured_value_exposed', 'Insured value exposed', 'USD m'), M('jamming_events_on_route', 'Jamming events on route')],
  },
  custom: { label: 'Custom', description: PURPOSE.custom.desc, rules: [], metrics: [M('vessels_in_zone', 'Vessels currently in zone', 'vessels')] },
};
const RC = (name, channel, address, min_severity, quiet_hours = null) => ({ name, channel, address, min_severity, quiet_hours });
const DEFAULT_RECIPIENTS = {
  cable: [RC('Coast guard duty officer', 'push', '+358 40 000 0112', 'medium'), RC('Grid operator control room', 'email', 'control-room@grid.example', 'high'),
    RC('Skynet feed', 'in_app', '', 'low')],
  sanctions: [RC('Compliance desk', 'email', 'compliance@bank.example', 'medium', '22:00-06:00'), RC('Sanctions analyst on call', 'push', '+44 7700 900123', 'high'),
    RC('Skynet feed', 'in_app', '', 'low')],
  insurance: [RC('Marine underwriter', 'email', 'underwriting@insurer.example', 'medium', '22:00-06:00'), RC('Claims system', 'webhook', 'https://hooks.insurer.example/skynet', 'high'),
    RC('Skynet feed', 'in_app', '', 'low')],
  custom: [RC('Me', 'in_app', '', 'low')],
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = (n) => `<i data-lucide="${n}" aria-hidden="true"></i>`;
const clone = (o) => JSON.parse(JSON.stringify(o));
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const humanType = (t) => cap(String(t || 'event').replace(/_/g, ' '));
const fmtNum = (v) => (typeof v === 'number' ? (Math.abs(v) >= 100 || Number.isInteger(v) ? Math.round(v).toLocaleString('en-GB') : v.toFixed(1)) : v);

// ---------- geometry (simple local metric projection) ----------
const KY = 110.574;
const kx = (lat) => 111.32 * Math.cos((lat * Math.PI) / 180);
function lineKm(line) {
  let d = 0;
  for (let i = 1; i < line.length; i++) {
    const la = (line[i][1] + line[i - 1][1]) / 2;
    d += Math.hypot((line[i][0] - line[i - 1][0]) * kx(la), (line[i][1] - line[i - 1][1]) * KY);
  }
  return d;
}
function areaKm2(ring) {
  if (ring.length < 3) return 0;
  const lat0 = ring.reduce((s, p) => s + p[1], 0) / ring.length, k = kx(lat0);
  let a = 0;
  for (let i = 0; i < ring.length; i++) { const p = ring[i], q = ring[(i + 1) % ring.length]; a += p[0] * k * q[1] * KY - q[0] * k * p[1] * KY; }
  return Math.abs(a / 2);
}
// Buffer a polyline by halfKm each side with mitred joins and round caps. Returns an open ring [[lon, lat], ...].
function bufferLine(line, halfKm) {
  const pts = line.filter((p, i) => i === 0 || p[0] !== line[i - 1][0] || p[1] !== line[i - 1][1]);
  if (pts.length < 2 || !(halfKm > 0)) return null;
  const lat0 = pts.reduce((s, p) => s + p[1], 0) / pts.length, k = kx(lat0);
  const X = pts.map(([lo, la]) => [lo * k, la * KY]);
  const segN = [];
  for (let i = 0; i < X.length - 1; i++) {
    const dx = X[i + 1][0] - X[i][0], dy = X[i + 1][1] - X[i][1], L = Math.hypot(dx, dy) || 1;
    segN.push([-dy / L, dx / L]);
  }
  const left = [], right = [];
  for (let i = 0; i < X.length; i++) {
    let n, len = halfKm;
    if (i === 0) n = segN[0];
    else if (i === X.length - 1) n = segN[segN.length - 1];
    else {
      const a = segN[i - 1], b = segN[i], m = [a[0] + b[0], a[1] + b[1]], L = Math.hypot(m[0], m[1]);
      if (L < 1e-6) n = b; else { n = [m[0] / L, m[1] / L]; len = Math.min(halfKm * 3, halfKm / Math.max(0.2, n[0] * b[0] + n[1] * b[1])); }
    }
    left.push([X[i][0] + n[0] * len, X[i][1] + n[1] * len]);
    right.push([X[i][0] - n[0] * len, X[i][1] - n[1] * len]);
  }
  const arc = (c, n, out) => {
    const a0 = Math.atan2(n[1], n[0]);
    for (let s = 1; s < 10; s++) { const a = a0 - (Math.PI * s) / 10; out.push([c[0] + Math.cos(a) * halfKm, c[1] + Math.sin(a) * halfKm]); }
  };
  const ring = [...left];
  arc(X[X.length - 1], segN[segN.length - 1], ring);
  ring.push(...right.reverse());
  const n0 = segN[0];
  arc(X[0], [-n0[0], -n0[1]], ring);
  return ring.map(([x, y]) => [x / k, y / KY]);
}
const closeRing = (r) => [...r, r[0]];

export function init(ctx) {
  const root = document.getElementById('zones');
  if (!root) return;
  const map = ctx.map;
  const S = {
    presets: null, selected: null, sheet: null, // sheet: 'wizard' | 'summary' | 'pick' | null
    draw: null, pick: null, wiz: null, summary: null, listOpen: false, lastTick: 0, lastPickToggle: null,
  };

  root.innerHTML = `
    <div class="zn-toolbar" role="toolbar" aria-label="Watch zones"></div>
    <div class="zn-list" hidden></div>
    <aside class="zn-sheet" hidden></aside>`;
  const tb = root.querySelector('.zn-toolbar');
  const listEl = root.querySelector('.zn-list');
  const sheet = root.querySelector('.zn-sheet');
  const icons = () => window.lucide?.createIcons();
  const on = (name, fn) => ctx.bus.on(name, fn);
  const toast = (text) => ctx.bus.emit('toast', { text });
  const zones = () => ctx.world?.zones || [];
  const vessel = (id) => (ctx.world?.vessels || []).find((v) => v.id === id) || null;
  const asset = (id) => (ctx.world?.assets || []).find((a) => a.id === id) || null;
  const isAlert = () => ctx.mode === 'alert' || ctx.mode === 'zones';

  async function loadPresets() {
    try { const p = await ctx.api.get('/api/presets'); if (p && typeof p === 'object' && Object.keys(p).length) S.presets = p; }
    catch (e) { console.warn('[zones] /api/presets unavailable, using built-in presets', e); }
    return S.presets || FALLBACK_PRESETS;
  }
  // Server presets win per purpose; a purpose the server leaves out or leaves empty uses the built-in one.
  const presets = () => {
    const out = {};
    for (const pid of new Set([...Object.keys(FALLBACK_PRESETS), ...Object.keys(S.presets || {})])) {
      const s = S.presets?.[pid], f = FALLBACK_PRESETS[pid];
      out[pid] = s && ((s.rules || []).length || (s.metrics || []).length || !f) ? s : { ...f, ...(s?.description ? { description: s.description } : {}) };
    }
    return out;
  };
  loadPresets();

  // ---------- zone geometry helpers ----------
  function assetLine(a) {
    const g = a?.geometry; if (!g) return null;
    if (g.type === 'LineString') return g.coordinates;
    if (g.type === 'MultiLineString') return g.coordinates.reduce((best, l) => (l.length > (best?.length || 0) ? l : best), null);
    return null;
  }
  function corridorLine(z) { return z.geometry?.line?.length ? z.geometry.line : assetLine(asset(z.asset_id)); }
  function zoneRing(z) {
    const g = z.geometry || {};
    if (g.type === 'polygon') return g.coordinates?.length >= 3 ? g.coordinates : null;
    if (g.type === 'corridor') { const l = corridorLine(z); return l ? bufferLine(l, (g.width_km || 4) / 2) : null; }
    return null;
  }
  function zoneAnchor(z) {
    const g = z.geometry || {};
    if (g.type === 'corridor') { const l = corridorLine(z); return l ? l[Math.floor(l.length / 2)] : null; }
    if (g.type === 'polygon' && g.coordinates?.length) {
      const c = g.coordinates; return [c.reduce((s, p) => s + p[0], 0) / c.length, c.reduce((s, p) => s + p[1], 0) / c.length];
    }
    return null;
  }
  function nearestAsset(geometry) {
    const pts = geometry.type === 'corridor' ? geometry.line : geometry.type === 'polygon' ? geometry.coordinates : null;
    if (!pts?.length) return null;
    let best = null, bestD = 15; // km
    for (const a of ctx.world?.assets || []) {
      const lines = a.geometry?.type === 'MultiLineString' ? a.geometry.coordinates : a.geometry?.type === 'LineString' ? [a.geometry.coordinates] : [];
      for (const l of lines) for (const q of l) for (const p of pts) {
        const d = Math.hypot((p[0] - q[0]) * kx(p[1]), (p[1] - q[1]) * KY);
        if (d < bestD) { bestD = d; best = a; }
      }
    }
    return best;
  }
  function geomText(z) {
    const g = z.geometry || {};
    if (g.type === 'corridor') {
      const l = corridorLine(z), a = asset(z.asset_id);
      return `Corridor · ${fmtNum(g.width_km || 4)} km wide · ${l ? Math.round(lineKm(l)) : '?'} km along ${a ? esc(a.alias || a.name) : 'a drawn line'}`;
    }
    if (g.type === 'polygon') return `Area · ${g.coordinates?.length || 0} points · about ${fmtNum(Math.round(areaKm2(zoneRing(z) || [])))} km²`;
    if (g.type === 'watchlist') {
      const ids = g.vessel_ids || [];
      return `Watchlist · ${ids.length} vessel${ids.length === 1 ? '' : 's'}: ${esc(ids.slice(0, 6).join(', '))}${ids.length > 6 ? '…' : ''}`;
    }
    return 'No geometry';
  }
  const bboxOf = (pts) => [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
  function zoneBbox(z) {
    const r = zoneRing(z);
    if (r) return bboxOf(r);
    const pos = (z.geometry?.vessel_ids || []).map((id) => vessel(id)).filter(Boolean).map((v) => ctx.vesselAt(v, ctx.clock.t)).filter(Boolean).map((p) => [p.lon, p.lat]);
    return pos.length ? bboxOf(pos) : null;
  }
  function fitZone(z) {
    const b = zoneBbox(z); if (!b || !map) return;
    const pad = 0.02;
    map.fitBounds([[b[0] - pad, b[1] - pad], [b[2] + pad, b[3] + pad]], { padding: { top: 140, bottom: 120, left: 80, right: 480 }, duration: 700, maxZoom: 11 });
  }

  // ---------- map layers ----------
  const EMPTY = { type: 'FeatureCollection', features: [] };
  function setData(id, fc) { const s = map?.getSource(id); if (s) s.setData(fc); }
  function setupLayers() {
    if (!map) return;
    const before = (map.getStyle()?.layers || []).find((l) => /^(base|det|al|rw|an)-/.test(l.id))?.id;
    for (const s of ['zn-zones', 'zn-centre', 'zn-labels', 'zn-watch', 'zn-draft']) if (!map.getSource(s)) map.addSource(s, { type: 'geojson', data: EMPTY });
    const add = (layer, b = before) => { if (!map.getLayer(layer.id)) map.addLayer(layer, b); };
    add({ id: 'zn-fill', type: 'fill', source: 'zn-zones', paint: { 'fill-color': '#292929', 'fill-opacity': ['case', ['get', 'sel'], 0.09, 0.04] } });
    add({ id: 'zn-outline', type: 'line', source: 'zn-zones', paint: { 'line-color': '#292929', 'line-width': ['case', ['get', 'sel'], 2.5, 1.5], 'line-opacity': 0.85 } });
    add({ id: 'zn-hit', type: 'line', source: 'zn-zones', paint: { 'line-color': '#000', 'line-width': 14, 'line-opacity': 0 } });
    add({ id: 'zn-centre', type: 'line', source: 'zn-centre', paint: { 'line-color': '#5D5D5D', 'line-width': 1.2, 'line-dasharray': [3, 3], 'line-opacity': 0.8 } });
    add({ id: 'zn-watch', type: 'circle', source: 'zn-watch', paint: { 'circle-radius': 13, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#292929', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': 0.8 } });
    add({ id: 'zn-label', type: 'symbol', source: 'zn-labels',
      layout: { 'text-field': ['get', 'name'], 'text-font': ['Noto Sans Regular'], 'text-size': 12, 'text-offset': [0, -1.2], 'text-allow-overlap': false },
      paint: { 'text-color': '#292929', 'text-halo-color': '#FFFFFF', 'text-halo-width': 1.6, 'text-opacity': 0.9 } });
    // Draft preview sits on top of everything.
    add({ id: 'zn-draft-fill', type: 'fill', source: 'zn-draft', filter: ['==', ['get', 'k'], 'fill'], paint: { 'fill-color': '#1F6FEB', 'fill-opacity': 0.1 } }, undefined);
    add({ id: 'zn-draft-line', type: 'line', source: 'zn-draft', filter: ['==', ['get', 'k'], 'line'], paint: { 'line-color': '#1F6FEB', 'line-width': 2 } }, undefined);
    add({ id: 'zn-draft-centre', type: 'line', source: 'zn-draft', filter: ['==', ['get', 'k'], 'centre'], paint: { 'line-color': '#1F6FEB', 'line-width': 1.5, 'line-dasharray': [3, 2] } }, undefined);
    add({ id: 'zn-draft-ring', type: 'circle', source: 'zn-draft', filter: ['==', ['get', 'k'], 'ring'], paint: { 'circle-radius': 14, 'circle-color': 'rgba(31,111,235,0.08)', 'circle-stroke-color': '#1F6FEB', 'circle-stroke-width': 2 } }, undefined);
    add({ id: 'zn-draft-pts', type: 'circle', source: 'zn-draft', filter: ['==', ['get', 'k'], 'vertex'],
      paint: { 'circle-radius': ['case', ['get', 'first'], 6, 4.5], 'circle-color': '#FFFFFF', 'circle-stroke-color': '#1F6FEB', 'circle-stroke-width': 2 } }, undefined);
    applyModeStyle();
  }
  function applyModeStyle() {
    if (!map?.getLayer('zn-outline')) return;
    const strong = isAlert();
    map.setPaintProperty('zn-outline', 'line-opacity', strong ? 0.85 : 0.35);
    map.setPaintProperty('zn-fill', 'fill-opacity', strong ? ['case', ['get', 'sel'], 0.09, 0.04] : ['case', ['get', 'sel'], 0.06, 0.015]);
    map.setPaintProperty('zn-centre', 'line-opacity', strong ? 0.8 : 0.3);
    map.setPaintProperty('zn-label', 'text-opacity', strong ? 0.9 : 0.5);
    map.setPaintProperty('zn-watch', 'circle-stroke-opacity', strong ? 0.8 : 0.3);
  }
  function renderZones() {
    if (!map) return;
    const polys = [], centres = [], labels = [];
    const hide = S.wiz?.editingId;
    for (const z of zones()) {
      if (z.id === hide && S.sheet === 'wizard') continue;
      const sel = z.id === S.selected, ring = zoneRing(z), props = { id: z.id, name: z.name || z.id, sel };
      if (ring) polys.push({ type: 'Feature', properties: props, geometry: { type: 'Polygon', coordinates: [closeRing(ring)] } });
      if (z.geometry?.type === 'corridor') { const l = corridorLine(z); if (l) centres.push({ type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: l } }); }
      const a = zoneAnchor(z);
      if (a) labels.push({ type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: a } });
    }
    setData('zn-zones', { type: 'FeatureCollection', features: polys });
    setData('zn-centre', { type: 'FeatureCollection', features: centres });
    setData('zn-labels', { type: 'FeatureCollection', features: labels });
    renderWatch(true);
  }
  function vesselPoint(id) {
    const v = vessel(id); if (!v) return null;
    const p = ctx.vesselAt(v, ctx.clock.t); return p ? [p.lon, p.lat] : null;
  }
  function renderWatch(force) {
    if (!map) return;
    const now = performance.now();
    if (!force && now - S.lastTick < 400) return;
    S.lastTick = now;
    const feats = [];
    for (const z of zones()) {
      if (z.geometry?.type !== 'watchlist') continue;
      for (const id of z.geometry.vessel_ids || []) { const c = vesselPoint(id); if (c) feats.push({ type: 'Feature', properties: { id: z.id, vessel_id: id }, geometry: { type: 'Point', coordinates: c } }); }
    }
    setData('zn-watch', { type: 'FeatureCollection', features: feats });
    if (S.pick || (S.sheet === 'wizard' && S.wiz?.draft.geometry?.type === 'watchlist')) renderDraft();
  }
  function renderDraft() {
    if (!map) return;
    const F = (k, geometry, extra = {}) => ({ type: 'Feature', properties: { k, ...extra }, geometry });
    const feats = [];
    const shape = (geometry) => {
      if (geometry.type === 'corridor' && geometry.line?.length >= 2) {
        const ring = bufferLine(geometry.line, (geometry.width_km || 4) / 2);
        if (ring) { feats.push(F('fill', { type: 'Polygon', coordinates: [closeRing(ring)] })); feats.push(F('line', { type: 'LineString', coordinates: closeRing(ring) })); }
        feats.push(F('centre', { type: 'LineString', coordinates: geometry.line }));
      } else if (geometry.type === 'polygon' && geometry.coordinates?.length >= 3) {
        feats.push(F('fill', { type: 'Polygon', coordinates: [closeRing(geometry.coordinates)] }));
        feats.push(F('line', { type: 'LineString', coordinates: closeRing(geometry.coordinates) }));
      } else if (geometry.type === 'watchlist') {
        for (const id of geometry.vessel_ids || []) { const c = vesselPoint(id); if (c) feats.push(F('ring', { type: 'Point', coordinates: c })); }
      }
    };
    const d = S.draw;
    if (d) {
      const pts = d.points;
      if (d.phase === 'width') shape({ type: 'corridor', line: pts, width_km: d.width_km });
      else {
        const path = d.cursor ? [...pts, d.cursor] : pts;
        if (d.kind === 'polygon' && path.length >= 3) feats.push(F('fill', { type: 'Polygon', coordinates: [closeRing(path)] }));
        if (path.length >= 2) feats.push(F(d.kind === 'corridor' ? 'centre' : 'line', { type: 'LineString', coordinates: d.kind === 'polygon' && pts.length >= 3 ? closeRing(path) : path }));
        pts.forEach((p, i) => feats.push(F('vertex', { type: 'Point', coordinates: p }, { first: i === 0 })));
      }
    } else if (S.pick) shape({ type: 'watchlist', vessel_ids: S.pick.ids });
    else if (S.sheet === 'wizard' && S.wiz) shape(S.wiz.draft.geometry);
    setData('zn-draft', { type: 'FeatureCollection', features: feats });
  }

  // ---------- toolbar ----------
  function renderToolbar() {
    const d = S.draw;
    root.classList.toggle('zn-busy', !!(d || S.pick));
    if (d && d.phase === 'width') {
      tb.innerHTML = `
        <span class="zn-tb-hint">Set the corridor width</span>
        <label class="zn-width"><span class="sr-only">Corridor width in km</span>
          <input type="range" min="0.5" max="20" step="0.5" value="${d.width_km}" data-act="width">
          <output class="zn-width-out">${fmtNum(d.width_km)} km</output></label>
        <button class="zn-tb-btn" data-act="draw-back">${ic('undo-2')}<span>Redraw line</span></button>
        <button class="zn-tb-btn" data-act="draw-cancel">${ic('x')}<span>Cancel</span></button>
        <button class="btn primary zn-tb-cta" data-act="draw-continue">Continue${ic('arrow-right')}</button>`;
    } else if (d) {
      const need = d.kind === 'polygon' ? 3 : 2;
      const hint = d.kind === 'polygon'
        ? 'Click points on the map. Double-click or click the first point to close. Esc cancels.'
        : 'Click along the line to protect. Double-click to finish. Esc cancels.';
      tb.innerHTML = `
        <span class="zn-tb-hint">${hint}</span>
        <button class="zn-tb-btn" data-act="draw-undo" ${d.points.length ? '' : 'disabled'}>${ic('undo-2')}<span>Undo point</span></button>
        <button class="zn-tb-btn" data-act="draw-cancel">${ic('x')}<span>Cancel</span></button>
        <button class="btn primary zn-tb-cta" data-act="draw-finish" ${d.points.length >= need ? '' : 'disabled'}>${d.kind === 'polygon' ? 'Close area' : 'Finish line'}</button>`;
    } else if (S.pick) {
      tb.innerHTML = `<span class="zn-tb-hint">Click vessels on the map, or pick them from the list. ${S.pick.ids.length} selected.</span>
        <button class="zn-tb-btn" data-act="pick-cancel">${ic('x')}<span>Cancel</span></button>`;
    } else {
      const n = zones().length;
      tb.innerHTML = `
        <span class="zn-tb-title">Watch zones</span>
        <button class="zn-tb-btn" data-act="draw-polygon">${ic('pentagon')}<span>Draw area</span></button>
        <button class="zn-tb-btn" data-act="draw-corridor">${ic('route')}<span>Draw corridor</span></button>
        <button class="zn-tb-btn" data-act="pick">${ic('mouse-pointer-click')}<span>Pick vessels</span></button>
        <span class="zn-tb-sep" aria-hidden="true"></span>
        <button class="zn-tb-btn" data-act="list" aria-expanded="${S.listOpen}" aria-haspopup="true">${ic('layers')}<span>Zones</span><span class="zn-count">${n}</span></button>`;
    }
    icons();
  }
  function renderList() {
    listEl.hidden = !S.listOpen || !!S.draw || !!S.pick;
    if (listEl.hidden) return;
    const zs = zones();
    listEl.innerHTML = zs.length
      ? `<ul role="list">${zs.map((z) => `<li><button class="zn-list-item" data-act="open-zone" data-id="${esc(z.id)}">
          ${ic(PURPOSE[z.purpose]?.icon || 'map')}<span class="zn-list-text"><span class="zn-list-name">${esc(z.name || z.id)}</span>
          <span class="zn-list-meta">${esc(PURPOSE[z.purpose]?.label || 'Custom')} · ${esc(z.geometry?.type || '')}</span></span></button></li>`).join('')}</ul>`
      : '<p class="zn-empty">No watch zones yet. Draw one to start.</p>';
    icons();
  }
  tb.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]'); if (!b || b.disabled) return;
    const act = b.dataset.act;
    if (act === 'draw-polygon') startDraw('polygon');
    else if (act === 'draw-corridor') startDraw('corridor');
    else if (act === 'pick') startPick([]);
    else if (act === 'list') { S.listOpen = !S.listOpen; renderToolbar(); renderList(); }
    else if (act === 'draw-undo') { S.draw.points.pop(); renderDraft(); renderToolbar(); }
    else if (act === 'draw-cancel') cancelDraw();
    else if (act === 'draw-finish') finishDraw();
    else if (act === 'draw-back') { S.draw.phase = 'points'; S.draw.points = []; renderDraft(); renderToolbar(); }
    else if (act === 'draw-continue') completeDraw();
    else if (act === 'pick-cancel') cancelPick();
  });
  tb.addEventListener('input', (e) => {
    if (e.target.dataset.act !== 'width' || !S.draw) return;
    S.draw.width_km = +e.target.value;
    tb.querySelector('.zn-width-out').textContent = `${fmtNum(S.draw.width_km)} km`;
    renderDraft();
  });
  listEl.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act="open-zone"]'); if (!b) return;
    S.listOpen = false; renderToolbar(); renderList();
    const z = zones().find((x) => x.id === b.dataset.id); if (z) fitZone(z);
    openSummary(b.dataset.id);
  });

  // ---------- drawing ----------
  function startDraw(kind, onDone) {
    cancelPick(true);
    S.listOpen = false; renderList();
    S.draw = { kind, points: [], cursor: null, phase: 'points', width_km: 4, onDone: onDone || ((g) => startWizard(g)) };
    if (!onDone) closeSheet();
    else hideSheet();
    if (map) { map.doubleClickZoom.disable(); map.getCanvas().style.cursor = 'crosshair'; }
    renderToolbar(); renderDraft();
  }
  function endDraw() {
    S.draw = null;
    if (map) { map.getCanvas().style.cursor = ''; setTimeout(() => map.doubleClickZoom.enable(), 350); }
    renderToolbar(); renderDraft();
  }
  function cancelDraw() {
    const back = S.draw?.onBack; endDraw();
    if (back) back();
  }
  function finishDraw() {
    const d = S.draw; if (!d) return;
    d.cursor = null;
    if (d.kind === 'polygon') { if (d.points.length >= 3) completeDraw(); return; }
    if (d.points.length >= 2) { d.phase = 'width'; renderToolbar(); renderDraft(); tb.querySelector('[data-act="width"]')?.focus(); }
  }
  function completeDraw() {
    const d = S.draw; if (!d) return;
    const g = d.kind === 'polygon'
      ? { type: 'polygon', coordinates: d.points.map(round) }
      : { type: 'corridor', line: d.points.map(round), width_km: d.width_km };
    const done = d.onDone; endDraw(); done(g);
  }
  const round = (p) => [+p[0].toFixed(5), +p[1].toFixed(5)];
  function drawClick(e) {
    const d = S.draw; if (d.phase !== 'points') return;
    const ll = [e.lngLat.lng, e.lngLat.lat];
    if (d.points.length) {
      const last = map.project(d.points[d.points.length - 1]);
      if (Math.hypot(last.x - e.point.x, last.y - e.point.y) < 8) return; // second click of a double-click
      if (d.kind === 'polygon' && d.points.length >= 3) {
        const f = map.project(d.points[0]);
        if (Math.hypot(f.x - e.point.x, f.y - e.point.y) < 12) { completeDraw(); return; }
      }
    }
    d.points.push(ll);
    renderDraft(); renderToolbar();
  }

  // ---------- vessel picking (watchlist) ----------
  function startPick(ids, onDone) {
    if (S.draw) endDraw();
    S.listOpen = false; renderList();
    S.pick = { ids: [...new Set(ids)], q: '', onDone: onDone || ((vids) => startWizard({ type: 'watchlist', vessel_ids: vids })), back: !!onDone };
    if (map) map.getCanvas().style.cursor = '';
    S.sheet = 'pick'; S.selected = null;
    renderPickSheet(); renderToolbar(); renderDraft(); renderZones();
  }
  function cancelPick(silent) {
    if (!S.pick) return;
    const back = S.pick.back; S.pick = null;
    renderDraft(); renderToolbar();
    if (silent) return;
    if (back && S.wiz) { S.sheet = 'wizard'; renderWizard(); } else closeSheet();
  }
  function togglePick(id) {
    if (!S.pick || !vessel(id)) return;
    const i = S.pick.ids.indexOf(id);
    if (i >= 0) S.pick.ids.splice(i, 1); else S.pick.ids.push(id);
    renderPickSheet(); renderToolbar(); renderDraft();
  }
  function vesselRows() {
    const q = S.pick.q.trim().toLowerCase();
    const vs = [...(ctx.world?.vessels || [])]
      .filter((v) => !q || [v.id, v.name, v.flag, v.type, v.mmsi, v.imo].some((x) => String(x ?? '').toLowerCase().includes(q)))
      .sort((a, b) => (b.risk?.score ?? 0) - (a.risk?.score ?? 0));
    if (!vs.length) return '<p class="zn-empty">No vessels match.</p>';
    return vs.map((v) => {
      const on = S.pick.ids.includes(v.id), lvl = v.risk?.level || 'low';
      return `<label class="zn-vrow"><input type="checkbox" data-vid="${esc(v.id)}" ${on ? 'checked' : ''}>
        <span class="zn-vtext"><span class="zn-vname"><span class="w500">${esc(v.id)}</span> ${esc(v.name || '')}</span>
        <span class="zn-vmeta">${esc([cap(v.type || ''), v.flag].filter(Boolean).join(' · '))}</span></span>
        ${v.risk ? `<span class="chip ${lvl}">Risk ${esc(v.risk.score)}</span>` : ''}</label>`;
    }).join('');
  }
  function renderPickSheet() {
    if (S.sheet !== 'pick' || !S.pick) return;
    const chips = S.pick.ids.map((id) => `<button class="zn-pchip" data-act="unpick" data-id="${esc(id)}" aria-label="Remove ${esc(id)}">${esc(id)}${ic('x')}</button>`).join('');
    const focusSearch = document.activeElement?.classList.contains('zn-search');
    showSheet(`
      <header class="zn-head">
        <div class="zn-head-text"><p class="zn-kicker">${ic('mouse-pointer-click')}New watchlist zone</p><h2 class="zn-title">Pick vessels</h2></div>
        <button class="zn-icon-btn" data-act="pick-close" aria-label="Close">${ic('x')}</button>
      </header>
      <div class="zn-body">
        <p class="zn-help">Click vessels on the map, or search and tick them here.</p>
        <label class="zn-field"><span class="zn-flabel">Search vessels</span>
          <input class="zn-input zn-search" type="search" placeholder="Name, ID, MMSI, flag" value="${esc(S.pick.q)}" data-act="pick-search"></label>
        <div class="zn-pchips" aria-live="polite">${chips || '<span class="zn-help">No vessels selected yet.</span>'}</div>
        <div class="zn-vlist" role="group" aria-label="Vessels">${vesselRows()}</div>
      </div>
      <footer class="zn-foot">
        <button class="btn" data-act="pick-close">${S.pick.back ? 'Back' : 'Cancel'}</button>
        <button class="btn primary" data-act="pick-next" ${S.pick.ids.length ? '' : 'disabled'}>${S.pick.back ? 'Use these vessels' : 'Next: purpose'}${ic('arrow-right')}</button>
      </footer>`, 'Pick vessels');
    if (focusSearch) { const i = sheet.querySelector('.zn-search'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
  }

  // ---------- sheet plumbing ----------
  function showSheet(html, label) {
    sheet.innerHTML = html;
    sheet.setAttribute('aria-label', label || 'Watch zone');
    sheet.hidden = false;
    root.classList.add('zn-raised');
    icons();
  }
  function hideSheet() { sheet.hidden = true; root.classList.remove('zn-raised'); }
  function closeSheet() {
    S.sheet = null; S.summary = null; S.selected = null;
    if (S.wiz) { S.wiz = null; }
    hideSheet(); renderDraft(); renderZones();
  }

  // ---------- wizard ----------
  function startWizard(geometry, zone) {
    const draft = zone ? clone(zone) : { name: '', purpose: null, geometry, asset_id: null, rules: [], metrics: [], recipients: [] };
    if (!zone && geometry.type !== 'watchlist') draft.asset_id = nearestAsset(geometry)?.id || null;
    S.wiz = { step: 1, draft, editingId: zone?.id || null, nameAuto: !zone, addRule: '', saving: false, error: '',
      cm: { signal: 'speed_below_kn', param: 3, aggregation: 'count', window: '1h', alert_above: 2 } };
    S.sheet = 'wizard'; S.selected = null; S.summary = null;
    loadPresets().then(() => { if (S.sheet === 'wizard') renderWizard(); });
    renderWizard(); renderZones(); renderDraft();
  }
  function autoName(d) {
    const a = asset(d.asset_id), g = d.geometry?.type;
    if (d.purpose === 'cable' && a) return `${a.alias || a.name} ${g === 'corridor' ? 'corridor' : 'area'}`;
    const base = { cable: 'Cable watch', sanctions: 'Sanctions', insurance: 'Voyage risk', custom: 'Custom' }[d.purpose] || 'Watch';
    return `${base} ${g === 'watchlist' ? 'watchlist' : g === 'corridor' ? 'corridor' : 'area'}`;
  }
  function choosePurpose(pid) {
    const d = S.wiz.draft;
    if (d.purpose !== pid) {
      const pr = presets()[pid] || FALLBACK_PRESETS[pid] || { rules: [], metrics: [] };
      d.purpose = pid;
      d.rules = clone(pr.rules || []);
      d.metrics = clone(pr.metrics || []).map((m) => ({ ...m, enabled: m.enabled !== false }));
      d.recipients = clone(DEFAULT_RECIPIENTS[pid] || DEFAULT_RECIPIENTS.custom);
      if (pid !== 'cable' && d.geometry.type === 'watchlist') d.asset_id = null;
    }
    if (S.wiz.nameAuto) d.name = autoName(d);
    renderWizard();
  }
  function stepper() {
    const s = S.wiz.step, canJump = !!S.wiz.draft.purpose;
    return `<ol class="zn-steps" aria-label="Steps">${STEPS.map((l, i) => {
      const n = i + 1, state = n === s ? 'current' : n < s ? 'done' : 'todo';
      return `<li><button class="zn-step" data-act="step" data-n="${n}" data-state="${state}" ${n === s ? 'aria-current="step"' : ''} ${canJump || n === 1 ? '' : 'disabled'}>
        <span class="zn-step-n">${n < s ? ic('check') : n}</span><span>${l}</span></button></li>`;
    }).join('')}</ol>`;
  }
  function seg(name, value, attrs) {
    return `<div class="zn-seg" role="radiogroup" aria-label="${esc(name)}">${SEVS.map((v) => `<button role="radio" aria-checked="${v === value}" data-val="${v}" ${attrs}>${SEV_LABEL[v]}</button>`).join('')}</div>`;
  }
  function stepPurpose() {
    const d = S.wiz.draft, g = d.geometry;
    const choices = PURPOSES.map((p) => {
      const desc = presets()[p.id]?.description || p.desc;
      return `<button class="zn-choice" role="radio" aria-checked="${d.purpose === p.id}" data-act="purpose" data-id="${p.id}">
        <span class="zn-choice-icon">${ic(p.icon)}</span><span class="zn-choice-text"><span class="zn-choice-label">${esc(p.label)}</span>
        <span class="zn-choice-desc">${esc(desc)}</span></span></button>`;
    }).join('');
    const assets = ctx.world?.assets || [];
    return `
      <section class="zn-sec"><h3 class="zn-h">What is this zone for?</h3>
        <div class="zn-choices" role="radiogroup" aria-label="Purpose">${choices}</div></section>
      <section class="zn-sec"><h3 class="zn-h">Zone</h3>
        <label class="zn-field"><span class="zn-flabel">Name</span><input class="zn-input" data-f="name" value="${esc(d.name)}" placeholder="e.g. Cable A corridor"></label>
        <div class="zn-geom"><span class="zn-geom-text">${geomText(d)}</span>
          <button class="btn small" data-act="redraw">${ic(g.type === 'watchlist' ? 'mouse-pointer-click' : 'pencil')}${g.type === 'watchlist' ? 'Edit vessels' : 'Redraw'}</button></div>
        ${g.type === 'corridor' ? `<label class="zn-field"><span class="zn-flabel">Corridor width <output class="zn-wiz-w">${fmtNum(g.width_km)} km</output></span>
          <input type="range" min="0.5" max="20" step="0.5" value="${g.width_km}" data-f="width"></label>` : ''}
        ${g.type !== 'watchlist' && assets.length ? `<label class="zn-field"><span class="zn-flabel">Protected asset</span>
          <select class="zn-input" data-f="asset"><option value="">None</option>${assets.map((a) => `<option value="${esc(a.id)}" ${a.id === d.asset_id ? 'selected' : ''}>${esc(a.alias ? `${a.alias} · ${a.name}` : a.name)}</option>`).join('')}</select></label>` : ''}
      </section>`;
  }
  function allRuleTemplates() {
    const out = [], seen = new Set(S.wiz.draft.rules.map((r) => r.id));
    for (const [pid, pr] of Object.entries(presets())) for (const r of pr.rules || []) if (!seen.has(r.id)) { seen.add(r.id); out.push({ pid, r }); }
    return out;
  }
  function stepRules() {
    const d = S.wiz.draft;
    const rows = d.rules.map((r, i) => `
      <div class="zn-rule ${r.enabled ? '' : 'is-off'}">
        <div class="zn-rule-head">
          <span class="zn-rule-label" id="zn-rl-${i}">${esc(r.label)}</span>
          <button class="zn-icon-btn" data-act="rule-remove" data-i="${i}" aria-label="Remove rule ${esc(r.label)}">${ic('trash-2')}</button>
          <button class="zn-switch" role="switch" aria-checked="${!!r.enabled}" aria-labelledby="zn-rl-${i}" data-act="rule-toggle" data-i="${i}"><span class="zn-switch-track"><span class="zn-switch-knob"></span></span></button>
        </div>
        ${(r.params || []).length ? `<div class="zn-params">${r.params.map((p, j) => `
          <label class="zn-param"><span class="zn-flabel">${esc(p.label)}</span><span class="zn-num">
            <input class="zn-input" type="number" step="any" value="${esc(p.value)}" data-f="param" data-i="${i}" data-j="${j}"><span class="zn-unit">${esc(p.unit || '')}</span></span></label>`).join('')}</div>` : ''}
        <div class="zn-rule-foot">
          <div class="zn-field-inline"><span class="zn-flabel" id="zn-sv-${i}">Severity</span>${seg('Severity', r.severity, `data-act="rule-sev" data-i="${i}"`)}</div>
          <label class="zn-field-inline"><span class="zn-flabel">Window</span>
            <select class="zn-input zn-select-sm" data-f="rule-window" data-i="${i}"><option value="">Any time</option>${WINDOWS.map((w) => `<option value="${w}" ${r.window === w ? 'selected' : ''}>Rolling ${WIN_LABEL[w]}</option>`).join('')}</select></label>
        </div>
      </div>`).join('');
    const tpl = allRuleTemplates();
    const on = d.rules.filter((r) => r.enabled).length;
    return `
      <section class="zn-sec"><h3 class="zn-h">Rules <span class="zn-h-meta">${on} of ${d.rules.length} on</span></h3>
        <p class="zn-help">A vessel that breaks an enabled rule becomes an event; high and medium events become alerts.</p>
        <div class="zn-rules">${rows || '<p class="zn-empty">No rules yet. Add one below.</p>'}</div></section>
      <section class="zn-sec"><h3 class="zn-h">Add rule</h3>
        <div class="zn-add">
          <label class="zn-field zn-grow"><span class="sr-only">Rule to add</span>
            <select class="zn-input" data-f="add-rule"><option value="">Choose a rule…</option>${tpl.map(({ pid, r }) => `<option value="${esc(r.id)}" ${S.wiz.addRule === r.id ? 'selected' : ''}>${esc(r.label)} · ${esc(PURPOSE[pid]?.label.split(' /')[0] || pid)}</option>`).join('')}</select></label>
          <button class="btn" data-act="rule-add" ${tpl.length ? '' : 'disabled'}>${ic('plus')}Add rule</button>
        </div></section>`;
  }
  function cmSentence(c) {
    const s = SIGNALS[c.signal] || SIGNALS.in_zone;
    return `${c.aggregation} of ${s.phrase(c.param)}, rolling ${WIN_LABEL[c.window] || c.window}, alert if > ${c.alert_above}`;
  }
  function stepMetrics() {
    const d = S.wiz.draft, c = S.wiz.cm;
    const std = d.metrics.map((m, i) => ({ m, i })).filter(({ m }) => !m.custom);
    const custom = d.metrics.map((m, i) => ({ m, i })).filter(({ m }) => m.custom);
    return `
      <section class="zn-sec"><h3 class="zn-h">Metrics to track</h3>
        <div class="zn-checks">${std.map(({ m, i }) => `<label class="zn-check"><input type="checkbox" data-f="metric" data-i="${i}" ${m.enabled ? 'checked' : ''}>
          <span>${esc(m.label)}</span>${m.unit ? `<span class="zn-unit">${esc(m.unit)}</span>` : ''}</label>`).join('') || '<p class="zn-empty">This purpose has no preset metrics.</p>'}</div>
        ${custom.length ? `<h3 class="zn-h zn-h-sub">Custom metrics</h3><ul class="zn-custom" role="list">${custom.map(({ m, i }) => `
          <li><label class="zn-check zn-grow"><input type="checkbox" data-f="metric" data-i="${i}" ${m.enabled ? 'checked' : ''}><span>${esc(cap(cmSentence(m.custom)))}</span></label>
          <button class="zn-icon-btn" data-act="metric-remove" data-i="${i}" aria-label="Remove metric">${ic('trash-2')}</button></li>`).join('')}</ul>` : ''}
      </section>
      <section class="zn-sec"><h3 class="zn-h">Define a custom metric</h3>
        <div class="zn-grid">
          <label class="zn-field"><span class="zn-flabel">Signal</span><select class="zn-input" data-f="cm-signal">${Object.entries(SIGNALS).map(([k, s]) => `<option value="${k}" ${c.signal === k ? 'selected' : ''}>${s.label}</option>`).join('')}</select></label>
          ${SIGNALS[c.signal]?.param ? `<label class="zn-field"><span class="zn-flabel">Below</span><span class="zn-num"><input class="zn-input" type="number" step="any" value="${c.param}" data-f="cm-param"><span class="zn-unit">kn</span></span></label>` : '<span></span>'}
          <label class="zn-field"><span class="zn-flabel">Aggregation</span><select class="zn-input" data-f="cm-aggregation">${AGGS.map((a) => `<option value="${a}" ${c.aggregation === a ? 'selected' : ''}>${cap(a)}</option>`).join('')}</select></label>
          <label class="zn-field"><span class="zn-flabel">Window</span><select class="zn-input" data-f="cm-window">${WINDOWS.map((w) => `<option value="${w}" ${c.window === w ? 'selected' : ''}>Rolling ${WIN_LABEL[w]}</option>`).join('')}</select></label>
          <label class="zn-field"><span class="zn-flabel">Alert if above</span><input class="zn-input" type="number" step="any" value="${c.alert_above}" data-f="cm-alert"></label>
        </div>
        <p class="zn-sentence" aria-live="polite">Reads as: <span class="zn-sentence-text">${esc(cmSentence(c))}</span></p>
        <button class="btn" data-act="metric-add">${ic('plus')}Add metric</button>
      </section>`;
  }
  function stepRecipients() {
    const d = S.wiz.draft;
    const rows = d.recipients.map((r, i) => `
      <div class="zn-recip">
        <label class="zn-field zn-span2"><span class="zn-flabel">Name</span><input class="zn-input" data-f="r-name" data-i="${i}" value="${esc(r.name)}"></label>
        <label class="zn-field"><span class="zn-flabel">Channel</span><select class="zn-input" data-f="r-channel" data-i="${i}">${CHANNELS.map(([k, l]) => `<option value="${k}" ${r.channel === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
        <label class="zn-field"><span class="zn-flabel">Address</span><input class="zn-input" data-f="r-address" data-i="${i}" value="${esc(r.address)}" placeholder="${esc(CHANNEL_HINT[r.channel] || '')}" ${r.channel === 'in_app' ? 'disabled' : ''}></label>
        <label class="zn-field"><span class="zn-flabel">Minimum severity</span><select class="zn-input" data-f="r-sev" data-i="${i}">${SEVS.map((s) => `<option value="${s}" ${r.min_severity === s ? 'selected' : ''}>${SEV_LABEL[s]} and up</option>`).join('')}</select></label>
        <label class="zn-field"><span class="zn-flabel">Quiet hours</span><select class="zn-input" data-f="r-quiet" data-i="${i}">${[...new Set([...QUIET, r.quiet_hours || ''])].map((q) => `<option value="${q}" ${(r.quiet_hours || '') === q ? 'selected' : ''}>${q ? q.replace('-', '–') + ' UTC' : 'None'}</option>`).join('')}</select></label>
        <button class="btn small ghost zn-span2 zn-remove" data-act="r-remove" data-i="${i}">${ic('trash-2')}Remove ${esc(r.name || 'recipient')}</button>
      </div>`).join('');
    return `<section class="zn-sec"><h3 class="zn-h">Who gets alerted</h3>
      <p class="zn-help">Each alert goes to every recipient whose minimum severity it meets. Quiet hours hold push and email until morning, except high alerts.</p>
      <div class="zn-recips">${rows || '<p class="zn-empty">No recipients. Alerts will only show in Skynet.</p>'}</div>
      <button class="btn" data-act="r-add">${ic('plus')}Add recipient</button></section>`;
  }
  function renderWizard() {
    if (S.sheet !== 'wizard' || !S.wiz) return;
    const w = S.wiz, d = w.draft;
    const body = [stepPurpose, stepRules, stepMetrics, stepRecipients][w.step - 1]();
    const scroll = sheet.querySelector('.zn-body')?.scrollTop || 0;
    const sameStep = sheet.dataset.step === String(w.step) && sheet.dataset.kind === 'wizard';
    const focusKey = document.activeElement && sheet.contains(document.activeElement) ? focusSig(document.activeElement) : null;
    showSheet(`
      <header class="zn-head">
        <div class="zn-head-text"><p class="zn-kicker">${ic(d.purpose ? PURPOSE[d.purpose].icon : 'map-pin')}${w.editingId ? `Edit zone ${esc(w.editingId)}` : 'New watch zone'}</p>
          <h2 class="zn-title">${esc(d.name || 'Untitled zone')}</h2></div>
        <button class="zn-icon-btn" data-act="wiz-close" aria-label="Close">${ic('x')}</button>
      </header>
      ${stepper()}
      <div class="zn-body">${body}${w.error ? `<p class="zn-error" role="alert">${esc(w.error)}</p>` : ''}</div>
      <footer class="zn-foot">
        ${w.step > 1 ? `<button class="btn" data-act="back">${ic('arrow-left')}Back</button>` : '<button class="btn" data-act="wiz-close">Cancel</button>'}
        ${w.step < 4
          ? `<button class="btn primary" data-act="next" ${d.purpose ? '' : 'disabled'}>Next: ${STEPS[w.step]}${ic('arrow-right')}</button>`
          : `<button class="btn primary" data-act="save" ${w.saving ? 'disabled' : ''}>${ic('check')}${w.saving ? 'Saving…' : w.editingId ? 'Save changes' : 'Save zone'}</button>`}
      </footer>`, 'Watch zone wizard');
    sheet.dataset.kind = 'wizard'; sheet.dataset.step = String(w.step);
    if (sameStep) sheet.querySelector('.zn-body').scrollTop = scroll;
    if (focusKey) { const el = sheet.querySelector(focusKey); el?.focus(); }
    renderDraft();
  }
  function focusSig(el) {
    const a = ['data-act', 'data-f', 'data-i', 'data-j', 'data-id', 'data-n', 'data-val'].filter((k) => el.hasAttribute(k)).map((k) => `[${k}="${CSS.escape(el.getAttribute(k))}"]`).join('');
    return a ? `${el.tagName.toLowerCase()}${a}` : null;
  }
  async function saveWizard() {
    const w = S.wiz, d = w.draft;
    if (!d.name?.trim()) d.name = autoName(d);
    const body = { name: d.name.trim(), purpose: d.purpose, geometry: d.geometry, asset_id: d.asset_id || null, rules: d.rules, metrics: d.metrics,
      recipients: d.recipients.map((r) => ({ ...r, quiet_hours: r.quiet_hours || null })), created: d.created ?? Math.round(ctx.clock.t), created_by: d.created_by || 'Ian (demo)' };
    w.saving = true; w.error = ''; renderWizard();
    try {
      const res = w.editingId ? await ctx.api.put(`/api/zones/${encodeURIComponent(w.editingId)}`, { ...body, id: w.editingId }) : await ctx.api.post('/api/zones', body);
      const z = res?.zone || res;
      S.wiz = null;
      try { await ctx.reloadWorld(); } catch (e) { console.error(e); }
      ctx.bus.emit('zones-changed', { zone_id: z?.id });
      const n = res?.events?.length ?? 0, m = res?.alerts?.length ?? 0;
      toast(`Zone saved · ${n} event${n === 1 ? '' : 's'} found · ${m} alert${m === 1 ? '' : 's'}`);
      if (z?.id) openSummary(z.id, res); else closeSheet();
    } catch (e) {
      console.error(e);
      w.saving = false; w.error = `Could not save the zone: ${e.message}`; renderWizard();
    }
  }
  sheet.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]'); if (!b || b.disabled) return;
    const act = b.dataset.act, i = +b.dataset.i, w = S.wiz, d = w?.draft;
    switch (act) {
      // picker
      case 'pick-close': cancelPick(); return;
      case 'unpick': togglePick(b.dataset.id); return;
      case 'pick-next': { const ids = [...S.pick.ids], done = S.pick.onDone; S.pick = null; renderToolbar(); done(ids); return; }
      // wizard
      case 'wiz-close': closeSheet(); return;
      case 'purpose': choosePurpose(b.dataset.id); return;
      case 'step': w.step = +b.dataset.n; renderWizard(); sheet.querySelector('.zn-body').scrollTop = 0; return;
      case 'next': w.step = Math.min(4, w.step + 1); renderWizard(); sheet.querySelector('.zn-body').scrollTop = 0; return;
      case 'back': w.step = Math.max(1, w.step - 1); renderWizard(); return;
      case 'save': saveWizard(); return;
      case 'redraw': {
        const back = () => { S.sheet = 'wizard'; renderWizard(); };
        if (d.geometry.type === 'watchlist') { startPick(d.geometry.vessel_ids || [], (ids) => { d.geometry = { type: 'watchlist', vessel_ids: ids }; back(); }); }
        else {
          const width = d.geometry.width_km;
          startDraw(d.geometry.type === 'corridor' ? 'corridor' : 'polygon', (g) => {
            d.geometry = g; if (!d.asset_id) d.asset_id = nearestAsset(g)?.id || null;
            if (w.nameAuto) d.name = autoName(d); back();
          });
          if (width) S.draw.width_km = width;
          S.draw.onBack = back;
        }
        return;
      }
      case 'rule-toggle': d.rules[i].enabled = !d.rules[i].enabled; renderWizard(); return;
      case 'rule-sev': d.rules[i].severity = b.dataset.val; renderWizard(); return;
      case 'rule-remove': d.rules.splice(i, 1); renderWizard(); return;
      case 'rule-add': {
        const t = allRuleTemplates().find(({ r }) => r.id === w.addRule) || allRuleTemplates()[0];
        if (t) { d.rules.push({ ...clone(t.r), enabled: true }); w.addRule = ''; renderWizard(); }
        return;
      }
      case 'metric-remove': d.metrics.splice(i, 1); renderWizard(); return;
      case 'metric-add': {
        const c = { ...w.cm }; if (!SIGNALS[c.signal].param) delete c.param;
        const n = d.metrics.filter((m) => m.custom).length + 1;
        let id = `custom-${n}`; while (d.metrics.some((m) => m.id === id)) id += 'b';
        d.metrics.push({ id, label: cap(cmSentence(c).split(', alert')[0]), enabled: true, custom: c });
        renderWizard(); return;
      }
      case 'r-add': d.recipients.push(RC('', 'email', '', 'medium')); renderWizard();
        sheet.querySelector(`[data-f="r-name"][data-i="${d.recipients.length - 1}"]`)?.focus(); return;
      case 'r-remove': d.recipients.splice(i, 1); renderWizard(); return;
      // summary
      case 'sum-close': closeSheet(); return;
      case 'sum-edit': { const z = zones().find((x) => x.id === S.summary?.id); if (z) { S.summary = null; startWizard(null, z); } return; }
      case 'sum-delete': S.summary.confirm = true; renderSummary(); sheet.querySelector('[data-act="sum-delete-yes"]')?.focus(); return;
      case 'sum-delete-no': S.summary.confirm = false; renderSummary(); return;
      case 'sum-delete-yes': deleteZone(S.summary.id); return;
      case 'sum-report': {
        const vid = b.dataset.vid; if (!vid) return;
        const z = S.summary?.data?.zone || zones().find((x) => x.id === S.summary?.id);
        const tpl = ['cable', 'sanctions', 'insurance'].includes(z?.purpose) ? z.purpose : 'cable';
        ctx.bus.emit('open-report', { vessel_id: vid, template: tpl }); return;
      }
      case 'event': ctx.bus.emit('focus', { vessel_ids: [b.dataset.vid], t: +b.dataset.t || undefined }); return;
      default:
    }
  });
  sheet.addEventListener('input', (e) => {
    const el = e.target, f = el.dataset.f, i = +el.dataset.i;
    if (el.dataset.act === 'pick-search') { S.pick.q = el.value; sheet.querySelector('.zn-vlist').innerHTML = vesselRows(); return; }
    if (!S.wiz || !f) return;
    const w = S.wiz, d = w.draft;
    if (f === 'name') { d.name = el.value; w.nameAuto = false; sheet.querySelector('.zn-title').textContent = el.value || 'Untitled zone'; }
    else if (f === 'width') { d.geometry.width_km = +el.value; sheet.querySelector('.zn-wiz-w').textContent = `${fmtNum(+el.value)} km`; sheet.querySelector('.zn-geom-text').innerHTML = geomText(d); renderDraft(); }
    else if (f === 'param') { const v = parseFloat(el.value); if (!Number.isNaN(v)) d.rules[i].params[+el.dataset.j].value = v; }
    else if (f === 'r-name') d.recipients[i].name = el.value;
    else if (f === 'r-address') d.recipients[i].address = el.value;
    else if (f.startsWith('cm-') && el.tagName === 'INPUT') {
      const v = parseFloat(el.value); if (Number.isNaN(v)) return;
      if (f === 'cm-param') w.cm.param = v; else if (f === 'cm-alert') w.cm.alert_above = v;
      sheet.querySelector('.zn-sentence-text').textContent = cmSentence(w.cm);
    }
  });
  sheet.addEventListener('change', (e) => {
    const el = e.target, f = el.dataset.f, i = +el.dataset.i;
    if (el.dataset.vid) { togglePick(el.dataset.vid); return; }
    if (!S.wiz || !f) return;
    const w = S.wiz, d = w.draft;
    if (f === 'asset') { d.asset_id = el.value || null; if (w.nameAuto && d.purpose) d.name = autoName(d); renderWizard(); }
    else if (f === 'rule-window') d.rules[i].window = el.value || null;
    else if (f === 'add-rule') w.addRule = el.value;
    else if (f === 'metric') d.metrics[i].enabled = el.checked;
    else if (f === 'cm-signal') { w.cm.signal = el.value; if (SIGNALS[el.value].param && w.cm.param == null) w.cm.param = 3; renderWizard(); }
    else if (f === 'cm-aggregation') { w.cm.aggregation = el.value; sheet.querySelector('.zn-sentence-text').textContent = cmSentence(w.cm); }
    else if (f === 'cm-window') { w.cm.window = el.value; sheet.querySelector('.zn-sentence-text').textContent = cmSentence(w.cm); }
    else if (f === 'r-channel') { d.recipients[i].channel = el.value; if (el.value === 'in_app') d.recipients[i].address = ''; renderWizard(); }
    else if (f === 'r-sev') d.recipients[i].min_severity = el.value;
    else if (f === 'r-quiet') d.recipients[i].quiet_hours = el.value || null;
  });

  // ---------- summary sheet ----------
  async function openSummary(id, prefetched) {
    if (S.draw) endDraw();
    if (S.pick) cancelPick(true);
    S.wiz = null; S.sheet = 'summary'; S.selected = id;
    S.summary = { id, data: null, loading: true, error: '', confirm: false, t: ctx.clock.t };
    renderZones(); renderDraft();
    if (prefetched?.zone) { S.summary.data = { zone: prefetched.zone, metrics: prefetched.metrics || [], events: prefetched.events || [], alerts: prefetched.alerts || [] }; }
    renderSummary();
    await fetchSummary(id);
  }
  async function fetchSummary(id) {
    const tok = (S.summaryTok = (S.summaryTok || 0) + 1);
    try {
      const data = await ctx.api.get(`/api/zones/${encodeURIComponent(id)}/summary?t=${Math.round(ctx.clock.t)}`);
      if (tok !== S.summaryTok || S.summary?.id !== id) return;
      S.summary.data = data; S.summary.error = '';
    } catch (e) {
      if (tok !== S.summaryTok || S.summary?.id !== id) return;
      console.warn('[zones] summary failed', e);
      if (!S.summary.data) {
        const z = zones().find((x) => x.id === id);
        S.summary.data = z ? { zone: z, metrics: null, events: (ctx.world?.events || []).filter((ev) => ev.zone_id === id), alerts: (ctx.world?.alerts || []).filter((a) => a.zone_id === id) } : null;
      }
      S.summary.error = 'Live metrics are unavailable right now.';
    }
    S.summary.loading = false; S.summary.t = ctx.clock.t;
    renderSummary();
  }
  function topVessel(data) {
    const z = data.zone, ids = new Set();
    for (const a of data.alerts || []) ids.add(a.vessel_id);
    for (const ev of data.events || []) ids.add(ev.vessel_id);
    for (const v of z?.geometry?.vessel_ids || []) ids.add(v);
    let best = null;
    for (const id of ids) { const v = vessel(id); if (v && (!best || (v.risk?.score ?? 0) > (best.risk?.score ?? 0))) best = v; }
    return best;
  }
  function renderSummary() {
    if (S.sheet !== 'summary' || !S.summary) return;
    const s = S.summary, data = s.data, z = data?.zone || zones().find((x) => x.id === s.id);
    if (!z) {
      showSheet(`<header class="zn-head"><div class="zn-head-text"><h2 class="zn-title">${s.loading ? 'Loading zone…' : 'Zone not found'}</h2></div>
        <button class="zn-icon-btn" data-act="sum-close" aria-label="Close">${ic('x')}</button></header>`, 'Zone summary');
      return;
    }
    const pur = PURPOSE[z.purpose] || PURPOSE.custom;
    const metrics = data?.metrics;
    const metricHtml = metrics == null
      ? `<p class="zn-help">${s.loading ? 'Loading metrics…' : esc(s.error || 'No metrics.')}</p>`
      : metrics.length
        ? `<dl class="zn-kvs">${metrics.map((m) => `<div class="zn-kv"><dt><span>${esc(m.label)}</span>${m.detail ? `<span class="zn-kv-detail">${esc(m.detail)}</span>` : ''}</dt>
            <dd>${m.value == null || m.value === '' ? '—' : esc(fmtNum(m.value))}${m.unit && m.value != null && m.value !== '' ? ` <span class="zn-kv-unit">${esc(m.unit)}</span>` : ''}</dd></div>`).join('')}</dl>`
        : '<p class="zn-help">No metrics enabled for this zone.</p>';
    const all = [...(data?.events || [])].sort((a, b) => b.t - a.t);
    const upto = ctx.world?.scenario?.time_mode === 'static' ? all : all.filter((ev) => ev.t <= ctx.clock.t);
    const evs = (upto.length ? upto : all).slice(0, 5);
    const evHtml = evs.length
      ? `<ul class="zn-events" role="list">${evs.map((ev) => {
          const v = vessel(ev.vessel_id);
          return `<li><button class="zn-event" data-act="event" data-vid="${esc(ev.vessel_id)}" data-t="${ev.t}">
            <span class="zn-dot ${esc(ev.severity || 'low')}" aria-label="${esc(SEV_LABEL[ev.severity] || 'Low')} severity"></span>
            <span class="zn-event-text"><span class="zn-event-sum">${esc(ev.summary || humanType(ev.type))}</span>
            <span class="zn-event-meta">${esc(ev.vessel_id)}${v?.name ? ` ${esc(v.name)}` : ''} · ${esc(ctx.fmtTime(ev.t))} UTC · ${esc(humanType(ev.type))}</span></span>
            ${ic('chevron-right')}</button></li>`;
        }).join('')}</ul>`
      : `<p class="zn-help">${s.loading ? 'Loading events…' : 'No events in this zone yet.'}</p>`;
    const rulesOn = (z.rules || []).filter((r) => r.enabled).length;
    const top = data ? topVessel(data) : null;
    const nAlerts = (data?.alerts || []).length;
    showSheet(`
      <header class="zn-head">
        <div class="zn-head-text"><p class="zn-kicker">${ic(pur.icon)}${esc(pur.label)} · ${esc(z.id)}</p><h2 class="zn-title">${esc(z.name || z.id)}</h2></div>
        <button class="zn-icon-btn" data-act="sum-close" aria-label="Close">${ic('x')}</button>
      </header>
      <div class="zn-body">
        <p class="zn-geomline">${geomText(z)}</p>
        <p class="zn-help">${rulesOn} of ${(z.rules || []).length} rules on · ${(z.recipients || []).length} recipient${(z.recipients || []).length === 1 ? '' : 's'} · ${nAlerts} alert${nAlerts === 1 ? '' : 's'}</p>
        <section class="zn-sec"><h3 class="zn-h">Metrics <span class="zn-h-meta">at ${esc(ctx.fmtTime(Math.round(s.t || ctx.clock.t)))} UTC</span></h3>${metricHtml}
          ${metrics != null && s.error ? `<p class="zn-help">${esc(s.error)}</p>` : ''}</section>
        <section class="zn-sec"><h3 class="zn-h">Recent events</h3>${evHtml}</section>
      </div>
      <footer class="zn-foot zn-foot-col">
        ${s.confirm
          ? `<p class="zn-confirm">Delete “${esc(z.name)}”? Its events and alerts are removed too.</p>
             <div class="zn-foot-row"><button class="btn" data-act="sum-delete-no">Keep zone</button><button class="btn danger" data-act="sum-delete-yes">${ic('trash-2')}Delete zone</button></div>`
          : `<button class="btn primary zn-wide" data-act="sum-report" ${top ? `data-vid="${esc(top.id)}"` : 'disabled'}>${ic('file-text')}${top ? `Report on this zone's top vessel · ${esc(top.id)}` : "Report on this zone's top vessel"}</button>
             <div class="zn-foot-row"><button class="btn" data-act="sum-edit">${ic('pencil')}Edit</button><button class="btn danger" data-act="sum-delete">${ic('trash-2')}Delete</button></div>`}
      </footer>`, `Zone ${z.name || z.id}`);
    sheet.dataset.kind = 'summary';
  }
  async function deleteZone(id) {
    try {
      await ctx.api.del(`/api/zones/${encodeURIComponent(id)}`);
      closeSheet();
      try { await ctx.reloadWorld(); } catch (e) { console.error(e); }
      ctx.bus.emit('zones-changed', { zone_id: id });
      toast('Zone deleted');
    } catch (e) { toast(`Could not delete the zone: ${e.message}`); }
  }

  // ---------- map interaction ----------
  function vesselAtPoint(pt) {
    const fs = map.queryRenderedFeatures([[pt.x - 8, pt.y - 8], [pt.x + 8, pt.y + 8]]);
    for (const f of fs) {
      if (f.layer.id.startsWith('zn-') || !/^(base|det|al|rw|an)-/.test(f.layer.id)) continue;
      const id = f.properties?.vessel_id || f.properties?.id;
      if (id && vessel(id)) return id;
    }
    return null;
  }
  if (map) {
    setupLayers();
    map.on('click', (e) => {
      if (S.draw) { drawClick(e); return; }
      if (S.pick) { const id = vesselAtPoint(e.point); if (id) pickOnce(id); return; }
      if (S.sheet === 'wizard') return;
      if (vesselAtPoint(e.point)) return;
      const layers = isAlert() ? ['zn-fill', 'zn-hit', 'zn-label'] : ['zn-hit', 'zn-label'];
      const f = map.queryRenderedFeatures(e.point, { layers: layers.filter((l) => map.getLayer(l)) })[0];
      if (f?.properties?.id) { if (!isAlert()) ctx.setMode('alert'); openSummary(f.properties.id); }
    });
    map.on('dblclick', (e) => { if (S.draw) { e.preventDefault(); finishDraw(); } });
    map.on('mousemove', (e) => {
      if (S.draw && S.draw.phase === 'points') { S.draw.cursor = [e.lngLat.lng, e.lngLat.lat]; renderDraft(); return; }
      if (S.draw || S.pick) { if (S.pick) map.getCanvas().style.cursor = vesselAtPoint(e.point) ? 'pointer' : ''; return; }
      if (S.sheet === 'wizard') return;
      const hit = map.getLayer('zn-hit') && map.queryRenderedFeatures(e.point, { layers: ['zn-hit', 'zn-label'] }).length;
      if (hit) { map.getCanvas().style.cursor = 'pointer'; S.hover = true; }
      else if (S.hover) { map.getCanvas().style.cursor = ''; S.hover = false; }
    });
    map.getCanvas().addEventListener('mouseleave', () => { if (S.draw) { S.draw.cursor = null; renderDraft(); } });
  }
  // A vessel click from map.js/detect.js while picking also toggles it (deduped with our own map click).
  function pickOnce(id) {
    const l = S.lastPickToggle, now = performance.now();
    if (l && l.id === id && now - l.at < 600) return;
    S.lastPickToggle = { id, at: now };
    togglePick(id);
  }
  on('select-vessel', ({ id }) => { if (S.pick && id) pickOnce(id); });

  document.addEventListener('keydown', (e) => {
    const inField = e.target.closest?.('input, textarea, select');
    if (S.draw) {
      if (e.key === 'Escape') { e.preventDefault(); cancelDraw(); }
      else if (e.key === 'Enter' && !e.target.closest?.('button')) { e.preventDefault(); S.draw.phase === 'width' ? completeDraw() : finishDraw(); }
      else if (e.key === 'Backspace' && !inField && S.draw.phase === 'points') { e.preventDefault(); S.draw.points.pop(); renderDraft(); renderToolbar(); }
      return;
    }
    if (e.key === 'Escape') {
      if (S.listOpen) { S.listOpen = false; renderToolbar(); renderList(); tb.querySelector('[data-act="list"]')?.focus(); }
      else if (S.pick && !inField) cancelPick();
      else if (S.sheet === 'summary') closeSheet();
    }
  });
  document.addEventListener('pointerdown', (e) => { if (S.listOpen && !listEl.contains(e.target) && !tb.contains(e.target)) { S.listOpen = false; renderToolbar(); renderList(); } });

  // ---------- bus ----------
  on('mode', ({ mode }) => {
    root.dataset.mode = mode;
    applyModeStyle();
    if (!(mode === 'alert' || mode === 'zones')) {
      // Zones live in Alert mode: leaving it closes the toolbar state and any open sheet.
      if (S.draw) endDraw();
      if (S.pick) cancelPick(true);
      if (S.sheet) closeSheet();
      S.listOpen = false; renderList();
    }
    renderToolbar();
  });
  on('open-zones', ({ vessel_id } = {}) => {
    // app.js switches to Alert mode first.
    if (vessel_id) startPick([vessel_id]);
    else { tb.classList.remove('zn-flash'); void tb.offsetWidth; tb.classList.add('zn-flash'); tb.querySelector('button')?.focus(); }
  });
  on('world', () => {
    renderZones(); renderToolbar(); renderList();
    if (S.sheet === 'summary' && S.summary && !zones().find((z) => z.id === S.summary.id)) closeSheet();
  });
  on('case', () => { if (S.draw) endDraw(); S.pick = null; closeSheet(); renderToolbar(); });
  on('zones-changed', () => { if (S.sheet === 'summary' && S.summary) fetchSummary(S.summary.id); });
  on('tick', ({ t }) => {
    renderWatch(false);
    if (S.sheet === 'summary' && S.summary && !S.summary.loading && Math.abs(t - (S.summary.t || 0)) >= 600) { S.summary.loading = true; fetchSummary(S.summary.id); }
  });

  root.dataset.mode = ctx.mode;
  renderToolbar(); renderZones();
}
