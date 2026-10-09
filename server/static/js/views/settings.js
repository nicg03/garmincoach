/* Settings: the athlete profile plans are built from, where data comes
   from, and the account itself. */
import { renderGarmin } from '../components/garmin-connect.js';
import { bindDropzone, dropzoneHtml, extensionHtml } from '../components/importers.js';
import { api, isNative, nativeBridge, post, put } from '../core/api.js';
import { getLocale, t, translatePhrase } from '../core/i18n.js';
import { finishSignOut, signOut } from '../core/session.js';
import { rerender } from '../core/router.js';
import { cached, invalidate, refreshStatus, state } from '../core/state.js';
import { $, $$, bindCopy, busy, confirmDialog, esc, loadingPage, subnav, toast } from '../core/ui.js';

function tabs() {
  return [['profile', t('settings.profile')], ['sources', t('settings.sources')], ['account', t('settings.account')]];
}
const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DEFAULT_MINUTES = { mon: 0, tue: 60, wed: 60, thu: 60, fri: 60, sat: 90, sun: 90 };

function languageCard() {
  const lang = getLocale();
  return `<div class="card">
    <div class="card-head"><h2>${esc(t('settings.language'))}</h2></div>
    <p class="muted">${esc(t('settings.languageHelp'))}</p>
    <div class="lang-switch" role="group" aria-label="${esc(t('settings.language'))}">
      <button type="button" data-lang="en" aria-pressed="${lang === 'en'}">English</button>
      <button type="button" data-lang="it" aria-pressed="${lang === 'it'}">Italiano</button>
    </div>
  </div>`;
}

export async function render(root, ctx = {}) {
  const wanted = (ctx.path || [])[0];
  const items = tabs();
  const sub = items.some(([key]) => key === wanted) ? wanted : 'profile';
  root.innerHTML = `${languageCard()}${subnav('#/settings', items, sub)}<div data-sub>${loadingPage()}</div>`;
  const host = $('[data-sub]', root);
  if (sub === 'sources') return sources(host);
  if (sub === 'account') return account(host);
  return profile(host);
}

function rows(pairs) {
  return `<div class="rows">${pairs.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join('')}</div>`;
}

// ---- profile ------------------------------------------------------------------
async function profile(host) {
  const athlete = (await cached('athlete', () => api('/api/athlete'))) || {};
  const avail = athlete.availability || {};
  host.innerHTML = `
    <form class="card" data-athlete>
      <div class="card-head"><h2>${esc(t('settings.availability'))}</h2></div>
      <p class="muted">${esc(t('settings.availabilityHelp'))}</p>
      <div class="form-grid days">${WEEKDAYS.map((key) => {
        const mins = avail[key] && avail[key].minutes != null ? avail[key].minutes : DEFAULT_MINUTES[key];
        return `<label class="field"><span>${esc(t('day.' + key))}</span>
          <input type="number" min="0" max="300" step="5" data-day="${key}" value="${mins}"></label>`;
      }).join('')}</div>
      <div class="card-head" style="margin-top:18px"><h2>${esc(t('settings.body'))}</h2></div>
      <p class="muted">${esc(t('settings.bodyHelp'))}</p>
      <div class="form-grid">
        <label class="field"><span>${esc(t('settings.hrMax'))}</span>
          <input type="number" name="hr_max" min="120" max="230" value="${esc(athlete.hr_max || '')}"></label>
        <label class="field"><span>${esc(t('settings.hrRest'))}</span>
          <input type="number" name="hr_rest" min="30" max="100" value="${esc(athlete.hr_rest || '')}"></label>
        <label class="field"><span>${esc(t('settings.weight'))}</span>
          <input type="number" name="weight_kg" min="30" max="200" step="0.1" value="${esc(athlete.weight_kg || '')}"></label>
      </div>
      <div class="row-actions"><button class="primary inline" type="submit">${esc(t('settings.saveProfile'))}</button></div>
    </form>`;
  const form = $('[data-athlete]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), t('settings.saving'), async () => {
      const availability = {};
      $$('[data-day]', form).forEach((input) => {
        availability[input.dataset.day] = { minutes: Number(input.value) || 0, sports: ['running'] };
      });
      await put('/api/athlete', {
        availability,
        hr_max: Number(form.hr_max.value) || null,
        hr_rest: Number(form.hr_rest.value) || null,
        weight_kg: Number(form.weight_kg.value) || null,
      });
      invalidate('athlete');
      toast(t('settings.saved'));
    });
  });
}

// ---- data sources -------------------------------------------------------------
async function sources(host) {
  const status = await refreshStatus();
  const user = status.user || {};
  const repo = status.repo || state.site.repo || '';
  const last = status.last_ingest;
  const native = isNative();
  const command = `python -m garmin_sync link --url ${nativeBridge()?.apiBase || location.origin}`;
  const none = esc(t('settings.noneYet'));
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('settings.garmin'))}</h2></div>
      <p class="muted">${esc(t('settings.garminHelp'))}</p>
      <div data-garmin></div>
    </div>

    <div class="card">
      <div class="card-head"><h2>${esc(t('settings.yourData'))}</h2></div>
      ${rows([
        [t('settings.recentDay'), status.last ? `${esc(status.last)} (${esc(translatePhrase(status.ago))})` : none],
        [t('settings.oldestDay'), esc(status.first || t('settings.noneYet'))],
        [t('settings.daysStored'), esc(status.days || 0)],
        [t('settings.activitiesStored'), esc(status.activities || 0)],
        [t('settings.lastUpload'), last ? `${last.source === 'garmin' ? esc(t('settings.garminDirect')) : esc(t('settings.extOrComputer'))}` +
          `${last.at ? ` · ${esc(last.at)}` : ''}` : none],
      ])}
    </div>

    <h2 class="section-title">${esc(t('settings.otherImport'))}</h2>
    <div class="${native ? '' : 'grid-2'}">
      ${native ? '' : `
      <div class="card">
        <div class="card-head"><h2>${esc(t('settings.extension'))}</h2></div>
        <p class="muted">${esc(t('settings.extensionHelp'))}</p>
        ${extensionHtml('settings')}
      </div>`}
      <div class="card">
        <div class="card-head"><h2>${esc(t('settings.exportZip'))}</h2></div>
        ${dropzoneHtml()}
      </div>
    </div>

    ${native ? '' : `
    <details class="card advanced">
      <summary>${esc(t('settings.advanced'))}</summary>
      <p class="muted">${esc(t('settings.advancedHelp'))}
        ${repo ? `<a href="${esc(repo)}" target="_blank" rel="noopener">${esc(t('settings.repo'))}</a>` : esc(t('settings.repo'))}.</p>
      <div class="cmd"><pre id="link-command">${esc(command)}</pre>
        <button type="button" class="ghost" data-copy="link-command">${esc(t('ui.copy'))}</button></div>
      <p class="muted">${esc(t('settings.syncToken'))}</p>
      <div class="token-row"><code class="token" id="sync-token">${esc(user.sync_token || '')}</code>
        <button type="button" class="ghost" data-copy="sync-token">${esc(t('ui.copy'))}</button>
        <button type="button" class="ghost" data-rotate>${esc(t('settings.newToken'))}</button></div>
    </details>`}`;

  renderGarmin($('[data-garmin]', host), { onChange: rerender });
  bindDropzone(host, rerender);
  if (!native) bindCopy(host);
  const rotate = $('[data-rotate]', host);
  if (rotate) rotate.addEventListener('click', async (event) => {
    const ok = await confirmDialog({
      title: t('settings.rotateTitle'),
      body: t('settings.rotateBody'),
      confirm: t('settings.rotateConfirm'),
      danger: true,
    });
    if (!ok) return;
    await busy(event.target, '…', async () => {
      const body = await post('/api/token/rotate');
      state.status.user.sync_token = body.sync_token;
      toast(t('settings.tokenReady'));
      rerender();
    });
  });
}

// ---- account --------------------------------------------------------------------
function account(host) {
  const status = state.status;
  const user = status.user || {};
  // Change password / email / verify: parked until a Resend domain exists.
  const emails = Boolean(state.site.email);
  const min = state.site.min_password || 8;
  const verified = user.email_verified ? esc(t('settings.yes'))
    : `${esc(t('settings.notYet'))} · <button type="button" class="link" data-verify>${esc(t('settings.sendLinkAgain'))}</button>`;
  const pairs = [
    [t('settings.email'), esc(user.email || '')],
    ...(emails ? [[t('settings.emailConfirmed'), verified]] : []),
    [t('settings.accountType'), user.role === 'coach' ? esc(t('settings.coach')) : esc(t('settings.athlete'))],
    [t('settings.memberSince'), esc(user.since || '')],
  ];
  if (status.coach_limit) {
    pairs.push([t('settings.questionsToday'), esc(t('settings.questionsOf', { used: status.coach_used, limit: status.coach_limit }))]);
  }
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('settings.account'))}</h2></div>
      ${rows(pairs)}
      <div class="row-actions" style="margin-top:14px">
        <button type="button" class="ghost" data-logout>${esc(t('settings.signOut'))}</button>
        ${emails ? `<button type="button" class="ghost" data-revoke>${esc(t('settings.signOutOthers'))}</button>` : ''}
      </div>
    </div>
    ${reminderCard()}
    ${emails ? `
    <div class="grid-2">
      <form class="card" data-password novalidate>
        <div class="card-head"><h2>${esc(t('settings.changePassword'))}</h2></div>
        <p class="muted">${esc(t('settings.changePasswordHelp'))}</p>
        <label class="field"><span>${esc(t('settings.currentPassword'))}</span>
          <input type="password" name="current" autocomplete="current-password" required></label>
        <label class="field"><span>${esc(t('settings.newPassword'))}</span>
          <input type="password" name="password" autocomplete="new-password" required></label>
        <p class="field-hint">${esc(t('settings.minChars', { n: min }))}</p>
        <div class="row-actions"><button class="primary inline" type="submit">${esc(t('settings.changePasswordBtn'))}</button></div>
      </form>
      <form class="card" data-email novalidate>
        <div class="card-head"><h2>${esc(t('settings.changeEmail'))}</h2></div>
        <p class="muted">${esc(t('settings.changeEmailHelp'))}</p>
        <label class="field"><span>${esc(t('settings.newEmail'))}</span>
          <input type="email" name="email" autocomplete="email" required></label>
        <label class="field"><span>${esc(t('settings.password'))}</span>
          <input type="password" name="password" autocomplete="current-password" required></label>
        <div class="row-actions"><button class="primary inline" type="submit">${esc(t('settings.sendConfirm'))}</button></div>
      </form>
    </div>` : ''}
    <div class="card danger">
      <div class="card-head"><h2>${esc(t('settings.delete'))}</h2></div>
      <p class="muted">${esc(t('settings.deleteHelp'))}</p>
      <button type="button" class="ghost danger-button" data-delete>${esc(t('settings.deleteBtn'))}</button>
    </div>`;

  bindReminder(host);
  $('[data-logout]', host).addEventListener('click', () => signOut());
  const resend = $('[data-verify]', host);
  if (resend) {
    resend.addEventListener('click', () => busy(resend, t('banner.sending'), async () => {
      await post('/api/email/verify/send');
      toast(t('settings.sentTo', { email: user.email }));
    }));
  }
  const revoke = $('[data-revoke]', host);
  if (revoke) {
    revoke.addEventListener('click', async (event) => {
      const ok = await confirmDialog({
        title: t('settings.revokeTitle'),
        body: t('settings.revokeBody'),
        confirm: t('settings.revokeConfirm'),
      });
      if (!ok) return;
      await busy(event.target, '…', async () => {
        await post('/api/sessions/revoke');
        toast(t('settings.revoked'));
      });
    });
  }

  const passwordForm = $('[data-password]', host);
  if (passwordForm) {
    passwordForm.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', passwordForm), t('settings.saving'), async () => {
        await post('/api/password/change', {
          current: passwordForm.current.value,
          password: passwordForm.password.value,
        });
        passwordForm.reset();
        toast(t('settings.passwordChanged'));
      });
    });
  }

  const emailForm = $('[data-email]', host);
  if (emailForm) {
    emailForm.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', emailForm), t('banner.sending'), async () => {
        const body = await post('/api/email/change', {
          email: emailForm.email.value.trim(),
          password: emailForm.password.value,
        });
        emailForm.reset();
        toast(t('settings.linkSent', { email: body.pending }));
      });
    });
  }

  $('[data-delete]', host).addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: t('settings.deleteTitle'),
      body: t('settings.deleteBody', { email: `<strong>${esc(user.email)}</strong>` }),
      confirm: t('settings.deleteConfirm'),
      danger: true,
      requireText: user.email || '',
    });
    if (!ok) return;
    try {
      await post('/api/account/delete', { confirm: user.email });
      finishSignOut();
    } catch (error) {
      toast(error.message, 'bad');
      refreshStatus().catch(() => {});
    }
  });
}

function reminderCard() {
  if (!isNative()) return '';
  return `<form class="card" data-reminder>
    <div class="card-head"><h2>${esc(t('settings.dailyReminder'))}</h2></div>
    <p class="muted">${esc(t('settings.dailyReminderHelp'))}</p>
    <label class="role-option">
      <input type="checkbox" name="enabled">
      <span>${esc(t('settings.reminderEnabled'))}</span>
    </label>
    <label class="field" style="max-width:180px;margin-top:12px">
      <span>${esc(t('settings.reminderTime'))}</span>
      <input type="time" name="time" value="07:30">
    </label>
    <div class="row-actions"><button class="primary inline" type="submit">${esc(t('settings.saveReminder'))}</button></div>
  </form>`;
}

async function bindReminder(host) {
  const form = $('[data-reminder]', host);
  const bridge = nativeBridge();
  if (!form || !bridge) return;
  const saved = await bridge.getReminder();
  form.enabled.checked = saved.enabled;
  form.time.value = `${String(saved.hour).padStart(2, '0')}:${String(saved.minute).padStart(2, '0')}`;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), t('settings.saving'), async () => {
      const [hour, minute] = form.time.value.split(':').map(Number);
      const accepted = await bridge.setReminder({
        enabled: form.enabled.checked,
        hour: Number.isFinite(hour) ? hour : 7,
        minute: Number.isFinite(minute) ? minute : 30,
      });
      if (!accepted) {
        form.enabled.checked = false;
        toast(t('settings.reminderDenied'), 'bad');
        return;
      }
      toast(t('settings.reminderSaved'));
    });
  });
}
