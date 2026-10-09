/* One catalog for the "?" popovers and the Guide page. Popovers show `short`;
   the Guide adds how we calculate it and how to read it. Copy lives in the
   language catalogs; this file only keeps which group each term belongs to. */
import { t } from './i18n.js';
import { esc } from './ui.js';

const GROUP_IDS = ['about', 'today', 'load', 'recovery', 'performance', 'training', 'coaching'];

export function groups() {
  return GROUP_IDS.map((id) => ({
    id,
    title: t(`gloss.group.${id}.title`),
    lead: t(`gloss.group.${id}.lead`),
  }));
}

export const GLOSSARY = {
  overview: { group: 'about' },
  readiness: { group: 'today' },
  session: { group: 'today' },
  training_load: { group: 'load' },
  fitness: { group: 'load' },
  fatigue: { group: 'load' },
  form: { group: 'load' },
  load_ratio: { group: 'load' },
  week: { group: 'load' },
  monotony: { group: 'load' },
  hrv: { group: 'recovery' },
  rhr: { group: 'recovery' },
  sleep: { group: 'recovery' },
  vdot: { group: 'performance' },
  cs: { group: 'performance' },
  predictions: { group: 'performance' },
  paces: { group: 'performance' },
  polar: { group: 'performance' },
  efficiency: { group: 'performance' },
  decoupling: { group: 'performance' },
  pace_check: { group: 'training' },
  projected: { group: 'training' },
  adherence: { group: 'training' },
  goal: { group: 'training' },
  coaching_ai: { group: 'coaching' },
  coaching_memory: { group: 'coaching' },
  coaching_human: { group: 'coaching' },
};

const ALIASES = { tsb: 'form', ctl: 'fitness' };

export function resolveKey(key) {
  return ALIASES[key] || key;
}

export function entry(key) {
  const id = resolveKey(key);
  const item = GLOSSARY[id];
  if (!item) return undefined;
  return {
    key: id,
    group: item.group,
    title: t(`gloss.${id}.title`),
    short: t(`gloss.${id}.short`),
    calc: t(`gloss.${id}.calc`),
    read: t(`gloss.${id}.read`),
  };
}

export function entriesIn(groupId) {
  return Object.keys(GLOSSARY)
    .filter((key) => GLOSSARY[key].group === groupId)
    .map((key) => entry(key));
}

export function info(key) {
  const item = entry(key);
  if (!item) return '';
  return `<button type="button" class="info" data-info="${item.key}" aria-label="${esc(t('gloss.what', { title: item.title }))}">?</button>`;
}

let open = null;

function close() {
  if (open) {
    open.node.remove();
    open.button.setAttribute('aria-expanded', 'false');
    open = null;
  }
}

export function installGlossary() {
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-info]');
    if (!button) {
      if (open && !open.node.contains(event.target)) close();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const same = open && open.button === button;
    close();
    if (same) return;
    const key = resolveKey(button.dataset.info);
    const item = entry(key);
    if (!item) return;
    const node = document.createElement('div');
    node.className = 'popover';
    node.setAttribute('role', 'tooltip');
    node.innerHTML = `<h3>${esc(item.title)}</h3><p>${esc(item.short)}</p>
      <a class="btn-link" href="#/guide/${encodeURIComponent(key)}">${esc(t('gloss.more'))}</a>`;
    document.body.appendChild(node);
    const rect = button.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    const left = Math.max(12, Math.min(rect.left + window.scrollX - 12, window.scrollX + window.innerWidth - width - 12));
    node.style.width = `${width}px`;
    node.style.left = `${left}px`;
    node.style.top = `${rect.bottom + window.scrollY + 8}px`;
    button.setAttribute('aria-expanded', 'true');
    open = { node, button };
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); });
  window.addEventListener('hashchange', close);
}
