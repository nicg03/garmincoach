/* Today: am I ready, what's the session, and what changed. Everything else
   is one click away. */
import { activityHead, activityRows, bindActivityList, groupedHeadline } from '../components/headline.js';
import { api, post } from '../core/api.js';
import { showText } from '../core/copy.js';
import { info } from '../core/glossary.js';
import { getLocale, t, translatePhrase } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { cached, hasData, invalidate, role, state } from '../core/state.js';
import {
  $, $$, busy, daysUntil, emptyState, esc, fmt, longDate, markdown, signed, skeleton, todayIso, toast,
} from '../core/ui.js';
import { renderOnboarding } from './onboarding.js';

function noteKind(kind) {
  const label = t('note.' + kind);
  return label === 'note.' + kind ? kind : label;
}

function verdict(action) {
  const map = {
    keep: ['good', t('today.ready'), t('today.readyHelp')],
    ease: ['warn', t('today.takeEasy'), ''],
    swap: ['warn', t('today.moveHard'), ''],
    rest: ['bad', t('today.restCall'), ''],
  };
  return map[action] || map.keep;
}

function actions() {
  return [
    ['keep', t('today.keep')],
    ['ease', t('today.ease')],
    ['swap', t('today.swap')],
    ['rest', t('today.rest')],
  ];
}

function historyBanner(status) {
  if (!status.first) return '';
  const ageDays = Math.round((Date.now() - new Date(`${status.first}T00:00:00`).getTime()) / 86400000);
  const g = status.garmin || {};
  if (ageDays >= 500 || (g.connected && g.history_done)) return '';
  const action = g.connected
    ? (g.running ? t('today.historyLoading') : t('today.historySync'))
    : t('today.historySources');
  return `<div class="banner"><strong>${esc(t('today.historyFrom', { date: status.first }))}</strong>
    <span class="muted">${esc(action)}</span><span class="spacer"></span>
    <a class="btn-link" href="#/settings/sources">${esc(t('today.sources'))}</a></div>`;
}

function deltaLine(h) {
  const parts = [];
  if (h.hrv_delta != null) parts.push(t('today.hrvDelta', { n: signed(h.hrv_delta) }));
  if (h.resting_hr_delta != null) parts.push(t('today.rhrDelta', { n: signed(h.resting_hr_delta) }));
  if (h.sleep_score != null) parts.push(t('today.sleepScore', { n: fmt(h.sleep_score) }));
  return parts.join(' · ');
}

function sessionBlock(decision) {
  const session = decision && decision.session;
  if (!session) {
    return `<div class="session">
      <p class="kicker">${esc(t('today.session'))} ${info('session')}</p>
      <h3>${esc(t('today.nothing'))}</h3>
      <p class="muted">${esc(t('today.nothingHelp'))}</p>
      <div class="row-actions">
        <a class="ghost small" href="#/training/races">${esc(t('today.planRace'))}</a>
        <a class="ghost small" href="#/training/workouts">${esc(t('today.build'))}</a>
      </div>
    </div>`;
  }
  const wo = session.workout || {};
  const name = showText(wo.name) || showText(session.kind) || (session.kind || '').replace(/_/g, ' ') || t('today.sessionFallback');
  const desc = showText(session.description || wo.description || '');
  const km = session.distance_km != null ? `${fmt(session.distance_km, 1)} km` : '';
  const applied = decision.applied;
  const chosen = applied ? (t('today.' + applied) === 'today.' + applied ? applied : t('today.' + applied)) : '';
  return `<div class="session">
    <p class="kicker">${esc(t('today.session'))} ${info('session')} ${session.assigned_by ? `<span class="badge">${esc(t('today.fromCoach'))}</span>` : ''}</p>
    <h3>${esc(name)}</h3>
    <p class="muted">${esc(desc || [km, (wo.targets && wo.targets.work) || ''].filter(Boolean).join(' · '))}</p>
    ${applied ? `<p class="muted">${esc(t('today.youChose'))} <strong>${esc(chosen)}</strong>.</p>` : ''}
    <div class="row-actions" data-actions>
      ${actions().map(([key, label]) => `<button type="button" data-act="${key}"
        class="${key === (applied || decision.action) ? 'primary inline' : 'ghost'}">${label}</button>`).join('')}
    </div>
  </div>`;
}

function readinessCard(decision) {
  if (!decision) {
    return `<div class="readiness"><div>
      <p class="kicker">${esc(t('today.readiness'))} ${info('readiness')}</p>
      <p class="verdict">${esc(t('today.noRead'))}</p>
      <p class="muted">${esc(t('today.noReadHelp'))}</p></div></div>`;
  }
  const [tone, label, fallback] = verdict(decision.action);
  const h = decision.headline || {};
  return `<section class="readiness ${tone}" aria-label="${esc(t('today.readiness'))}">
    <div>
      <p class="kicker">${esc(longDate(todayIso()))} ${info('readiness')}</p>
      <p class="verdict">${esc(label)}</p>
      <p>${esc(translatePhrase(decision.reason) || fallback)}</p>
      <p class="muted">${esc(deltaLine(h))}</p>
    </div>
    ${sessionBlock(decision)}
  </section>`;
}

function raceCard(races) {
  const next = (races || []).filter((r) => r.date >= todayIso()).sort((a, b) => a.date.localeCompare(b.date))[0];
  if (!next) {
    return `<div class="card"><div class="card-head"><h2>${esc(t('today.nextRace'))}</h2></div>
      ${emptyState(esc(t('today.noRace')), esc(t('today.noRaceHelp')),
        `<a class="ghost small" href="#/training/races">${esc(t('today.addRace'))}</a>`)}</div>`;
  }
  const days = daysUntil(next.date);
  return `<div class="card"><div class="card-head"><h2>${esc(t('today.nextRace'))}</h2>
      <a class="btn-link" href="#/training/races">${esc(t('today.races'))}</a></div>
    <div class="countdown">${days === 0 ? esc(t('time.today')) : esc(days === 1 ? t('time.day', { n: days }) : t('time.days', { n: days }))}</div>
    <p><strong>${esc(next.name)}</strong></p>
    <p class="muted">${esc(longDate(next.date))}${next.distance_m ? ` · ${fmt(next.distance_m / 1000, 1)} km` : ''}
      ${next.goal_time ? ` · ${esc(t('today.goal', { time: next.goal_time }))}` : ''}</p>
  </div>`;
}

function coachNoteCard(mine) {
  const note = mine && (mine.notes || [])[0];
  if (!note) return '';
  return `<div class="card"><div class="card-head"><h2>${esc(t('today.fromCoachTitle'))}</h2>
      <a class="btn-link" href="#/coaching/coach">${esc(t('today.allNotes'))}</a></div>
    <div class="coach-note"><div class="meta"><span class="badge">${esc(noteKind(note.kind))}</span>
      ${esc(note.coach_email)} · ${esc(note.created)}</div>${esc(note.text)}</div></div>`;
}

export async function render(root) {
  const status = state.status;
  if (!hasData(status)) {
    renderOnboarding(root);
    return;
  }
  const athlete = role() === 'athlete';
  const [metrics, decision, races, mine] = await Promise.all([
    cached('metrics::90', () => api('/api/metrics?days=90')),
    cached('decide', () => api('/api/day/decide')).catch(() => null),
    cached('races:', () => api('/api/races')).catch(() => ({ races: [] })),
    athlete ? cached('mine', () => api('/api/coaching/mine')).catch(() => null) : null,
  ]);
  const recent = metrics.recent_activities || [];

  root.innerHTML = `
    ${historyBanner(status)}
    ${readinessCard(decision)}
    ${groupedHeadline(metrics.headline || {})}
    <div class="grid-2">
      ${raceCard(races.races)}
      ${status.coach ? `<div class="card"><div class="card-head"><h2>${esc(t('today.coachRead'))}</h2>
          <a class="btn-link" href="#/coaching/ai">${esc(t('today.ask'))}</a></div>
          <div class="prose clamp" data-brief>${skeleton('line', 3)}</div></div>` : ''}
      ${coachNoteCard(mine)}
    </div>
    <div class="card"><div class="card-head"><h2>${esc(t('today.recent'))}</h2>
        <a class="btn-link" href="#/insights/activities">${esc(t('today.allActivities'))}</a></div>
      <div class="scroll"><table>${activityHead()}<tbody>${activityRows(recent, 5) ||
        `<tr><td colspan="7">${status.days
          ? esc(t('today.noActivities'))
          : esc(t('today.empty'))}</td></tr>`}</tbody></table></div>
    </div>`;

  bindActivityList(root, recent.slice(0, 5));

  $$('[data-act]', root).forEach((button) => {
    button.addEventListener('click', () => busy(button, '…', async () => {
      await post('/api/day/decide', { action: button.dataset.act });
      invalidate('decide', 'plan');
      toast(button.dataset.act === 'keep' ? t('today.kept') : t('today.updated'));
      rerender();
    }));
  });

  const brief = $('[data-brief]', root);
  if (brief) {
    const lang = getLocale();
    cached(`brief:${lang}`, () => api(`/api/brief?lang=${encodeURIComponent(lang)}`))
      .then((body) => { brief.innerHTML = markdown(body.text || ''); })
      .catch((error) => { brief.innerHTML = `<p class="muted">${esc(error.message)}</p>`; });
  }
}
