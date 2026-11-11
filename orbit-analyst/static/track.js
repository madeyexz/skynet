// Ship tracker: AIS tracks on a time scrubber, zones with alerts, radar scans and notes.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

const TYPE_COLOR = { Cargo: "#2f5bd3", Tanker: "#b4561c", Fishing: "#0f7a6c", Passenger: "#7b45d6", HSC: "#7b45d6",
  Pleasure: "#c43d7a", Sailing: "#c43d7a", Tug: "#55534e", Pilot: "#55534e", SAR: "#55534e", Towing: "#55534e", Military: "#2b2b2b" };
const LEGEND = [["Cargo", "#2f5bd3"], ["Tanker", "#b4561c"], ["Fishing", "#0f7a6c"], ["Passenger", "#7b45d6"], ["Pleasure", "#c43d7a"], ["Service", "#55534e"], ["Other", "#8a877f"]];
const color = (t) => TYPE_COLOR[t] || "#8a877f";
const NOTE_COLOR = { note: "#111", suspicious: "#e5432d", watch: "#d99a00", cleared: "#1a9c5b" };
const KIND = {
  ais_off: ["AIS went quiet", "dark"], ais_on: ["AIS first heard", "dark"], gap: ["AIS gap", "dark"],
  loiter: ["Stopped", "loiter"], meet: ["Alongside", "meet"], enter: ["Entered", "move"], exit: ["Left", "move"],
};
const GROUPS = { dark: "Dark signals", meet: "Alongside", loiter: "Stopped", move: "Enter / exit" };
const SILENT_AFTER = 900;     // no fix for 15 min: the ship is shown as silent
const SILENT_SHOW = 7200;     // keep a silent ship on the map for 2 h

const S = {
  src: "skagen", data: null, t: 0, tmax: 86400, playing: false, speed: 300, trail: 3600,
  sel: null, zone: null, zoneLayer: null, alerts: [], groups: { dark: true, meet: true, loiter: true, move: false },
  notes: [], noteDraft: null, scans: [], radarLayers: {}, follow: false, atNow: true, lastFrame: 0,
};

// ---------------------------------------------------------------- map and canvas

const map = L.map("map", { zoomAnimation: false, preferCanvas: true, zoomControl: true }).setView([57.7, 10.65], 10);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors", maxZoom: 18, className: "osm-tiles",
}).addTo(map);
map.pm.setGlobalOptions({ pathOptions: { color: "#111", weight: 1.5, dashArray: "5 4", fillColor: "#ffe26a", fillOpacity: 0.12 } });

const canvas = document.createElement("canvas");
canvas.className = "tk-canvas";
map.getContainer().appendChild(canvas);
const ctx = canvas.getContext("2d");
function resizeCanvas() {
  const r = map.getContainer().getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = r.width * dpr; canvas.height = r.height * dpr;
  canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
map.on("move zoom resize", draw);
window.addEventListener("resize", () => { map.invalidateSize(); resizeCanvas(); });

// ---------------------------------------------------------------- geometry and time

const pt = (lat, lon) => map.latLngToContainerPoint([lat, lon]);
function metres(aLat, aLon, bLat, bLon) {
  const x = (bLon - aLon) * 111320 * Math.cos((aLat + bLat) * Math.PI / 360), y = (bLat - aLat) * 110570;
  return Math.hypot(x, y);
}
function inZone(lat, lon) {
  const z = S.zone; if (!z) return false;
  let inside = false;
  for (let i = 0, j = z.length - 1; i < z.length; j = i++) {
    const [yi, xi] = z[i], [yj, xj] = z[j];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function zoneKm2(z) {
  const lat0 = (z.reduce((a, p) => a + p[0], 0) / z.length) * Math.PI / 180;
  const xy = z.map(([la, lo]) => [lo * 111.32 * Math.cos(lat0), la * 110.57]);
  let a = 0; for (let i = 0; i < xy.length; i++) { const [x0, y0] = xy[i], [x1, y1] = xy[(i + 1) % xy.length]; a += x0 * y1 - x1 * y0; }
  return Math.abs(a) / 2;
}
const epoch = (t) => S.data.t0 + t;
const hhmm = (t) => new Date(epoch(t) * 1000).toISOString().slice(11, 16);
function dur(s) { s = Math.round(s / 60); return s < 60 ? `${s} min` : `${Math.floor(s / 60)} h ${String(s % 60).padStart(2, "0")} min`; }
function passT(p) { return Date.parse(p.pass_time) / 1000 - S.data.t0; }

// Did the ship stop reporting near the edge of the covered area? Then it left, it did not go quiet.
function nearEdge(lat, lon) {
  const d = S.data;
  if (d.circle) return metres(lat, lon, d.circle[0], d.circle[1]) > (d.circle[2] - 8) * 1000;
  const [w, s, e, n] = d.bbox;
  return lat - s < 0.03 || n - lat < 0.03 || lon - w < 0.05 || e - lon < 0.05;
}

function idxAt(p, t) {
  if (!p.length || t < p[0][0]) return -1;
  let lo = 0, hi = p.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (p[m][0] <= t) lo = m; else hi = m - 1; }
  return lo;
}
// Position of a ship at time t: interpolated between fixes, or held at the last fix while it is silent.
function stateAt(s, t) {
  const p = s.p, i = idxAt(p, t);
  if (i < 0) return null;
  const a = p[i], b = p[i + 1];
  let lat = a[1], lon = a[2], hdg = a[4] >= 0 && a[3] > 0.5 ? a[4] : null, age = t - a[0];
  if (b && b[0] - a[0] < 1800) {
    const f = (t - a[0]) / (b[0] - a[0]);
    lat += (b[1] - a[1]) * f; lon += (b[2] - a[2]) * f; age = 0;
  }
  if (age > SILENT_SHOW) return null;
  const silent = age > SILENT_AFTER;
  if (silent && !b && nearEdge(a[1], a[2])) return null;
  return { lat, lon, hdg, sog: a[3], st: a[5], age, silent, i, last: a[0] };
}

// ---------------------------------------------------------------- drawing

function shipSize(s) { const l = +s.length || 0; return l > 200 ? 11 : l > 100 ? 9 : l > 40 ? 7.5 : 6; }

function drawShip(x, y, st, s, selected) {
  const L = shipSize(s), col = color(s.type);
  ctx.save(); ctx.translate(x, y);
  if (selected) { ctx.beginPath(); ctx.arc(0, 0, L + 7, 0, 7); ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5; ctx.stroke(); }
  ctx.beginPath();
  if (st.hdg != null && !st.silent) {
    ctx.rotate(st.hdg * Math.PI / 180);
    ctx.moveTo(0, -L); ctx.lineTo(L * 0.55, L * 0.7); ctx.lineTo(0, L * 0.35); ctx.lineTo(-L * 0.55, L * 0.7); ctx.closePath();
  } else ctx.arc(0, 0, L * 0.48, 0, 7);
  if (st.silent) { ctx.setLineDash([2, 2]); ctx.strokeStyle = "#e5432d"; ctx.lineWidth = 1.5; ctx.stroke(); }
  else { ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.lineWidth = 1; ctx.stroke(); }
  ctx.restore();
}

function drawTrail(s, t, st) {
  if (!S.trail) return;
  const p = s.p, from = t - S.trail;
  ctx.beginPath(); let started = false;
  for (let k = Math.max(0, idxAt(p, from)); k <= st.i; k++) {
    if (p[k][0] < from) continue;
    const q = pt(p[k][1], p[k][2]);
    if (!started) { ctx.moveTo(q.x, q.y); started = true; } else ctx.lineTo(q.x, q.y);
  }
  if (!started) return;
  const q = pt(st.lat, st.lon); ctx.lineTo(q.x, q.y);
  ctx.strokeStyle = color(s.type) + "66"; ctx.lineWidth = 1.4; ctx.stroke();
}

// The selected ship: its whole day, past solid, future dashed, AIS gaps in red.
function drawSelected(s, t) {
  const p = s.p;
  for (let k = 1; k < p.length; k++) {
    const a = pt(p[k - 1][1], p[k - 1][2]), b = pt(p[k][1], p[k][2]), gap = p[k][0] - p[k - 1][0] > 1800;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.setLineDash(gap ? [3, 4] : p[k][0] > t ? [5, 5] : []);
    ctx.strokeStyle = gap ? "#e5432d" : p[k][0] > t ? "rgba(17,17,17,.35)" : "#111";
    ctx.lineWidth = gap ? 2 : 2.2; ctx.stroke();
  }
  ctx.setLineDash([]);
}

function drawTargets(markers, alpha, scan) {
  ctx.globalAlpha = alpha;
  for (const m of markers) {
    const q = pt(m.lat, m.lon), dark = m.status !== "ais";
    ctx.beginPath(); ctx.arc(q.x, q.y, dark ? 10 : 7, 0, 7);
    ctx.strokeStyle = dark ? "#e5432d" : "#1a9c5b"; ctx.lineWidth = dark ? 2.2 : 1.4;
    ctx.setLineDash(scan ? [] : [3, 2]); ctx.stroke();
    if (dark) { ctx.beginPath(); ctx.moveTo(q.x - 4, q.y); ctx.lineTo(q.x + 4, q.y); ctx.moveTo(q.x, q.y - 4); ctx.lineTo(q.x, q.y + 4); ctx.stroke(); }
  }
  ctx.setLineDash([]); ctx.globalAlpha = 1;
}

let drawn = [];   // [x, y, ship] for clicks
function draw() {
  if (!S.data) return;
  const { width, height } = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, width, height);
  const t = S.t;
  drawn = [];
  const states = [];
  for (const s of S.data.ships) {
    const st = stateAt(s, t);
    if (st) states.push([s, st]);
  }
  for (const [s, st] of states) drawTrail(s, t, st);
  if (S.sel) drawSelected(S.sel, t);
  // Radar passes fade in and out over the 30 minutes around the pass.
  for (const p of passesNow()) drawTargets(p.markers, p.alpha, false);
  for (const sc of S.scans) drawTargets(sc.markers, 1, true);
  for (const [s, st] of states) {
    const q = pt(st.lat, st.lon);
    if (q.x < -20 || q.y < -20 || q.x > width + 20 || q.y > height + 20) continue;
    drawShip(q.x, q.y, st, s, s === S.sel);
    drawn.push([q.x, q.y, s, st]);
  }
  S.visible = states;
  updateStats(states);
  syncRadarImages();
}

function passesNow() {
  return (S.data.passes || []).map((p) => {
    const d = Math.abs(S.t - passT(p));
    return d <= 1800 ? Object.assign(p, { alpha: 1 - (d / 1800) * 0.7 }) : null;
  }).filter(Boolean);
}

function syncRadarImages() {
  const want = new Map();
  for (const p of passesNow()) if (p.map_overlay) want.set(p.map_overlay.url, [p.map_overlay, 0.75 * p.alpha]);
  for (const sc of S.scans) if (sc.map_overlay) want.set(sc.map_overlay.url, [sc.map_overlay, 0.85]);
  for (const [url, layer] of Object.entries(S.radarLayers)) if (!want.has(url)) { map.removeLayer(layer); delete S.radarLayers[url]; }
  for (const [url, [ov, op]] of want) {
    if (!S.radarLayers[url]) S.radarLayers[url] = L.imageOverlay(url, ov.bounds, { opacity: op, className: "tk-radar" }).addTo(map);
    else S.radarLayers[url].setOpacity(op);
  }
}

// ---------------------------------------------------------------- stats and side panels

function updateStats(states) {
  const moving = states.filter(([, st]) => !st.silent && st.sog > 1).length;
  const silent = states.filter(([, st]) => st.silent).length;
  const zoneN = S.zone ? states.filter(([, st]) => inZone(st.lat, st.lon)).length : null;
  $("#stats").innerHTML = [
    [states.length - silent, "ships reporting"], [moving, "moving"], [silent, "silent > 15 min"],
    [zoneN ?? "–", "in the zone"],
  ].map(([v, l]) => `<div><b>${v}</b><span>${l}</span></div>`).join("");
  $("#clock").textContent = hhmm(S.t) + " UTC";
  $("#day").textContent = new Date(epoch(S.t) * 1000).toISOString().slice(0, 10);
  $("#scrub").value = S.t;
  if (S.sel) renderShip();
}

function renderLegend() {
  $("#legend").innerHTML = LEGEND.map(([l, c]) => `<span><i style="background:${c}"></i>${l}</span>`).join("") +
    `<span><i class="sil"></i>Silent</span><span><i class="dk"></i>Radar, no AIS</span><span><i class="ok"></i>Radar, AIS</span>`;
}

function renderSource() {
  const d = S.data, banner = $("#banner");
  let line = esc(d.source);
  if (d.kind === "live") {
    const n = d.ships.length;
    line += d.ok ? ` · polled ${Math.max(0, Math.round(Date.now() / 1000 - d.polled))} s ago · ${n} ships · collecting tracks since ${hhmm(d.collecting_since)} UTC, kept for 3 h`
      : ` · <span class="warn">feed down${d.error ? ": " + esc(d.error) : ""}</span>`;
  }
  if (d.kind === "replay") line += ` · ${d.ships.length} ships · ${(d.passes || []).length} Sentinel-1 passes on the timeline`;
  $("#srcline").innerHTML = line;
  banner.classList.toggle("hidden", !(d.kind === "sim" || (d.kind === "live" && !d.ok)));
  if (d.kind === "sim") banner.innerHTML = "<b>Simulated.</b> The real 6 Nov Skagen AIS day, replayed on today's clock. Use it when the real-time feed is down.";
  else if (d.kind === "live" && !d.ok) banner.innerHTML = `<b>The live feed is not answering.</b> <button class="tk-link" data-go="sim">Switch to simulated live</button>`;
}

function renderTimeline() {
  const pct = (t) => (100 * t / S.tmax).toFixed(3) + "%";
  const ticks = [];
  for (const p of S.data.passes || []) ticks.push(`<button class="tick pass" style="left:${pct(passT(p))}" data-t="${passT(p)}" title="Sentinel-1 pass · ${esc(p.headline)}">S1 ${hhmm(passT(p))}</button>`);
  for (const a of S.alerts.filter((a) => S.groups[KIND[a.kind][1]])) ticks.push(`<button class="tick al" style="left:${pct(a.t)}" data-t="${a.t}" title="${esc(KIND[a.kind][0])} · ${esc(a.name)}"></button>`);
  for (const n of S.notes) { const t = n.t - S.data.t0; if (t >= 0 && t <= S.tmax) ticks.push(`<button class="tick nt" style="left:${pct(t)};background:${NOTE_COLOR[n.tag]}" data-t="${t}" title="${esc(n.text)}"></button>`); }
  $("#ticks").innerHTML = ticks.join("");
  const hours = [], step = S.tmax > 30000 ? 3 * 3600 : 1800;
  const first = Math.ceil(S.data.t0 / step) * step - S.data.t0;
  for (let t = first; t <= S.tmax; t += step) hours.push(`<span style="left:${pct(t)}">${hhmm(t)}</span>`);
  $("#hours").innerHTML = hours.join("");
}

// ---------------------------------------------------------------- zone alerts

function computeAlerts() {
  S.alerts = [];
  if (!S.zone || !S.data) return;
  const d = S.data, tEnd = d.kind === "replay" ? S.tmax : d.now, out = S.alerts;
  const statuses = d.statuses;
  const tStart = d.collecting_since ?? d.ships.reduce((m, s) => Math.min(m, s.p[0][0]), Infinity);   // when collection began
  const still = (st) => ["At anchor", "Moored"].includes(statuses[st]);
  for (const s of d.ships) {
    const p = s.p, name = s.name || "MMSI " + s.mmsi;
    const ins = p.map((q) => inZone(q[1], q[2]));
    if (!ins.some(Boolean)) continue;
    const add = (k, q, text, extra = {}) => out.push({ kind: k, t: q[0], lat: q[1], lon: q[2], mmsi: s.mmsi, name, text, ...extra });
    if (ins[0] && p[0][0] > tStart + 1800) add("ais_on", p[0], `${name} first heard inside the zone, not coming in from outside.`);
    const L = p.length - 1;
    if (ins[L] && p[L][0] < tEnd - 1800 && !nearEdge(p[L][1], p[L][2])) add("ais_off", p[L], `${name} last heard inside the zone ${dur(tEnd - p[L][0])} before the end.`);
    let stopStart = null;
    for (let k = 1; k <= L; k++) {
      const a = p[k - 1], b = p[k];
      if (ins[k] && !ins[k - 1]) add("enter", b, `${name} entered the zone.`);
      if (!ins[k] && ins[k - 1]) add("exit", b, `${name} left the zone.`);
      if (b[0] - a[0] > 1800 && (ins[k] || ins[k - 1])) {
        const km = metres(a[1], a[2], b[1], b[2]) / 1000;
        add("gap", a, `${name} sent no AIS for ${dur(b[0] - a[0])}${km > 1 ? ` and came back ${km.toFixed(1)} km away` : ""}.`, { gap: b[0] - a[0] });
      }
      const stopped = ins[k] && b[3] >= 0 && b[3] < 1 && !still(b[5]);
      if (stopped && stopStart == null) stopStart = a;
      if ((!stopped || k === L) && stopStart) {
        const span = (stopped ? b[0] : a[0]) - stopStart[0];
        if (span >= 5400) add("loiter", stopStart, `${name} stopped for ${dur(span)} without reporting anchored or moored.`);
        stopStart = null;
      }
    }
  }
  // Ships alongside: two ships within 200 m of each other for 30 minutes or more (a possible ship-to-ship transfer).
  const SERVICE = new Set(["Tug", "Pilot", "SAR", "Towing"]);
  const ships = d.ships.filter((s) => !SERVICE.has(s.type) && s.p.some((q) => inZone(q[1], q[2])));
  const runs = new Map();
  const flush = (key, run) => {
    if (run.n >= 7) {
      const [a, b] = run.ships;
      out.push({ kind: "meet", t: run.t, lat: run.lat, lon: run.lon, mmsi: a.mmsi, mmsi2: b.mmsi, name: a.name || a.mmsi,
        text: `${a.name || a.mmsi} (${a.type || "?"}) and ${b.name || b.mmsi} (${b.type || "?"}) were within 200 m for ${dur((run.n - 1) * 300)}. Possible ship-to-ship transfer.` });
    }
    runs.delete(key);
  };
  for (let t = 0; t <= tEnd; t += 300) {
    const here = [];
    for (const s of ships) { const st = stateAt(s, t); if (st && !st.silent && st.sog < 3 && inZone(st.lat, st.lon)) here.push([s, st]); }
    const seen = new Set();
    for (let i = 0; i < here.length; i++) for (let j = i + 1; j < here.length; j++) {
      const [a, sa] = here[i], [b, sb] = here[j];
      if (Math.abs(sa.lat - sb.lat) > 0.003 || metres(sa.lat, sa.lon, sb.lat, sb.lon) > 200) continue;
      const key = a.mmsi + "|" + b.mmsi; seen.add(key);
      const r = runs.get(key) || { n: 0, t, lat: sa.lat, lon: sa.lon, ships: [a, b] };
      r.n++; runs.set(key, r);
    }
    for (const [key, r] of [...runs]) if (!seen.has(key)) flush(key, r);
  }
  for (const [key, r] of [...runs]) flush(key, r);
  out.sort((a, b) => a.t - b.t);
}

function renderZone() {
  const z = S.zone;
  $("#zarea").textContent = z ? `${zoneKm2(z).toFixed(0)} km²` : "none";
  $("#scan").disabled = !z;
  $("#zhint").classList.toggle("hidden", !!z);
  const counts = {};
  for (const a of S.alerts) counts[KIND[a.kind][1]] = (counts[KIND[a.kind][1]] || 0) + 1;
  $("#chips").innerHTML = z ? Object.entries(GROUPS).map(([g, l]) => `<button class="chip ${S.groups[g] ? "on" : ""}" data-g="${g}">${l} <b>${counts[g] || 0}</b></button>`).join("") : "";
  const shown = S.alerts.filter((a) => S.groups[KIND[a.kind][1]]);
  $("#alerts").innerHTML = z ? (shown.length ? shown.map((a, i) => `
    <li class="al-${KIND[a.kind][1]}" data-i="${S.alerts.indexOf(a)}"><span class="t">${hhmm(a.t)}</span><span><b>${esc(KIND[a.kind][0])}</b> ${esc(a.text)}</span></li>`).join("")
    : `<li class="none">Nothing in these groups inside the zone.</li>`) : "";
  renderTimeline();
}

function setZone(latlngs, save = true) {
  S.zone = latlngs ? latlngs.map((p) => [p.lat ?? p[0], p.lng ?? p[1]]) : null;
  if (save) localStorage.setItem("zone:" + S.src, JSON.stringify(S.zone));
  computeAlerts(); renderZone(); draw();
}

function showZoneLayer() {
  if (S.zoneLayer) { map.removeLayer(S.zoneLayer); S.zoneLayer = null; }
  if (!S.zone) return;
  S.zoneLayer = L.polygon(S.zone, { color: "#111", weight: 1.5, dashArray: "5 4", fillColor: "#ffe26a", fillOpacity: 0.12 }).addTo(map);
  watchZone(S.zoneLayer);
}
function watchZone(layer) {
  layer.on("pm:edit pm:dragend", () => setZone(layer.getLatLngs()[0]));
}

map.on("pm:create", (e) => {
  if (S.zoneLayer) map.removeLayer(S.zoneLayer);
  S.zoneLayer = e.layer; watchZone(e.layer);
  setZone(e.layer.getLatLngs()[0]);
});
$("#drawRect").onclick = () => map.pm.enableDraw("Rectangle");
$("#drawPoly").onclick = () => map.pm.enableDraw("Polygon", { snappable: false });
$("#editZone").onclick = () => {
  if (!S.zoneLayer) return;
  const on = S.zoneLayer.pm.enabled();
  on ? S.zoneLayer.pm.disable() : S.zoneLayer.pm.enable({ allowSelfIntersection: false });
  $("#editZone").textContent = on ? "Edit" : "Done";
};
$("#clearZone").onclick = () => { if (S.zoneLayer) map.removeLayer(S.zoneLayer); S.zoneLayer = null; S.scans = []; $("#scanout").innerHTML = ""; setZone(null); };
$("#chips").onclick = (e) => { const b = e.target.closest(".chip"); if (b) { S.groups[b.dataset.g] = !S.groups[b.dataset.g]; renderZone(); } };
$("#alerts").onclick = (e) => {
  const li = e.target.closest("li[data-i]"); if (!li) return;
  const a = S.alerts[+li.dataset.i];
  setTime(a.t); select(S.data.ships.find((s) => s.mmsi === a.mmsi));
  map.setView([a.lat, a.lon], Math.max(map.getZoom(), 12));
};

// ---------------------------------------------------------------- radar scan of the zone

$("#scan").onclick = async () => {
  if (!S.zone) return;
  const btn = $("#scan"), out = $("#scanout");
  btn.disabled = true; btn.textContent = "Scanning…";
  // Replay and simulated live ask for the radar pass nearest the moment on the scrubber.
  const when = S.data.kind === "sim" ? S.data.replay_t0 + S.t : S.data.kind === "replay" ? epoch(S.t) : Date.now() / 1000;
  const polygon = S.zone.map(([la, lo]) => [lo, la]);
  try {
    const r = await fetch("/api/scan", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ polygon, time: when }) });
    if (!r.ok) throw new Error((await r.json()).detail);
    const { job } = await r.json();
    let j;
    do {
      await new Promise((ok) => setTimeout(ok, 800));
      j = await (await fetch(`/api/jobs/${job}`)).json();
      out.innerHTML = `<ol class="tk-steps">${(j.steps || []).map((s) => `<li class="${s.kind}"><span class="t">${s.t.toFixed(0)}s</span><span>${md(s.text)}</span></li>`).join("")}</ol>`;
    } while (j.status === "running");
    if (j.status === "error") throw new Error(j.error);
    const res = j.result;
    S.scans = [res];
    out.innerHTML = `<div class="tk-scanres"><div class="tk-h"><b>${esc(res.headline)}</b><button class="tk-link" id="scanclear">Hide</button></div>
      <p>${md(res.answer)}</p><p class="muted">Pass ${esc(res.pass_time.slice(0, 16).replace("T", " "))} UTC · ${esc(res.scene)} · ${res.seconds.toFixed(0)} s</p></div>`;
    $("#scanclear").onclick = () => { S.scans = []; out.innerHTML = ""; draw(); };
    if (S.data.kind === "replay") setTime(passT({ pass_time: res.pass_time }));
    draw();
  } catch (e) {
    out.innerHTML = `<div class="err">${esc(e.message)}</div>`;
  } finally {
    btn.disabled = !S.zone; btn.textContent = "Scan the zone with radar";
  }
};

// ---------------------------------------------------------------- ship selection

function select(s) {
  S.sel = s || null;
  $("#shipcard").classList.toggle("hidden", !S.sel);
  if (S.sel) renderShip();
  draw();
}

function renderShip() {
  const s = S.sel, st = stateAt(s, S.t), d = S.data;
  const al = S.alerts.filter((a) => a.mmsi === s.mmsi || a.mmsi2 === s.mmsi);
  const notes = S.notes.filter((n) => n.mmsi === s.mmsi);
  const gaps = []; for (let k = 1; k < s.p.length; k++) if (s.p[k][0] - s.p[k - 1][0] > 1800) gaps.push(s.p[k][0] - s.p[k - 1][0]);
  const where = st ? `${st.lat.toFixed(4)}, ${st.lon.toFixed(4)}` : "not reporting at this time";
  $("#shipcard").innerHTML = `
    <div class="tk-h"><h4 style="color:${color(s.type)}">${esc(s.type || "Unknown type")}</h4><button class="tk-link" id="unsel">Close</button></div>
    <h3 class="tk-ship">${esc(s.name || "No name")}</h3>
    <dl class="tk-dl">
      <dt>MMSI</dt><dd>${esc(s.mmsi)}</dd>
      <dt>Length</dt><dd>${esc(s.length || "?")} m</dd>
      <dt>Heading to</dt><dd>${esc(s.dest || "?")}</dd>
      <dt>Status</dt><dd>${st ? esc(d.statuses[st.st]) : "–"}</dd>
      <dt>Speed</dt><dd>${st && st.sog >= 0 ? st.sog.toFixed(1) + " kn" : "–"}</dd>
      <dt>Position</dt><dd>${where}</dd>
      <dt>Last fix</dt><dd>${st ? (st.silent ? `<span class="warn">${dur(st.age)} ago</span>` : "now") : "–"}</dd>
      <dt>AIS gaps</dt><dd>${gaps.length ? gaps.map(dur).join(", ") : "none over 30 min"}</dd>
    </dl>
    <div class="tk-btns">
      <button id="follow" class="${S.follow ? "on" : ""}">${S.follow ? "Following" : "Follow"}</button>
      <button id="shipnote">Add note</button>
      <a class="tk-link" href="https://www.marinetraffic.com/en/ais/details/ships/mmsi:${encodeURIComponent(s.mmsi)}" target="_blank" rel="noopener">MarineTraffic ↗</a>
    </div>
    ${al.length ? `<ol class="tk-alerts small">${al.map((a) => `<li class="al-${KIND[a.kind][1]}"><span class="t">${hhmm(a.t)}</span><span><b>${KIND[a.kind][0]}</b> ${esc(a.text)}</span></li>`).join("")}</ol>` : ""}
    ${notes.length ? `<ol class="tk-notes small">${notes.map(noteLi).join("")}</ol>` : ""}`;
  $("#unsel").onclick = () => { S.follow = false; select(null); };
  $("#follow").onclick = () => { S.follow = !S.follow; renderShip(); };
  $("#shipnote").onclick = () => { if (st) startNote({ lat: st.lat, lon: st.lon, mmsi: s.mmsi, name: s.name }); };
}

map.on("click", (e) => {
  if (S.placingNote) { S.placingNote = false; map.getContainer().classList.remove("placing"); startNote({ lat: e.latlng.lat, lon: e.latlng.lng }); return; }
  if (map.pm.globalDrawModeEnabled()) return;
  const q = e.containerPoint;
  let best = null, bd = 14;
  for (const [x, y, s] of drawn) { const dd = Math.hypot(x - q.x, y - q.y); if (dd < bd) { bd = dd; best = s; } }
  select(best);
});

// ---------------------------------------------------------------- notes

const noteLayer = L.layerGroup().addTo(map);
function noteLi(n) {
  return `<li data-id="${n.id}"><i style="background:${NOTE_COLOR[n.tag]}"></i><span class="t">${hhmm(n.t - S.data.t0)}</span>
    <span>${n.name || n.mmsi ? `<b>${esc(n.name || n.mmsi)}</b> ` : ""}${esc(n.text)}</span><button class="tk-x" data-del="${n.id}" title="Delete">×</button></li>`;
}
function renderNotes() {
  noteLayer.clearLayers();
  for (const n of S.notes) {
    const icon = L.divIcon({ className: "tk-pin", html: `<span style="background:${NOTE_COLOR[n.tag]}"></span>`, iconSize: [16, 16], iconAnchor: [8, 16] });
    L.marker([n.lat, n.lon], { icon }).bindTooltip(`<b>${hhmm(n.t - S.data.t0)} UTC · ${esc(n.tag)}</b><br>${esc(n.text)}`).on("click", () => setTime(n.t - S.data.t0)).addTo(noteLayer);
  }
  $("#notes").innerHTML = S.notes.length ? S.notes.slice().sort((a, b) => a.t - b.t).map(noteLi).join("") : `<li class="none">No notes yet. Notes keep their place and time, and everyone sees them.</li>`;
  renderTimeline();
}
async function loadNotes() { S.notes = await (await fetch(`/api/notes?source=${S.src}`)).json(); renderNotes(); }
function startNote(where) {
  S.noteDraft = { ...where, t: epoch(S.t) };
  $("#noteform").classList.remove("hidden");
  $("#notewhere").textContent = `${where.name || where.mmsi ? (where.name || where.mmsi) + " · " : ""}${hhmm(S.t)} UTC · ${where.lat.toFixed(4)}, ${where.lon.toFixed(4)}`;
  $("#notetext").focus();
}
$("#noteBtn").onclick = () => { S.placingNote = true; map.getContainer().classList.add("placing"); };
$("#notecancel").onclick = () => { S.noteDraft = null; $("#noteform").classList.add("hidden"); };
$("#noteform").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("#notetext").value.trim(); if (!text || !S.noteDraft) return;
  await fetch("/api/notes", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ source: S.src, text, tag: $("#notetag").value, ...S.noteDraft }) });
  $("#notetext").value = ""; $("#noteform").classList.add("hidden"); S.noteDraft = null;
  await loadNotes(); if (S.sel) renderShip();
};
document.addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) { e.stopPropagation(); await fetch(`/api/notes/${del.dataset.del}`, { method: "DELETE" }); await loadNotes(); if (S.sel) renderShip(); return; }
  const li = e.target.closest(".tk-notes li[data-id]");
  if (li) { const n = S.notes.find((x) => x.id === li.dataset.id); setTime(n.t - S.data.t0); map.setView([n.lat, n.lon], Math.max(map.getZoom(), 12)); }
  const go = e.target.closest("[data-go]"); if (go) loadSource(go.dataset.go);
});

// ---------------------------------------------------------------- time control

function setTime(t) {
  S.t = Math.max(0, Math.min(S.tmax, t));
  S.atNow = S.data.kind !== "replay" && S.t >= S.tmax - 5;
  if (S.follow && S.sel) { const st = stateAt(S.sel, S.t); if (st) map.panTo([st.lat, st.lon], { animate: false }); }
  draw();
}
$("#scrub").addEventListener("input", (e) => setTime(+e.target.value));
$("#ticks").addEventListener("click", (e) => { const b = e.target.closest(".tick"); if (b) setTime(+b.dataset.t); });
$("#speed").onchange = (e) => { S.speed = +e.target.value; };
$("#trail").onchange = (e) => { S.trail = +e.target.value; draw(); };
function setPlaying(on) { S.playing = on; $("#play").textContent = on ? "❚❚" : "▶"; S.lastFrame = performance.now(); }
$("#play").onclick = () => { if (!S.playing && S.t >= S.tmax) setTime(0); setPlaying(!S.playing); };
function frame(now) {
  if (S.playing) {
    const dt = (now - S.lastFrame) / 1000; S.lastFrame = now;
    setTime(S.t + dt * S.speed);
    if (S.t >= S.tmax) setPlaying(false);
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
document.addEventListener("keydown", (e) => {
  if (["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement.tagName) && document.activeElement.type !== "range") return;
  if (e.code === "Space") { e.preventDefault(); $("#play").click(); }
  if (e.key === "ArrowRight") setTime(S.t + (e.shiftKey ? 600 : 60));
  if (e.key === "ArrowLeft") setTime(S.t - (e.shiftKey ? 600 : 60));
  if (e.key === "Escape") { select(null); S.placingNote = false; map.getContainer().classList.remove("placing"); }
});

// ---------------------------------------------------------------- sources

const SKAGEN_ANCHORAGE = [[57.6, 10.55], [57.705, 10.55], [57.705, 10.76], [57.6, 10.76]];
const DEFAULT_ZONE = { skagen: SKAGEN_ANCHORAGE, sim: SKAGEN_ANCHORAGE };
let liveTimer = null;

async function fetchSource(src) {
  const r = await fetch(`/api/tracks/${src}`);
  if (!r.ok) throw new Error((await r.json()).detail);
  return r.json();
}

function applyData(d, keepView) {
  const prevEpoch = S.data ? epoch(S.t) : null, selM = S.sel?.mmsi;
  S.data = d;
  S.tmax = d.kind === "replay" ? 86400 : Math.max(60, d.now);
  $("#scrub").max = S.tmax;
  if (!keepView) {
    const [w, s, e, n] = d.bbox; map.fitBounds([[s, w], [n, e]], { padding: [10, 10] });
  }
  S.sel = selM ? d.ships.find((x) => x.mmsi === selM) || null : null;
  $("#shipcard").classList.toggle("hidden", !S.sel);
  if (d.kind === "replay") S.t = prevEpoch == null ? 16 * 3600 + 53 * 60 : Math.min(S.tmax, prevEpoch - d.t0);
  else S.t = S.atNow || prevEpoch == null ? S.tmax : Math.max(0, Math.min(S.tmax, prevEpoch - d.t0));
  computeAlerts(); renderZone(); renderSource(); draw();
}

async function loadSource(src) {
  clearInterval(liveTimer);
  setPlaying(false);
  S.src = src; S.sel = null; S.scans = []; S.data = null; S.atNow = true; $("#scanout").innerHTML = "";
  document.querySelectorAll("#src button").forEach((b) => b.classList.toggle("on", b.dataset.src === src));
  $("#srcline").textContent = "Loading…";
  const d = await fetchSource(src);
  const saved = localStorage.getItem("zone:" + src);
  S.zone = saved ? JSON.parse(saved) : DEFAULT_ZONE[src] || null;
  showZoneLayer();
  applyData(d, false);
  await loadNotes();
  if (src !== "skagen") liveTimer = setInterval(async () => { try { applyData(await fetchSource(src), true); } catch (e) { /* next poll */ } }, 15000);
}
document.querySelectorAll("#src button").forEach((b) => b.addEventListener("click", () => loadSource(b.dataset.src)));

renderLegend();
resizeCanvas();
loadSource(new URLSearchParams(location.search).get("src") || "skagen");
