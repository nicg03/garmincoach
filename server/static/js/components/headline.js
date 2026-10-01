/* The headline numbers, split into Load and Recovery so Form is not sitting
   next to Sleep with no context. Shared by Today, Insights and an athlete page. */
import { info } from '../core/glossary.js';
import { esc, fmt, signed, tile } from '../core/ui.js';

export function formTone(form) {
  if (form === null || form === undefined) return ['', ''];
  if (form < -25) return ['bad', 'deep in the hole'];
  if (form < -8) return ['warn', 'building'];
  return ['good', 'fresh'];
}

export function acwrTone(acwr) {
  if (acwr === null || acwr === undefined) return ['', 'acute vs chronic load'];
  if (acwr > 1.5) return ['bad', 'ramping up fast'];
  if (acwr > 1.3) return ['warn', 'above the sweet spot'];
  if (acwr < 0.8) return ['warn', 'detraining'];
  return ['good', 'in the sweet spot'];
}

function has(v) {
  return v !== null && v !== undefined;
}

export function loadTiles(h = {}) {
  const [fTone, fNote] = formTone(h.form);
  const [rTone, rNote] = acwrTone(h.acwr);
  return [
    tile('Form', fmt(h.form),
      [fNote, has(h.ctl) ? `Fitness ${fmt(h.ctl)}` : '', has(h.atl) ? `Fatigue ${fmt(h.atl)}` : '']
        .filter(Boolean).join(' · '), fTone, info('form')),
    tile('Load ratio', fmt(h.acwr, 2), rNote, rTone, info('load_ratio')),
    tile('This week', `${fmt(h.week_km, 1)} km`,
      `${h.week_sessions || 0} sessions · load ${fmt(h.week_load)}`, '', info('week')),
  ].join('');
}

export function recoveryTiles(h = {}) {
  const hrvTone = h.hrv_delta > 0 ? 'good' : h.hrv_delta < -3 ? 'warn' : '';
  const rhrTone = h.resting_hr_delta > 2 ? 'warn' : h.resting_hr_delta < 0 ? 'good' : '';
  return [
    tile('HRV', fmt(h.hrv), has(h.hrv_delta) ? `${signed(h.hrv_delta)} vs baseline` : (h.hrv_status || ''),
      hrvTone, info('hrv')),
    tile('Resting HR', fmt(h.resting_hr),
      has(h.resting_hr_delta) ? `${signed(h.resting_hr_delta)} vs baseline` : '', rhrTone, info('rhr')),
    tile('Sleep', fmt(h.sleep_score), h.sleep_h ? `${fmt(h.sleep_h, 1)} h last night` : '', '', info('sleep')),
  ].join('');
}

export function groupedHeadline(h = {}) {
  return `<div class="metric-groups">
    <section class="metric-group">
      <p class="kicker">Load</p>
      <div class="cards">${loadTiles(h)}</div>
    </section>
    <section class="metric-group">
      <p class="kicker">Recovery</p>
      <div class="cards">${recoveryTiles(h)}</div>
    </section>
  </div>`;
}

export function activityRows(activities, limit = 0) {
  const list = limit ? activities.slice(0, limit) : activities;
  return list.map((a) => {
    const km = a.distance_m ? a.distance_m / 1000 : null;
    return `<tr>
      <td>${(a.start || '').slice(0, 10)}</td>
      <td>${esc(a.name || a.type || '')}</td>
      <td>${esc((a.type || '').replace(/_/g, ' '))}</td>
      <td>${a.duration_s ? Math.round(a.duration_s / 60) + "'" : '--'}</td>
      <td>${km ? fmt(km, 1) : '--'}</td>
      <td>${a.avg_hr || '--'}</td>
      <td>${fmt(a.training_load)}</td>
    </tr>`;
  }).join('');
}

export const ACTIVITY_HEAD = '<thead><tr><th>Date</th><th>Name</th><th>Sport</th><th>Time</th>' +
  '<th>km</th><th>HR</th><th>Load</th></tr></thead>';
