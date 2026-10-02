/* "Connect Garmin": the direct connection, in onboarding and in Settings.
   Two steps when Garmin asks for a code; afterwards a status panel with
   Sync now, full history and Disconnect. */
import { del, post } from '../core/api.js';
import { t } from '../core/i18n.js';
import { refreshStatus, state } from '../core/state.js';
import { $, agoFromEpoch, busy, confirmDialog, esc, toast } from '../core/ui.js';

function statusLine(info) {
  if (info.running) return `<span class="badge">${esc(t('garmin.syncing'))}</span>`;
  if (info.needs_login) return `<span class="badge bad">${esc(t('garmin.signAgain'))}</span>`;
  if (info.last_error) return `<span class="badge warn">${esc(t('garmin.problem'))}</span>`;
  return `<span class="badge ok">${esc(t('garmin.connected'))}</span>`;
}

function resultLine(info) {
  const r = info.last_result;
  if (!r) return info.running ? t('garmin.fetching') : t('garmin.noSync');
  const parts = [t('garmin.activities', { n: r.activities }), t('garmin.days', { n: r.days })];
  if (r.history && (r.history.activities || r.history.days)) {
    parts.push(t('garmin.older', { n: r.history.activities + r.history.days }));
  }
  if (r.written) parts.push(t('garmin.written', { n: r.written }));
  return t('garmin.lastSync', { when: agoFromEpoch(info.last_sync), parts: parts.join(', ') });
}

function loginForm(info, message = '') {
  return `<form class="connect-form" data-login>
    ${message ? `<p class="error">${esc(message)}</p>` : ''}
    <label class="field"><span>${esc(t('garmin.email'))}</span>
      <input type="email" name="email" autocomplete="username" required value="${esc(info.garmin_email || '')}"></label>
    <label class="field"><span>${esc(t('garmin.password'))}</span>
      <input type="password" name="password" autocomplete="current-password" required></label>
    <button class="primary" type="submit">${esc(t('garmin.connect'))}</button>
    <p class="privacy-note">${esc(t('garmin.privacy'))}</p>
  </form>`;
}

function mfaForm() {
  return `<form class="connect-form" data-mfa>
    <p>${esc(t('garmin.mfa'))}</p>
    <label class="field"><span>${esc(t('garmin.code'))}</span>
      <input name="code" inputmode="numeric" autocomplete="one-time-code" required></label>
    <div class="row-actions">
      <button class="primary inline" type="submit">${esc(t('garmin.verify'))}</button>
      <button class="ghost" type="button" data-restart>${esc(t('garmin.restart'))}</button>
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
    host.innerHTML = `<p class="muted">${esc(t('garmin.unavailable'))}</p>`;
    return;
  }

  const showLogin = (message = '') => {
    host.innerHTML = loginForm(info, message);
    $('[data-login]', host).addEventListener('submit', (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = $('button[type="submit"]', form);
      busy(button, t('garmin.signing'), async () => {
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
      busy($('button[type="submit"]', form), t('garmin.verifying'), async () => {
        await post('/api/garmin/mfa', { challenge_id: challenge, code: form.code.value });
        await done();
      });
    });
  };

  const done = async () => {
    toast(t('garmin.connectedToast'));
    await refreshStatus();
    onChange();
  };

  if (!info.connected) {
    showLogin();
    return;
  }
  if (info.needs_login) {
    showLogin(info.last_error || t('garmin.askAgain'));
    return;
  }

  host.innerHTML = `
    <div class="card-head">${statusLine(info)}<span class="muted">${esc(info.garmin_email)}</span></div>
    <p class="muted">${resultLine(info)}</p>
    ${info.last_error ? `<p class="error">${esc(info.last_error)}</p>` : ''}
    ${info.history_done ? '' : `<p class="muted">${esc(t('garmin.historyBg'))}</p>`}
    <div class="row-actions">
      <button type="button" class="primary inline" data-sync ${info.running ? 'disabled' : ''}>${esc(t('garmin.syncNow'))}</button>
      ${info.history_done ? '' : `<button type="button" class="ghost" data-history ${info.running ? 'disabled' : ''}>${esc(t('garmin.loadHistory'))}</button>`}
      <button type="button" class="ghost danger-button" data-disconnect>${esc(t('garmin.disconnect'))}</button>
    </div>`;

  const kick = (history) => async () => {
    await post('/api/garmin/sync', { history });
    toast(history ? t('garmin.loadingHistory') : t('garmin.syncingToast'));
    await refreshStatus();
    onChange();
  };
  $('[data-sync]', host).addEventListener('click', (event) => busy(event.currentTarget, t('garmin.starting'), kick(false)));
  const history = $('[data-history]', host);
  if (history) history.addEventListener('click', (event) => busy(event.currentTarget, t('garmin.starting'), kick(true)));
  $('[data-disconnect]', host).addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: t('garmin.disconnectTitle'),
      body: t('garmin.disconnectBody'),
      confirm: t('garmin.disconnect'),
      danger: true,
    });
    if (!ok) return;
    await del('/api/garmin/connect');
    toast(t('garmin.disconnected'));
    await refreshStatus();
    onChange();
  });
}
