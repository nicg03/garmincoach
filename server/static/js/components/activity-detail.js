/* Detail for one stored activity summary. Only fields that are present are
   shown. Splits and heart-rate zones appear when the summary already has them. */
import { showText } from '../core/copy.js';
import { t } from '../core/i18n.js';
import { $, esc, fmt, longDate, tile } from '../core/ui.js';

function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatHms(seconds) {
  const total = num(seconds);
  if (total === null || total <= 0) return null;
  const rounded = Math.round(total);
  const h = Math.floor(rounded / 3600);
  const m = Math.floor((rounded % 3600) / 60);
  const s = rounded % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatPace(sec) {
  if (sec === null || sec <= 0 || sec > 20 * 60) return null;
  let minutes = Math.floor(sec / 60);
  let seconds = Math.round(sec - minutes * 60);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function sportKind(type) {
  const sport = (type || '').toLowerCase();
  if (/swim/.test(sport)) return 'swim';
  if (/cycl|bik/.test(sport)) return 'speed';
  if (/run|trail|track|treadmill|walk|hik/.test(sport)) return 'pace';
  return 'other';
}

function paceSeconds(distanceM, durationS, minMetres) {
  const metres = num(distanceM);
  const seconds = num(durationS);
  if (!metres || !seconds || metres < minMetres) return null;
  return seconds / (metres / 1000);
}

function movement(activity) {
  const kind = sportKind(activity.type);
  if (kind === 'speed') {
    const secPerKm = paceSeconds(activity.distance_m, activity.duration_s, 200);
    if (!secPerKm) return null;
    return { label: t('act.speed'), value: fmt(3600 / secPerKm, 1), note: t('act.kmh') };
  }
  if (kind === 'swim') {
    const secPerKm = paceSeconds(activity.distance_m, activity.duration_s, 25);
    const pace = secPerKm ? formatPace(secPerKm / 10) : null;
    if (!pace) return null;
    return { label: t('act.swimPace'), value: pace, note: t('act.per100') };
  }
  if (kind === 'pace') {
    const pace = formatPace(paceSeconds(activity.distance_m, activity.duration_s, 200));
    if (!pace) return null;
    return { label: t('act.pace'), value: pace, note: t('act.perKm') };
  }
  return null;
}

function splitMovement(activity, split) {
  const kind = sportKind(activity.type);
  const secPerKm = paceSeconds(split.distance_m, split.duration_s, 1);
  if (!secPerKm) return null;
  if (kind === 'speed') return `${fmt(3600 / secPerKm, 1)}`;
  if (kind === 'swim') return formatPace(secPerKm / 10);
  return formatPace(secPerKm);
}

function clock(start) {
  const time = (start || '').slice(11, 19);
  if (!time || time === '00:00:00') return '';
  return time.slice(0, 5);
}

function decoupling(activity) {
  const usable = (activity.splits || []).filter((s) => num(s.distance_m) && num(s.duration_s));
  if (usable.length < 4) return null;
  const mid = Math.floor(usable.length / 2);
  const pace = (chunk) => {
    const distance = chunk.reduce((sum, s) => sum + s.distance_m, 0);
    const duration = chunk.reduce((sum, s) => sum + s.duration_s, 0);
    return distance ? duration / (distance / 1000) : null;
  };
  const first = pace(usable.slice(0, mid));
  const second = pace(usable.slice(mid));
  if (!first || !second) return null;
  const drift = (second - first) / first * 100;
  const rounded = Math.round(drift * 10) / 10;
  const signed = `${rounded > 0 ? '+' : ''}${rounded.toFixed(1)}%`;
  return signed;
}

function primaryTiles(activity) {
  const tiles = [];
  const duration = formatHms(activity.duration_s);
  if (duration) tiles.push(tile(t('act.duration'), esc(duration), ''));
  const metres = num(activity.distance_m);
  if (metres && metres > 0) {
    tiles.push(tile(t('act.distance'), esc(`${fmt(metres / 1000, 2)} km`), ''));
  }
  const move = movement(activity);
  if (move) tiles.push(tile(move.label, esc(move.value), esc(move.note)));
  const avgHr = num(activity.avg_hr);
  if (avgHr && avgHr > 0) tiles.push(tile(t('act.avgHr'), esc(String(Math.round(avgHr))), esc(t('act.bpm'))));
  const maxHr = num(activity.max_hr);
  if (maxHr && maxHr > 0) tiles.push(tile(t('act.maxHr'), esc(String(Math.round(maxHr))), esc(t('act.bpm'))));
  if (num(activity.training_load) !== null) {
    tiles.push(tile(t('act.load'), esc(fmt(activity.training_load)), ''));
  }
  const gain = num(activity.elevation_gain_m);
  if (gain !== null) tiles.push(tile(t('act.elevGain'), esc(`${Math.round(gain)} m`), ''));
  if (num(activity.calories) !== null) {
    tiles.push(tile(t('act.calories'), esc(fmt(activity.calories)), esc(t('act.kcal'))));
  }
  return tiles.length ? `<div class="cards">${tiles.join('')}</div>` : '';
}

function cadenceNote(type) {
  const kind = sportKind(type);
  if (kind === 'speed') return t('act.rpm');
  if (kind === 'swim') return t('act.strokes');
  if (kind === 'pace') return t('act.spm');
  return '';
}

function secondary(activity) {
  const rows = [];
  const add = (label, value, note) => {
    if (value === null || value === undefined || value === '') return;
    rows.push(`<div><span>${esc(label)}</span><span>${esc(note ? `${value} ${note}` : value)}</span></div>`);
  };
  const te = (value) => {
    const n = num(value);
    return n === null ? null : n.toFixed(1);
  };
  add(t('act.aerobic'), te(activity.aerobic_te));
  add(t('act.anaerobic'), te(activity.anaerobic_te));
  const avgPower = num(activity.avg_power);
  if (avgPower && avgPower > 0) add(t('act.avgPower'), Math.round(avgPower), t('act.watts'));
  const normPower = num(activity.norm_power);
  if (normPower && normPower > 0) add(t('act.normPower'), Math.round(normPower), t('act.watts'));
  const cadence = num(activity.avg_cadence);
  if (cadence && cadence > 0) add(t('act.cadence'), Math.round(cadence), cadenceNote(activity.type));
  const stride = num(activity.avg_stride_cm);
  if (stride && stride > 0) add(t('act.stride'), Math.round(stride), t('act.cm'));
  const vo2 = num(activity.vo2max);
  if (vo2 && vo2 > 0) add(t('act.vo2'), vo2.toFixed(1));
  const maxSpeed = num(activity.max_speed);
  if (maxSpeed && maxSpeed > 0) add(t('act.maxSpeed'), fmt(maxSpeed * 3.6, 1), t('act.kmh'));
  const minHr = num(activity.min_hr);
  if (minHr && minHr > 0) add(t('act.minHr'), Math.round(minHr), t('act.bpm'));
  const loss = num(activity.elevation_loss_m);
  if (loss !== null) add(t('act.elevLoss'), Math.round(loss), 'm');
  return rows.length ? `<div class="rows">${rows.join('')}</div>` : '';
}

function zones(activity) {
  const rows = (activity.hr_zones || []).filter((z) => num(z.secs) > 0);
  if (!rows.length) return '';
  const total = rows.reduce((sum, z) => sum + z.secs, 0);
  const bars = rows.map((z) => {
    const pct = total ? Math.max(2, Math.round(z.secs / total * 100)) : 0;
    return `<div class="zone-row">
      <span>${esc(t('act.zone', { n: z.zone }))}</span>
      <span class="zone-bar" role="presentation"><span style="width:${pct}%"></span></span>
      <span>${esc(formatHms(z.secs))}</span>
    </div>`;
  }).join('');
  return `<h3>${esc(t('act.zones'))}</h3><div class="zone-list">${bars}</div>`;
}

function splits(activity) {
  const rows = (activity.splits || []).filter((s) => num(s.distance_m) || num(s.duration_s));
  if (!rows.length) return '';
  const kind = sportKind(activity.type);
  const moveLabel = kind === 'speed' ? t('act.speed') : t('act.pace');
  const showHr = rows.some((s) => num(s.avg_hr) > 0);
  const showElev = rows.some((s) => num(s.elevation_gain_m) !== null);
  const head = `<th>${esc(t('act.km'))}</th><th>${esc(t('act.time'))}</th><th>${esc(moveLabel)}</th>`
    + (showHr ? `<th>${esc(t('act.hr'))}</th>` : '')
    + (showElev ? `<th>${esc(t('act.elevGain'))}</th>` : '');
  const body = rows.map((s) => {
    const metres = num(s.distance_m);
    const km = metres ? fmt(metres / 1000, 2) : '';
    const hr = num(s.avg_hr);
    const elev = num(s.elevation_gain_m);
    return `<tr>
      <td>${esc(km)}</td>
      <td>${esc(formatHms(s.duration_s) || '')}</td>
      <td>${esc(splitMovement(activity, s) || '')}</td>
      ${showHr ? `<td>${hr && hr > 0 ? esc(String(Math.round(hr))) : ''}</td>` : ''}
      ${showElev ? `<td>${elev !== null ? esc(String(Math.round(elev))) : ''}</td>` : ''}
    </tr>`;
  }).join('');
  const drift = decoupling(activity);
  return `<h3>${esc(t('act.splits'))}</h3>
    ${drift ? `<p class="muted">${esc(t('act.drift', { n: drift }))}</p>` : ''}
    <div class="scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function sportLabel(type) {
  if (!type) return '';
  const label = showText(type);
  return label === type ? type.replace(/_/g, ' ') : label;
}

export function openActivity(activity, opener) {
  if (!activity || document.querySelector('[data-activity-detail]')) return;
  const name = activity.name || sportLabel(activity.type) || t('act.untitled');
  const when = [longDate(activity.start), clock(activity.start)].filter(Boolean).join(' · ');
  const sport = sportLabel(activity.type);
  const meta = [when, sport].filter(Boolean).join(' · ');
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.setAttribute('data-activity-detail', '');
  back.innerHTML = `<div class="modal activity-detail" role="dialog" aria-modal="true" aria-labelledby="activity-title">
    ${meta ? `<p class="muted activity-when">${esc(meta)}</p>` : ''}
    <h2 id="activity-title">${esc(name)}</h2>
    ${primaryTiles(activity)}
    ${secondary(activity)}
    ${zones(activity)}
    ${splits(activity)}
    <div class="row-actions">
      <button type="button" class="ghost" data-close>${esc(t('act.close'))}</button>
    </div>
  </div>`;
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey);
    if (opener && opener.isConnected && opener.focus) opener.focus();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  back.addEventListener('click', (event) => { if (event.target === back) close(); });
  $('[data-close]', back).addEventListener('click', close);
  document.body.appendChild(back);
  const dialog = $('[role="dialog"]', back);
  dialog.tabIndex = -1;
  dialog.focus({ preventScroll: true });
}
