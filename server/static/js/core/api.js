/* Fetch wrappers. A 401 anywhere hands control back to the sign-in gate. */

let unauthorized = () => {};

export function onUnauthorized(handler) {
  unauthorized = handler;
}

export async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  if (response.status === 401) {
    unauthorized();
    throw new Error('Not signed in');
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || body.detail || response.statusText || 'Request failed');
  }
  return body;
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
