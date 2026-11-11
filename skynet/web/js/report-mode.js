// Skynet · Report mode (owner: E)
// Rewind replay of one vessel record: picker, map layers (rw-*), time scrubber,
// event timeline, and the report launcher with an in-app A4 preview.

const SRC = {
  ais: { label: 'AIS', icon: 'radio-tower', color: '#1F6FEB' },
  sar: { label: 'Satellite radar', icon: 'satellite', color: '#292929' },
  rf: { label: 'RF', icon: 'audio-waveform', color: '#1A7F37' },
  operator: { label: 'Operator action', icon: 'user-round-check', color: '#5D5D5D' },
  fusion: { label: 'Fusion', icon: 'combine', color: '#D1242F' },
  registry: { label: 'Registry', icon: 'file-text', color: '#5D5D5D' },
  sanctions: { label: 'Sanctions', icon: 'shield-alert', color: '#D1242F' },
};
const srcOf = (s) => SRC[s] || { label: s ? s[0].toUpperCase() + s.slice(1) : 'Source', icon: 'circle-dot', color: '#5D5D5D' };

const TEMPLATES = [
  { id: 'cable', title: 'Cable incident record', desc: 'What happened over the asset, minute by minute, for the owner and investigators.' },
  { id: 'sanctions', title: 'Vessel sanctions due-diligence', desc: 'Dark periods, meetings and identity changes for compliance checks.' },
  { id: 'insurance', title: 'Insurance voyage history / claims reconstruction', desc: 'Where the vessel really was, for underwriting and claims.' },
];

const COLORS = { claimed: '#6E85B7', verified: '#292929', dark: '#E8590C', blue: '#1F6FEB', red: '#D1242F' };
const SPEEDS = [1, 10, 60];
const LAYERS = ['rw-claimed', 'rw-verified-full', 'rw-verified', 'rw-dark', 'rw-link', 'rw-rf', 'rw-sar', 'rw-head-claimed', 'rw-head'];

const pad2 = (n) => String(n).padStart(2, '0');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const hm = (t) => { const d = new Date(t * 1000); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const hms = (t) => { const d = new Date(t * 1000); return `${hm(t)}:${pad2(d.getUTCSeconds())}`; };
const dmy = (t) => { const d = new Date(t * 1000); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const toInput = (t) => new Date(t * 1000).toISOString().slice(0, 16);
const fromInput = (s) => { const v = Date.parse(s + ':00Z'); return Number.isFinite(v) ? Math.round(v / 1000) : null; };
const humanType = (s) => (s || 'event').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fc = (features) => ({ type: 'FeatureCollection', features });
const line = (coords, props = {}) => ({ type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: coords } });
const point = (c, props = {}) => ({ type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: c } });

export function init(ctx) {
  const root = document.getElementById('report');
  if (!root) return;
  const map = ctx.map;
  const S = {
    caseId: null, vesselId: null, vessel: null, record: null, rows: [], range: [0, 1],
    visible: false, speed: 10, dragging: false, lastT: null, activeRow: -1, report: null, query: '',
  };

  const caseId = () => (typeof ctx.case === 'string' && ctx.case) || ctx.api?.case || S.caseId
    || new URLSearchParams(location.search).get('case') || 'cable';
  const world = () => ctx.world || { vessels: [], events: [], actions: [], assets: [] };
  const clockT = () => (ctx.clock && Number.isFinite(ctx.clock.t) ? ctx.clock.t : S.range[0]);
  const on = (name, fn) => {
    if (!ctx.bus) return;
    const h = (x) => fn(x instanceof Event && 'detail' in x ? x.detail || {} : x || {});
    if (typeof ctx.bus.on === 'function') ctx.bus.on(name, h); else ctx.bus.addEventListener?.(name, h);
  };
  const emit = (name, detail) => { try { ctx.bus?.emit ? ctx.bus.emit(name, detail) : ctx.bus?.dispatchEvent?.(new CustomEvent(name, { detail })); } catch (e) { /* ignore */ } };

  // ---------- DOM ----------
  root.classList.add('rw-root');
  root.innerHTML = `
    <section class="rw-card rw-vessel" aria-label="Replay vessel">
      <div class="rw-picker">
        <button type="button" class="rw-pick-btn" aria-haspopup="listbox" aria-expanded="false">
          <span class="rw-pick-main"><span class="rw-pick-id"></span><span class="rw-pick-name"></span></span>
          <span class="rw-pill rw-risk"></span>
          <i data-lucide="chevrons-up-down"></i>
        </button>
        <div class="rw-pick-pop" hidden>
          <label class="rw-search"><i data-lucide="search"></i><span class="rw-sr">Search vessels</span>
            <input type="search" placeholder="Search name, id, MMSI or IMO" autocomplete="off"></label>
          <ul class="rw-pick-list" role="listbox" aria-label="Vessels"></ul>
        </div>
      </div>
      <p class="rw-vessel-meta"></p>
      <p class="rw-vessel-dark"></p>
      <ul class="rw-legend" aria-label="Map legend">
        <li><span class="rw-key rw-key-claimed"></span>Claimed (AIS)</li>
        <li><span class="rw-key rw-key-verified"></span>Verified</li>
        <li><span class="rw-key rw-key-dark"></span>Dark</li>
        <li><span class="rw-key rw-key-sar"></span>Radar</li>
        <li><span class="rw-key rw-key-rf"></span>RF</li>
      </ul>
      <button type="button" class="rw-cta rw-gen-open"><i data-lucide="file-text"></i>Generate report</button>
    </section>

    <aside class="rw-card rw-sheet" aria-label="Event timeline">
      <header class="rw-sheet-head">
        <h2>Event timeline</h2><span class="rw-count"></span>
      </header>
      <ol class="rw-rows"></ol>
      <p class="rw-fp" title="Record fingerprint"></p>
    </aside>

    <div class="rw-scrub" aria-label="Time scrubber">
      <button type="button" class="rw-play" aria-label="Play">
        <span class="rw-ico-play"><i data-lucide="play"></i></span><span class="rw-ico-pause" hidden><i data-lucide="pause"></i></span>
      </button>
      <div class="rw-speed" role="radiogroup" aria-label="Playback speed">
        ${SPEEDS.map((s) => `<button type="button" role="radio" data-speed="${s}" aria-checked="false">${s}x</button>`).join('')}
      </div>
      <div class="rw-now"><span class="rw-now-time">--:--:--</span><span class="rw-now-date">UTC</span></div>
      <div class="rw-trackwrap">
        <div class="rw-track" role="slider" tabindex="0" aria-label="Replay time" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0">
          <div class="rw-rail"></div>
          <div class="rw-bands"></div>
          <div class="rw-fill"></div>
          <div class="rw-ticks"></div>
          <div class="rw-handle"></div>
          <div class="rw-hover" hidden></div>
        </div>
        <div class="rw-axis"></div>
      </div>
    </div>

    <div class="rw-modal" hidden>
      <form class="rw-card rw-gen" aria-labelledby="rw-gen-title">
        <header class="rw-gen-head">
          <h2 id="rw-gen-title">Generate report</h2>
          <button type="button" class="rw-icon-btn rw-gen-close" aria-label="Close"><i data-lucide="x"></i></button>
        </header>
        <p class="rw-gen-sub"></p>
        <fieldset class="rw-tpls"><legend>Template</legend>
          ${TEMPLATES.map((t) => `<label class="rw-tpl"><input type="radio" name="template" value="${t.id}">
            <span class="rw-tpl-text"><span class="rw-tpl-title">${t.title}</span><span class="rw-tpl-desc">${t.desc}</span></span></label>`).join('')}
        </fieldset>
        <fieldset class="rw-period"><legend>Period covered (UTC)</legend>
          <div class="rw-period-row">
            <label>From<input type="datetime-local" name="from" required></label>
            <label>To<input type="datetime-local" name="to" required></label>
          </div>
          <div class="rw-chips">
            <button type="button" class="rw-chip" data-period="dark">Dark period ± 1 h</button>
            <button type="button" class="rw-chip" data-period="full">Full record</button>
          </div>
        </fieldset>
        <label class="rw-field">Requested by<input type="text" name="requested_by" value="Coast guard duty officer" required></label>
        <p class="rw-gen-err" role="alert" hidden></p>
        <footer class="rw-gen-foot">
          <button type="button" class="rw-btn rw-gen-cancel">Cancel</button>
          <button type="submit" class="rw-cta rw-gen-go">Generate report</button>
        </footer>
      </form>
    </div>

    <section class="rw-preview" aria-label="Report preview" hidden>
      <header class="rw-preview-head">
        <div class="rw-preview-title"><span class="rw-preview-id"></span><span class="rw-preview-name"></span></div>
        <a class="rw-cta rw-pdf" href="#" download><i data-lucide="download"></i>Export PDF</a>
        <a class="rw-btn rw-full" href="#" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Open full page</a>
        <button type="button" class="rw-icon-btn rw-preview-close" aria-label="Close preview"><i data-lucide="x"></i></button>
      </header>
      <iframe class="rw-frame" title="A4 evidence record preview"></iframe>
    </section>`;
  const $ = (s) => root.querySelector(s);
  const icons = () => { try { window.lucide?.createIcons?.(); } catch (e) { /* ignore */ } };
  icons();

  // ---------- data ----------
  function vesselRange(v, rec) {
    const ts = [];
    for (const p of v?.track || []) ts.push(p[0]);
    for (const p of v?.claimed || []) ts.push(p[0]);
    for (const o of v?.observed || []) ts.push(o.t);
    for (const o of v?.emitted || []) ts.push(o.t);
    for (const p of rec?.verified_track || []) ts.push(p[0]);
    if (!ts.length) for (const e of rec?.events || []) ts.push(e.t);
    const sc = world().scenario || {};
    if (!ts.length) ts.push(sc.now || sc.start || Math.floor(Date.now() / 1000));
    let a = Math.min(...ts), b = Math.max(...ts);
    if (b - a < 60) { a -= 1800; b += 1800; }
    return [a, b];
  }
  const darkPeriods = () => S.record?.dark_periods?.length ? S.record.dark_periods : (S.vessel?.dark_periods || []);
  const longestDark = () => darkPeriods().slice().sort((x, y) => (y.end - y.start) - (x.end - x.start))[0] || null;

  function buildRows() {
    const v = S.vessel;
    let events = S.record?.events;
    if (!events) {
      events = (world().events || []).filter((e) => e.vessel_id === v.id);
      for (const a of world().actions || []) if (a.vessel_id === v.id) {
        events.push({ id: a.id, t: a.t, source: 'operator', type: 'operator_action', summary: `${a.label || humanType(a.action)}${a.by ? ' · ' + a.by : ''}${a.note ? ' · ' + a.note : ''}` });
      }
    }
    const rows = events.map((e) => ({
      t: e.t, end: e.end, source: e.source || 'fusion', type: e.type, id: e.id,
      text: e.summary || e.text || (e.facts || []).join(' · ') || (e.label ? `${e.label}${e.by ? ' · ' + e.by : ''}` : ''),
      severity: e.severity,
    }));
    const has = (src, t) => rows.some((r) => r.source === src && Math.abs(r.t - t) < 60);
    for (const o of v.observed || []) if (!has('sar', o.t)) rows.push({
      t: o.t, source: 'sar', type: 'radar_detection', id: o.id,
      text: `${o.id || 'Detection'}: ${o.length_m ? o.length_m + ' m hull' : 'hull'}${o.matched_to_ais === false ? ', no matching AIS' : o.matched_to_ais ? ', matched to AIS' : ''}`,
    });
    for (const o of v.emitted || []) if (!has('rf', o.t)) rows.push({
      t: o.t, source: 'rf', type: 'rf_emission', id: o.id, text: `${o.id || 'Emission'}: ${o.band || 'emitter'} detected`,
    });
    for (const d of darkPeriods()) {
      if (!has('ais', d.start)) rows.push({ t: d.start, source: 'ais', type: 'ais_signal_lost', text: 'Last AIS report before the gap.' });
      if (d.end && !has('ais', d.end)) rows.push({ t: d.end, source: 'ais', type: 'ais_resumed', text: `AIS back after ${d.minutes || Math.round((d.end - d.start) / 60)} min.` });
    }
    rows.sort((a, b) => a.t - b.t || (a.source === 'operator') - (b.source === 'operator'));
    S.rows = rows;
  }

  async function loadRecord(id) {
    try {
      const r = await fetch(`/api/vessels/${encodeURIComponent(id)}/record?case=${encodeURIComponent(caseId())}`);
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) { return null; }
  }

  function defaultVesselId() {
    const vs = world().vessels || [];
    if (!vs.length) return null;
    return vs.slice().sort((a, b) => (b.risk?.score || 0) - (a.risk?.score || 0))[0].id;
  }

  async function selectVessel(id, { fit = true, seek = null } = {}) {
    const v = (world().vessels || []).find((x) => x.id === id);
    if (!v) return;
    const changed = S.vesselId !== id;
    S.vesselId = id; S.vessel = v; S.record = changed ? null : S.record;
    S.range = vesselRange(v, null);
    buildRows(); renderAll();
    if (fit) fitToTrack();
    seekInitial(seek, changed);
    const rec = await loadRecord(id);
    if (S.vesselId !== id) return;
    if (rec) { S.record = rec; S.range = vesselRange(v, rec); buildRows(); renderAll(); if (seek != null) setT(seek); }
  }

  function seekInitial(seek, changed) {
    if (seek != null) return setT(seek);
    const t = clockT();
    if (!changed && t >= S.range[0] && t <= S.range[1]) return renderTime(t);
    const d = longestDark();
    setT(d ? Math.max(S.range[0], d.start - 15 * 60) : S.range[0]);
  }

  function setT(t) {
    t = Math.max(S.range[0], Math.min(S.range[1], Math.round(t)));
    try { ctx.clock?.set?.(t); } catch (e) { /* ignore */ }
    renderTime(t);
  }

  // ---------- picker ----------
  const pickBtn = $('.rw-pick-btn'), pickPop = $('.rw-pick-pop'), pickInput = $('.rw-search input'), pickList = $('.rw-pick-list');
  function renderPicker() {
    const v = S.vessel;
    $('.rw-pick-id').textContent = v ? v.id : 'No vessel';
    $('.rw-pick-name').textContent = v ? (v.name || '') : '';
    const risk = $('.rw-risk');
    if (v?.risk) { risk.hidden = false; risk.textContent = `Risk ${v.risk.score}`; risk.dataset.level = v.risk.level || ''; } else risk.hidden = true;
    $('.rw-vessel-meta').textContent = v ? [v.type && humanType(v.type), v.flag, v.length_m && `${v.length_m} m`, v.mmsi && `MMSI ${v.mmsi}`].filter(Boolean).join(' · ') : '';
    const dps = darkPeriods();
    const darkEl = $('.rw-vessel-dark');
    if (dps.length) {
      const mins = dps.reduce((s, d) => s + (d.minutes || Math.round((d.end - d.start) / 60)), 0);
      darkEl.innerHTML = `<span class="rw-dot-dark"></span>${dps.length} dark period${dps.length > 1 ? 's' : ''} · ${mins} min with AIS off`;
    } else darkEl.textContent = 'No dark periods in this record';
  }
  function renderPickList() {
    const q = S.query.trim().toLowerCase();
    const vs = (world().vessels || []).slice().sort((a, b) => (b.risk?.score || 0) - (a.risk?.score || 0))
      .filter((v) => !q || [v.id, v.name, v.mmsi, v.imo, v.flag, v.type].some((f) => String(f ?? '').toLowerCase().includes(q)));
    pickList.innerHTML = vs.slice(0, 60).map((v) => `<li role="option" aria-selected="${v.id === S.vesselId}">
      <button type="button" data-id="${esc(v.id)}"><span class="rw-opt-id">${esc(v.id)}</span><span class="rw-opt-name">${esc(v.name || '')}</span>
      <span class="rw-opt-risk" data-level="${esc(v.risk?.level || '')}">${v.risk ? v.risk.score : ''}</span></button></li>`).join('')
      || '<li class="rw-empty">No vessels match</li>';
  }
  function openPicker(open) {
    pickPop.hidden = !open; pickBtn.setAttribute('aria-expanded', String(open));
    if (open) { S.query = ''; pickInput.value = ''; renderPickList(); pickInput.focus(); }
  }
  pickBtn.addEventListener('click', () => openPicker(pickPop.hidden));
  pickInput.addEventListener('input', () => { S.query = pickInput.value; renderPickList(); });
  pickInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { const b = pickList.querySelector('button'); if (b) b.click(); e.preventDefault(); }
    if (e.key === 'ArrowDown') { pickList.querySelector('button')?.focus(); e.preventDefault(); }
  });
  pickList.addEventListener('keydown', (e) => {
    const bs = [...pickList.querySelectorAll('button')]; const i = bs.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { bs[Math.min(bs.length - 1, i + 1)]?.focus(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { (i <= 0 ? pickInput : bs[i - 1]).focus(); e.preventDefault(); }
  });
  pickList.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]'); if (!b) return;
    openPicker(false); pickBtn.focus(); selectVessel(b.dataset.id);
  });
  document.addEventListener('pointerdown', (e) => { if (!pickPop.hidden && !e.target.closest('.rw-picker')) openPicker(false); });

  // ---------- timeline ----------
  const rowsEl = $('.rw-rows');
  function renderRows() {
    $('.rw-count').textContent = `${S.rows.length} event${S.rows.length === 1 ? '' : 's'}`;
    rowsEl.innerHTML = S.rows.map((r, i) => {
      const s = srcOf(r.source);
      return `<li><button type="button" class="rw-row" data-i="${i}" data-source="${esc(r.source)}">
        <span class="rw-row-time">${hm(r.t)}</span>
        <span class="rw-row-body">
          <span class="rw-row-src"><span class="rw-src-ico" style="color:${s.color}"><i data-lucide="${s.icon}"></i></span>${esc(s.label)}<span class="rw-row-type">${esc(humanType(r.type))}</span></span>
          <span class="rw-row-text">${esc(r.text)}</span>
        </span></button></li>`;
    }).join('') || '<li class="rw-empty">No events in this record</li>';
    const fp = S.record?.fingerprint;
    $('.rw-fp').textContent = fp ? `Record ${fp.slice(0, 19)}…` : '';
    icons();
    S.activeRow = -2;
  }
  rowsEl.addEventListener('click', (e) => {
    const b = e.target.closest('.rw-row'); if (!b) return;
    const r = S.rows[+b.dataset.i]; if (!r) return;
    pause(); setT(r.t);
    const v = S.vessel && ctx.vesselAt ? safeAt(S.vessel, r.t) : null;
    if (v && map) map.easeTo({ center: [v.lon, v.lat], duration: 600 });
  });

  // ---------- scrubber ----------
  const track = $('.rw-track'), fill = $('.rw-fill'), handle = $('.rw-handle'), hover = $('.rw-hover');
  const frac = (t) => (t - S.range[0]) / Math.max(1, S.range[1] - S.range[0]);
  function renderScrubber() {
    const [a, b] = S.range;
    track.setAttribute('aria-valuemin', a); track.setAttribute('aria-valuemax', b);
    $('.rw-bands').innerHTML = darkPeriods().map((d) => {
      const l = Math.max(0, frac(d.start)), r = Math.min(1, frac(d.end ?? b));
      return `<span class="rw-band" style="left:${l * 100}%;width:${Math.max(0.4, (r - l) * 100)}%" title="Dark ${hm(d.start)}–${hm(d.end)} UTC"></span>`;
    }).join('');
    $('.rw-ticks').innerHTML = S.rows.map((r, i) => `<span class="rw-tick" data-source="${esc(r.source)}" data-i="${i}" style="left:${frac(r.t) * 100}%;background:${srcOf(r.source).color}" title="${hm(r.t)} · ${esc(srcOf(r.source).label)} · ${esc(humanType(r.type))}"></span>`).join('');
    // axis labels
    const span = b - a;
    const steps = [300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
    const step = steps.find((s) => span / s <= 9) || 604800;
    const labels = [];
    for (let t = Math.ceil(a / step) * step; t <= b; t += step) labels.push(t);
    $('.rw-axis').innerHTML = `<span class="rw-axis-end rw-axis-start">${hm(a)}</span>`
      + labels.filter((t) => frac(t) > 0.06 && frac(t) < 0.94).map((t) => `<span style="left:${frac(t) * 100}%">${step >= 86400 ? dmy(t).slice(0, -5) : hm(t)}</span>`).join('')
      + `<span class="rw-axis-end rw-axis-stop">${hm(b)}${span > 86400 ? '' : ' UTC'}</span>`;
  }
  const tFromX = (x) => { const r = track.getBoundingClientRect(); return S.range[0] + Math.max(0, Math.min(1, (x - r.left) / r.width)) * (S.range[1] - S.range[0]); };
  track.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    S.dragging = true; track.setPointerCapture(e.pointerId); pause(); setT(tFromX(e.clientX)); track.focus();
  });
  track.addEventListener('pointermove', (e) => {
    const t = tFromX(e.clientX);
    hover.hidden = false; hover.style.left = `${frac(t) * 100}%`; hover.textContent = hm(t);
    if (S.dragging) setT(t);
  });
  track.addEventListener('pointerleave', () => { hover.hidden = true; });
  const endDrag = () => { S.dragging = false; };
  track.addEventListener('pointerup', endDrag); track.addEventListener('pointercancel', endDrag);
  track.addEventListener('keydown', (e) => {
    const big = e.shiftKey ? 600 : 60;
    const k = { ArrowRight: big, ArrowUp: big, ArrowLeft: -big, ArrowDown: -big, PageUp: 1800, PageDown: -1800 }[e.key];
    if (k) { pause(); setT(clockT() + k); e.preventDefault(); }
    if (e.key === 'Home') { pause(); setT(S.range[0]); e.preventDefault(); }
    if (e.key === 'End') { pause(); setT(S.range[1]); e.preventDefault(); }
    if (e.key === ' ') { togglePlay(); e.preventDefault(); }
  });

  const playBtn = $('.rw-play');
  const isPlaying = () => !!ctx.clock?.playing;
  function renderPlay() {
    const p = isPlaying() && S.visible;
    $('.rw-ico-play').hidden = p; $('.rw-ico-pause').hidden = !p;
    playBtn.setAttribute('aria-label', p ? 'Pause' : 'Play');
    root.querySelectorAll('.rw-speed button').forEach((b) => b.setAttribute('aria-checked', String(+b.dataset.speed === S.speed)));
  }
  function pause() { try { ctx.clock?.pause?.(); } catch (e) { /* ignore */ } renderPlay(); }
  function play() {
    if (clockT() >= S.range[1] - 1) setT(S.range[0]);
    try { if (ctx.clock) { ctx.clock.speed = S.speed; ctx.clock.play?.(S.speed); } } catch (e) { /* ignore */ }
    renderPlay();
  }
  function togglePlay() { isPlaying() ? pause() : play(); }
  playBtn.addEventListener('click', togglePlay);
  $('.rw-speed').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-speed]'); if (!b) return;
    S.speed = +b.dataset.speed;
    if (isPlaying()) play(); else renderPlay();
  });

  // ---------- map layers ----------
  function makeDiamond() {
    const s = 22, c = document.createElement('canvas'); c.width = c.height = s;
    const g = c.getContext('2d');
    g.translate(s / 2, s / 2); g.rotate(Math.PI / 4);
    g.fillStyle = '#E8590C'; g.strokeStyle = '#FFFFFF'; g.lineWidth = 2.5;
    g.beginPath(); g.rect(-5.5, -5.5, 11, 11); g.fill(); g.stroke();
    return g.getImageData(0, 0, s, s);
  }
  function ensureLayers() {
    if (!map || !map.getStyle) return false;
    try {
      if (!map.hasImage('rw-diamond')) map.addImage('rw-diamond', makeDiamond(), { pixelRatio: 2 });
      const src = (id) => { if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: fc([]) }); };
      ['rw-claimed', 'rw-verified', 'rw-verified-full', 'rw-dark', 'rw-sar', 'rw-rf', 'rw-head', 'rw-link'].forEach(src);
      const add = (l) => { if (!map.getLayer(l.id)) map.addLayer({ ...l, layout: { ...(l.layout || {}), visibility: S.visible ? 'visible' : 'none' } }); };
      add({ id: 'rw-claimed', type: 'line', source: 'rw-claimed', paint: { 'line-color': COLORS.claimed, 'line-width': 2, 'line-dasharray': [2, 2], 'line-opacity': 0.9 }, layout: { 'line-cap': 'butt' } });
      add({ id: 'rw-verified-full', type: 'line', source: 'rw-verified-full', paint: { 'line-color': COLORS.verified, 'line-width': 2, 'line-opacity': 0.22 }, layout: { 'line-join': 'round' } });
      add({ id: 'rw-verified', type: 'line', source: 'rw-verified', paint: { 'line-color': COLORS.verified, 'line-width': 2.5 }, layout: { 'line-join': 'round', 'line-cap': 'round' } });
      add({ id: 'rw-dark', type: 'line', source: 'rw-dark', paint: { 'line-color': COLORS.dark, 'line-width': 4.5, 'line-opacity': 0.95 }, layout: { 'line-join': 'round', 'line-cap': 'round' } });
      add({ id: 'rw-link', type: 'line', source: 'rw-link', paint: { 'line-color': COLORS.red, 'line-width': 1.5, 'line-dasharray': [2, 2] } });
      add({ id: 'rw-rf', type: 'circle', source: 'rw-rf', paint: { 'circle-radius': 6, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': SRC.rf.color, 'circle-stroke-width': 1.75 } });
      add({ id: 'rw-sar', type: 'symbol', source: 'rw-sar', layout: { 'icon-image': 'rw-diamond', 'icon-allow-overlap': true, 'icon-ignore-placement': true } });
      add({ id: 'rw-head-claimed', type: 'circle', source: 'rw-head', filter: ['==', ['get', 'kind'], 'claimed'], paint: { 'circle-radius': 7, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': COLORS.red, 'circle-stroke-width': 2 } });
      add({ id: 'rw-head', type: 'circle', source: 'rw-head', filter: ['==', ['get', 'kind'], 'head'], paint: {
        'circle-radius': 7, 'circle-stroke-color': '#FFFFFF', 'circle-stroke-width': 2.5,
        'circle-color': ['match', ['get', 'state'], 'dark', COLORS.dark, 'spoofing', COLORS.red, COLORS.blue] } });
      return true;
    } catch (e) { console.warn('[report-mode] layers', e); return false; }
  }
  const setData = (id, data) => { const s = map?.getSource?.(id); if (s) s.setData(data); };
  function setVisible(v) {
    if (!map?.getLayer) return;
    for (const id of LAYERS) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', v ? 'visible' : 'none');
  }
  function verifiedPts() {
    if (S.vessel?.track?.length) return S.vessel.track;
    return S.record?.verified_track || [];
  }
  function claimedPts() {
    if (S.vessel?.claimed?.length) return S.vessel.claimed;
    return S.record?.claimed_track || [];
  }
  function splitByGap(pts, gap = 600) {
    const segs = []; let cur = [];
    for (let i = 0; i < pts.length; i++) {
      if (i && pts[i][0] - pts[i - 1][0] > gap) { if (cur.length > 1) segs.push(cur); cur = []; }
      cur.push([pts[i][1], pts[i][2]]);
    }
    if (cur.length > 1) segs.push(cur);
    return segs;
  }
  function renderLayers() {
    if (!ensureLayers()) return;
    const v = S.vessel; if (!v) return;
    const vp = verifiedPts();
    setData('rw-claimed', fc(splitByGap(claimedPts()).map((c) => line(c))));
    setData('rw-verified-full', fc(vp.length > 1 ? [line(vp.map((p) => [p[1], p[2]]))] : []));
    const dark = darkPeriods().map((d) => vp.filter((p) => p[0] >= d.start && p[0] <= (d.end ?? Infinity)).map((p) => [p[1], p[2]])).filter((c) => c.length > 1);
    setData('rw-dark', fc(dark.map((c) => line(c))));
    setData('rw-sar', fc((v.observed || []).filter((o) => Number.isFinite(o.lon)).map((o) => point([o.lon, o.lat], { id: o.id, t: o.t }))));
    setData('rw-rf', fc((v.emitted || []).filter((o) => Number.isFinite(o.lon)).map((o) => point([o.lon, o.lat], { id: o.id, t: o.t }))));
    S.lastT = null;
  }
  function interp(pts, t) {
    if (!pts.length) return null;
    if (t <= pts[0][0]) return { lon: pts[0][1], lat: pts[0][2] };
    if (t >= pts[pts.length - 1][0]) { const p = pts[pts.length - 1]; return { lon: p[1], lat: p[2] }; }
    let lo = 0, hi = pts.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (pts[m][0] <= t) lo = m; else hi = m; }
    const a = pts[lo], b = pts[hi], f = (t - a[0]) / Math.max(1, b[0] - a[0]);
    return { lon: a[1] + (b[1] - a[1]) * f, lat: a[2] + (b[2] - a[2]) * f };
  }
  function stateAt(t) {
    const st = (S.vessel?.states || []).find((s) => t >= s.start && t <= s.end);
    if (st) return st.state;
    return darkPeriods().some((d) => t >= d.start && t <= (d.end ?? Infinity)) ? 'dark' : 'verified';
  }
  function safeAt(v, t) {
    let r = null;
    try { r = ctx.vesselAt ? ctx.vesselAt(v, t) : null; } catch (e) { r = null; }
    if (!r || !Number.isFinite(r.lon)) {
      const p = interp(verifiedPts(), t) || (v.observed?.[0] && { lon: v.observed[0].lon, lat: v.observed[0].lat });
      if (!p) return null;
      r = { ...p, state: stateAt(t), claimed: null };
    }
    return r;
  }
  function renderHead(t) {
    const v = S.vessel; if (!v || !map) return;
    const vp = verifiedPts();
    const played = vp.filter((p) => p[0] <= t).map((p) => [p[1], p[2]]);
    const h = safeAt(v, t);
    if (h && played.length) played.push([h.lon, h.lat]);
    setData('rw-verified', fc(played.length > 1 ? [line(played)] : []));
    const feats = [], links = [];
    if (h) {
      const state = h.state || stateAt(t);
      feats.push(point([h.lon, h.lat], { kind: 'head', state }));
      const c = h.claimed;
      if (c && Number.isFinite(c.lon) && state === 'spoofing') {
        feats.push(point([c.lon, c.lat], { kind: 'claimed' }));
        links.push(line([[c.lon, c.lat], [h.lon, h.lat]]));
      }
    }
    setData('rw-head', fc(feats)); setData('rw-link', fc(links));
  }
  function fitToTrack() {
    if (!map || !S.vessel) return;
    const pts = [...verifiedPts().map((p) => [p[1], p[2]]), ...claimedPts().map((p) => [p[1], p[2]]),
      ...(S.vessel.observed || []).map((o) => [o.lon, o.lat])].filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]) && Math.abs(p[1]) < 85);
    if (!pts.length) return;
    let b = [Infinity, Infinity, -Infinity, -Infinity];
    for (const [x, y] of pts) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
    if (b[2] - b[0] < 0.02) { b[0] -= 0.02; b[2] += 0.02; }
    if (b[3] - b[1] < 0.01) { b[1] -= 0.01; b[3] += 0.01; }
    const W = window.innerWidth;
    const padding = W > 1000 ? { top: 110, bottom: 150, left: 380, right: 440 } : { top: 90, bottom: 150, left: 40, right: 40 };
    try { map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding, duration: 800, maxZoom: 12.5 }); } catch (e) { /* ignore */ }
  }

  // ---------- render ----------
  function renderAll() { renderPicker(); renderRows(); renderScrubber(); renderLayers(); renderTime(clockT()); renderPlay(); }
  function renderTime(t) {
    if (!S.vessel) return;
    const tt = Math.max(S.range[0], Math.min(S.range[1], t));
    const f = frac(tt);
    fill.style.width = `${f * 100}%`; handle.style.left = `${f * 100}%`;
    track.setAttribute('aria-valuenow', Math.round(tt)); track.setAttribute('aria-valuetext', `${hms(tt)} UTC`);
    $('.rw-now-time').textContent = hms(tt);
    $('.rw-now-date').textContent = `${dmy(tt)} UTC`;
    if (S.lastT !== tt) { renderHead(tt); S.lastT = tt; }
    // active row = last event at or before t
    let idx = -1;
    for (let i = 0; i < S.rows.length; i++) if (S.rows[i].t <= tt) idx = i; else break;
    if (idx !== S.activeRow) {
      S.activeRow = idx;
      rowsEl.querySelectorAll('.rw-row').forEach((b) => {
        const i = +b.dataset.i;
        b.classList.toggle('is-active', i === idx);
        b.classList.toggle('is-future', i > idx);
      });
      if (idx >= 0 && isPlaying()) rowsEl.querySelector(`.rw-row[data-i="${idx}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
  function onTick(x) {
    if (!S.visible || !S.vessel) return;
    const t = typeof x === 'number' ? x : Number.isFinite(x?.t) ? x.t : clockT();
    if (isPlaying() && t >= S.range[1]) { pause(); }
    renderTime(t);
    if (playBtn.getAttribute('aria-label') !== (isPlaying() ? 'Pause' : 'Play')) renderPlay();
  }
  if (typeof ctx.clock?.on === 'function') ctx.clock.on(onTick); else on('tick', onTick);

  // ---------- generate report ----------
  const modal = $('.rw-modal'), form = $('.rw-gen'), errEl = $('.rw-gen-err');
  function defaultTemplate(v) {
    const c = caseId();
    if (c === 'war-risk' || v?.voyage) return 'insurance';
    if (c === 'dark-skagen') return 'sanctions';
    if (c === 'cable' || c === 'baltic-2024' || c === 'taiwan') return 'cable';
    return 'sanctions';
  }
  function setPeriod(kind) {
    const d = longestDark();
    let a = S.range[0], b = S.range[1];
    if (kind === 'dark' && d) { a = Math.max(S.range[0], d.start - 3600); b = Math.min(S.range[1], (d.end ?? d.start) + 3600); }
    form.elements.from.value = toInput(a); form.elements.to.value = toInput(b);
    root.querySelectorAll('.rw-chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.period === kind)));
  }
  function openGen(opts = {}) {
    if (!S.vessel) return;
    openPicker(false);
    const tpl = opts.template && TEMPLATES.some((t) => t.id === opts.template) ? opts.template : defaultTemplate(S.vessel);
    form.querySelectorAll('input[name=template]').forEach((r) => { r.checked = r.value === tpl; });
    $('.rw-gen-sub').textContent = `${S.vessel.id}${S.vessel.name ? ' · ' + S.vessel.name : ''} · from the stored vessel record`;
    root.querySelector('.rw-chip[data-period="dark"]').hidden = !longestDark();
    setPeriod(longestDark() ? 'dark' : 'full');
    if (opts.from) form.elements.from.value = toInput(opts.from);
    if (opts.to) form.elements.to.value = toInput(opts.to);
    if (opts.from || opts.to) root.querySelectorAll('.rw-chip').forEach((c) => c.setAttribute('aria-pressed', 'false'));
    if (opts.requested_by) form.elements.requested_by.value = opts.requested_by;
    errEl.hidden = true;
    modal.hidden = false;
    (form.querySelector('input[name=template]:checked') || form.elements.from).focus();
  }
  const closeGen = () => { modal.hidden = true; };
  $('.rw-gen-open').addEventListener('click', () => openGen());
  $('.rw-gen-close').addEventListener('click', closeGen);
  $('.rw-gen-cancel').addEventListener('click', closeGen);
  modal.addEventListener('pointerdown', (e) => { if (e.target === modal) closeGen(); });
  root.querySelectorAll('.rw-chip').forEach((c) => c.addEventListener('click', () => setPeriod(c.dataset.period)));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = {
      vessel_id: S.vessel.id, template: fd.get('template') || 'cable',
      from: fromInput(fd.get('from')), to: fromInput(fd.get('to')), requested_by: String(fd.get('requested_by') || '').trim() || 'Skynet operator',
    };
    if (body.from == null || body.to == null || body.to <= body.from) { errEl.textContent = 'Choose a period where "To" is after "From".'; errEl.hidden = false; return; }
    const go = $('.rw-gen-go'); go.disabled = true; go.textContent = 'Generating…';
    try {
      const r = await fetch(`/api/reports?case=${encodeURIComponent(caseId())}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(`The report service answered ${r.status}.`);
      const rep = await r.json();
      if (!rep?.id) throw new Error('The report service returned no report id.');
      closeGen(); showPreview(rep);
      emit('toast', { text: `${rep.id} generated · ${rep.title || ''}` });
    } catch (err) {
      errEl.textContent = `Could not generate the report. ${err.message || ''}`; errEl.hidden = false;
    } finally { go.disabled = false; go.textContent = 'Generate report'; }
  });

  const preview = $('.rw-preview'), frame = $('.rw-frame');
  function showPreview(rep) {
    S.report = rep;
    const c = encodeURIComponent(caseId()), id = encodeURIComponent(rep.id);
    $('.rw-preview-id').textContent = rep.id;
    $('.rw-preview-name').textContent = rep.title || '';
    $('.rw-pdf').href = `/api/reports/${id}.pdf?case=${c}`;
    $('.rw-pdf').setAttribute('download', `${rep.id}.pdf`);
    $('.rw-full').href = `/report.html?id=${id}&case=${c}`;
    frame.src = `/report.html?id=${id}&case=${c}&embed=1`;
    preview.hidden = false;
    requestAnimationFrame(() => preview.classList.add('is-open'));
    $('.rw-preview-close').focus();
  }
  function closePreview() {
    preview.classList.remove('is-open');
    setTimeout(() => { if (!preview.classList.contains('is-open')) { preview.hidden = true; frame.src = 'about:blank'; } }, 260);
  }
  $('.rw-preview-close').addEventListener('click', closePreview);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !S.visible) return;
    if (!modal.hidden) closeGen(); else if (!pickPop.hidden) openPicker(false); else if (!preview.hidden) closePreview();
  });

  // ---------- mode + bus ----------
  function show(v) {
    S.visible = v;
    root.hidden = !v;
    root.classList.toggle('is-visible', v);
    setVisible(v);
    if (v) {
      if (!S.vesselId || !(world().vessels || []).some((x) => x.id === S.vesselId)) {
        const id = S.pending?.vessel_id || defaultVesselId();
        if (id) selectVessel(id, { seek: S.pending?.t ?? null }); else renderEmpty();
      } else if (!S.pending) { fitToTrack(); renderAll(); }
      if (!S.pending) pause();
    } else {
      closeGen(); openPicker(false);
    }
  }
  function renderEmpty() { $('.rw-pick-id').textContent = 'No vessels in this case'; $('.rw-rows').innerHTML = '<li class="rw-empty">No events</li>'; }

  async function openFor(d, then) {
    S.pending = d;
    if (ctx.mode !== 'report') { try { ctx.setMode?.('report'); } catch (e) { /* ignore */ } }
    if (!S.visible) show(true);
    S.pending = null;
    const id = d.vessel_id || S.vesselId || defaultVesselId();
    if (id && id !== S.vesselId) await selectVessel(id, { seek: d.t ?? null });
    else if (id) { fitToTrack(); if (d.t != null) setT(d.t); }
    pause();
    then?.();
  }
  on('mode', (d) => show((d.mode || d) === 'report'));
  on('open-rewind', (d) => openFor(d || {}));
  on('open-report', (d) => openFor(d || {}, () => openGen(d || {})));
  on('select-vessel', (d) => { if (S.visible && d.id && d.id !== S.vesselId) selectVessel(d.id); });
  on('case', (d) => { if (d?.id) S.caseId = d.id; });
  on('world', () => {
    const vs = world().vessels || [];
    if (S.vesselId && !vs.some((v) => v.id === S.vesselId)) { S.vesselId = null; S.vessel = null; S.record = null; }
    if (S.visible) {
      if (S.vesselId) selectVessel(S.vesselId, { fit: false });
      else { const id = defaultVesselId(); if (id) selectVessel(id); else renderEmpty(); }
    } else if (S.vesselId) { S.vessel = vs.find((v) => v.id === S.vesselId); S.record = null; }
  });
  // Re-add our layers only after a real style swap (setStyle drops sources); never recurse.
  let readding = false;
  if (map?.on) map.on('styledata', () => {
    if (readding || !S.vessel || map.getSource('rw-head') || !map.isStyleLoaded?.()) return;
    readding = true; try { renderLayers(); } finally { readding = false; }
  });

  show(ctx.mode === 'report');
}
