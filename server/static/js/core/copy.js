/* Server text (plan names, phases, purposes) is stored in English because
   that is what the watch receives. The site shows Italian when that is the locale. */
import { getLocale, t } from './i18n.js';

const PURPOSE = {
  'Very easy jogging to loosen the legs after a hard day.': 'purpose.recovery',
  'Aerobic running at conversational effort. Most of the week\'s kilometres live here.': 'purpose.easy',
  'Pfitzinger\'s midweek endurance run: longer than an easy day, still conversational.': 'purpose.mediumLong',
  'Aerobic endurance. Keep it easy — long is a duration stimulus, not a race.': 'purpose.long',
  'Long run that finishes at race-relevant pace so the last kilometres teach control, not just time on feet.': 'purpose.progression',
  'Marathon-pace work inside a long run — the race-specific session in a marathon or late half block.': 'purpose.mp',
  'Lactate threshold: comfortably hard, about the effort you could hold for an hour.': 'purpose.tempo',
  'Cruise intervals: threshold pace broken into repeats with a short jog so you can accumulate more T than a single tempo.': 'purpose.cruise',
  'VO2max intervals (3–5 min at I). Jog recovery about as long as the rep so you actually hit VO2max, not just go anaerobic.': 'purpose.vo2',
  'Repetition pace: short, fast, full recovery. Economy and speed, not fatigue.': 'purpose.reps',
  'Alternating threshold and easy 400s. Teaches changing gears without a full stop.': 'purpose.mix',
  'A short time trial to check current fitness. Run it honestly; it feeds VDOT and the next block\'s paces.': 'purpose.tt',
  'Keep neuromuscular snap during a taper without loading the legs.': 'purpose.strides',
  'Race day. Trust the taper; start controlled.': 'purpose.race',
};

const PACE = {
  '5k pace': 'pace.5k',
  '10k pace': 'pace.10k',
  'half pace': 'pace.half',
  'marathon pace': 'pace.marathon',
};

const PATTERNS = [
  [/^Recovery ([\d.]+) km$/, 'woName.recovery', ['n']],
  [/^Easy ([\d.]+) km \+ strides$/, 'woName.easyStrides', ['n']],
  [/^Easy ([\d.]+) km$/, 'woName.easy', ['n']],
  [/^Easy \+ strides$/, 'woName.strides', []],
  [/^Medium-long ([\d.]+) km$/, 'woName.mediumLong', ['n']],
  [/^Long ([\d.]+) km$/, 'woName.long', ['n']],
  [/^Progression long$/, 'woName.progression', []],
  [/^MP long \(([\d.]+) km M\)$/, 'woName.mp', ['n']],
  [/^Tempo (\d+) min$/, 'woName.tempo', ['n']],
  [/^(\d+)×(\d+)m T$/, 'woName.cruise', ['n', 'm']],
  [/^(\d+)×(\d+) m I$/, 'woName.vo2', ['n', 'm']],
  [/^(\d+)×400 m R$/, 'woName.reps', ['n']],
  [/^(\d+)×\(400 T \+ 400 E\)$/, 'woName.mix', ['n']],
  [/^([\d.]+) km (5k pace|10k pace|half pace|marathon pace)$/, 'woName.racePace', ['n', 'pace']],
  [/^([\d.]+)k time trial$/, 'woName.tt', ['n']],
  [/^Race-pace rehearsal: ([\d.]+) km at ([^,]+), not an all-out time trial\.$/, 'purpose.racePace', ['n', 'pace']],
  [/^The last (\d+) quality sessions were faster than the planned range\. Accept to tighten targets by (\d+) s\/km \(about VDOT \+(\d+)\)\.$/, 'insight.faster', ['n', 's', 'v']],
  [/^The last (\d+) quality sessions were slower than the planned range\. Accept to ease targets by (\d+) s\/km \(about VDOT (-?\d+)\)\.$/, 'insight.slower', ['n', 's', 'v']],
];

function catalog(prefix, value) {
  const key = `${prefix}.${value}`;
  const label = t(key);
  return label === key ? null : label;
}

function scrub(text) {
  return text
    .replace(/\bWU\b/g, t('step.wu'))
    .replace(/\bCD\b/g, t('step.cd'))
    .replace(/\brec\b/g, t('step.rec'))
    .replace(/\brest\b/g, t('step.rest'))
    .replace(/\breps\b/g, t('step.reps'))
    .replace(/\blap\b/g, t('step.lap'));
}

/** English server copy, shown in the active language. Unknown text stays as stored. */
export function showText(text) {
  if (text == null || text === '') return '';
  const raw = String(text);
  if (getLocale() !== 'it') return raw;
  if (PURPOSE[raw]) return t(PURPOSE[raw]);
  for (const prefix of ['phase', 'kind', 'family', 'sport', 'int']) {
    const label = catalog(prefix, raw);
    if (label) return label;
  }
  const pace = PACE[raw];
  if (pace) return t(pace);
  for (const [pattern, key, names] of PATTERNS) {
    const match = raw.match(pattern);
    if (!match) continue;
    const vars = {};
    names.forEach((name, index) => {
      const value = match[index + 1];
      vars[name] = PACE[value] ? t(PACE[value]) : value;
    });
    return t(key, vars);
  }
  if (/\b(WU|CD|rec|rest|reps|lap)\b| → /.test(raw)) return scrub(raw);
  return raw;
}
