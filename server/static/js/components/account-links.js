/* The pages behind links in account emails (#/verify, #/reset, #/email) and
   the forgot-password form. They work signed in or not, so they render in
   the gate's second card rather than inside the shell. */
import { t } from '../core/i18n.js';
import { publicApi } from '../core/api.js';
import { state } from '../core/state.js';
import { $, esc, toast } from '../core/ui.js';

const LINK_ROUTES = ['verify', 'reset', 'email'];

export function isLinkRoute(segments) {
  return LINK_ROUTES.includes(segments[0]) && Boolean(segments[1]);
}

// Not the shared api(): a 401 here must not bounce to the sign-in form.
async function send(url, body) {
  return publicApi(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
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
  showPanel(`<h2>${esc(kind === 'verify' ? t('account.confirming') : t('account.changing'))}</h2>`);
  try {
    if (kind === 'verify') {
      const body = await send('/api/email/verify', { token });
      result(t('account.confirmed'), `<strong>${esc(body.email)}</strong> ${esc(t('account.confirmedBody'))}`, t('account.continue'), moveOn);
    } else {
      const body = await send('/api/email/change/confirm', { token });
      result(t('account.changed'), `${esc(t('account.changedBody'))} <strong>${esc(body.user.email)}</strong>.
        ${esc(t('account.changedAfter'))}`, t('account.continue'), moveOn);
    }
  } catch (error) {
    result(t('account.linkFailed'), esc(error.message), t('account.continue'), moveOn);
  }
}

function resetForm(token, moveOn) {
  const min = state.site.min_password || 8;
  const panel = showPanel(`
    <form novalidate data-reset>
      <h2>${esc(t('account.newPasswordTitle'))}</h2>
      <p class="gate-panel-text">${esc(t('account.newPasswordHelp'))}</p>
      <label for="reset-password">${esc(t('account.newPassword'))}</label>
      <input type="password" id="reset-password" autocomplete="new-password" required>
      <p class="field-hint">${esc(t('gate.lengthMin', { n: min }))}</p>
      <button class="primary" type="submit">${esc(t('account.savePassword'))}</button>
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
      toast(t('account.passwordIn'));
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
      <h2>${esc(t('account.resetTitle'))}</h2>
      <p class="gate-panel-text">${esc(t('account.resetHelp'))}</p>
      <label for="forgot-email">${esc(t('gate.email'))}</label>
      <input type="email" id="forgot-email" autocomplete="email" required value="${esc(email)}">
      <button class="primary" type="submit">${esc(t('account.sendReset'))}</button>
      <p class="error" role="alert" data-error></p>
      <p class="gate-alt"><button type="button" class="link" data-back>${esc(t('account.back'))}</button></p>
    </form>`);
  const form = $('[data-forgot]', panel);
  $('#forgot-email', form).focus();
  $('[data-back]', form).addEventListener('click', closePanel);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const address = $('#forgot-email', form).value.trim();
    const error = $('[data-error]', form);
    if (!address) {
      error.textContent = t('account.enterEmail');
      return;
    }
    const button = $('button[type="submit"]', form);
    button.disabled = true;
    try {
      await send('/api/password/forgot', { email: address });
      const where = state.site.email ? '' : ` ${esc(t('account.noMail'))}`;
      result(
        t('account.checkInbox'),
        `${t('account.resetSent', { email: `<strong>${esc(address)}</strong>` })}${where}`,
        t('account.back'),
        closePanel,
      );
    } catch (e) {
      error.textContent = e.message;
      button.disabled = false;
    }
  });
}
