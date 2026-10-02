/* The headline numbers, split into Load and Recovery so Form is not sitting
   next to Sleep with no context. Shared by Today, Insights and an athlete page. */
import { openActivity } from './activity-detail.js';
import { info } from '../core/glossary.js';
import { t } from '../core/i18n.js';
import { $$, esc, fmt, signed, tile } from '../core/ui.js';

export function formTone(form) {
  if (form === null || form === undefined) return ['', ''];
  if (form < -25) return ['bad', t('metric.deep')];
  if (form < -8) return ['warn', t('metric.building')];
  return ['good', t('metric.fresh')];
}

export function acwrTone(acwr) {
  if (acwr === null || acwr === undefined) return ['', t('metric.acwrHint')];
  if (acwr > 1.5) return ['bad', t('metric.ramping')];
  if (acwr > 1.3) return ['warn', t('metric.above')];
  if (acwr < 0.8) return ['warn', t('metric.detraining')];
  return ['good', t('metric.sweet')];
}

function has(v) {
  return v !== null && v !== undefined;
}

export function loadTiles(h = {}) {
  const [fTone, fNote] = formTone(h.form);
  const [rTone, rNote] = acwrTone(h.acwr);
  return [
    tile(t('metric.form'), fmt(h.form),
      [fNote, has(h.ctl) ? t('metric.fitness', { n: fmt(h.ctl) }) : '', has(h.atl) ? t('metric.fatigue', { n: fmt(h.atl) }) : '']
        .filter(Boolean).join(' · '), fTone, info('form')),
    tile(t('metric.loadRatio'), fmt(h.acwr, 2), rNote, rTone, info('load_ratio')),
    tile(t('metric.week'), `${fmt(h.week_km, 1)} km`,
      t('metric.weekMeta', { n: h.week_sessions || 0, load: fmt(h.week_load) }), '', info('week')),
  ].join('');
}

export function recoveryTiles(h = {}) {
  const hrvTone = h.hrv_delta > 0 ? 'good' : h.hrv_delta < -3 ? 'warn' : '';
  const rhrTone = h.resting_hr_delta > 2 ? 'warn' : h.resting_hr_delta < 0 ? 'good' : '';
  return [
    tile(t('metric.hrv'), fmt(h.hrv), has(h.hrv_delta) ? t('metric.vsBaseline', { n: signed(h.hrv_delta) }) : (h.hrv_status || ''),
      hrvTone, info('hrv')),
    tile(t('metric.rhr'), fmt(h.resting_hr),
      has(h.resting_hr_delta) ? t('metric.vsBaseline', { n: signed(h.resting_hr_delta) }) : '', rhrTone, info('rhr')),
    tile(t('metric.sleep'), fmt(h.sleep_score), h.sleep_h ? t('metric.sleepHours', { n: fmt(h.sleep_h, 1) }) : '', '', info('sleep')),
  ].join('');
}

export function groupedHeadline(h = {}) {
  return `<div class="metric-groups">
    <section class="metric-group">
      <p class="kicker">${esc(t('metric.loadKicker'))}</p>
      <div class="cards">${loadTiles(h)}</div>
    </section>
    <section class="metric-group">
      <p class="kicker">${esc(t('metric.recoveryKicker'))}</p>
      <div class="cards">${recoveryTiles(h)}</div>
    </section>
  </div>`;
}

export function activityRows(activities, limit = 0) {
  const list = limit ? activities.slice(0, limit) : activities;
  return list.map((a) => {
    const km = a.distance_m ? a.distance_m / 1000 : null;
    const label = a.name || a.type || '';
    const openable = a.id != null && a.id !== '';
    const attrs = openable
      ? ` class="clickable" data-id="${esc(a.id)}" tabindex="0" role="button" aria-label="${esc(label)}"`
      : '';
    return `<tr${attrs}>
      <td>${(a.start || '').slice(0, 10)}</td>
      <td>${esc(label)}</td>
      <td>${esc((a.type || '').replace(/_/g, ' '))}</td>
      <td>${a.duration_s ? Math.round(a.duration_s / 60) + "'" : '--'}</td>
      <td>${km ? fmt(km, 1) : '--'}</td>
      <td>${a.avg_hr || '--'}</td>
      <td>${fmt(a.training_load)}</td>
    </tr>`;
  }).join('');
}

export function bindActivityList(root, activities) {
  const byId = new Map((activities || []).map((a) => [String(a.id), a]));
  $$('tr[data-id]', root).forEach((row) => {
    const open = () => {
      const activity = byId.get(row.dataset.id);
      if (activity) openActivity(activity, row);
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        open();
      }
    });
  });
}

export function activityHead() {
  return `<thead><tr><th>${esc(t('act.date'))}</th><th>${esc(t('act.name'))}</th><th>${esc(t('act.sport'))}</th><th>${esc(t('act.time'))}</th>` +
    `<th>${esc(t('act.km'))}</th><th>${esc(t('act.hr'))}</th><th>${esc(t('act.load'))}</th></tr></thead>`;
}
