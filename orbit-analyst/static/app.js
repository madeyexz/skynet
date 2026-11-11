const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
const TASK = { flood: "Flood", vessels: "Vessels", construction: "Built change" };
const DEMO_TAG = { "bang-ban-flood": "Flood claim · Ayutthaya", "skagen-dark-vessels": "Dark vessels · Skagen", "chancay-port": "Port build-out · Chancay" };

let map, timerId, pollId;

async function loadDemos() {
  const demos = await (await fetch("/api/demos")).json();
  $("#demos").innerHTML = demos.map((d) => `
    <button class="demo" data-id="${d.id}">
      <div class="tag">${esc(DEMO_TAG[d.id] || d.place)}</div>
      <div class="dq">${esc(d.question)}</div>
      <div class="dh"><span class="sensor">${d.sensor === "optical" ? "Colour" : "Radar"}</span>${esc(d.headline || "")}</div>
    </button>`).join("");
  document.querySelectorAll(".demo").forEach((b) => b.addEventListener("click", () => {
    const d = demos.find((x) => x.id === b.dataset.id);
    $("#q").value = d.question;
    ask({ demo: d.id, question: d.question });
  }));
}

function showSteps(steps) {
  $("#steps").innerHTML = steps.map((s) => `<li class="${s.kind}"><span class="t">${s.t.toFixed(1)}s</span><span>${md(s.text)}</span></li>`).join("");
}

async function ask(body) {
  clearInterval(timerId); clearInterval(pollId);
  $("#go").disabled = true;
  $("#answer").classList.add("hidden");
  const work = $("#work");
  work.classList.remove("hidden", "done", "error");
  $("#work-title").textContent = body.demo ? "Opening the cached answer" : "The agent is working";
  showSteps([]);
  const t0 = Date.now();
  timerId = setInterval(() => { $("#timer").textContent = ((Date.now() - t0) / 1000).toFixed(0) + " s"; }, 250);
  work.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const { job } = await r.json();
    await new Promise((resolve) => {
      const tick = async () => {
        const j = await (await fetch(`/api/jobs/${job}`)).json();
        showSteps(j.steps || []);
        if (j.status === "running") return;
        clearInterval(pollId); clearInterval(timerId);
        if (j.status === "done") {
          work.classList.add("done");
          $("#work-title").textContent = j.result.cached ? "Cached answer · computed earlier from the same images" : `Answered in ${j.result.seconds.toFixed(0)} s`;
          render(j.result);
        } else {
          work.classList.add("error");
          $("#work-title").textContent = "Could not answer";
          $("#answer").classList.remove("hidden");
          $("#answer").innerHTML = `<div class="err">${esc(j.error)}</div>`;
        }
        resolve();
      };
      pollId = setInterval(tick, 700); tick();
    });
  } catch (e) {
    clearInterval(timerId);
    work.classList.add("error");
    $("#work-title").textContent = e.message;
  } finally {
    $("#go").disabled = false;
  }
}

function fmtDate(iso) { return (iso || "").slice(0, 16).replace("T", " "); }
function money(n) { return "$" + n.toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 }); }

function render(r) {
  const el = $("#answer");
  el.classList.remove("hidden");
  const sensor = r.sensor === "sar" ? "Radar · Sentinel-1" : "Colour · Sentinel-2";
  const dark = (r.markers || []).filter((m) => m.status !== "ais");
  const tl = r.timeline || [];
  const max = Math.max(1, ...tl.map((t) => t.km2));
  const mainDate = (r.images.after_label.match(/\d{4}-\d{2}-\d{2}/) || [])[0];
  el.innerHTML = `
    <div class="a-meta"><span class="pill ${r.sensor}">${sensor}</span><span class="pill">${TASK[r.task]}</span><span class="pill">${esc(r.place.split(",").slice(0, 2).join(","))}</span>${r.cached ? '<span class="pill">Cached demo</span>' : '<span class="pill">Live</span>'}</div>
    <p class="a-q">“${esc(r.question)}”</p>
    <h2 class="a-head">${esc(r.headline)}</h2>
    <p class="a-text">${md(r.answer)}</p>
    <div class="stats">${r.stats.map((s) => `<div class="stat"><div class="v">${esc(s.value)}</div><div class="l">${esc(s.label)}</div></div>`).join("")}</div>
    <div class="grid2">
      <div class="panel">
        <div class="panel-h"><span>Before / after · drag to compare</span><label class="toggle"><input type="checkbox" id="ovt" checked> Overlay</label></div>
        <div class="compare" id="cmp">
          <img class="after" src="${r.images.after}" alt="After">
          <img class="ov" src="${r.images.overlay}" alt="">
          <div class="ov marks">${(r.markers || []).map((m) => `<span class="mk ${m.status === "ais" ? "ok" : "flag"}" style="left:${(m.fx * 100).toFixed(2)}%;top:${(m.fy * 100).toFixed(2)}%"></span>`).join("")}</div>
          <img class="before" src="${r.images.before}" alt="Before">
          <span class="lab l">${esc(r.images.before_label)}</span><span class="lab r">${esc(r.images.after_label)}</span>
          <div class="handle"></div>
        </div>
        <div class="legend">${r.legend.map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}</span>`).join("")}<span class="muted">Overlay shows on the after side.</span></div>
      </div>
      <div class="side">
        <div class="panel"><div class="panel-h"><span>Where</span><span class="muted">${r.lat.toFixed(3)}, ${r.lon.toFixed(3)}</span></div><div id="minimap"></div></div>
        ${tl.length > 1 ? `<div class="panel"><div class="panel-h"><span>New water by radar pass</span><span class="muted">km²</span></div><div class="panel-b"><div class="timeline">${tl.map((t) => `
          <div class="bar ${t.date === mainDate ? "main" : ""}" title="${t.orbit || ""} pass"><span class="n">${t.km2}</span><div class="b" style="height:${(t.km2 / max) * 80}%"></div><span>${t.date.slice(5)}</span></div>`).join("")}</div></div></div>` : ""}
        ${r.task === "vessels" && r.cached ? `<a class="btn-dark tk-open" href="/track">Track these ships over the day →</a>` : ""}
        ${r.task === "vessels" ? `<div class="panel"><div class="panel-h"><span>Targets without AIS</span><span class="muted">${dark.length}</span></div><div class="panel-b" style="padding:0;max-height:260px;overflow:auto">
          <table class="ships"><tr><th>#</th><th>Lat, lon</th><th>Size</th><th>Peak</th></tr>${dark.map((m, i) => `
          <tr><td class="flag">${i + 1}</td><td>${m.lat.toFixed(4)}, ${m.lon.toFixed(4)}</td><td>${m.size}</td><td>${m.peak_db} dB</td></tr>`).join("")}</table></div></div>` : ""}
      </div>
    </div>

    <div class="sharpen">
      <div class="sharpen-h">
        <div><h3>Get a sharper image</h3><p>Sentinel pixels are 10–20 m. A commercial image shows single ${r.task === "vessels" ? "boats" : r.task === "flood" ? "fields, roads and houses" : "buildings and cranes"}. Prices are SkyFi list prices for this ${r.quote.area_km2} km² box.</p></div>
        <button class="btn-dark" id="qbtn">Show the quote</button>
      </div>
      <div class="quote hidden" id="quote">
        <div class="qopts">${r.quote.options.map((o) => `
          <div class="qopt ${o.kind === r.quote.recommended && o.label.includes(r.quote.recommended === "sar" ? "archive" : "50 cm") ? "rec" : ""}">
            <div class="lbl">${esc(o.label)}</div><div class="p">${money(o.price)}</div><div class="u">${esc(o.unit)}</div><div class="w">${esc(o.why)}</div>
          </div>`).join("")}</div>
        <div class="qnote"><span><b>${esc(r.quote.note)}</b> Final price depends on provider, date and area at checkout.</span><span>${esc(r.quote.source)}</span></div>
      </div>
    </div>

    <div class="cols">
      <div><h4>What it cannot see</h4><ul class="limits">${r.limits.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>
      <div><h4>Sources</h4><ul class="sources">${r.sources.map((s) => `
        <li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.id)}</a><span class="k">${s.datetime ? fmtDate(s.datetime) : ""}${s.cloud_pct != null ? ` · ${s.cloud_pct}% cloud` : ""}</span></li>`).join("")}</ul></div>
    </div>

    <details class="trail"><summary>Agent steps (${r.steps.length})</summary><ol class="steps">${r.steps.map((s) => `<li class="${s.kind}"><span class="t">${s.t.toFixed(1)}s</span><span>${md(s.text)}</span></li>`).join("")}</ol></details>
  `;
  $("#work").classList.add("hidden");
  setupCompare(r);
  setupMap(r);
  $("#qbtn").addEventListener("click", () => {
    const q = $("#quote"); q.classList.toggle("hidden");
    $("#qbtn").textContent = q.classList.contains("hidden") ? "Show the quote" : "Hide the quote";
  });
  $("#ovt").addEventListener("change", (e) => { document.querySelectorAll("#cmp .ov").forEach((o) => { o.style.opacity = e.target.checked ? 1 : 0; }); });
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setupCompare(r) {
  const cmp = $("#cmp");
  const img = new Image();
  img.onload = () => { cmp.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`; };
  img.src = r.images.after;
  cmp.style.aspectRatio = "1 / 1";
  cmp.style.setProperty("--x", "35%");
  const set = (clientX) => {
    const b = cmp.getBoundingClientRect();
    const x = Math.min(Math.max((clientX - b.left) / b.width, 0), 1);
    cmp.style.setProperty("--x", (x * 100).toFixed(2) + "%");
  };
  let down = false;
  cmp.addEventListener("pointerdown", (e) => { down = true; cmp.setPointerCapture(e.pointerId); set(e.clientX); });
  cmp.addEventListener("pointermove", (e) => { if (down) set(e.clientX); });
  cmp.addEventListener("pointerup", () => { down = false; });
}

function setupMap(r) {
  if (map) { map.remove(); map = null; }
  map = L.map("minimap", { zoomControl: true, attributionControl: true, scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors", maxZoom: 19, className: "osm-tiles",
  }).addTo(map);
  const [w, s, e, n] = r.bbox;
  const box = L.rectangle([[s, w], [n, e]], { color: "#111", weight: 1.5, fill: false, dashArray: "4 3" }).addTo(map);
  (r.markers || []).forEach((m) => {
    const color = m.status === "ais" ? "#1a9c5b" : "#e5432d";
    const tip = m.ais ? `${esc(m.ais.name || "MMSI " + m.ais.mmsi)} · ${esc(m.ais.type)} · ${esc(m.ais.length || "?")} m` : `No AIS · ${m.size} target · peak ${m.peak_db} dB`;
    L.circleMarker([m.lat, m.lon], { radius: m.status === "ais" ? 3.5 : 5, color, weight: 1.5, fillColor: color, fillOpacity: 0.6 }).bindTooltip(tip).addTo(map);
  });
  map.fitBounds(box.getBounds(), { padding: [12, 12] });
  if (!r.markers?.length) L.circleMarker([r.lat, r.lon], { radius: 4, color: "#111", fillColor: "#111", fillOpacity: 1 }).addTo(map);
  setTimeout(() => map.invalidateSize(), 50);
}

$("#ask").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (q) ask({ question: q });
});
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask").requestSubmit(); }
});
loadDemos();
