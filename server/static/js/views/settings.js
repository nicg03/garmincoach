/* Settings: the athlete profile plans are built from, where data comes
   from, and the account itself. */
import { renderGarmin } from '../components/garmin-connect.js';
import { bindDropzone, dropzoneHtml, extensionHtml } from '../components/importers.js';
import { api, post, put } from '../core/api.js';
import { rerender } from '../core/router.js';
import { cached, invalidate, refreshStatus, state } from '../core/state.js';
import { $, $$, bindCopy, busy, confirmDialog, esc, loadingPage, subnav, toast } from '../core/ui.js';

const TABS = [['profile', 'Profile'], ['sources', 'Data sources'], ['account', 'Account']];
const WEEKDAYS = [['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'], ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun']];
const DEFAULT_MINUTES = { mon: 0, tue: 60, wed: 60, thu: 60, fri: 60, sat: 90, sun: 90 };

export async function render(root, ctx = {}) {
  const wanted = (ctx.path || [])[0];
  const sub = TABS.some(([key]) => key === wanted) ? wanted : 'profile';
  root.innerHTML = `${subnav('#/settings', TABS, sub)}<div data-sub>${loadingPage()}</div>`;
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
      <div class="card-head"><h2>Weekly availability</h2></div>
      <p class="muted">Minutes you can train each day. Plans put the long run and the quality
        sessions on the days with the most time; 0 means a rest day.</p>
      <div class="form-grid days">${WEEKDAYS.map(([key, label]) => {
        const mins = avail[key] && avail[key].minutes != null ? avail[key].minutes : DEFAULT_MINUTES[key];
        return `<label class="field"><span>${label}</span>
          <input type="number" min="0" max="300" step="5" data-day="${key}" value="${mins}"></label>`;
      }).join('')}</div>
      <div class="card-head" style="margin-top:18px"><h2>Body</h2></div>
      <p class="muted">Optional. Leave blank and they're estimated from your activities.</p>
      <div class="form-grid">
        <label class="field"><span>Max heart rate</span>
          <input type="number" name="hr_max" min="120" max="230" value="${esc(athlete.hr_max || '')}"></label>
        <label class="field"><span>Resting heart rate</span>
          <input type="number" name="hr_rest" min="30" max="100" value="${esc(athlete.hr_rest || '')}"></label>
        <label class="field"><span>Weight (kg)</span>
          <input type="number" name="weight_kg" min="30" max="200" step="0.1" value="${esc(athlete.weight_kg || '')}"></label>
      </div>
      <div class="row-actions"><button class="primary inline" type="submit">Save profile</button></div>
    </form>`;

  const form = $('[data-athlete]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), 'Saving…', async () => {
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
      toast('Saved. New plans will use this.');
    });
  });
}

// ---- data sources -------------------------------------------------------------
async function sources(host) {
  const status = await refreshStatus();
  const user = status.user || {};
  const repo = status.repo || state.site.repo || '';
  const last = status.last_ingest;
  const command = `python -m garmin_sync link --url ${location.origin}`;
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>Garmin Connect</h2></div>
      <p class="muted">The easiest way: sign in once and we sync every few hours by ourselves.</p>
      <div data-garmin></div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Your data</h2></div>
      ${rows([
        ['Most recent day', status.last ? `${esc(status.last)} (${esc(status.ago)})` : 'none yet'],
        ['Oldest day', esc(status.first || 'none yet')],
        ['Days stored', esc(status.days || 0)],
        ['Activities stored', esc(status.activities || 0)],
        ['Last upload', last ? `${last.source === 'garmin' ? 'Garmin direct' : 'Extension or computer'}` +
          `${last.at ? ` · ${esc(last.at)}` : ''}` : 'none yet'],
      ])}
    </div>

    <h2 class="section-title">Other ways to import</h2>
    <div class="grid-2">
      <div class="card">
        <div class="card-head"><h2>Browser extension</h2></div>
        <p class="muted">Syncs through your own Garmin Connect session in Chrome. Useful if the
          direct connection is blocked, or if you'd rather not share a password.</p>
        ${extensionHtml('settings')}
      </div>
      <div class="card">
        <div class="card-head"><h2>Garmin export zip</h2></div>
        ${dropzoneHtml()}
      </div>
    </div>

    <details class="card advanced">
      <summary>Advanced: sync from your computer</summary>
      <p class="muted">The command-line client pushes data with the token below. Get it from
        ${repo ? `<a href="${esc(repo)}" target="_blank" rel="noopener">the repository</a>` : 'the repository'}.</p>
      <div class="cmd"><pre id="link-command">${esc(command)}</pre>
        <button type="button" class="ghost" data-copy="link-command">Copy</button></div>
      <p class="muted">Sync token</p>
      <div class="token-row"><code class="token" id="sync-token">${esc(user.sync_token || '')}</code>
        <button type="button" class="ghost" data-copy="sync-token">Copy</button>
        <button type="button" class="ghost" data-rotate>New token</button></div>
    </details>`;

  renderGarmin($('[data-garmin]', host), { onChange: rerender });
  bindDropzone(host, rerender);
  bindCopy(host);
  $('[data-rotate]', host).addEventListener('click', async (event) => {
    const ok = await confirmDialog({
      title: 'Make a new sync token?',
      body: 'The old token stops working straight away. Run the link command again on your computer, and reconnect the extension.',
      confirm: 'Make new token',
      danger: true,
    });
    if (!ok) return;
    await busy(event.target, '…', async () => {
      const body = await post('/api/token/rotate');
      state.status.user.sync_token = body.sync_token;
      toast('New token ready');
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
  const verified = user.email_verified ? 'Yes'
    : `Not yet · <button type="button" class="link" data-verify>Send link again</button>`;
  const pairs = [
    ['Email', esc(user.email || '')],
    ...(emails ? [['Email confirmed', verified]] : []),
    ['Account type', user.role === 'coach' ? 'Coach' : 'Athlete'],
    ['Member since', esc(user.since || '')],
  ];
  if (status.coach_limit) pairs.push(['Coach questions today', `${status.coach_used} of ${status.coach_limit}`]);
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>Account</h2></div>
      ${rows(pairs)}
      <div class="row-actions" style="margin-top:14px">
        <button type="button" class="ghost" data-logout>Sign out</button>
        ${emails ? '<button type="button" class="ghost" data-revoke>Sign out other devices</button>' : ''}
      </div>
    </div>
    ${emails ? `
    <div class="grid-2">
      <form class="card" data-password novalidate>
        <div class="card-head"><h2>Change password</h2></div>
        <p class="muted">Other devices get signed out; this one stays signed in.</p>
        <label class="field"><span>Current password</span>
          <input type="password" name="current" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span>
          <input type="password" name="password" autocomplete="new-password" required></label>
        <p class="field-hint">At least ${min} characters.</p>
        <div class="row-actions"><button class="primary inline" type="submit">Change password</button></div>
      </form>
      <form class="card" data-email novalidate>
        <div class="card-head"><h2>Change email</h2></div>
        <p class="muted">We send a link to the new address. Nothing changes until you open it.</p>
        <label class="field"><span>New email</span>
          <input type="email" name="email" autocomplete="email" required></label>
        <label class="field"><span>Password</span>
          <input type="password" name="password" autocomplete="current-password" required></label>
        <div class="row-actions"><button class="primary inline" type="submit">Send confirmation link</button></div>
      </form>
    </div>` : ''}
    <div class="card danger">
      <div class="card-head"><h2>Delete account</h2></div>
      <p class="muted">Removes your account, every stored day and activity, plans, races, coach
        links and the Garmin connection. This can't be undone.</p>
      <button type="button" class="ghost danger-button" data-delete>Delete my account</button>
    </div>`;

  $('[data-logout]', host).addEventListener('click', async () => {
    await fetch('/api/logout', { method: 'POST' });
    location.hash = '';
    location.reload();
  });
  const resend = $('[data-verify]', host);
  if (resend) {
    resend.addEventListener('click', () => busy(resend, 'Sending…', async () => {
      await post('/api/email/verify/send');
      toast(`Sent to ${user.email}`);
    }));
  }
  const revoke = $('[data-revoke]', host);
  if (revoke) {
    revoke.addEventListener('click', async (event) => {
      const ok = await confirmDialog({
        title: 'Sign out other devices?',
        body: 'Every other browser signed into this account has to sign in again. This one stays signed in.',
        confirm: 'Sign them out',
      });
      if (!ok) return;
      await busy(event.target, '…', async () => {
        await post('/api/sessions/revoke');
        toast('Other devices are signed out');
      });
    });
  }

  const passwordForm = $('[data-password]', host);
  if (passwordForm) {
    passwordForm.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', passwordForm), 'Saving…', async () => {
        await post('/api/password/change', {
          current: passwordForm.current.value,
          password: passwordForm.password.value,
        });
        passwordForm.reset();
        toast('Password changed. Other devices are signed out.');
      });
    });
  }

  const emailForm = $('[data-email]', host);
  if (emailForm) {
    emailForm.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', emailForm), 'Sending…', async () => {
        const body = await post('/api/email/change', {
          email: emailForm.email.value.trim(),
          password: emailForm.password.value,
        });
        emailForm.reset();
        toast(`Link sent to ${body.pending}. Open it to finish the change.`);
      });
    });
  }

  $('[data-delete]', host).addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: 'Delete your account?',
      body: `Everything goes, for good. Type <strong>${esc(user.email)}</strong> to confirm.`,
      confirm: 'Delete everything',
      danger: true,
      requireText: user.email || '',
    });
    if (!ok) return;
    try {
      await post('/api/account/delete', { confirm: user.email });
      location.hash = '';
      location.reload();
    } catch (error) {
      toast(error.message, 'bad');
      refreshStatus().catch(() => {});
    }
  });
}
