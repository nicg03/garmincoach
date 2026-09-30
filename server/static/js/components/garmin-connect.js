/* "Connect Garmin": the direct connection, in onboarding and in Settings.
   Two steps when Garmin asks for a code; afterwards a status panel with
   Sync now, full history and Disconnect. */
import { del, post } from '../core/api.js';
import { refreshStatus, state } from '../core/state.js';
import { $, agoFromEpoch, busy, confirmDialog, esc, toast } from '../core/ui.js';

const PRIVACY = `Your Garmin password goes to Garmin once and is never stored here.
  We keep encrypted access tokens, use them only to read your training data and
  write the workouts you schedule, and delete them when you disconnect.`;

function statusLine(info) {
  if (info.running) return '<span class="badge">Syncing now</span>';
  if (info.needs_login) return '<span class="badge bad">Sign in again</span>';
  if (info.last_error) return '<span class="badge warn">Last sync had a problem</span>';
  return '<span class="badge ok">Connected</span>';
}

function resultLine(info) {
  const r = info.last_result;
  if (!r) return info.running ? 'Fetching your recent weeks. This page fills in as it goes.' : 'No sync yet.';
  const parts = [`${r.activities} activities`, `${r.days} days`];
  if (r.history && (r.history.activities || r.history.days)) {
    parts.push(`${r.history.activities + r.history.days} older records`);
  }
  if (r.written) parts.push(`${r.written} workouts sent`);
  return `Last sync ${agoFromEpoch(info.last_sync)}: ${parts.join(', ')}.`;
}

function loginForm(info, message = '') {
  return `<form class="connect-form" data-login>
    ${message ? `<p class="error">${esc(message)}</p>` : ''}
    <label class="field"><span>Garmin email</span>
      <input type="email" name="email" autocomplete="username" required value="${esc(info.garmin_email || '')}"></label>
    <label class="field"><span>Garmin password</span>
      <input type="password" name="password" autocomplete="current-password" required></label>
    <button class="primary" type="submit">Connect Garmin</button>
    <p class="privacy-note">${PRIVACY}</p>
  </form>`;
}

function mfaForm() {
  return `<form class="connect-form" data-mfa>
    <p>Garmin sent you a verification code by email or text. Enter it to finish.</p>
    <label class="field"><span>Code</span>
      <input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
    <div class="row-actions">
      <button class="primary inline" type="submit">Verify</button>
      <button class="ghost" type="button" data-restart>Start again</button>
    </div>
  </form>`;
}

/**
 * Render into `host`. `onChange` runs after connecting, syncing or
 * disconnecting, so the page around it can refresh.
 */
export function renderGarmin(host, { onChange = () => {} } = {}) {
  const info = state.status.garmin || { available: false, connected: false };

  if (!info.available) {
    host.innerHTML = `<p class="muted">The direct Garmin connection isn't installed on this
      server. Use the browser extension or the export zip below.</p>`;
    return;
  }

  const showLogin = (message = '') => {
    host.innerHTML = loginForm(info, message);
    $('[data-login]', host).addEventListener('submit', (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = $('button[type="submit"]', form);
      busy(button, 'Signing in to Garmin…', async () => {
        const body = await post('/api/garmin/connect', {
          email: form.email.value, password: form.password.value,
        });
        if (body.status === 'mfa') {
          showMfa(body.challenge_id);
          return;
        }
        await done();
      });
    });
  };

  const showMfa = (challenge) => {
    host.innerHTML = mfaForm();
    const form = $('[data-mfa]', host);
    form.code.focus();
    $('[data-restart]', form).addEventListener('click', () => showLogin());
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', form), 'Verifying…', async () => {
        await post('/api/garmin/mfa', { challenge_id: challenge, code: form.code.value });
        await done();
      });
    });
  };

  const done = async () => {
    toast('Garmin connected. Your data is on its way.');
    await refreshStatus();
    onChange();
  };

  if (!info.connected) {
    showLogin();
    return;
  }
  if (info.needs_login) {
    showLogin(info.last_error || 'Garmin asks you to sign in again.');
    return;
  }

  host.innerHTML = `
    <div class="card-head">${statusLine(info)}<span class="muted">${esc(info.garmin_email)}</span></div>
    <p class="muted">${resultLine(info)}</p>
    ${info.last_error ? `<p class="error">${esc(info.last_error)}</p>` : ''}
    ${info.history_done ? '' : '<p class="muted">Older history loads a little at a time in the background.</p>'}
    <div class="row-actions">
      <button type="button" class="primary inline" data-sync ${info.running ? 'disabled' : ''}>Sync now</button>
      ${info.history_done ? '' : `<button type="button" class="ghost" data-history ${info.running ? 'disabled' : ''}>Load all history now</button>`}
      <button type="button" class="ghost danger-button" data-disconnect>Disconnect</button>
    </div>`;

  const kick = (history) => async () => {
    await post('/api/garmin/sync', { history });
    toast(history ? 'Loading your full history. This can take a few minutes.' : 'Syncing with Garmin…');
    await refreshStatus();
    onChange();
  };
  $('[data-sync]', host).addEventListener('click', (event) => busy(event.currentTarget, 'Starting…', kick(false)));
  const history = $('[data-history]', host);
  if (history) history.addEventListener('click', (event) => busy(event.currentTarget, 'Starting…', kick(true)));
  $('[data-disconnect]', host).addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: 'Disconnect Garmin?',
      body: 'The stored tokens are deleted and syncing stops. Data already here stays.',
      confirm: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    await del('/api/garmin/connect');
    toast('Garmin disconnected');
    await refreshStatus();
    onChange();
  });
}
