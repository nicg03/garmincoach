/* First run: three steps to the first chart. Connecting Garmin directly is
   the main path; the extension and the zip sit under "Other ways". The shell
   polls status while there's no data, and Today redraws once it lands. */
import { renderGarmin } from '../components/garmin-connect.js';
import { bindDropzone, dropzoneHtml, extensionHtml } from '../components/importers.js';
import { rerender } from '../core/router.js';
import { state } from '../core/state.js';
import { $, bindCopy, esc } from '../core/ui.js';

export function renderOnboarding(root) {
  const status = state.status;
  const g = status.garmin || {};
  const last = status.last_ingest;
  const linked = Boolean(g.connected || last);
  const steps = [
    { title: 'Account created', body: esc((status.user || {}).email || ''), done: true },
    {
      title: 'Connect Garmin',
      body: 'Sign in to Garmin once. We fetch your recent weeks, then keep up by ourselves.',
      done: linked,
    },
    {
      title: 'First data arrives',
      body: g.running ? 'Fetching now. This page fills in by itself.' : 'Usually within a minute of connecting.',
      done: false,
    },
  ];
  const current = steps.findIndex((s) => !s.done);
  const doneCount = steps.filter((s) => s.done).length;
  const emptyIngest = last && last.stored && !(last.stored.activities || last.stored.days);

  root.innerHTML = `
    <div class="card setup-card">
      <p class="kicker">Getting started</p>
      <h2>Let's bring in your training</h2>
      <p class="lede muted">Once your Garmin data is here you get daily readiness, training
        load, race plans and a coach that reads your numbers.</p>
      <div class="progress" aria-hidden="true"><i style="width:${Math.round(doneCount / steps.length * 100)}%"></i></div>
      <ol class="checklist">
        ${steps.map((s, i) => `<li class="${s.done ? 'done' : i === current ? 'current' : ''}">
          <span class="tick">${s.done ? '✓' : i + 1}</span>
          <div><strong>${s.title}</strong><span class="muted">${s.body}</span></div>
        </li>`).join('')}
      </ol>
      ${emptyIngest && !g.running ? `<div class="banner warn">${esc(last.error ||
        'The last sync stored nothing. Check that Garmin Connect has data for this account, then sync again.')}</div>` : ''}
      <div data-garmin></div>
    </div>

    <details class="card advanced" ${g.available ? '' : 'open'}>
      <summary>Other ways to import</summary>
      <div class="grid-2" style="margin-top:14px">
        <div>
          <h3>Browser extension</h3>
          <p class="muted">Syncs through your own Garmin Connect session in Chrome.</p>
          ${extensionHtml('onboard')}
        </div>
        <div>
          <h3>Garmin export zip</h3>
          ${dropzoneHtml()}
        </div>
      </div>
    </details>`;

  renderGarmin($('[data-garmin]', root), { onChange: rerender });
  bindDropzone(root, rerender);
  bindCopy(root);
}
