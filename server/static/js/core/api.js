/* Fetch wrappers. A 401 anywhere hands control back to the sign-in gate. */

let unauthorized = () => {};

export function onUnauthorized(handler) {
  unauthorized = handler;
}

export function nativeBridge() {
  return window.GepardNative || null;
}

export function isNative() {
  return Boolean(nativeBridge()?.isNative);
}

export function apiUrl(path) {
  const bridge = nativeBridge();
  return bridge && path.startsWith('/') ? bridge.apiBase + path : path;
}

export async function request(path, options = {}) {
  const bridge = nativeBridge();
  if (bridge) await bridge.ready;
  const headers = new Headers(options.headers || {});
  if (bridge) {
    headers.set('X-Gepard-Client', 'capacitor');
    const token = await bridge.getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
  }
  return fetch(apiUrl(path), {
    credentials: bridge ? 'omit' : 'same-origin',
    ...options,
    headers,
  });
}

export async function captureSession(body) {
  const bridge = nativeBridge();
  if (bridge && body && typeof body.session_token === 'string') {
    await bridge.setToken(body.session_token);
    delete body.session_token;
  }
  return body;
}

async function parse(response) {
  const body = await response.json().catch(() => ({}));
  await captureSession(body);
  if (!response.ok) {
    throw new Error(body.error || body.detail || response.statusText || 'Request failed');
  }
  return body;
}

export async function publicApi(path, options = {}) {
  return parse(await request(path, options));
}

export async function api(path, options = {}) {
  const response = await request(path, options);
  if (response.status === 401) {
    await nativeBridge()?.clearToken();
    unauthorized();
    throw new Error('Not signed in');
  }
  return parse(response);
}

function send(method, path, body) {
  return api(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

export const post = (path, body) => send('POST', path, body);
export const put = (path, body) => send('PUT', path, body);
export const patch = (path, body) => send('PATCH', path, body);
export const del = (path) => api(path, { method: 'DELETE' });

/** Adds `athlete_id` so a coach reads an athlete's data through the same routes. */
export function scoped(path, athleteId) {
  if (!athleteId) return path;
  return path + (path.includes('?') ? '&' : '?') + 'athlete_id=' + encodeURIComponent(athleteId);
}
