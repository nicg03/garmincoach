/* The six headline numbers, shared by Today, Insights and an athlete's page. */
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

export function headlineTiles(h = {}) {
  const [fTone, fNote] = formTone(h.form);
  const [rTone, rNote] = acwrTone(h.acwr);
  const hrvTone = h.hrv_delta > 0 ? 'good' : h.hrv_delta < -3 ? 'warn' : '';
  const rhrTone = h.resting_hr_delta > 2 ? 'warn' : h.resting_hr_delta < 0 ? 'good' : '';
  const has = (v) => v !== null && v !== undefined;
  return [
    tile('Form (TSB)', fmt(h.form),
      [fNote, has(h.ctl) ? `CTL ${fmt(h.ctl)}` : '', has(h.atl) ? `ATL ${fmt(h.atl)}` : '']
        .filter(Boolean).join(' · '), fTone, info('tsb')),
    tile('Load ratio', fmt(h.acwr, 2), rNote, rTone, info('ctl')),
    tile('HRV', fmt(h.hrv), has(h.hrv_delta) ? `${signed(h.hrv_delta)} vs baseline` : (h.hrv_status || ''),
      hrvTone, info('hrv')),
    tile('Resting HR', fmt(h.resting_hr),
      has(h.resting_hr_delta) ? `${signed(h.resting_hr_delta)} vs baseline` : '', rhrTone),
    tile('Sleep', fmt(h.sleep_score), h.sleep_h ? `${fmt(h.sleep_h, 1)} h last night` : '', '', info('sleep')),
    tile('This week', `${fmt(h.week_km, 1)} km`,
      `${h.week_sessions || 0} sessions · load ${fmt(h.week_load)}`),
  ].join('');
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