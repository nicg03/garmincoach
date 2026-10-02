/* Guide: the long form of the "?" popovers, grouped the same way Insights is.
   Deep links are `#/guide/form` so a tooltip can land on the matching entry. */
import { entriesIn, groups, resolveKey } from '../core/glossary.js';
import { t } from '../core/i18n.js';
import { esc } from '../core/ui.js';

export async function render(root, ctx = {}) {
  const wanted = resolveKey((ctx.path || [])[0]);
  const catalog = groups();
  const toc = catalog.map((g) => `<a href="#/guide/${g.id}">${esc(g.title)}</a>`).join('');
  const sections = catalog.map((group) => {
    const items = entriesIn(group.id);
    return `<section class="guide-section" data-guide-group="${esc(group.id)}" id="${esc(group.id)}">
      <h2 class="section-title">${esc(group.title)}</h2>
      ${group.lead ? `<p class="muted">${esc(group.lead)}</p>` : ''}
      ${items.map((item) => `<article class="card guide-entry" data-guide-entry="${esc(item.key)}" id="${esc(item.key)}">
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.short)}</p>
        <dl>
          <dt>${esc(t('guide.calc'))}</dt><dd>${esc(item.calc)}</dd>
          <dt>${esc(t('guide.read'))}</dt><dd>${esc(item.read)}</dd>
        </dl>
      </article>`).join('')}
    </section>`;
  }).join('');

  root.innerHTML = `
    <p class="lede">${esc(t('guide.lede'))}</p>
    <nav class="guide-toc" aria-label="${esc(t('guide.nav'))}">${toc}</nav>
    ${sections}`;

  const id = /^[a-z_][a-z0-9_]*$/.test(wanted || '') ? wanted : '';
  const target = id && (
    root.querySelector(`[data-guide-entry="${id}"]`) ||
    root.querySelector(`[data-guide-group="${id}"]`)
  );
  if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
}
