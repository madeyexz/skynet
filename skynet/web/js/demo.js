// Skynet demo stage: three scripted beats (Detect, Alert, Prove) over the D-04 Cable A scenario.
// Owner: H. Uses GET /api/world when it has a usable D-04 record, else the embedded fallback below.
// URL flags: ?auto=1 plays all three beats back to back (~30 s); ?fallback=1 forces the embedded data.

const qs = new URLSearchParams(location.search);
const AUTO = qs.get('auto') === '1';
const FORCE_FALLBACK = qs.get('fallback') === '1';

const $ = (s, r = document) => r.querySelector(s);
const pad2 = (n) => String(n).padStart(2, '0');
const hhmm = (t) => { const d = new Date(t * 1000); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const hhmmss = (t) => { const d = new Date(t * 1000); return `${hhmm(t)}:${pad2(d.getUTCSeconds())}`; };
const day = (t) => new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const icons = () => { try { window.lucide && window.lucide.createIcons(); } catch { /* icons are decoration */ } };
const BLUE = '#1F6FEB', ORANGE = '#E8590C', RED = '#D1242F', INK = '#292929';

// ---------------------------------------------------------------------------------------------
// Fallback scenario (fixed coordinates and times, all illustrative). 2026-11-11, UTC.
const T0 = 1794355200;
const at = (h, m) => T0 + h * 3600 + m * 60;
const FALLBACK = {
  vessel: { id: 'D-04', name: 'KAIROS STAR', flag: 'Cook Islands', mmsi: 538009876, imo: '9329760', type: 'tanker', length_m: 228 },
  cable: { alias: 'Cable A', name: 'Estlink 2 (approx. route)', owner: 'Grid operator (illustrative)',
    lines: [[[25.55, 60.33], [25.72, 60.14], [25.98, 59.96], [26.28, 59.79], [26.58, 59.64], [26.88, 59.50], [27.02, 59.43]]] },
  claimedWaypoints: [[at(3, 41), 27.10, 59.775], [at(4, 35), 26.80, 59.752], [at(5, 18), 26.53, 59.722], [at(5, 41), 26.4525, 59.7113]],
  drag: [[at(5, 41), 26.4525, 59.7113], [at(5, 55), 26.4299, 59.7119], [at(6, 10), 26.4248, 59.7214], [at(6, 25), 26.4052, 59.7236],
    [at(6, 40), 26.4001, 59.7331], [at(6, 55), 26.3840, 59.7355], [at(7, 15), 26.3779, 59.7430]],
  sar: [
    { id: 'SAR-0193', t: at(5, 58), lon: 26.4294, lat: 59.7136, length_m: 230 },
    { id: 'SAR-0201', t: at(6, 34), lon: 26.4027, lat: 59.7290, length_m: 226 },
    { id: 'SAR-0214', t: at(7, 9), lon: 26.3801, lat: 59.7405, length_m: 232 },
  ],
  rf: [
    { id: 'RF-0441', t: at(6, 5), lon: 26.4270, lat: 59.7190, band: 'X-band navigation radar' },
    { id: 'RF-0447', t: at(6, 41), lon: 26.3985, lat: 59.7338, band: 'X-band navigation radar' },
    { id: 'RF-0452', t: at(7, 12), lon: 26.3772, lat: 59.7425, band: 'X-band navigation radar' },
  ],
  dark: { start: at(5, 41), end: at(7, 15), minutes: 94 },
  risk: { score: 87, level: 'high' },
  zoneName: 'Cable A corridor',
  corridorKm: 2,
  events: [
    { id: 'E-0101', vessel_id: 'D-04', type: 'zone_entry', t: at(5, 9), end: null, lon: 26.58, lat: 59.727, severity: 'low', summary: 'Entered the Cable A corridor at 10 kn.', facts: [], source: 'ais', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
    { id: 'E-0102', vessel_id: 'D-04', type: 'dark_period', t: at(5, 41), end: at(7, 15), lon: 26.4525, lat: 59.7113, severity: 'high', summary: 'AIS went silent inside the Cable A corridor for 94 min.', facts: ['Last broadcast 05:41 UTC'], source: 'ais', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
    { id: 'E-0103', vessel_id: 'D-04', type: 'position_mismatch', t: at(5, 58), end: null, lon: 26.4294, lat: 59.7136, severity: 'high', summary: 'Satellite radar saw a 230 m hull with no AIS match over Cable A.', facts: ['SAR-0193'], source: 'sar', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
    { id: 'E-0104', vessel_id: 'D-04', type: 'position_mismatch', t: at(6, 5), end: null, lon: 26.4270, lat: 59.7190, severity: 'medium', summary: 'X-band navigation radar emitting from the dark hull position.', facts: ['RF-0441'], source: 'rf', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
    { id: 'E-0105', vessel_id: 'D-04', type: 'closest_approach_to_asset', t: at(6, 10), end: null, lon: 26.4248, lat: 59.7214, severity: 'high', summary: 'Fused track crossed Cable A.', facts: [], source: 'fusion', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
    { id: 'E-0106', vessel_id: 'D-04', type: 'anchor_drag_pattern', t: at(6, 40), end: at(7, 15), lon: 26.4001, lat: 59.7331, severity: 'high', summary: 'Slow zigzag along the cable, consistent with a dragged anchor.', facts: [], source: 'fusion', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null },
  ],
  alert: null, // built in derive() from the numbers above
};

function resample(wps, step) {
  const out = [];
  for (let t = wps[0][0]; t <= wps[wps.length - 1][0]; t += step) out.push([t, ...posAt(wps, t)]);
  const last = wps[wps.length - 1];
  if (out[out.length - 1][0] !== last[0]) out.push([last[0], last[1], last[2]]);
  return out;
}
function posAt(track, t) {
  if (t <= track[0][0]) return [track[0][1], track[0][2]];
  for (let i = 0; i < track.length - 1; i++) {
    const a = track[i], b = track[i + 1];
    if (t <= b[0]) { const u = (t - a[0]) / Math.max(1, b[0] - a[0]); return [a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u]; }
  }
  const z = track[track.length - 1]; return [z[1], z[2]];
}
function buildFallback() {
  const f = structuredClone(FALLBACK);
  return { ...f, source: 'fallback', claimed: resample(f.claimedWaypoints, 240) };
}

// Normalise a World from the API into the stage's data, or return null when D-04 is not usable.
function fromWorld(w) {
  const v = (w?.vessels || []).find((x) => x.id === 'D-04');
  if (!v) return null;
  const dp = (v.dark_periods || []).slice().sort((a, b) => (b.minutes || 0) - (a.minutes || 0))[0];
  if (!dp) return null;
  const end = dp.end ?? w.scenario?.now ?? dp.start + 5400;
  const claimed = (v.claimed || []).filter((p) => p[0] <= dp.start + 60 && p[0] >= dp.start - 2.5 * 3600).map((p) => [p[0], p[1], p[2]]);
  const drag = (v.track || []).filter((p) => p[0] >= dp.start - 60 && p[0] <= end + 60).map((p) => [p[0], p[1], p[2]]);
  const near = (o) => o.t >= dp.start - 1800 && o.t <= end + 1800;
  let sar = (v.observed || []).filter(near); if (!sar.length) sar = v.observed || [];
  let rf = (v.emitted || []).filter(near); if (!rf.length) rf = v.emitted || [];
  const asset = (w.assets || []).find((a) => a.id === 'cable-a') || (w.assets || []).find((a) => a.protected);
  if (claimed.length < 4 || drag.length < 2 || !sar.length || !asset?.geometry) return null;
  const g = asset.geometry;
  const lines = g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : null;
  if (!lines?.length) return null;
  const alert = (w.alerts || []).find((a) => a.vessel_id === 'D-04') || null;
  const zone = (w.zones || []).find((z) => z.id === (alert?.zone_id || 'Z-1'));
  return {
    source: 'api',
    vessel: { id: v.id, name: v.name, flag: v.flag, mmsi: v.mmsi, imo: v.imo, type: v.type || 'tanker', length_m: v.length_m },
    cable: { alias: asset.alias || 'Cable A', name: asset.name || '', owner: asset.owner || '', lines },
    claimed, drag,
    sar: sar.map((o) => ({ id: o.id, t: o.t, lon: o.lon, lat: o.lat, length_m: o.length_m })).sort((a, b) => a.t - b.t),
    rf: rf.map((o) => ({ id: o.id, t: o.t, lon: o.lon, lat: o.lat, band: o.band })).sort((a, b) => a.t - b.t),
    dark: { start: dp.start, end, minutes: dp.minutes ?? Math.round((end - dp.start) / 60) },
    risk: v.risk || { score: 87, level: 'high' },
    zoneName: zone?.name || 'Cable A corridor',
    corridorKm: (zone?.geometry?.width_km || 4) / 2,
    events: (w.events || []).filter((e) => e.vessel_id === 'D-04').sort((a, b) => a.t - b.t || String(a.id).localeCompare(b.id)),
    alert: alert && { id: alert.id, t: alert.t, severity: alert.severity, headline: alert.headline, facts: alert.facts || [],
      actions: alert.actions || [], recipients: alert.recipients || [] },
  };
}

// ---------------------------------------------------------------------------------------------
// Geometry in a local km plane (equirectangular around the incident).
let P = null;
function proj(lat0) {
  const kx = 111.32 * Math.cos((lat0 * Math.PI) / 180), ky = 110.57;
  return { to: ([lon, lat]) => [lon * kx, lat * ky], from: ([x, y]) => [x / kx, y / ky] };
}
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const lerp2 = (a, b, u) => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
function projectOn(line, p) {
  let best = { d: Infinity, along: 0 }, acc = 0;
  for (let i = 0; i < line.length - 1; i++) {
    const a = line[i], b = line[i + 1], vx = b[0] - a[0], vy = b[1] - a[1], L2 = vx * vx + vy * vy, L = Math.sqrt(L2);
    const u = L2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2)) : 0;
    const d = dist(p, [a[0] + vx * u, a[1] + vy * u]);
    if (d < best.d) best = { d, along: acc + u * L };
    acc += L;
  }
  return best;
}
function sliceLine(line, d0, d1) {
  const out = []; let acc = 0;
  for (let i = 0; i < line.length - 1; i++) {
    const a = line[i], b = line[i + 1], L = dist(a, b);
    if (L > 0 && acc + L >= d0 && acc <= d1) {
      const pa = lerp2(a, b, Math.max(0, (d0 - acc) / L)), pb = lerp2(a, b, Math.min(1, (d1 - acc) / L));
      if (!out.length) out.push(pa);
      out.push(pb);
    }
    acc += L;
  }
  return out;
}
function corridorRing(line, w) {
  const norm = (a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1; return [-dy / L, dx / L]; };
  const left = [], right = [];
  line.forEach((p, i) => {
    const n1 = i > 0 ? norm(line[i - 1], p) : null, n2 = i < line.length - 1 ? norm(p, line[i + 1]) : null;
    let n = n1 && n2 ? [n1[0] + n2[0], n1[1] + n2[1]] : n1 || n2;
    const L = Math.hypot(n[0], n[1]) || 1; n = [n[0] / L, n[1] / L];
    left.push([p[0] + n[0] * w, p[1] + n[1] * w]); right.push([p[0] - n[0] * w, p[1] - n[1] * w]);
  });
  const ring = [...left, ...right.reverse()];
  ring.push(ring[0]);
  return ring;
}
const pathKm = (xy) => xy.slice(1).reduce((s, p, i) => s + dist(xy[i], p), 0);

// Everything the beats show, derived once from either data source.
function derive(D) {
  P = proj(D.drag[0][2]);
  const ll = (p) => [p[1], p[2]];
  const dragXY = D.drag.map((p) => P.to(ll(p)));
  let best = null;
  for (const ln of D.cable.lines) {
    const xy = ln.map(P.to), pr = projectOn(xy, dragXY[0]);
    if (!best || pr.d < best.d) best = { ln, xy, d: pr.d };
  }
  D.cableLine = best.ln;
  const along = dragXY.map((p) => projectOn(best.xy, p).along);
  D.cableHl = sliceLine(best.xy, Math.min(...along) - 1.5, Math.max(...along) + 1.5).map(P.from);
  D.corridor = corridorRing(best.xy, D.corridorKm).map(P.from);
  D.sarDistKm = Math.min(...D.sar.map((o) => projectOn(best.xy, P.to([o.lon, o.lat])).d));
  const hours = Math.max(1 / 60, (D.drag[D.drag.length - 1][0] - D.drag[0][0]) / 3600);
  D.dragKn = pathKm(dragXY) / hours / 1.852;
  D.lastAis = D.claimed[D.claimed.length - 1];
  D.hullM = Math.round((D.sar[0].length_m || D.vessel.length_m || 230) / 10) * 10;
  D.claimedLL = D.claimed.map(ll);
  D.dragLL = D.drag.map(ll);
  const distTxt = D.sarDistKm < 0.1 ? '<0.1' : D.sarDistKm.toFixed(1);
  D.distTxt = distTxt;
  D.alert = D.alert || {
    id: 'A-0003', t: D.dark.end, severity: 'high',
    headline: `Dark tanker dragging a likely anchor over ${D.cable.alias}`,
    facts: [`AIS silent ${D.dark.minutes} min`, `Radar: ${D.hullM} m hull ${distTxt} km from the cable`, 'Nav radar still emitting',
      `Speed ${D.dragKn.toFixed(1)} kn, heading swings 70°`],
    actions: [], recipients: [],
  };
  const A = D.alert;
  if (!A.actions?.length) A.actions = [{ id: 'dispatch_patrol', label: 'Dispatch nearest patrol' }, { id: 'forward_owner', label: 'Forward to cable owner' }, { id: 'dismiss', label: 'Not suspicious' }];
  if (!A.recipients?.length) A.recipients = [{ name: 'Coast guard duty officer', channel: 'push' }, { name: 'Grid operator control room', channel: 'email' }];
  D.opT = A.t + 120;
  D.opEvent = { id: 'E-OP-0001', vessel_id: D.vessel.id, type: 'operator_action', t: D.opT, end: null, lon: D.drag[D.drag.length - 1][1],
    lat: D.drag[D.drag.length - 1][2], severity: 'high', summary: `${A.actions[0].label}, by ${A.recipients[0].name}.`, facts: ['ACT-0001'],
    source: 'operator', zone_id: 'Z-1', asset_id: 'cable-a', other_vessel_id: null };
  D.recordEvents = [...D.events, D.opEvent];
  const sarLast = D.sar[D.sar.length - 1];
  D.rows = [
    { t: D.lastAis[0], src: 'AIS', tone: 'blue', text: 'Last broadcast in the corridor, then silence' },
    { t: D.sar[0].t, src: 'Satellite radar', tone: 'orange', text: `${D.hullM} m hull, no AIS match, ${distTxt} km from cable` },
    D.rf[0] && { t: D.rf[0].t, src: 'RF', tone: 'orange', text: 'X-band navigation radar at the same position' },
    D.sar.length > 1 && { t: sarLast.t, src: 'Satellite radar', tone: 'orange', text: `Still over the cable, zigzag at ${D.dragKn.toFixed(1)} kn` },
    { t: D.opT, src: 'Operator action', tone: 'ink', text: `${A.actions[0].label} (ACT-0001)` },
  ].filter(Boolean).sort((a, b) => a.t - b.t);
  D.generated = Math.max(D.opT, ...D.rows.map((r) => r.t)) + 60;
  return D;
}

// Canonical JSON (sorted keys, no spaces, ASCII escapes like Python's json.dumps) and sha256.
function canon(v) {
  const s = (x) => JSON.stringify(x).replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  if (v === null || typeof v !== 'object') return s(v);
  if (Array.isArray(v)) return '[' + v.map(canon).join(',') + ']';
  return '{' + Object.keys(v).filter((k) => v[k] !== undefined).sort().map((k) => s(k) + ':' + canon(v[k])).join(',') + '}';
}
async function sha256(str) {
  if (!window.crypto?.subtle) return null;
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return 'sha256:' + [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// ---------------------------------------------------------------------------------------------
// Map
let map = null, mapReady = false;
const EMPTY = { type: 'FeatureCollection', features: [] };
const fc = (features) => ({ type: 'FeatureCollection', features });
const lineF = (coords) => (coords.length > 1 ? fc([{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } }]) : EMPTY);
const ptsF = (coords) => fc(coords.map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } })));
const markers = new Set();

function initMap() {
  return new Promise((resolve) => {
    if (!window.maplibregl) return resolve(false);
    try {
      map = new maplibregl.Map({
        container: 'dm-map', style: 'https://tiles.openfreemap.org/styles/positron', center: [26.6, 59.75], zoom: 9.5,
        attributionControl: { compact: true }, keyboard: false, fadeDuration: 0, maxPitch: 0, dragRotate: false,
      });
      map.on('load', () => {
        try { addLayers(); mapReady = true; } catch (e) { console.warn('demo layers', e); }
        resolve(mapReady);
        // The map arrived after boot gave up waiting: redraw whatever beat is on screen.
        if (window.__demo?.ready && mapReady) (cur >= 0 ? play(cur) : reset());
      });
      setTimeout(() => resolve(mapReady), 15000);
    } catch (e) { console.warn('demo map', e); resolve(false); }
  });
}
function addLayers() {
  for (const id of ['dm-corridor', 'dm-cable', 'dm-cable-hl', 'dm-ais', 'dm-ais-pts', 'dm-drag']) map.addSource(id, { type: 'geojson', data: EMPTY });
  const round = { 'line-cap': 'round', 'line-join': 'round' };
  const tr = { duration: 700, delay: 0 };
  map.addLayer({ id: 'dm-corridor-fill', type: 'fill', source: 'dm-corridor', paint: { 'fill-color': BLUE, 'fill-opacity': 0.035 } });
  map.addLayer({ id: 'dm-corridor-line', type: 'line', source: 'dm-corridor', paint: { 'line-color': BLUE, 'line-opacity': 0.3, 'line-width': 1, 'line-dasharray': [3, 3] } });
  map.addLayer({ id: 'dm-cable-casing', type: 'line', source: 'dm-cable', layout: round, paint: { 'line-color': '#FFFFFF', 'line-width': 6 } });
  map.addLayer({ id: 'dm-cable-line', type: 'line', source: 'dm-cable', layout: round, paint: { 'line-color': INK, 'line-width': 2, 'line-opacity': 0.85 } });
  map.addLayer({ id: 'dm-cable-label', type: 'symbol', source: 'dm-cable',
    layout: { 'symbol-placement': 'line', 'text-field': `${D.cable.alias} · ${D.cable.name}`.replace(/ · $/, ''), 'text-font': ['Noto Sans Regular'],
      'text-size': 12, 'text-offset': [0, -1.1], 'symbol-spacing': 320, 'text-max-angle': 30 },
    paint: { 'text-color': INK, 'text-halo-color': '#FFFFFF', 'text-halo-width': 2 } });
  map.addLayer({ id: 'dm-cable-hl-glow', type: 'line', source: 'dm-cable-hl', layout: round, paint: { 'line-color': RED, 'line-width': 18, 'line-blur': 6, 'line-opacity': 0, 'line-opacity-transition': tr } }, 'dm-cable-casing');
  map.addLayer({ id: 'dm-cable-hl', type: 'line', source: 'dm-cable-hl', layout: round, paint: { 'line-color': RED, 'line-width': 8, 'line-opacity': 0, 'line-opacity-transition': tr } }, 'dm-cable-casing');
  map.addLayer({ id: 'dm-ais-line', type: 'line', source: 'dm-ais', layout: round, paint: { 'line-color': BLUE, 'line-width': 2.5, 'line-opacity': 0.9, 'line-opacity-transition': tr } });
  map.addLayer({ id: 'dm-ais-pts', type: 'circle', source: 'dm-ais-pts', paint: { 'circle-radius': 3.5, 'circle-color': BLUE, 'circle-stroke-color': '#FFFFFF',
    'circle-stroke-width': 1.5, 'circle-opacity': 1, 'circle-stroke-opacity': 1, 'circle-opacity-transition': tr, 'circle-stroke-opacity-transition': tr } });
  map.addLayer({ id: 'dm-drag-glow', type: 'line', source: 'dm-drag', layout: round, paint: { 'line-color': RED, 'line-width': 16, 'line-blur': 5, 'line-opacity': 0, 'line-opacity-transition': tr } });
  map.addLayer({ id: 'dm-drag-line', type: 'line', source: 'dm-drag', layout: round, paint: { 'line-color': ORANGE, 'line-width': 4.5, 'line-color-transition': tr } });
  map.getSource('dm-cable').setData(lineF(D.cableLine));
  map.getSource('dm-corridor').setData(fc([{ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [D.corridor] } }]));
  map.getSource('dm-cable-hl').setData(lineF(D.cableHl));
}
function setData(id, data) { if (mapReady) map.getSource(id)?.setData(data); }
function paint(layer, prop, val, I) {
  if (!mapReady) return;
  try { map.setPaintProperty(layer, `${prop}-transition`, { duration: I ? 0 : 700, delay: 0 }); map.setPaintProperty(layer, prop, val); } catch { /* ignore */ }
}
function marker(html, cls, lnglat) {
  if (!mapReady) return null;
  const el = document.createElement('div');
  el.className = `dm-mk ${cls || ''}`;
  el.innerHTML = html;
  const m = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(lnglat).addTo(map);
  markers.add(m);
  return m;
}
function clearMarkers() { markers.forEach((m) => m.remove()); markers.clear(); }

const bboxOf = (coords) => coords.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [Infinity, Infinity, -Infinity, -Infinity]);
function paperBox() { const w = Math.min(560 * paperScale(), innerWidth * 0.5); return w; }
const VIEWS = {
  start: () => ({ pts: [...D.claimedLL, ...D.dragLL], pad: { top: 120, bottom: 150, left: 440, right: 90 }, maxZoom: 10.8, duration: 1400 }),
  close: () => ({ pts: [...D.dragLL, D.claimedLL[D.claimedLL.length - 1], ...D.sar.map((o) => [o.lon, o.lat])], pad: { top: 150, bottom: 180, left: 470, right: 260 }, maxZoom: 11.9, duration: 2200 }),
  alert: () => ({ pts: [...D.dragLL, D.claimedLL[D.claimedLL.length - 1]], pad: { top: 140, bottom: 160, left: 360, right: 640 }, maxZoom: 11.9, duration: 1500 }),
  record: () => ({ pts: [...D.claimedLL.slice(-Math.ceil(D.claimedLL.length / 2)), ...D.dragLL], pad: { top: 230, bottom: 360, left: 120, right: paperBox() + 120 }, maxZoom: 11.2, duration: 1600 }),
};
function view(name, I) {
  if (!mapReady) return;
  const v = VIEWS[name](), b = bboxOf(v.pts);
  // Keep padding valid on small screens.
  const sx = Math.min(1, (innerWidth - 160) / (v.pad.left + v.pad.right)), sy = Math.min(1, (innerHeight - 120) / (v.pad.top + v.pad.bottom));
  const pad = { top: v.pad.top * sy, bottom: v.pad.bottom * sy, left: v.pad.left * sx, right: v.pad.right * sx };
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: pad, maxZoom: v.maxZoom, duration: I ? 0 : v.duration, essential: true });
}

// ---------------------------------------------------------------------------------------------
// Timeline engine: every beat is a list of [ms, step(instant)]. Jumping to beat N runs the steps
// of earlier beats instantly, so any beat can be replayed on its own and the result is identical.
let D = null, gen = 0, cur = -1;
const timers = new Set();
function later(ms, fn) {
  const g = gen;
  const id = setTimeout(() => { timers.delete(id); if (g === gen) fn(); }, ms);
  timers.add(id);
}
function clearTimers() { timers.forEach(clearTimeout); timers.clear(); }
function animate(ms, fn, I) {
  if (I || ms <= 0) { fn(1); return; }
  const g = gen, t0 = performance.now();
  const tick = (now) => { if (g !== gen) return; const k = Math.min(1, (now - t0) / ms); fn(k); if (k < 1) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
}
// Run fn(i) for each item, gap ms apart (or all at once when instant).
function seq(I, n, gap, fn) { for (let i = 0; i < n; i++) (I ? fn(i, true) : later(i * gap, () => fn(i, false))); }
function show(el, I, cls = 'in') {
  el.hidden = false;
  if (I) { el.classList.add(cls); return; }
  void el.offsetWidth;
  requestAnimationFrame(() => el.classList.add(cls));
}
const easeOut = (k) => 1 - Math.pow(1 - k, 2.2);

const S = {}; // scene handles for the current run
let lastClock = 0;
function clock(t, force) {
  if (!force && t < lastClock) return;
  lastClock = t;
  $('#dm-clock').innerHTML = `${esc(day(t))} · <b>${hhmm(t)} UTC</b>`;
}
const TITLES = [
  ['Cable A, Gulf of Finland', 'What a ship claims, checked against what space observes.'],
  ['Detect', 'Three layers from space, stacked over one cable.'],
  ['Alert', 'One alert, to the people who can act on it.'],
  ['Prove', 'The same record, as evidence.'],
];
function setTitle(n) {
  const [h, p] = TITLES[n + 1];
  $('#dm-title-h').textContent = h;
  $('#dm-title-p').textContent = p;
  $('#dm-hint').hidden = n >= 0;
  const t = $('#dm-title');
  t.classList.remove('swap'); void t.offsetWidth; t.classList.add('swap');
  layout();
}
function layout() {
  const head = $('.dm-head').getBoundingClientRect();
  $('#dm-stack').style.top = `${Math.round(head.bottom + 12)}px`;
  const s = paperScale();
  document.body.style.setProperty('--dm-paper-scale', s.toFixed(3));
  document.body.style.setProperty('--dm-caption-w', `${Math.max(320, Math.min(560, innerWidth - 560 * s - 96))}px`);
}
function paperScale() {
  const h = $('#dm-paper').offsetHeight || 792;
  return Math.min(1, (innerHeight - 120) / h, (innerWidth * 0.5) / 560);
}

function resetScene() {
  clearMarkers();
  Object.keys(S).forEach((k) => delete S[k]);
  setData('dm-ais', EMPTY); setData('dm-ais-pts', EMPTY); setData('dm-drag', EMPTY);
  paint('dm-cable-hl', 'line-opacity', 0, true); paint('dm-cable-hl-glow', 'line-opacity', 0, true);
  paint('dm-drag-glow', 'line-opacity', 0, true); paint('dm-drag-line', 'line-color', ORANGE, true);
  paint('dm-ais-pts', 'circle-opacity', 1, true); paint('dm-ais-pts', 'circle-stroke-opacity', 1, true); paint('dm-ais-line', 'line-opacity', 0.9, true);
  const stack = $('#dm-stack'); stack.innerHTML = ''; stack.className = 'dm-stack';
  const banner = $('#dm-banner'); banner.hidden = true; banner.classList.remove('in');
  const right = $('#dm-right'); right.innerHTML = ''; right.className = 'dm-right';
  const phone = $('#dm-phone'); phone.hidden = true; phone.className = 'dm-phone'; phone.innerHTML = '';
  const paper = $('#dm-paper'); paper.hidden = true; paper.className = 'dm-paper'; paper.innerHTML = '';
  const cap = $('#dm-caption'); cap.hidden = true; cap.classList.remove('in');
  document.querySelectorAll('.dm-sweep').forEach((e) => e.remove());
  $('#dm-flash').classList.remove('go');
  clock(D.claimed[0][0], true);
  view('start', true);
}

// --- Beat 1: Detect ---------------------------------------------------------------------------
function layerCard(key, tone, icon, k, state, main, sub) {
  const el = document.createElement('div');
  el.className = 'dm-layer';
  el.dataset.k = key;
  el.innerHTML = `<span class="dm-ico ${tone}"><i data-lucide="${icon}"></i></span>
    <div><div class="dm-layer-top"><span class="dm-k">${esc(k)}</span><span class="dm-pill dm-state"><span class="dm-dot ${tone}"></span><span>${esc(state)}</span></span></div>
    <p class="dm-layer-main">${esc(main)}</p><p class="dm-layer-sub">${esc(sub)}</p></div>`;
  return el;
}
function addLayer(el, I) {
  const stack = $('#dm-stack');
  stack.querySelectorAll('.dm-layer').forEach((c) => c.classList.remove('now'));
  stack.appendChild(el);
  icons();
  el.classList.add('now');
  show(el, I);
}
function updateCard(key, { state, tone, main, sub }, I) {
  const el = $(`.dm-layer[data-k="${key}"]`);
  if (!el) return;
  if (state) { el.querySelector('.dm-state span:last-child').textContent = state; }
  if (tone) { el.querySelector('.dm-state .dm-dot').className = `dm-dot ${tone}`; }
  for (const [sel, val] of [['.dm-layer-main', main], ['.dm-layer-sub', sub]]) {
    if (val == null) continue;
    const p = el.querySelector(sel);
    p.textContent = val;
    if (!I) { p.classList.remove('dm-swap'); void p.offsetWidth; p.classList.add('dm-swap'); }
  }
}
function drawAis(k) {
  const C = D.claimed, n = C.length, f = easeOut(k) * (n - 1), i = Math.floor(f), u = f - i;
  const coords = C.slice(0, i + 1).map((p) => [p[1], p[2]]);
  let head = coords[coords.length - 1], t = C[i][0];
  if (i < n - 1) { head = lerp2([C[i][1], C[i][2]], [C[i + 1][1], C[i + 1][2]], u); coords.push(head); t = C[i][0] + (C[i + 1][0] - C[i][0]) * u; }
  setData('dm-ais', lineF(coords));
  setData('dm-ais-pts', ptsF(C.slice(0, i + 1).map((p) => [p[1], p[2]])));
  S.ais?.setLngLat(head);
  clock(t);
  const card = $('.dm-layer[data-k="claimed"] .dm-layer-sub');
  if (card && k < 1) card.textContent = `Position report ${hhmm(t)} UTC · MMSI ${D.vessel.mmsi}`;
}
function beatDetect() {
  const v = D.vessel;
  return [
    [0, (I) => {
      view('start', I);
      addLayer(layerCard('claimed', 'blue', 'radio-tower', 'Claimed · AIS', 'Broadcasting', `${v.name} reports its own position`,
        `Position report ${hhmm(D.claimed[0][0])} UTC · MMSI ${v.mmsi}`), I);
      S.ais = marker(`<span class="dm-vdot"></span><span class="dm-lbl">${esc(v.name)} <span class="muted">· AIS</span></span>`, '', D.claimedLL[0]);
    }],
    [350, (I) => animate(2600, drawAis, I)],
    [3100, (I) => {
      drawAis(1);
      const el = S.ais?.getElement();
      if (el) { el.classList.add('silent'); el.querySelector('.dm-lbl').innerHTML = `Last AIS ${hhmm(D.lastAis[0])} <span class="muted">· then silence</span>`; }
      paint('dm-ais-pts', 'circle-opacity', 0.35, I); paint('dm-ais-pts', 'circle-stroke-opacity', 0.35, I);
      updateCard('claimed', { state: 'Silent', tone: 'orange', main: `Last broadcast ${hhmm(D.lastAis[0])} · then silence`,
        sub: `No AIS for ${D.dark.minutes} min inside the ${D.zoneName}` }, I);
    }],
    [4000, (I) => {
      view('close', I);
      addLayer(layerCard('observed', 'orange', 'satellite', 'Observed · Satellite radar', 'No AIS match', `Tanker-sized hull, ~${D.hullM} m, over the cable`,
        `${D.sar.length} radar detection${D.sar.length > 1 ? 's' : ''} · SAR (simulated)`), I);
    }],
    [5300, (I) => {
      if (I) return;
      const sw = document.createElement('div');
      sw.className = 'dm-sweep';
      sw.innerHTML = `<span class="dm-sweep-tag"><i data-lucide="satellite"></i>Radar pass · ${hhmm(D.sar[0].t)} UTC</span>`;
      document.body.appendChild(sw);
      icons();
      later(1400, () => sw.remove());
    }],
    [5900, (I) => {
      if (!I) { const f = $('#dm-flash'); f.classList.remove('go'); void f.offsetWidth; f.classList.add('go'); }
      seq(I, D.sar.length, 320, (i, inst) => {
        const o = D.sar[i];
        marker(`${inst ? '' : '<span class="dm-burst"></span>'}<span class="dm-dia"></span>`, inst ? '' : 'pop', [o.lon, o.lat]);
        clock(o.t);
      });
    }],
    [7300, (I) => {
      addLayer(layerCard('emitted', 'orange', 'radio', 'Emitted · RF', 'Emitting', 'Its navigation radar is still on',
        D.rf.length ? `${D.rf[0].band || 'X-band navigation radar'} · ${D.rf.length} fix${D.rf.length > 1 ? 'es' : ''} at the hull` : 'X-band navigation radar'), I);
      S.rf = [];
      seq(I, D.rf.length, 220, (i) => { const o = D.rf[i]; S.rf.push(marker('<span class="dm-rf"><i></i><i></i><i></i><b></b></span>', '', [o.lon, o.lat])); clock(o.t); });
    }],
    [8900, (I) => {
      $('.dm-layer.now')?.classList.remove('now');
      show($('#dm-banner'), I);
      S.rf?.forEach((m) => m?.getElement().classList.add('calm'));
      animate(900, (k) => {
        const n = D.dragLL.length, f = k * (n - 1), i = Math.floor(f), u = f - i;
        const coords = D.dragLL.slice(0, i + 1);
        if (i < n - 1) coords.push(lerp2(D.dragLL[i], D.dragLL[i + 1], u));
        setData('dm-drag', lineF(coords));
      }, I);
      paint('dm-cable-hl', 'line-opacity', 0.14, I); paint('dm-cable-hl-glow', 'line-opacity', 0.22, I);
      const fin = (inst) => {
        paint('dm-drag-line', 'line-color', RED, inst); paint('dm-drag-glow', 'line-opacity', 0.3, inst);
        S.dark = marker(`<span class="dm-pulse"></span><span class="dm-dia lg"></span><span class="dm-lbl">${esc(v.id)} · ${esc(v.name)} <span class="muted">· no AIS</span></span>`,
          'alarm', D.dragLL[D.dragLL.length - 1]);
        clock(D.alert.t);
      };
      I ? fin(true) : later(950, () => fin(false));
    }],
  ];
}

// --- Beat 2: Alert ----------------------------------------------------------------------------
function trackSvg({ w, h, focus, lastLabel }) {
  const xyF = focus.map(P.to), b = bboxOf(xyF), pad = 18;
  const cx = (b[0] + b[2]) / 2, cy = (b[1] + b[3]) / 2;
  const s = Math.min((w - 2 * pad) / Math.max(0.2, b[2] - b[0]), (h - 2 * pad) / Math.max(0.2, b[3] - b[1]));
  const S2 = (c) => { const [x, y] = P.to(c); return [(x - cx) * s + w / 2, h / 2 - (y - cy) * s]; };
  const d = (cs) => cs.map((c, i) => (i ? 'L' : 'M') + S2(c).map((n) => n.toFixed(1)).join(' ')).join('');
  const last = S2(D.claimedLL[D.claimedLL.length - 1]), end = S2(D.dragLL[D.dragLL.length - 1]);
  const dia = D.sar.map((o) => { const [x, y] = S2([o.lon, o.lat]); return `<rect class="dm-fade" x="${(x - 3.5).toFixed(1)}" y="${(y - 3.5).toFixed(1)}" width="7" height="7" rx="1" transform="rotate(45 ${x.toFixed(1)} ${y.toFixed(1)})" fill="${ORANGE}" stroke="#fff" stroke-width="1.5"/>`; }).join('');
  const lbl = lastLabel ? `<text class="dm-fade" x="${(last[0] + 8).toFixed(1)}" y="${(last[1] + 16).toFixed(1)}" font-size="12" fill="#5D5D5D" font-family="inherit">Last AIS ${hhmm(D.lastAis[0])}</text>` : '';
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Track: AIS until ${hhmm(D.lastAis[0])}, then a dark track over ${esc(D.cable.alias)}">
    <rect width="${w}" height="${h}" fill="#FAFAFA"/>
    <path d="${d(D.corridor)}" fill="${BLUE}" fill-opacity=".06" stroke="none"/>
    <path d="${d(D.cableLine)}" fill="none" stroke="${INK}" stroke-width="1.5" stroke-opacity=".8"/>
    <path d="${d(D.cableHl)}" fill="none" stroke="${RED}" stroke-width="7" stroke-opacity=".16" stroke-linecap="round"/>
    <path class="dm-draw" pathLength="1" d="${d(D.claimedLL)}" fill="none" stroke="${BLUE}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    <path class="dm-draw d2" pathLength="1" d="${d(D.dragLL)}" fill="none" stroke="${RED}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3.5" fill="#fff" stroke="${BLUE}" stroke-width="2"/>
    ${dia}
    <rect class="dm-fade" x="${(end[0] - 5).toFixed(1)}" y="${(end[1] - 5).toFixed(1)}" width="10" height="10" rx="1.5" transform="rotate(45 ${end[0].toFixed(1)} ${end[1].toFixed(1)})" fill="${RED}" stroke="#fff" stroke-width="1.5"/>
    ${lbl}
  </svg>`;
}
const factTone = (f) => (/AIS/i.test(f) ? 'blue' : /speed|heading|kn\b/i.test(f) ? 'red' : 'orange');
function renderAlert() {
  const A = D.alert, v = D.vessel, [p0, p1, p2] = A.actions;
  const right = $('#dm-right');
  right.innerHTML = `
  <div class="dm-alert" id="dm-alert">
    <div class="dm-alert-top">
      <span class="dm-ico red"><i data-lucide="siren"></i></span>
      <div><div class="dm-k">Alert ${esc(A.id)} · ${esc(D.zoneName)}</div><div class="dm-s">${hhmm(A.t)} UTC · needs a decision</div></div>
      <span class="dm-pill"><span class="dm-dot red"></span>${esc((A.severity || 'high').replace(/^./, (c) => c.toUpperCase()))} · ${esc(D.risk?.score ?? 87)}</span>
    </div>
    <h2 class="dm-alert-h">${esc(A.headline)}</h2>
    <p class="dm-alert-v">${esc(v.name)} · ${esc(v.id)} · ${esc(v.flag)} · ${esc(v.type)} ${esc(v.length_m)} m</p>
    <ul class="dm-facts">${A.facts.slice(0, 4).map((f) => `<li><span class="dm-dot ${factTone(f)}"></span><span>${esc(f)}</span></li>`).join('')}</ul>
    <div class="dm-mini" id="dm-mini">${trackSvg({ w: 358, h: 120, focus: [...D.dragLL, D.claimedLL[D.claimedLL.length - 1], ...D.claimedLL.slice(-6)] })}</div>
    <div class="dm-actions">
      <button class="dm-act primary" data-act="0"><i data-lucide="ship"></i><span>${esc(p0.label)}</span></button>
      <div class="dm-act-row">
        ${p1 ? `<button class="dm-act" data-act="1"><i data-lucide="forward"></i><span>${esc(p1.label)}</span></button>` : ''}
        ${p2 ? `<button class="dm-act ghost" data-act="2"><span>${esc(p2.label)}</span></button>` : ''}
      </div>
      <p class="dm-act-log" id="dm-act-log" hidden></p>
    </div>
  </div>
  <div class="dm-deliv" id="dm-deliv">
    <p class="dm-deliv-h"><i data-lucide="send"></i><span>Sent to <b>${A.recipients.map((r) => esc(r.name)).join('</b> · <b>')}</b></span></p>
    ${A.recipients.slice(0, 2).map((r, i) => `
      <div class="dm-row" data-i="${i}">
        <span class="dm-ico sm"><i data-lucide="${r.channel === 'email' ? 'mail' : r.channel === 'webhook' ? 'webhook' : 'smartphone'}"></i></span>
        <div><div class="w">${esc(r.channel === 'email' ? 'Email' : r.channel === 'push' ? 'Push' : r.channel)}</div><div class="dm-s">${esc(r.name)}</div></div>
        <span class="dm-tick"><span class="t">Sending</span><span class="dm-spin"></span><span class="dm-check"><i data-lucide="check"></i></span></span>
      </div>`).join('')}
  </div>`;
  icons();
  right.querySelectorAll('.dm-act').forEach((b) => b.addEventListener('click', () => actionDone(Number(b.dataset.act), false)));
}
function renderPhone() {
  const A = D.alert, v = D.vessel;
  $('#dm-phone').innerHTML = `<div class="dm-screen">
    <span class="dm-island"></span>
    <div class="dm-status"><span>${hhmm(A.t)}</span><span><i data-lucide="signal"></i><i data-lucide="battery-full"></i></span></div>
    <div class="dm-lock"><div class="d">${esc(new Date(A.t * 1000).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }))}</div><div class="t">${hhmm(A.t)}</div></div>
    <div class="dm-notif" id="dm-notif">
      <div class="dm-notif-top"><span class="dm-logo sm"></span><b>Skynet</b><span>now</span></div>
      <p class="dm-notif-h">${esc(A.headline)}</p>
      <p class="dm-notif-b">${esc(v.name)} · ${esc(A.facts[0] || '')}. Tap to dispatch or forward.</p>
    </div>
    <span class="dm-home"></span>
  </div>`;
  icons();
}
function deliver(i, I) {
  const row = $(`.dm-row[data-i="${i}"]`);
  if (!row) return;
  row.classList.add('ok');
  row.querySelector('.t').textContent = `Delivered ${hhmmss(D.alert.t + 4 + i * 3)}`;
  if (i === 0) { const n = $('#dm-notif'); n && show(n, I); }
}
function actionDone(idx, I) {
  const btns = [...document.querySelectorAll('#dm-alert .dm-act')];
  const b = btns.find((x) => Number(x.dataset.act) === idx);
  if (!b || b.disabled || btns.some((x) => x.classList.contains('done'))) return;
  b.classList.remove('press');
  b.classList.add('done');
  if (idx === 0) { b.innerHTML = '<i data-lucide="check"></i><span>Patrol dispatched</span>'; }
  btns.forEach((x) => { if (x !== b) x.disabled = true; });
  const log = $('#dm-act-log');
  log.innerHTML = `<i data-lucide="circle-check"></i>Logged to the vessel record · ACT-0001 · ${hhmm(D.opT)} UTC`;
  log.hidden = false;
  icons();
  clock(D.opT, true);
}
function beatAlert() {
  return [
    [0, (I) => {
      $('#dm-stack').classList.add('out');
      const bn = $('#dm-banner'); bn.classList.remove('in'); if (I) bn.hidden = true; else later(500, () => { bn.hidden = true; });
      view('alert', I);
      renderAlert();
      clock(D.alert.t, true);
      show($('#dm-alert'), I);
    }],
    [700, (I) => { $('#dm-alert')?.classList.add('facts'); }],
    [1300, (I) => { const m = $('#dm-mini'); if (I) m.classList.add('drawn'); else requestAnimationFrame(() => m.classList.add('drawn')); }],
    [2300, (I) => show($('#dm-deliv'), I)],
    [2800, (I) => { renderPhone(); show($('#dm-phone'), I); }],
    [3600, (I) => deliver(0, I)],
    [4400, (I) => deliver(1, I)],
    [5800, (I) => { if (!I) $('#dm-alert .dm-act[data-act="0"]')?.classList.add('press'); }],
    [6200, (I) => actionDone(0, I)],
  ];
}

// --- Beat 3: Prove ----------------------------------------------------------------------------
function renderPaper() {
  const v = D.vessel, A = D.alert;
  const srcTone = { blue: 'blue', orange: 'orange', ink: 'ink' };
  $('#dm-paper').innerHTML = `
    <div class="dp-top"><span class="dm-logo sm"></span><b>Skynet</b><span>Evidence record R-0001</span><span class="r">Generated ${esc(day(D.generated))} · ${hhmm(D.generated)} UTC</span></div>
    <h2 class="dp-title">Cable incident record</h2>
    <dl class="dp-meta">
      <dt>Vessel</dt><dd>${esc(v.name)} · ${esc(v.id)} · IMO ${esc(v.imo)} · ${esc(v.flag)}</dd>
      <dt>Asset</dt><dd>${esc(D.cable.alias)} · ${esc(D.cable.name)}</dd>
      <dt>Period</dt><dd>${esc(day(D.claimed[0][0]))}, ${hhmm(D.claimed[0][0])}–${hhmm(D.generated - 60)} UTC · for ${esc(A.recipients[0].name)}</dd>
    </dl>
    <p class="dp-sum">${esc(v.name)}, a ${esc(v.length_m)} m ${esc(v.type)}, stopped broadcasting AIS at ${hhmm(D.lastAis[0])} UTC inside the ${esc(D.zoneName)}.
      For ${D.dark.minutes} minutes satellite radar kept seeing a ${D.hullM} m hull with no AIS over the cable, and its navigation radar was still emitting.
      The fused track is a slow zigzag along the cable, consistent with a dragged anchor. ${esc(A.actions[0].label.replace(/^Dispatch/, 'The'))} was dispatched at ${hhmm(D.opT)} UTC.</p>
    <div class="dp-sec"><i data-lucide="list"></i>Timeline</div>
    <table class="dp-tl">
      <thead><tr><th>UTC</th><th>Source</th><th>Observation</th></tr></thead>
      <tbody>${D.rows.map((r) => `<tr><td>${hhmm(r.t)}</td><td><span class="dp-src"><span class="dm-dot ${srcTone[r.tone]}"></span>${esc(r.src)}</span></td><td>${esc(r.text)}</td></tr>`).join('')}</tbody>
    </table>
    <div class="dp-sec"><i data-lucide="map"></i>Exhibit A · Track over ${esc(D.cable.alias)}</div>
    <div class="dp-ex" id="dp-ex">${trackSvg({ w: 486, h: 128, focus: [...D.claimedLL, ...D.dragLL], lastLabel: true })}</div>
    <div class="dp-legend">
      <span><i style="background:${BLUE}"></i>AIS, as claimed</span><span><i style="background:${RED}"></i>Fused track while dark</span>
      <span><i class="dia"></i>Radar detection</span><span><i style="background:${INK}"></i>${esc(D.cable.alias)}</span>
    </div>
    <div class="dp-fp">
      <div class="dp-sec"><i data-lucide="fingerprint"></i>Record fingerprint</div>
      <code id="dp-hash"></code>
      <p id="dp-hash-note"><i data-lucide="shield-check"></i>sha256 of the canonical event list, ${D.recordEvents.length} events · /v1/vessels/${esc(v.id)}/record</p>
    </div>`;
  icons();
}
function typeHash(I) {
  const code = $('#dp-hash');
  if (!code) return;
  const h = D.fingerprint || 'sha256: unavailable (needs a secure context)';
  animate(900, (k) => { code.textContent = h.slice(0, Math.round(h.length * k)); if (k >= 1 && D.fingerprint) code.classList.add('ok'); }, I);
}
function beatProve() {
  return [
    [0, (I) => {
      $('#dm-right').classList.add('out');
      const ph = $('#dm-phone'); ph.classList.remove('in');
      $('#dm-stack').classList.add('out');
      renderPaper();
      const paper = $('#dm-paper');
      paper.hidden = false; layout();
      view('record', I);
      clock(D.generated, true);
      show(paper, I);
      if (I) { $('#dm-right').innerHTML = ''; ph.hidden = true; } else later(900, () => { $('#dm-right').innerHTML = ''; ph.hidden = true; });
    }],
    [900, (I) => $('#dm-paper').classList.add('rows')],
    [1800, (I) => { const ex = $('#dp-ex'); if (I) ex.classList.add('drawn'); else requestAnimationFrame(() => ex.classList.add('drawn')); }],
    [3300, (I) => typeHash(I)],
    [4800, (I) => {
      const cap = $('#dm-caption');
      cap.textContent = 'Every alert and every report comes from one verified vessel record.';
      show(cap, I);
    }],
  ];
}

const BEATS = [
  { dur: 10600, steps: beatDetect },
  { dur: 8000, steps: beatAlert },
  { dur: 8600, steps: beatProve },
];

// ---------------------------------------------------------------------------------------------
const beatBtns = [...document.querySelectorAll('.dm-beat')];
function setBeatUI(n) {
  beatBtns.forEach((b, i) => {
    b.setAttribute('aria-pressed', String(i === n));
    b.classList.toggle('done', i < n);
    const bar = b.querySelector('.dm-prog i');
    bar.style.transition = 'none';
    bar.style.width = i < n ? '100%' : '0%';
    if (i === n) { void bar.offsetWidth; bar.style.transition = `width ${BEATS[n].dur}ms linear`; bar.style.width = '100%'; }
  });
}
function play(n) {
  if (!D) return;
  gen++; clearTimers();
  document.body.classList.add('dm-instant');
  resetScene();
  for (let b = 0; b < n; b++) BEATS[b].steps().forEach(([, fn]) => fn(true));
  void document.body.offsetWidth;
  document.body.classList.remove('dm-instant');
  cur = n;
  setBeatUI(n); setTitle(n);
  window.__demo.beat = n; window.__demo.playing = true; window.__demo.done = false;
  BEATS[n].steps().forEach(([ms, fn]) => later(ms, () => fn(false)));
  later(BEATS[n].dur, () => {
    beatBtns[n].classList.add('done');
    window.__demo.playing = false;
    if (AUTO && n < BEATS.length - 1) later(700, () => play(n + 1));
    else if (n === BEATS.length - 1) window.__demo.done = true;
  });
}
function reset() {
  gen++; clearTimers();
  document.body.classList.add('dm-instant');
  resetScene();
  void document.body.offsetWidth;
  document.body.classList.remove('dm-instant');
  cur = -1; setBeatUI(-1); setTitle(-1);
  Object.assign(window.__demo, { beat: -1, playing: false, done: false });
}

window.__demo = { ready: false, beat: -1, playing: false, done: false, source: null, play, reset };

beatBtns.forEach((b) => b.addEventListener('click', () => play(Number(b.dataset.beat))));
$('#dm-reset').addEventListener('click', reset);
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || !D) return;
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); play(Math.min(cur + 1, BEATS.length - 1)); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); play(Math.max(cur - 1, 0)); }
  else if (['1', '2', '3'].includes(e.key)) play(Number(e.key) - 1);
  else if (e.key === 'r' || e.key === 'R' || e.key === '0' || e.key === 'Escape') reset();
});
addEventListener('resize', layout);

async function loadWorld() {
  if (FORCE_FALLBACK) return null;
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), 2500);
  try {
    const r = await fetch('/api/world', { signal: ctl.signal });
    return r.ok ? await r.json() : null;
  } catch { return null; } finally { clearTimeout(tm); }
}

async function boot() {
  icons();
  const w = await loadWorld();
  let data = null;
  try { data = w && fromWorld(w); } catch (e) { console.warn('demo: world not usable, using fallback', e); }
  try { D = derive(data || buildFallback()); } catch (e) { console.warn('demo: derive failed, using fallback', e); D = derive(buildFallback()); }
  window.__demo.source = D.source;
  D.fingerprint = await sha256(canon(D.recordEvents)).catch(() => null);
  layout();
  await initMap();
  window.__demo.map = map;
  reset();
  window.__demo.ready = true;
  if (AUTO) {
    if (mapReady) await new Promise((r) => { map.once('idle', r); setTimeout(r, 4000); });
    setTimeout(() => play(0), 600);
  }
}
boot();
