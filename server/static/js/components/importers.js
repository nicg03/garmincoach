/* The other ways in: the browser extension and Garmin's export zip. */
import { refreshStatus } from '../core/state.js';
import { $, esc, toast } from '../core/ui.js';

export function extensionHtml(idPrefix = 'ext') {
  return `<ol class="steps">
      <li><a class="btn-link" href="/extension.zip">Download the extension</a>, unzip it, open
        <code>chrome://extensions</code>, turn on <em>Developer mode</em> and click <em>Load unpacked</em>.</li>
      <li>Keep Garmin Connect signed in in the same browser.</li>
      <li>With this site open, click the extension icon and press <strong>Connect</strong>. It
        recognises the site by itself; if it asks, paste this address:</li>
    </ol>
    <div class="cmd" style="margin-top:8px">
      <pre id="${idPrefix}-origin">${esc(location.origin)}</pre>
      <button type="button" class="ghost" data-copy="${idPrefix}-origin">Copy</button>
    </div>`;
}

export function dropzoneHtml() {
  return `<div class="dropzone" data-drop tabindex="0" role="button" aria-label="Import a Garmin export zip">
      <strong>Drop Garmin's export zip here</strong>
      <p class="muted">Garmin: Account → Data Management → Export Your Data. When the email
        arrives, drop the zip here. Years of history in one go.</p>
      <input type="file" accept=".zip,application/zip" hidden data-file>
      <button type="button" class="ghost" data-browse>Choose a zip</button>
      <p class="muted" data-import-status aria-live="polite"></p>
    </div>`;
}

async function importExport(file, status, onDone) {
  const name = file.name || 'export.zip';
  if (!/\.zip$/i.test(name) && file.type !== 'application/zip') {
    status.textContent = 'That needs to be the zip Garmin emailed you.';
    return;
  }
  status.textContent = `Reading ${name}… this can take a minute.`;
  try {
    const response = await fetch('/api/import/garmin-export', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || body.detail || 'Import failed.');
    const stored = body.stored || {};
    const report = body.report || {};
    status.textContent = `Imported ${stored.activities || report.activities || 0} activities ` +
      `and ${stored.days || report.days || 0} days.`;
    toast('Export imported');
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
