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
  load: 'rgba(33, 84, 255, .22)',
  atl: '#2154ff',
  ctl: '#0f8a5a',
  form: '#7c5cff',
  hrv: '#0e9aa0',
  rhr: '#d63b3b',
  score: '#111111',
  deep: '#1b3a7a',
  light: '#8aa8ff',
  rem: '#0e9aa0',
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
    : 'Your training data, brought over by a browser extension.';
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
  if (tab.dataset.tab === 'plan') loadPlanTab();
  if (tab.dataset.tab === 'workouts') loadWorkouts();
  if (tab.dataset.tab === 'performance') loadPerformance();
  if (tab.dataset.tab === 'account') loadAthlete();
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
    tile('Form (TSB)', fmt(h.form),
      [formNote, h.ctl != null ? `CTL ${fmt(h.ctl)}` : '', h.atl != null ? `ATL ${fmt(h.atl)}` : '']
        .filter(Boolean).join(' · '), formTone),
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
  if (!window.Chart) return;
  const canvas = $('#' + id);
  if (!canvas) return;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas, config);
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

function siteOrigin() {
  return location.origin;
}

function bindDropzone(zone, fileInput, status, browse) {
  if (!zone || !fileInput) return;

  const pick = () => fileInput.click();
  zone.addEventListener('click', (event) => {
    if (event.target === browse) return;
    pick();
  });
  zone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      pick();
    }
  });
  if (browse) browse.addEventListener('click', (event) => {
    event.stopPropagation();
    pick();
  });
  zone.addEventListener('dragover', (event) => {
    event.preventDefault();
    zone.classList.add('drag');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('drag');
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) importExport(file, status);
  });
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) importExport(file, status);
    fileInput.value = '';
  });
}

async function importExport(file, status) {
  if (!file) return;
  const name = file.name || 'export.zip';
  if (!/\.zip$/i.test(name) && file.type !== 'application/zip') {
    if (status) status.textContent = 'That needs to be the zip Garmin emailed you.';
    return;
  }
  if (status) status.textContent = `Reading ${name}… this can take a minute.`;
  try {
    const response = await fetch('/api/import/garmin-export', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.error || body.detail || 'Import failed.');
    }
    const stored = body.stored || {};
    const report = body.report || {};
    if (status) {
      status.textContent =
        `Imported ${stored.activities || report.activities || 0} activities ` +
        `and ${stored.days || report.days || 0} days.`;
    }
    await boot();
  } catch (error) {
    if (status) status.textContent = error.message;
  }
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
  const origin = $('#account-origin');
  if (origin) origin.textContent = siteOrigin();
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

// ---- plan / workouts / performance -----------------------------------------
function sessionKind(s) {
  return (s.kind || s.workout && s.workout.kind || '').replace(/_/g, ' ');
}

async function loadPlanTab() {
  const [races, planBody, decision, perf] = await Promise.all([
    api('/api/races'),
    api('/api/plan'),
    api('/api/day/decide').catch(() => null),
    api('/api/performance').catch(() => ({})),
  ]);
  state.races = races.races || [];
  state.plan = planBody.plan;
  state.adherence = planBody.adherence;
  state.performance = perf;
  renderRaces();
  renderPlan();
  renderDecision(decision);
}

function renderDecision(decision) {
  if (!decision) {
    $('#decide-text').textContent = 'No session planned for today.';
    $('#decide-actions').innerHTML = '';
    return;
  }
  const wo = decision.session && decision.session.workout;
  const name = wo && wo.name || (decision.session && decision.session.kind) || 'No session';
  const applied = decision.applied ? ` Applied: ${decision.applied}.` : '';
  const desc = (decision.session && (decision.session.description || (wo && wo.description))) || '';
  $('#decide-text').textContent =
    `${name}: ${decision.reason} Suggested: ${decision.action}.${applied}` +
    (desc ? ` ${desc}` : '');
  $('#decide-actions').innerHTML = ['keep', 'ease', 'swap', 'rest'].map((a) =>
    `<button type="button" class="ghost" data-act="${a}">${a}</button>`).join('');
  $$('#decide-actions button').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await post('/api/day/decide', { action: btn.dataset.act });
      loadPlanTab();
    });
  });
}

function raceFeasibility(race) {
  const p = state.performance;
  if (!p || !race.goal_time || !race.distance_m) return '';
  const pred = (p.predictions || []).find((row) =>
    Math.abs((row.distance_m || 0) - race.distance_m) < 800);
  const expected = (pred && (pred.vdot_s || pred.riegel_s)) || null;
  if (!expected) return '';
  const parts = String(race.goal_time).split(':').map(Number);
  let goal = 0;
  if (parts.length === 3) goal = parts[0] * 3600 + parts[1] * 60 + parts[2];
  else if (parts.length === 2) goal = parts[0] * 60 + parts[1];
  if (!goal) return '';
  const pct = (goal - expected) / expected;
  if (pct > 0.03) return 'comfortable (slower than VDOT by >3%)';
  if (pct > -0.03) return 'on pace (within 3% of VDOT)';
  if (pct > -0.08) return 'stretch (3–8% faster than VDOT)';
  return 'ambitious (>8% faster than VDOT)';
}

function renderRaces() {
  const today = new Date().toISOString().slice(0, 10);
  const rows = (state.races || []).map((r) => {
    const feas = raceFeasibility(r);
    const past = r.date <= today;
    return `<tr>
      <td>${r.date}</td>
      <td>${escapeHtml(r.name)}</td>
      <td>${escapeHtml(r.priority || 'A')}</td>
      <td>${r.distance_m ? Math.round(r.distance_m / 1000) + ' km' : ''}</td>
      <td>${escapeHtml(r.goal_time || '')}</td>
      <td>${escapeHtml(feas)}</td>
      <td>
        ${past ? `<button type="button" class="ghost" data-review="${escapeHtml(r.id)}">Review</button>` : ''}
        <button type="button" class="ghost" data-del="${escapeHtml(r.id)}">Remove</button>
      </td>
    </tr>`;
  }).join('');
  $('#race-table').innerHTML =
    '<thead><tr><th>Date</th><th>Name</th><th>Pri</th><th>Dist</th><th>Goal</th><th>Fit</th><th></th></tr></thead>'
    + `<tbody>${rows || '<tr><td colspan="7">No races yet.</td></tr>'}</tbody>`;
  $$('#race-table [data-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await api('/api/races/' + btn.dataset.del, { method: 'DELETE' });
      loadPlanTab();
    });
  });
  $$('#race-table [data-review]').forEach((btn) => {
    btn.addEventListener('click', () => loadRaceReview(btn.dataset.review));
  });
  $('#plan-race').innerHTML = (state.races || []).map((r) =>
    `<option value="${r.id}">${escapeHtml(r.name)} (${r.date})</option>`).join('');
}

async function loadRaceReview(id) {
  const host = $('#race-review');
  if (!host) return;
  try {
    const body = await api('/api/races/' + id + '/review');
    const r = body.result || {};
    const f = body.feasibility || {};
    const splits = (body.splits || []).map((s) => s.label + ': ' + (s.pace || '')).join(' · ');
    host.innerHTML =
      `<strong>${escapeHtml((body.race && body.race.name) || 'Race')}</strong> — ` +
      `goal ${escapeHtml(r.goal || '—')}, actual ${escapeHtml(r.actual || 'not raced yet')}` +
      (r.vs_goal ? `, ${r.vs_goal}` : '') +
      (f.label ? `. ${escapeHtml(f.label)}` : '') +
      (splits ? `. Splits: ${escapeHtml(splits)}` : '');
  } catch (error) {
    host.textContent = error.message;
  }
}

function renderPlan() {
  const plan = state.plan;
  const sheet = $('#session-sheet');
  if (sheet) sheet.classList.add('hidden');
  if (!plan) {
    $('#plan-rationale').textContent = 'Generate a plan from a race.';
    $('#plan-headline').innerHTML = '';
    if ($('#pace-board')) $('#pace-board').innerHTML = '';
    if ($('#pace-source')) $('#pace-source').textContent = '';
    if ($('#plan-weeks')) $('#plan-weeks').innerHTML = '';
    $('#plan-adherence').textContent = '';
    return;
  }
  $('#plan-rationale').textContent = plan.rationale || '';
  const h = plan.headline || {};
  $('#plan-headline').innerHTML = [
    tile('Weeks', h.weeks, h.short_block ? `short vs ${h.ideal_weeks} ideal` : (h.family || '')),
    tile('Sessions', h.sessions, ''),
    tile('VDOT', fmt(h.vdot, 1), ''),
    tile('Peak km/wk', fmt(h.weekly_km_peak, 0), `now ${fmt(h.weekly_km_now, 0)}`),
    tile('CTL at race', fmt(h.ctl_at_race), `now ${fmt(h.ctl_now)}`),
  ].join('');
  renderPaceBoard(h.paces || (state.performance && state.performance.paces) || {});
  const adh = state.adherence || {};
  $('#plan-adherence').textContent = adh.planned
    ? `Adherence: ${adh.completed}/${adh.planned} done`
      + (adh.skipped ? `, ${adh.skipped} skipped` : '')
    : '';
  renderWeekCalendar(plan);
  drawProjected(plan.projected || []);
}

function renderPaceBoard(paces) {
  const host = $('#pace-board');
  if (!host) return;
  const bands = paces.bands || {};
  const rows = [
    ['easy', 'Easy'],
    ['marathon', 'M'],
    ['threshold', 'T'],
    ['interval', 'I'],
    ['rep', 'R'],
    ['goal', 'Goal'],
  ];
  host.innerHTML = rows.map(([key, label]) => {
    const b = bands[key] || {};
    const mid = b.mid || paces[key];
    if (!mid) return '';
    const rng = (b.high && b.low && b.high !== b.low) ? `${b.high}–${b.low}` : '';
    return `<div class="pace-chip"><span class="k">${label}</span>` +
      `<span class="v">${escapeHtml(mid)}</span>` +
      (rng ? `<span class="r">${escapeHtml(rng)}/km</span>` : '') +
      `</div>`;
  }).join('');
  const src = $('#pace-source');
  if (src) {
    const from = (state.performance && state.performance.vdot_from) || {};
    src.textContent = paces.note
      || (paces.source === 'vdot' && from.mark
        ? `Paces from VDOT ${fmt(paces.vdot, 1)} (${from.mark} ${from.time || ''}). Easy is a range; T/I/R are ± a few s/km.`
        : (paces.source ? `Paces from ${paces.source}.` : ''));
  }
}

function mondayOf(iso) {
  const d = new Date(iso + 'T00:00:00');
  const day = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - day);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function renderWeekCalendar(plan) {
  const host = $('#plan-weeks');
  if (!host) return;
  const sessions = plan.sessions || [];
  const weekMeta = {};
  (plan.weeks || []).forEach((w) => { weekMeta[w.start] = w; });
  const groups = [];
  const index = {};
  sessions.forEach((s) => {
    const key = mondayOf(s.date);
    if (!index[key]) {
      index[key] = [];
      groups.push(key);
    }
    index[key].push(s);
  });
  const dow = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  host.innerHTML = groups.map((key) => {
    const meta = weekMeta[key] || {};
    const byDow = {};
    (index[key] || []).forEach((s) => {
      const d = new Date(s.date + 'T00:00:00');
      byDow[(d.getDay() + 6) % 7] = s;
    });
    const cells = dow.map((label, i) => {
      const s = byDow[i];
      if (!s) {
        return `<button type="button" class="day-cell empty" disabled>
          <span class="dow">${label}</span><span class="nm">—</span></button>`;
      }
      const wo = s.workout || {};
      const km = s.distance_km != null ? fmt(s.distance_km, 1) + ' km' : '';
      const pace = (wo.targets && wo.targets.work) || '';
      return `<button type="button" class="day-cell" data-sid="${escapeHtml(s.id)}">
        <span class="dow">${label} ${s.date.slice(8)}</span>
        <span class="nm">${escapeHtml(wo.name || sessionKind(s))}</span>
        <span class="meta">${escapeHtml([km, pace, s.phase].filter(Boolean).join(' · '))}</span>
      </button>`;
    }).join('');
    return `<div class="week-block">
      <h3>${key}<span>${escapeHtml(meta.phase || '')}` +
      `${meta.target_km ? ` · ${fmt(meta.target_km, 0)} km` : ''}</span></h3>
      <div class="week-grid">${cells}</div>
    </div>`;
  }).join('');
  $$('#plan-weeks [data-sid]').forEach((btn) => {
    btn.addEventListener('click', () => {
      $$('#plan-weeks .day-cell').forEach((el) => el.classList.remove('active'));
      btn.classList.add('active');
      const session = sessions.find((s) => s.id === btn.dataset.sid);
      showSessionSheet(session);
    });
  });
}

function stepRows(steps, prefix) {
  const rows = [];
  (steps || []).forEach((step, i) => {
    if (step.kind === 'repeat') {
      rows.push(`<tr><td colspan="3"><strong>${step.times}× set</strong></td></tr>`);
      rows.push(...stepRows(step.steps || [], `${prefix}${i}.`));
      return;
    }
    const dur = step.duration || {};
    let when = dur.value + (dur.unit ? ' ' + dur.unit : '');
    if (dur.type === 'distance' && dur.unit === 'm') when = dur.value + ' m';
    if (dur.type === 'distance' && dur.unit === 'km') when = dur.value + ' km';
    if (dur.type === 'time' && dur.unit === 'min') when = dur.value + ' min';
    if (dur.type === 'time' && dur.unit === 's') when = dur.value + ' s';
    const t = step.target || {};
    let pace = '';
    if (t.type === 'pace') {
      pace = (t.low && t.high && t.low !== t.high) ? `${t.high}–${t.low}/km` : `${t.low || t.high || ''}/km`;
    } else if (t.type === 'hr_zone') {
      pace = 'HR Z' + t.zone;
    }
    rows.push(`<tr><td>${escapeHtml(step.intensity || '')}</td><td>${escapeHtml(String(when))}</td><td>${escapeHtml(pace)}</td></tr>`);
  });
  return rows;
}

function showSessionSheet(session) {
  const host = $('#session-sheet');
  if (!host || !session) return;
  const wo = session.workout || {};
  const steps = stepRows(wo.steps || []).join('');
  host.classList.remove('hidden');
  host.innerHTML = `
    <h3>${escapeHtml(wo.name || sessionKind(session))}</h3>
    <p class="purpose muted">${escapeHtml(session.purpose || wo.purpose || '')}</p>
    <p>${escapeHtml(session.description || wo.description || '')}</p>
    <table class="session-steps">
      <thead><tr><th>Step</th><th>Duration</th><th>Pace</th></tr></thead>
      <tbody>${steps || '<tr><td colspan="3">No structure.</td></tr>'}</tbody>
    </table>
    <div class="row-actions">
      <button type="button" class="ghost" data-ease="${escapeHtml(session.id)}">Ease</button>
      <button type="button" class="ghost" data-rest="${escapeHtml(session.id)}">Rest</button>
      <button type="button" class="ghost" data-skip="${escapeHtml(session.id)}">Skip</button>
    </div>`;
  host.querySelector('[data-ease]').addEventListener('click', () => patchSession(session.id, 'ease'));
  host.querySelector('[data-rest]').addEventListener('click', () => patchSession(session.id, 'rest'));
  host.querySelector('[data-skip]').addEventListener('click', () => patchSession(session.id, 'skip'));
  host.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function patchSession(id, op) {
  if (!state.plan) return;
  await post(`/api/plan/${state.plan.id}/patch`, { op, id });
  loadPlanTab();
}

function drawProjected(series) {
  const canvas = $('#chart-projected');
  if (!canvas || !window.Chart) return;
  if (state.charts.projected) state.charts.projected.destroy();
  state.charts.projected = new Chart(canvas, {
    type: 'line',
    data: {
      labels: series.map((r) => shortDate(r.date)),
      datasets: [
        { label: 'Load', data: series.map((r) => r.load), borderColor: COLORS.load, backgroundColor: COLORS.load, fill: true, tension: .2 },
        { label: 'ATL', data: series.map((r) => r.atl), borderColor: COLORS.atl, tension: .3, pointRadius: 0 },
        { label: 'CTL', data: series.map((r) => r.ctl), borderColor: COLORS.ctl, tension: .3, pointRadius: 0 },
      ],
    },
    options: baseOptions(),
  });
}

$('#race-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  await post('/api/races', {
    name: $('#race-name').value,
    date: $('#race-date').value,
    distance_m: Number($('#race-distance').value),
    goal_time: $('#race-goal').value,
    priority: $('#race-priority').value,
  });
  $('#race-name').value = '';
  loadPlanTab();
});

$('#plan-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const raceId = $('#plan-race').value;
  if (!raceId) return;
  const btn = event.submitter;
  if (btn) btn.disabled = true;
  try {
    await post('/api/plan/generate', {
      race_id: raceId,
      notes: $('#plan-notes').value,
      extras: ['strength'],
      adjust: Boolean($('#plan-notes').value),
    });
    await loadPlanTab();
  } finally {
    if (btn) btn.disabled = false;
  }
});

$('#btn-activate').addEventListener('click', async () => {
  if (!state.plan) return;
  await post(`/api/plan/${state.plan.id}/activate`);
  await loadPlanTab();
  alert('Queued. The next extension sync writes these workouts to Garmin.');
});

function emptyStep() {
  return { kind: 'step', intensity: 'active', duration: { type: 'time', value: 10, unit: 'min' }, target: { type: 'hr_zone', zone: 2 } };
}

function renderBuilder() {
  const host = $('#wo-steps');
  if (!state.draftSteps) state.draftSteps = [emptyStep()];
  host.innerHTML = state.draftSteps.map((step, i) => stepEditor(step, i)).join('');
  $$('#wo-steps [data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.draftSteps.splice(Number(btn.dataset.remove), 1);
      renderBuilder();
    });
  });
}

function stepEditor(step, i) {
  if (step.kind === 'repeat') {
    return `<div class="repeat-box">
      Repeat <input type="number" min="1" max="20" value="${step.times || 4}" data-rep="${i}"> times
      ${(step.steps || []).map((s, j) => stepEditor(s, `${i}.${j}`)).join('')}
    </div>`;
  }
  const d = step.duration || {};
  const t = step.target || {};
  return `<div class="step-row">
    <select data-f="intensity" data-i="${i}">
      ${['warmup','active','interval','recovery','cooldown','rest'].map((v) =>
        `<option${step.intensity === v ? ' selected' : ''}>${v}</option>`).join('')}
    </select>
    <input type="number" min="1" value="${d.value || 10}" data-f="durval" data-i="${i}">
    <select data-f="durtype" data-i="${i}">
      <option value="time"${d.type === 'time' ? ' selected' : ''}>min</option>
      <option value="distance"${d.type === 'distance' ? ' selected' : ''}>m</option>
      <option value="reps"${d.type === 'reps' ? ' selected' : ''}>reps</option>
    </select>
    <select data-f="targ" data-i="${i}">
      <option value="hr_zone"${t.type === 'hr_zone' ? ' selected' : ''}>HR zone</option>
      <option value="pace"${t.type === 'pace' ? ' selected' : ''}>Pace</option>
      <option value="power_zone"${t.type === 'power_zone' ? ' selected' : ''}>Power zone</option>
      <option value="none"${t.type === 'none' ? ' selected' : ''}>None</option>
    </select>
    <input placeholder="zone / 4:30" value="${t.zone || t.low || ''}" data-f="targval" data-i="${i}">
    <input placeholder="exercise" value="${step.exercise || ''}" data-f="ex" data-i="${i}">
    <button type="button" class="ghost" data-remove="${i}">×</button>
  </div>`;
}

function readBuilder() {
  if (state.draftWorkout && Array.isArray(state.draftWorkout.steps)
      && state.draftWorkout.steps.some((s) => s.kind === 'repeat')) {
    return {
      name: $('#wo-name').value || state.draftWorkout.name || 'Workout',
      sport: $('#wo-sport').value,
      kind: $('#wo-kind').value,
      steps: state.draftWorkout.steps,
    };
  }
  const steps = [];
  $$('#wo-steps .step-row').forEach((row) => {
    const get = (f) => row.querySelector(`[data-f="${f}"]`);
    if (!get('intensity')) return;
    const durType = get('durtype').value;
    const targetType = get('targ').value;
    const targVal = get('targval').value;
    const step = {
      kind: 'step',
      intensity: get('intensity').value,
      duration: { type: durType, value: Number(get('durval').value) || 10,
                  unit: durType === 'time' ? 'min' : (durType === 'distance' ? 'm' : '') },
      target: targetType === 'pace'
        ? { type: 'pace', low: targVal, high: targVal }
        : targetType === 'none' ? { type: 'none' }
        : { type: targetType, zone: Number(targVal) || 2 },
    };
    const ex = get('ex') && get('ex').value.trim();
    if (ex) step.exercise = ex;
    steps.push(step);
  });
  return {
    name: $('#wo-name').value || 'Workout',
    sport: $('#wo-sport').value,
    kind: $('#wo-kind').value,
    steps,
  };
}

$('#wo-add-step').addEventListener('click', () => {
  if (!state.draftSteps) state.draftSteps = [];
  state.draftSteps.push(emptyStep());
  renderBuilder();
});

$('#wo-add-repeat').addEventListener('click', () => {
  if (!state.draftSteps) state.draftSteps = [];
  state.draftSteps.push({
    kind: 'repeat', times: 4,
    steps: [
      { kind: 'step', intensity: 'interval', duration: { type: 'distance', value: 400, unit: 'm' }, target: { type: 'hr_zone', zone: 5 } },
      { kind: 'step', intensity: 'recovery', duration: { type: 'time', value: 90, unit: 's' } },
    ],
  });
  renderBuilder();
});

async function loadWorkouts() {
  if (!state.draftSteps) renderBuilder();
  if (!$('#wo-date').value) $('#wo-date').value = new Date().toISOString().slice(0, 10);
  const body = await api('/api/workouts');
  const rows = (body.workouts || []).map((w) => {
    const wo = w.workout || w;
    return `<tr>
      <td>${escapeHtml(w.name || wo.name || '')}</td>
      <td>${escapeHtml(wo.sport || '')}</td>
      <td>${escapeHtml(wo.kind || '')}</td>
      <td>${wo.est_load || ''}</td>
      <td><button type="button" class="ghost" data-use="${escapeHtml(w.key || w.id || '')}">Use</button></td>
    </tr>`;
  }).join('');
  $('#wo-library').innerHTML = '<thead><tr><th>Name</th><th>Sport</th><th>Kind</th><th>Load</th><th></th></tr></thead>'
    + `<tbody>${rows}</tbody>`;
  state.library = body.workouts || [];
  $$('#wo-library [data-use]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const item = (state.library || []).find((w) => (w.key || w.id) === btn.dataset.use);
      const wo = (item && (item.workout || item)) || null;
      if (!wo) return;
      $('#wo-name').value = wo.name || '';
      $('#wo-sport').value = wo.sport || 'running';
      $('#wo-kind').value = wo.kind || 'easy';
      state.draftSteps = wo.steps || [emptyStep()];
      state.draftWorkout = wo;
      renderBuilder();
    });
  });
}

$('#wo-preview').addEventListener('click', async () => {
  try {
    const body = await post('/api/workout/preview', readBuilder());
    $('#wo-preview-out').textContent =
      (body.description ? body.description + '\n\n' : '') +
      JSON.stringify(body.workout, null, 2);
  } catch (error) {
    $('#wo-preview-out').textContent = error.message;
  }
});

$('#wo-schedule').addEventListener('click', async () => {
  try {
    const body = await post('/api/workout/schedule', {
      ...readBuilder(),
      date: $('#wo-date').value,
    });
    $('#wo-preview-out').textContent = 'Queued for the next sync: ' + (body.session && body.session.date);
  } catch (error) {
    $('#wo-preview-out').textContent = error.message;
  }
});

$('#wo-save').addEventListener('click', async () => {
  try {
    await post('/api/workouts', readBuilder());
    loadWorkouts();
  } catch (error) {
    $('#wo-preview-out').textContent = error.message;
  }
});

async function loadPerformance() {
  const p = await api('/api/performance');
  state.performance = p;
  const dist = p.distribution || {};
  const fos = p.foster || {};
  $('#perf-headline').innerHTML = [
    tile('VDOT', fmt(p.vdot, 1), p.vdot_from && p.vdot_from.mark ? `from ${p.vdot_from.mark}` : ''),
    tile('Critical speed', (p.critical_speed && p.critical_speed.pace) || '--', 'threshold-ish'),
    tile('Easy / hard',
      dist.easy_pct != null ? `${fmt(dist.easy_pct, 0)}/${fmt(dist.hard_pct, 0)}` : '--',
      'last 6 weeks, HR zones'),
    tile('Monotony', fmt(fos.monotony, 2), fos.strain != null ? `strain ${fmt(fos.strain)}` : 'Foster 7d'),
  ].join('');
  const recRows = (p.records || []).map((r) =>
    `<tr><td>${r.mark}</td><td>${r.time}</td><td>${r.pace}</td><td>${r.date || ''}</td></tr>`).join('');
  $('#perf-records').innerHTML = '<thead><tr><th>Mark</th><th>Time</th><th>Pace</th><th>Date</th></tr></thead>'
    + `<tbody>${recRows || '<tr><td colspan="4">Need a few quality runs.</td></tr>'}</tbody>`;
  const predRows = (p.predictions || []).map((r) =>
    `<tr><td>${r.mark}</td><td>${r.vdot_time || '--'}</td><td>${r.riegel_time || '--'}</td><td>${r.garmin || '--'}</td></tr>`).join('');
  $('#perf-pred').innerHTML = '<thead><tr><th>Mark</th><th>VDOT</th><th>Riegel</th><th>Garmin</th></tr></thead>'
    + `<tbody>${predRows}</tbody>`;
  const order = ['easy', 'recovery', 'marathon', 'threshold', 'interval', 'rep', 'goal'];
  const labels = {
    easy: 'Easy (E)', recovery: 'Recovery', marathon: 'Marathon (M)',
    threshold: 'Threshold (T)', interval: 'Interval (I)', rep: 'Repetition (R)',
    goal: 'Goal race pace',
  };
  const bands = (p.paces && p.paces.bands) || {};
  const paceRows = order.map((key) => {
    const b = bands[key] || {};
    const mid = b.mid || (p.paces && p.paces[key]);
    if (!mid) return '';
    const rng = b.high && b.low && b.high !== b.low ? `${b.high}–${b.low}/km` : '';
    return `<div><span>${labels[key]}</span><span>${rng || mid + '/km'}</span></div>`;
  }).join('');
  $('#perf-paces').innerHTML = paceRows
    || '<div><span>Paces</span><span>Need a 5k-ish effort first</span></div>';
  const decRows = (p.efficiency || []).filter((e) => e.decoupling != null).slice(-12)
    .map((e) => `<tr><td>${e.date}</td><td>${e.pace}</td><td>${e.hr}</td><td>${e.decoupling}%</td></tr>`).join('');
  const decTable = $('#perf-decouple');
  if (decTable) {
    decTable.innerHTML = '<thead><tr><th>Date</th><th>Pace</th><th>HR</th><th>Decoupling</th></tr></thead>'
      + `<tbody>${decRows || '<tr><td colspan="4">Need long runs with splits.</td></tr>'}</tbody>`;
  }
  drawPaceCurve(p.curve || []);
  drawEfficiency(p.efficiency || []);
}

function drawPaceCurve(curve) {
  const canvas = $('#chart-curve');
  if (!canvas || !window.Chart) return;
  if (state.charts.curve) state.charts.curve.destroy();
  const paceS = curve.map((r) => {
    if (!r.pace) return null;
    const p = String(r.pace).split(':');
    return Number(p[0]) * 60 + Number(p[1] || 0);
  });
  state.charts.curve = new Chart(canvas, {
    type: 'line',
    data: {
      labels: curve.map((r) => r.mark),
      datasets: [{
        label: 'sec/km',
        data: paceS,
        borderColor: COLORS.atl,
        tension: .25,
        pointRadius: 4,
      }],
    },
    options: baseOptions({
      scales: {
        y: { reverse: true, title: { display: true, text: 'pace (faster up)', color: css('--muted') } },
      },
    }),
  });
}

function drawEfficiency(points) {
  const canvas = $('#chart-efficiency');
  if (!canvas || !window.Chart) return;
  if (state.charts.efficiency) state.charts.efficiency.destroy();
  state.charts.efficiency = new Chart(canvas, {
    type: 'line',
    data: {
      labels: points.map((p) => shortDate(p.date)),
      datasets: [
        { label: 'HR', data: points.map((p) => p.hr), borderColor: COLORS.rhr, tension: .3, pointRadius: 0 },
        { label: 'HR / pace', data: points.map((p) => p.hr_per_pace), borderColor: COLORS.hrv, tension: .3, pointRadius: 0, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        y1: { position: 'right', grid: { display: false } },
      },
    }),
  });
}

const WEEKDAYS = [
  ['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'],
  ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun'],
];

async function loadAthlete() {
  const profile = await api('/api/athlete');
  state.athlete = profile || {};
  const avail = state.athlete.availability || {};
  const defaults = { mon: 0, tue: 60, wed: 60, thu: 60, fri: 60, sat: 90, sun: 90 };
  const host = $('#ath-days');
  if (host) {
    host.innerHTML = WEEKDAYS.map(([key, label]) => {
      const mins = (avail[key] && avail[key].minutes != null)
        ? avail[key].minutes : defaults[key];
      return `<label class="muted">${label}<input type="number" min="0" max="240" data-day="${key}" value="${mins}"></label>`;
    }).join('');
  }
  if ($('#ath-hr-max')) $('#ath-hr-max').value = state.athlete.hr_max || '';
  if ($('#ath-hr-rest')) $('#ath-hr-rest').value = state.athlete.hr_rest || '';
  if ($('#ath-weight')) $('#ath-weight').value = state.athlete.weight_kg || '';
}

const athleteForm = $('#athlete-form');
if (athleteForm) {
  athleteForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const availability = {};
    $$('#ath-days [data-day]').forEach((input) => {
      availability[input.dataset.day] = {
        minutes: Number(input.value) || 0,
        sports: ['running'],
      };
    });
    await api('/api/athlete', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        availability,
        hr_max: Number($('#ath-hr-max').value) || null,
        hr_rest: Number($('#ath-hr-rest').value) || null,
        weight_kg: Number($('#ath-weight').value) || null,
      }),
    });
    const note = $('#ath-saved');
    if (note) note.textContent = 'Saved. New plans will use this.';
  });
}

// ---- boot -------------------------------------------------------------------
function updateHistoryBanner(status) {
  const el = $('#history-banner');
  if (!el) return;
  if (!status || !status.first) {
    el.classList.add('hidden');
    return;
  }
  const first = new Date(`${status.first}T00:00:00`);
  const ageDays = Math.round((Date.now() - first.getTime()) / 86400000);
  if (ageDays >= 500) {
    el.classList.add('hidden');
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML =
    `<strong>History from ${status.first}</strong>` +
    `<span>Open the extension → <em>Load all history</em>. Keep Garmin Connect ` +
    `signed in. This page fills in as older days arrive.</span>`;
}

let historyWatch = null;
function watchIncomingHistory() {
  if (historyWatch) return;
  const started = Date.now();
  let last = `${state.status.days}-${state.status.activities}-${state.status.first}`;
  historyWatch = setInterval(async () => {
    if (Date.now() - started > 20 * 60 * 1000) {
      clearInterval(historyWatch);
      historyWatch = null;
      return;
    }
    try {
      const status = await api('/api/status');
      const sig = `${status.days}-${status.activities}-${status.first}`;
      if (sig === last) return;
      last = sig;
      state.status = status;
      $('#freshness').textContent = status.last ? `updated ${status.ago}` : 'no data yet';
      renderAccount(status);
      updateHistoryBanner(status);
      if (!$('#dashboard-body').classList.contains('hidden')) {
        loadDashboard().catch(() => {});
      }
    } catch {
      // 401 already shows the gate.
    }
  }, 4000);
}

let emptyPoll = null;
function stopEmptyPoll() {
  if (!emptyPoll) return;
  clearInterval(emptyPoll);
  emptyPoll = null;
}

function startEmptyPoll() {
  if (emptyPoll) return;
  emptyPoll = setInterval(async () => {
    try {
      const status = await api('/api/status');
      state.status = status;
      $('#freshness').textContent = status.last
        ? `updated ${status.ago}`
        : 'waiting for Garmin data…';
      if (status.days || status.activities) {
        stopEmptyPoll();
        boot();
      }
    } catch {
      // 401 already shows the sign-in gate.
    }
  }, 2500);
}

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
  const repoAccount = $('#repo-link-account');
  if (repoAccount) repoAccount.href = repo;
  const origin = $('#onboard-origin');
  if (origin) origin.textContent = siteOrigin();
  renderAccount(status);
  updateHistoryBanner(status);
  loadAthlete().catch(() => {});

  if (window.Chart) {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = css('--muted');
  }

  // Nothing pushed yet: the charts would be empty, so explain what to do.
  const empty = !status.days && !status.activities;
  $('#onboarding').classList.toggle('hidden', !empty);
  $('#dashboard-body').classList.toggle('hidden', empty);

  $('#coach-off').classList.toggle('hidden', !!status.coach);
  $('#coach-body').classList.toggle('hidden', !status.coach);

  if (empty) {
    startEmptyPoll();
    return;
  }
  stopEmptyPoll();
  watchIncomingHistory();
  loadDashboard().catch((error) => {
    $('#headline').innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  });
}

bindDropzone($('#onboard-drop'), $('#onboard-file'),
             $('#onboard-import-status'), $('#onboard-browse'));
bindDropzone($('#account-drop'), $('#account-file'),
             $('#account-import-status'), $('#account-browse'));

boot().catch(() => showGate('Could not reach the site.'));
