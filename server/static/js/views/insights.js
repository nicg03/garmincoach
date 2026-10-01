/* Insights: the numbers behind Today, grouped by question. How hard have I
   been training (Load), am I absorbing it (Recovery), am I getting faster
   (Performance), and what did I actually do (Activities). */
import { ACTIVITY_HEAD, activityRows, loadTiles } from '../components/headline.js';
import { api, scoped } from '../core/api.js';
import { COLORS, PALETTE, axes, baseOptions, draw } from '../core/charts.js';
import { info } from '../core/glossary.js';
import { rerender } from '../core/router.js';
import { cached, hasData, state } from '../core/state.js';
import { $, $$, emptyState, esc, fmt, loadingPage, shortDate, subnav, tile } from '../core/ui.js';

const TABS = [['load', 'Load'], ['recovery', 'Recovery'], ['performance', 'Performance'], ['activities', 'Activities']];
const RANGES = [['30', '30 days'], ['90', '90 days'], ['365', '1 year'], ['all', 'All']];

export async function render(root, ctx = {}) {
  const athleteId = ctx.athleteId || null;
  const opts = { athleteId, key: athleteId || '', readOnly: Boolean(ctx.readOnly) };
  const wanted = (ctx.path || [])[0];
  const sub = TABS.some(([key]) => key === wanted) ? wanted : 'load';
  const ranged = sub !== 'performance';
  root.innerHTML = `
    <div class="page-head">
      ${subnav(ctx.base || '#/insights', TABS, sub)}
      ${ranged ? `<div class="chips" role="group" aria-label="Time range">${RANGES.map(([key, label]) =>
        `<button type="button" class="chip" data-range="${key}" aria-pressed="${state.range === key}">${label}</button>`).join('')}</div>` : ''}
      <a class="btn-link" href="#/guide/${sub === 'activities' ? 'about' : sub}">Guide</a>
    </div>
    <div data-sub>${loadingPage()}</div>`;
  $$('[data-range]', root).forEach((chip) => chip.addEventListener('click', () => {
    state.range = chip.dataset.range;
    rerender();
  }));
  const host = $('[data-sub]', root);
  if (!athleteId && !hasData()) {
    host.innerHTML = emptyState('No data yet', 'Connect Garmin and your charts appear here.',
      '<a class="primary inline" href="#/settings/sources">Connect Garmin</a>');
    return;
  }
  if (sub === 'performance') return performance(host, opts);
  if (sub === 'activities') return activities(host, opts);
  const data = await loadMetrics(opts);
  if (sub === 'recovery') return recovery(host, data);
  return load(host, data);
}

function loadMetrics(opts) {
  const query = state.range === 'all' ? 'days=3650' : `days=${state.range}`;
  return cached(`metrics:${opts.key}:${state.range}`, () => api(scoped(`/api/metrics?${query}`, opts.athleteId)));
}

function card(title, body, infoKey = '', extra = '') {
  return `<div class="card"><div class="card-head"><h2>${title} ${infoKey ? info(infoKey) : ''}</h2>${extra}</div>${body}</div>`;
}

function canvas(id, tall = false) {
  return `<div class="chart${tall ? ' tall' : ''}"><canvas id="${id}"></canvas></div>`;
}

// ---- load ---------------------------------------------------------------------
function load(host, data) {
  host.innerHTML = `
    <div class="cards">${loadTiles(data.headline || {})}</div>
    ${card('Load and form', `<p class="muted">Bars are daily load. Fitness is the slow line,
      fatigue the fast one; form is the gap between them.</p>${canvas('chart-load', true)}`, 'form')}
    ${card('Weekly volume', canvas('chart-weeks'))}`;
  const labels = data.dates.map(shortDate);
  const a = axes();
  draw($('#chart-load', host), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Load', data: data.training.map((r) => r.load), backgroundColor: COLORS.load, borderRadius: 2, order: 3 },
        { type: 'line', label: 'Fatigue (ATL 7d)', data: data.training.map((r) => r.atl), borderColor: COLORS.atl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: 'Fitness (CTL 42d)', data: data.training.map((r) => r.ctl), borderColor: COLORS.ctl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: 'Form', data: data.training.map((r) => r.form), borderColor: COLORS.form, borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, tension: .3, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({ scales: { x: a.x, y: { ...a.y, title: a.title('load') }, y1: { ...a.right, title: a.title('form') } } }),
  });

  const weeks = data.weeks || [];
  const sports = [...new Set(weeks.flatMap((w) => Object.keys(w.by_type || {})))];
  const datasets = sports.map((sport, i) => ({
    type: 'bar',
    label: sport.replace(/_/g, ' '),
    data: weeks.map((w) => ((w.by_type || {})[sport] || {}).hours || 0),
    backgroundColor: PALETTE[i % PALETTE.length],
    stack: 'w',
  }));
  datasets.push({ type: 'line', label: 'Load', data: weeks.map((w) => w.load), borderColor: COLORS.form, borderWidth: 2, pointRadius: 0, tension: .3, yAxisID: 'y1' });
  draw($('#chart-weeks', host), {
    data: { labels: weeks.map((w) => shortDate(w.start)), datasets },
    options: baseOptions({
      scales: {
        x: { ...a.x, stacked: true },
        y: { ...a.y, stacked: true, title: a.title('hours') },
        y1: a.right,
      },
    }),
  });
}

// ---- recovery -----------------------------------------------------------------
function recovery(host, data) {
  const h = data.headline || {};
  host.innerHTML = `
    <div class="cards">
      ${tile('HRV', fmt(h.hrv), h.hrv_delta != null ? `${h.hrv_delta > 0 ? '+' : ''}${fmt(h.hrv_delta, 1)} vs baseline` : (h.hrv_status || ''), '', info('hrv'))}
      ${tile('Resting HR', fmt(h.resting_hr), h.resting_hr_delta != null ? `${h.resting_hr_delta > 0 ? '+' : ''}${fmt(h.resting_hr_delta, 1)} vs baseline` : '', '', info('rhr'))}
      ${tile('Sleep', fmt(h.sleep_score), h.sleep_h ? `${fmt(h.sleep_h, 1)} h last night` : '', '', info('sleep'))}
    </div>
    ${card('HRV and resting heart rate', `<p class="muted">Solid lines are daily values, dashed lines your
      rolling baseline. HRV below and resting HR above baseline both say "not recovered".</p>${canvas('chart-recovery', true)}`, 'hrv')}
    <div class="grid-2">
      ${card('Sleep', canvas('chart-sleep'), 'sleep')}
      ${card('Does hard training cost you HRV?', `<p class="muted">Each dot is a day: its load against
        the next morning's HRV.</p>${canvas('chart-scatter')}`)}
    </div>`;
  const labels = data.dates.map(shortDate);
  const a = axes();
  const line = (label, key, color, extra = {}) => ({
    label, data: data.wellness.map((r) => r[key]), borderColor: color, borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true, ...extra,
  });
  draw($('#chart-recovery', host), {
    type: 'line',
    data: {
      labels,
      datasets: [
        line('HRV', 'hrv', COLORS.hrv),
        line('HRV baseline', 'hrv_base', COLORS.hrv, { borderWidth: 1, borderDash: [4, 3] }),
        line('Resting HR', 'resting_hr', COLORS.rhr, { yAxisID: 'y1' }),
        line('RHR baseline', 'resting_hr_base', COLORS.rhr, { borderWidth: 1, borderDash: [4, 3], yAxisID: 'y1' }),
      ],
    },
    options: baseOptions({ scales: { x: a.x, y: { ...a.y, title: a.title('HRV (ms)') }, y1: { ...a.right, title: a.title('bpm') } } }),
  });
  draw($('#chart-sleep', host), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Deep', data: data.wellness.map((r) => r.deep_h), backgroundColor: COLORS.deep, stack: 's' },
        { type: 'bar', label: 'Light', data: data.wellness.map((r) => r.light_h), backgroundColor: COLORS.light, stack: 's' },
        { type: 'bar', label: 'REM', data: data.wellness.map((r) => r.rem_h), backgroundColor: COLORS.rem, stack: 's' },
        { type: 'line', label: 'Score', data: data.wellness.map((r) => r.sleep_score), borderColor: COLORS.score, borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        x: { ...a.x, stacked: true },
        y: { ...a.y, stacked: true, title: a.title('hours') },
        y1: { ...a.right, min: 0, max: 100 },
      },
    }),
  });
  draw($('#chart-scatter', host), {
    type: 'scatter',
    data: { datasets: [{ label: 'day', data: (data.load_vs_hrv || []).map((p) => ({ x: p.load, y: p.next_hrv })), backgroundColor: COLORS.hrv, pointRadius: 4 }] },
    options: baseOptions({
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: a.grid }, border: { display: false }, ticks: a.y.ticks, title: a.title('training load that day') },
        y: { ...a.y, title: a.title('HRV next morning') },
      },
    }),
  });
}

// ---- performance ----------------------------------------------------------------
async function performance(host, opts) {
  const p = await cached(`performance:${opts.key}`, () => api(scoped('/api/performance', opts.athleteId)));
  const dist = p.distribution || {};
  const fos = p.foster || {};
  const records = (p.records || []).map((r) =>
    `<tr><td>${esc(r.mark)}</td><td>${esc(r.time)}</td><td>${esc(r.pace)}</td><td>${esc(r.date || '')}</td></tr>`).join('');
  const predictions = (p.predictions || []).map((r) =>
    `<tr><td>${esc(r.mark)}</td><td>${esc(r.vdot_time || '--')}</td><td>${esc(r.riegel_time || '--')}</td><td>${esc(r.garmin || '--')}</td></tr>`).join('');
  const order = [
    ['easy', 'Easy (E)'], ['recovery', 'Recovery'], ['marathon', 'Marathon (M)'], ['threshold', 'Threshold (T)'],
    ['interval', 'Interval (I)'], ['rep', 'Repetition (R)'], ['goal', 'Goal race pace'],
  ];
  const bands = (p.paces && p.paces.bands) || {};
  const paces = order.map(([key, label]) => {
    const b = bands[key] || {};
    const mid = b.mid || (p.paces && p.paces[key]);
    if (!mid) return '';
    const range = b.high && b.low && b.high !== b.low ? `${b.high}–${b.low}/km` : '';
    return `<div><span>${label}</span><span>${esc(range || mid + '/km')}</span></div>`;
  }).join('');
  const decoupling = (p.efficiency || []).filter((e) => e.decoupling != null).slice(-12).reverse()
    .map((e) => `<tr><td>${esc(e.date)}</td><td>${esc(e.pace)}</td><td>${esc(e.hr)}</td><td>${esc(e.decoupling)}%</td></tr>`).join('');

  host.innerHTML = `
    <h3 class="section-title">Current fitness</h3>
    <div class="cards">
      ${tile('VDOT', fmt(p.vdot, 1), p.vdot_from && p.vdot_from.mark ? `from your ${p.vdot_from.mark}` : '', '', info('vdot'))}
      ${tile('Critical speed', esc((p.critical_speed && p.critical_speed.pace) || '--'), 'about threshold pace', '', info('cs'))}
    </div>
    <div class="grid-2">
      ${card('Best efforts', `<div class="scroll"><table><thead><tr><th>Distance</th><th>Time</th><th>Pace</th><th>Date</th></tr></thead>
        <tbody>${records || '<tr><td colspan="4">Needs a few hard runs first.</td></tr>'}</tbody></table></div>`)}
      ${card('Race predictions', `<div class="scroll"><table><thead><tr><th>Distance</th><th>VDOT</th><th>Riegel</th><th>Garmin</th></tr></thead>
        <tbody>${predictions || '<tr><td colspan="4">Needs a few hard runs first.</td></tr>'}</tbody></table></div>`, 'predictions')}
    </div>
    ${card('Pace curve', `<p class="muted">Your best pace at each distance. Higher is faster.</p>${canvas('chart-curve')}`)}
    <h3 class="section-title">How you train</h3>
    <div class="cards">
      ${tile('Easy vs hard', dist.easy_pct != null ? `${fmt(dist.easy_pct)}/${fmt(dist.hard_pct)}` : '--', 'last 6 weeks, by HR zone · 80/20', '', info('polar'))}
      ${tile('Monotony', fmt(fos.monotony, 2), fos.strain != null ? `strain ${fmt(fos.strain)}` : 'last 7 days', '', info('monotony'))}
    </div>
    ${card('Training paces', `<div class="rows">${paces || '<div><span>Paces</span><span>Need a 5k-ish effort first</span></div>'}</div>`, 'paces')}
    <h3 class="section-title">Aerobic work</h3>
    <div class="grid-2">
      ${card('Aerobic efficiency', `<p class="muted">Heart rate per unit of pace on steady runs. Falling means fitter.</p>${canvas('chart-efficiency')}`, 'efficiency')}
      ${card('Long-run decoupling', `<div class="scroll"><table><thead><tr><th>Date</th><th>Pace</th><th>HR</th><th>Drift</th></tr></thead>
        <tbody>${decoupling || '<tr><td colspan="4">Needs long runs with splits.</td></tr>'}</tbody></table></div>`, 'decoupling')}
    </div>`;

  const a = axes();
  const curve = p.curve || [];
  draw($('#chart-curve', host), {
    type: 'line',
    data: {
      labels: curve.map((r) => r.mark),
      datasets: [{
        label: 'sec/km',
        data: curve.map((r) => {
          if (!r.pace) return null;
          const [m, s] = String(r.pace).split(':');
          return Number(m) * 60 + Number(s || 0);
        }),
        borderColor: COLORS.atl, tension: .25, pointRadius: 4,
      }],
    },
    options: baseOptions({
      plugins: { legend: { display: false } },
      scales: { x: a.x, y: { ...a.y, reverse: true, title: a.title('sec/km') } },
    }),
  });
  const points = p.efficiency || [];
  draw($('#chart-efficiency', host), {
    type: 'line',
    data: {
      labels: points.map((e) => shortDate(e.date)),
      datasets: [
        { label: 'HR', data: points.map((e) => e.hr), borderColor: COLORS.rhr, tension: .3, pointRadius: 0 },
        { label: 'HR / pace', data: points.map((e) => e.hr_per_pace), borderColor: COLORS.hrv, tension: .3, pointRadius: 0, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({ scales: { x: a.x, y: a.y, y1: a.right } }),
  });
}

// ---- activities -----------------------------------------------------------------
async function activities(host, opts) {
  const days = state.range === 'all' ? 3650 : Number(state.range);
  let list;
  if (opts.athleteId) {
    const data = await loadMetrics(opts);
    list = data.recent_activities || [];
  } else {
    const body = await cached(`activities:${days}`, () => api(`/api/activities?days=${days}`));
    list = (body.activities || []).slice().reverse();
  }
  const sports = [...new Set(list.map((a) => a.type).filter(Boolean))].sort();
  host.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Activities <span class="muted" data-count></span></h2>
        <div class="composer">
          <input type="search" placeholder="Search by name" aria-label="Search activities" data-q>
          <select aria-label="Sport" data-sport><option value="">All sports</option>
            ${sports.map((s) => `<option value="${esc(s)}">${esc(s.replace(/_/g, ' '))}</option>`).join('')}</select>
        </div>
      </div>
      ${opts.athleteId ? '<p class="muted">The 25 most recent activities.</p>' : ''}
      <div class="scroll"><table>${ACTIVITY_HEAD}<tbody data-rows></tbody></table></div>
    </div>`;
  const q = $('[data-q]', host);
  const sport = $('[data-sport]', host);
  const paint = () => {
    const needle = q.value.trim().toLowerCase();
    const shown = list.filter((a) => (!sport.value || a.type === sport.value)
      && (!needle || (a.name || '').toLowerCase().includes(needle)));
    $('[data-rows]', host).innerHTML = activityRows(shown) || '<tr><td colspan="7">Nothing matches.</td></tr>';
    $('[data-count]', host).textContent = `${shown.length}`;
  };
  q.addEventListener('input', paint);
  sport.addEventListener('change', paint);
  paint();
}
