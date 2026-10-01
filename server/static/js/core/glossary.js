/* One catalog for the "?" popovers and the Guide page. Popovers show `short`;
   the Guide adds how we calculate it and how to read it. */
import { esc } from './ui.js';

export const GROUPS = [
  { id: 'about', title: 'What this app does',
    lead: 'Today is the morning decision. Insights is the history. Training is the plan. Coaching is the conversation.' },
  { id: 'today', title: 'Today',
    lead: 'Are you recovered enough for what is on the calendar, and what should you do with that session.' },
  { id: 'load', title: 'Load',
    lead: 'How hard you have been training, and whether the last week is ahead of the fitness you have already built.' },
  { id: 'recovery', title: 'Recovery',
    lead: 'Whether you are absorbing that work. Absolute numbers barely matter; the gap to your own baseline does.' },
  { id: 'performance', title: 'Performance',
    lead: 'Whether you are getting faster: race fitness, how the weeks are polarised, and whether long runs still hold together.' },
  { id: 'training', title: 'Training',
    lead: 'The block that builds to a race, the paces that land on the watch, and whether you are running them as written.' },
  { id: 'coaching', title: 'Coaching',
    lead: 'An AI that has read your tables, and optional room for a human who sees the same numbers.' },
];

export const GLOSSARY = {
  overview: {
    group: 'about',
    title: 'The four places',
    short: 'Garmin data, a plan and a coach in one place.',
    calc: 'We store the days and activities Garmin syncs, then compute load, recovery and race fitness from that window. Nothing here is a second watch — it is your watch, summarised.',
    read: 'Today answers what to do this morning. Insights is load, recovery, performance and the activity log. Training holds races, the calendar and workout builder. Coaching is the briefing, the chat, and any human coach you add.',
  },
  readiness: {
    group: 'today',
    title: 'Readiness',
    short: 'A morning call: train, ease, swap or rest.',
    calc: 'Flags stack from HRV (≤ −5 vs baseline = 2), Garmin training readiness under 40 (= 2), resting HR ≥ +3, sleep score under 60, load ratio ≥ 1.4, and form under −30. Quality days (intervals, tempo, long) need fewer flags before we ease or rest.',
    read: 'Keep as planned is the default. Several markers off, especially before a hard day, is rest. Two flags is usually “keep moving, but easy”.',
  },
  session: {
    group: 'today',
    title: "Today's session",
    short: 'What was on the plan, and what you can do with it.',
    calc: 'The session dated today from your active plan, including one-off workouts and anything a coach assigned.',
    read: 'Keep as planned leaves it. Make it easy turns it into an easy run. Swap looks for an easier day later in the week. Rest takes the day. The choice is stored; the next watch sync can pick it up.',
  },
  training_load: {
    group: 'load',
    title: 'Training load',
    short: 'How much work that day actually was.',
    calc: "Garmin's training load when the device reports it. Otherwise duration × aerobic training effect × 0.5, so easy walks still count instead of vanishing.",
    read: 'Bars on the Load chart. Rest days are zero — that is what lets fitness and fatigue decay. A single huge bar is one session; the lines are the story.',
  },
  fitness: {
    group: 'load',
    title: 'Fitness',
    short: 'Chronic training load — the fitness you have built.',
    calc: 'Exponential moving average of daily load over ~42 days (CTL).',
    read: 'It moves slowly. A race plan aims to lift this into the goal week, then taper. Fitness at race on the calendar is the projected value on race day.',
  },
  fatigue: {
    group: 'load',
    title: 'Fatigue',
    short: 'Acute training load — what you feel this week.',
    calc: 'Exponential moving average of daily load over ~7 days (ATL).',
    read: 'It jumps after a hard week and drops on rest. Form is Fitness minus this number.',
  },
  form: {
    group: 'load',
    title: 'Form',
    short: 'Fitness minus fatigue. Positive means you are fresh.',
    calc: 'CTL − ATL (Banister-style TSB).',
    read: 'A dip to −10/−20 in a build is normal (the tile says “building”). Under −25 is “deep in the hole”. Under −20 with HRV also down: ease off. Positive is “fresh” — or detraining if load ratio is also low.',
  },
  load_ratio: {
    group: 'load',
    title: 'Load ratio',
    short: "This week's fatigue versus longer-term fitness.",
    calc: 'ATL / CTL, the acute:chronic workload ratio.',
    read: 'About 0.8–1.3 is productive. Below 0.8 is detraining. Above 1.3 is ramping fast; above 1.5 is where niggles cluster. Planned weeks are capped at 1.25.',
  },
  week: {
    group: 'load',
    title: 'This week',
    short: 'Kilometres, sessions and load since Monday.',
    calc: 'Sum of activities in the current ISO week.',
    read: 'A snapshot, not a target. Weekly volume on Insights stacks hours by sport with a load line on top.',
  },
  monotony: {
    group: 'load',
    title: 'Monotony',
    short: 'How samey the last seven days of load were.',
    calc: 'Foster: mean daily load divided by its standard deviation. Strain is monotony × total load for those days.',
    read: 'Above ~2 the week is too even: strain climbs even if no single day looks huge. Mix hard and easy, or add a rest day.',
  },
  hrv: {
    group: 'recovery',
    title: 'HRV',
    short: 'Heart-rate variability versus your own baseline.',
    calc: "Garmin's overnight HRV. Baseline is a 7-day trailing mean. The number that matters is the gap.",
    read: 'Down, with resting HR up, for two or three mornings is the signal. One odd night is noise. Garmin\'s status label is extra colour, not a second source of truth.',
  },
  rhr: {
    group: 'recovery',
    title: 'Resting HR',
    short: 'Morning heart rate versus your own baseline.',
    calc: 'Garmin daily or sleep resting HR, with the same 7-day trailing baseline as HRV.',
    read: 'A few beats above baseline, especially with HRV down, says you are not recovered. Isolated spikes after travel or alcohol are common.',
  },
  sleep: {
    group: 'recovery',
    title: 'Sleep',
    short: "Garmin's 0–100 score, plus hours and stages.",
    calc: 'Duration and stage mix from the watch. All-zero nights are treated as missing, not as no sleep.',
    read: 'Use it with HRV, not instead of it. A short night before a quality day is a reason to swap, not to hero through intervals.',
  },
  vdot: {
    group: 'performance',
    title: 'VDOT',
    short: "Jack Daniels' index of current race fitness.",
    calc: 'From your best recent 5k–half (time and distance → VO2). Training paces are percentages of this number. A profile override, if you set one, wins over the auto value.',
    read: 'It is what you can race now, not the time you wish you could run.',
  },
  cs: {
    group: 'performance',
    title: 'Critical speed',
    short: 'A threshold-ish pace from two mid-distance PRs.',
    calc: 'Distance difference ÷ time difference between your best efforts from 800 m to 15 km, typically a 3k and a 5k.',
    read: 'Roughly threshold pace. Used as a fallback for training paces when VDOT is not available yet.',
  },
  predictions: {
    group: 'performance',
    title: 'Race predictions',
    short: 'Three estimates of race time at common distances.',
    calc: "VDOT inverts Daniels' tables. Riegel scales your best recent race (exponent 1.06). Garmin is whatever the watch last published.",
    read: 'They will not agree. VDOT and Riegel come from your own efforts; Garmin is a black box. Goal vs fitness on the race list uses VDOT or Riegel.',
  },
  paces: {
    group: 'performance',
    title: 'Training paces',
    short: 'Easy through repetition, from current VDOT.',
    calc: 'Daniels %VO2: Easy 59–74%, Marathon ~80%, Threshold 88%, Interval 98%, Repetition 105%. Goal race pace is your A-goal if set. Easy is a range; T/I/R sit a few seconds either side of the midpoint.',
    read: 'These are the paces that land on the watch. They move when VDOT moves, or when you accept a Pace check suggestion.',
  },
  polar: {
    group: 'performance',
    title: 'Easy vs hard',
    short: 'Share of running time in HR zones 1–2 versus 3–5.',
    calc: 'Last 42 days of runs that have zone data. Polarised training sits near 80% easy.',
    read: 'If hard is winning, the easy days are not easy enough. Walks without HR zones do not count in the split.',
  },
  efficiency: {
    group: 'performance',
    title: 'Aerobic efficiency',
    short: 'Heart rate per unit of pace on steady runs.',
    calc: 'Average HR ÷ pace (min/km) on runs of at least 5 km slower than 4:00/km, so interval days drop out.',
    read: 'Falling over weeks means fitter at the same speed. A noisy week is not a trend.',
  },
  decoupling: {
    group: 'performance',
    title: 'Decoupling',
    short: 'Second-half fade versus the first, at similar effort.',
    calc: 'From kilometre splits: (second-half pace − first-half pace) / first-half. Needs at least four splits.',
    read: 'Over ~5% on a long run means the duration outran current endurance. Shorter quality sessions will show some drift; that is not the same test.',
  },
  pace_check: {
    group: 'training',
    title: 'Pace check',
    short: 'Quality sessions versus the range that was on the watch.',
    calc: 'Actual split pace compared with the planned band: too fast, too slow, or on target. Enough quality days in one direction can suggest a VDOT shift.',
    read: 'Nothing changes until you accept. Dismiss keeps current paces.',
  },
  projected: {
    group: 'training',
    title: 'Projected load',
    short: 'Fitness and fatigue if you complete the plan as written.',
    calc: "Seeded with the last ~60 days of real load, then each planned session's estimated load, smoothed with the same 7-day and 42-day averages.",
    read: 'A sketch of the block, not a promise. Rest days and skips will pull the lines down.',
  },
  adherence: {
    group: 'training',
    title: 'Adherence',
    short: 'Planned sessions done versus skipped, up to today.',
    calc: 'Counts non-rest, non-race sessions dated today or earlier. Ratio is completed / planned.',
    read: 'The Sessions tile on the calendar. Skips are not failures if readiness asked for them.',
  },
  goal: {
    group: 'training',
    title: 'Goal vs fitness',
    short: 'Is the race goal ahead of, on, or behind current fitness.',
    calc: 'Goal time versus the VDOT (or Riegel) prediction for that distance. More than 3% slower than predicted is comfortable; within 3% is on pace; 3–8% faster is a stretch; more than 8% is ambitious.',
    read: 'Ambitious is fine if the block is long enough. It is not a verdict on the goal, just on the gap today.',
  },
  coaching_ai: {
    group: 'coaching',
    title: 'AI coach',
    short: 'A briefing and a chat over your own numbers.',
    calc: 'Last ~90 days of load and recovery as CSV, weekly rollups for the whole history, recent sessions, current plan and races. The model never sees raw Garmin dumps.',
    read: 'It answers like a coach who has the tables. It is not a doctor. Daily question limits apply if the site owner set them.',
  },
  coaching_human: {
    group: 'coaching',
    title: 'Human coach',
    short: 'A person you add, who sees the same numbers.',
    calc: 'No extra formulas. They see Form, Load ratio, HRV delta, weekly kilometres, last data and next race. They can assign workouts and leave notes; they cannot rewrite your plan.',
    read: 'You invite them from Coaching with their email. Notes show on Today.',
  },
};

const ALIASES = { tsb: 'form', ctl: 'fitness' };

export function resolveKey(key) {
  return ALIASES[key] || key;
}

export function entry(key) {
  return GLOSSARY[resolveKey(key)];
}

export function entriesIn(groupId) {
  return Object.entries(GLOSSARY)
    .filter(([, item]) => item.group === groupId)
    .map(([key, item]) => ({ key, ...item }));
}

export function info(key) {
  const item = entry(key);
  if (!item) return '';
  const id = resolveKey(key);
  return `<button type="button" class="info" data-info="${id}" aria-label="What is ${esc(item.title)}?">?</button>`;
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
    const item = GLOSSARY[key];
    if (!item) return;
    const node = document.createElement('div');
    node.className = 'popover';
    node.setAttribute('role', 'tooltip');
    node.innerHTML = `<h3>${esc(item.title)}</h3><p>${esc(item.short)}</p>
      <a class="btn-link" href="#/guide/${encodeURIComponent(key)}">More in the Guide</a>`;
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
