/* Boot, the sign-in gate, the shell around every view, and the router that
   picks a view for the hash. Views live in ./views and render into a fresh
   frame, so a slow answer for a page you already left lands nowhere. */
import { closePanel, isLinkRoute, openLink, showForgot } from './components/account-links.js';
import { api, onUnauthorized, post } from './core/api.js';
import { navigate, path, rerender, start } from './core/router.js';
import { events, hasData, refreshStatus, role, state } from './core/state.js';
import { $, $$, agoFromEpoch, busy, errorCard, esc, loadingPage, toast } from './core/ui.js';
import { destroyCharts, initCharts } from './core/charts.js';
import { installGlossary } from './core/glossary.js';
import * as athletes from './views/athletes.js';
import * as coaching from './views/coaching.js';
import * as insights from './views/insights.js';
import * as settings from './views/settings.js';
import * as today from './views/today.js';
import * as training from './views/training.js';

const VIEWS = { today, training, insights, coaching, settings, athletes };
const TITLES = {
  today: 'Today', training: 'Training', insights: 'Insights',
  coaching: 'Coaching', settings: 'Settings', athletes: 'Athletes',
};

const ICON_PATHS = {
  today: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  training: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
  insights: '<path d="M3 20h18"/><path d="M6 16l4-5 3 3 5-7"/>',
  coaching: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  athletes: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

function sections() {
  const own = ['today', 'training', 'insights', 'coaching', 'settings'];
  return role() === 'coach' ? ['athletes', ...own] : own;
}

function bottomSections() {
  return role() === 'coach'
    ? ['athletes', 'today', 'training', 'insights', 'coaching']
    : ['today', 'training', 'insights', 'coaching', 'settings'];
}

function home() {
  return role() === 'coach' ? 'athletes' : 'today';
}

// ---- navigation -------------------------------------------------------------
let pendingRequests = 0;

function navLink(section) {
  const current = path()[0] === section ? ' aria-current="page"' : '';
  const count = section === 'athletes' && pendingRequests
    ? `<span class="count" aria-label="${pendingRequests} pending">${pendingRequests}</span>` : '';
  return `<a href="#/${section}"${current}>${icon(section)}<span>${TITLES[section]}</span>${count}</a>`;
}

function renderNav() {
  $('#nav').innerHTML = sections().map(navLink).join('');
  $('#bottombar').innerHTML = bottomSections().map(navLink).join('');
  const user = state.status.user || {};
  $('#sidebar-foot').textContent = user.email || '';
  $('#user-button').textContent = (user.email || '?').slice(0, 1).toUpperCase();
  $('#user-who').innerHTML = `<strong>${esc(user.email || '')}</strong><br>` +
    `<span class="muted">${user.role === 'coach' ? 'Coach account' : 'Athlete account'}</span>`;
}

function renderPill() {
  const status = state.status;
  const g = status.garmin || {};
  const pill = $('#sync-pill');
  let tone = 'warn';
  let text = 'No data yet';
  if (g.connected && g.running) {
    tone = 'busy'; text = 'Syncing…';
  } else if (g.connected && g.needs_login) {
    tone = 'bad'; text = 'Reconnect Garmin';
  } else if (g.connected && g.last_error) {
    tone = 'warn'; text = 'Sync problem';
  } else if (g.connected && g.last_sync) {
    tone = Date.now() / 1000 - g.last_sync > 86400 ? 'warn' : 'ok';
    text = `Synced ${agoFromEpoch(g.last_sync)}`;
  } else if (status.last) {
    tone = status.stale ? 'warn' : 'ok';
    text = `Data through ${status.ago}`;
  }
  pill.className = `pill ${tone}`;
  pill.textContent = text;
  pill.title = 'Data sources';
}

async function loadInboxCount() {
  if (role() !== 'coach') return;
  const inbox = await api('/api/coaching/inbox').catch(() => ({ requests: [] }));
  pendingRequests = (inbox.requests || []).length;
  renderNav();
}

// ---- routing ------------------------------------------------------------------
let cleanup = null;
let lastSection = null;

async function onRoute(segments) {
  if (isLinkRoute(segments)) {
    clearTimeout(pollTimer);
    openLink(segments, { done: () => boot() });
    return;
  }
  const [section, ...rest] = segments;
  if (!section || !VIEWS[section] || (section === 'athletes' && role() !== 'coach')) {
    navigate(home(), { replace: true });
    return;
  }
  destroyCharts();
  if (cleanup) cleanup();
  cleanup = null;
  renderNav();
  $('#page-title').textContent = TITLES[section];
  document.title = `${TITLES[section]} · garmincoach`;

  const frame = document.createElement('div');
  frame.innerHTML = loadingPage();
  $('#view').replaceChildren(frame);
  if (section !== lastSection) window.scrollTo(0, 0);
  lastSection = section;
  try {
    const result = await VIEWS[section].render(frame, { path: rest });
    if (frame.isConnected && typeof result === 'function') cleanup = result;
  } catch (error) {
    if (frame.isConnected) frame.innerHTML = errorCard(error);
  }
}

// ---- status polling -----------------------------------------------------------
let pollTimer = null;

function pollDelay() {
  const g = state.status.garmin || {};
  if (g.running || g.last_error || !hasData()) return 4000;
  return 60000;
}

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      await refreshStatus();
    } catch {
      return; // signed out: the gate is showing
    }
    schedulePoll();
  }, pollDelay());
}

function typing() {
  const el = document.activeElement;
  return el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
}

// ---- email verification banner --------------------------------------------------
function renderBanner() {
  const host = $('#account-banner');
  const user = state.status.user || {};
  // Without a mail provider there's nothing the user could click on.
  if (!state.site.email || !user.email || user.email_verified) {
    host.innerHTML = '';
    return;
  }
  if (host.dataset.for === user.email && host.innerHTML) return;
  host.dataset.for = user.email;
  host.innerHTML = `<div class="banner warn">
    <span>Confirm your email: we sent a link to <strong>${esc(user.email)}</strong>.
      You'll need it to reset your password.</span>
    <span class="spacer"></span>
    <button type="button" class="ghost small" data-resend>Send again</button></div>`;
  $('[data-resend]', host).addEventListener('click', (event) => busy(event.target, 'Sending…', async () => {
    await post('/api/email/verify/send');
    toast(`Sent to ${user.email}`);
  }));
}

events.addEventListener('status', () => {
  renderPill();
  renderBanner();
  schedulePoll();
});
events.addEventListener('data', () => {
  if (['today', 'insights'].includes(path()[0]) && !typing()) rerender();
});

// ---- sign-in gate ---------------------------------------------------------------
let mode = 'login';

function setMode(next) {
  mode = next;
  const signup = mode === 'signup';
  $$('#gate-tabs [data-mode]').forEach((tab) => {
    tab.setAttribute('aria-selected', String(tab.dataset.mode === mode));
  });
  $('#gate-submit').textContent = signup ? 'Create account' : 'Sign in';
  $('#password').setAttribute('autocomplete', signup ? 'new-password' : 'current-password');
  $('#gate-role').classList.toggle('hidden', !signup);
  $('#password-hint').classList.toggle('hidden', !signup);
  // Forgot password is parked until a Resend domain exists.
  $('#gate-alt').classList.toggle('hidden', signup || !state.site.email);
  updateHint();
  $('#gate-error').textContent = '';
}

function updateHint() {
  const min = state.site.min_password || 8;
  const long = $('#password').value.length >= min;
  const hint = $('#password-hint');
  hint.textContent = long ? 'Password length is fine.' : `At least ${min} characters.`;
  hint.classList.toggle('ok', long);
}

function showGate(message = '') {
  clearTimeout(pollTimer);
  closePanel();
  $('#shell').classList.add('hidden');
  $('#gate').classList.remove('hidden');
  $('#gate-error').textContent = message;
  $('#gate-tabs [data-mode="signup"]').classList.toggle('hidden', !state.site.signup_open);
}

$$('#gate-tabs [data-mode]').forEach((tab) => tab.addEventListener('click', () => setMode(tab.dataset.mode)));
$('#password').addEventListener('input', updateHint);
$('#gate-forgot').addEventListener('click', () => showForgot($('#email').value.trim()));

$('#gate-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const error = $('#gate-error');
  error.textContent = '';
  const email = $('#email').value.trim();
  const password = $('#password').value;
  if (!email || !password) {
    error.textContent = 'Enter your email and password.';
    return;
  }
  const payload = { email, password };
  if (mode === 'signup') {
    const picked = document.querySelector('input[name="gate-role"]:checked');
    payload.role = (picked && picked.value) || 'athlete';
  }
  $('#gate-submit').disabled = true;
  try {
    const response = await fetch(mode === 'signup' ? '/api/signup' : '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      error.textContent = body.error || body.detail || 'Could not sign in.';
      return;
    }
    $('#password').value = '';
    await boot();
  } finally {
    $('#gate-submit').disabled = false;
  }
});

// ---- user menu ------------------------------------------------------------------
function toggleMenu(open) {
  const list = $('#user-list');
  const show = open ?? list.classList.contains('hidden');
  list.classList.toggle('hidden', !show);
  $('#user-button').setAttribute('aria-expanded', String(show));
}
$('#user-button').addEventListener('click', (event) => {
  event.stopPropagation();
  toggleMenu();
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('#user-menu')) toggleMenu(false);
});
$('#user-list').addEventListener('click', (event) => {
  if (event.target.closest('a')) toggleMenu(false);
});
$('#btn-logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.hash = '';
  location.reload();
});

// ---- boot -------------------------------------------------------------------------
let started = false;

async function boot() {
  state.site = await fetch('/api/config').then((r) => r.json()).catch(() => ({}));
  setMode(mode);
  // Links from emails work whether or not this browser is signed in.
  if (isLinkRoute(path())) {
    openLink(path(), { done: () => boot() });
    return;
  }
  try {
    await refreshStatus();
  } catch {
    showGate();
    return;
  }
  $('#gate').classList.add('hidden');
  $('#shell').classList.remove('hidden');
  initCharts();
  renderPill();
  loadInboxCount();
  if (started) {
    rerender();
  } else {
    started = true;
    start(onRoute);
  }
}

onUnauthorized(() => showGate());
installGlossary();
events.addEventListener('inbox', loadInboxCount);
boot().catch(() => showGate('Could not reach the site.'));
