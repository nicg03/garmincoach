/* The other ways in: the browser extension and Garmin's export zip. */
import { t } from '../core/i18n.js';
import { refreshStatus } from '../core/state.js';
import { $, esc, toast } from '../core/ui.js';

export function extensionHtml(idPrefix = 'ext') {
  return `<ol class="steps">
      <li><a class="btn-link" href="/extension.zip">${esc(t('import.step1a'))}</a>${esc(t('import.step1b'))}
        <code>chrome://extensions</code>${esc(t('import.step1c'))} <em>${esc(t('import.step1d'))}</em> ${esc(t('import.step1e'))} <em>${esc(t('import.step1f'))}</em>.</li>
      <li>${esc(t('import.step2'))}</li>
      <li>${esc(t('import.step3'))}</li>
    </ol>
    <div class="cmd" style="margin-top:8px">
      <pre id="${idPrefix}-origin">${esc(location.origin)}</pre>
      <button type="button" class="ghost" data-copy="${idPrefix}-origin">${esc(t('ui.copy'))}</button>
    </div>`;
}

export function dropzoneHtml() {
  return `<div class="dropzone" data-drop tabindex="0" role="button" aria-label="${esc(t('import.dropLabel'))}">
      <strong>${esc(t('import.drop'))}</strong>
      <p class="muted">${esc(t('import.dropHelp'))}</p>
      <input type="file" accept=".zip,application/zip" hidden data-file>
      <button type="button" class="ghost" data-browse>${esc(t('import.choose'))}</button>
      <p class="muted" data-import-status aria-live="polite"></p>
    </div>`;
}

async function importExport(file, status, onDone) {
  const name = file.name || 'export.zip';
  if (!/\.zip$/i.test(name) && file.type !== 'application/zip') {
    status.textContent = t('import.needZip');
    return;
  }
  status.textContent = t('import.reading', { name });
  try {
    const response = await fetch('/api/import/garmin-export', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || body.detail || t('import.failed'));
    const stored = body.stored || {};
    const report = body.report || {};
    status.textContent = t('import.done', {
      activities: stored.activities || report.activities || 0,
      days: stored.days || report.days || 0,
    });
    toast(t('import.toast'));
    await refreshStatus();
    onDone();
  } catch (error) {
    status.textContent = error.message;
  }
}

export function bindDropzone(root, onDone = () => {}) {
  const zone = $('[data-drop]', root);
  if (!zone) return;
  const input = $('[data-file]', zone);
  const browse = $('[data-browse]', zone);
  const status = $('[data-import-status]', zone);
  const pick = () => input.click();
  zone.addEventListener('click', (event) => { if (event.target !== browse) pick(); });
  zone.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); }
  });
  browse.addEventListener('click', (event) => { event.stopPropagation(); pick(); });
  zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('drag');
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) importExport(file, status, onDone);
  });
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    if (file) importExport(file, status, onDone);
    input.value = '';
  });
}
