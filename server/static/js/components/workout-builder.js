/* Structured workout editor, shared by the athlete's Workouts tab and the
   coach's "Assign a workout". The steps live in a model and the inputs write
   into it, so nested repeats round-trip without re-reading the DOM. */
import { api, post } from '../core/api.js';
import { invalidate, state } from '../core/state.js';
import { $, $$, busy, esc, todayIso, toast } from '../core/ui.js';

const INTENSITIES = ['warmup', 'active', 'interval', 'recovery', 'cooldown', 'rest'];
const UNITS = [
  ['min', 'time', 'min'], ['s', 'time', 's'], ['m', 'distance', 'm'],
  ['km', 'distance', 'km'], ['reps', 'reps', ''],
];
const TARGETS = [['hr_zone', 'HR zone'], ['pace', 'Pace'], ['power_zone', 'Power zone'], ['none', 'None']];

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
      <label>Repeat <input type="number" min="1" max="30" value="${s.times || 4}" data-path="${path}" data-f="times"> times
        <button type="button" class="ghost small" data-remove="${path}" aria-label="Remove repeat">Remove</button></label>
      ${(s.steps || []).map((inner, j) => stepEditor(inner, `${path}.${j}`)).join('')}
      <button type="button" class="link" data-add-inner="${path}">+ step in this repeat</button>
    </div>`;
  }
  const t = s.target || {};
  const unit = unitKey(s.duration);
  return `<div class="step-row">
    <select data-path="${path}" data-f="intensity" aria-label="Intensity">
      ${INTENSITIES.map((v) => `<option${s.intensity === v ? ' selected' : ''}>${v}</option>`).join('')}
    </select>
    <input type="number" min="1" value="${(s.duration && s.duration.value) || 10}" data-path="${path}" data-f="value" aria-label="Duration">
    <select data-path="${path}" data-f="unit" aria-label="Unit">
      ${UNITS.map(([key]) => `<option value="${key}"${unit === key ? ' selected' : ''}>${key}</option>`).join('')}
    </select>
    <select data-path="${path}" data-f="target" aria-label="Target">
      ${TARGETS.map(([key, label]) => `<option value="${key}"${(t.type || 'none') === key ? ' selected' : ''}>${label}</option>`).join('')}
    </select>
    <input placeholder="zone or 4:30" value="${esc(t.zone || t.low || '')}" data-path="${path}" data-f="targval" aria-label="Target value"
      ${t.type === 'none' ? 'disabled' : ''}>
    <input placeholder="exercise (strength)" value="${esc(s.exercise || '')}" data-path="${path}" data-f="exercise" aria-label="Exercise">
    <button type="button" class="ghost small" data-remove="${path}" aria-label="Remove step">×</button>
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
export function mountBuilder(host, { athleteId = null, library = true, title = 'Build a workout', intro = '' } = {}) {
  const model = { name: '', sport: 'running', kind: 'easy', steps: [step()] };

  host.innerHTML = `
    <div class="card">
      <h2>${esc(title)}</h2>
      <p class="muted">${intro || 'Preview checks the structure. Schedule queues it; the next Garmin sync writes it to the watch.'}</p>
      <div class="builder">
        <input data-meta="name" placeholder="Name" aria-label="Workout name">
        <select data-meta="sport" aria-label="Sport">
          <option value="running">Run</option><option value="cycling">Bike</option>
          <option value="swimming">Swim</option><option value="strength">Strength</option>
        </select>
        <select data-meta="kind" aria-label="Kind">
          ${['easy', 'long', 'tempo', 'interval', 'strength', 'bike', 'swim'].map((k) => `<option value="${k}">${k[0].toUpperCase() + k.slice(1)}</option>`).join('')}
        </select>
      </div>
      <div data-steps></div>
      <div class="row-actions">
        <button class="ghost" type="button" data-add="step">Add step</button>
        <button class="ghost" type="button" data-add="repeat">Add repeat</button>
      </div>
      <div class="composer wrap" style="margin-top:12px">
        <input type="date" data-date aria-label="Date" value="${todayIso()}">
        <button class="ghost" type="button" data-preview>Preview</button>
        ${library && !athleteId ? '<button class="ghost" type="button" data-save>Save to library</button>' : ''}
        <button class="primary" type="button" data-schedule>${athleteId ? 'Schedule for athlete' : 'Schedule'}</button>
      </div>
      <pre class="cmd-out" data-out></pre>
    </div>
    ${library ? `<div class="card"><h2>Library</h2>
      <p class="muted">Built-in sessions and the ones you saved. Use loads one into the editor.</p>
      <div class="scroll"><table data-library></table></div></div>` : ''}`;

  const stepsHost = $('[data-steps]', host);
  const out = $('[data-out]', host);

  const render = () => {
    stepsHost.innerHTML = model.steps.map((s, i) => stepEditor(s, String(i))).join('')
      || '<p class="muted">No steps yet.</p>';
  };

  const read = () => ({
    name: $('[data-meta="name"]', host).value || model.name || 'Workout',
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

  $('[data-preview]', host).addEventListener('click', (event) => busy(event.currentTarget, 'Checking…', async () => {
    const body = await post('/api/workout/preview', read());
    out.textContent = (body.description ? body.description + '\n\n' : '') + JSON.stringify(body.workout, null, 2);
  }));

  $('[data-schedule]', host).addEventListener('click', (event) => busy(event.currentTarget, 'Scheduling…', async () => {
    const payload = { ...read(), date: $('[data-date]', host).value };
    if (athleteId) payload.athlete_id = athleteId;
    const body = await post('/api/workout/schedule', payload);
    const day = body.session && body.session.date;
    out.textContent = '';
    invalidate('plan', 'decide');
    if (!athleteId && state.status.garmin && state.status.garmin.connected) {
      post('/api/garmin/sync', {}).catch(() => {});
      toast(`Scheduled for ${day}. Sending it to Garmin now.`);
    } else {
      toast(athleteId ? `Queued for ${day}. It reaches their watch on their next sync.`
        : `Queued for ${day}. The next Garmin sync puts it on your watch.`);
    }
  }));

  const save = $('[data-save]', host);
  if (save) {
    save.addEventListener('click', (event) => busy(event.currentTarget, 'Saving…', async () => {
      await post('/api/workouts', read());
      toast('Saved to your library');
      loadLibrary();
    }));
  }

  async function loadLibrary() {
    const table = $('[data-library]', host);
    if (!table) return;
    const body = await api('/api/workouts').catch(() => ({ workouts: [] }));
    const items = body.workouts || [];
    table.innerHTML = '<thead><tr><th>Name</th><th>Sport</th><th>Kind</th><th>Load</th><th></th></tr></thead><tbody>' +
      (items.map((w, i) => {
        const wo = w.workout || w;
        return `<tr><td>${esc(w.name || wo.name || '')}</td><td>${esc(wo.sport || '')}</td>
          <td>${esc(wo.kind || '')}</td><td>${wo.est_load || ''}</td>
          <td><button type="button" class="ghost" data-use="${i}">Use</button></td></tr>`;
      }).join('') || '<tr><td colspan="5">No templates yet.</td></tr>') + '</tbody>';
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
