// Fetch helpers. Every call carries the current use case as ?case=. Owner: lead.
let currentCase = new URLSearchParams(location.search).get('case') || 'cable';

export function setCase(id) { currentCase = id; }
export function getCase() { return currentCase; }

function withCase(path) {
  const u = new URL(path, location.origin);
  if (!u.searchParams.has('case')) u.searchParams.set('case', currentCase);
  return u.pathname + u.search;
}

async function req(method, path, body) {
  const r = await fetch(withCase(path), {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
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
