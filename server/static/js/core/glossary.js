/* The explanations that used to sit in always-visible blocks, now behind a
   "?" next to the number or chart they explain. */
import { esc } from './ui.js';

export const GLOSSARY = {
  tsb: {
    title: 'Form (TSB)',
    body: 'CTL minus ATL. Positive means you\'re fresh; negative means fatigue is ahead of fitness. A dip to −10/−20 in a build is normal. If it stays under −20 and HRV is also down, ease off.',
  },
  ctl: {
    title: 'CTL, ATL and load ratio',
    body: 'CTL is ~42-day fitness, ATL ~7-day fatigue. The load ratio is ATL/CTL (ACWR). About 0.8–1.3 is productive. Above 1.5 is where niggles cluster. Planned weeks are capped at 1.25.',
  },
  hrv: {
    title: 'HRV and resting HR',
    body: 'Only the gap to your own baseline matters. HRV down and resting HR up for two or three mornings is the real signal. One odd night is noise.',
  },
  sleep: {
    title: 'Sleep score',
    body: 'Garmin\'s 0–100 blend of duration and stages. Use it with HRV, not instead of it. A short night before a quality day is a reason to swap, not to hero through intervals.',
  },
  vdot: {
    title: 'VDOT',
    body: 'Jack Daniels\' index of current race fitness, from your best recent 5k–half. Training paces (E/M/T/I/R) are percentages of this number, not of the time you wish you could run.',
  },
  polar: {
    title: '80/20',
    body: 'Share of running time in HR zones 1–2 vs 3–5 over six weeks. Polarised training sits near 80% easy. If hard is winning, the easy days are not easy enough.',
  },
  monotony: {
    title: 'Foster monotony',
    body: 'Mean daily load divided by its standard deviation over 7 days. Above ~2 the week is too samey: strain (monotony × total load) climbs even if no single day looks huge.',
  },
  decoupling: {
    title: 'Decoupling',
    body: 'How much second-half pace fades vs the first at similar HR. Aerobic decoupling over ~5% on a long run means the duration outran your current endurance.',
  },
};

export function info(key) {
  const entry = GLOSSARY[key];
  if (!entry) return '';
  return `<button type="button" class="info" data-info="${key}" aria-label="What is ${esc(entry.title)}?">?</button>`;
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
    const entry = GLOSSARY[button.dataset.info];
    const node = document.createElement('div');
    node.className = 'popover';
    node.setAttribute('role', 'tooltip');
    node.innerHTML = `<h3>${esc(entry.title)}</h3><p>${esc(entry.body)}</p>`;
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
