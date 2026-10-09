/* Boot, the sign-in gate, the shell around every view, and the router that
   picks a view for the hash. Views live in ./views and render into a fresh
   frame, so a slow answer for a page you already left lands nowhere. */
import { closePanel, isLinkRoute, openLink, showForgot } from './components/account-links.js';
import { openFeedback } from './components/feedback.js';
import { api, onUnauthorized, post } from './core/api.js';
import { getLocale, initLocale, onLocale, setLocale, t, translatePhrase } from './core/i18n.js';
import { navigate, path, rerender, start } from './core/router.js';
import { signOut } from './core/session.js';
import { events, hasData, invalidate, refreshStatus, role, state } from './core/state.js';
import { $, $$, agoFromEpoch, busy, errorCard, esc, loadingPage, toast } from './core/ui.js';
import { destroyCharts, initCharts } from './core/charts.js';
import { installGlossary } from './core/glossary.js';
import * as admin from './views/admin.js';
import * as athletes from './views/athletes.js';
import * as coaching from './views/coaching.js';
import * as guide from './views/guide.js';
import * as insights from './views/insights.js';
import * as settings from './views/settings.js';
import * as today from './views/today.js';
import * as training from './views/training.js';

const VIEWS = { today, training, insights, coaching, settings, athletes, guide, admin };

function isAdmin() {
  return Boolean(state.status.user && state.status.user.admin);
}

function title(section) {
  return t(`nav.${section}`);
}

const ICON_PATHS = {
  today: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  training: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/>',
  insights: '<path d="M3 20h18"/><path d="M6 16l4-5 3 3 5-7"/>',
  coaching: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  athletes: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/>',
  admin: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name]}</svg>`;
}

function sections() {
  const own = ['today', 'training', 'insights', 'coaching', 'settings'];
  const list = role() === 'coach' ? ['athletes', ...own] : own;
  return isAdmin() ? [...list, 'admin'] : list;
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
    ? `<span class="count" aria-label="${esc(t('nav.pending', { n: pendingRequests }))}">${pendingRequests}</span>` : '';
  return `<a href="#/${section}"${current}>${icon(section)}<span>${esc(title(section))}</span>${count}</a>`;
}

function renderNav() {
  $('#nav').innerHTML = sections().map(navLink).join('');
  $('#bottombar').innerHTML = bottomSections().map(navLink).join('');
  const user = state.status.user || {};
  $('#sidebar-foot').innerHTML = `<span>${esc(user.email || '')}</span>
    <button type="button" class="link feedback-link" data-feedback>${esc(t('feedback.betaLink'))}</button>`;
  $('#user-button').textContent = (user.email || '?').slice(0, 1).toUpperCase();
  $('#user-who').innerHTML = `<strong>${esc(user.email || '')}</strong><br>` +
    `<span class="muted">${user.role === 'coach' ? esc(t('nav.coachAccount')) : esc(t('nav.athleteAccount'))}</span>`;
  $('#menu-admin').classList.toggle('hidden', !isAdmin());
}

function renderPill() {
  const status = state.status;
  const g = status.garmin || {};
  const pill = $('#sync-pill');
  let tone = 'warn';
  let text = t('pill.none');
  if (g.connected && g.running) {
    tone = 'busy'; text = t('pill.syncing');
  } else if (g.connected && g.needs_login) {
    tone = 'bad'; text = t('pill.reconnect');
  } else if (g.connected && g.last_error) {
    tone = 'warn'; text = t('pill.problem');
  } else if (g.connected && g.last_sync) {
    tone = Date.now() / 1000 - g.last_sync > 86400 ? 'warn' : 'ok';
    text = t('pill.synced', { when: agoFromEpoch(g.last_sync) });
  } else if (status.last) {
    tone = status.stale ? 'warn' : 'ok';
    text = t('pill.through', { when: translatePhrase(status.ago) });
  }
  pill.className = `pill ${tone}`;
  pill.textContent = text;
  pill.title = t('pill.sources');
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
  if ($('#shell').classList.contains('hidden')) return;
  if (isLinkRoute(segments)) {
    clearTimeout(pollTimer);
    openLink(segments, { done: () => boot() });
    return;
  }
  const [section, ...rest] = segments;
  if (!section || !VIEWS[section] || (section === 'athletes' && role() !== 'coach')
      || (section === 'admin' && !isAdmin())) {
    navigate(home(), { replace: true });
    return;
  }
  destroyCharts();
  if (cleanup) cleanup();
  cleanup = null;
  renderNav();
  $('#page-title').textContent = title(section);
  document.title = `${title(section)} · gepard.fit`;

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
  if (g.running) return 4000;
  if (!hasData() && !g.last_error) return 10000;
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
    <span>${esc(t('banner.confirmBefore'))} <strong>${esc(user.email)}</strong>.
      ${esc(t('banner.confirmAfter'))}</span>
    <span class="spacer"></span>
    <button type="button" class="ghost small" data-resend>${esc(t('banner.sendAgain'))}</button></div>`;
  $('[data-resend]', host).addEventListener('click', (event) => busy(event.target, t('banner.sending'), async () => {
    await post('/api/email/verify/send');
    toast(t('banner.sent', { email: user.email }));
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
  $('#gate-submit').textContent = signup ? t('gate.create') : t('gate.signIn');
  $('#password').setAttribute('autocomplete', signup ? 'new-password' : 'current-password');
  $('#gate-role').classList.toggle('hidden', !signup);
  $('#password-hint').classList.toggle('hidden', !signup);
  // Forgot password needs account emails (ACCOUNT_EMAILS=1 and a Resend key).
  $('#gate-alt').classList.toggle('hidden', signup || !state.site.email);
  updateHint();
  $('#gate-error').textContent = '';
}

function updateHint() {
  const min = state.site.min_password || 8;
  const long = $('#password').value.length >= min;
  const hint = $('#password-hint');
  hint.textContent = long ? t('gate.lengthOk') : t('gate.lengthMin', { n: min });
  hint.classList.toggle('ok', long);
}

function showAuth(next = 'login') {
  if (next === 'signup' && !state.site.signup_open) next = 'login';
  setMode(next);
  $('#gate').classList.add('is-auth');
  requestAnimationFrame(() => $('#email').focus());
}

function hideAuth() {
  closePanel();
  $('#gate').classList.remove('is-auth');
}

function showGate(message = '') {
  clearTimeout(pollTimer);
  hideAuth();
  $('#shell').classList.add('hidden');
  $('#gate').classList.remove('hidden');
  $('#gate-error').textContent = message;
  $('#gate-tabs [data-mode="signup"]').classList.toggle('hidden', !state.site.signup_open);
  $$('[data-landing-signup]').forEach((button) => {
    button.classList.toggle('hidden', !state.site.signup_open);
  });
}

$$('#gate-tabs [data-mode]').forEach((tab) => tab.addEventListener('click', () => setMode(tab.dataset.mode)));
$('#password').addEventListener('input', updateHint);
$('#gate-forgot').addEventListener('click', () => showForgot($('#email').value.trim()));
$$('[data-landing-signin]').forEach((button) => button.addEventListener('click', () => showAuth('login')));
$$('[data-landing-signup]').forEach((button) => button.addEventListener('click', () => showAuth('signup')));
$$('[data-landing-close]').forEach((button) => button.addEventListener('click', hideAuth));
$('#landing-auth-layer').addEventListener('click', (event) => {
  if (event.target === event.currentTarget) hideAuth();
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if ($('#gate').classList.contains('is-auth') || $('#gate').classList.contains('landing-auth')) {
    hideAuth();
  }
});

$('#gate-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const error = $('#gate-error');
  error.textContent = '';
  const email = $('#email').value.trim();
  const password = $('#password').value;
  if (!email || !password) {
    error.textContent = t('gate.needBoth');
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
      error.textContent = body.error || body.detail || t('gate.failed');
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
$('#btn-feedback').addEventListener('click', () => {
  toggleMenu(false);
  openFeedback();
});
$('#sidebar-foot').addEventListener('click', (event) => {
  if (event.target.closest('[data-feedback]')) openFeedback();
});
$('#btn-logout').addEventListener('click', () => {
  toggleMenu(false);
  signOut();
});

function paintLang() {
  const lang = getLocale();
  $$('[data-lang]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.lang === lang));
  });
}
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-lang]');
  if (!button) return;
  setLocale(button.dataset.lang);
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
  $('#gate').classList.remove('is-auth', 'landing-auth');
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
initLocale();
paintLang();
document.addEventListener('app:signed-out', () => {
  state.status = {};
  state.chatId = null;
  invalidate();
  toggleMenu(false);
  document.title = 'gepard.fit';
  showGate();
});
onLocale(() => {
  paintLang();
  const banner = $('#account-banner');
  if (banner) delete banner.dataset.for;
  setMode(mode);
  if (!$('#shell').classList.contains('hidden')) {
    renderNav();
    renderPill();
    renderBanner();
    if (started) rerender();
  }
});
boot().catch(() => showGate(t('gate.unreachable')));
