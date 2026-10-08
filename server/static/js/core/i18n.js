/* UI language. English is the fallback for any missing Italian key.
   The choice lives in localStorage so it applies before sign-in. */
import { en } from '../i18n/en.js';
import { it } from '../i18n/it.js';

const CATALOGS = { en, it };
const STORAGE_KEY = 'garmincoach.lang';

const PHRASES = {
  'Recovery looks in line with your baseline.': 'reason.baseline',
  'Several recovery markers are off. Take the day.': 'reason.off',
  'Quality today would pile on. Swap in an easy run.': 'reason.quality',
  'Keep moving, but keep it easy.': 'reason.easy',
  "That's plenty for today, thank you. Try again tomorrow.": 'feedback.limit',
  today: 'time.today',
  yesterday: 'time.yesterday',
};

function readSaved() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function detect() {
  const saved = readSaved();
  if (saved === 'en' || saved === 'it') return saved;
  const nav = (typeof navigator !== 'undefined' && navigator.language || '').toLowerCase();
  return nav.startsWith('it') ? 'it' : 'en';
}

let locale = detect();
const listeners = new Set();

export function getLocale() {
  return locale;
}

export function dateLocale() {
  return locale === 'it' ? 'it-IT' : 'en-GB';
}

export function t(key, vars) {
  const catalog = CATALOGS[locale] || en;
  let text = catalog[key] ?? en[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value ?? ''));
    }
  }
  return text;
}

/** Known server phrases (readiness reasons, relative dates). Unknown text stays as-is. */
export function translatePhrase(text) {
  if (!text) return '';
  const key = PHRASES[text];
  if (key) return t(key);
  const days = String(text).match(/^(\d+) days ago$/);
  if (days) return t('time.daysAgo', { n: days[1] });
  return text;
}

export function applyDom(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => {
    const value = t(el.getAttribute('data-i18n'));
    const attr = el.getAttribute('data-i18n-attr');
    if (attr) {
      el.setAttribute(attr, value);
      return;
    }
    if (el.hasAttribute('data-i18n-html')) el.innerHTML = value;
    else el.textContent = value;
  });
}

export function setLocale(next) {
  if (next !== 'en' && next !== 'it') return;
  if (next === locale) return;
  locale = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* private mode */
  }
  document.documentElement.lang = next;
  applyDom();
  listeners.forEach((fn) => fn(next));
}

export function onLocale(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function initLocale() {
  document.documentElement.lang = locale === 'it' ? 'it' : 'en';
  applyDom();
}
