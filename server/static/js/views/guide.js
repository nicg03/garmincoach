/* Guide: the long form of the "?" popovers, grouped the same way Insights is.
   Deep links are `#/guide/form` so a tooltip can land on the matching entry. */
import { GROUPS, entriesIn, resolveKey } from '../core/glossary.js';
import { esc } from '../core/ui.js';

export async function render(root, ctx = {}) {
  const wanted = resolveKey((ctx.path || [])[0]);
  const toc = GROUPS.map((g) => `<a href="#/guide/${g.id}">${esc(g.title)}</a>`).join('');
  const sections = GROUPS.map((group) => {
    const items = entriesIn(group.id);
    return `<section class="guide-section" data-guide-group="${esc(group.id)}" id="${esc(group.id)}">
      <h2 class="section-title">${esc(group.title)}</h2>
      ${group.lead ? `<p class="muted">${esc(group.lead)}</p>` : ''}
      ${items.map((item) => `<article class="card guide-entry" data-guide-entry="${esc(item.key)}" id="${esc(item.key)}">
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.short)}</p>
        <dl>
          <dt>How we calculate it</dt><dd>${esc(item.calc)}</dd>
          <dt>How to read it</dt><dd>${esc(item.read)}</dd>
        </dl>
      </article>`).join('')}
    </section>`;
  }).join('');

  root.innerHTML = `
    <p class="lede">Every number on the site, what it is, how we get it, and what to do with it.
      The "?" next to a tile is the short version of the same text.</p>
    <nav class="guide-toc" aria-label="Guide">${toc}</nav>
    ${sections}`;

  const id = /^[a-z_][a-z0-9_]*$/.test(wanted || '') ? wanted : '';
  const target = id && (
    root.querySelector(`[data-guide-entry="${id}"]`) ||
    root.querySelector(`[data-guide-group="${id}"]`)
  );
  if (target) requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
}
