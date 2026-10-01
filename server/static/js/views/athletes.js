/* Coach area: who needs attention (roster), requests waiting (inbox), and an
   athlete's page that reuses Training and Insights read-only. */
import { acwrTone, formTone } from '../components/headline.js';
import { api, post } from '../core/api.js';
import { info } from '../core/glossary.js';
import { rerender } from '../core/router.js';
import { cached, emit, invalidate } from '../core/state.js';
import {
  $, $$, busy, emptyState, esc, fmt, loadingPage, longDate, shortDate, signed, subnav, tile, toast,
} from '../core/ui.js';
import * as insights from './insights.js';
import * as training from './training.js';

const DETAIL_TABS = [['overview', 'Overview'], ['training', 'Training'], ['insights', 'Insights'], ['notes', 'Notes']];

export async function render(root, ctx = {}) {
  const [id, sub, ...rest] = ctx.path || [];
  if (id && /^\d+$/.test(id)) return detail(root, Number(id), sub, rest);
  return roster(root);
}

// ---- roster ---------------------------------------------------------------------
const TONE = { good: 'ok', warn: 'warn', bad: 'bad', '': '' };

function flag(tone, label) {
  return `<span class="flag ${TONE[tone] || ''}" title="${esc(label)}"></span>`;
}

function hrvTone(delta) {
  if (delta === null || delta === undefined) return '';
  if (delta <= -5) return 'bad';
  if (delta < -2) return 'warn';
  return 'good';
}

function daysSince(iso) {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso + 'T00:00:00').getTime()) / 86400000);
}

async function roster(root) {
  const [inbox, body] = await Promise.all([
    api('/api/coaching/inbox'),
    cached('roster', () => api('/api/coaching/athletes')),
  ]);
  const requests = inbox.requests || [];
  const athletes = body.athletes || [];
  const rowsHtml = athletes.map((a) => {
    const [fTone, fNote] = formTone(a.form);
    const [rTone, rNote] = acwrTone(a.acwr);
    const hTone = hrvTone(a.hrv_delta);
    const gap = daysSince(a.last);
    const lastTone = gap === null ? 'bad' : gap > 3 ? 'warn' : '';
    return `<tr class="clickable" data-aid="${a.id}" tabindex="0">
      <td class="left"><div class="who">${esc(a.email)}</div>
        <div class="sub">${a.last_activity ? esc(`${(a.last_activity.type || 'activity').replace(/_/g, ' ')} ${shortDate((a.last_activity.start || '').slice(0, 10))}`) : 'no recent activity'}</div></td>
      <td>${flag(fTone, fNote)} ${fmt(a.form)}</td>
      <td>${flag(rTone, rNote)} ${fmt(a.acwr, 2)}</td>
      <td>${flag(hTone, 'HRV vs baseline')} ${a.hrv_delta != null ? signed(a.hrv_delta) : '--'}</td>
      <td>${fmt(a.week_km, 1)}</td>
      <td><span class="${lastTone ? `badge ${lastTone}` : ''}">${a.last ? (gap === 0 ? 'today' : gap === 1 ? 'yesterday' : `${gap} days ago`) : 'never'}</span></td>
      <td>${a.next_race ? `${esc(a.next_race.name)}<div class="sub">${esc(longDate(a.next_race.date))}</div>` : '<span class="muted">—</span>'}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    ${requests.length ? `<div class="card">
      <div class="card-head"><h2>Requests <span class="badge warn">${requests.length}</span></h2></div>
      ${requests.map((r) => `<div class="list-row"><span>${esc(r.athlete_email)} wants you as their coach</span>
        <span class="row-actions">
          <button type="button" class="primary inline" data-accept="${r.id}">Accept</button>
          <button type="button" class="ghost" data-reject="${r.id}">Decline</button>
        </span></div>`).join('')}
    </div>` : ''}
    <div class="card">
      <div class="card-head"><h2>Your athletes</h2><span class="muted">${athletes.length}</span></div>
      ${athletes.length ? `<div class="scroll"><table class="roster">
        <thead><tr><th>Athlete</th><th>Form</th><th>Load ratio</th><th>HRV Δ</th><th>km this week</th>
          <th>Last data</th><th>Next race</th></tr></thead>
        <tbody>${rowsHtml}</tbody></table></div>
        <p class="muted" style="margin-top:10px">Dots: green is fine, amber worth a look, red needs attention.</p>`
        : emptyState('No athletes yet', 'Athletes add you from Coaching → My coach using your email. Their request shows up here.')}
    </div>`;

  const decide = (button, verb) => busy(button, '…', async () => {
    await post(`/api/coaching/${button.dataset[verb]}/${verb}`);
    invalidate('roster');
    emit('inbox');
    toast(verb === 'accept' ? 'Athlete added' : 'Request declined');
    rerender();
  });
  $$('[data-accept]', root).forEach((b) => b.addEventListener('click', () => decide(b, 'accept')));
  $$('[data-reject]', root).forEach((b) => b.addEventListener('click', () => decide(b, 'reject')));
  $$('[data-aid]', root).forEach((row) => {
    const open = () => { location.hash = `#/athletes/${row.dataset.aid}/overview`; };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
  });
}

// ---- athlete detail ---------------------------------------------------------------
async function detail(root, id, sub, rest) {
  const tab = DETAIL_TABS.some(([key]) => key === sub) ? sub : 'overview';
  const body = await cached(`athlete:${id}`, () => api(`/api/coaching/athletes/${id}`));
  const base = `#/athletes/${id}`;
  root.innerHTML = `
    <a class="btn-link" href="#/athletes">← All athletes</a>
    <div class="athlete-head"><h2>${esc(body.email || 'Athlete')}</h2>
      <span class="muted">${body.last ? `data through ${esc(longDate(body.last))}` : 'no Garmin data yet'}</span></div>
    ${subnav(base, DETAIL_TABS, tab)}
    <div data-detail>${loadingPage()}</div>`;
  const host = $('[data-detail]', root);
  const readOnlyNote = '<p class="read-only">You\'re viewing this athlete\'s data. Only they can change their plan.</p>';

  if (tab === 'training') {
    host.insertAdjacentHTML('beforebegin', readOnlyNote);
    return training.render(host, { path: rest, athleteId: id, readOnly: true, base: `${base}/training` });
  }
  if (tab === 'insights') {
    return insights.render(host, { path: rest, athleteId: id, readOnly: true, base: `${base}/insights` });
  }
  if (tab === 'notes') return notes(host, id);
  return overview(host, body, id);
}

function overview(host, body, id) {
  const [fTone, fNote] = formTone(body.form);
  const [rTone, rNote] = acwrTone(body.acwr);
  const races = body.races || [];
  const goals = (body.profile && body.profile.notes) || body.goals || '';
  host.innerHTML = `
    <div class="cards">
      ${tile('Fitness', fmt(body.ctl), '', '', info('fitness'))}
      ${tile('Form', fmt(body.form), fNote, fTone, info('form'))}
      ${tile('Load ratio', fmt(body.acwr, 2), rNote, rTone, info('load_ratio'))}
      ${tile('HRV', fmt(body.hrv), body.hrv_delta != null ? `${signed(body.hrv_delta)} vs baseline` : '', '', info('hrv'))}
      ${tile('This week', `${fmt(body.week_km, 1)} km`, '')}
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>Goals</h2></div>
        <p>${goals ? esc(goals) : '<span class="muted">No notes from the athlete.</span>'}</p></div>
      <div class="card"><div class="card-head"><h2>Races</h2>
          <a class="btn-link" href="#/athletes/${id}/training/races">Details</a></div>
        ${races.length ? `<div class="scroll"><table><thead><tr><th>Date</th><th>Race</th><th>Priority</th><th>Goal</th></tr></thead>
          <tbody>${races.map((r) => `<tr><td>${esc(longDate(r.date || ''))}</td><td class="left">${esc(r.name || '')}</td>
            <td>${esc(r.priority || '')}</td><td>${esc(r.goal_time || '')}</td></tr>`).join('')}</tbody></table></div>`
          : '<p class="muted">No races.</p>'}
      </div>
    </div>
    <div class="card"><div class="card-head"><h2>Quick actions</h2></div>
      <div class="row-actions">
        <a class="ghost small" href="#/athletes/${id}/training/workouts">Assign a workout</a>
        <a class="ghost small" href="#/athletes/${id}/notes">Leave a note</a>
        <a class="ghost small" href="#/athletes/${id}/insights/load">See load and recovery</a>
      </div>
    </div>`;
}

async function notes(host, id) {
  const body = await api(`/api/coaching/athletes/${id}/notes`);
  const list = body.notes || [];
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>Write to the athlete</h2></div>
      <form class="composer wrap" data-note>
        <select name="kind" aria-label="Kind"><option value="comment">Comment</option><option value="suggestion">Suggestion</option></select>
        <input name="text" required placeholder="They'll see this on their Today page" aria-label="Note">
        <button class="primary" type="submit">Send</button>
      </form>
    </div>
    <div class="card">
      <div class="card-head"><h2>Notes</h2></div>
      ${list.length ? list.map((n) => `<div class="coach-note"><div class="meta"><span class="badge">${esc(n.kind)}</span>
        ${esc(n.created)}</div>${esc(n.text)}</div>`).join('') : emptyState('No notes yet')}
    </div>`;
  const form = $('[data-note]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), 'Sending…', async () => {
      await post(`/api/coaching/athletes/${id}/notes`, { kind: form.kind.value, text: form.text.value });
      invalidate(`athlete:${id}`);
      toast('Note sent');
      rerender();
    });
  });
}
