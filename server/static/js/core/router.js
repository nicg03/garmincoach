/* Hash router: `#/insights/recovery` -> ['insights', 'recovery']. Deep links,
   reloads and the back button all work because the hash is the state. */

let handler = () => {};

export function path() {
  return location.hash.replace(/^#\/?/, '').split('?')[0].split('/').filter(Boolean)
    .map(decodeURIComponent);
}

export function navigate(to, { replace = false } = {}) {
  const hash = to.startsWith('#') ? to : '#/' + to.replace(/^\//, '');
  if (location.hash === hash) {
    handler(path());
    return;
  }
  if (replace) {
    history.replaceState(null, '', hash);
    handler(path());
  } else {
    location.hash = hash;
  }
}

export function start(onRoute) {
  handler = onRoute;
  window.addEventListener('hashchange', () => handler(path()));
  handler(path());
}

export function rerender() {
  handler(path());
}
