// Skynet · Alert mode (owner: F)
// Alert feed (right side), compact slide-in notification in other modes,
// recipient phone preview (390x844) with simulated delivery log.

const MIN = 60;
const FALLBACK_ACTIONS = [
  { id: 'acknowledge', label: 'Acknowledge' },
  { id: 'open_record', label: 'Open record' },
];
const CHANNEL_ICON = { push: 'smartphone', email: 'mail', webhook: 'webhook', in_app: 'bell', sms: 'message-square' };
const CHANNEL_LABEL = { push: 'Push', email: 'Email', webhook: 'Webhook', in_app: 'In-app', sms: 'SMS' };
const SEV_LABEL = { high: 'High', medium: 'Medium', low: 'Low' };
const SEV_RANK = { high: 0, medium: 1, low: 2 };
const CASE_TEMPLATE = { cable: 'cable', 'baltic-2024': 'cable', taiwan: 'cable', 'dark-skagen': 'sanctions', 'war-risk': 'insurance', migration: 'cable' };

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function init(ctx) {
  const root = document.getElementById('alert') || Object.assign(document.body.appendChild(document.createElement('div')), { id: 'alert' });
  root.classList.add('al-root');
  root.innerHTML = `
    <section class="al-feed" aria-label="Alert feed">
      <header class="al-head">
        <div class="al-head-title"></div>
        <div class="al-queue" aria-live="polite"></div>
      </header>
      <div class="al-list" role="list"></div>
    </section>
    <div class="al-notify-wrap" aria-live="assertive"></div>`;
  let phone = document.getElementById('phone');
  if (!phone) { phone = document.createElement('div'); phone.id = 'phone'; }
  if (phone.parentElement !== root) root.appendChild(phone);
  phone.classList.add('al-phone-overlay');
  phone.hidden = true;

  const feed = root.querySelector('.al-feed');
  const headTitle = root.querySelector('.al-head-title');
  const queueEl = root.querySelector('.al-queue');
  const list = root.querySelector('.al-list');
  const notifyWrap = root.querySelector('.al-notify-wrap');

  // ---- state ------------------------------------------------------------
  const seen = new Set();          // alert ids already shown (fired)
  const logged = new Map();        // alert id -> Action (latest)
  const pending = new Set();       // alert ids with a request in flight
  const notifyIds = new Set();     // alerts that fired while not in Alert mode
  let lastT = null;
  let caseId = null;
  let scenarioKey = null;
  let mode = ctx.mode || 'detect';
  let phoneState = null;           // {alertId, screen:'lock'|'detail'}

  // ---- helpers ----------------------------------------------------------
  const on = (name, fn) => {
    const h = (x) => fn(x instanceof Event && 'detail' in x ? x.detail : x);
    if (ctx.bus?.on) ctx.bus.on(name, h);
    else if (ctx.bus?.addEventListener) ctx.bus.addEventListener(name, h);
  };
  const emit = (name, detail) => {
    try {
      if (ctx.bus?.emit) ctx.bus.emit(name, detail);
      else ctx.bus?.dispatchEvent?.(new CustomEvent(name, { detail }));
    } catch (e) { console.warn('[alert] emit', name, e); }
  };
  const world = () => ctx.world || {};
  const nowT = () => (ctx.clock && Number.isFinite(ctx.clock.t) ? ctx.clock.t : (world().scenario?.now ?? 0));
  const fmtTime = (t) => {
    if (!Number.isFinite(t)) return '';
    try { if (ctx.fmtTime) return ctx.fmtTime(t); } catch {}
    const d = new Date(t * 1000);
    return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
  };
  const currentCase = () => {
    const c = ctx.caseId ?? ctx.case ?? ctx.api?.caseId ?? ctx.api?.case;
    if (typeof c === 'string' && c) return c;
    if (c && typeof c === 'object' && c.id) return c.id;
    if (caseId) return caseId;
    return new URLSearchParams(location.search).get('case') || 'cable';
  };
  const vesselById = (id) => (world().vessels || []).find((v) => v.id === id);
  const zoneById = (id) => (world().zones || []).find((z) => z.id === id);
  const alertById = (id) => (world().alerts || []).find((a) => a.id === id);
  const actionsFor = (a) => (Array.isArray(a.actions) && a.actions.length ? a.actions : FALLBACK_ACTIONS);
  const isDismiss = (act) => act.id === 'dismiss' || /not suspicious|dismiss/i.test(act.label || '');
  const isOpenRecord = (act) => act.id === 'open_record';
  const statusOf = (a) => a.status || 'open';
  const icons = () => { try { window.lucide?.createIcons?.(); } catch {} };
  const fired = () => {
    const t = nowT();
    return (world().alerts || []).filter((a) => Number.isFinite(a.t) ? a.t <= t : true);
  };
  const vesselLabel = (a) => {
    const v = vesselById(a.vessel_id);
    return v ? `${v.id}${v.name ? ' · ' + v.name : ''}` : (a.vessel_id || '');
  };
  const recipientsFor = (a) => {
    if (Array.isArray(a.recipients) && a.recipients.length) return a.recipients;
    const z = zoneById(a.zone_id);
    if (z?.recipients?.length) return z.recipients;
    return [{ name: 'Duty officer', channel: 'in_app' }];
  };
  const templateFor = (a) => {
    const z = zoneById(a.zone_id);
    if (z && ['cable', 'sanctions', 'insurance'].includes(z.purpose)) return z.purpose;
    return CASE_TEMPLATE[currentCase()] || 'cable';
  };
  const openRecord = (a) => {
    const s = world().scenario || {};
    const from = Math.max(s.start ?? -Infinity, (a.t ?? nowT()) - 3 * 3600);
    const to = Math.min(s.end ?? Infinity, (a.t ?? nowT()) + 30 * MIN);
    emit('open-report', { vessel_id: a.vessel_id, template: templateFor(a), from, to, alert_id: a.id });
  };
  const viewOnMap = (a) => emit('focus', { vessel_ids: [a.vessel_id], t: a.t });
  const loggedFor = (a) => {
    if (logged.has(a.id)) return logged.get(a.id);
    const acts = (world().actions || []).filter((x) => x.alert_id === a.id);
    return acts.length ? acts[acts.length - 1] : null;
  };
  const loggedText = (a, act) => `Logged to ${a.vessel_id} record · ${fmtTime(act.t)} · ${act.label || act.action}`;

  // ---- mini track map (inline SVG) -------------------------------------
  function miniMap(a, w = 348, h = 148) {
    const v = vesselById(a.vessel_id);
    const tEnd = Number.isFinite(a.t) ? a.t : nowT();
    const tStart = tEnd - 60 * MIN;
    const inWin = (p) => p[0] >= tStart && p[0] <= tEnd + 2 * MIN;
    const track = (v?.track || []).filter(inWin);
    const claimed = (v?.claimed || []).filter(inWin);
    const obs = (v?.observed || []).filter((o) => o.t >= tStart && o.t <= tEnd + 2 * MIN);
    const events = (world().events || []).filter((e) => (a.event_ids || []).includes(e.id) && Number.isFinite(e.lon));
    const pts = [...track.map((p) => [p[1], p[2]]), ...obs.map((o) => [o.lon, o.lat]), ...events.map((e) => [e.lon, e.lat])];
    // claimed AIS: include only when near the track (spoofing can put it far away)
    const claimPts = claimed.map((p) => [p[1], p[2]]);
    if (!pts.length) pts.push(...claimPts);
    if (!pts.length) return '';
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
    const grow = (p) => { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); };
    pts.forEach(grow);
    const midLat = (y0 + y1) / 2;
    const kx = Math.cos((midLat * Math.PI) / 180);
    // include claimed points within 3x the current span
    const span0 = Math.max((x1 - x0) * kx, y1 - y0, 0.01);
    claimPts.forEach((p) => { if (Math.abs((p[0] - (x0 + x1) / 2) * kx) < span0 * 3 && Math.abs(p[1] - midLat) < span0 * 3) grow(p); });
    let spanX = Math.max((x1 - x0) * kx, 0.012);
    let spanY = Math.max(y1 - y0, 0.008);
    const pad = 18;
    const scale = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const P = (lon, lat) => [w / 2 + (lon - cx) * kx * scale, h / 2 - (lat - cy) * scale];
    const path = (arr) => arr.map((p, i) => { const [x, y] = P(p[0], p[1]); return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`; }).join('');
    const kmPx = scale / 111.32; // px per km (lat degrees)

    let out = '';
    // zone outline
    const z = zoneById(a.zone_id);
    if (z?.geometry?.type === 'corridor' && z.geometry.line?.length) {
      const wpx = Math.max(6, (z.geometry.width_km || 4) * kmPx);
      out += `<path class="al-mm-zone" d="${path(z.geometry.line)}" stroke-width="${wpx.toFixed(1)}"/>`;
      out += `<path class="al-mm-zone-edge" d="${path(z.geometry.line)}" stroke-width="${(wpx + 2).toFixed(1)}"/>`;
    } else if (z?.geometry?.type === 'polygon' && z.geometry.coordinates?.length) {
      out += `<path class="al-mm-zonepoly" d="${path(z.geometry.coordinates)}Z"/>`;
    }
    // asset line(s)
    const asset = (world().assets || []).find((x) => x.id === (z?.asset_id || a.asset_id))
      || (world().assets || []).find((x) => events.some((e) => e.asset_id === x.id));
    if (asset?.geometry) {
      const g = asset.geometry;
      const lines = g.type === 'MultiLineString' ? g.coordinates : g.type === 'LineString' ? [g.coordinates] : [];
      lines.forEach((ln) => { out += `<path class="al-mm-asset" d="${path(ln)}"/>`; });
    }
    // claimed AIS (dashed)
    if (claimPts.length > 1) out += `<path class="al-mm-claimed" d="${path(claimPts)}"/>`;
    // verified track, split into verified / dark runs
    const darkAt = (t) => (v?.dark_periods || []).some((d) => t >= d.start && t <= d.end)
      || (v?.states || []).some((s) => s.state === 'dark' && t >= s.start && t <= s.end);
    if (track.length > 1) {
      let run = [track[0]], dark = darkAt(track[0][0]);
      const flush = () => { if (run.length > 1) out += `<path class="${dark ? 'al-mm-dark' : 'al-mm-track'}" d="${path(run.map((p) => [p[1], p[2]]))}"/>`; };
      for (let i = 1; i < track.length; i++) {
        const d = darkAt(track[i][0]);
        run.push(track[i]);
        if (d !== dark) { flush(); run = [track[i]]; dark = d; }
      }
      flush();
    }
    // radar detections
    obs.forEach((o) => { const [x, y] = P(o.lon, o.lat); out += `<rect class="al-mm-obs" x="${(x - 3.5).toFixed(1)}" y="${(y - 3.5).toFixed(1)}" width="7" height="7" transform="rotate(45 ${x.toFixed(1)} ${y.toFixed(1)})"/>`; });
    // vessel now
    const last = track.length ? [track[track.length - 1][1], track[track.length - 1][2]] : pts[pts.length - 1];
    const [lx, ly] = P(last[0], last[1]);
    out += `<circle class="al-mm-ring" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="9"/><circle class="al-mm-dot ${darkAt(tEnd) ? 'is-dark' : ''}" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4.5"/>`;
    // scale bar
    const nice = [0.5, 1, 2, 5, 10, 20, 50, 100].find((k) => k * kmPx >= 36) || 100;
    const bw = nice * kmPx;
    out += `<g class="al-mm-scale" transform="translate(${(w - 12 - bw).toFixed(1)} ${h - 12})"><path d="M0 -4V0H${bw.toFixed(1)}V-4"/><text x="${(bw / 2).toFixed(1)}" y="-7" text-anchor="middle">${nice} km</text></g>`;

    const legend = [
      track.length ? '<span><i class="al-lg al-lg-track"></i>Verified</span>' : '',
      claimPts.length ? '<span><i class="al-lg al-lg-claimed"></i>AIS claimed</span>' : '',
      track.some((p) => darkAt(p[0])) ? '<span><i class="al-lg al-lg-dark"></i>Dark</span>' : '',
      asset ? `<span><i class="al-lg al-lg-asset"></i>${esc(asset.alias || asset.name || 'Asset')}</span>` : '',
    ].join('');
    return `<figure class="al-mm">
      <svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Last 60 minutes: verified track versus claimed AIS">${out}</svg>
      <figcaption class="al-mm-legend">${legend}<span class="al-mm-win">Last 60 min</span></figcaption>
    </figure>`;
  }

  // ---- card -------------------------------------------------------------
  const sevChip = (a) => `<span class="al-sev al-sev-${esc(a.severity || 'medium')}"><i data-lucide="${a.severity === 'high' ? 'triangle-alert' : 'circle-alert'}"></i>${esc(SEV_LABEL[a.severity] || 'Medium')}</span>`;
  const recipientsLine = (a, phoneLink = false) => {
    const rs = recipientsFor(a);
    return `<div class="al-recip"><span class="al-recip-label">Sent to</span>${rs.map((r, i) => `${i ? '<span class="al-sep">·</span>' : ''}<span class="al-recip-item"><i data-lucide="${CHANNEL_ICON[r.channel] || 'bell'}" aria-label="${esc(CHANNEL_LABEL[r.channel] || r.channel)}"></i>${esc(r.name)}</span>`).join('')}${phoneLink ? `<button type="button" class="al-recip-phone" data-link="phone" data-alert="${esc(a.id)}"><i data-lucide="smartphone"></i>See what the recipient sees</button>` : ''}</div>`;
  };
  const actionButtons = (a, where) => {
    const acts = actionsFor(a);
    return `<div class="al-actions">${acts.map((act, i) => `<button type="button" class="al-btn ${i === 0 ? 'al-btn-primary' : ''} ${isDismiss(act) ? 'al-btn-quiet' : ''}" data-act="${esc(act.id)}" data-alert="${esc(a.id)}" data-where="${where}" ${pending.has(a.id) ? 'disabled' : ''}>${esc(act.label)}</button>`).join('')}</div>`;
  };
  const loggedLine = (a, act) => `<div class="al-logged" role="status"><i data-lucide="circle-check"></i><span>${esc(loggedText(a, act))}</span></div>`;

  function cardHTML(a) {
    const st = statusOf(a);
    const act = loggedFor(a);
    const hasOpenRecordAction = actionsFor(a).some(isOpenRecord);
    const decided = st !== 'open';
    return `
      <article class="al-card ${decided ? 'is-decided' : ''}" data-id="${esc(a.id)}" role="listitem" tabindex="-1">
        <div class="al-card-top">
          ${decided ? `<span class="al-sev al-sev-done"><i data-lucide="circle-check"></i>${st === 'dismissed' ? 'Dismissed' : 'Decided'}</span>` : sevChip(a)}
          <span class="al-vessel">${esc(vesselLabel(a))}</span>
          <span class="al-time">${esc(fmtTime(a.t))}</span>
        </div>
        <h3 class="al-headline">${esc(a.headline || 'Alert')}</h3>
        ${decided ? '' : `
          <ul class="al-facts">${(a.facts || []).slice(0, 4).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
          ${miniMap(a)}
          ${recipientsLine(a, true)}
          ${actionButtons(a, 'card')}`}
        ${act ? loggedLine(a, act) : ''}
        <div class="al-links">
          <button type="button" class="al-link" data-link="map" data-alert="${esc(a.id)}"><i data-lucide="map-pin"></i>View on map</button>
          ${hasOpenRecordAction && !decided ? '' : `<button type="button" class="al-link" data-link="record" data-alert="${esc(a.id)}"><i data-lucide="file-text"></i>Open record</button>`}
        </div>
      </article>`;
  }

  // ---- feed render -----------------------------------------------------
  function visibleAlerts() {
    const rank = (a) => (statusOf(a) === 'open' ? 0 : 10) + (SEV_RANK[a.severity] ?? 3);
    return fired().filter((a) => seen.has(a.id) && statusOf(a) !== 'dismissed').sort((x, y) => rank(x) - rank(y) || (y.t ?? 0) - (x.t ?? 0));
  }

  function renderHead() {
    const vis = visibleAlerts();
    const open = vis.filter((a) => statusOf(a) === 'open').length;
    const w = world();
    const nv = (w.vessels || []).length, nz = (w.zones || []).length;
    if (open === 0) {
      headTitle.innerHTML = `<span class="al-quiet-dot"></span><span>All quiet · watching ${nv} vessel${nv === 1 ? '' : 's'} in ${nz} zone${nz === 1 ? '' : 's'}</span>`;
      queueEl.innerHTML = '';
      feed.classList.add('is-quiet');
    } else {
      headTitle.innerHTML = `<span class="al-head-label">Alerts</span>`;
      queueEl.innerHTML = `<span class="al-queue-dot"></span>${open} need${open === 1 ? 's' : ''} a decision`;
      feed.classList.remove('is-quiet');
    }
  }

  function renderFeed(animateIds = new Set()) {
    const vis = visibleAlerts();
    const focused = document.activeElement && list.contains(document.activeElement) ? document.activeElement.closest('.al-card')?.dataset.id : null;
    list.innerHTML = vis.map(cardHTML).join('');
    animateIds.forEach((id) => {
      const el = list.querySelector(`.al-card[data-id="${CSS.escape(id)}"]`);
      if (!el) return;
      el.classList.add('is-entering');
      requestAnimationFrame(() => requestAnimationFrame(() => { el.classList.remove('is-entering'); el.classList.add('al-pulse'); }));
      setTimeout(() => el.classList.remove('al-pulse'), 2600);
    });
    if (focused) list.querySelector(`.al-card[data-id="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    renderHead();
    icons();
  }

  // ---- notification in other modes -------------------------------------
  function notify(a, more = 0) {
    const prev = notifyWrap.querySelector('.al-notify');
    const same = prev && prev.dataset.id === a.id;
    notifyWrap.querySelectorAll('.al-notify').forEach((n) => n.remove());
    const el = document.createElement('div');
    el.className = same ? 'al-notify' : 'al-notify is-entering';
    el.dataset.id = a.id;
    el.innerHTML = `
      <div class="al-card-top">${sevChip(a)}<span class="al-vessel">${esc(vesselLabel(a))}</span><span class="al-time">${esc(fmtTime(a.t))}</span></div>
      <p class="al-headline">${esc(a.headline || 'New alert')}</p>
      ${more ? `<p class="al-notify-more">and ${more} more alert${more === 1 ? '' : 's'} need${more === 1 ? 's' : ''} a decision</p>` : ''}
      <div class="al-actions">
        <button type="button" class="al-btn al-btn-primary" data-n="open">Review alert</button>
        <button type="button" class="al-btn" data-n="later">Later</button>
      </div>`;
    notifyWrap.appendChild(el);
    icons();
    requestAnimationFrame(() => requestAnimationFrame(() => { el.classList.remove('is-entering'); el.classList.add('al-pulse'); }));
    el.addEventListener('click', (ev) => {
      const b = ev.target.closest('button[data-n]');
      if (!b) return;
      if (b.dataset.n === 'open') {
        try { ctx.setMode?.('alert'); } catch (e) { console.warn(e); }
        setTimeout(() => list.querySelector(`.al-card[data-id="${CSS.escape(a.id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 50);
      }
      hideNotify();
    });
  }
  function hideNotify() {
    notifyIds.clear();
    notifyWrap.querySelectorAll('.al-notify').forEach((n) => { n.classList.add('is-leaving'); setTimeout(() => n.remove(), 320); });
  }

  // ---- map pulse (HTML marker, no layers) ------------------------------
  function mapPulse(a) {
    try {
      const v = vesselById(a.vessel_id);
      if (!ctx.map || !window.maplibregl || !v) return;
      let pos = null;
      try { pos = ctx.vesselAt?.(v, a.t); } catch {}
      if (!pos) {
        const p = (v.track || []).filter((q) => q[0] <= a.t).pop() || (v.track || [])[0];
        if (p) pos = { lon: p[1], lat: p[2] };
      }
      if (!pos || !Number.isFinite(pos.lon)) return;
      const el = document.createElement('div');
      el.className = 'al-map-pulse';
      el.innerHTML = '<span></span><span></span>';
      const m = new window.maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(ctx.map);
      setTimeout(() => m.remove(), 6200);
    } catch (e) { console.warn('[alert] pulse', e); }
  }

  // ---- firing / clock ---------------------------------------------------
  function fire(list_) {
    if (!list_.length) return;
    list_.forEach((a) => seen.add(a.id));
    renderFeed(new Set(list_.map((a) => a.id)));
    const openOnes = list_.filter((a) => statusOf(a) === 'open');
    openOnes.forEach((a) => { emit('alert', { alert: a }); mapPulse(a); });
    feed.classList.remove('al-feed-pulse'); void feed.offsetWidth; feed.classList.add('al-feed-pulse');
    if (openOnes.length && mode !== 'alert') {
      openOnes.forEach((a) => notifyIds.add(a.id));
      const cand = [...notifyIds].map(alertById).filter((a) => a && statusOf(a) === 'open' && seen.has(a.id))
        .sort((x, y) => (SEV_RANK[x.severity] ?? 3) - (SEV_RANK[y.severity] ?? 3) || (y.t ?? 0) - (x.t ?? 0));
      if (cand.length) notify(cand[0], cand.length - 1);
    }
  }

  function onTick() {
    const t = nowT();
    if (lastT != null && t < lastT - 1) {
      // clock jumped backwards: forget alerts that have not fired yet at t
      let changed = false;
      (world().alerts || []).forEach((a) => { if (Number.isFinite(a.t) && a.t > t && seen.delete(a.id)) changed = true; });
      if (changed) { renderFeed(); const n = notifyWrap.querySelector('.al-notify'); if (n && !seen.has(n.dataset.id)) hideNotify(); }
    }
    lastT = t;
    const fresh = fired().filter((a) => !seen.has(a.id));
    if (fresh.length) fire(fresh);
  }

  function onWorld() {
    const w = world();
    const key = `${currentCase()}|${w.scenario?.id ?? ''}`;
    if (key !== scenarioKey) {
      // new case or first load: already-past alerts appear quietly, no notification
      scenarioKey = key;
      seen.clear(); logged.clear(); pending.clear();
      fired().forEach((a) => seen.add(a.id));
      hideNotify();
      closePhone();
      lastT = nowT();
      renderFeed();
      return;
    }
    // same case reloaded (or reset): the server world is the source of truth
    logged.clear();
    renderFeed();
    onTick();
    if (phoneState) renderPhone();
  }

  // ---- actions ----------------------------------------------------------
  async function doAction(alertId, actId) {
    const a = alertById(alertId);
    if (!a || pending.has(alertId)) return;
    const act = actionsFor(a).find((x) => x.id === actId) || { id: actId, label: actId };
    if (isOpenRecord(act)) { openRecord(a); return; }
    pending.add(alertId);
    renderFeed(); if (phoneState) renderPhone();
    let result = null;
    try {
      const r = await fetch(`/api/alerts/${encodeURIComponent(a.id)}/actions?case=${encodeURIComponent(currentCase())}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: act.id, by: 'Duty officer', note: '', t: Math.round(nowT()) }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      result = await r.json();
    } catch (e) {
      console.warn('[alert] action failed', e);
      pending.delete(alertId);
      renderFeed(); if (phoneState) renderPhone();
      emit('toast', { text: `Could not log "${act.label}". Try again.` });
      return;
    }
    pending.delete(alertId);
    const s = world().scenario || {};
    const inScenario = (t) => Number.isFinite(t) && (!s.start || (t >= s.start - 86400 && t <= (s.end ?? t) + 86400));
    const action = { ...(result || {}), alert_id: a.id, vessel_id: a.vessel_id, action: act.id, label: result?.label || act.label, by: 'Duty officer' };
    if (!inScenario(action.t)) action.t = Math.round(nowT());
    logged.set(a.id, action);
    const dismissed = isDismiss(act);
    a.status = dismissed ? 'dismissed' : 'actioned';
    const w = world();
    if (Array.isArray(w.actions)) w.actions.push(action); else w.actions = [action];
    if (Array.isArray(w.events)) {
      w.events.push({ id: `OP-${action.id || a.id}`, vessel_id: a.vessel_id, type: 'operator_action', source: 'operator', t: action.t, end: null,
        severity: 'low', summary: `${action.by} logged: ${action.label}.`, facts: [], zone_id: a.zone_id ?? null, asset_id: null });
    }
    emit('toast', { text: dismissed ? `Dismissed · logged to ${a.vessel_id} record` : loggedText(a, action) });
    if (dismissed) {
      const el = list.querySelector(`.al-card[data-id="${CSS.escape(a.id)}"]`);
      if (el) { el.style.height = el.offsetHeight + 'px'; requestAnimationFrame(() => el.classList.add('is-leaving')); setTimeout(() => renderFeed(), 380); }
      else renderFeed();
    } else {
      // keep the full card with its logged line for a moment, then collapse
      const el = list.querySelector(`.al-card[data-id="${CSS.escape(a.id)}"]`);
      if (el) {
        el.querySelector('.al-actions')?.remove();
        el.querySelector('.al-logged')?.remove();
        el.querySelector('.al-links')?.insertAdjacentHTML('beforebegin', loggedLine(a, action));
        el.querySelector('.al-sev')?.replaceWith(Object.assign(document.createElement('span'), { className: 'al-sev al-sev-done', innerHTML: '<i data-lucide="circle-check"></i>Decided' }));
        el.classList.add('is-justlogged');
        renderHead(); icons();
        setTimeout(() => { if (statusOf(a) !== 'open') renderFeed(); }, 4500);
      } else renderFeed();
    }
    if (phoneState?.alertId === a.id) renderPhone();
  }

  root.addEventListener('click', (ev) => {
    const b = ev.target.closest('button');
    if (!b || !root.contains(b)) return;
    if (b.dataset.act) { doAction(b.dataset.alert, b.dataset.act); return; }
    const a = alertById(b.dataset.alert);
    if (b.dataset.link && a) {
      if (b.dataset.link === 'map') viewOnMap(a);
      else if (b.dataset.link === 'record') openRecord(a);
      else if (b.dataset.link === 'phone') openPhone(a.id);
    }
  });

  // ---- phone preview ----------------------------------------------------
  function openPhone(alertId) {
    phoneState = { alertId, screen: 'lock', deliveries: null };
    phone.hidden = false;
    renderPhone();
    requestAnimationFrame(() => phone.classList.add('is-open'));
    loadDeliveries(alertId);
    setTimeout(() => phone.querySelector('.al-ph-close')?.focus(), 50);
  }
  function closePhone() {
    if (!phoneState) return;
    phoneState = null;
    phone.classList.remove('is-open');
    setTimeout(() => { if (!phoneState) { phone.hidden = true; phone.innerHTML = ''; } }, 220);
  }
  async function loadDeliveries(alertId) {
    let rows = null;
    try {
      const r = await fetch(`/api/deliveries?alert_id=${encodeURIComponent(alertId)}&case=${encodeURIComponent(currentCase())}`);
      if (r.ok) { const j = await r.json(); rows = Array.isArray(j) ? j : (j.deliveries || null); }
    } catch {}
    if (!phoneState || phoneState.alertId !== alertId) return;
    const a = alertById(alertId);
    if (!rows || !rows.length) {
      // simulated fallback from the alert's recipients plus the Data API webhook
      const base = a?.t ?? nowT();
      rows = recipientsFor(a || {}).map((rc, i) => ({ id: `sim-${i}`, alert_id: alertId, recipient: rc.name, channel: rc.channel, status: 'delivered', t: base + 2 + i * 3,
        preview: { title: `${SEV_LABEL[a?.severity] || 'New'} alert · ${a?.vessel_id || ''}`, body: a?.headline || '' } }));
      rows.push({ id: 'sim-wh', alert_id: alertId, recipient: 'Data API webhook (alert.created)', channel: 'webhook', status: 'delivered', t: base + 1, preview: null });
    }
    phoneState.deliveries = rows;
    renderPhone();
  }

  const appMark = (cls = '') => `<span class="al-ph-appicon ${cls}" aria-hidden="true"><svg viewBox="0 0 24 24" width="100%" height="100%"><circle cx="12" cy="12" r="7.5" fill="none" stroke="#fff" stroke-width="1.6" opacity=".55"/><circle cx="12" cy="12" r="3.6" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M12 12 L18.5 6.5" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><circle cx="17.6" cy="15.6" r="1.5" fill="#E8590C"/></svg></span>`;

  function phoneLock(a) {
    const d = new Date((a.t ?? nowT()) * 1000);
    const date = d.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
    const push = (phoneState.deliveries || []).find((r) => r.channel === 'push');
    const title = push?.preview?.title || `${SEV_LABEL[a.severity] || 'New'} alert · ${a.vessel_id}`;
    const body = push?.preview?.body || a.headline;
    return `
      <div class="al-ph-lock">
        <div class="al-ph-lockclock"><i data-lucide="lock"></i><div class="al-ph-date">${esc(date)}</div><div class="al-ph-big">${esc(fmtTime(a.t))}</div></div>
        <button type="button" class="al-ph-push" data-ph="open" aria-label="Open notification">
          ${appMark()}
          <span class="al-ph-push-text">
            <span class="al-ph-push-top"><span class="al-ph-app">SKYNET</span><span class="al-ph-now">now</span></span>
            <span class="al-ph-push-title">${esc(title)}</span>
            <span class="al-ph-push-body">${esc(body)}</span>
          </span>
        </button>
        <div class="al-ph-hint">Tap the notification</div>
        <div class="al-ph-home"></div>
      </div>`;
  }

  function phoneDetail(a) {
    const act = loggedFor(a);
    const decided = statusOf(a) !== 'open';
    return `
      <div class="al-ph-app-screen">
        <div class="al-ph-appbar">
          <button type="button" class="al-ph-back" data-ph="back" aria-label="Back to lock screen"><i data-lucide="chevron-left"></i></button>
          ${appMark('is-small')}<span class="al-ph-appname">Skynet</span>
        </div>
        <div class="al-ph-body">
          <div class="al-card-top">${decided ? `<span class="al-sev al-sev-done"><i data-lucide="circle-check"></i>${statusOf(a) === 'dismissed' ? 'Dismissed' : 'Decided'}</span>` : sevChip(a)}<span class="al-vessel">${esc(vesselLabel(a))}</span><span class="al-time">${esc(fmtTime(a.t))}</span></div>
          <h3 class="al-ph-headline">${esc(a.headline)}</h3>
          <ul class="al-facts">${(a.facts || []).slice(0, 4).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
          ${miniMap(a, 342, 170)}
          ${recipientsLine(a)}
          ${act ? loggedLine(a, act) : ''}
        </div>
        ${decided ? '' : `<div class="al-ph-actions">${actionButtons(a, 'phone')}</div>`}
        <div class="al-ph-home is-dark"></div>
      </div>`;
  }

  function deliveryLog(a) {
    const rows = phoneState.deliveries;
    const email = (rows || []).find((r) => r.channel === 'email');
    const emailTo = email?.recipient || recipientsFor(a).find((r) => r.channel === 'email')?.name;
    return `
      <aside class="al-dl" aria-label="Delivery log">
        <div class="al-dl-head"><span class="al-dl-title">Delivery log</span><span class="al-dl-sim">Simulated delivery</span></div>
        <ul class="al-dl-list">
          ${rows ? rows.map((r) => `
            <li class="al-dl-row">
              <i data-lucide="${CHANNEL_ICON[r.channel] || 'bell'}"></i>
              <span class="al-dl-who"><span class="al-dl-name">${esc(r.recipient)}</span><span class="al-dl-ch">${esc(CHANNEL_LABEL[r.channel] || r.channel)}</span></span>
              <span class="al-dl-status"><i data-lucide="check"></i>${r.status && r.status !== 'delivered' ? esc(r.status) : 'Delivered'} · ${esc(fmtTime(r.t))}</span>
            </li>`).join('') : '<li class="al-dl-row is-loading">Loading deliveries…</li>'}
        </ul>
        ${emailTo ? `
        <div class="al-email" aria-label="Email preview">
          <div class="al-email-meta">
            <div><span class="al-email-k">From</span>Skynet alerts &lt;alerts@skynet.example&gt;</div>
            <div><span class="al-email-k">To</span>${esc(emailTo)}</div>
          </div>
          <div class="al-email-subj">${esc(email?.preview?.title || `[${SEV_LABEL[a.severity] || 'Alert'}] ${a.headline}`)}</div>
          <div class="al-email-body">
            <p>${esc(email?.preview?.body || a.headline)}</p>
            <ul>${(a.facts || []).slice(0, 4).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
            <span class="al-email-cta">Open record ${esc(a.vessel_id)}</span>
          </div>
        </div>` : ''}
      </aside>`;
  }

  function renderPhone() {
    if (!phoneState) return;
    const a = alertById(phoneState.alertId);
    if (!a) { closePhone(); return; }
    const sb = `<div class="al-ph-status ${phoneState.screen === 'lock' ? 'is-light' : ''}"><span>${esc(fmtTime(nowT()))}</span><span class="al-ph-notch"></span><span class="al-ph-sys"><i data-lucide="signal"></i><i data-lucide="wifi"></i><i data-lucide="battery-full"></i></span></div>`;
    phone.innerHTML = `
      <div class="al-ph-backdrop" data-ph="close"></div>
      <div class="al-ph-stage" role="dialog" aria-modal="true" aria-label="What the recipient sees">
        <div class="al-ph-col">
          <div class="al-ph-cap"><span>What the recipient sees</span><button type="button" class="al-ph-close" data-ph="close" aria-label="Close phone preview"><i data-lucide="x"></i></button></div>
          <div class="al-ph-frame"><div class="al-ph-screen ${phoneState.screen === 'lock' ? 'is-lock' : 'is-detail'}">${sb}${phoneState.screen === 'lock' ? phoneLock(a) : phoneDetail(a)}</div></div>
        </div>
        ${deliveryLog(a)}
      </div>`;
    icons();
  }

  phone.addEventListener('click', (ev) => {
    const t = ev.target.closest('[data-ph]');
    if (!t) return;
    const k = t.dataset.ph;
    if (k === 'close') closePhone();
    else if (k === 'open' && phoneState) { phoneState.screen = 'detail'; renderPhone(); }
    else if (k === 'back' && phoneState) { phoneState.screen = 'lock'; renderPhone(); }
  });
  document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && phoneState) closePhone(); });

  // ---- layout: sit below the case banner when it would overlap the feed --
  function layout() {
    const b = document.getElementById('case-banner');
    let top = '';
    if (b && !b.hidden) {
      const r = b.getBoundingClientRect();
      if (r.height && r.right > window.innerWidth - 16 - 380 - 8) top = `${Math.round(r.bottom + 12)}px`;
    }
    if (root.style.getPropertyValue('--al-top') !== top) root.style.setProperty('--al-top', top || null);
  }
  const banner = document.getElementById('case-banner');
  if (banner) new MutationObserver(layout).observe(banner, { attributes: true, childList: true, subtree: true });
  window.addEventListener('resize', layout);

  // ---- mode -------------------------------------------------------------
  function applyMode(m) {
    mode = m || mode;
    root.classList.toggle('is-alert-mode', mode === 'alert');
    feed.hidden = mode !== 'alert';
    if (mode === 'alert') hideNotify();
  }

  // ---- wire up ------------------------------------------------------------
  on('mode', (d) => applyMode(d?.mode ?? d));
  on('world', () => onWorld());
  on('case', (d) => { if (d?.id) caseId = d.id; onWorld(); });
  on('tick', () => onTick());
  if (!ctx.bus?.on) { try { ctx.clock?.on?.(() => onTick()); } catch {} }

  applyMode(ctx.mode || 'detect');
  layout();
  onWorld();
  onTick();

  // test/demo hook
  window.__skynetAlert = { openPhone, fire: (id) => { const a = alertById(id); if (a) { seen.delete(id); fire([a]); } } };
}
