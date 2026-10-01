/* Today: am I ready, what's the session, and what changed. Everything else
   is one click away. */
import { ACTIVITY_HEAD, activityRows, headlineTiles } from '../components/headline.js';
import { api, post } from '../core/api.js';
import { rerender } from '../core/router.js';
import { cached, hasData, invalidate, role, state } from '../core/state.js';
import {
  $, $$, busy, daysUntil, emptyState, esc, fmt, longDate, markdown, signed, skeleton, todayIso, toast,
} from '../core/ui.js';
import { renderOnboarding } from './onboarding.js';

const VERDICT = {
  keep: ['good', 'Ready to train', 'Go ahead with today as planned.'],
  ease: ['warn', 'Take it easy today', ''],
  swap: ['warn', 'Move the hard work', ''],
  rest: ['bad', 'Rest is the better call', ''],
};

const ACTIONS = [
  ['keep', 'Keep as planned'],
  ['ease', 'Make it easy'],
  ['swap', 'Swap days'],
  ['rest', 'Rest'],
];

function historyBanner(status) {
  if (!status.first) return '';
  const ageDays = Math.round((Date.now() - new Date(`${status.first}T00:00:00`).getTime()) / 86400000);
  const g = status.garmin || {};
  if (ageDays >= 500 || (g.connected && g.history_done)) return '';
  const action = g.connected
    ? (g.running ? 'Older history is loading in the background.' : 'Older history loads a little at every sync.')
    : 'Bring in older history from Data sources.';
  return `<div class="banner"><strong>History from ${esc(status.first)}</strong>
    <span class="muted">${action}</span><span class="spacer"></span>
    <a class="btn-link" href="#/settings/sources">Data sources</a></div>`;
}

function deltaLine(h) {
  const parts = [];
  if (h.hrv_delta != null) parts.push(`HRV ${signed(h.hrv_delta)} vs baseline`);
  if (h.resting_hr_delta != null) parts.push(`resting HR ${signed(h.resting_hr_delta)}`);
  if (h.sleep_score != null) parts.push(`sleep ${fmt(h.sleep_score)}`);
  return parts.join(' · ');
}

function sessionBlock(decision) {
  const session = decision && decision.session;
  if (!session) {
    return `<div class="session">
      <p class="kicker">Today's session</p>
      <h3>Nothing planned</h3>
      <p class="muted">Plan a block around a race or put a single workout on your watch.</p>
      <div class="row-actions">
        <a class="ghost small" href="#/training/races">Plan for a race</a>
        <a class="ghost small" href="#/training/workouts">Build a workout</a>
      </div>
    </div>`;
  }
  const wo = session.workout || {};
  const name = wo.name || (session.kind || '').replace(/_/g, ' ') || 'Session';
  const desc = session.description || wo.description || '';
  const km = session.distance_km != null ? `${fmt(session.distance_km, 1)} km` : '';
  const applied = decision.applied;
  return `<div class="session">
    <p class="kicker">Today's session ${session.assigned_by ? '<span class="badge">from your coach</span>' : ''}</p>
    <h3>${esc(name)}</h3>
    <p class="muted">${esc(desc || [km, (wo.targets && wo.targets.work) || ''].filter(Boolean).join(' · '))}</p>
    ${applied ? `<p class="muted">You chose: <strong>${esc(applied)}</strong>.</p>` : ''}
    <div class="row-actions" data-actions>
      ${ACTIONS.map(([key, label]) => `<button type="button" data-act="${key}"
        class="${key === (applied || decision.action) ? 'primary inline' : 'ghost'}">${label}</button>`).join('')}
    </div>
  </div>`;
}

function readinessCard(decision) {
  if (!decision) {
    return `<div class="readiness"><div><p class="kicker">Readiness</p>
      <p class="verdict">No read on today yet</p>
      <p class="muted">Readiness needs a few mornings of HRV and resting heart rate.</p></div></div>`;
  }
  const [tone, verdict, fallback] = VERDICT[decision.action] || VERDICT.keep;
  const h = decision.headline || {};
  return `<section class="readiness ${tone}" aria-label="Readiness">
    <div>
      <p class="kicker">${esc(longDate(todayIso()))}</p>
      <p class="verdict">${verdict}</p>
      <p>${esc(decision.reason || fallback)}</p>
      <p class="muted">${esc(deltaLine(h))}</p>
    </div>
    ${sessionBlock(decision)}
  </section>`;
}

function raceCard(races) {
  const next = (races || []).filter((r) => r.date >= todayIso()).sort((a, b) => a.date.localeCompare(b.date))[0];
  if (!next) {
    return `<div class="card"><div class="card-head"><h2>Next race</h2></div>
      ${emptyState('No race on the calendar', 'Add one and get a plan that builds to it.',
        '<a class="ghost small" href="#/training/races">Add a race</a>')}</div>`;
  }
  const days = daysUntil(next.date);
  return `<div class="card"><div class="card-head"><h2>Next race</h2>
      <a class="btn-link" href="#/training/races">Races</a></div>
    <div class="countdown">${days === 0 ? 'Today' : `${days} ${days === 1 ? 'day' : 'days'}`}</div>
    <p><strong>${esc(next.name)}</strong></p>
    <p class="muted">${esc(longDate(next.date))}${next.distance_m ? ` · ${fmt(next.distance_m / 1000, 1)} km` : ''}
      ${next.goal_time ? ` · goal ${esc(next.goal_time)}` : ''}</p>
  </div>`;
}

function coachNoteCard(mine) {
  const note = mine && (mine.notes || [])[0];
  if (!note) return '';
  return `<div class="card"><div class="card-head"><h2>From your coach</h2>
      <a class="btn-link" href="#/coaching/coach">All notes</a></div>
    <div class="coach-note"><div class="meta"><span class="badge">${esc(note.kind)}</span>
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
    <div class="cards strip">${headlineTiles(metrics.headline || {})}</div>
    <div class="grid-2">
      ${raceCard(races.races)}
      ${status.coach ? `<div class="card"><div class="card-head"><h2>Coach's read</h2>
          <a class="btn-link" href="#/coaching/ai">Ask the coach</a></div>
          <div class="prose clamp" data-brief>${skeleton('line', 3)}</div></div>` : ''}
      ${coachNoteCard(mine)}
    </div>
    <div class="card"><div class="card-head"><h2>Recent activities</h2>
        <a class="btn-link" href="#/insights/activities">All activities</a></div>
      <div class="scroll"><table>${ACTIVITY_HEAD}<tbody>${activityRows(recent, 5) ||
        `<tr><td colspan="7">${status.days
          ? 'Nessuna attività recente — sincronizza'
          : 'Nothing here yet.'}</td></tr>`}</tbody></table></div>
    </div>`;

  $$('[data-act]', root).forEach((button) => {
    button.addEventListener('click', () => busy(button, '…', async () => {
      await post('/api/day/decide', { action: button.dataset.act });
      invalidate('decide', 'plan');
      toast(button.dataset.act === 'keep' ? 'Keeping today as planned' : 'Today updated');
      rerender();
    }));
  });

  const brief = $('[data-brief]', root);
  if (brief) {
    cached('brief', () => api('/api/brief'))
      .then((body) => { brief.innerHTML = markdown(body.text || ''); })
      .catch((error) => { brief.innerHTML = `<p class="muted">${esc(error.message)}</p>`; });
  }
}
