/* First run: three steps to the first chart. Connecting Garmin directly is
   the main path; the extension and the zip sit under "Other ways". The shell
   polls status while there's no data, and Today redraws once it lands. */
import { renderGarmin } from '../components/garmin-connect.js';
import { bindDropzone, dropzoneHtml, extensionHtml } from '../components/importers.js';
import { t } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { state } from '../core/state.js';
import { $, bindCopy, esc } from '../core/ui.js';

export function renderOnboarding(root) {
  const status = state.status;
  const g = status.garmin || {};
  const last = status.last_ingest;
  const linked = Boolean(g.connected || last);
  const steps = [
    { title: t('onboard.created'), body: esc((status.user || {}).email || ''), done: true },
    {
      title: t('onboard.connect'),
      body: esc(t('onboard.connectHelp')),
      done: linked,
    },
    {
      title: t('onboard.arrives'),
      body: esc(g.running ? t('onboard.fetching') : t('onboard.usually')),
      done: false,
    },
  ];
  const current = steps.findIndex((s) => !s.done);
  const doneCount = steps.filter((s) => s.done).length;
  const emptyIngest = last && last.stored && !(last.stored.activities || last.stored.days);

  root.innerHTML = `
    <div class="card setup-card">
      <p class="kicker">${esc(t('onboard.kicker'))}</p>
      <h2>${esc(t('onboard.title'))}</h2>
      <p class="lede muted">${esc(t('onboard.lede'))}</p>
      <div class="progress" aria-hidden="true"><i style="width:${Math.round(doneCount / steps.length * 100)}%"></i></div>
      <ol class="checklist">
        ${steps.map((s, i) => `<li class="${s.done ? 'done' : i === current ? 'current' : ''}">
          <span class="tick">${s.done ? '✓' : i + 1}</span>
          <div><strong>${esc(s.title)}</strong><span class="muted">${s.body}</span></div>
        </li>`).join('')}
      </ol>
      ${emptyIngest && !g.running ? `<div class="banner warn">${esc(last.error || t('onboard.emptySync'))}</div>` : ''}
      <div data-garmin></div>
    </div>

    <details class="card advanced" ${g.available ? '' : 'open'}>
      <summary>${esc(t('onboard.other'))}</summary>
      <div class="grid-2" style="margin-top:14px">
        <div>
          <h3>${esc(t('onboard.extension'))}</h3>
          <p class="muted">${esc(t('onboard.extensionHelp'))}</p>
          ${extensionHtml('onboard')}
        </div>
        <div>
          <h3>${esc(t('onboard.zip'))}</h3>
          ${dropzoneHtml()}
        </div>
      </div>
    </details>`;

  renderGarmin($('[data-garmin]', root), { onChange: rerender });
  bindDropzone(root, rerender);
  bindCopy(root);
}
