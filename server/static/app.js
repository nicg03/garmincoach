/* The whole client: sign in, fetch the aggregates, draw them, talk to the
   coach. No build step and no framework -- one payload from /api/metrics feeds
   every chart on the page, so there's very little state to keep straight. */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  range: '90',
  metrics: null,
  charts: {},
  history: [],
  site: {},
  status: {},
  mode: 'login',
};

const COLORS = {
  load: 'rgba(47, 109, 246, .35)',
  atl: '#e08c2c',
  ctl: '#1f9d5b',
  form: '#7c5cd6',
  hrv: '#12a5a5',
  rhr: '#d64545',
  score: '#2f6df6',
  deep: '#2457cc',
  light: '#7ba3f8',
  rem: '#12a5a5',
};

const SUGGESTIONS = [
  'How is my training load trending, and is recovery keeping up?',
  'What should I do this week?',
  'Am I sleeping enough for this volume?',
  'Anything in the last month that looks like a warning sign?',
];

// ---- plumbing ---------------------------------------------------------------
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  if (response.status === 401) {
    showGate();
    throw new Error('Not signed in');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || body.error || response.statusText);
  }
  return response.json();
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

function showGate(message) {
  $('#app').classList.add('hidden');
  $('#gate').classList.remove('hidden');
  $('#gate-error').textContent = message || '';
}

function fmt(value, digits = 0) {
  return value === null || value === undefined || Number.isNaN(value)
    ? '--' : Number(value).toFixed(digits);
}

function shortDate(iso) {
  return iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) : '';
}

function css(name) {
  return getComputedStyle(document.body).getPropertyValue(name).trim();
}

function escapeHtml(text) {
  return String(text).replace(/[&<>]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

// ---- sign in and sign up ----------------------------------------------------
function setMode(mode) {
  state.mode = mode;
  const signup = mode === 'signup';
  $('#gate-submit').textContent = signup ? 'Create account' : 'Sign in';
  $('#gate-switch').textContent = signup ? 'Sign in instead' : 'Create an account';
  $('#gate-switch-text').textContent = signup ? 'Already have one?' : 'New here?';
  $('#password').setAttribute('autocomplete', signup ? 'new-password' : 'current-password');
  $('#gate-blurb').textContent = signup
    ? `Pick a password of at least ${state.site.min_password || 8} characters.`
    : 'Your training data, and someone to think about it with.';
  $('#gate-error').textContent = '';
}

$('#gate-switch').addEventListener('click', () => {
  setMode(state.mode === 'signup' ? 'login' : 'signup');
});

$('#gate-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('#gate-error').textContent = '';
  $('#gate-submit').disabled = true;
  try {
    const path = state.mode === 'signup' ? '/api/signup' : '/api/login';
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: $('#email').value, password: $('#password').value }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      $('#gate-error').textContent = body.error || body.detail || 'Could not sign in.';
      return;
    }
    $('#password').value = '';
    boot();
  } finally {
    $('#gate-submit').disabled = false;
  }
});

$('#btn-logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  location.reload();
});

// ---- tabs and range ---------------------------------------------------------
$$('.tab').forEach((tab) => tab.addEventListener('click', () => {
  $$('.tab').forEach((t) => t.classList.toggle('active', t === tab));
  $$('.panel').forEach((panel) => {
    panel.classList.toggle('hidden', panel.id !== 'tab-' + tab.dataset.tab);
  });
  if (tab.dataset.tab === 'coach' && state.status.coach && !$('#brief').dataset.loaded) {
    loadBrief();
  }
}));

$$('.chip').forEach((chip) => chip.addEventListener('click', () => {
  $$('.chip').forEach((c) => c.classList.toggle('active', c === chip));
  state.range = chip.dataset.range;
  loadDashboard();
}));

// ---- dashboard --------------------------------------------------------------
async function loadDashboard() {
  const query = state.range === 'all' ? 'days=3650' : `days=${state.range}`;
  state.metrics = await api(`/api/metrics?${query}`);
  renderHeadline(state.metrics.headline);
  renderCharts(state.metrics);
  renderActivities(state.metrics.recent_activities || []);
}

function tile(label, value, note, tone) {
  const cls = tone ? ` ${tone}` : '';
  return `<div class="stat"><div class="label">${label}</div>` +
         `<div class="value">${value}</div>` +
         `<div class="note${cls}">${note || ''}</div></div>`;
}

function signed(value, digits = 1) {
  if (value === null || value === undefined) return '';
  const n = Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}

function renderHeadline(h) {
  const formTone = h.form === null || h.form === undefined ? ''
    : (h.form < -25 ? 'bad' : h.form < -8 ? 'warn' : 'good');
  const formNote = h.form === null || h.form === undefined ? ''
    : (h.form < -25 ? 'deep in the hole' : h.form < -8 ? 'building' : 'fresh');

  let ratioTone = '';
  let ratioNote = 'acute vs chronic load';
  if (h.acwr !== null && h.acwr !== undefined) {
    if (h.acwr > 1.5) { ratioTone = 'bad'; ratioNote = 'ramping up fast'; }
    else if (h.acwr > 1.3) { ratioTone = 'warn'; ratioNote = 'above the sweet spot'; }
    else if (h.acwr < 0.8) { ratioTone = 'warn'; ratioNote = 'detraining'; }
    else { ratioTone = 'good'; ratioNote = 'in the sweet spot'; }
  }

  const hrvTone = h.hrv_delta > 0 ? 'good' : h.hrv_delta < -3 ? 'warn' : '';
  const rhrTone = h.resting_hr_delta > 2 ? 'warn' : h.resting_hr_delta < 0 ? 'good' : '';

  $('#headline').innerHTML = [
    tile('Form', fmt(h.form), formNote, formTone),
    tile('Load ratio', fmt(h.acwr, 2), ratioNote, ratioTone),
    tile('HRV', fmt(h.hrv), h.hrv_delta !== null && h.hrv_delta !== undefined
      ? `${signed(h.hrv_delta)} vs baseline` : (h.hrv_status || ''), hrvTone),
    tile('Resting HR', fmt(h.resting_hr), h.resting_hr_delta !== null &&
      h.resting_hr_delta !== undefined ? `${signed(h.resting_hr_delta)} vs baseline` : '', rhrTone),
    tile('Sleep', fmt(h.sleep_score), h.sleep_h ? `${fmt(h.sleep_h, 1)} h last night` : ''),
    tile('This week', `${fmt(h.week_km, 1)} km`,
      `${h.week_sessions || 0} sessions · load ${fmt(h.week_load)}`),
  ].join('');
}

function baseOptions(extra = {}) {
  const grid = css('--line');
  const tick = css('--muted');
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: tick, boxWidth: 10, usePointStyle: true, font: { size: 11 } } },
      tooltip: { backgroundColor: '#1c2530', padding: 10, cornerRadius: 8 },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkipPadding: 18, font: { size: 10 } } },
      y: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } } },
    },
    ...extra,
  };
}

function draw(id, config) {
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart($('#' + id), config);
}

function renderCharts(data) {
  const labels = data.dates.map(shortDate);
  const grid = css('--line');
  const tick = css('--muted');
  const rightAxis = {
    position: 'right',
    grid: { display: false },
    border: { display: false },
    ticks: { color: tick, font: { size: 10 } },
  };

  // Load, fatigue, fitness and what's left over.
  draw('chart-load', {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Load', data: data.training.map((r) => r.load),
          backgroundColor: COLORS.load, borderRadius: 2, order: 3 },
        { type: 'line', label: 'ATL (7d)', data: data.training.map((r) => r.atl),
          borderColor: COLORS.atl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: 'CTL (42d)', data: data.training.map((r) => r.ctl),
          borderColor: COLORS.ctl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: 'Form', data: data.training.map((r) => r.form),
          borderColor: COLORS.form, borderWidth: 1.5, borderDash: [4, 3],
          pointRadius: 0, tension: .3, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkipPadding: 18, font: { size: 10 } } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'load', color: tick, font: { size: 10 } } },
        y1: rightAxis,
      },
    }),
  });

  // Recovery against its own baseline.
  draw('chart-recovery', {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'HRV', data: data.wellness.map((r) => r.hrv), borderColor: COLORS.hrv,
          borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true },
        { label: 'HRV baseline', data: data.wellness.map((r) => r.hrv_base),
          borderColor: COLORS.hrv, borderWidth: 1, borderDash: [4, 3],
          pointRadius: 0, tension: .3, spanGaps: true },
        { label: 'Resting HR', data: data.wellness.map((r) => r.resting_hr),
          borderColor: COLORS.rhr, borderWidth: 2, pointRadius: 0, tension: .3,
          spanGaps: true, yAxisID: 'y1' },
        { label: 'RHR baseline', data: data.wellness.map((r) => r.resting_hr_base),
          borderColor: COLORS.rhr, borderWidth: 1, borderDash: [4, 3],
          pointRadius: 0, tension: .3, spanGaps: true, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        x: { grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkipPadding: 18, font: { size: 10 } } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'HRV (ms)', color: tick, font: { size: 10 } } },
        y1: { ...rightAxis, title: { display: true, text: 'bpm', color: tick, font: { size: 10 } } },
      },
    }),
  });

  // Sleep stages stacked, score on top.
  draw('chart-sleep', {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Deep', data: data.wellness.map((r) => r.deep_h),
          backgroundColor: COLORS.deep, stack: 's' },
        { type: 'bar', label: 'Light', data: data.wellness.map((r) => r.light_h),
          backgroundColor: COLORS.light, stack: 's' },
        { type: 'bar', label: 'REM', data: data.wellness.map((r) => r.rem_h),
          backgroundColor: COLORS.rem, stack: 's' },
        { type: 'line', label: 'Score', data: data.wellness.map((r) => r.sleep_score),
          borderColor: COLORS.score, borderWidth: 2, pointRadius: 0, tension: .3,
          spanGaps: true, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: tick, maxRotation: 0, autoSkipPadding: 18, font: { size: 10 } } },
        y: { stacked: true, grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'hours', color: tick, font: { size: 10 } } },
        y1: { ...rightAxis, min: 0, max: 100 },
      },
    }),
  });

  renderWeeks(data.weeks || [], grid, tick, rightAxis);

  draw('chart-scatter', {
    type: 'scatter',
    data: {
      datasets: [{
        label: 'day',
        data: (data.load_vs_hrv || []).map((p) => ({ x: p.load, y: p.next_hrv })),
        backgroundColor: COLORS.hrv,
        pointRadius: 4,
      }],
    },
    options: baseOptions({
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'training load that day', color: tick, font: { size: 10 } } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'HRV next morning', color: tick, font: { size: 10 } } },
      },
    }),
  });
}

function renderWeeks(weeks, grid, tick, rightAxis) {
  const sports = [...new Set(weeks.flatMap((w) => Object.keys(w.by_type || {})))];
  const palette = ['#2f6df6', '#12a5a5', '#e08c2c', '#7c5cd6', '#1f9d5b', '#d64545'];
  const datasets = sports.map((sport, i) => ({
    type: 'bar',
    label: sport.replace(/_/g, ' '),
    data: weeks.map((w) => (w.by_type[sport] || {}).hours || 0),
    backgroundColor: palette[i % palette.length],
    stack: 'w',
  }));
  datasets.push({
    type: 'line',
    label: 'Load',
    data: weeks.map((w) => w.load),
    borderColor: COLORS.form,
    borderWidth: 2,
    pointRadius: 0,
    tension: .3,
    yAxisID: 'y1',
  });

  draw('chart-weeks', {
    data: { labels: weeks.map((w) => shortDate(w.start)), datasets },
    options: baseOptions({
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { color: tick, font: { size: 10 } } },
        y: { stacked: true, grid: { color: grid }, border: { display: false }, ticks: { color: tick, font: { size: 10 } }, title: { display: true, text: 'hours', color: tick, font: { size: 10 } } },
        y1: rightAxis,
      },
    }),
  });
}

function renderActivities(activities) {
  const rows = activities.map((a) => {
    const minutes = a.duration_s ? Math.round(a.duration_s / 60) : null;
    const km = a.distance_m ? a.distance_m / 1000 : null;
    return `<tr>
      <td>${(a.start || '').slice(0, 10)}</td>
      <td>${escapeHtml(a.name || a.type || '')}</td>
      <td>${escapeHtml((a.type || '').replace(/_/g, ' '))}</td>
      <td>${minutes ? minutes + "'" : '--'}</td>
      <td>${km ? fmt(km, 1) : '--'}</td>
      <td>${a.avg_hr || '--'}</td>
      <td>${fmt(a.training_load)}</td>
    </tr>`;
  }).join('');

  $('#activities').innerHTML =
    '<thead><tr><th>Date</th><th>Name</th><th>Sport</th><th>Time</th>' +
    '<th>km</th><th>HR</th><th>Load</th></tr></thead>' +
    `<tbody>${rows || '<tr><td colspan="7">Nothing here yet.</td></tr>'}</tbody>`;
}

// ---- account ----------------------------------------------------------------
function linkCommand() {
  return `python -m garmin_sync link --url ${location.origin}`;
}

function renderAccount(status) {
  const user = status.user || {};
  const rows = [
    ['Email', escapeHtml(user.email || '')],
    ['Member since', user.since || ''],
  ];
  if (status.coach_limit) {
    rows.push(['Coach questions today',
               `${status.coach_used} of ${status.coach_limit}`]);
  }
  $('#account-rows').innerHTML = rows
    .map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join('');

  $('#token').textContent = user.sync_token || '';
  $('#account-command').textContent = linkCommand();
  $('#delete-confirm').placeholder = `Type ${user.email} to confirm`;

  $('#status-rows').innerHTML = [
    ['Most recent day', status.last ? `${status.last} (${status.ago})` : 'none yet'],
    ['Oldest day', status.first || 'none yet'],
    ['Days stored', status.days],
    ['Activities stored', status.activities],
    ['Coach', status.coach ? 'available' : 'off on this site'],
  ].map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join('');
}

$('#btn-copy-token').addEventListener('click', async () => {
  const token = $('#token').textContent;
  try {
    await navigator.clipboard.writeText(token);
    $('#btn-copy-token').textContent = 'Copied';
  } catch {
    $('#btn-copy-token').textContent = 'Select it by hand';
  }
  setTimeout(() => { $('#btn-copy-token').textContent = 'Copy'; }, 2000);
});

$$('.copy-cmd').forEach((button) => {
  button.addEventListener('click', async () => {
    const el = document.getElementById(button.dataset.copy);
    const text = (el && el.textContent) || '';
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = 'Copied';
    } catch {
      button.textContent = 'Select it';
    }
    setTimeout(() => { button.textContent = 'Copy'; }, 2000);
  });
});

$('#btn-rotate').addEventListener('click', async () => {
  if (!confirm('The old token stops working immediately. Continue?')) return;
  const body = await post('/api/token/rotate');
  state.status.user.sync_token = body.sync_token;
  renderAccount(state.status);
  $('#rotate-note').textContent =
    'New token ready. On your computer run the Connect command above again.';
});

$('#btn-delete').addEventListener('click', async () => {
  $('#delete-error').textContent = '';
  try {
    await post('/api/account/delete', { confirm: $('#delete-confirm').value });
    location.reload();
  } catch (error) {
    $('#delete-error').textContent = error.message;
  }
});

// ---- coach ------------------------------------------------------------------
/* Just enough markdown for what the model actually sends back.
   Wrapped lines are joined back into their paragraph or bullet first --
   otherwise emphasis that straddles a line break never closes, and every
   line becomes its own paragraph. */
function markdown(text) {
  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>');

  const blocks = [];
  let paragraph = [];
  let items = null;

  const flushParagraph = () => {
    if (paragraph.length) blocks.push(`<p>${inline(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (items) {
      blocks.push(`<ul>${items.map((i) => `<li>${inline(i)}</li>`).join('')}</ul>`);
    }
    items = null;
  };

  for (const raw of escapeHtml(text).split('\n')) {
    const line = raw.trim();
    if (!line) { flushParagraph(); flushList(); continue; }

    if (/^#{1,6}\s/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push(`<h3>${inline(line.replace(/^#+\s*/, ''))}</h3>`);
      continue;
    }

    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (!items) items = [];
      items.push(bullet[1]);
    } else if (items) {
      items[items.length - 1] += ' ' + line;
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return blocks.join('');
}

async function loadBrief(refresh = false) {
  const target = $('#brief');
  target.dataset.loaded = '1';
  target.innerHTML = '<p class="muted">Thinking about your last few weeks…</p>';
  try {
    const body = await api('/api/brief' + (refresh ? '?refresh=1' : ''));
    target.innerHTML = markdown(body.text || '');
  } catch (error) {
    target.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  }
}

$('#btn-rebrief').addEventListener('click', () => loadBrief(true));

function addMessage(cls, text) {
  const node = document.createElement('div');
  node.className = `msg ${cls}`;
  node.innerHTML = cls === 'me' ? escapeHtml(text) : markdown(text);
  $('#chat').appendChild(node);
  node.scrollIntoView({ block: 'nearest' });
  return node;
}

async function ask(question) {
  if (!question.trim()) return;
  addMessage('me', question);
  $('#question').value = '';
  const bubble = addMessage('claude', '');
  bubble.classList.add('pending');
  $('#btn-ask').disabled = true;

  let answer = '';
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ question, history: state.history.slice(-8) }),
    });
    if (!response.ok || !response.body) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || body.error || 'The coach is unavailable.');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const event of events) {
        const line = event.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const payload = JSON.parse(line.slice(5).trim());
        if (payload.error) throw new Error(payload.error);
        if (payload.delta) {
          answer += payload.delta;
          bubble.innerHTML = markdown(answer);
          bubble.scrollIntoView({ block: 'nearest' });
        }
      }
    }
    state.history.push({ role: 'user', content: question });
    state.history.push({ role: 'assistant', content: answer });
  } catch (error) {
    bubble.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  } finally {
    bubble.classList.remove('pending');
    $('#btn-ask').disabled = false;
  }
}

$('#chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  ask($('#question').value);
});

$('#question').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    ask($('#question').value);
  }
});

$('#suggestions').innerHTML = SUGGESTIONS
  .map((s) => `<button type="button">${s}</button>`).join('');
$$('#suggestions button').forEach((button) => {
  button.addEventListener('click', () => ask(button.textContent));
});

// ---- boot -------------------------------------------------------------------
async function boot() {
  state.site = await fetch('/api/config').then((r) => r.json()).catch(() => ({}));
  setMode(state.mode);
  $('#gate-switch').classList.toggle('hidden', !state.site.signup_open);
  $('#gate-switch-text').classList.toggle('hidden', !state.site.signup_open);

  let status;
  try {
    status = await api('/api/status');
  } catch {
    return; // api() already showed the gate
  }
  state.status = status;

  $('#gate').classList.add('hidden');
  $('#app').classList.remove('hidden');
  $('#freshness').textContent = status.last ? `updated ${status.ago}` : 'no data yet';
  const repo = status.repo || state.site.repo || '#';
  $('#repo-link').href = repo;
  const repoAccount = $('#repo-link-account');
  if (repoAccount) repoAccount.href = repo;
  $('#onboard-command').textContent = linkCommand();
  renderAccount(status);

  // Nothing pushed yet: the charts would be empty, so explain what to do.
  const empty = !status.days;
  $('#onboarding').classList.toggle('hidden', !empty);
  $('#dashboard-body').classList.toggle('hidden', empty);

  $('#coach-off').classList.toggle('hidden', !!status.coach);
  $('#coach-body').classList.toggle('hidden', !status.coach);

  if (empty) return;
  loadDashboard().catch((error) => {
    $('#headline').innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  });
}

boot().catch(() => showGate('Could not reach the site.'));
