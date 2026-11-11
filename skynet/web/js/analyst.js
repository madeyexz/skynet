// Skynet · Analyst ask bar (top bar) + answer card (#analyst-panel). Owner: I.
// POST /api/analyst {question, t, case} -> Answer (see CONTRACT "Analyst answer").

const SUGGESTIONS = {
  cable: [
    'Which ships are within 2 km of Cable A right now?',
    'Where was D-04 on Tuesday?',
    'Should we quote tanker T-11 for a Hormuz transit tomorrow?',
  ],
  'war-risk': [
    'Should we quote tanker T-11 for a Hormuz transit tomorrow?',
    'What is the risk on T-11?',
    'Which vessel is the riskiest right now?',
  ],
  default: [
    'Which vessel is the riskiest right now?',
    'Which vessels are dark right now?',
    'What happened in zone Z-1?',
  ],
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icons = () => window.lucide?.createIcons();

export function init(ctx) {
  const mount = document.getElementById('ask-mount');
  const panel = document.getElementById('analyst-panel');
  if (!mount || !panel) return;

  mount.innerHTML = `
    <form class="an-ask" role="search" autocomplete="off">
      <label class="an-field">
        <span class="sr-only">Ask Skynet</span>
        <i data-lucide="search" aria-hidden="true"></i>
        <input class="an-input" type="search" name="q" enterkeyhint="search"
          placeholder="Ask Skynet… e.g. Which ships are within 2 km of Cable A right now?"
          aria-controls="an-suggest" aria-expanded="false" aria-autocomplete="list">
      </label>
      <div class="an-suggest" id="an-suggest" role="listbox" aria-label="Suggested questions" hidden></div>
    </form>`;
  const form = mount.querySelector('.an-ask');
  const input = mount.querySelector('.an-input');
  const suggest = mount.querySelector('.an-suggest');
  icons();

  let seq = 0;
  let lastFocus = null;

  // ---------- suggestions ----------
  function suggestions() {
    return SUGGESTIONS[ctx.case] || (ctx.world?.assets?.length ? SUGGESTIONS.cable : SUGGESTIONS.default);
  }
  function openSuggest() {
    suggest.innerHTML = `<div class="an-suggest-title">Try asking</div>` + suggestions().map((q, i) =>
      `<button type="button" class="an-sugg" role="option" data-i="${i}" aria-selected="false"><i data-lucide="corner-down-right" aria-hidden="true"></i><span>${esc(q)}</span></button>`).join('');
    suggest.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    icons();
  }
  function closeSuggest() { suggest.hidden = true; input.setAttribute('aria-expanded', 'false'); }

  input.addEventListener('focus', () => { if (!input.value.trim()) openSuggest(); });
  input.addEventListener('input', () => (input.value.trim() ? closeSuggest() : openSuggest()));
  form.addEventListener('focusout', (e) => { if (!form.contains(e.relatedTarget)) closeSuggest(); });
  suggest.addEventListener('mousedown', (e) => e.preventDefault()); // keep focus in the input on click
  suggest.addEventListener('click', (e) => {
    const b = e.target.closest('.an-sugg'); if (!b) return;
    input.value = suggestions()[+b.dataset.i];
    closeSuggest();
    ask(input.value);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && !suggest.hidden) { e.preventDefault(); suggest.querySelector('.an-sugg')?.focus(); }
    if (e.key === 'Escape') {
      if (!suggest.hidden) { closeSuggest(); e.stopPropagation(); }
      else if (!panel.hidden) { close(); e.stopPropagation(); }
    }
  });
  suggest.addEventListener('keydown', (e) => {
    const items = [...suggest.querySelectorAll('.an-sugg')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(items.length - 1, i + 1)]?.focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); (i <= 0 ? input : items[i - 1]).focus(); }
    if (e.key === 'Escape') { e.preventDefault(); closeSuggest(); input.focus(); }
  });
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) { openSuggest(); return; }
    closeSuggest();
    ask(q);
  });

  // ---------- answer card ----------
  panel.hidden = true;
  panel.classList.add('an-root');

  function close() {
    seq++;
    panel.hidden = true;
    panel.innerHTML = '';
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || panel.hidden) return;
    if (e.target?.closest?.('input, textarea, select') && e.target !== input) return;
    close();
  });
  ctx.bus.on('case', close);

  function shell(question, body) {
    return `
      <article class="an-card card" role="dialog" aria-modal="false" aria-labelledby="an-title">
        <header class="an-head">
          <div class="an-head-text">
            <div class="an-kicker" id="an-title"><i data-lucide="sparkles" aria-hidden="true"></i>Skynet Analyst</div>
            <div class="an-q">${esc(question)}</div>
          </div>
          <button type="button" class="an-close" aria-label="Close answer"><i data-lucide="x"></i></button>
        </header>
        ${body}
      </article>`;
  }
  function wire() {
    panel.querySelector('.an-close')?.addEventListener('click', close);
    icons();
  }

  function renderLoading(question) {
    panel.innerHTML = shell(question, `
      <div class="an-loading" role="status" aria-live="polite">
        <i data-lucide="loader-circle" class="an-spin" aria-hidden="true"></i>
        <span>Checking AIS, radar and RF records…</span>
      </div>`);
    panel.hidden = false;
    wire();
  }

  function renderError(question, err) {
    panel.innerHTML = shell(question, `
      <p class="an-verdict">The analyst is not reachable right now.</p>
      <p class="an-muted">${esc(err?.message || err)}. Nothing was answered, so nothing was guessed.</p>`);
    wire();
  }

  function riskBlock(risk) {
    if (!risk || risk.score == null) return '';
    const lvl = String(risk.level || '').toLowerCase();
    const chip = { high: 'red', medium: 'orange', low: 'green' }[lvl] || '';
    return `<div class="an-risk">
        <span class="an-risk-num">${esc(Math.round(risk.score))}</span>
        <span class="an-risk-of">/100 risk</span>
        ${risk.level ? `<span class="chip ${chip}">${esc(risk.label || lvl)}</span>` : ''}
      </div>`;
  }

  function actionButton(a, i) {
    const icon = a.type === 'report' ? 'file-text' : 'map-pin';
    const cls = i === 0 ? 'btn primary' : 'btn';
    return `<button type="button" class="${cls} an-act" data-i="${i}"><i data-lucide="${icon}" aria-hidden="true"></i>${esc(a.label)}</button>`;
  }

  function renderAnswer(ans) {
    const reasons = (ans.reasons || []).filter(Boolean);
    const sources = (ans.sources || []).filter((s) => s && s.label);
    const actions = (ans.actions || []).filter((a) => a && (a.type === 'report' ? a.vessel_id : true) &&
      !(a.type === 'show_on_map' && !(a.vessel_ids || []).length && !a.bbox));
    const calls = ans.tool_calls || [];
    const engine = String(ans.engine || '').startsWith('llm:') ? `LLM (${esc(ans.engine.slice(4))})` : 'Deterministic planner';
    panel.innerHTML = shell(ans.question, `
      ${ans.empty ? `<div class="an-empty"><i data-lucide="circle-slash" aria-hidden="true"></i>No matching data</div>` : ''}
      <p class="an-verdict">${esc(ans.verdict)}</p>
      ${riskBlock(ans.risk)}
      ${reasons.length ? `<ul class="an-reasons">${reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      ${actions.length ? `<div class="an-actions">${actions.map(actionButton).join('')}</div>` : ''}
      ${sources.length ? `<div class="an-sources"><span class="an-sources-label">Sources</span> ${sources.map((s) =>
        s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>` : `<span>${esc(s.label)}</span>`).join('<span aria-hidden="true"> · </span>')}</div>` : ''}
      <details class="an-trace">
        <summary><i data-lucide="chevron-right" aria-hidden="true"></i>How Skynet answered · ${calls.length} tool call${calls.length === 1 ? '' : 's'}</summary>
        <ol>${calls.map((c) => `<li><code>${esc(c.tool)}(${esc(fmtArgs(c.args))})</code><span>${esc(c.summary)}</span></li>`).join('')}</ol>
        <div class="an-engine">Engine: ${engine}</div>
      </details>`);
    panel.querySelectorAll('.an-act').forEach((b) => b.addEventListener('click', () => {
      const a = actions[+b.dataset.i];
      if (a.type === 'report') {
        ctx.bus.emit('open-report', { vessel_id: a.vessel_id, template: a.template || 'cable' });
      } else {
        const detail = {};
        if (a.vessel_ids?.length) detail.vessel_ids = a.vessel_ids;
        if (a.bbox) detail.bbox = a.bbox;
        if (a.t) detail.t = a.t;
        ctx.bus.emit('focus', detail);
      }
    }));
    wire();
  }

  function fmtArgs(args) {
    return Object.entries(args || {}).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
  }

  async function ask(question) {
    const my = ++seq;
    if (!panel.contains(document.activeElement)) lastFocus = document.activeElement;
    renderLoading(question);
    const body = { question, t: Math.round(ctx.clock?.t || 0) || null, case: ctx.case };
    try {
      const ans = ctx.api?.post
        ? await ctx.api.post('/api/analyst', body)
        : await fetch('/api/analyst', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))));
      if (my !== seq) return;
      renderAnswer({ ...ans, question: ans.question || question });
    } catch (e) {
      if (my !== seq) return;
      renderError(question, e);
    }
  }
}
