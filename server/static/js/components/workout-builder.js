/* Structured workout editor, shared by the athlete's Workouts tab and the
   coach's "Assign a workout". The steps live in a model and the inputs write
   into it, so nested repeats round-trip without re-reading the DOM. */
import { api, post } from '../core/api.js';
import { showText } from '../core/copy.js';
import { t } from '../core/i18n.js';
import { invalidate, state } from '../core/state.js';
import { $, $$, busy, esc, todayIso, toast } from '../core/ui.js';

const INTENSITIES = ['warmup', 'active', 'interval', 'recovery', 'cooldown', 'rest'];
const UNITS = [
  ['min', 'time', 'min'], ['s', 'time', 's'], ['m', 'distance', 'm'],
  ['km', 'distance', 'km'], ['reps', 'reps', ''],
];
function targets() {
  return [['hr_zone', t('tgt.hr_zone')], ['pace', t('tgt.pace')], ['power_zone', t('tgt.power_zone')], ['none', t('tgt.none')]];
}
function kinds() {
  return [['easy', t('wo.easy')], ['long', t('wo.long')], ['tempo', t('wo.tempo')], ['interval', t('wo.interval')], ['strength', t('wo.strength')], ['bike', t('wo.bike')], ['swim', t('wo.swim')]];
}

function step(intensity = 'active', value = 10, unit = 'min', target = { type: 'hr_zone', zone: 2 }) {
  const [, type, u] = UNITS.find(([key]) => key === unit) || UNITS[0];
  return { kind: 'step', intensity, duration: { type, value, unit: u }, target };
}

function repeat() {
  return {
    kind: 'repeat', times: 4,
    steps: [
      step('interval', 400, 'm', { type: 'hr_zone', zone: 5 }),
      step('recovery', 90, 's', { type: 'none' }),
    ],
  };
}

function unitKey(duration) {
  const d = duration || {};
  if (d.type === 'reps') return 'reps';
  if (d.type === 'distance') return d.unit === 'km' ? 'km' : 'm';
  return d.unit === 's' ? 's' : 'min';
}

function at(model, path) {
  return path.split('.').reduce((node, key) => (Array.isArray(node) ? node[Number(key)] : node.steps[Number(key)]), model);
}

function stepEditor(s, path) {
  if (s.kind === 'repeat') {
    return `<div class="repeat-box">
      <label>${esc(t('wo.repeat'))} <input type="number" min="1" max="30" value="${s.times || 4}" data-path="${path}" data-f="times"> ${esc(t('wo.times'))}
        <button type="button" class="ghost small" data-remove="${path}" aria-label="${esc(t('wo.removeRepeat'))}">${esc(t('wo.remove'))}</button></label>
      ${(s.steps || []).map((inner, j) => stepEditor(inner, `${path}.${j}`)).join('')}
      <button type="button" class="link" data-add-inner="${path}">${esc(t('wo.addInner'))}</button>
    </div>`;
  }
  const target = s.target || {};
  const unit = unitKey(s.duration);
  return `<div class="step-row">
    <select data-path="${path}" data-f="intensity" aria-label="${esc(t('wo.intensity'))}">
      ${INTENSITIES.map((v) => `<option value="${v}"${s.intensity === v ? ' selected' : ''}>${esc(t('int.' + v))}</option>`).join('')}
    </select>
    <input type="number" min="1" value="${(s.duration && s.duration.value) || 10}" data-path="${path}" data-f="value" aria-label="${esc(t('wo.duration'))}">
    <select data-path="${path}" data-f="unit" aria-label="${esc(t('wo.unit'))}">
      ${UNITS.map(([key]) => `<option value="${key}"${unit === key ? ' selected' : ''}>${key}</option>`).join('')}
    </select>
    <select data-path="${path}" data-f="target" aria-label="${esc(t('wo.target'))}">
      ${targets().map(([key, label]) => `<option value="${key}"${(target.type || 'none') === key ? ' selected' : ''}>${esc(label)}</option>`).join('')}
    </select>
    <input placeholder="${esc(t('wo.targetPh'))}" value="${esc(target.zone || target.low || '')}" data-path="${path}" data-f="targval" aria-label="${esc(t('wo.targetValue'))}"
      ${target.type === 'none' ? 'disabled' : ''}>
    <input placeholder="${esc(t('wo.exercisePh'))}" value="${esc(s.exercise || '')}" data-path="${path}" data-f="exercise" aria-label="${esc(t('wo.exercise'))}">
    <button type="button" class="ghost small" data-remove="${path}" aria-label="${esc(t('wo.removeStep'))}">×</button>
  </div>`;
}

function applyField(s, field, value) {
  if (field === 'times') s.times = Math.max(1, Number(value) || 1);
  else if (field === 'intensity') s.intensity = value;
  else if (field === 'value') s.duration.value = Number(value) || 1;
  else if (field === 'unit') {
    const [, type, unit] = UNITS.find(([key]) => key === value);
    s.duration = { ...s.duration, type, unit };
  } else if (field === 'target') {
    s.target = value === 'none' ? { type: 'none' }
      : value === 'pace' ? { type: 'pace', low: '', high: '' } : { type: value, zone: 2 };
  } else if (field === 'targval') {
    if (s.target.type === 'pace') s.target = { type: 'pace', low: value, high: value };
    else s.target = { ...s.target, zone: Number(value) || 2 };
  } else if (field === 'exercise') {
    if (value.trim()) s.exercise = value.trim();
    else delete s.exercise;
  }
}

/**
 * Mount the builder into `host`.
 * `athleteId` schedules onto that athlete's plan (coach use); `library`
 * shows the saved and built-in templates.
 */
export function mountBuilder(host, { athleteId = null, library = true, title = '', intro = '' } = {}) {
  const model = { name: '', sport: 'running', kind: 'easy', steps: [step()] };
  const heading = title || t('wo.title');
  const lead = intro || t('wo.intro');

  host.innerHTML = `
    <div class="card">
      <h2>${esc(heading)}</h2>
      <p class="muted">${esc(lead)}</p>
      <div class="builder">
        <input data-meta="name" placeholder="${esc(t('wo.name'))}" aria-label="${esc(t('wo.nameLabel'))}">
        <select data-meta="sport" aria-label="${esc(t('wo.sport'))}">
          <option value="running">${esc(t('wo.run'))}</option><option value="cycling">${esc(t('wo.bike'))}</option>
          <option value="swimming">${esc(t('wo.swim'))}</option><option value="strength">${esc(t('wo.strength'))}</option>
        </select>
        <select data-meta="kind" aria-label="${esc(t('wo.kind'))}">
          ${kinds().map(([key, label]) => `<option value="${key}">${esc(label)}</option>`).join('')}
        </select>
      </div>
      <div data-steps></div>
      <div class="row-actions">
        <button class="ghost" type="button" data-add="step">${esc(t('wo.addStep'))}</button>
        <button class="ghost" type="button" data-add="repeat">${esc(t('wo.addRepeat'))}</button>
      </div>
      <div class="composer wrap" style="margin-top:12px">
        <input type="date" data-date aria-label="${esc(t('wo.date'))}" value="${todayIso()}">
        <button class="ghost" type="button" data-preview>${esc(t('wo.preview'))}</button>
        ${library && !athleteId ? `<button class="ghost" type="button" data-save>${esc(t('wo.save'))}</button>` : ''}
        <button class="primary" type="button" data-schedule>${esc(athleteId ? t('wo.scheduleAthlete') : t('wo.schedule'))}</button>
      </div>
      <pre class="cmd-out" data-out></pre>
    </div>
    ${library ? `<div class="card"><h2>${esc(t('wo.library'))}</h2>
      <p class="muted">${esc(t('wo.libraryHelp'))}</p>
      <div class="scroll"><table data-library></table></div></div>` : ''}`;

  const stepsHost = $('[data-steps]', host);
  const out = $('[data-out]', host);

  const render = () => {
    stepsHost.innerHTML = model.steps.map((s, i) => stepEditor(s, String(i))).join('')
      || `<p class="muted">${esc(t('wo.noSteps'))}</p>`;
  };

  const read = () => ({
    name: $('[data-meta="name"]', host).value || model.name || t('wo.fallbackName'),
    sport: $('[data-meta="sport"]', host).value,
    kind: $('[data-meta="kind"]', host).value,
    steps: model.steps,
  });

  stepsHost.addEventListener('input', (event) => {
    const el = event.target.closest('[data-path]');
    if (!el) return;
    applyField(at(model.steps, el.dataset.path), el.dataset.f, el.value);
    if (el.dataset.f === 'target' || el.dataset.f === 'unit') render();
  });
  stepsHost.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove]');
    const inner = event.target.closest('[data-add-inner]');
    if (remove) {
      const parts = remove.dataset.remove.split('.');
      const index = Number(parts.pop());
      const parent = parts.length ? at(model.steps, parts.join('.')).steps : model.steps;
      parent.splice(index, 1);
      render();
    } else if (inner) {
      at(model.steps, inner.dataset.addInner).steps.push(step('recovery', 60, 's', { type: 'none' }));
      render();
    }
  });
  $$('[data-add]', host).forEach((button) => button.addEventListener('click', () => {
    model.steps.push(button.dataset.add === 'repeat' ? repeat() : step());
    render();
  }));

  $('[data-preview]', host).addEventListener('click', (event) => busy(event.currentTarget, t('wo.checking'), async () => {
    const body = await post('/api/workout/preview', read());
    out.textContent = (body.description ? body.description + '\n\n' : '') + JSON.stringify(body.workout, null, 2);
  }));

  $('[data-schedule]', host).addEventListener('click', (event) => busy(event.currentTarget, t('wo.scheduling'), async () => {
    const payload = { ...read(), date: $('[data-date]', host).value };
    if (athleteId) payload.athlete_id = athleteId;
    const body = await post('/api/workout/schedule', payload);
    const day = body.session && body.session.date;
    out.textContent = '';
    invalidate('plan', 'decide');
    if (!athleteId && state.status.garmin && state.status.garmin.connected) {
      post('/api/garmin/sync', {}).catch(() => {});
      toast(t('wo.scheduledNow', { day }));
    } else {
      toast(athleteId ? t('wo.queuedCoach', { day }) : t('wo.queued', { day }));
    }
  }));

  const save = $('[data-save]', host);
  if (save) {
    save.addEventListener('click', (event) => busy(event.currentTarget, t('wo.saving'), async () => {
      await post('/api/workouts', read());
      toast(t('wo.saved'));
      loadLibrary();
    }));
  }

  async function loadLibrary() {
    const table = $('[data-library]', host);
    if (!table) return;
    const body = await api('/api/workouts').catch(() => ({ workouts: [] }));
    const items = body.workouts || [];
    table.innerHTML = `<thead><tr><th>${esc(t('wo.name'))}</th><th>${esc(t('wo.sport'))}</th><th>${esc(t('wo.kind'))}</th><th>${esc(t('wo.load'))}</th><th></th></tr></thead><tbody>` +
      (items.map((w, i) => {
        const wo = w.workout || w;
        return `<tr><td>${esc(showText(w.name || wo.name || ''))}</td><td>${esc(showText(wo.sport || ''))}</td>
          <td>${esc(showText(wo.kind || ''))}</td><td>${wo.est_load || ''}</td>
          <td><button type="button" class="ghost" data-use="${i}">${esc(t('wo.use'))}</button></td></tr>`;
      }).join('') || `<tr><td colspan="5">${esc(t('wo.noTemplates'))}</td></tr>`) + '</tbody>';
    $$('[data-use]', table).forEach((button) => button.addEventListener('click', () => {
      const item = items[Number(button.dataset.use)];
      const wo = item.workout || item;
      $('[data-meta="name"]', host).value = wo.name || '';
      $('[data-meta="sport"]', host).value = wo.sport || 'running';
      $('[data-meta="kind"]', host).value = wo.kind || 'easy';
      model.steps = JSON.parse(JSON.stringify(wo.steps || [step()]));
      render();
      host.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
  }

  render();
  loadLibrary();
}
