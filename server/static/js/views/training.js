/* Training: the plan calendar, races, and single workouts. A coach sees the
   same pages for an athlete through `athleteId`, read-only except for
   assigning workouts. */
import { mountBuilder } from '../components/workout-builder.js';
import { api, del, post, scoped } from '../core/api.js';
import { COLORS, baseOptions, draw } from '../core/charts.js';
import { info } from '../core/glossary.js';
import { t } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { cached, invalidate, state } from '../core/state.js';
import {
  $, $$, busy, confirmDialog, emptyState, esc, fmt, loadingPage, longDate, shortDate, subnav, tile,
  todayIso, toast,
} from '../core/ui.js';

function tabs() {
  return [['calendar', t('train.calendar')], ['races', t('train.races')], ['workouts', t('train.workouts')]];
}
function dow() {
  return ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((key) => t(`day.${key}`));
}

export async function render(root, ctx = {}) {
  const athleteId = ctx.athleteId || null;
  const opts = { athleteId, readOnly: Boolean(ctx.readOnly), key: athleteId || '' };
  const wanted = (ctx.path || [])[0];
  const items = tabs();
  const sub = items.some(([key]) => key === wanted) ? wanted : 'calendar';
  root.innerHTML = `<div class="page-head">${subnav(ctx.base || '#/training', items, sub)}
    <a class="btn-link" href="#/guide/training">${esc(t('train.guide'))}</a></div><div data-sub>${loadingPage()}</div>`;
  const host = $('[data-sub]', root);
  if (sub === 'races') return races(host, opts);
  if (sub === 'workouts') return workouts(host, opts);
  return calendar(host, opts);
}

// ---- helpers ----------------------------------------------------------------
function mondayOf(iso) {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function sessionKind(s) {
  return (s.kind || (s.workout && s.workout.kind) || '').replace(/_/g, ' ');
}

function paceStatusLabel(status) {
  if (status === 'too_fast') return t('train.faster');
  if (status === 'too_slow') return t('train.slower');
  if (status === 'on_target') return t('train.onTarget');
  return status || '';
}

function loadPerformance(opts) {
  return cached(`performance:${opts.key}`, () => api(scoped('/api/performance', opts.athleteId)))
    .catch(() => ({}));
}

function loadRaces(opts) {
  return cached(`races:${opts.key}`, () => api(scoped('/api/races', opts.athleteId)));
}

function raceFeasibility(race, perf) {
  if (!perf || !race.goal_time || !race.distance_m) return '';
  const pred = (perf.predictions || []).find((row) => Math.abs((row.distance_m || 0) - race.distance_m) < 800);
  const expected = (pred && (pred.vdot_s || pred.riegel_s)) || null;
  if (!expected) return '';
  const parts = String(race.goal_time).split(':').map(Number);
  let goal = 0;
  if (parts.length === 3) goal = parts[0] * 3600 + parts[1] * 60 + parts[2];
  else if (parts.length === 2) goal = parts[0] * 60 + parts[1];
  if (!goal) return '';
  const pct = (goal - expected) / expected;
  if (pct > 0.03) return ['ok', t('train.comfortable')];
  if (pct > -0.03) return ['ok', t('train.onPace')];
  if (pct > -0.08) return ['warn', t('train.stretch')];
  return ['bad', t('train.ambitious')];
}

// ---- calendar -----------------------------------------------------------------
function paceBoard(paces, perf) {
  const bands = paces.bands || {};
  const rows = [['easy', t('pace.easy')], ['marathon', t('pace.m')], ['threshold', t('pace.t')], ['interval', t('pace.i')], ['rep', t('pace.r')], ['goal', t('pace.goal')]];
  const chips = rows.map(([key, label]) => {
    const b = bands[key] || {};
    const mid = b.mid || paces[key];
    if (!mid) return '';
    const range = b.high && b.low && b.high !== b.low ? `${b.high}–${b.low}` : '';
    return `<div class="pace-chip"><span class="k">${label}</span><span class="v">${esc(mid)}</span>` +
      (range ? `<span class="r">${esc(range)}/km</span>` : '') + '</div>';
  }).join('');
  if (!chips) return '';
  const from = (perf && perf.vdot_from) || {};
  const source = paces.note || (paces.source === 'vdot' && from.mark
    ? t('train.pacesFromVdot', { vdot: fmt(paces.vdot, 1), mark: from.mark, time: from.time || '' })
    : (paces.source ? t('train.pacesFrom', { source: paces.source }) : ''));
  return `<div class="card"><div class="card-head"><h2>${esc(t('train.paces'))} ${info('paces')}</h2></div>
    <div class="pace-board">${chips}</div><p class="muted">${esc(source)}</p></div>`;
}

function paceInsights(body) {
  const reviews = (body && body.reviews) || [];
  const rec = body && body.recommendation;
  if (!reviews.length && !rec) return '';
  const bars = reviews.map((r) => {
    const pct = Math.max(-12, Math.min(12, r.delta_pct || 0));
    const left = 50 + (pct / 12) * 50;
    const klass = r.status === 'on_target' ? 'ok' : (r.status === 'too_fast' ? 'fast' : 'slow');
    return `<div class="insight-row">
      <span class="d">${esc((r.date || '').slice(5))} · ${esc(r.name || r.kind || '')}</span>
      <span class="bar"><i class="${klass}" style="left:${left}%"></i></span>
      <span class="nums">${esc(r.actual || '—')} vs ${esc(r.target || '—')}</span>
    </div>`;
  }).join('');
  const recHtml = rec
    ? `<div class="insight-rec"><p>${esc(rec.summary)}</p>
        <div class="row-actions">
          <button type="button" class="primary inline" data-insight="accept">${esc(t('train.acceptPaces'))}</button>
          <button type="button" class="ghost" data-insight="dismiss">${esc(t('train.keepPaces'))}</button>
        </div><p class="muted">${esc(t('train.acceptHint'))}</p></div>`
    : `<p class="muted">${esc(t('train.paceEmpty'))}</p>`;
  return `<div class="card pace-insights"><div class="card-head"><h2>${esc(t('train.paceCheck'))} ${info('pace_check')}</h2></div>
    <p class="muted">${esc(t('train.paceHelp'))}</p>
    <div class="insight-list">${bars}</div>${recHtml}</div>`;
}

function weekCalendar(plan) {
  const sessions = plan.sessions || [];
  const meta = {};
  (plan.weeks || []).forEach((w) => { meta[w.start] = w; });
  const groups = new Map();
  sessions.forEach((s) => {
    const key = mondayOf(s.date);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  });
  const today = todayIso();
  const thisWeek = mondayOf(today);
  return [...groups.entries()].map(([key, list]) => {
    const byDow = {};
    list.forEach((s) => { byDow[(new Date(s.date + 'T00:00:00').getDay() + 6) % 7] = s; });
    const cells = dow().map((label, i) => {
      const s = byDow[i];
      if (!s) return `<div class="day-cell empty"><span class="dow">${label}</span><span class="nm">—</span></div>`;
      const wo = s.workout || {};
      const km = s.distance_km != null ? `${fmt(s.distance_km, 1)} km` : '';
      const pace = (wo.targets && wo.targets.work) || '';
      const cls = [s.date === today ? 'today' : '', s.state === 'completed' ? 'done' : ''].filter(Boolean).join(' ');
      return `<button type="button" class="day-cell ${cls}" data-sid="${esc(s.id)}">
        <span class="dow">${label} ${s.date.slice(8)}</span>
        <span class="nm">${esc(wo.name || sessionKind(s))}</span>
        <span class="meta">${esc([km, pace].filter(Boolean).join(' · '))}</span>
        ${s.assigned_by ? `<span class="badge">${esc(t('train.coachBadge'))}</span>` : ''}
        ${s.state === 'skipped' ? `<span class="badge warn">${esc(t('train.skipped'))}</span>` : ''}
      </button>`;
    }).join('');
    const m = meta[key] || {};
    return `<div class="week-block${key === thisWeek ? ' current' : ''}" ${key === thisWeek ? 'data-current' : ''}>
      <h3>${esc(t('train.weekOf', { date: longDate(key) }))}<span>${esc(m.phase || list[0].phase || '')}${m.target_km ? ` · ${fmt(m.target_km, 0)} km` : ''}</span></h3>
      <div class="week-grid">${cells}</div>
    </div>`;
  }).join('');
}

function stepRows(steps) {
  const rows = [];
  (steps || []).forEach((st) => {
    if (st.kind === 'repeat') {
      rows.push(`<tr><td colspan="3"><strong>${esc(t('train.repeatSet', { n: st.times }))}</strong></td></tr>`);
      rows.push(...stepRows(st.steps || []));
      return;
    }
    const dur = st.duration || {};
    const when = `${dur.value ?? ''}${dur.unit ? ' ' + dur.unit : ''}`;
    const t = st.target || {};
    let pace = '';
    if (t.type === 'pace') pace = t.low && t.high && t.low !== t.high ? `${t.high}–${t.low}/km` : `${t.low || t.high || ''}/km`;
    else if (t.type === 'hr_zone') pace = `HR Z${t.zone}`;
    else if (t.type === 'power_zone') pace = `Power Z${t.zone}`;
    rows.push(`<tr><td>${esc(st.intensity || '')}</td><td>${esc(when)}</td><td>${esc(pace)}</td></tr>`);
  });
  return rows;
}

function sessionSheet(session, readOnly) {
  const wo = session.workout || {};
    const done = session.completed_pace
    ? `<p class="muted">${esc(t('train.doneAt', {
      pace: session.completed_pace,
      target: session.target_pace || t('train.target'),
      status: paceStatusLabel(session.pace_status),
    }))}</p>` : '';
  return `<div class="card-head"><h2>${esc(wo.name || sessionKind(session))}</h2>
      <span class="muted">${esc(longDate(session.date))}</span></div>
    ${session.assigned_by ? `<span class="badge">${esc(t('train.fromTheCoach'))}</span>` : ''}
    <p class="muted">${esc(session.purpose || wo.purpose || '')}</p>
    <p>${esc(session.description || wo.description || '')}</p>
    ${done}
    <div class="scroll"><table class="session-steps">
      <thead><tr><th>${esc(t('train.step'))}</th><th>${esc(t('train.duration'))}</th><th>${esc(t('train.target'))}</th></tr></thead>
      <tbody>${stepRows(wo.steps).join('') || `<tr><td colspan="3">${esc(t('train.noStructure'))}</td></tr>`}</tbody>
    </table></div>
    ${readOnly ? '' : `<div class="row-actions">
      <button type="button" class="ghost" data-op="ease">${esc(t('train.makeEasy'))}</button>
      <button type="button" class="ghost" data-op="rest">${esc(t('train.turnRest'))}</button>
      <button type="button" class="ghost" data-op="skip">${esc(t('train.skip'))}</button>
    </div>`}`;
}

function drawProjected(canvas, series) {
  draw(canvas, {
    type: 'line',
    data: {
      labels: series.map((r) => shortDate(r.date)),
      datasets: [
        { label: t('chart.load'), data: series.map((r) => r.load), borderColor: COLORS.load, backgroundColor: COLORS.load, fill: true, tension: .2, pointRadius: 0 },
        { label: t('chart.fatigue'), data: series.map((r) => r.atl), borderColor: COLORS.atl, tension: .3, pointRadius: 0 },
        { label: t('chart.fitness'), data: series.map((r) => r.ctl), borderColor: COLORS.ctl, tension: .3, pointRadius: 0 },
      ],
    },
    options: baseOptions(),
  });
}

async function calendar(host, opts) {
  const { athleteId, readOnly } = opts;
  const [planBody, perf, insightsBody] = await Promise.all([
    cached(`plan:${opts.key}`, () => api(scoped('/api/plan', athleteId))),
    loadPerformance(opts),
    athleteId ? null : cached('insights', () => api('/api/insights')).catch(() => null),
  ]);
  const plan = planBody.plan;
  if (!plan) {
    host.innerHTML = emptyState(esc(t('train.noPlan')),
      esc(readOnly ? t('train.noPlanCoach') : t('train.noPlanHelp')),
      readOnly ? '' : `<a class="primary inline" href="#/training/races">${esc(t('today.planRace'))}</a> ` +
        `<a class="ghost small" href="#/training/workouts">${esc(t('today.build'))}</a>`);
    return;
  }
  const h = plan.headline || {};
  const adh = planBody.adherence || {};
  const active = plan.status === 'active';
  const g = state.status.garmin || {};
  const oneOff = !plan.race_id && !h.weeks;

  host.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>${esc(oneOff ? t('train.scheduled') : (plan.name || h.race || t('train.planFallback')))}
          <span class="badge ${active ? 'ok' : ''}">${esc(active ? t('train.onWatch') : t('train.draft'))}</span></h2>
        ${readOnly || oneOff ? '' : `<button type="button" class="${active ? 'ghost' : 'primary inline'}" data-activate>
          ${esc(active ? t('train.resend') : t('train.send'))}</button>`}
      </div>
      ${plan.rationale ? `<p class="muted">${esc(plan.rationale)}</p>` : ''}
      ${oneOff ? `<p class="muted">${esc(t('train.oneOff'))}${readOnly ? '' :
        ` <a class="btn-link" href="#/training/races">${esc(t('train.buildRace'))}</a>`}</p>` : `<div class="cards">
        ${tile(t('train.weeks'), fmt(h.weeks), h.short_block ? t('train.shortBlock', { n: h.ideal_weeks }) : (h.family || ''))}
        ${tile(t('train.sessions'), fmt(h.sessions), adh.planned ? t('train.sessionsDone', { done: adh.completed, planned: adh.planned }) +
          (adh.skipped ? `, ${t('train.sessionsSkipped', { n: adh.skipped })}` : '') : '', '', info('adherence'))}
        ${tile(t('train.vdot'), fmt(h.vdot, 1), '', '', info('vdot'))}
        ${tile(t('train.peak'), fmt(h.weekly_km_peak), t('train.peakNow', { n: fmt(h.weekly_km_now) }))}
        ${tile(t('train.fitnessRace'), fmt(h.ctl_at_race), t('train.peakNow', { n: fmt(h.ctl_now) }), '', info('fitness'))}
      </div>`}
    </div>
    ${paceBoard(h.paces || perf.paces || {}, perf)}
    ${readOnly ? '' : paceInsights(insightsBody)}
    <div class="card">
      <div class="card-head"><h2>${esc(t('train.weeksTitle'))}</h2><span class="muted">${esc(t('train.tapDay'))}</span></div>
      <div data-weeks>${weekCalendar(plan)}</div>
    </div>
    <div class="card session-sheet hidden" data-sheet></div>
    ${(plan.projected || []).length ? `<div class="card"><div class="card-head"><h2>${esc(t('train.projected'))} ${info('projected')}</h2></div>
      <div class="chart"><canvas id="chart-projected"></canvas></div></div>` : ''}`;

  const canvas = $('#chart-projected', host);
  if (canvas) drawProjected(canvas, plan.projected);

  const sheet = $('[data-sheet]', host);
  $$('[data-sid]', host).forEach((button) => {
    button.addEventListener('click', () => {
      $$('.day-cell', host).forEach((el) => el.classList.remove('active'));
      button.classList.add('active');
      const session = (plan.sessions || []).find((s) => s.id === button.dataset.sid);
      sheet.innerHTML = sessionSheet(session, readOnly);
      sheet.classList.remove('hidden');
      sheet.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      $$('[data-op]', sheet).forEach((op) => op.addEventListener('click', () => busy(op, '…', async () => {
        await post(`/api/plan/${plan.id}/patch`, { op: op.dataset.op, id: session.id });
        invalidate('plan', 'decide');
        toast(t('train.sessionUpdated'));
        rerender();
      })));
    });
  });

  $$('[data-insight]', host).forEach((button) => button.addEventListener('click', () => busy(button, '…', async () => {
    await post('/api/insights', { action: button.dataset.insight });
    invalidate('plan', 'insights');
    toast(button.dataset.insight === 'accept' ? t('train.pacesApplied') : t('train.pacesKept'));
    rerender();
  })));

  const activate = $('[data-activate]', host);
  if (activate) {
    activate.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: t('train.sendTitle'),
        body: g.connected ? t('train.sendConnected') : t('train.sendExtension'),
        confirm: t('train.send'),
      });
      if (!ok) return;
      await busy(activate, t('train.sending'), async () => {
        await post(`/api/plan/${plan.id}/activate`);
        invalidate('plan', 'decide');
        if (g.connected && !g.needs_login) await post('/api/garmin/sync', {}).catch(() => {});
        toast(g.connected ? t('train.sentConnected') : t('train.sentQueued'));
        rerender();
      });
    });
  }
}

// ---- races ----------------------------------------------------------------------
async function races(host, opts) {
  const { readOnly } = opts;
  const [body, perf] = await Promise.all([loadRaces(opts), loadPerformance(opts)]);
  const list = (body.races || []).slice().sort((a, b) => a.date.localeCompare(b.date));
  const today = todayIso();
  const upcoming = list.filter((r) => r.date >= today);

  const rows = list.map((r) => {
    const fit = raceFeasibility(r, perf);
    return `<tr>
      <td>${esc(longDate(r.date))}</td>
      <td class="left">${esc(r.name)}</td>
      <td>${esc(r.priority || 'A')}</td>
      <td>${r.distance_m ? fmt(r.distance_m / 1000, 1) + ' km' : ''}</td>
      <td>${esc(r.goal_time || '')}</td>
      <td>${fit ? `<span class="badge ${fit[0]}">${fit[1]}</span>` : ''}</td>
      ${readOnly ? '' : `<td>${r.date <= today ? `<button type="button" class="ghost small" data-review="${esc(r.id)}">${esc(t('train.review'))}</button>` : ''}
        <button type="button" class="ghost small" data-remove="${esc(r.id)}" data-name="${esc(r.name)}">${esc(t('train.remove'))}</button></td>`}
    </tr>`;
  }).join('');

  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('train.races'))}</h2></div>
      ${list.length ? `<div class="scroll"><table>
        <thead><tr><th>${esc(t('train.date'))}</th><th>${esc(t('train.race'))}</th><th>${esc(t('train.priority'))}</th><th>${esc(t('train.distance'))}</th><th>${esc(t('train.goal'))}</th>
          <th>${esc(t('train.goalVs'))} ${info('goal')}</th>${readOnly ? '' : '<th></th>'}</tr></thead>
        <tbody>${rows}</tbody></table></div>`
        : emptyState(esc(t('train.noRaces')), readOnly ? '' : esc(t('train.noRacesHelp')))}
      <p class="muted" data-review-out aria-live="polite"></p>
    </div>
    ${readOnly ? '' : `
    <div class="card">
      <div class="card-head"><h2>${esc(t('train.addRace'))}</h2></div>
      <form class="form-grid" data-race-form>
        <label class="field"><span>${esc(t('train.name'))}</span><input name="name" required placeholder="${esc(t('train.namePh'))}"></label>
        <label class="field"><span>${esc(t('train.date'))}</span><input name="date" type="date" required min="${today}"></label>
        <label class="field"><span>${esc(t('train.distance'))}</span><select name="distance">
          <option value="5000">${esc(t('train.dist5'))}</option><option value="10000">${esc(t('train.dist10'))}</option>
          <option value="21097" selected>${esc(t('train.distHalf'))}</option><option value="42195">${esc(t('train.distMara'))}</option>
        </select></label>
        <label class="field"><span>${esc(t('train.goalTime'))}</span><input name="goal" placeholder="1:45:00"></label>
        <label class="field"><span>${esc(t('train.priority'))}</span><select name="priority">
          <option value="A" selected>${esc(t('train.priorityA'))}</option><option value="B">B</option><option value="C">C</option>
        </select></label>
        <div class="row-actions"><button class="primary inline" type="submit">${esc(t('train.addRaceBtn'))}</button></div>
      </form>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(t('train.buildPlan'))}</h2></div>
      ${upcoming.length ? `<form class="form-grid" data-plan-form>
        <label class="field"><span>${esc(t('train.race'))}</span><select name="race">${upcoming.map((r) =>
          `<option value="${esc(r.id)}">${esc(r.name)} (${esc(longDate(r.date))})</option>`).join('')}</select></label>
        <label class="field"><span>${esc(t('train.constraints'))}</span>
          <input name="notes" placeholder="${esc(t('train.constraintsPh'))}"></label>
        <div class="row-actions"><button class="primary inline" type="submit">${esc(t('train.generate'))}</button></div>
        <p class="muted">${esc(t('train.generateHelp'))}</p>
      </form>` : `<p class="muted">${esc(t('train.addUpcoming'))}</p>`}
    </div>`}`;

  if (readOnly) return;
  const out = $('[data-review-out]', host);
  $$('[data-review]', host).forEach((button) => button.addEventListener('click', () => busy(button, '…', async () => {
    const review = await api(`/api/races/${button.dataset.review}/review`);
    const r = review.result || {};
    const f = review.feasibility || {};
    const splits = (review.splits || []).map((s) => `${s.label}: ${s.pace || ''}`).join(' · ');
    out.innerHTML = `<strong>${esc((review.race && review.race.name) || t('train.race'))}</strong>: ${esc(t('train.reviewLine', { goal: r.goal || '—', actual: r.actual || t('train.notRaced') }))}` +
      (r.vs_goal ? `, ${esc(r.vs_goal)}` : '') +
      (f.label ? `. ${esc(f.label)}` : '') + (splits ? `. ${esc(t('train.splits'))}: ${esc(splits)}` : '');
  })));
  $$('[data-remove]', host).forEach((button) => button.addEventListener('click', async () => {
    const ok = await confirmDialog({ title: t('train.removeTitle', { name: button.dataset.name }), confirm: t('train.remove'), danger: true });
    if (!ok) return;
    await del(`/api/races/${button.dataset.remove}`);
    invalidate('races');
    toast(t('train.raceRemoved'));
    rerender();
  }));

  $('[data-race-form]', host).addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    busy($('button[type="submit"]', form), t('train.adding'), async () => {
      await post('/api/races', {
        name: form.name.value,
        date: form.date.value,
        distance_m: Number(form.distance.value),
        goal_time: form.goal.value,
        priority: form.priority.value,
      });
      invalidate('races');
      toast(t('train.raceAdded'));
      rerender();
    });
  });

  const planForm = $('[data-plan-form]', host);
  if (planForm) {
    planForm.addEventListener('submit', (event) => {
      event.preventDefault();
      busy($('button[type="submit"]', planForm), t('train.building'), async () => {
        const notes = planForm.notes.value;
        await post('/api/plan/generate', {
          race_id: planForm.race.value, notes, extras: ['strength'], adjust: Boolean(notes),
        });
        invalidate('plan', 'decide', 'insights');
        toast(t('train.planReady'));
        location.hash = '#/training/calendar';
      });
    });
  }
}

// ---- workouts ------------------------------------------------------------------
function workouts(host, opts) {
  if (opts.athleteId) {
    mountBuilder(host, {
      athleteId: opts.athleteId,
      library: false,
      title: t('train.assignTitle'),
      intro: t('train.assignIntro'),
    });
    return;
  }
  mountBuilder(host, {
    intro: t('train.workoutIntro'),
  });
}
