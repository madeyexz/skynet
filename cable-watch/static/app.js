// Cable Watch front end: canvas map, replay animation, live feed, ranked list and vessel detail.
const $ = (s) => document.querySelector(s);
const CASE = "yi-peng-3";
const CASE_MMSI = 414270000;
const KIND = { stopped: "停留／下錨", slow: "低速徘徊", zigzag: "Z 字形", speed_drop: "異常減速", ais_gap: "AIS 中斷", watchlist: "名單", flag: "權宜船旗" };
const TYPE = { Cargo: "貨輪", Tanker: "油輪", Passenger: "客輪", Fishing: "漁船", HSC: "高速船", Other: "其他", Undefined: "未知" };
const VIEWS = { replay: { lon: 14.3, lat: 55.9, k: 150 }, live: { lon: 24.3, lat: 59.85, k: 230 }, taiwan: { lon: 120.6, lat: 24.3, k: 240 } };
const REGION = { replay: "baltic", live: "baltic", taiwan: "taiwan" };
const LAYERS = {};
// When each cable in the replay was cut (UTC, from the sources in EVENTS); both were repaired after the window.
const CUTS = [{ name: "BCS East-West Interlink", t: Date.UTC(2024, 10, 17, 7, 50) / 1000 },
              { name: "C-Lion1", t: Date.UTC(2024, 10, 18, 2, 0) / 1000 }];
// Sourced events of the November 2024 case (UTC).
const EVENTS = [
  { t: Date.UTC(2024, 10, 17, 7, 50) / 1000, label: "BCS East-West 斷線", cut: true, text: "BCS East-West（立陶宛–哥特蘭）斷線；RIPE Atlas 量到 20% 路徑延遲增加 10–20 ms", src: "RIPE Labs" },
  { t: Date.UTC(2024, 10, 17, 22, 41) / 1000, label: "YP3 重開 AIS", text: "Yi Peng 3 關掉 AIS 約 7.5 小時後，在厄蘭島南方重新開啟", src: "Wikipedia／Kpler", low: true },
  { t: Date.UTC(2024, 10, 18, 2, 0) / 1000, label: "C-Lion1 斷線", cut: true, text: "C-Lion1（赫爾辛基–羅斯托克）斷線；Cinia 回報，RIPE Atlas 量到約 5 ms 延遲跳升", src: "Cinia／RIPE Labs" },
  { t: 1731909704, label: "YP3 進入丹麥範圍", text: "丹麥海事局 AIS 第一次收到 Yi Peng 3（博恩霍爾姆島以東），已是兩次斷線之後", src: "aisdata.ais.dk", low: true },
];

const S = { mode: "replay", view: { ...VIEWS.replay }, t: 0, playing: false, speed: 1, sel: null, data: null, traffic: null,
  live: null, cables: null, land: null, base: null, shown: new Set(), ticker: [], lastFrame: 0 };
const cv = $("#map"), ctx = cv.getContext("2d");
const trail = document.createElement("canvas"), tctx = trail.getContext("2d");
let W = 0, H = 0, DPR = 1;

// ---------- projection ----------
let COS = Math.cos((57 * Math.PI) / 180);
const px = (lon, lat) => [W / 2 + (lon - S.view.lon) * COS * S.view.k, H / 2 - (lat - S.view.lat) * S.view.k];
const unpx = (x, y) => [S.view.lon + (x - W / 2) / (COS * S.view.k), S.view.lat - (y - H / 2) / S.view.k];

function resize() {
  DPR = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  W = r.width; H = r.height;
  for (const c of [cv, trail]) { c.width = W * DPR; c.height = H * DPR; }
  tctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  S.base = null;
}

function drawBase() {
  const b = document.createElement("canvas");
  b.width = W * DPR; b.height = H * DPR;
  const g = b.getContext("2d");
  g.setTransform(DPR, 0, 0, DPR, 0, 0);
  g.fillStyle = "#1d1a17"; g.fillRect(0, 0, W, H);
  // graticule
  g.strokeStyle = "rgba(251,250,247,.05)"; g.lineWidth = 1;
  for (let lon = 0; lon <= 35; lon++) { const [x] = px(lon, 0); g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke(); }
  for (let lat = 50; lat <= 70; lat++) { const [, y] = px(0, lat); g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke(); }
  g.fillStyle = "#3b362f";
  for (const f of S.land.features) poly(g, f.geometry, true);
  const lines = (kind) => S.cables.features.filter((f) => f.properties.kind === kind);
  g.save();
  g.setLineDash([5, 4]); g.strokeStyle = "rgba(126,163,181,.55)"; g.lineWidth = 1.2;
  for (const f of lines("pipeline")) poly(g, f.geometry, false);
  g.restore();
  g.save();
  g.strokeStyle = "rgba(232,176,74,.22)"; g.lineWidth = 5;
  for (const f of lines("telecom")) poly(g, f.geometry, false);
  g.strokeStyle = "rgba(240,190,90,.85)"; g.lineWidth = 1.3;
  for (const f of lines("telecom")) poly(g, f.geometry, false);
  g.restore();
  // labels for the cables in the story
  g.font = "11px " + getComputedStyle(document.body).fontFamily; g.fillStyle = "rgba(240,190,90,.8)";
  const seen = new Set(), placed = [];
  for (const f of S.cables.features) {
    const n = f.properties.name;
    if (seen.has(n) || !/C-Lion1|BCS East-West|Balticconnector|Nord Stream 1$|NordBalt|Estonia|EESF|FEC-|Baltica|Latvia-Sweden|TPKM|Trans-Pacific Express|Taiwan Strait Express|Taiwan-Matsu|Cross-Straits/.test(n)) continue;
    const c = f.geometry.type === "LineString" ? [f.geometry.coordinates] : f.geometry.coordinates;
    const line = c.reduce((a, b) => (b.length > a.length ? b : a));
    const [x, y] = px(...line[Math.floor(line.length / 2)]);
    if (x < 10 || y < 60 || x > W - 120 || y > H - 80 || placed.some(([a, b]) => Math.abs(a - x) < 130 && Math.abs(b - y) < 16)) continue;
    seen.add(n); placed.push([x, y]); g.fillText(n, x + 4, y - 4);
  }
  S.base = b;
}

function poly(g, geom, fill) {
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates
    : geom.type === "LineString" ? [[geom.coordinates]] : geom.type === "MultiLineString" ? geom.coordinates.map((l) => [l]) : [];
  g.beginPath();
  for (const rings of polys) for (const ring of rings) ring.forEach((c, i) => { const [x, y] = px(c[0], c[1]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  fill ? g.fill() : g.stroke();
}

// ---------- replay traffic ----------
function prepTraffic(tr) {
  const n = tr.mmsi.length;
  tr.at = tr.frames.map((f) => { const a = new Int32Array(n).fill(-1); for (let i = 0; i < f.length; i += 3) a[f[i]] = i; return a; });
  tr.t1 = tr.t0 + tr.step * (tr.frames.length - 1);
  return tr;
}

function shipsAt(t) {
  const tr = S.traffic, f = Math.max(0, Math.min(tr.frames.length - 1.001, (t - tr.t0) / tr.step));
  const k = Math.floor(f), u = f - k, A = tr.frames[k], B = tr.frames[k + 1], bi = tr.at[k + 1], out = [];
  for (let i = 0; i < A.length; i += 3) {
    let lon = A[i + 1], lat = A[i + 2];
    const j = bi ? bi[A[i]] : -1;
    if (j >= 0) { lon += (B[j + 1] - lon) * u; lat += (B[j + 2] - lat) * u; }
    out.push([tr.mmsi[A[i]], lon / 1e4, lat / 1e4]);
  }
  return out;
}

function vesselPos(v, t) {
  const tr = v.track;
  if (!tr || t < tr[0][2] || t > tr[tr.length - 1][2] + 900) return null;
  let i = tr.findIndex((p) => p[2] > t);
  if (i < 0) return [tr[tr.length - 1][0], tr[tr.length - 1][1]];
  if (i === 0) return [tr[0][0], tr[0][1]];
  const a = tr[i - 1], b = tr[i], u = (t - a[2]) / (b[2] - a[2]);
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
}

// ---------- drawing ----------
function frame(now) {
  requestAnimationFrame(frame);
  const dt = Math.min(0.1, (now - S.lastFrame) / 1000); S.lastFrame = now;
  if (!S.cables || !S.land) return;
  if (!S.base) { drawBase(); tctx.clearRect(0, 0, W, H); }
  if (S.mode === "replay" && S.traffic) {
    if (S.playing) {
      S.t += dt * 2880 * S.speed; // 1× = 48 minutes of AIS per second
      if (S.t >= S.traffic.t1) { S.t = S.traffic.t1; S.playing = false; $("#play").textContent = "▶"; }
      updateTicker();
    }
  }
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.drawImage(S.base, 0, 0, W, H);
  const pulse = (Math.sin(now / 260) + 1) / 2;
  drawCut(now);
  if (S.mode === "taiwan") drawTaiwan(pulse); else if (S.mode === "replay" && S.data) drawReplay(pulse); else if (S.live) drawLive(pulse);
  drawClock();
}

// Cables that are broken at the current moment turn red; for the first hours after the cut they flash.
function drawCut(now) {
  let cut = [];
  if (S.mode === "replay" && S.traffic) cut = CUTS.filter((c) => S.t >= c.t).map((c) => ({ ...c, age: S.t - c.t }));
  else if (S.mode === "taiwan" && S.selInc) cut = (S.selInc.cables || []).map((name) => ({ name, age: 1e9 }));
  for (const c of cut) {
    const fs = S.cables.features.filter((f) => f.properties.name === c.name);
    const fresh = c.age < 3 * 3600, flash = fresh ? (Math.sin(now / 120) + 1) / 2 : 0;
    ctx.save();
    ctx.strokeStyle = `rgba(255,90,95,${fresh ? 0.25 + 0.45 * flash : 0.3})`; ctx.lineWidth = fresh ? 9 + 8 * flash : 7;
    for (const f of fs) poly(ctx, f.geometry, false);
    ctx.strokeStyle = "#ff5a5f"; ctx.lineWidth = 2.2;
    for (const f of fs) poly(ctx, f.geometry, false);
    ctx.restore();
    const line = fs.flatMap((f) => (f.geometry.type === "LineString" ? [f.geometry.coordinates] : f.geometry.coordinates))
      .reduce((a, b) => (b.length > a.length ? b : a), []);
    if (line.length) { const [x, y] = px(...line[Math.floor(line.length / 2)]); label(x, y + 18, c.name + " 斷線", "#ff8a8d"); }
  }
}

function drawReplay(pulse) {
  const flagged = S.byMmsi;
  // comet trails for all traffic
  tctx.globalCompositeOperation = "destination-out";
  tctx.fillStyle = "rgba(0,0,0,.10)"; tctx.fillRect(0, 0, W, H);
  tctx.globalCompositeOperation = "source-over";
  const ships = shipsAt(S.t);
  tctx.fillStyle = "rgba(220,214,200,.55)";
  for (const [m, lon, lat] of ships) {
    const v = flagged.get(m);
    if (v && v.score >= 40) continue;
    const [x, y] = px(lon, lat); tctx.fillRect(x - 0.9, y - 0.9, 1.8, 1.8);
  }
  ctx.drawImage(trail, 0, 0, W, H);
  // selected vessel's whole track
  if (S.sel?.track) drawTrack(S.sel.track);
  // flagged ships, glowing when one of their signals is active
  for (const v of S.top) {
    const p = vesselPos(v, S.t); if (!p) continue;
    const [x, y] = px(...p);
    const active = v.signals.some((s) => s.lon != null && S.t >= s.start - 1800 && S.t <= s.end + 1800);
    const sanc = v.signals.some((s) => s.kind === "watchlist");
    dot(x, y, sanc ? "#e5484d" : "#d9622b", active || sanc ? 3.6 : 2.6, active ? pulse : 0);
    if ((active && v.score >= 60) || sanc || v === S.sel) label(x, y, (sanc ? "⚑ " : "") + (v.name || v.mmsi), sanc ? "#ff8a8d" : "#f0a27c");
  }
  const cs = S.byMmsi.get(CASE_MMSI);
  if (cs && !S.top.includes(cs)) {
    const p = vesselPos(cs, S.t);
    if (p) { const [x, y] = px(...p); dot(x, y, "#f6dd8f", 3.8, pulse); label(x, y, "YI PENG 3", "#f6dd8f"); }
  }
}

function drawLive(pulse) {
  tctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "rgba(230,224,210,.75)";
  for (const [lon, lat, , m, sc] of S.live.positions) {
    if (sc >= 40) continue;
    const [x, y] = px(lon, lat); ctx.beginPath(); ctx.arc(x, y, sc > 0 ? 2.4 : 1.6, 0, 7); ctx.fill();
  }
  if (S.sel?.track) drawTrack(S.sel.track);
  for (const v of S.top) {
    const [x, y] = px(v.last[0], v.last[1]);
    dot(x, y, "#d9622b", 3.4, pulse); label(x, y, v.name || v.mmsi, "#f0a27c");
  }
}

function drawTrack(tr) {
  ctx.save(); ctx.strokeStyle = "rgba(246,221,143,.9)"; ctx.lineWidth = 1.6; ctx.shadowColor = "#f6dd8f"; ctx.shadowBlur = 6;
  ctx.beginPath();
  tr.forEach((p, i) => { const [x, y] = px(p[0], p[1]); i && p[2] - tr[i - 1][2] < 2700 ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.stroke(); ctx.restore();
  // AIS gaps as dashed jumps, signal spots as rings
  ctx.save(); ctx.setLineDash([3, 4]); ctx.strokeStyle = "rgba(246,221,143,.6)"; ctx.beginPath();
  tr.forEach((p, i) => { if (i && p[2] - tr[i - 1][2] >= 2700) { const a = px(tr[i - 1][0], tr[i - 1][1]), b = px(p[0], p[1]); ctx.moveTo(...a); ctx.lineTo(...b); } });
  ctx.stroke(); ctx.restore();
  for (const s of S.sel.signals || []) if (s.lon != null) {
    const [x, y] = px(s.lon, s.lat);
    ctx.strokeStyle = "#d9622b"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 9, 0, 7); ctx.stroke();
  }
}

function dot(x, y, color, r, glow) {
  ctx.save(); ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 8 + glow * 14;
  ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
  if (glow) { ctx.globalAlpha = 0.6 * (1 - glow); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, r + 4 + glow * 12, 0, 7); ctx.stroke(); }
  ctx.restore();
}

function label(x, y, text, color) {
  ctx.font = "600 11px " + getComputedStyle(document.body).fontFamily;
  ctx.fillStyle = "rgba(29,26,23,.75)"; const w = ctx.measureText(text).width;
  ctx.fillRect(x + 7, y - 17, w + 8, 15); ctx.fillStyle = color; ctx.fillText(text, x + 11, y - 6);
}

const fmtT = (t, opt) => new Date(t * 1000).toLocaleString("zh-TW", { timeZone: "UTC", hour12: false, ...opt });
function drawClock() {
  const t = S.mode === "replay" ? S.t : S.live?.summary.end || Date.now() / 1000;
  $("#clock-date").textContent = fmtT(t, { year: "numeric", month: "long", day: "numeric", weekday: "short" }) + " · UTC";
  $("#clock-time").textContent = fmtT(t, { hour: "2-digit", minute: "2-digit" });
  if (S.mode === "replay" && S.traffic) {
    const p = (S.t - S.traffic.t0) / (S.traffic.t1 - S.traffic.t0) * 100;
    $("#tl-fill").style.width = p + "%"; $("#tl-knob").style.left = p + "%";
    const near = EVENTS.filter((e) => S.t >= e.t && S.t - e.t < 4 * 3600).pop();
    $("#clock-note").textContent = near ? near.text : "";
  }
}

// ---------- ticker ----------
function updateTicker() {
  const items = [];
  for (const e of EVENTS) if (e.t <= S.t && !S.shown.has(e)) { S.shown.add(e); items.push({ t: e.t, html: `<b>${e.label}</b>　${e.src}`, cls: e.cut ? "incident cut" : "incident" }); }
  for (const v of S.top) for (const s of v.signals) {
    if (s.lon == null || s.start > S.t || S.shown.has(s)) continue;
    S.shown.add(s);
    if (S.t - s.start > 6 * 3600) continue;
    items.push({ t: s.start, html: `<b>${v.name || v.mmsi}</b>　${KIND[s.kind]} · ${s.cable || ""}`, v });
  }
  items.sort((a, b) => a.t - b.t);
  for (const it of items) {
    const li = document.createElement("li");
    li.className = it.cls || ""; li.innerHTML = `<time>${fmtT(it.t, { hour: "2-digit", minute: "2-digit" })}</time>${it.html}`;
    if (it.v) li.onclick = () => select(it.v);
    $("#ticker").prepend(li);
  }
  while ($("#ticker").children.length > 5) $("#ticker").lastElementChild.remove();
}
function resetTicker() {
  S.shown = new Set(); $("#ticker").innerHTML = "";
  for (const e of EVENTS) if (e.t <= S.t) S.shown.add(e);
  for (const v of S.top || []) for (const s of v.signals) if (s.start <= S.t) S.shown.add(s);
  tctx.clearRect(0, 0, W, H);
}

// ---------- side panel ----------
function funnel(sm, liveNote) {
  const first = liveNote === "taiwan" ? `艘非漁船在臺灣周邊徘徊或停留（GFW，${sm.events.toLocaleString()} 次）`
    : liveNote ? "艘船目前在 Digitraffic AIS 範圍內" : "艘船出現在丹麥海事局 AIS（3 天）";
  const steps = [
    [sm.ships, first],
    [sm.near_cable, liveNote === "taiwan" ? "艘停在海纜 20 公里內" : "艘曾經過海纜或管線 20 公里內"],
    [sm.flagged, "艘有至少一個可疑訊號"],
    [sm.medium + sm.high, `艘分數 ≥ 40，需要人看（其中 ${sm.high} 艘 ≥ 60）`],
  ];
  const sanc = (S.top || []).filter((v) => v.signals.some((x) => x.kind === "watchlist")).length;
  $("#funnel").innerHTML = steps.map(([n, t], i) => `<div class="step ${i === 3 ? "last" : ""}">
    <div class="bar" style="width:${(100 * n) / Math.max(1, steps[0][0])}%"></div>
    <div class="n">${n.toLocaleString()}</div><div class="t">${t}</div></div>`).join("")
    + (sanc ? `<div class="sanc">其中 <b>${sanc}</b> 艘在制裁或影子船隊名單上（OpenSanctions）</div>` : "");
}

function row(v) {
  const top = [...v.signals].sort((a, b) => b.points - a.points)[0];
  const li = document.createElement("li");
  if (v.mmsi === CASE_MMSI) li.className = "case";
  const sanc = v.signals.some((x) => x.kind === "watchlist") ? `<span class="tag">制裁名單</span>` : "";
  li.innerHTML = `<div class="score ${v.tier}">${v.score}</div><div class="name">${v.name || "（無船名）"}${sanc}</div>
    <div class="meta">${TYPE[v.type] || v.type || "類型未知"} · MMSI ${v.mmsi} · ${[...new Set(v.signals.map((s) => KIND[s.kind]))].join("、")}</div>
    <div class="why">${top ? top.reason : ""}</div>`;
  li.onclick = () => select(v);
  return li;
}

function renderList() {
  const list = $("#list"); list.innerHTML = "";
  if (S.mode === "replay") {
    const cs = S.byMmsi.get(CASE_MMSI);
    if (cs) list.append(row(cs));
  }
  S.top.forEach((v) => v.mmsi !== CASE_MMSI && list.append(row(v)));
  if (!S.top.length) list.innerHTML = `<li class="empty" style="padding-left:12px;cursor:default">目前沒有分數 ≥ 40 的船。即時模式從伺服器啟動後才開始累積軌跡，停留、徘徊這類訊號要累積 30 分鐘以上才會出現。</li>`;
}

async function select(v) {
  if (S.mode === "live" && !v.track) v = { ...v, ...(await (await fetch(`/api/live/${v.mmsi}`)).json()) };
  S.sel = v;
  const tr = v.track || [];
  if (tr.length) {
    const lons = tr.map((p) => p[0]), lats = tr.map((p) => p[1]);
    const span = Math.max((Math.max(...lons) - Math.min(...lons)) * COS, Math.max(...lats) - Math.min(...lats), 0.15);
    S.view = { lon: (Math.max(...lons) + Math.min(...lons)) / 2, lat: (Math.max(...lats) + Math.min(...lats)) / 2, k: Math.min(1800, 0.6 * Math.min(W, H) / span) };
    S.base = null;
  } else if (S.mode === "taiwan" && v.signals.some((sg) => sg.box)) {
    const bs = v.signals.filter((sg) => sg.box).map((sg) => sg.box);
    const w = Math.min(...bs.map((b) => b[0])), so = Math.min(...bs.map((b) => b[1])), e = Math.max(...bs.map((b) => b[2])), n = Math.max(...bs.map((b) => b[3]));
    const span = Math.max((e - w) * COS, n - so, 0.08);
    S.view = { lon: (w + e) / 2, lat: (so + n) / 2, k: Math.min(3000, 0.55 * Math.min(W, H) / span) }; S.base = null;
  } else if (v.last) {
    S.view = { lon: v.last[0], lat: v.last[1], k: 700 }; S.base = null;
  }
  if (S.mode === "replay" && v.signals.length) {
    const first = v.signals.find((s) => s.lon != null);
    if (first) { S.t = Math.max(S.traffic.t0, first.start - 3 * 3600); resetTicker(); }
  }
  const d = $("#panel-detail");
  const caseNote = v.mmsi === CASE_MMSI ? `<div class="note">兩條海纜在 11/17 07:50 與 11/18 02:00 UTC 斷線時，Yi Peng 3 都在附近（Kpler、MarineTraffic）。
    兩個斷點都在丹麥 AIS 範圍以東，這份免費資料 11/18 06:01 才第一次收到它，所以這裡只看得到它之後的航程和 11/19 在卡特加特海峽停船。
    要抓到斷線當下，需要芬蘭或瑞典的 AIS 歷史，或商用衛星 AIS。</div>` : "";
  d.innerHTML = `<button class="back">← 回到清單</button>
    <div class="dhead"><div class="score ${v.tier}">${v.score}</div><div><h2>${v.name || "（無船名）"}</h2>
    <div class="meta">${TYPE[v.type] || v.type || "類型未知"} · MMSI ${v.mmsi}${v.imo ? " · IMO " + v.imo : ""}${v.length ? " · " + v.length + " m" : ""} · 跨越海纜 ${v.crossings} 次</div></div></div>
    ${caseNote}
    ${v.signals.map((s, i) => `<div class="sig"><div class="k">${KIND[s.kind]}<em>+${s.points} 分${s.cable ? " · " + s.cable : ""}</em></div>
      <p>${s.reason}</p><div class="when">${fmtT(s.start, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} – ${fmtT(s.end, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} UTC</div>
      ${s.lon != null ? `<div class="s1" data-i="${i}">${s1html(s)}</div>` : ""}</div>`).join("")}
    <p class="scope" style="margin-top:12px">同一種訊號只算一次（取最高分），總分上限 100。</p>`;
  if (v.path?.length) {
    const fmt = (t) => fmtT(t, { month: "numeric", day: "numeric" });
    const what = (p) => p.type === "port_visit" ? `靠港 ${p.port || ""}` : p.type === "encounter" ? "與他船會合"
      : `停留／徘徊（平均 ${p.knots} 節）${v.signals.some((sg) => sg.start === p.start && sg.cable) ? "・<b>靠近海纜</b>" : ""}`;
    const sec = document.createElement("div");
    sec.className = "sig";
    sec.innerHTML = `<div class="k">行程<em>GFW 事件依時間排列。地圖上的框是每段停留實際活動的範圍；虛線只把事件位置連起來，不是實際航線</em></div>`
      + v.path.map((p) => `<p class="leg"><span class="when">${fmt(p.start)}–${fmt(p.end)}</span>　${what(p)}</p>`).join("");
    d.querySelector(".dhead").after(sec);
  }
  d.querySelector(".back").onclick = back;
  d.querySelectorAll(".s1 button").forEach((b) => (b.onclick = () => loadS1(v, +b.parentElement.dataset.i)));
  $("#panel-list").hidden = true; d.hidden = false;
}

function s1html(s) {
  if (!s.s1) return `<button>查這段時間的 Sentinel-1 雷達過境</button>`;
  if (!s.s1.length) return `這段時間 Sentinel-1 沒有拍到這裡，雷達無法補上這段空白。`;
  return `Sentinel-1 在這段時間拍到這裡 ${s.s1.length} 次：` + s.s1.map((p) => `<br><a href="${p.browser}" target="_blank" rel="noopener">${p.time} · ${p.satellite}</a>`).join("");
}

async function loadS1(v, i) {
  const s = v.signals[i], el = document.querySelector(`.s1[data-i="${i}"]`);
  el.textContent = "查詢 Copernicus 目錄中…";
  try { s.s1 = await (await fetch(`/api/s1?lon=${s.lon}&lat=${s.lat}&start=${s.start - 1800}&end=${s.end + 1800}`)).json(); el.innerHTML = s1html(s); }
  catch { el.textContent = "Copernicus 目錄暫時沒有回應。"; }
}

function back() { S.sel = null; $("#panel-detail").hidden = true; $("#panel-list").hidden = false; }

// ---------- modes ----------
async function setMode(m) {
  S.mode = m; S.sel = null; back();
  const reg = REGION[m];
  LAYERS[reg] ||= await Promise.all(["cables", "land"].map((l) => fetch(`/api/layers/${l}?region=${reg}`).then((r) => r.json())));
  [S.cables, S.land] = LAYERS[reg];
  COS = Math.cos((VIEWS[m].lat * Math.PI) / 180);
  S.selInc = null;
  document.querySelector("aside h3").innerHTML = `需要人看的船 <small>分數 ≥ 40，依分數排序</small>`;
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  S.view = { ...VIEWS[m] }; S.base = null;
  $("#timeline").classList.toggle("live", m !== "replay");
  $("#ticker").innerHTML = "";
  if (m === "replay") {
    if (!S.data) {
      $("#scope-title").textContent = "載入中…";
      [S.data, S.traffic] = await Promise.all([fetch(`/api/replay/${CASE}`).then((r) => r.json()), fetch(`/api/replay/${CASE}/traffic`).then((r) => r.json()).then(prepTraffic)]);
      S.t = S.traffic.t0; marks();
    }
    S.byMmsi = new Map(S.data.vessels.map((v) => [v.mmsi, v]));
    S.top = S.data.vessels.filter((v) => v.score >= 40);
    $("#scope-title").textContent = "2024 年 11 月 17–19 日，西波羅的海";
    $("#scope-text").textContent = "BCS East-West 和 C-Lion1 在這三天內斷線。用丹麥海事局公開的 AIS，重跑每一艘船的軌跡，找出在海纜上方停下、徘徊、減速或關掉 AIS 的船。";
    funnel(S.data.summary); renderList(); resetTicker();
    $("#play").textContent = S.playing ? "❚❚" : "▶";
  } else if (m === "taiwan") {
    S.playing = false; $("#play").textContent = "▶";
    await renderTaiwan();
  } else {
    S.playing = false; $("#play").textContent = "▶";
    await refreshLive();
  }
}

// ---------- Taiwan Strait ----------
async function renderTaiwan() {
  S.tw ||= await (await fetch("/api/taiwan")).json();
  S.top = [];
  $("#scope-title").textContent = "臺灣海峽：三起拖斷海纜事件";
  $("#scope-text").innerHTML = `臺灣每年平均 7–8 起海纜事故；2025 年 7 起中有 3 起是船錨拖斷（42.9%）。<a href="https://moda.gov.tw/en/press/press-releases/19392" target="_blank" rel="noopener">數位發展部</a>`;
  const g = S.tw.gfw;
  S.top = g ? g.vessels.filter((v) => v.score >= 40) : [];
  if (g) {
    const sm = g.summary;
    $("#scope-text").innerHTML += `<br>船舶資料：Global Fishing Watch，${sm.start} 到 ${sm.end}。`;
    funnel(sm, "taiwan");
    $("#funnel").innerHTML += `<div class="sanc">Sentinel-1 在 ${sm.sar_days} 天拍到 <b>${sm.sar_dark.toLocaleString()}</b> 艘次沒開 AIS 的船，其中 <b>${sm.sar_near}</b> 艘次在海纜 2 公里內（地圖上的藍點）</div>`;
  } else $("#funnel").innerHTML = `<div class="note">臺灣的船舶資料要等 Global Fishing Watch 的 API 金鑰。</div>`;
  document.querySelector("aside h3").innerHTML = `事件 <small>點一下看時間線與來源</small>`;
  const list = $("#list"); list.innerHTML = "";
  for (const inc of S.tw.incidents) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="score high" style="font-size:11px">${inc.date.slice(2, 7).replace("-", "/")}</div><div class="name">${inc.title}</div>
      <div class="meta">${inc.cable}</div><div class="why">${inc.signals.join("、")}</div>`;
    li.onclick = () => selectIncident(inc);
    list.append(li);
  }
  if (S.top.length) {
    const h = document.createElement("h3");
    h.innerHTML = `現在需要人看的船 <small>分數 ≥ 40，依分數排序</small>`;
    list.append(h);
    S.top.forEach((v) => list.append(row(v)));
  }
}

function selectIncident(inc) {
  S.selInc = inc;
  S.view = { lon: inc.lon, lat: inc.lat, k: 900 }; S.base = null;
  const d = $("#panel-detail");
  d.innerHTML = `<button class="back">← 回到清單</button>
    <h2>${inc.title}</h2><div class="scope">${inc.date} · ${inc.cable}</div>
    <div class="sig"><div class="k">船</div><p>${inc.ship}</p></div>
    <div class="sig"><div class="k">時間線<em>臺灣時間</em></div>${inc.timeline.map(([t, x]) => `<p><span class="when">${t}</span>　${x}</p>`).join("")}</div>
    <div class="sig"><div class="k">規則會抓到的訊號</div><p>${inc.signals.join("、")}</p></div>
    <div class="sig"><div class="k">Sentinel-1 雷達</div><p>${inc.s1}</p></div>
    <div class="sig"><div class="k">結果</div><p>${inc.outcome}</p></div>
    <div class="note">${inc.approx ? "地圖上的點是依新聞稿描述推算的約略位置。" : ""}來源：${inc.sources.map(([n, u]) => `<a href="${u}" target="_blank" rel="noopener">${n}</a>`).join("、")}</div>`;
  d.querySelector(".back").onclick = () => { S.selInc = null; S.view = { ...VIEWS.taiwan }; S.base = null; back(); };
  $("#panel-list").hidden = true; d.hidden = false;
}

function drawBox(b, stroke, fill) {
  const [x0, y0] = px(b[0], b[3]), [x1, y1] = px(b[2], b[1]);
  const w = Math.max(3, x1 - x0), h = Math.max(3, y1 - y0);
  ctx.fillStyle = fill; ctx.fillRect(x0, y0, w, h);
  ctx.strokeStyle = stroke; ctx.lineWidth = 1.2; ctx.strokeRect(x0, y0, w, h);
}

function drawItinerary(v) {
  const pts = v.path.map((p) => px(p.lon, p.lat));
  ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(246,221,143,.75)"; ctx.lineWidth = 1.4;
  ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke(); ctx.restore();
  const near = new Set(v.signals.filter((sg) => sg.box).map((sg) => sg.start));
  v.path.forEach((p, i) => {
    const [x, y] = pts[i];
    if (p.type === "loitering" && p.box) {
      const hot = near.has(p.start);
      drawBox(p.box, hot ? "#ff7a3d" : "rgba(246,221,143,.6)", hot ? "rgba(255,122,61,.25)" : "rgba(246,221,143,.08)");
      if (hot) label(x, y, fmtT(p.start, { month: "numeric", day: "numeric" }) + " 停在海纜上", "#ffb38a");
    } else if (p.type === "port_visit") {
      ctx.fillStyle = "#f6dd8f"; ctx.fillRect(x - 4, y - 4, 8, 8);
      label(x, y, (p.port || "港") + " " + fmtT(p.start, { month: "numeric", day: "numeric" }), "#f6dd8f");
    } else { ctx.fillStyle = "#e5484d"; ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill(); }
  });
}

function drawTaiwan(pulse) {
  tctx.clearRect(0, 0, W, H);
  const g = S.tw?.gfw;
  if (g) {
    for (const [lon, lat, , , near] of g.sar) {
      const [x, y] = px(lon, lat);
      ctx.fillStyle = near ? "rgba(110,200,255,.95)" : "rgba(110,170,220,.28)";
      ctx.beginPath(); ctx.arc(x, y, near ? 2.2 : 1.4, 0, 7); ctx.fill();
    }
    // every flagged ship's activity boxes near cables, faint; the selected ship's in full
    for (const v of S.top) for (const sg of v.signals) if (sg.box && v !== S.sel) drawBox(sg.box, "rgba(217,98,43,.55)", "rgba(217,98,43,.08)");
    if (S.sel?.path) drawItinerary(S.sel);
    for (const v of S.top) {
      const [x, y] = px(v.last[0], v.last[1]);
      const hot = v.score >= 60 || v === S.sel;
      dot(x, y, "#d9622b", hot ? 3.6 : 2.8, hot ? pulse : 0);
      if (hot) label(x, y, v.name || v.mmsi, "#f0a27c");
    }
  }
  for (const inc of S.tw?.incidents || []) {
    const [x, y] = px(inc.lon, inc.lat);
    dot(x, y, "#e5484d", inc === S.selInc ? 5 : 4, pulse);
    label(x, y, inc.title, "#ff8a8d");
  }
}

async function refreshLive() {
  if (S.mode !== "live") return;
  S.live = await (await fetch("/api/live")).json();
  S.top = S.live.vessels.filter((v) => v.score >= 40);
  const sm = S.live.summary, mins = Math.max(0, Math.round((sm.end - sm.start) / 60));
  $("#scope-title").textContent = "芬蘭灣與波羅的海北部，現在";
  $("#scope-text").textContent = `Fintraffic Digitraffic 開放 AIS，每分鐘更新。已累積 ${mins} 分鐘、${sm.fixes.toLocaleString()} 筆位置；規則和重播完全相同。${sm.error ? "（上次更新失敗：" + sm.error + "）" : ""}`;
  funnel(sm, true);
  if (!S.sel) renderList();
}
setInterval(refreshLive, 60_000);

function marks() {
  const tr = S.traffic, span = tr.t1 - tr.t0, pct = (t) => ((t - tr.t0) / span) * 100;
  let html = EVENTS.map((e) => `<div class="mark ${e.low ? "low" : ""} ${e.cut ? "cut" : ""}" style="left:${pct(e.t)}%"><span>${e.label}</span></div>`).join("");
  for (let d = Math.ceil(tr.t0 / 86400) * 86400; d < tr.t1; d += 86400) html += `<div class="day" style="left:${pct(d)}%">${fmtT(d, { month: "numeric", day: "numeric" })}</div>`;
  $("#tl-marks").innerHTML = html;
}

// ---------- input ----------
$("#play").onclick = () => {
  if (S.mode !== "replay") return;
  if (S.t >= S.traffic.t1) { S.t = S.traffic.t0; resetTicker(); }
  S.playing = !S.playing; $("#play").textContent = S.playing ? "❚❚" : "▶";
};
$("#speed").onclick = () => { S.speed = S.speed >= 4 ? 1 : S.speed * 2; $("#speed").textContent = S.speed + "×"; };
$("#tl-track").onpointerdown = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  const go = (ev) => { S.t = S.traffic.t0 + Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * (S.traffic.t1 - S.traffic.t0); resetTicker(); };
  go(e); window.onpointermove = go; window.onpointerup = () => (window.onpointermove = null);
};
document.querySelectorAll("nav button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));

let drag = null;
cv.onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, v: { ...S.view }, moved: false }; cv.classList.add("drag"); };
window.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  S.view = { ...drag.v, lon: drag.v.lon - dx / (COS * drag.v.k), lat: drag.v.lat + dy / drag.v.k }; S.base = null;
});
window.addEventListener("pointerup", (e) => {
  if (drag && !drag.moved) pick(e);
  drag = null; cv.classList.remove("drag");
});
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  const [lon, lat] = unpx(mx, my), f = Math.exp(-e.deltaY * 0.0015);
  S.view.k = Math.max(60, Math.min(4000, S.view.k * f));
  S.view.lon = lon - (mx - W / 2) / (COS * S.view.k); S.view.lat = lat + (my - H / 2) / S.view.k; S.base = null;
}, { passive: false });

function pick(e) {
  const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
  let best = null, bd = 14;
  const cand = S.mode === "replay" ? [...S.top, S.byMmsi.get(CASE_MMSI)].filter(Boolean) : S.top;
  for (const v of cand) {
    const p = S.mode === "replay" ? vesselPos(v, S.t) : v.last; if (!p) continue;
    const [x, y] = px(p[0], p[1]), d = Math.hypot(x - mx, y - my);
    if (d < bd) { bd = d; best = v; }
  }
  if (best) select(best);
}

window.addEventListener("resize", resize);
(async () => {
  resize();
  const qm = new URLSearchParams(location.search).get("mode");
  await setMode(VIEWS[qm] && qm !== "replay" ? qm : "replay");
  const t = new URLSearchParams(location.search).get("t");
  if (t && S.traffic) { S.t = +t; resetTicker(); }
  if (new URLSearchParams(location.search).has("play")) $("#play").click();
  requestAnimationFrame(frame);
})();
