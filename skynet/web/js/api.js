// Fetch helpers. Every call carries the current use case as ?case=. Owner: lead.
let currentCase = new URLSearchParams(location.search).get('case') || 'cable';
const ADMIN_KEY_STORAGE = 'skynet_admin_key';

export function setCase(id) { currentCase = id; }
export function getCase() { return currentCase; }

function withCase(path) {
  const u = new URL(path, location.origin);
  if (!u.searchParams.has('case')) u.searchParams.set('case', currentCase);
  return u.pathname + u.search;
}

function adminKey() {
  try { return localStorage.getItem(ADMIN_KEY_STORAGE); } catch { return null; }
}

async function send(method, path, body) {
  const headers = body !== undefined ? { 'Content-Type': 'application/json' } : {};
  const key = method !== 'GET' ? adminKey() : null;
  if (key) headers['x-admin-key'] = key;
  const r = await fetch(withCase(path), {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { r, data };
}

async function req(method, path, body) {
  let { r, data } = await send(method, path, body);
  // Write routes on the hosted demo need the admin key: ask once, remember it, retry once.
  if (r.status === 401 && data && data.error === 'admin_key_required') {
    const key = window.prompt('This action needs the Skynet demo admin key.');
    if (!key || !key.trim()) {
      const err = new Error('Admin key required');
      err.status = 401; err.data = data;
      throw err;
    }
    try { localStorage.setItem(ADMIN_KEY_STORAGE, key.trim()); } catch { /* storage blocked */ }
    ({ r, data } = await send(method, path, body));
    if (r.status === 401 && data && data.error === 'admin_key_required') {
      try { localStorage.removeItem(ADMIN_KEY_STORAGE); } catch { /* storage blocked */ }
    }
  }
  if (!r.ok) {
    const err = new Error((data && (data.detail || data.error)) || `${method} ${path} → ${r.status}`);
    err.status = r.status; err.data = data;
    throw err;
  }
  return data;
}

export const api = {
  get: (p) => req('GET', p),
  post: (p, b = {}) => req('POST', p, b),
  put: (p, b = {}) => req('PUT', p, b),
  del: (p) => req('DELETE', p),
  url: withCase,
  get case() { return currentCase; },
};
