/* Admin area: every registered account, for the emails listed in ADMIN_EMAILS.
   Search and the role filter work on the list already loaded. */
import { api } from '../core/api.js';
import { dateLocale, t } from '../core/i18n.js';
import { $, emptyState, esc, tile, todayIso } from '../core/ui.js';

function fullDate(iso) {
  if (!iso) return '—';
  return new Date(iso.slice(0, 10) + 'T00:00:00')
    .toLocaleDateString(dateLocale(), { day: 'numeric', month: 'short', year: 'numeric' });
}

function daysAgo(n) {
  const d = new Date(todayIso() + 'T00:00:00');
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function row(user) {
  const role = user.role === 'coach' ? t('admin.coach') : t('admin.athlete');
  return `<tr>
    <td class="left"><strong>${esc(user.email)}</strong></td>
    <td class="left"><span class="badge${user.role === 'coach' ? ' warn' : ''}">${esc(role)}</span></td>
    <td>${esc(fullDate(user.created))}</td>
    <td>${user.email_verified
      ? `<span class="badge ok">${esc(t('admin.yes'))}</span>`
      : `<span class="muted">${esc(t('admin.no'))}</span>`}</td>
  </tr>`;
}

export async function render(root) {
  const { users = [] } = await api('/api/admin/users');
  const coaches = users.filter((u) => u.role === 'coach').length;
  const weekAgo = daysAgo(7);
  const recent = users.filter((u) => (u.created || '') >= weekAgo).length;
  const verified = users.filter((u) => u.email_verified).length;

  root.innerHTML = `
    <div class="cards">
      ${tile(esc(t('admin.total')), users.length, '')}
      ${tile(esc(t('admin.athletes')), users.length - coaches, '')}
      ${tile(esc(t('admin.coaches')), coaches, '')}
      ${tile(esc(t('admin.newWeek')), recent, '')}
      ${tile(esc(t('admin.verified')), verified, '')}
    </div>
    <div class="card">
      <div class="card-head">
        <h2>${esc(t('admin.accounts'))} <span class="muted" data-shown>${users.length}</span></h2>
        <span class="spacer"></span>
        <select data-role style="width:auto">
          <option value="">${esc(t('admin.allRoles'))}</option>
          <option value="athlete">${esc(t('admin.athletes'))}</option>
          <option value="coach">${esc(t('admin.coaches'))}</option>
        </select>
        <input type="search" data-search placeholder="${esc(t('admin.search'))}" style="width:240px" autocomplete="off">
      </div>
      ${users.length ? `<div class="scroll"><table>
        <thead><tr><th class="left">${esc(t('admin.email'))}</th><th class="left">${esc(t('admin.role'))}</th>
          <th>${esc(t('admin.since'))}</th><th>${esc(t('admin.emailVerified'))}</th></tr></thead>
        <tbody data-rows></tbody></table></div>
        <div data-none class="hidden">${emptyState(esc(t('admin.noMatch')))}</div>`
        : emptyState(esc(t('admin.empty')))}
    </div>`;

  const body = $('[data-rows]', root);
  if (!body) return;
  const search = $('[data-search]', root);
  const roleFilter = $('[data-role]', root);
  const apply = () => {
    const needle = search.value.trim().toLowerCase();
    const wanted = roleFilter.value;
    const shown = users.filter((u) => (!needle || u.email.includes(needle))
      && (!wanted || u.role === wanted));
    body.innerHTML = shown.map(row).join('');
    $('[data-shown]', root).textContent = shown.length === users.length
      ? String(users.length) : `${shown.length} / ${users.length}`;
    $('[data-none]', root).classList.toggle('hidden', shown.length > 0);
  };
  search.addEventListener('input', apply);
  roleFilter.addEventListener('change', apply);
  apply();
}
