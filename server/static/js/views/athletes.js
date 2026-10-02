/* Coach area: who needs attention (roster), requests waiting (inbox), and an
   athlete's page that reuses Training and Insights read-only. */
import { acwrTone, formTone } from '../components/headline.js';
import { api, post } from '../core/api.js';
import { info } from '../core/glossary.js';
import { t } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { cached, emit, invalidate } from '../core/state.js';
import {
  $, $$, busy, emptyState, esc, fmt, loadingPage, longDate, shortDate, signed, subnav, tile, toast,
} from '../core/ui.js';
import * as insights from './insights.js';
import * as training from './training.js';

function detailTabs() {
  return [['overview', t('ath.overview')], ['training', t('ath.training')], ['insights', t('ath.insights')], ['notes', t('ath.notes')]];
}

function agoLabel(gap, last) {
  if (!last) return t('time.never');
  if (gap === 0) return t('time.today');
  if (gap === 1) return t('time.yesterday');
  return t('time.daysAgo', { n: gap });
}

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
        <div class="sub">${a.last_activity ? esc(`${(a.last_activity.type || 'activity').replace(/_/g, ' ')} ${shortDate((a.last_activity.start || '').slice(0, 10))}`) : esc(t('ath.noActivity'))}</div></td>
      <td>${flag(fTone, fNote)} ${fmt(a.form)}</td>
      <td>${flag(rTone, rNote)} ${fmt(a.acwr, 2)}</td>
      <td>${flag(hTone, t('ath.hrvTitle'))} ${a.hrv_delta != null ? signed(a.hrv_delta) : '--'}</td>
      <td>${fmt(a.week_km, 1)}</td>
      <td><span class="${lastTone ? `badge ${lastTone}` : ''}">${esc(agoLabel(gap, a.last))}</span></td>
      <td>${a.next_race ? `${esc(a.next_race.name)}<div class="sub">${esc(longDate(a.next_race.date))}</div>` : '<span class="muted">—</span>'}</td>
    </tr>`;
  }).join('');

  root.innerHTML = `
    ${requests.length ? `<div class="card">
      <div class="card-head"><h2>${esc(t('ath.requests'))} <span class="badge warn">${requests.length}</span></h2></div>
      ${requests.map((r) => `<div class="list-row"><span>${esc(t('ath.wants', { email: r.athlete_email }))}</span>
        <span class="row-actions">
          <button type="button" class="primary inline" data-accept="${r.id}">${esc(t('ath.accept'))}</button>
          <button type="button" class="ghost" data-reject="${r.id}">${esc(t('ath.decline'))}</button>
        </span></div>`).join('')}
    </div>` : ''}
    <div class="card">
      <div class="card-head"><h2>${esc(t('ath.yours'))}</h2><span class="muted">${athletes.length}</span></div>
      ${athletes.length ? `<div class="scroll"><table class="roster">
        <thead><tr><th>${esc(t('ath.athlete'))}</th><th>${esc(t('ath.form'))}</th><th>${esc(t('ath.loadRatio'))}</th><th>${esc(t('ath.hrv'))}</th><th>${esc(t('ath.kmWeek'))}</th>
          <th>${esc(t('ath.lastData'))}</th><th>${esc(t('ath.nextRace'))}</th></tr></thead>
        <tbody>${rowsHtml}</tbody></table></div>
        <p class="muted" style="margin-top:10px">${esc(t('ath.dots'))}</p>`
        : emptyState(esc(t('ath.empty')), esc(t('ath.emptyHelp')))}
    </div>`;

  const decide = (button, verb) => busy(button, '…', async () => {
    await post(`/api/coaching/${button.dataset[verb]}/${verb}`);
    invalidate('roster');
    emit('inbox');
    toast(verb === 'accept' ? t('ath.added') : t('ath.declined'));
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
  const tabs = detailTabs();
  const tab = tabs.some(([key]) => key === sub) ? sub : 'overview';
  const body = await cached(`athlete:${id}`, () => api(`/api/coaching/athletes/${id}`));
  const base = `#/athletes/${id}`;
  root.innerHTML = `
    <a class="btn-link" href="#/athletes">${esc(t('ath.all'))}</a>
    <div class="athlete-head"><h2>${esc(body.email || t('ath.fallback'))}</h2>
      <span class="muted">${body.last ? esc(t('ath.through', { date: longDate(body.last) })) : esc(t('ath.noGarmin'))}</span></div>
    ${subnav(base, tabs, tab)}
    <div data-detail>${loadingPage()}</div>`;
  const host = $('[data-detail]', root);
  const readOnlyNote = `<p class="read-only">${esc(t('ath.readOnly'))}</p>`;

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
      ${tile(t('ath.fitness'), fmt(body.ctl), '', '', info('fitness'))}
      ${tile(t('ath.form'), fmt(body.form), fNote, fTone, info('form'))}
      ${tile(t('ath.loadRatio'), fmt(body.acwr, 2), rNote, rTone, info('load_ratio'))}
      ${tile(t('metric.hrv'), fmt(body.hrv), body.hrv_delta != null ? t('metric.vsBaseline', { n: signed(body.hrv_delta) }) : '', '', info('hrv'))}
      ${tile(t('ath.thisWeek'), `${fmt(body.week_km, 1)} km`, '')}
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h2>${esc(t('ath.goals'))}</h2></div>
        <p>${goals ? esc(goals) : `<span class="muted">${esc(t('ath.noNotes'))}</span>`}</p></div>
      <div class="card"><div class="card-head"><h2>${esc(t('ath.races'))}</h2>
          <a class="btn-link" href="#/athletes/${id}/training/races">${esc(t('ath.details'))}</a></div>
        ${races.length ? `<div class="scroll"><table><thead><tr><th>${esc(t('train.date'))}</th><th>${esc(t('train.race'))}</th><th>${esc(t('ath.priority'))}</th><th>${esc(t('ath.goal'))}</th></tr></thead>
          <tbody>${races.map((r) => `<tr><td>${esc(longDate(r.date || ''))}</td><td class="left">${esc(r.name || '')}</td>
            <td>${esc(r.priority || '')}</td><td>${esc(r.goal_time || '')}</td></tr>`).join('')}</tbody></table></div>`
          : `<p class="muted">${esc(t('ath.noRaces'))}</p>`}
      </div>
    </div>
    <div class="card"><div class="card-head"><h2>${esc(t('ath.quick'))}</h2></div>
      <div class="row-actions">
        <a class="ghost small" href="#/athletes/${id}/training/workouts">${esc(t('ath.assign'))}</a>
        <a class="ghost small" href="#/athletes/${id}/notes">${esc(t('ath.leaveNote'))}</a>
        <a class="ghost small" href="#/athletes/${id}/insights/load">${esc(t('ath.seeLoad'))}</a>
      </div>
    </div>`;
}

async function notes(host, id) {
  const body = await api(`/api/coaching/athletes/${id}/notes`);
  const list = body.notes || [];
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('ath.write'))}</h2></div>
      <form class="composer wrap" data-note>
        <select name="kind" aria-label="${esc(t('ath.kind'))}"><option value="comment">${esc(t('note.comment'))}</option><option value="suggestion">${esc(t('note.suggestion'))}</option></select>
        <input name="text" required placeholder="${esc(t('ath.notePh'))}" aria-label="${esc(t('ath.noteLabel'))}">
        <button class="primary" type="submit">${esc(t('ath.send'))}</button>
      </form>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(t('ath.notes'))}</h2></div>
      ${list.length ? list.map((n) => `<div class="coach-note"><div class="meta"><span class="badge">${esc(t('note.' + n.kind) === 'note.' + n.kind ? n.kind : t('note.' + n.kind))}</span>
        ${esc(n.created)}</div>${esc(n.text)}</div>`).join('') : emptyState(esc(t('ath.noNotesYet')))}
    </div>`;
  const form = $('[data-note]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), t('coach.sending'), async () => {
      await post(`/api/coaching/athletes/${id}/notes`, { kind: form.kind.value, text: form.text.value });
      invalidate(`athlete:${id}`);
      toast(t('ath.noteSent'));
      rerender();
    });
  });
}
