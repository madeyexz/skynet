// Skynet map: base layers (assets, overlays) and the Detect vessel layers by fused state. Owner: D.
// Layer ids: base-* (assets, overlays) and det-* (vessels, radar, RF). Exposes ctx.detMap for detect.js.

const C = {
  blue: '#1F6FEB', orange: '#E8590C', red: '#D1242F', ink: '#292929', ink2: '#5D5D5D', ink3: '#9E9E9E',
  rf: '#8250DF', cable: '#9E9E9E',
};
const TRAIL_S = 20 * 60;          // verified trails
const TRAIL_MISMATCH_S = 60 * 60; // dark / spoofing trails read longer
const AIS_WIN = 10 * 60;          // a vessel counts as broadcasting if it sent AIS in the last 10 min
const SAR_WIN = 60 * 60;          // radar detections shown/counted for 60 min after the pass
const RF_WIN = 30 * 60;           // RF emissions shown/counted for 30 min
const STATIC_NEAR = 45 * 60;      // observed-only vessels show this close to their detection
const MAX_LABELS = 14;

const SRC = ['base-assets', 'base-overlays', 'det-trails', 'det-sar', 'det-rf', 'det-verified', 'det-dark',
  'det-spoof-link', 'det-spoof-ring', 'det-spoof-pos', 'det-halo', 'det-selected', 'det-flash'];
const HIT_LAYERS = ['det-spoof-pos', 'det-dark', 'det-spoof-ring', 'det-verified'];

const fc = (features = []) => ({ type: 'FeatureCollection', features });
const pt = (lon, lat, properties = {}) => ({ type: 'Feature', properties, geometry: { type: 'Point', coordinates: [lon, lat] } });
const ln = (coords, properties = {}) => ({ type: 'Feature', properties, geometry: { type: 'LineString', coordinates: coords } });
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function bisect(arr, t, key = (x) => x[0]) {
  let lo = 0, hi = arr.length - 1;
  if (!arr.length || key(arr[0]) > t) return -1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (key(arr[mid]) <= t) lo = mid; else hi = mid - 1; }
  return lo;
}
function interp(arr, t, maxGap = Infinity) {
  if (!arr || !arr.length) return null;
  const n = arr.length;
  if (t < arr[0][0] - 120 || t > arr[n - 1][0] + 120) return null;
  if (t <= arr[0][0]) return arr[0];
  if (t >= arr[n - 1][0]) return arr[n - 1];
  const i = bisect(arr, t), a = arr[i], b = arr[i + 1] || a;
  if (b[0] - a[0] > maxGap) return null;
  const f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
  return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3], a[4]];
}
function stateAt(v, t) {
  const s = v.states || [];
  for (const x of s) if (t >= x.start && t <= x.end) return x.state;
  return s.length ? s[s.length - 1].state : null;
}
function bearing(a, b) {
  const toR = Math.PI / 180, y = Math.sin((b[0] - a[0]) * toR) * Math.cos(b[1] * toR);
  const x = Math.cos(a[1] * toR) * Math.sin(b[1] * toR) - Math.sin(a[1] * toR) * Math.cos(b[1] * toR) * Math.cos((b[0] - a[0]) * toR);
  return (Math.atan2(y, x) / toR + 360) % 360;
}

// ---------- canvas icons ----------
function icon(size, draw) {
  const r = 2, c = document.createElement('canvas');
  c.width = c.height = size * r;
  const g = c.getContext('2d');
  g.scale(r, r);
  draw(g, size);
  const d = g.getImageData(0, 0, c.width, c.height);
  return { width: c.width, height: c.height, data: new Uint8Array(d.data.buffer) };
}
function shipIcon(color, tick) {
  return icon(24, (g, s) => {
    const m = s / 2;
    if (tick) {
      g.strokeStyle = '#fff'; g.lineWidth = 4; g.lineCap = 'round';
      g.beginPath(); g.moveTo(m, m - 3); g.lineTo(m, 2.5); g.stroke();
      g.strokeStyle = color; g.lineWidth = 2;
      g.beginPath(); g.moveTo(m, m - 3); g.lineTo(m, 2.5); g.stroke();
    }
    g.beginPath(); g.arc(m, m, 4.6, 0, Math.PI * 2);
    g.fillStyle = color; g.fill(); g.lineWidth = 1.8; g.strokeStyle = '#fff'; g.stroke();
  });
}
function diamondIcon(color) {
  return icon(28, (g, s) => {
    const m = s / 2, r = 8.5;
    g.beginPath(); g.moveTo(m, m - r); g.lineTo(m + r, m); g.lineTo(m, m + r); g.lineTo(m - r, m); g.closePath();
    g.fillStyle = color; g.fill(); g.lineJoin = 'round'; g.lineWidth = 2; g.strokeStyle = '#fff'; g.stroke();
  });
}
function ringIcon(color) {
  return icon(34, (g, s) => {
    const m = s / 2;
    g.beginPath(); g.arc(m, m, 12, 0, Math.PI * 2);
    g.fillStyle = 'rgba(209,36,47,0.08)'; g.fill();
    g.setLineDash([3.2, 2.6]); g.lineWidth = 2; g.strokeStyle = color; g.stroke();
    g.setLineDash([]);
    g.beginPath(); g.arc(m, m, 2.4, 0, Math.PI * 2); g.lineWidth = 1.5; g.stroke();
  });
}
function squareIcon(color) {
  return icon(26, (g, s) => {
    const m = s / 2, r = 9, k = 4;
    g.strokeStyle = color; g.lineWidth = 1.6; g.lineCap = 'round';
    // radar "lock" corners
    for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const x = m + sx * r, y = m + sy * r;
      g.beginPath(); g.moveTo(x, y - sy * k); g.lineTo(x, y); g.lineTo(x - sx * k, y); g.stroke();
    }
  });
}
function hatchIcon() {
  return icon(12, (g, s) => {
    g.strokeStyle = 'rgba(232,89,12,0.32)'; g.lineWidth = 1.2;
    g.beginPath();
    for (let i = -s; i <= s * 2; i += 6) { g.moveTo(i, s); g.lineTo(i + s, 0); }
    g.stroke();
  });
}

export function initMap(ctx) {
  const map = ctx.map;
  const S = {
    layers: { claimed: true, observed: true, emitted: true },
    selected: null, flashes: [], listeners: new Set(),
    world: null, idx: null, lastT: null, pendingT: null, raf: 0,
    labels: new Map(), ringLabels: new Map(), assetLabels: [], mismatch: 0,
    states: new Map(), pos: new Map(), counts: {},
  };

  // ---------- images ----------
  const imgs = {
    'det-ship': shipIcon(C.blue, true), 'det-ship-still': shipIcon(C.blue, false),
    'det-ship-red': shipIcon(C.red, true), 'det-ship-red-still': shipIcon(C.red, false),
    'det-diamond': diamondIcon(C.orange), 'det-ring': ringIcon(C.red),
    'det-sar-un': squareIcon(C.orange), 'det-sar-m': squareIcon('#7A7A7A'), 'det-hatch': hatchIcon(),
  };
  for (const [k, v] of Object.entries(imgs)) if (!map.hasImage(k)) map.addImage(k, v, { pixelRatio: 2 });

  // ---------- sources + layers ----------
  for (const id of SRC) if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: fc() });
  const add = (l) => { if (!map.getLayer(l.id)) map.addLayer(l); };
  const font = ['Noto Sans Regular'];

  const isIncident = ['has', 'ref'];  // JMIC incident circles carry a UKMTO/MSCIO reference
  add({ id: 'base-overlay-fill', type: 'fill', source: 'base-overlays', filter: ['!', isIncident], paint: { 'fill-pattern': 'det-hatch', 'fill-opacity': 0.9 } });
  add({ id: 'base-overlay-line', type: 'line', source: 'base-overlays', filter: ['!', isIncident], paint: { 'line-color': C.orange, 'line-opacity': 0.35, 'line-width': 1 } });
  add({ id: 'base-incident-fill', type: 'fill', source: 'base-overlays', filter: isIncident,
    paint: { 'fill-color': ['match', ['get', 'kind'], 'Attack', C.red, C.orange], 'fill-opacity': 0.28 } });
  add({ id: 'base-incident-line', type: 'line', source: 'base-overlays', filter: isIncident,
    paint: { 'line-color': ['match', ['get', 'kind'], 'Attack', C.red, C.orange], 'line-width': 1.2 } });
  add({ id: 'base-assets-casing', type: 'line', source: 'base-assets', filter: ['==', ['get', 'protected'], true],
    layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#fff', 'line-width': 7, 'line-opacity': 0.9 } });
  add({ id: 'base-assets-line', type: 'line', source: 'base-assets', filter: ['!=', ['get', 'protected'], true],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': C.cable, 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.8, 10, 1.6], 'line-dasharray': [3, 2], 'line-opacity': 0.9 } });
  add({ id: 'base-assets-protected', type: 'line', source: 'base-assets', filter: ['==', ['get', 'protected'], true],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': C.ink, 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1.6, 10, 3] } });
  add({ id: 'base-assets-label', type: 'symbol', source: 'base-assets', filter: ['!=', ['get', 'protected'], true],
    layout: { 'symbol-placement': 'line', 'text-field': ['get', 'label'], 'text-font': font, 'text-size': 12, 'text-letter-spacing': 0.02,
      'symbol-spacing': 400, 'text-max-angle': 30 },
    paint: { 'text-color': C.ink2, 'text-halo-color': '#fff', 'text-halo-width': 1.5 } });

  add({ id: 'det-trails', type: 'line', source: 'det-trails', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ['match', ['get', 'state'], 'dark', C.orange, 'spoofing', C.red, C.blue],
      'line-width': ['match', ['get', 'state'], 'verified', 1.2, 2],
      'line-opacity': ['match', ['get', 'state'], 'verified', 0.22, 0.55] } });
  add({ id: 'det-rf', type: 'circle', source: 'det-rf',
    paint: { 'circle-radius': ['interpolate', ['linear'], ['get', 'age'], 0, 6, 1, 14], 'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-color': C.rf, 'circle-stroke-width': 1.5, 'circle-stroke-opacity': ['interpolate', ['linear'], ['get', 'age'], 0, 0.9, 1, 0.15] } });
  add({ id: 'det-rf-core', type: 'circle', source: 'det-rf',
    paint: { 'circle-radius': 2.2, 'circle-color': C.rf, 'circle-opacity': ['interpolate', ['linear'], ['get', 'age'], 0, 0.9, 1, 0.3] } });
  add({ id: 'det-sar', type: 'symbol', source: 'det-sar',
    layout: { 'icon-image': ['case', ['get', 'matched'], 'det-sar-m', 'det-sar-un'], 'icon-allow-overlap': true, 'icon-ignore-placement': true },
    paint: { 'icon-opacity': ['interpolate', ['linear'], ['get', 'age'], 0, 0.95, 1, 0.25] } });
  add({ id: 'det-spoof-link', type: 'line', source: 'det-spoof-link', layout: { 'line-cap': 'round' },
    paint: { 'line-color': C.red, 'line-width': 1.6, 'line-dasharray': [2, 2], 'line-opacity': 0.85 } });
  add({ id: 'det-halo-static', type: 'circle', source: 'det-halo',
    paint: { 'circle-radius': 15, 'circle-color': ['get', 'color'], 'circle-opacity': 0.12, 'circle-blur': 0.2 } });
  add({ id: 'det-halo', type: 'circle', source: 'det-halo',
    paint: { 'circle-radius': 12, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.4 } });
  add({ id: 'det-verified', type: 'symbol', source: 'det-verified',
    layout: { 'icon-image': ['case', ['get', 'moving'], 'det-ship', 'det-ship-still'], 'icon-rotate': ['get', 'cog'],
      'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 5, 0.75, 9, 1] } });
  add({ id: 'det-spoof-ring', type: 'symbol', source: 'det-spoof-ring',
    layout: { 'icon-image': 'det-ring', 'icon-allow-overlap': true, 'icon-ignore-placement': true } });
  add({ id: 'det-spoof-pos', type: 'symbol', source: 'det-spoof-pos',
    layout: { 'icon-image': ['case', ['get', 'moving'], 'det-ship-red', 'det-ship-red-still'], 'icon-rotate': ['get', 'cog'],
      'icon-rotation-alignment': 'map', 'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-size': 1.15 } });
  add({ id: 'det-dark', type: 'symbol', source: 'det-dark',
    layout: { 'icon-image': 'det-diamond', 'icon-allow-overlap': true, 'icon-ignore-placement': true,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 5, 0.8, 9, 1] } });
  add({ id: 'det-selected', type: 'circle', source: 'det-selected',
    paint: { 'circle-radius': 17, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': C.ink, 'circle-stroke-width': 2 } });
  add({ id: 'det-flash', type: 'circle', source: 'det-flash',
    paint: { 'circle-radius': 10, 'circle-color': ['get', 'color'], 'circle-opacity': 0, 'circle-stroke-color': ['get', 'color'],
      'circle-stroke-width': 3, 'circle-stroke-opacity': 0 } });

  const setData = (id, data) => map.getSource(id)?.setData(data);

  // ---------- world indexing ----------
  function indexWorld(w) {
    const vessels = w?.vessels || [];
    const sar = [], rf = [];
    for (const v of vessels) {
      for (const o of v.observed || []) sar.push({ v, o });
      for (const e of v.emitted || []) rf.push({ v, e });
    }
    sar.sort((a, b) => a.o.t - b.o.t);
    rf.sort((a, b) => a.e.t - b.e.t);
    const byId = new Map(vessels.map((v) => [v.id, v]));
    return { vessels, sar, rf, byId, isStatic: w?.scenario?.time_mode === 'static' };
  }

  function drawStatic(w) {
    const assets = (w?.assets || []).map((a) => ({ type: 'Feature', geometry: a.geometry,
      properties: { id: a.id, protected: !!a.protected, label: a.alias && a.alias !== a.name ? `${a.alias} · ${a.name}` : (a.name || a.alias || a.id) } }))
      .filter((f) => f.geometry);
    setData('base-assets', fc(assets));
    // protected asset labels as HTML pills (SF Pro, matches the UI)
    for (const m of S.assetLabels) m.remove();
    S.assetLabels = [];
    for (const a of (w?.assets || []).filter((x) => x.protected && x.geometry)) {
      const coords = a.geometry.type === 'MultiLineString' ? a.geometry.coordinates.flat() : a.geometry.coordinates;
      if (!coords?.length) continue;
      const p = coords[Math.floor(coords.length * 0.18)] || coords[0];
      const el = document.createElement('div');
      el.className = 'det-asset-label';
      el.innerHTML = `<span class="det-asset-dot" aria-hidden="true"></span><span class="w500">${esc(a.alias || a.name)}</span>${a.alias && a.name ? `<span class="det-asset-sub">${esc(a.name)}</span>` : ''}`;
      S.assetLabels.push(new maplibregl.Marker({ element: el, anchor: 'bottom', offset: [0, -8] }).setLngLat(p).addTo(map));
    }
    setData('base-overlays', fc(overlayFeatures(w?.scenario?.overlays)));
  }

  function overlayFeatures(ov) {
    if (!ov) return [];
    const out = [];
    const push = (geom, props = {}) => { if (geom && geom.type && geom.coordinates) out.push({ type: 'Feature', geometry: geom, properties: props }); };
    const visit = (o) => {
      if (!o) return;
      if (Array.isArray(o)) { o.forEach(visit); return; }
      if (o.type === 'FeatureCollection') { (o.features || []).forEach(visit); return; }
      if (o.type === 'Feature') { push(o.geometry, o.properties || {}); return; }
      if (o.type && o.coordinates) { push(o); return; }
      const props = { kind: o.kind || o.type || 'overlay', label: o.label || '' };
      if (o.geometry) visit(o.geometry.type === 'Feature' || o.geometry.type === 'FeatureCollection' ? o.geometry : { type: 'Feature', geometry: o.geometry, properties: props });
      if (o.features) visit(o.features);
      if (o.geojson) visit(o.geojson);
      for (const k of ['hexes', 'cells', 'polygons']) {
        if (Array.isArray(o[k])) for (const h of o[k]) {
          if (Array.isArray(h) && Array.isArray(h[0])) {
            const ring = Array.isArray(h[0][0]) ? h[0] : h;
            const closed = ring.length && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) ? [...ring, ring[0]] : ring;
            push({ type: 'Polygon', coordinates: [closed] }, props);
          } else if (h && Array.isArray(h.poly || h.boundary) && !h.geometry) {
            // gpsjam-style cell: {lat, lon, bad, poly: [[lat, lon], ...]} (lat-first when it matches the centre)
            let ring = h.poly || h.boundary;
            if (Number.isFinite(h.lat) && Number.isFinite(h.lon) && ring.length
              && Math.abs(ring[0][0] - h.lat) + Math.abs(ring[0][1] - h.lon) < Math.abs(ring[0][1] - h.lat) + Math.abs(ring[0][0] - h.lon)) {
              ring = ring.map(([a, b]) => [b, a]);
            }
            if (ring.length && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])) ring = [...ring, ring[0]];
            push({ type: 'Polygon', coordinates: [ring] }, { ...props, bad: h.bad ?? null });
          } else if (h && h.geometry) push(h.geometry, { ...props, ...(h.properties || {}) });
        }
      }
    };
    visit(ov);
    return out.filter((f) => /Polygon/.test(f.geometry.type));
  }

  // ---------- per-vessel position ----------
  // states missing, or a single all-verified placeholder covering the scenario: treat as not fused yet
  function unfused(v) {
    const s = v.states || [];
    return !s.length || (s.length === 1 && s[0].state === 'verified' && !(v.dark_periods || []).length && !(v.risk?.score));
  }
  function position(v, t, isStatic) {
    const hasTrack = v.track && v.track.length;
    if (hasTrack) {
      let p = null;
      if (typeof ctx.vesselAt === 'function') { try { p = ctx.vesselAt(v, t); } catch { p = null; } }
      if (!p) {
        const q = interp(v.track, t);
        if (!q) return null;
        const st = stateAt(v, t) || 'verified';
        let claimed = null;
        if (st !== 'dark') { const c = interp(v.claimed, t, 15 * 60); if (c) claimed = { lon: c[1], lat: c[2] }; }
        p = { lon: q[1], lat: q[2], sog: q[3], cog: q[4], state: st, claimed };
      }
      if (p.lon == null || p.lat == null || Number.isNaN(p.lon)) return null;
      if (unfused(v)) {
        // fusion has not run for this vessel: compare the AIS layer with the track ourselves
        p = { ...p };
        const c = v.claimed || [], i = bisect(c, t);
        if (!c.length || i < 0 || t - c[i][0] > AIS_WIN) { p.state = 'dark'; p.claimed = null; }
        else {
          const q = interp(c, t, 15 * 60);
          const off = q && Math.hypot((q[1] - p.lon) * 55.6, (q[2] - p.lat) * 111.2);
          if (off > 3) { p.state = 'spoofing'; p.claimed = { lon: q[1], lat: q[2] }; } else p.state = 'verified';
        }
      }
      if (p.state === 'spoofing' && !p.claimed) { const c = interp(v.claimed, t, 30 * 60); if (c) p.claimed = { lon: c[1], lat: c[2] }; }
      return p;
    }
    // no fused track: use AIS if any, else the radar detection
    const c = interp(v.claimed, t, 30 * 60);
    if (c) return { lon: c[1], lat: c[2], sog: c[3], cog: c[4], state: stateAt(v, t) || 'verified', claimed: null };
    const obs = v.observed || [];
    if (!obs.length) return null;
    let best = obs[0];
    for (const o of obs) if (Math.abs(o.t - t) < Math.abs(best.t - t)) best = o;
    if (!isStatic && Math.abs(best.t - t) > STATIC_NEAR) return null;
    const st = stateAt(v, t);
    return { lon: best.lon, lat: best.lat, sog: null, cog: null, state: st && st !== 'verified' ? st : (best.matched_to_ais ? 'verified' : 'dark'), claimed: null, observed_only: true };
  }

  function trail(v, t, state) {
    const tr = v.track || [];
    if (tr.length < 2) return null;
    const span = state === 'verified' ? TRAIL_S : TRAIL_MISMATCH_S;
    const i1 = bisect(tr, t);
    if (i1 < 0) return null;
    const i0 = Math.max(0, bisect(tr, t - span));
    const coords = [];
    for (let i = i0; i <= i1; i++) coords.push([tr[i][1], tr[i][2]]);
    const p = S.pos.get(v.id);
    if (p) coords.push([p.lon, p.lat]);
    return coords.length >= 2 ? coords : null;
  }

  // ---------- main update ----------
  function update(t, { force = false } = {}) {
    const idx = S.idx;
    if (!idx || t == null) return;
    const prevT = S.lastT;
    if (!force && prevT === t) return;
    S.lastT = t;
    const L = S.layers;
    const verified = [], dark = [], spoofPos = [], spoofRing = [], spoofLink = [], halo = [], trails = [];
    const states = new Map(), pos = new Map();
    let ais = 0, nDark = 0, nSpoof = 0;
    const mismatchIds = [];

    for (const v of idx.vessels) {
      const p = position(v, t, idx.isStatic);
      // AIS broadcasting now?
      const c = v.claimed || [];
      if (c.length) {
        if (idx.isStatic) ais += 1;
        else { const i = bisect(c, t); if (i >= 0 && t - c[i][0] <= AIS_WIN) ais += 1; }
      }
      if (!p) continue;
      const st = p.state || 'verified';
      states.set(v.id, st);
      pos.set(v.id, { lon: p.lon, lat: p.lat, claimed: p.claimed });
      const cog = Number.isFinite(p.cog) ? p.cog : 0;
      const moving = Number.isFinite(p.sog) ? p.sog > 0.6 : Number.isFinite(p.cog);
      const props = { id: v.id, cog, moving, state: st };
      if (st === 'dark') {
        nDark += 1; mismatchIds.push(v.id);
        if (L.observed) { dark.push(pt(p.lon, p.lat, props)); halo.push(pt(p.lon, p.lat, { id: v.id, color: C.orange })); }
      } else if (st === 'spoofing') {
        nSpoof += 1; mismatchIds.push(v.id);
        if (L.observed) { spoofPos.push(pt(p.lon, p.lat, props)); halo.push(pt(p.lon, p.lat, { id: v.id, color: C.red })); }
        if (p.claimed) {
          if (L.claimed) spoofRing.push(pt(p.claimed.lon, p.claimed.lat, { id: v.id }));
          if (L.claimed && L.observed) spoofLink.push(ln([[p.claimed.lon, p.claimed.lat], [p.lon, p.lat]], { id: v.id }));
        }
      } else if (L.claimed) {
        verified.push(pt(p.lon, p.lat, props));
      }
    }
    S.states = states; S.pos = pos;

    for (const v of idx.vessels) {
      const st = states.get(v.id);
      if (!st) continue;
      if (st === 'verified' ? !L.claimed : !L.observed) continue;
      const coords = trail(v, t, st);
      if (coords) trails.push(ln(coords, { id: v.id, state: st }));
    }

    // radar + RF in their windows
    const sar = [], rf = [];
    let nSar = 0, nRf = 0, lastPass = null;
    const sarLo = bisect(idx.sar, t - SAR_WIN, (x) => x.o.t) + 1, sarHi = bisect(idx.sar, t, (x) => x.o.t);
    for (let i = idx.isStatic ? 0 : Math.max(0, sarLo); i <= (idx.isStatic ? idx.sar.length - 1 : sarHi); i++) {
      const { v, o } = idx.sar[i];
      nSar += 1;
      // draw radar marks only where they matter (mismatch vessels or unmatched hulls); counts include all
      if (L.observed && (states.get(v.id) || 'dark') !== 'verified') sar.push(pt(o.lon, o.lat, { id: o.id, matched: !!o.matched_to_ais, age: idx.isStatic ? 0 : Math.min(1, (t - o.t) / SAR_WIN) }));
    }
    if (sarHi >= 0) lastPass = idx.sar[sarHi].o.t;
    const rfLo = bisect(idx.rf, t - RF_WIN, (x) => x.e.t) + 1, rfHi = bisect(idx.rf, t, (x) => x.e.t);
    for (let i = idx.isStatic ? 0 : Math.max(0, rfLo); i <= (idx.isStatic ? idx.rf.length - 1 : rfHi); i++) {
      const { v, e } = idx.rf[i];
      nRf += 1;
      if (L.emitted && (states.get(v.id) || 'dark') !== 'verified') rf.push(pt(e.lon, e.lat, { id: e.id, age: idx.isStatic ? 0 : Math.min(1, (t - e.t) / RF_WIN) }));
    }

    setData('det-verified', fc(verified));
    setData('det-dark', fc(dark));
    setData('det-spoof-pos', fc(spoofPos));
    setData('det-spoof-ring', fc(spoofRing));
    setData('det-spoof-link', fc(spoofLink));
    setData('det-halo', fc(halo));
    setData('det-trails', fc(trails));
    setData('det-sar', fc(sar));
    setData('det-rf', fc(rf));
    S.mismatch = halo.length;
    drawSelected();
    updateLabels();
    for (const f of S.flashes) { const q = pos.get(f.id); if (q) { f.lon = q.lon; f.lat = q.lat; } }

    S.counts = { ais, sar: nSar, rf: nRf, dark: nDark, spoof: nSpoof, mismatchIds, lastPass, total: idx.vessels.length };
    const info = { t, prevT, states, pos, counts: S.counts, isStatic: idx.isStatic };
    for (const fn of S.listeners) { try { fn(info); } catch (e) { console.error(e); } }
  }

  function drawSelected() {
    const p = S.selected && S.pos.get(S.selected);
    const st = S.selected && S.states.get(S.selected);
    const visible = st === 'verified' ? S.layers.claimed : S.layers.observed;
    setData('det-selected', fc(p && visible ? [pt(p.lon, p.lat)] : []));
  }

  // ---------- HTML labels on high-risk / mismatch vessels ----------
  const STATE_WORD = { dark: 'Dark', spoofing: 'False position' };
  function updateLabels() {
    const idx = S.idx, L = S.layers;
    const want = [];
    for (const v of idx.vessels) {
      const st = S.states.get(v.id);
      if (!st) continue;
      const high = v.risk?.level === 'high' || st !== 'verified' || S.selected === v.id;
      if (!high) continue;
      if (st === 'verified' ? !L.claimed : !L.observed) continue;
      want.push({ v, st, score: (st !== 'verified' ? 1000 : 0) + (v.risk?.score || 0) + (S.selected === v.id ? 5000 : 0) });
    }
    want.sort((a, b) => b.score - a.score);
    const keep = new Set();
    for (const { v, st } of want.slice(0, MAX_LABELS)) {
      keep.add(v.id);
      const p = S.pos.get(v.id);
      let m = S.labels.get(v.id);
      if (!m) {
        const el = document.createElement('button');
        el.type = 'button';
        el.addEventListener('click', (e) => { e.stopPropagation(); select(v.id); });
        m = new maplibregl.Marker({ element: el, anchor: 'left', offset: [14, 0] }).setLngLat([p.lon, p.lat]).addTo(map);
        S.labels.set(v.id, m);
      }
      const el = m.getElement();
      const key = `${st}|${S.selected === v.id}`;
      if (el.dataset.key !== key) {
        el.dataset.key = key;
        // keep MapLibre's own marker classes (they position the element)
        el.classList.remove('det-label--verified', 'det-label--dark', 'det-label--spoofing');
        el.classList.add('det-label', `det-label--${st}`);
        el.classList.toggle('is-selected', S.selected === v.id);
        el.setAttribute('aria-label', `${v.id} ${v.name || ''} ${STATE_WORD[st] || 'Verified'}`);
        el.innerHTML = `<span class="det-label-id">${esc(v.id)}</span>${STATE_WORD[st] ? `<span class="det-label-state">${STATE_WORD[st]}</span>` : ''}`;
      }
      m.setLngLat([p.lon, p.lat]);
    }
    for (const [id, m] of S.labels) if (!keep.has(id)) { m.remove(); S.labels.delete(id); }

    // "AIS claims here" tag at each false-position ring
    const rings = new Set();
    if (L.claimed) for (const [id, p] of S.pos) {
      if (S.states.get(id) !== 'spoofing' || !p.claimed) continue;
      rings.add(id);
      let m = S.ringLabels.get(id);
      if (!m) {
        const el = document.createElement('div');
        el.className = 'det-ring-label';
        el.textContent = `${id} · AIS claims here`;
        m = new maplibregl.Marker({ element: el, anchor: 'top', offset: [0, 16] }).setLngLat([p.claimed.lon, p.claimed.lat]).addTo(map);
        S.ringLabels.set(id, m);
      }
      m.setLngLat([p.claimed.lon, p.claimed.lat]);
    }
    for (const [id, m] of S.ringLabels) if (!rings.has(id)) { m.remove(); S.ringLabels.delete(id); }
  }

  // ---------- animation: pulse + flashes ----------
  let lastFrame = 0;
  function animate(now) {
    requestAnimationFrame(animate);
    if (now - lastFrame < 33) return;
    lastFrame = now;
    if (S.mismatch && map.getLayer('det-halo')) {
      const k = (now % 1800) / 1800;
      const e = 1 - Math.pow(1 - k, 2);
      map.setPaintProperty('det-halo', 'circle-radius', 10 + 20 * e);
      map.setPaintProperty('det-halo', 'circle-stroke-opacity', 0.55 * (1 - k));
    }
    if (S.flashes.length) {
      const FL = 2200;
      S.flashes = S.flashes.filter((f) => now - f.t0 < FL);
      const feats = S.flashes.map((f) => pt(f.lon, f.lat, { color: f.color, k: ((now - f.t0) % 1100) / 1100 }));
      setData('det-flash', fc(feats));
      const k = S.flashes.length ? ((now - S.flashes[0].t0) % 1100) / 1100 : 0;
      map.setPaintProperty('det-flash', 'circle-radius', 12 + 34 * k);
      map.setPaintProperty('det-flash', 'circle-opacity', 0.22 * (1 - k));
      map.setPaintProperty('det-flash', 'circle-stroke-opacity', 0.9 * (1 - k));
      if (!S.flashes.length) setData('det-flash', fc());
    }
  }
  requestAnimationFrame(animate);

  function flash(id) {
    const p = S.pos.get(id);
    if (!p) return;
    const st = S.states.get(id);
    S.flashes.push({ id, lon: p.lon, lat: p.lat, color: st === 'spoofing' ? C.red : C.orange, t0: performance.now() });
    const el = S.labels.get(id)?.getElement();
    if (el) { el.classList.remove('is-flash'); void el.offsetWidth; el.classList.add('is-flash'); }
  }

  // ---------- scheduling ----------
  function schedule(t) {
    S.pendingT = t;
    if (S.raf) return;
    S.raf = requestAnimationFrame(() => { S.raf = 0; update(S.pendingT); });
  }

  function loadWorld(w) {
    S.world = w || ctx.world;
    S.idx = indexWorld(S.world);
    S.lastT = null;
    S.flashes = [];
    for (const m of S.labels.values()) m.remove();
    for (const m of S.ringLabels.values()) m.remove();
    S.labels.clear(); S.ringLabels.clear();
    if (S.selected && !S.idx.byId.has(S.selected)) S.selected = null;
    drawStatic(S.world);
    update(ctx.clock?.t ?? S.world?.scenario?.now, { force: true });
  }

  function select(id) {
    S.selected = id || null;
    drawSelected();
    updateLabels();
    if (id) ctx.bus.emit('select-vessel', { id });
  }

  // ---------- interaction ----------
  function hit(e) {
    const r = 8, b = [[e.point.x - r, e.point.y - r], [e.point.x + r, e.point.y + r]];
    const layers = HIT_LAYERS.filter((l) => map.getLayer(l));
    const fs = map.queryRenderedFeatures(b, { layers });
    if (!fs.length) return null;
    fs.sort((a, c) => HIT_LAYERS.indexOf(a.layer.id) - HIT_LAYERS.indexOf(c.layer.id));
    return fs[0].properties.id;
  }
  map.on('click', (e) => { const id = hit(e); if (id) select(id); });
  let hovering = false;
  map.on('mousemove', (e) => {
    const h = !!hit(e);
    if (h !== hovering) { hovering = h; map.getCanvas().style.cursor = h ? 'pointer' : ''; }
  });

  ctx.bus.on('world', ({ world } = {}) => loadWorld(world || ctx.world));
  ctx.bus.on('tick', ({ t }) => schedule(t ?? ctx.clock?.t));
  ctx.bus.on('select-vessel', ({ id }) => { if (id !== S.selected) { S.selected = id || null; drawSelected(); updateLabels(); } });
  ctx.bus.on('deselect-vessel', () => { S.selected = null; drawSelected(); updateLabels(); });

  // shared with detect.js
  ctx.detMap = {
    get layers() { return { ...S.layers }; },
    setLayer(name, on) { if (name in S.layers) { S.layers[name] = !!on; update(S.lastT ?? ctx.clock?.t, { force: true }); } },
    onUpdate(fn) { S.listeners.add(fn); if (S.lastT != null) fn({ t: S.lastT, prevT: null, states: S.states, pos: S.pos, counts: S.counts, isStatic: S.idx?.isStatic }); return () => S.listeners.delete(fn); },
    get counts() { return S.counts; },
    stateOf(id) { return S.states.get(id) || null; },
    positionOf(id) { return S.pos.get(id) || null; },
    select, flash,
    colors: C,
  };

  if (ctx.world) loadWorld(ctx.world);
}
