// Skynet app shell: boot, mode router, bus, clock, world loading. Owner: lead.
import { api, setCase, getCase } from './api.js';

const $ = (s) => document.querySelector(s);
const FALLBACK_CASES = [{ id: 'cable', label: 'Cable protection · Gulf of Finland (seeded)' }];
const SPEEDS = [1, 10, 30, 60, 120];
const MODES = ['detect', 'alert', 'report'];

// ---------- bus ----------
const target = new EventTarget();
const bus = {
  on(name, fn) { const h = (e) => fn(e.detail || {}); target.addEventListener(name, h); return () => target.removeEventListener(name, h); },
  emit(name, detail = {}) { target.dispatchEvent(new CustomEvent(name, { detail })); },
};

// ---------- clock ----------
const clock = {
  t: 0, speed: 30, playing: false, min: 0, max: Infinity,
  _subs: new Set(), _last: 0,
  set(t) { this.t = Math.max(this.min, Math.min(this.max, Math.round(t))); this._emit(); },
  play(speed) { if (speed) this.speed = speed; this.playing = true; this._last = performance.now(); renderClock(); },
  pause() { this.playing = false; renderClock(); },
  on(fn) { this._subs.add(fn); return () => this._subs.delete(fn); },
  _emit() { for (const f of this._subs) { try { f(this.t); } catch (e) { console.error(e); } } bus.emit('tick', { t: this.t }); renderClock(); },
};
function frame(now) {
  if (clock.playing) {
    const dt = (now - clock._last) / 1000;
    if (dt >= 0.1) {
      clock._last = now;
      const next = clock.t + dt * clock.speed;
      if (next >= clock.max) { clock.t = clock.max; clock.playing = false; } else clock.t = next;
      clock._emit();
    }
  } else clock._last = now;
  requestAnimationFrame(frame);
}

// ---------- formatting ----------
const pad = (n) => String(n).padStart(2, '0');
const fmtTime = (t) => { const d = new Date(t * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
const fmtDate = (t) => new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const fmtDateTime = (t) => `${fmtDate(t)} ${fmtTime(t)} UTC`;
const fmtDuration = (s) => { s = Math.round(s / 60); if (s < 60) return `${s} min`; const h = Math.floor(s / 60), m = s % 60; return m ? `${h} h ${m} min` : `${h} h`; };

// ---------- vessel interpolation ----------
function bisect(arr, t) { let lo = 0, hi = arr.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (arr[mid][0] <= t) lo = mid; else hi = mid - 1; } return lo; }
function interp(track, t) {
  if (!track || !track.length) return null;
  if (t <= track[0][0]) return track[0];
  if (t >= track[track.length - 1][0]) return track[track.length - 1];
  const i = bisect(track, t), a = track[i], b = track[i + 1] || a;
  const f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
  return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] ?? null, a[4] ?? null];
}
function stateAt(v, t) {
  for (const s of v.states || []) if (t >= s.start && t <= s.end) return s.state;
  return (v.states && v.states.length) ? v.states[v.states.length - 1].state : (v.track && v.track.length ? 'verified' : 'dark');
}
function vesselAt(v, t) {
  const sc = ctx.world?.scenario || {};
  const track = v.track || [];
  if (track.length) {
    if (t < track[0][0] - 600 || t > track[track.length - 1][0] + 600) return null;
    const p = interp(track, t);
    const state = stateAt(v, t);
    let claimed = null;
    const c = v.claimed || [];
    if (c.length && state !== 'dark') {
      const i = bisect(c, t);
      const q = c[i];
      if (q && Math.abs(t - q[0]) <= 600) { const ci = interp(c, t); claimed = { lon: ci[1], lat: ci[2] }; }
    }
    return { lon: p[1], lat: p[2], sog: p[3], cog: p[4], state, claimed };
  }
  const obs = v.observed || [];
  if (!obs.length) return null;
  let best = obs[0];
  for (const o of obs) if (Math.abs(o.t - t) < Math.abs(best.t - t)) best = o;
  if (sc.time_mode !== 'static' && Math.abs(best.t - t) > 3 * 3600) return null;
  return { lon: best.lon, lat: best.lat, sog: null, cog: null, state: stateAt(v, t), claimed: null, observed_only: true };
}

// ---------- context ----------
const ctx = {
  map: null, api, bus, clock, world: null, mode: 'detect', case: getCase(), cases: FALLBACK_CASES,
  setMode, reloadWorld, vesselAt, fmtTime, fmtDate, fmtDateTime, fmtDuration,
  vessel: (id) => ctx.world?.vessels?.find((v) => v.id === id) || null,
  toast: (text) => bus.emit('toast', { text }),
};
window.skynet = ctx;

function setMode(m) {
  if (!MODES.includes(m)) m = 'detect';
  ctx.mode = m;
  document.body.dataset.mode = m;
  document.querySelectorAll('.tb-mode').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === m)));
  if (location.hash !== `#${m}`) history.replaceState(null, '', `${location.pathname}${location.search}#${m}`);
  bus.emit('mode', { mode: m });
}

async function reloadWorld({ resetClock = false } = {}) {
  const w = await api.get('/api/world');
  ctx.world = w;
  const sc = w.scenario || {};
  clock.min = sc.start ?? 0;
  clock.max = sc.end ?? Infinity;
  if (resetClock || !clock.t || clock.t < clock.min || clock.t > clock.max) {
    if (sc.time_mode === 'static') { clock.pause(); clock.t = sc.now ?? sc.end ?? sc.start; }
    else { clock.t = Math.max(clock.min, (sc.now ?? clock.min) - 50 * 60); clock.play(30); }
  }
  renderBanner();
  bus.emit('world', { world: w });
  clock._emit();
  return w;
}

async function switchCase(id, { fit = true } = {}) {
  setCase(id); ctx.case = id;
  const u = new URL(location.href); u.searchParams.set('case', id); history.replaceState(null, '', u);
  $('#case-select').value = id;
  try {
    await reloadWorld({ resetClock: true });
    if (fit) fitWorld();
    bus.emit('case', { id });
  } catch (e) { console.error(e); ctx.toast(`Could not load use case: ${e.message}`); }
}

function fitWorld() {
  const b = ctx.world?.scenario?.bbox;
  if (ctx.map && b) ctx.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: 120, bottom: 120, left: 80, right: 420 }, duration: 800 });
}

// ---------- top bar ----------
function renderClock() {
  const el = $('#clock-time'); if (!el) return;
  el.textContent = clock.t ? `${fmtTime(clock.t)} UTC` : '--:--';
  const btn = $('#clock-play');
  const icon = clock.playing ? 'pause' : 'play';
  if (btn.dataset.icon !== icon) { btn.dataset.icon = icon; btn.innerHTML = `<i data-lucide="${icon}"></i>`; btn.setAttribute('aria-label', clock.playing ? 'Pause' : 'Play'); window.lucide?.createIcons(); }
  $('#clock-speed').textContent = `${clock.speed}×`;
}
let badgeT = 0;
function renderBadge() {
  const now = performance.now(); if (now - badgeT < 400) return; badgeT = now;
  const n = (ctx.world?.alerts || []).filter((a) => a.t <= clock.t && (a.status || 'open') === 'open').length;
  const b = $('#tb-alert-badge'); b.hidden = !n; b.textContent = n;
}
function renderBanner() {
  const sc = ctx.world?.scenario || {}, el = $('#case-banner');
  if (!sc.label) { el.hidden = true; return; }
  const kind = sc.illustrative === false ? '<span class="chip green">Real data</span>' : '<span class="chip">Illustrative</span>';
  const note = sc.illustrative === false
    ? (sc.sources || []).slice(0, 3).map((s) => s.label).join(' · ')
    : (sc.note || 'Illustrative reconstruction inspired by the 2024 Estlink 2 incident · all names, times and figures are illustrative');
  el.innerHTML = `${kind}<span class="cb-text"><span class="w500" style="color:var(--text)">${esc(sc.label)}</span>${note ? ' · ' + esc(note) : ''}</span><button class="cb-close" aria-label="Hide"><i data-lucide="x"></i></button>`;
  el.hidden = false;
  el.querySelector('.cb-close').onclick = () => { el.hidden = true; };
  window.lucide?.createIcons();
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function loadCases() {
  try { const c = await api.get('/api/cases'); if (Array.isArray(c) && c.length) ctx.cases = c; } catch { /* fallback list */ }
  if (!ctx.cases.find((c) => c.id === ctx.case)) ctx.cases = [{ id: ctx.case, label: ctx.case }, ...ctx.cases];
  const sel = $('#case-select');
  sel.innerHTML = ctx.cases.map((c) => `<option value="${esc(c.id)}">${esc(c.label)}${c.data === 'real' ? ' · real data' : ''}</option>`).join('');
  sel.value = ctx.case;
  sel.onchange = () => switchCase(sel.value);
}

function wireTopbar() {
  document.querySelectorAll('.tb-mode').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
  $('#clock-play').onclick = () => (clock.playing ? clock.pause() : (clock.t >= clock.max && clock.set(clock.min), clock.play()));
  $('#clock-speed').onclick = () => { clock.speed = SPEEDS[(SPEEDS.indexOf(clock.speed) + 1) % SPEEDS.length]; renderClock(); };
  $('#reset-btn').onclick = async () => {
    try { await api.post('/api/reset'); await reloadWorld({ resetClock: true }); fitWorld(); bus.emit('zones-changed'); ctx.toast('Demo reset'); }
    catch (e) { ctx.toast(`Reset failed: ${e.message}`); }
  };
  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select, [contenteditable]')) return;
    if (e.key === ' ' && !e.target.closest('button')) { e.preventDefault(); $('#clock-play').click(); }
    if (e.key === '1' || e.key === '2' || e.key === '3') setMode(MODES[+e.key - 1]);
  });
  let toastTimer;
  bus.on('toast', ({ text }) => { const t = $('#toast'); t.textContent = text; t.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('show'), 3600); });
  bus.on('open-report', () => { if (ctx.mode !== 'report') setMode('report'); });
  bus.on('open-zones', () => { if (ctx.mode !== 'alert') setMode('alert'); });
  bus.on('focus', ({ vessel_ids, bbox, t }) => {
    if (t) clock.set(t);
    if (!ctx.map) return;
    if (bbox) { ctx.map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 160, duration: 800, maxZoom: 12 }); return; }
    const pts = (vessel_ids || []).map((id) => ctx.vessel(id)).filter(Boolean).map((v) => vesselAt(v, clock.t) || vesselAt(v, (v.track?.[0]?.[0]) ?? clock.t)).filter(Boolean);
    if (pts.length === 1) ctx.map.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: Math.max(ctx.map.getZoom(), 10), duration: 800 });
    else if (pts.length > 1) {
      const lons = pts.map((p) => p.lon), lats = pts.map((p) => p.lat);
      ctx.map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 160, duration: 800, maxZoom: 12 });
    }
  });
  clock.on(renderBadge);
  bus.on('world', () => { badgeT = 0; renderBadge(); });
}

async function importModule(path, fn = 'init') {
  try { const m = await import(path); if (typeof m[fn] === 'function') await m[fn](ctx); }
  catch (e) { console.error(`[skynet] ${path} failed`, e); }
}

async function boot() {
  window.lucide?.createIcons();
  wireTopbar();
  requestAnimationFrame(frame);
  const casesP = loadCases();
  const worldP = reloadWorld({ resetClock: true }).catch((e) => { console.error(e); ctx.toast(`World failed to load: ${e.message}`); });

  ctx.map = new maplibregl.Map({
    container: 'map', style: 'https://tiles.openfreemap.org/styles/positron',
    center: [25.6, 59.85], zoom: 7.4, attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false,
  });
  ctx.map.touchZoomRotate.disableRotation();
  ctx.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  await new Promise((r) => (ctx.map.loaded() ? r() : ctx.map.once('load', r)));
  await Promise.all([casesP, worldP]);
  fitWorld();

  await importModule('/js/map.js', 'initMap');
  for (const m of ['/js/detect.js', '/js/alert.js', '/js/zones.js', '/js/report-mode.js', '/js/analyst.js']) await importModule(m);

  const fromHash = location.hash.slice(1);
  setMode(MODES.includes(fromHash) ? fromHash : 'detect');
  if (ctx.world) { bus.emit('world', { world: ctx.world }); clock._emit(); }
  window.lucide?.createIcons();
}

boot();
