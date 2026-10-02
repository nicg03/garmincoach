/* Insights: the numbers behind Today, grouped by question. How hard have I
   been training (Load), am I absorbing it (Recovery), am I getting faster
   (Performance), and what did I actually do (Activities). */
import { activityHead, activityRows, loadTiles } from '../components/headline.js';
import { api, scoped } from '../core/api.js';
import { showText } from '../core/copy.js';
import { COLORS, PALETTE, axes, baseOptions, draw } from '../core/charts.js';
import { info } from '../core/glossary.js';
import { t } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { cached, hasData, state } from '../core/state.js';
import { $, $$, emptyState, esc, fmt, loadingPage, shortDate, subnav, tile } from '../core/ui.js';

function tabs() {
  return [['load', t('ins.load')], ['recovery', t('ins.recovery')], ['performance', t('ins.performance')], ['activities', t('ins.activities')]];
}
function ranges() {
  return [['30', t('ins.d30')], ['90', t('ins.d90')], ['365', t('ins.d365')], ['all', t('ins.all')]];
}

export async function render(root, ctx = {}) {
  const athleteId = ctx.athleteId || null;
  const opts = { athleteId, key: athleteId || '', readOnly: Boolean(ctx.readOnly) };
  const wanted = (ctx.path || [])[0];
  const items = tabs();
  const sub = items.some(([key]) => key === wanted) ? wanted : 'load';
  const ranged = sub !== 'performance';
  root.innerHTML = `
    <div class="page-head">
      ${subnav(ctx.base || '#/insights', items, sub)}
      ${ranged ? `<div class="chips" role="group" aria-label="${esc(t('ins.range'))}">${ranges().map(([key, label]) =>
        `<button type="button" class="chip" data-range="${key}" aria-pressed="${state.range === key}">${esc(label)}</button>`).join('')}</div>` : ''}
      <a class="btn-link" href="#/guide/${sub === 'activities' ? 'about' : sub}">${esc(t('ins.guide'))}</a>
    </div>
    <div data-sub>${loadingPage()}</div>`;
  $$('[data-range]', root).forEach((chip) => chip.addEventListener('click', () => {
    state.range = chip.dataset.range;
    rerender();
  }));
  const host = $('[data-sub]', root);
  if (!athleteId && !hasData()) {
    host.innerHTML = emptyState(esc(t('ins.noData')), esc(t('ins.noDataHelp')),
      `<a class="primary inline" href="#/settings/sources">${esc(t('ins.connect'))}</a>`);
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
    ${card(t('ins.loadForm'), `<p class="muted">${esc(t('ins.loadFormHelp'))}</p>${canvas('chart-load', true)}`, 'form')}
    ${card(t('ins.weekly'), canvas('chart-weeks'))}`;
  const labels = data.dates.map(shortDate);
  const a = axes();
  draw($('#chart-load', host), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: t('chart.load'), data: data.training.map((r) => r.load), backgroundColor: COLORS.load, borderRadius: 2, order: 3 },
        { type: 'line', label: t('chart.fatigueAtl'), data: data.training.map((r) => r.atl), borderColor: COLORS.atl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: t('chart.fitnessCtl'), data: data.training.map((r) => r.ctl), borderColor: COLORS.ctl, borderWidth: 2, pointRadius: 0, tension: .3 },
        { type: 'line', label: t('chart.form'), data: data.training.map((r) => r.form), borderColor: COLORS.form, borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0, tension: .3, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({ scales: { x: a.x, y: { ...a.y, title: a.title(t('chart.load')) }, y1: { ...a.right, title: a.title(t('chart.form')) } } }),
  });

  const weeks = data.weeks || [];
  const sports = [...new Set(weeks.flatMap((w) => Object.keys(w.by_type || {})))];
  const datasets = sports.map((sport, i) => ({
    type: 'bar',
    label: showText(sport),
    data: weeks.map((w) => ((w.by_type || {})[sport] || {}).hours || 0),
    backgroundColor: PALETTE[i % PALETTE.length],
    stack: 'w',
  }));
  datasets.push({ type: 'line', label: t('chart.load'), data: weeks.map((w) => w.load), borderColor: COLORS.form, borderWidth: 2, pointRadius: 0, tension: .3, yAxisID: 'y1' });
  draw($('#chart-weeks', host), {
    data: { labels: weeks.map((w) => shortDate(w.start)), datasets },
    options: baseOptions({
      scales: {
        x: { ...a.x, stacked: true },
        y: { ...a.y, stacked: true, title: a.title(t('chart.hours')) },
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
      ${tile(t('ins.hrv'), fmt(h.hrv), h.hrv_delta != null ? t('ins.vsBaseline', { n: `${h.hrv_delta > 0 ? '+' : ''}${fmt(h.hrv_delta, 1)}` }) : (h.hrv_status || ''), '', info('hrv'))}
      ${tile(t('ins.rhr'), fmt(h.resting_hr), h.resting_hr_delta != null ? t('ins.vsBaseline', { n: `${h.resting_hr_delta > 0 ? '+' : ''}${fmt(h.resting_hr_delta, 1)}` }) : '', '', info('rhr'))}
      ${tile(t('ins.sleep'), fmt(h.sleep_score), h.sleep_h ? t('ins.sleepHours', { n: fmt(h.sleep_h, 1) }) : '', '', info('sleep'))}
    </div>
    ${card(t('ins.hrvRhr'), `<p class="muted">${esc(t('ins.hrvRhrHelp'))}</p>${canvas('chart-recovery', true)}`, 'hrv')}
    <div class="grid-2">
      ${card(t('ins.sleepCard'), canvas('chart-sleep'), 'sleep')}
      ${card(t('ins.scatterTitle'), `<p class="muted">${esc(t('ins.scatterHelp'))}</p>${canvas('chart-scatter')}`)}
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
        line(t('chart.hrv'), 'hrv', COLORS.hrv),
        line(t('chart.hrvBase'), 'hrv_base', COLORS.hrv, { borderWidth: 1, borderDash: [4, 3] }),
        line(t('chart.rhr'), 'resting_hr', COLORS.rhr, { yAxisID: 'y1' }),
        line(t('chart.rhrBase'), 'resting_hr_base', COLORS.rhr, { borderWidth: 1, borderDash: [4, 3], yAxisID: 'y1' }),
      ],
    },
    options: baseOptions({ scales: { x: a.x, y: { ...a.y, title: a.title(t('chart.hrvMs')) }, y1: { ...a.right, title: a.title(t('chart.bpm')) } } }),
  });
  draw($('#chart-sleep', host), {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: t('chart.deep'), data: data.wellness.map((r) => r.deep_h), backgroundColor: COLORS.deep, stack: 's' },
        { type: 'bar', label: t('chart.light'), data: data.wellness.map((r) => r.light_h), backgroundColor: COLORS.light, stack: 's' },
        { type: 'bar', label: t('chart.rem'), data: data.wellness.map((r) => r.rem_h), backgroundColor: COLORS.rem, stack: 's' },
        { type: 'line', label: t('chart.score'), data: data.wellness.map((r) => r.sleep_score), borderColor: COLORS.score, borderWidth: 2, pointRadius: 0, tension: .3, spanGaps: true, yAxisID: 'y1' },
      ],
    },
    options: baseOptions({
      scales: {
        x: { ...a.x, stacked: true },
        y: { ...a.y, stacked: true, title: a.title(t('chart.hours')) },
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
        x: { grid: { color: a.grid }, border: { display: false }, ticks: a.y.ticks, title: a.title(t('chart.loadDay')) },
        y: { ...a.y, title: a.title(t('chart.hrvMorning')) },
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
    ['easy', t('pace.easyE')], ['recovery', t('pace.recovery')], ['marathon', t('pace.marathon')], ['threshold', t('pace.threshold')],
    ['interval', t('pace.interval')], ['rep', t('pace.rep')], ['goal', t('pace.goalRace')],
  ];
  const needsRuns = `<tr><td colspan="4">${esc(t('ins.needsRuns'))}</td></tr>`;
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
    <h3 class="section-title">${esc(t('ins.currentFitness'))}</h3>
    <div class="cards">
      ${tile(t('train.vdot'), fmt(p.vdot, 1), p.vdot_from && p.vdot_from.mark ? t('ins.vdotFrom', { mark: p.vdot_from.mark }) : '', '', info('vdot'))}
      ${tile(t('ins.cs'), esc((p.critical_speed && p.critical_speed.pace) || '--'), t('ins.csNote'), '', info('cs'))}
    </div>
    <div class="grid-2">
      ${card(t('ins.best'), `<div class="scroll"><table><thead><tr><th>${esc(t('ins.distance'))}</th><th>${esc(t('ins.time'))}</th><th>${esc(t('ins.pace'))}</th><th>${esc(t('ins.date'))}</th></tr></thead>
        <tbody>${records || needsRuns}</tbody></table></div>`)}
      ${card(t('ins.predictions'), `<div class="scroll"><table><thead><tr><th>${esc(t('ins.distance'))}</th><th>VDOT</th><th>Riegel</th><th>Garmin</th></tr></thead>
        <tbody>${predictions || needsRuns}</tbody></table></div>`, 'predictions')}
    </div>
    ${card(t('ins.curve'), `<p class="muted">${esc(t('ins.curveHelp'))}</p>${canvas('chart-curve')}`)}
    <h3 class="section-title">${esc(t('ins.how'))}</h3>
    <div class="cards">
      ${tile(t('ins.easyHard'), dist.easy_pct != null ? `${fmt(dist.easy_pct)}/${fmt(dist.hard_pct)}` : '--', t('ins.easyHardNote'), '', info('polar'))}
      ${tile(t('ins.monotony'), fmt(fos.monotony, 2), fos.strain != null ? t('ins.strain', { n: fmt(fos.strain) }) : t('ins.last7'), '', info('monotony'))}
    </div>
    ${card(t('ins.trainingPaces'), `<div class="rows">${paces || `<div><span>${esc(t('ins.pace'))}</span><span>${esc(t('ins.needEffort'))}</span></div>`}</div>`, 'paces')}
    <h3 class="section-title">${esc(t('ins.aerobic'))}</h3>
    <div class="grid-2">
      ${card(t('ins.efficiency'), `<p class="muted">${esc(t('ins.efficiencyHelp'))}</p>${canvas('chart-efficiency')}`, 'efficiency')}
      ${card(t('ins.decoupling'), `<div class="scroll"><table><thead><tr><th>${esc(t('ins.date'))}</th><th>${esc(t('ins.pace'))}</th><th>${esc(t('ins.hr'))}</th><th>${esc(t('ins.drift'))}</th></tr></thead>
        <tbody>${decoupling || `<tr><td colspan="4">${esc(t('ins.needsSplits'))}</td></tr>`}</tbody></table></div>`, 'decoupling')}
    </div>`;

  const a = axes();
  const curve = p.curve || [];
  draw($('#chart-curve', host), {
    type: 'line',
    data: {
      labels: curve.map((r) => r.mark),
      datasets: [{
        label: t('chart.secKm'),
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
      scales: { x: a.x, y: { ...a.y, reverse: true, title: a.title(t('chart.secKm')) } },
    }),
  });
  const points = p.efficiency || [];
  draw($('#chart-efficiency', host), {
    type: 'line',
    data: {
      labels: points.map((e) => shortDate(e.date)),
      datasets: [
        { label: t('ins.hr'), data: points.map((e) => e.hr), borderColor: COLORS.rhr, tension: .3, pointRadius: 0 },
        { label: t('chart.hrPerPace'), data: points.map((e) => e.hr_per_pace), borderColor: COLORS.hrv, tension: .3, pointRadius: 0, yAxisID: 'y1' },
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
        <h2>${esc(t('ins.activities'))} <span class="muted" data-count></span></h2>
        <div class="composer">
          <input type="search" placeholder="${esc(t('ins.searchPh'))}" aria-label="${esc(t('ins.searchLabel'))}" data-q>
          <select aria-label="${esc(t('ins.sport'))}" data-sport><option value="">${esc(t('ins.allSports'))}</option>
            ${sports.map((s) => `<option value="${esc(s)}">${esc(showText(s))}</option>`).join('')}</select>
        </div>
      </div>
      ${opts.athleteId ? `<p class="muted">${esc(t('ins.recent25'))}</p>` : ''}
      <div class="scroll"><table>${activityHead()}<tbody data-rows></tbody></table></div>
    </div>`;
  const q = $('[data-q]', host);
  const sport = $('[data-sport]', host);
  const paint = () => {
    const needle = q.value.trim().toLowerCase();
    const shown = list.filter((a) => (!sport.value || a.type === sport.value)
      && (!needle || (a.name || '').toLowerCase().includes(needle)));
    $('[data-rows]', host).innerHTML = activityRows(shown) || `<tr><td colspan="7">${esc(t('ins.noMatch'))}</td></tr>`;
    $('[data-count]', host).textContent = `${shown.length}`;
  };
  q.addEventListener('input', paint);
  sport.addEventListener('change', paint);
  paint();
}
