/* Shared state and a per-session cache of API answers. Views read through
   `cached` so moving between tabs doesn't refetch; a sync or an edit clears
   the keys it made stale. */
import { api } from './api.js';

export const state = {
  site: {},
  status: {},
  range: '90',
  chat: [],
  history: [],
};

const cache = new Map();

export function cached(key, loader, { force = false } = {}) {
  if (!force && cache.has(key)) return cache.get(key);
  const promise = loader().catch((error) => {
    cache.delete(key);
    throw error;
  });
  cache.set(key, promise);
  return promise;
}

export function invalidate(...prefixes) {
  for (const key of [...cache.keys()]) {
    if (!prefixes.length || prefixes.some((p) => key.startsWith(p))) cache.delete(key);
  }
}

export const events = new EventTarget();

export function emit(name, detail) {
  events.dispatchEvent(new CustomEvent(name, { detail }));
}

export function role() {
  return (state.status.user && state.status.user.role) || 'athlete';
}

export function hasData(status = state.status) {
  return Boolean(status.days || status.activities);
}

export async function refreshStatus() {
  const previous = state.status;
  const status = await api('/api/status');
  state.status = status;
  const signature = (s) => `${s.days}-${s.activities}-${s.first}-${s.last}`;
  if (signature(previous) !== signature(status)) {
    invalidate('metrics', 'performance', 'plan', 'decide', 'activities');
    emit('data', status);
  }
  emit('status', status);
  return status;
}
