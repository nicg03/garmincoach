/* The pages behind links in account emails (#/verify, #/reset, #/email) and
   the forgot-password form. They work signed in or not, so they render in
   the gate's second card rather than inside the shell. */
import { state } from '../core/state.js';
import { $, esc, toast } from '../core/ui.js';

const LINK_ROUTES = ['verify', 'reset', 'email'];

export function isLinkRoute(segments) {
  return LINK_ROUTES.includes(segments[0]) && Boolean(segments[1]);
}

// Not the shared api(): a 401 here must not bounce to the sign-in form.
async function send(url, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.detail || 'Something went wrong.');
  return data;
}

function showPanel(html) {
  $('#shell').classList.add('hidden');
  $('#gate').classList.remove('hidden');
  $('#gate').classList.add('landing-auth');
  $('#gate-form').classList.add('hidden');
  const panel = $('#gate-panel');
  panel.innerHTML = html;
  panel.classList.remove('hidden');
  return panel;
}

export function closePanel() {
  $('#gate').classList.remove('landing-auth');
  $('#gate-panel').classList.add('hidden');
  $('#gate-form').classList.remove('hidden');
}

function result(title, text, button, onClick) {
  const panel = showPanel(`
    <h2>${esc(title)}</h2>
    <p class="gate-panel-text">${text}</p>
    <button class="primary" type="button" data-next>${esc(button)}</button>`);
  $('[data-next]', panel).addEventListener('click', onClick);
}

/** Handles a link from an email. `done` runs once the user moves on. */
export async function openLink([kind, token], { done }) {
  // The token leaves the address bar and the history straight away.
  history.replaceState(null, '', location.pathname + location.search);
  const moveOn = () => { closePanel(); done(); };

  if (kind === 'reset') {
    resetForm(token, moveOn);
    return;
  }
  showPanel(`<h2>${kind === 'verify' ? 'Confirming your email…' : 'Changing your email…'}</h2>`);
  try {
    if (kind === 'verify') {
      const body = await send('/api/email/verify', { token });
      result('Email confirmed', `<strong>${esc(body.email)}</strong> is confirmed. You can now reset
        your password with it if you ever need to.`, 'Continue', moveOn);
    } else {
      const body = await send('/api/email/change/confirm', { token });
      result('Email changed', `From now on you sign in with <strong>${esc(body.user.email)}</strong>.
        Other devices were signed out.`, 'Continue', moveOn);
    }
  } catch (error) {
    result('That link didn\'t work', esc(error.message), 'Continue', moveOn);
  }
}

function resetForm(token, moveOn) {
  const min = state.site.min_password || 8;
  const panel = showPanel(`
    <form novalidate data-reset>
      <h2>Choose a new password</h2>
      <p class="gate-panel-text">Every device signed in with the old one gets signed out.</p>
      <label for="reset-password">New password</label>
      <input type="password" id="reset-password" autocomplete="new-password" required>
      <p class="field-hint">At least ${min} characters.</p>
      <button class="primary" type="submit">Save password</button>
      <p class="error" role="alert" data-error></p>
    </form>`);
  const form = $('[data-reset]', panel);
  $('#reset-password', form).focus();
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('button[type="submit"]', form);
    const error = $('[data-error]', form);
    error.textContent = '';
    button.disabled = true;
    try {
      await send('/api/password/reset', { token, password: $('#reset-password', form).value });
      toast('Password changed. You\'re signed in.');
      moveOn();
    } catch (e) {
      error.textContent = e.message;
    } finally {
      button.disabled = false;
    }
  });
}

export function showForgot(email = '') {
  const panel = showPanel(`
    <form novalidate data-forgot>
      <h2>Reset your password</h2>
      <p class="gate-panel-text">We'll email you a link to choose a new one.</p>
      <label for="forgot-email">Email</label>
      <input type="email" id="forgot-email" autocomplete="email" required value="${esc(email)}">
      <button class="primary" type="submit">Send reset link</button>
      <p class="error" role="alert" data-error></p>
      <p class="gate-alt"><button type="button" class="link" data-back>Back to sign in</button></p>
    </form>`);
  const form = $('[data-forgot]', panel);
  $('#forgot-email', form).focus();
  $('[data-back]', form).addEventListener('click', closePanel);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const address = $('#forgot-email', form).value.trim();
    const error = $('[data-error]', form);
    if (!address) {
      error.textContent = 'Enter your email.';
      return;
    }
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    try {
      await send('/api/password/forgot', { email: address });
      const where = state.site.email ? ''
        : ' Email isn\'t set up on this server, so the link is in the server log.';
      result('Check your inbox', `If <strong>${esc(address)}</strong> has an account, a reset link
        is on its way. It works for one hour.${where}`, 'Back to sign in', closePanel);
    } catch (e) {
      error.textContent = e.message;
      button.disabled = false;
    }
  });
}
