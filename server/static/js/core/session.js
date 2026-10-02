/* Leave the signed-in app and return to the public landing page. */

export function finishSignOut() {
  // replaceState does not fire hashchange, so the router cannot send us to Today.
  history.replaceState(null, '', `${location.pathname}${location.search}`);
  document.dispatchEvent(new CustomEvent('app:signed-out'));
}

export async function signOut() {
  await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
  finishSignOut();
}
