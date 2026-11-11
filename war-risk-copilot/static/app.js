const $ = (id) => document.getElementById(id);
const LEVEL_COLOR = { LOW: "#33d69f", ELEVATED: "#e6d34a", HIGH: "#ffb020", SEVERE: "#ff7a2f", EXTREME: "#ff4b3a" };
const fmtUSD = (n) => n >= 1e6 ? `$${(n / 1e6).toFixed(n >= 1e7 ? 1 : 2)}M` : `$${Math.round(n / 1e3)}k`;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let META, map, trackLayer, incLayer, shipMarker, anim, clockT, clockEnd;

const F = {
  name: $("f-name"), imo: $("f-imo"), type: $("f-type"), flag: $("f-flag"), hull: $("f-hull"), route: $("f-route"),
  speed: $("f-speed"), port: $("f-port"), nexus: $("f-nexus"), israel: $("f-israel"), saudi: $("f-saudi"),
};

async function init() {
  META = await (await fetch("/api/meta")).json();
  const f = META.fresh;
  const d = (s) => (s ? s.slice(0, 10) : "—");
  $("feeds").innerHTML = [
    ["PortWatch AIS", `to ${META.transits["Strait of Hormuz"]?.series.at(-1)?.[0] ?? "—"}`, META.sources.portwatch[1], f.portwatch],
    ["gpsjam", META.jam_day ?? "—", META.sources.gpsjam[1], f.gpsjam],
    ["OFAC SDN", `${META.sdn_count.toLocaleString()} vessels`, META.sources.ofac[1], f.sdn],
    ["JMIC", "update 112 · 8 Nov", META.jmic[112], "2026-11-08"],
  ].map(([n, v, u, ok]) => `<a class="feed${ok ? "" : " stale"}" href="${u}" target="_blank" title="fetched ${d(ok)}"><i></i>${n} · ${v}</a>`).join("");

  $("presets").innerHTML = META.presets.map((p) => `<button data-id="${p.id}">${esc(p.label)}</button>`).join("");
  $("presets").onclick = (e) => { const b = e.target.closest("button"); if (b) applyPreset(b.dataset.id); };
  F.route.innerHTML = Object.entries(META.routes).map(([k, r]) => `<option value="${k}" title="via ${esc(r.via)}">${esc(r.label)}</option>`).join("");

  initMap();
  renderFacts();
  $("sources").innerHTML = Object.values(META.sources).map(([t, u]) => `<a href="${u}" target="_blank">${esc(t)}</a>`).join("")
    + Object.entries(META.jmic).filter(([k]) => k != 112).map(([k, u]) => `<a href="${u}" target="_blank">JMIC Advisory Update ${k}</a>`).join("");

  Object.values(F).forEach((el) => el.addEventListener(el.type === "text" ? "input" : "change", () => { markCustom(); run(); }));
  F.hull.addEventListener("input", () => { $("v-hull").textContent = `$${F.hull.value}M`; });
  F.name.addEventListener("input", sdnSuggest);
  applyPreset("vlcc");
}

function applyPreset(id) {
  const p = META.presets.find((x) => x.id === id);
  const s = p.ship;
  Object.assign(F.name, { value: s.name }); F.imo.value = s.imo; F.type.value = s.type; F.flag.value = s.flag;
  F.hull.value = s.hull_usd_m; $("v-hull").textContent = `$${s.hull_usd_m}M`;
  F.route.value = p.route; F.speed.value = p.speed_kn; F.port.value = p.port_days;
  F.nexus.checked = s.nexus_us_uk_il; F.israel.checked = s.israel_call; F.saudi.checked = s.saudi_link;
  document.querySelectorAll(".presets button").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
  run();
}
const markCustom = () => document.querySelectorAll(".presets button").forEach((b) => b.classList.remove("on"));

let sdnT;
function sdnSuggest() {
  clearTimeout(sdnT);
  const q = F.name.value.trim();
  if (q.length < 3) return ($("sdn-hint").style.display = "none");
  sdnT = setTimeout(async () => {
    const r = await (await fetch(`/api/sdn?q=${encodeURIComponent(q)}`)).json();
    const h = $("sdn-hint");
    h.innerHTML = r.map((v) => `<div data-n="${esc(v.name)}" data-i="${v.imo}" data-f="${esc(v.flag)}"><b>SDN</b> ${esc(v.name)} · IMO ${v.imo || "—"} · ${esc(v.program)}</div>`).join("");
    h.style.display = r.length ? "block" : "none";
  }, 200);
}
$("sdn-hint").onclick = (e) => {
  const d = e.target.closest("div[data-n]"); if (!d) return;
  F.name.value = d.dataset.n; F.imo.value = d.dataset.i; F.flag.value = d.dataset.f; $("sdn-hint").style.display = "none"; run();
};

let runT;
function run() { clearTimeout(runT); runT = setTimeout(assess, 120); }

async function assess() {
  const body = {
    route: F.route.value, speed_kn: +F.speed.value || 13, port_days: +F.port.value || 0,
    ship: { name: F.name.value, imo: F.imo.value.trim(), type: F.type.value, hull_usd_m: +F.hull.value, flag: F.flag.value,
      nexus_us_uk_il: F.nexus.checked, israel_call: F.israel.checked, saudi_link: F.saudi.checked },
  };
  const r = await (await fetch("/api/assess", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })).json();
  renderVerdict(r); renderFactors(r); renderIncidents(r); renderTrack(r); renderChart(r);
}

/* ---------- map ---------- */
function initMap() {
  map = L.map("map", { zoomControl: false, attributionControl: true, worldCopyJump: true }).setView([22, 52], 5);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 16, attribution: "Esri, HERE, Garmin, &copy; OpenStreetMap · incidents: JMIC/UKMTO · jamming: gpsjam.org",
  }).addTo(map);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16, opacity: 0.7 }).addTo(map);
  L.polygon(META.listed_area, { color: "#ff4b3a", weight: 1.2, dashArray: "5 5", fillColor: "#ff4b3a", fillOpacity: 0.07, interactive: false }).addTo(map);
  const jamColor = (b) => (b >= 0.1 ? "#b46bff" : "#7a4cc0");
  META.jam.forEach((h) => L.polygon(h.poly, { stroke: false, fillColor: jamColor(h.bad), fillOpacity: h.bad >= 0.1 ? 0.55 : 0.22, interactive: false }).addTo(map));
  incLayer = L.layerGroup().addTo(map);
  trackLayer = L.layerGroup().addTo(map);
}

function incMarker(i, onTrack) {
  const cls = i.kind === "Attack" ? "" : " sus";
  const m = L.marker([i.lat, i.lon], { icon: L.divIcon({ className: "", html: `<div class="pulse${cls}${onTrack ? "" : " old"}"></div>`, iconSize: [14, 14] }) });
  m.bindPopup(`<b>${esc(i.ref)}</b> · ${i.date}<br><b>${esc(i.vessel)}</b> · ${esc(i.vtype)} · ${esc(i.kind)}<br>${esc(i.text)}${i.approx ? "<br><i>Position approximate (no coordinates in report)</i>" : ""}<br><a href="${META.jmic[i.upd]}" target="_blank">JMIC update ${i.upd} →</a>`);
  return m;
}

function segColor(v) { return v >= 0.95 ? "#ff4b3a" : v >= 0.65 ? "#ff7a2f" : v >= 0.35 ? "#ffb020" : "#5f8f7e"; }

function renderTrack(r) {
  trackLayer.clearLayers(); incLayer.clearLayers();
  const on = new Set(r.incidents.map((i) => i.ref));
  META.incidents.forEach((i) => incLayer.addLayer(incMarker(i, on.has(i.ref))));
  const t = r.track;
  for (let k = 0; k < t.length - 1; k++) {
    const seg = [[t[k][0], t[k][1]], [t[k + 1][0], t[k + 1][1]]];
    const c = segColor(t[k][2]);
    trackLayer.addLayer(L.polyline(seg, { color: c, weight: 10, opacity: 0.18, interactive: false }));
    trackLayer.addLayer(L.polyline(seg, { color: c, weight: 3, opacity: 0.95, interactive: false }));
  }
  // focus on the listed-area part of the track
  const focus = t.filter((p) => p[1] < 66 && p[0] > -2);
  map.flyToBounds(L.latLngBounds(focus.map((p) => [p[0], p[1]])).pad(0.18), { duration: 0.8, maxZoom: 7 });
  shipMarker = L.marker([focus[0][0], focus[0][1]], { icon: L.divIcon({ className: "", html: '<div class="shipicon"></div>', iconSize: [16, 16] }), interactive: false }).addTo(trackLayer);
  cancelAnimationFrame(anim);
  let i = 0, f = 0;
  const step = () => {
    f += 0.08; if (f >= 1) { f = 0; i = (i + 1) % (focus.length - 1); }
    const a = focus[i], b = focus[i + 1];
    shipMarker.setLatLng([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
    anim = requestAnimationFrame(step);
  };
  anim = requestAnimationFrame(step);
  const p = r.price;
  $("maptag").innerHTML = `<b>${esc(r.route.label)}</b><br>${p.km_in_area.toLocaleString()} km inside listed area<br>${p.days_in_area} days exposed → ${p.blocks} × 7-day block${p.blocks > 1 ? "s" : ""}`;
}

/* ---------- verdict ---------- */
function renderVerdict(r) {
  const c = LEVEL_COLOR[r.level];
  const ang = (v) => Math.PI * (1 - v / 100);
  const pt = (v, rad) => [100 + rad * Math.cos(ang(v)), 105 - rad * Math.sin(ang(v))];
  const arc = (v0, v1, rad) => { const [x0, y0] = pt(v0, rad), [x1, y1] = pt(v1, rad); return `M${x0},${y0} A${rad},${rad} 0 0 1 ${x1},${y1}`; };
  let ticks = "";
  for (let v = 0; v <= 100; v += 5) { const [x0, y0] = pt(v, 92), [x1, y1] = pt(v, v % 25 ? 88 : 84); ticks += `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="#3a414b" stroke-width="1"/>`; }
  $("dial").innerHTML = `<defs><filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
    ${ticks}<path d="${arc(0, 100, 78)}" stroke="#20252d" stroke-width="12" fill="none" stroke-linecap="round"/>
    <path d="${arc(0, Math.max(1, r.score), 78)}" stroke="${c}" stroke-width="12" fill="none" stroke-linecap="round" filter="url(#glow)"/>`;
  animateNum($("score"), r.score);
  Object.assign($("level").style, { background: c + "22", color: c });
  $("level").textContent = r.level;

  const p = r.price;
  if (!p.insurable) {
    $("usd").className = "usd no"; $("usd").textContent = "No Western market quote";
    $("pct").textContent = `OFAC SDN: ${r.sdn.name} · IMO ${r.sdn.imo} · ${r.sdn.program}`;
    $("bandbar").innerHTML = "";
    $("vs").innerHTML = `Sanctioned ships breach the sanctions warranty in Gulf war cover (Britannia P&I). The risk score still shows what the voyage itself faces.`;
  } else {
    $("usd").className = "usd"; $("usd").textContent = `${fmtUSD(p.lo_usd)} – ${fmtUSD(p.hi_usd)}`;
    $("pct").textContent = `${p.lo_pct}% – ${p.hi_pct}% of $${r.ship.hull_usd_m}M hull · ${p.blocks} × 7-day block`;
    const b = p.band, w = b.hi - b.lo;
    $("bandbar").innerHTML = `<div class="track"></div><div class="fill" style="left:${((p.lo_pct - b.lo) / w) * 100}%;width:${Math.max(2, ((p.hi_pct - p.lo_pct) / w) * 100)}%"></div>
      <span class="t" style="left:0">${b.lo}%</span><span class="t" style="right:0">${b.hi}%</span>`;
    const mult = Math.round(((p.lo_usd + p.hi_usd) / 2) / p.prewar_usd);
    $("vs").innerHTML = `<span class="mult">×${mult}</span> the pre-war price of <b>${fmtUSD(p.prewar_usd)}</b> (0.25%).<br>Market band: <a href="${b.url}" target="_blank">${esc(b.label)}, ${b.lo}–${b.hi}%</a>.`
      + (r.link_flags.length ? `<br><span style="color:var(--red)">Linked ship: priced at the top of the band; Lloyd's List reports ~3× others, and Britannia's excess War P&I needs prior approval.</span>` : "");
  }
  const tr = r.trend, tc = { worsening: "#ff4b3a", easing: "#33d69f", steady: "#ffb020" }[tr];
  const t = r.traffic;
  $("trend").innerHTML = `<span class="tag" style="background:${tc}22;color:${tc}">${tr.toUpperCase()}</span>`
    + `Attacks on track: last 7 days vs prior 7. ${t ? `Transits ${t.avg7}/day vs ${t.prev7} the week before.` : ""}`
    + (tr === "easing" ? " A short wait may re-price lower; recheck tomorrow." : tr === "worsening" ? " Waiting is unlikely to help." : "");
  startClock(r.route.via === "Strait of Hormuz" ? 12 : 24);
}

function startClock(h) {
  clockEnd = Date.now() + h * 3600e3;
  clearInterval(clockT);
  const tick = () => {
    const s = Math.max(0, Math.round((clockEnd - Date.now()) / 1000));
    $("clock").textContent = [s / 3600, (s % 3600) / 60, s % 60].map((x) => String(Math.floor(x)).padStart(2, "0")).join(":");
  };
  tick(); clockT = setInterval(tick, 1000);
  $("clock").title = h === 12 ? "AFP: owners normally get 24 h to accept a listed-area quote, cut to 12 h for Hormuz" : "Marsh: quotes now valid for 24 hours";
}

function animateNum(el, to) {
  const from = +el.textContent || 0, t0 = performance.now();
  const f = (t) => { const k = Math.min(1, (t - t0) / 600); el.textContent = Math.round(from + (to - from) * (1 - (1 - k) ** 3)); if (k < 1) requestAnimationFrame(f); };
  requestAnimationFrame(f);
}

/* ---------- panels ---------- */
function renderFactors(r) {
  $("factors").innerHTML = r.factors.map((f) => {
    const v = Math.round(f.value * 100), c = v >= 80 ? "#ff4b3a" : v >= 50 ? "#ff7a2f" : v >= 25 ? "#ffb020" : "#33d69f";
    return `<div class="fac"><div class="nm">${esc(f.label)}<small>weight ${Math.round(f.weight * 100)}%</small></div>
      <div class="bar"><i style="width:${v}%;background:${c}"></i></div><div class="v">${v}</div><div class="dt">${esc(f.detail)}</div></div>`;
  }).join("");
}

function renderIncidents(r) {
  if (!r.incidents.length) return ($("incidents").innerHTML = `<li class="empty">No confirmed incidents within 25 NM of this track in 21 days.</li>`);
  $("incidents").innerHTML = r.incidents.map((i) => `<li data-lat="${i.lat}" data-lon="${i.lon}">
    <div class="d">${i.date.slice(5)}<br>${i.age}d ago</div>
    <div class="t"><span class="k ${i.kind}">${i.kind.toUpperCase()}</span><b>${esc(i.vessel)}</b> · ${esc(i.vtype)} · ${esc(i.region)}<br>${esc(i.text)}
    <a href="${META.jmic[i.upd]}" target="_blank">${esc(i.ref)} · JMIC ${i.upd}</a></div></li>`).join("");
  $("incidents").onclick = (e) => {
    const li = e.target.closest("li[data-lat]"); if (!li || e.target.tagName === "A") return;
    map.flyTo([+li.dataset.lat, +li.dataset.lon], 9, { duration: 0.8 });
    window.scrollTo({ top: document.querySelector(".mapwrap").offsetTop - 20, behavior: "smooth" });
  };
}

function renderChart(r) {
  const cp = META.transits[r.route.via];
  if (!cp) return;
  const s = cp.series.filter((x) => x[0] >= "2025-12-01");
  const W = 560, H = 210, pl = 30, pr = 10, pt = 14, pb = 24;
  const ymax = Math.max(cp.baseline * 1.25, ...s.map((x) => x[1]));
  const X = (i) => pl + (i / (s.length - 1)) * (W - pl - pr), Y = (v) => pt + (1 - v / ymax) * (H - pt - pb);
  const line = s.map((x, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(x[1]).toFixed(1)}`).join("");
  const ma = s.map((x, i) => { const w = s.slice(Math.max(0, i - 6), i + 1); return w.reduce((a, b) => a + b[1], 0) / w.length; });
  const maLine = ma.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const ci = s.findIndex((x) => x[0] >= "2026-02-28");
  const months = s.map((x, i) => [x[0], i]).filter(([d]) => d.endsWith("-01"));
  $("chart").innerHTML = `
    <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#ffb020" stop-opacity=".35"/><stop offset="1" stop-color="#ffb020" stop-opacity="0"/></linearGradient></defs>
    ${months.map(([d, i]) => `<line x1="${X(i)}" x2="${X(i)}" y1="${pt}" y2="${H - pb}" stroke="#1c2128"/><text x="${X(i) + 3}" y="${H - 8}" fill="#7d838d" font-size="9.5" font-family="Geist Mono">${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+d.slice(5, 7) - 1]}</text>`).join("")}
    <line x1="${pl}" x2="${W - pr}" y1="${Y(cp.baseline)}" y2="${Y(cp.baseline)}" stroke="#33d69f" stroke-dasharray="4 4"/>
    <text x="${W - pr - 4}" y="${Y(cp.baseline) - 5}" text-anchor="end" fill="#33d69f" font-size="10" font-family="Geist Mono">pre-crisis avg ${cp.baseline}/day</text>
    ${ci > 0 ? `<line x1="${X(ci)}" x2="${X(ci)}" y1="${pt}" y2="${H - pb}" stroke="#ff4b3a" stroke-dasharray="2 3"/><text x="${X(ci) + 4}" y="${pt + 9}" fill="#ff4b3a" font-size="10" font-family="Geist Mono">28 Feb strikes</text>` : ""}
    <path d="${line}L${X(s.length - 1)},${H - pb}L${pl},${H - pb}Z" fill="url(#ga)"/>
    <path d="${line}" stroke="#ffb020" stroke-opacity=".45" stroke-width="1" fill="none"/>
    <path d="${maLine}" stroke="#ffb020" stroke-width="2" fill="none"/>
    <circle cx="${X(s.length - 1)}" cy="${Y(ma.at(-1))}" r="4" fill="#ffb020"/>
    <text x="${pl - 4}" y="${Y(0) + 3}" text-anchor="end" fill="#7d838d" font-size="9.5" font-family="Geist Mono">0</text>
    <text x="${pl - 4}" y="${Y(Math.round(ymax)) + 9}" text-anchor="end" fill="#7d838d" font-size="9.5" font-family="Geist Mono">${Math.round(ymax)}</text>`;
  const t = r.traffic;
  $("chart-note").innerHTML = `${esc(r.route.via)}: <b>${t.avg7}</b> transits/day over the 7 days to ${t.last_day}, <b>${Math.round(t.ratio * 100)}%</b> of normal. `
    + (r.route.via === "Strait of Hormuz" ? `AIS-visible only; JMIC also reports ~${META.jmic_stats.us_facilitated_per_day} US-facilitated transits/day and ~${META.jmic_stats.anchored_east_of_hormuz} ships anchored east of the strait.` : "AIS-visible only.");
}

function renderFacts() {
  $("facts").innerHTML = META.facts.map((f) => `<div class="fact"><div class="c"><span class="vd ${f.verdict}">${f.verdict === "holds" ? "HOLDS" : "PARTLY"}</span>${esc(f.claim)}</div>
    <div class="n">${esc(f.note)}${f.src.map((k) => `<a href="${META.sources[k][1]}" target="_blank">${esc(META.sources[k][0].split(":")[0].split(",")[0])} →</a>`).join("")}</div></div>`).join("");
}

init();
