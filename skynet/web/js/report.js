// Skynet · A4 evidence record (owner: E).
// /report.html?id=R-0001&case=cable[&print=1|&embed=1]
// /report.html?vessel=D-04&case=cable[&template=&from=&to=&requested_by=]  (builds one via POST /api/reports)

const q = new URLSearchParams(location.search);
const CASE = q.get('case') || 'cable';
const PRINT = q.get('print') === '1';
const EMBED = q.get('embed') === '1';
const paper = document.getElementById('rp-paper');
document.body.classList.toggle('is-print', PRINT);
document.body.classList.toggle('is-embed', EMBED);

const SRC = {
  ais: { label: 'AIS', color: '#1F6FEB' },
  sar: { label: 'Satellite radar', color: '#292929' },
  rf: { label: 'RF', color: '#1A7F37' },
  operator: { label: 'Operator action', color: '#5D5D5D' },
  fusion: { label: 'Fusion', color: '#D1242F' },
  registry: { label: 'Registry', color: '#5D5D5D' },
  sanctions: { label: 'Sanctions', color: '#D1242F' },
};
const BY_LABEL = Object.fromEntries(Object.values(SRC).map((s) => [s.label.toLowerCase(), s]));
const srcOf = (s) => SRC[String(s || '').toLowerCase()] || BY_LABEL[String(s || '').toLowerCase()] || { label: s || 'Fusion', color: '#5D5D5D' };
const TITLES = { cable: 'Cable incident record', sanctions: 'Vessel sanctions due-diligence', insurance: 'Insurance voyage history / claims reconstruction' };

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = (n) => String(n).padStart(2, '0');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const d8 = (t) => new Date(t * 1000);
const hm = (t) => `${pad(d8(t).getUTCHours())}:${pad(d8(t).getUTCMinutes())}`;
const day = (t) => `${d8(t).getUTCDate()} ${MONTHS[d8(t).getUTCMonth()]} ${d8(t).getUTCFullYear()}`;
const dt = (t) => (t == null ? '—' : `${day(t)}, ${hm(t)} UTC`);
const humanType = (s) => (s || 'event').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const dur = (s) => { const m = Math.round(s / 60); if (m < 60) return `${m} min`; const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`; };

function ready() {
  window.__reportReady = true;
  document.body.setAttribute('data-ready', '1');
}

async function load() {
  const id = q.get('id');
  if (id) {
    const r = await fetch(`/api/reports/${encodeURIComponent(id)}?case=${encodeURIComponent(CASE)}`);
    if (!r.ok) throw new Error(`Report ${id} was not found (${r.status}).`);
    return r.json();
  }
  const vessel = q.get('vessel');
  if (!vessel) throw new Error('No report id or vessel in the address.');
  const num = (k) => (q.get(k) && Number.isFinite(+q.get(k)) ? +q.get(k) : undefined);
  const body = { vessel_id: vessel, template: q.get('template') || 'cable', from: num('from'), to: num('to'), requested_by: q.get('requested_by') || 'Skynet operator' };
  const r = await fetch(`/api/reports?case=${encodeURIComponent(CASE)}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`Could not build a report for ${vessel} (${r.status}).`);
  const rep = await r.json();
  if (rep?.id) {
    const u = new URL(location.href);
    ['vessel', 'template', 'from', 'to', 'requested_by'].forEach((k) => u.searchParams.delete(k));
    u.searchParams.set('id', rep.id); u.searchParams.set('case', CASE);
    history.replaceState(null, '', u);
  }
  return rep;
}

// ---------- deterministic noise ----------
function rng(seedStr) {
  let h = 2166136261;
  for (const c of String(seedStr)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  return () => { h ^= h << 13; h ^= h >>> 17; h ^= h << 5; return ((h >>> 0) % 1e6) / 1e6; };
}

// ---------- exhibit 1: SAR chip ----------
function sarChipDataUrl(det, heading) {
  const N = 240, c = document.createElement('canvas'); c.width = c.height = N;
  const g = c.getContext('2d'), img = g.createImageData(N, N), R = rng(det.id || 'sar');
  const len = Math.max(40, Math.min(400, det.length_m || 180));
  const px = 4; // metres per pixel (chip ~ 960 m wide)
  const L = len / px / 2, W = Math.max(2.2, len / 7 / px / 2);
  const a = ((heading ?? 35) - 90) * Math.PI / 180, ca = Math.cos(a), sa = Math.sin(a);
  // low-frequency sea clutter
  const waves = Array.from({ length: 5 }, () => ({ kx: (R() - 0.5) * 0.18, ky: (R() - 0.5) * 0.18, p: R() * 6.28 }));
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const dx = x - N / 2, dy = y - N / 2;
    const u = dx * ca + dy * sa, v = -dx * sa + dy * ca;
    let clutter = 0;
    for (const w of waves) clutter += Math.sin(x * w.kx + y * w.ky + w.p);
    const speckle = -Math.log(1 - R() * 0.999); // exponential (single-look intensity)
    let val = 26 + clutter * 3 + speckle * 22;
    // hull: bright elongated blob with a few strong scatterers
    const e = (u / L) ** 2 + (v / W) ** 2;
    if (e < 2.2) val += Math.exp(-e * 1.6) * (150 + speckle * 40);
    // faint wake behind the hull
    if (u < -L && u > -L * 4 && Math.abs(v) < 1 + (-u - L) * 0.12) val += 10 * R();
    val = Math.max(0, Math.min(255, val));
    const i = (y * N + x) * 4;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = val; img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
  return { url: c.toDataURL('image/jpeg', 0.82), metres: N * px };
}

function radarExhibit(ex, n, rep) {
  const det = ex.detection || {};
  const cap = ex.caption || `Satellite radar detection ${det.id || ''}`;
  const meta = [det.sensor || 'Satellite radar', det.t != null ? dt(det.t) : null, det.length_m ? `length estimate ${det.length_m} m` : null,
    Number.isFinite(det.lon) ? `${det.lat.toFixed(4)}° N, ${det.lon.toFixed(4)}° E` : null].filter(Boolean).join(' · ');
  const realImg = det.image_url || ex.image_url || (typeof ex.image === 'string' ? ex.image : null);
  let figure;
  if (realImg) {
    figure = `<img class="rp-chip-img" src="${esc(realImg)}" alt="Satellite radar image chip of detection ${esc(det.id || '')}">`;
  } else {
    const track = rep.exhibits?.find((x) => x.kind === 'track');
    const heading = headingNear(track?.verified, det);
    const chip = sarChipDataUrl(det, heading);
    const S = 240, bar = 200 / (chip.metres / S); // 200 m bar in px
    figure = `<svg class="rp-chip" viewBox="0 0 ${S} ${S}" role="img" aria-label="Illustrative radar chip of detection ${esc(det.id || '')}">
      <image href="${chip.url}" x="0" y="0" width="${S}" height="${S}" preserveAspectRatio="none"/>
      <g fill="none" stroke="#FFFFFF" stroke-width="1.2" opacity=".85">
        <path d="M ${S / 2 - 34} ${S / 2 - 24} v -10 h 10 M ${S / 2 + 24} ${S / 2 - 34} h 10 v 10 M ${S / 2 + 34} ${S / 2 + 24} v 10 h -10 M ${S / 2 - 24} ${S / 2 + 34} h -10 v -10"/>
      </g>
      <g font-size="12" fill="#FFFFFF" font-family="inherit">
        <text x="10" y="20">${esc(det.id || 'SAR')}</text>
        <text x="${S - 10}" y="20" text-anchor="end">VV</text>
        <rect x="10" y="${S - 18}" width="${bar.toFixed(1)}" height="3" fill="#FFFFFF"/>
        <text x="10" y="${S - 24}">200 m</text>
      </g>
    </svg>`;
  }
  return `<figure class="rp-exhibit rp-ex-radar">
    <div class="rp-ex-media">${figure}</div>
    <figcaption><span class="rp-ex-n">Exhibit ${n} · Radar detection</span>
      <span class="rp-ex-cap">${esc(cap)}</span>
      <span class="rp-ex-meta">${esc(meta)}</span>
      ${realImg ? '' : '<span class="rp-ex-meta">Image chip is a rendering for illustration; the detection values above are from the record.</span>'}
    </figcaption>
  </figure>`;
}

function headingNear(verified, det) {
  if (!verified?.length || !Number.isFinite(det.lon)) return null;
  let bi = 0, bd = Infinity;
  verified.forEach((p, i) => { const d = (p[0] - det.lon) ** 2 + (p[1] - det.lat) ** 2; if (d < bd) { bd = d; bi = i; } });
  const a = verified[Math.max(0, bi - 2)], b = verified[Math.min(verified.length - 1, bi + 2)];
  if (!a || !b || (a[0] === b[0] && a[1] === b[1])) return null;
  const k = Math.cos(det.lat * Math.PI / 180);
  return (Math.atan2((b[0] - a[0]) * k, b[1] - a[1]) * 180 / Math.PI + 360) % 360;
}

// ---------- exhibit 2: reconstructed track ----------
function assetLines(g) {
  if (!g) return [];
  if (g.type === 'LineString') return [g.coordinates];
  if (g.type === 'MultiLineString') return g.coordinates;
  if (g.type === 'Feature') return assetLines(g.geometry);
  if (g.type === 'FeatureCollection') return g.features.flatMap((f) => assetLines(f.geometry));
  return [];
}
function splitJumps(pts) {
  if (pts.length < 3) return [pts];
  const steps = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1])).sort((a, b) => a - b);
  const med = steps[Math.floor(steps.length / 2)] || 0;
  const lim = Math.max(med * 8, 0.01);
  const segs = []; let cur = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    if (Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) > lim) { segs.push(cur); cur = []; }
    cur.push(pts[i]);
  }
  segs.push(cur);
  return segs.filter((s) => s.length > 1);
}
function niceKm(km) {
  const c = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  return c.reverse().find((x) => x <= km) || 0.1;
}
function trackExhibit(ex, n, rep) {
  const W = 680, H = 380, P = 24;
  const verified = (ex.verified || []).filter((p) => Number.isFinite(p[0]));
  const claimed = (ex.claimed || []).filter((p) => Number.isFinite(p[0]));
  const dark = (ex.dark || []).filter((s) => s.length > 1);
  const det = rep.exhibits?.find((x) => x.kind === 'radar')?.detection;
  const pts = [...verified, ...claimed];
  if (det && Number.isFinite(det.lon)) pts.push([det.lon, det.lat]);
  if (!pts.length) {
    return `<figure class="rp-exhibit"><figcaption><span class="rp-ex-n">Exhibit ${n} · Reconstructed track</span>
      <span class="rp-ex-cap">No track positions were recorded in this period.</span></figcaption></figure>`;
  }
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  const lat0 = (y0 + y1) / 2, k = Math.cos(lat0 * Math.PI / 180);
  let w = Math.max((x1 - x0) * k, 0.02), h = Math.max(y1 - y0, 0.012);
  // pad 12% and match the aspect ratio
  w *= 1.24; h *= 1.24;
  const ar = (W - 2 * P) / (H - 2 * P);
  if (w / h > ar) h = w / ar; else w = h * ar;
  const cx = (x0 + x1) / 2 * k, cy = (y0 + y1) / 2;
  const sx = (W - 2 * P) / w;
  const X = (lon) => P + (lon * k - (cx - w / 2)) * sx;
  const Y = (lat) => P + ((cy + h / 2) - lat) * sx;
  const path = (seg) => seg.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(' ');
  // scale bar
  const kmPerPx = (1 / sx) * 111.32; // projected units are latitude-degree equivalents
  const barKm = niceKm(kmPerPx * 140), barPx = barKm / kmPerPx;
  // grid every nice degree step
  const gstep = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5].find((s) => h / s <= 6) || 5;
  const grid = [];
  const latMin = cy - h / 2, latMax = cy + h / 2, lonMin = (cx - w / 2) / k, lonMax = (cx + w / 2) / k;
  for (let la = Math.ceil(latMin / gstep) * gstep; la <= latMax; la += gstep) grid.push(`<path d="M${P} ${Y(la).toFixed(1)} H${W - P}"/>`);
  const gstepX = k < 0.7 ? gstep * 2 : gstep;
  for (let lo = Math.ceil(lonMin / gstepX) * gstepX; lo <= lonMax; lo += gstepX) grid.push(`<path d="M${X(lo).toFixed(1)} ${P} V${H - P}"/>`);
  const assets = assetLines(ex.asset).map((l) => `<path d="${path(l)}"/>`).join('');
  const first = verified[0] || claimed[0], last = verified[verified.length - 1] || claimed[claimed.length - 1];
  const darkLabel = dark.length ? (() => { const s = dark[0][Math.floor(dark[0].length / 2)]; return `<text x="${(X(s[0]) + 10).toFixed(1)}" y="${(Y(s[1]) - 10).toFixed(1)}" fill="#292929">AIS off</text>`; })() : '';
  const assetLabel = ex.asset_name && assetLines(ex.asset).length ? (() => {
    // put the label at the first asset vertex inside the frame
    for (const l of assetLines(ex.asset)) for (const p of l) { const x = X(p[0]), y = Y(p[1]); if (x > P + 30 && x < W - P - 90 && y > P + 20 && y < H - P - 30) return `<text x="${(x + 6).toFixed(1)}" y="${(y + 16).toFixed(1)}" fill="#5D5D5D">${esc(ex.asset_name)}</text>`; }
    return '';
  })() : '';
  const svg = `<svg class="rp-map" viewBox="0 0 ${W} ${H}" role="img" aria-label="Reconstructed track map">
    <defs><clipPath id="rp-clip"><rect x="${P}" y="${P}" width="${W - 2 * P}" height="${H - 2 * P}"/></clipPath></defs>
    <rect x="${P}" y="${P}" width="${W - 2 * P}" height="${H - 2 * P}" fill="#F7F9FB" stroke="#EAEAEA"/>
    <g clip-path="url(#rp-clip)">
      <g stroke="#EAEAEA" stroke-width="1">${grid.join('')}</g>
      <g fill="none" stroke="#9E9E9E" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">${assets}</g>
      <g fill="none" stroke="#6E85B7" stroke-width="1.6" stroke-dasharray="5 4">${splitJumps(claimed).map((s) => `<path d="${path(s)}"/>`).join('')}</g>
      <path d="${path(verified)}" fill="none" stroke="#292929" stroke-width="1.8" stroke-linejoin="round"/>
      <g fill="none" stroke="#E8590C" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">${dark.map((s) => `<path d="${path(s)}"/>`).join('')}</g>
      ${det && Number.isFinite(det.lon) ? `<rect x="${(X(det.lon) - 5).toFixed(1)}" y="${(Y(det.lat) - 5).toFixed(1)}" width="10" height="10" fill="#E8590C" stroke="#FFFFFF" stroke-width="1.5" transform="rotate(45 ${X(det.lon).toFixed(1)} ${Y(det.lat).toFixed(1)})"/>` : ''}
      ${first ? `<circle cx="${X(first[0]).toFixed(1)}" cy="${Y(first[1]).toFixed(1)}" r="4" fill="#FFFFFF" stroke="#292929" stroke-width="1.5"/>` : ''}
      ${last ? `<circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="4" fill="#292929"/>` : ''}
      <g font-size="12">${darkLabel}${assetLabel}</g>
    </g>
    <g font-size="12" fill="#5D5D5D">
      <rect x="${P + 12}" y="${H - P - 20}" width="${barPx.toFixed(1)}" height="3" fill="#292929"/>
      <text x="${P + 12}" y="${H - P - 26}">${barKm} km</text>
      <text x="${W - P - 12}" y="${P + 22}" text-anchor="end" fill="#292929">N ↑</text>
    </g>
  </svg>`;
  const legend = `<ul class="rp-legend">
    <li><span class="rp-key rp-key-claimed"></span>Claimed (AIS)</li>
    <li><span class="rp-key rp-key-verified"></span>Verified (fused)</li>
    ${dark.length ? '<li><span class="rp-key rp-key-dark"></span>Dark, AIS off</li>' : ''}
    ${ex.asset ? `<li><span class="rp-key rp-key-asset"></span>${esc(ex.asset_name || 'Asset')}</li>` : ''}
    ${det ? '<li><span class="rp-key rp-key-sar"></span>Radar detection</li>' : ''}
    <li><span class="rp-key rp-key-start"></span>Start</li><li><span class="rp-key rp-key-end"></span>End</li>
  </ul>`;
  return `<figure class="rp-exhibit rp-ex-track">
    <div class="rp-ex-media">${svg}</div>
    <figcaption><span class="rp-ex-n">Exhibit ${n} · Reconstructed track</span>
      <span class="rp-ex-cap">${esc(ex.caption || '')}</span>${legend}</figcaption>
  </figure>`;
}

// ---------- render ----------
function render(rep) {
  const v = rep.vessel || {};
  const sc = rep.scenario || {};
  const illustrative = sc.illustrative !== false && v.illustrative !== false;
  const title = rep.title || TITLES[rep.template] || 'Evidence record';
  const period = rep.period || {};
  document.title = rep.id || 'Skynet evidence record';
  document.querySelector('.rp-bar-id').textContent = `${rep.id} · ${title}`;
  const pdf = document.getElementById('rp-pdf');
  pdf.href = `/api/reports/${encodeURIComponent(rep.id)}.pdf?case=${encodeURIComponent(rep.case || CASE)}`;
  pdf.setAttribute('download', `${rep.id}.pdf`);

  const risk = rep.risk;
  const identity = [
    ['Name', v.name], ['Skynet id', v.id], ['MMSI', v.mmsi], ['IMO', v.imo], ['Flag', v.flag],
    ['Type', v.type ? humanType(v.type) : null], ['Length', v.length_m ? `${v.length_m} m` : null],
  ].filter(([, x]) => x != null && x !== '');
  const recordRows = [
    ['Period covered', `${period.from != null && period.to != null && day(period.from) === day(period.to)
      ? `${day(period.from)}, ${hm(period.from)}–${hm(period.to)} UTC` : `${dt(period.from)} to ${dt(period.to)}`}${period.from != null && period.to != null ? ` (${dur(period.to - period.from)})` : ''}`],
    ['Requested by', rep.requested_by || '—'],
    ['Generated', dt(rep.generated)],
    ['Use case', sc.label || rep.case || CASE],
  ];
  const kv = (rows) => `<table class="rp-kv"><tbody>${rows.map(([k, x]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(x)}</td></tr>`).join('')}</tbody></table>`;

  const sections = (rep.sections || []).filter((s) => s.rows?.length).map((s) => `<section class="rp-sec">
    <h2>${esc(s.title)}</h2>${kv(s.rows.map((r) => [r.label, r.value]))}</section>`).join('');

  const riskHtml = risk ? `<section class="rp-sec rp-risk">
    <h2>Risk score</h2>
    <p class="rp-risk-line"><span class="rp-risk-score">${esc(risk.score)}</span><span class="rp-risk-of">/ 100</span>
      <span class="rp-level" data-level="${esc(risk.level || '')}">${esc(humanType(risk.level || ''))}</span></p>
    ${risk.reasons?.length ? `<table class="rp-table rp-reasons"><thead><tr><th>Reason</th><th>Source</th><th class="rp-num">Points</th></tr></thead><tbody>
      ${risk.reasons.map((r) => `<tr><td>${esc(r.text)}</td><td>${esc(srcOf(r.source).label)}</td><td class="rp-num">${r.points != null ? '+' + esc(r.points) : ''}</td></tr>`).join('')}
    </tbody></table>` : ''}
  </section>` : '';

  const tl = rep.timeline || [];
  const multiDay = tl.length && day(tl[0].t) !== day(tl[tl.length - 1].t);
  const timeline = `<section class="rp-sec rp-tl">
    <h2>Timeline</h2>
    ${tl.length ? `<table class="rp-table"><thead><tr><th class="rp-t">Time (UTC)</th><th class="rp-s">Source</th><th class="rp-e">Event</th><th>Detail</th></tr></thead><tbody>
    ${tl.map((r) => { const s = srcOf(r.source); return `<tr${r.severity === 'high' ? ' class="is-high"' : ''}>
      <td class="rp-t">${multiDay ? `${d8(r.t).getUTCDate()} ${MONTHS[d8(r.t).getUTCMonth()]} ` : ''}${hm(r.t)}</td>
      <td class="rp-s"><span class="rp-dot" style="background:${s.color}"></span>${esc(s.label)}</td>
      <td class="rp-e">${esc(humanType(r.type))}</td><td>${esc(r.text)}</td></tr>`; }).join('')}
    </tbody></table>` : '<p class="rp-muted">No events were recorded for this vessel in the period.</p>'}
  </section>`;

  let n = 0;
  const exhibits = (rep.exhibits || []).map((ex) => {
    if (ex.kind === 'radar') return radarExhibit(ex, ++n, rep);
    if (ex.kind === 'track') return trackExhibit(ex, ++n, rep);
    return '';
  }).join('');

  const sources = (sc.sources || []);
  const provenance = illustrative
    ? '<p class="rp-label">Illustrative reconstruction · all names, times and figures are illustrative</p>'
    : `<p class="rp-label">Data sources: ${sources.length ? sources.map((s) => s.url ? `<a href="${esc(s.url)}">${esc(s.label)}</a>` : esc(s.label)).join(' · ') : esc(sc.label || 'see record')}</p>`;
  const recordUrl = rep.record_url ? new URL(rep.record_url, location.origin).href : '';

  paper.innerHTML = `
    <header class="rp-head">
      <div class="rp-brand"><span class="rp-logo" aria-hidden="true"></span><span class="rp-word">Skynet</span></div>
      <div class="rp-head-r"><span>Evidence record</span><span class="rp-id">${esc(rep.id)}</span></div>
    </header>
    <h1>${esc(title)}</h1>
    <p class="rp-sub">${esc([v.name, v.id].filter(Boolean).join(' · '))}${period.from != null ? ` · ${esc(day(period.from))}` : ''}</p>
    <section class="rp-sec rp-summary"><h2>Summary</h2><p>${esc(rep.summary || '')}</p></section>
    <div class="rp-two">
      <section class="rp-sec"><h2>Vessel identity</h2>${kv(identity)}</section>
      <section class="rp-sec"><h2>Record</h2>${kv(recordRows)}</section>
    </div>
    ${riskHtml}
    ${sections}
    ${timeline}
    <section class="rp-sec rp-exhibits"><h2>Exhibits</h2>${exhibits || '<p class="rp-muted">No exhibits.</p>'}</section>
    <footer class="rp-foot">
      <table class="rp-kv rp-kv-mono"><tbody>
        <tr><th scope="row">Record fingerprint</th><td>${esc(rep.fingerprint || '—')}</td></tr>
        <tr><th scope="row">Record URL</th><td>${recordUrl ? `<a href="${esc(recordUrl)}">${esc(rep.record_url)}</a>` : '—'}</td></tr>
      </tbody></table>
      <p class="rp-muted">The fingerprint is the SHA-256 of the record's event list (canonical JSON). Fetch the record URL with an API key to verify that this document matches the stored record.</p>
      ${provenance}
    </footer>`;

  // running footer on every printed page (Chrome @page margin boxes)
  const cssStr = (s) => `"${String(s).replace(/["\\]/g, '')}"`;
  const st = document.createElement('style');
  st.textContent = `@page { @bottom-left { content: ${cssStr(`Skynet · ${rep.id} · ${(rep.fingerprint || '').slice(0, 23)}…`)}; } }`;
  document.head.appendChild(st);

  const imgs = [...paper.querySelectorAll('img')];
  return Promise.all(imgs.map((im) => (im.complete ? null : new Promise((r) => { im.onload = im.onerror = r; setTimeout(r, 8000); }))));
}

function renderError(err) {
  document.title = 'Skynet evidence record';
  paper.innerHTML = `<header class="rp-head"><div class="rp-brand"><span class="rp-logo" aria-hidden="true"></span><span class="rp-word">Skynet</span></div>
    <div class="rp-head-r"><span>Evidence record</span></div></header>
    <h1>Record unavailable</h1><p class="rp-sub">${esc(err.message || err)}</p>`;
}

document.getElementById('rp-print').addEventListener('click', () => window.print());

load().then(render).catch(renderError).finally(() => {
  if (document.fonts?.ready) document.fonts.ready.then(ready); else ready();
});
