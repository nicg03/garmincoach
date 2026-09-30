/* Small DOM helpers and the shared feedback widgets: tiles, toasts, confirm
   dialogs, skeletons, empty states, sub-navigation, markdown. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

export function fmt(value, digits = 0) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? '--' : Number(value).toFixed(digits);
}

export function signed(value, digits = 1) {
  if (value === null || value === undefined) return '';
  const n = Number(value);
  return (n > 0 ? '+' : '') + n.toFixed(digits);
}

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function shortDate(iso) {
  return iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) : '';
}

export function longDate(iso) {
  if (!iso) return '';
  const d = new Date(iso.slice(0, 10) + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

export function daysUntil(iso) {
  const a = new Date(todayIso() + 'T00:00:00');
  const b = new Date(iso.slice(0, 10) + 'T00:00:00');
  return Math.round((b - a) / 86400000);
}

/** "5 min ago" from epoch seconds. */
export function agoFromEpoch(seconds) {
  if (!seconds) return 'never';
  const minutes = Math.round((Date.now() / 1000 - seconds) / 60);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

export function minutes(seconds) {
  return seconds ? `${Math.round(seconds / 60)}'` : '--';
}

export function tile(label, value, note, tone, infoHtml = '') {
  return `<div class="stat"><div class="label">${label}${infoHtml}</div>` +
    `<div class="value">${value}</div>` +
    `<div class="note${tone ? ' ' + tone : ''}">${note || ''}</div></div>`;
}

export function skeleton(kind = 'block', count = 1) {
  return Array.from({ length: count }, () => `<div class="skeleton ${kind}"></div>`).join('');
}

export function loadingPage() {
  return `<div class="cards">${skeleton('tile', 3)}</div>` +
    `<div class="card">${skeleton('line', 2)}${skeleton('block')}</div>`;
}

export function emptyState(title, body = '', action = '') {
  return `<div class="empty"><strong>${title}</strong>${body ? `<span>${body}</span>` : ''}` +
    `${action ? `<div>${action}</div>` : ''}</div>`;
}

export function errorCard(error) {
  return `<div class="card"><p class="error">${esc(error.message || error)}</p></div>`;
}

export function subnav(base, items, current) {
  return `<nav class="subnav" aria-label="Section">${items.map(([key, label]) =>
    `<a href="${base}/${key}"${key === current ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
}

// ---- toasts -----------------------------------------------------------------
export function toast(message, tone = '') {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = document.createElement('div');
  node.className = `toast${tone ? ' ' + tone : ''}`;
  node.setAttribute('role', tone === 'bad' ? 'alert' : 'status');
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), tone === 'bad' ? 6000 : 3800);
}

// ---- confirm dialog -----------------------------------------------------------
/** Resolves true when confirmed. `requireText` makes the user type a value first. */
export function confirmDialog({ title, body = '', confirm = 'Confirm', danger = false, requireText = '' }) {
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'modal-back';
    back.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <h2 id="modal-title">${esc(title)}</h2>
      <p class="muted">${body}</p>
      ${requireText ? `<input type="text" data-confirm-input placeholder="${esc(requireText)}" autocomplete="off">` : ''}
      <div class="row-actions">
        <button type="button" class="ghost" data-cancel>Cancel</button>
        <button type="button" class="${danger ? 'ghost danger-button' : 'primary inline'}" data-ok
          ${requireText ? 'disabled' : ''}>${esc(confirm)}</button>
      </div>
    </div>`;
    const previous = document.activeElement;
    const close = (value) => {
      back.remove();
      document.removeEventListener('keydown', onKey);
      if (previous && previous.focus) previous.focus();
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') close(false); };
    document.addEventListener('keydown', onKey);
    back.addEventListener('click', (event) => { if (event.target === back) close(false); });
    $('[data-cancel]', back).addEventListener('click', () => close(false));
    const ok = $('[data-ok]', back);
    ok.addEventListener('click', () => close(true));
    const input = $('[data-confirm-input]', back);
    if (input) {
      input.addEventListener('input', () => {
        ok.disabled = input.value.trim().toLowerCase() !== requireText.toLowerCase();
      });
    }
    document.body.appendChild(back);
    (input || ok).focus();
  });
}

// ---- busy buttons -------------------------------------------------------------
/** Disables a button while `work` runs and reports failures as a toast. */
export async function busy(button, label, work) {
  const original = button ? button.textContent : '';
  if (button) {
    button.disabled = true;
    if (label) button.textContent = label;
  }
  try {
    return await work();
  } catch (error) {
    toast(error.message || String(error), 'bad');
    return undefined;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

export function bindCopy(root) {
  $$('[data-copy]', root).forEach((button) => {
    button.addEventListener('click', async () => {
      const source = document.getElementById(button.dataset.copy);
      try {
        await navigator.clipboard.writeText((source && source.textContent) || '');
        toast('Copied');
      } catch {
        toast('Select the text and copy it by hand', 'bad');
      }
    });
  });
}

// ---- markdown -----------------------------------------------------------------
/* Just enough markdown for what the model sends back. Wrapped lines are
   joined back into their paragraph or bullet first, otherwise emphasis that
   straddles a line break never closes. */
export function markdown(text) {
  const inline = (s) => s
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
  const blocks = [];
  let paragraph = [];
  let items = null;
  const flushParagraph = () => {
    if (paragraph.length) blocks.push(`<p>${inline(paragraph.join(' '))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (items) blocks.push(`<ul>${items.map((i) => `<li>${inline(i)}</li>`).join('')}</ul>`);
    items = null;
  };
  for (const raw of esc(text).split('\n')) {
    const line = raw.trim();
    if (!line) { flushParagraph(); flushList(); continue; }
    if (/^#{1,6}\s/.test(line)) {
      flushParagraph();
      flushList();
      blocks.push(`<h3>${inline(line.replace(/^#+\s*/, ''))}</h3>`);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      if (!items) items = [];
      items.push(bullet[1]);
    } else if (items) {
      items[items.length - 1] += ' ' + line;
    } else {
      paragraph.push(line);
    }
  }
  flushParagraph();
  flushList();
  return blocks.join('');
}
