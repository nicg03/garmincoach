/* Coaching: the AI coach (briefing and chat) and, for athletes, the human
   coach they're linked to. The conversation survives moving between pages. */
import { api, post } from '../core/api.js';
import { info } from '../core/glossary.js';
import { getLocale, t } from '../core/i18n.js';
import { rerender } from '../core/router.js';
import { cached, invalidate, role, state } from '../core/state.js';
import { $, $$, busy, emptyState, esc, loadingPage, markdown, skeleton, subnav, toast } from '../core/ui.js';

function suggestions() {
  return [t('coach.q1'), t('coach.q2'), t('coach.q3'), t('coach.q4')];
}

function linkLabel(status) {
  const key = `link.${status}`;
  const label = t(key);
  return label === key ? status : label;
}

function noteKind(kind) {
  const label = t('note.' + kind);
  return label === 'note.' + kind ? kind : label;
}

export async function render(root, ctx = {}) {
  const tabs = role() === 'athlete' ? [['ai', t('coach.ai')], ['coach', t('coach.mine')]] : [['ai', t('coach.ai')]];
  const wanted = (ctx.path || [])[0];
  const sub = tabs.some(([key]) => key === wanted) ? wanted : 'ai';
  root.innerHTML = `<div class="page-head">${tabs.length > 1 ? subnav('#/coaching', tabs, sub) : ''}
    <a class="btn-link" href="#/guide/coaching">${esc(t('coach.guide'))}</a></div><div data-sub>${loadingPage()}</div>`;
  const host = $('[data-sub]', root);
  if (sub === 'coach') return humanCoach(host);
  return aiCoach(host);
}

// ---- AI coach -----------------------------------------------------------------
function aiCoach(host) {
  const status = state.status;
  if (!status.coach) {
    host.innerHTML = `<div class="card">${emptyState(esc(t('coach.off')), esc(t('coach.offHelp')))}</div>`;
    return;
  }
  const limit = status.coach_limit
    ? `<span class="muted">${esc(t('coach.used', { used: status.coach_used, limit: status.coach_limit }))}</span>` : '';
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('coach.briefing'))} ${info('coaching_ai')}</h2>
        <button type="button" class="ghost small" data-rebrief>${esc(t('coach.refresh'))}</button></div>
      <div class="prose" data-brief>${skeleton('line', 4)}</div>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(t('coach.askTitle'))}</h2>${limit}</div>
      <p class="muted">${esc(t('coach.askHelp'))}</p>
      <div class="chat" data-chat aria-live="polite"></div>
      <div class="suggestions" data-suggestions>${suggestions().map((s) =>
        `<button type="button">${esc(s)}</button>`).join('')}</div>
      <form class="composer" data-chat-form>
        <textarea rows="2" placeholder="${esc(t('coach.placeholder'))}"
          aria-label="${esc(t('coach.question'))}" data-question></textarea>
        <button class="primary" type="submit" data-ask>${esc(t('coach.ask'))}</button>
      </form>
    </div>`;

  const brief = $('[data-brief]', host);
  const lang = getLocale();
  const loadBrief = (refresh) => {
    if (refresh) invalidate(`brief:${lang}`);
    brief.innerHTML = `<p class="muted">${esc(t('coach.reading'))}</p>`;
    const query = new URLSearchParams({ lang });
    if (refresh) query.set('refresh', '1');
    return cached(`brief:${lang}`, () => api(`/api/brief?${query}`))
      .then((body) => { brief.innerHTML = markdown(body.text || ''); })
      .catch((error) => { brief.innerHTML = `<p class="error">${esc(error.message)}</p>`; });
  };
  loadBrief(false);
  const rebrief = $('[data-rebrief]', host);
  rebrief.addEventListener('click', () => busy(rebrief, t('coach.refreshing'), () => loadBrief(true)));

  const chat = $('[data-chat]', host);
  const question = $('[data-question]', host);
  const askButton = $('[data-ask]', host);
  const add = (cls, text) => {
    const node = document.createElement('div');
    node.className = `msg ${cls}`;
    node.innerHTML = cls === 'me' ? esc(text) : markdown(text);
    chat.appendChild(node);
    return node;
  };
  state.chat.forEach((m) => add(m.cls, m.text));
  $('[data-suggestions]', host).classList.toggle('hidden', state.chat.length > 0);

  const ask = async (text) => {
    if (!text.trim() || askButton.disabled) return;
    $('[data-suggestions]', host).classList.add('hidden');
    add('me', text);
    state.chat.push({ cls: 'me', text });
    question.value = '';
    const bubble = add('claude', '');
    bubble.classList.add('pending');
    bubble.scrollIntoView({ block: 'nearest' });
    askButton.disabled = true;
    let answer = '';
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          question: text,
          history: state.history.slice(-8),
          lang: getLocale(),
        }),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || body.error || t('coach.unavailable'));
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop();
        for (const event of events) {
          const line = event.split('\n').find((l) => l.startsWith('data:'));
          if (!line) continue;
          const payload = JSON.parse(line.slice(5).trim());
          if (payload.error) throw new Error(payload.error);
          if (payload.delta) {
            answer += payload.delta;
            bubble.innerHTML = markdown(answer);
            bubble.scrollIntoView({ block: 'nearest' });
          }
        }
      }
      state.history.push({ role: 'user', content: text }, { role: 'assistant', content: answer });
      state.chat.push({ cls: 'claude', text: answer });
      if (state.status.coach_limit) state.status.coach_used += 1;
    } catch (error) {
      bubble.innerHTML = `<span class="error">${esc(error.message)}</span>`;
    } finally {
      bubble.classList.remove('pending');
      askButton.disabled = false;
    }
  };

  $('[data-chat-form]', host).addEventListener('submit', (event) => {
    event.preventDefault();
    ask(question.value);
  });
  question.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      ask(question.value);
    }
  });
  $$('[data-suggestions] button', host).forEach((button) => button.addEventListener('click', () => ask(button.textContent)));
}

// ---- human coach --------------------------------------------------------------
async function humanCoach(host) {
  const mine = await cached('mine', () => api('/api/coaching/mine'));
  const links = mine.links || [];
  const notes = mine.notes || [];
  host.innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-head"><h2>${esc(t('coach.yours'))} ${info('coaching_human')}</h2></div>
        ${links.length ? links.map((l) => `<div class="list-row"><span>${esc(l.coach_email)}</span>
          <span class="badge ${l.status === 'accepted' ? 'ok' : l.status === 'rejected' ? 'bad' : ''}">${esc(linkLabel(l.status))}</span></div>`).join('')
          : `<p class="muted">${esc(t('coach.none'))}</p>`}
      </div>
      <div class="card">
        <div class="card-head"><h2>${esc(t('coach.add'))}</h2></div>
        <form class="connect-form" data-lookup>
          <label class="field"><span>${esc(t('coach.email'))}</span>
            <input type="email" name="email" required placeholder="coach@example.com"></label>
          <button class="primary" type="submit">${esc(t('coach.send'))}</button>
          <p class="muted">${esc(t('coach.addHelp'))}</p>
        </form>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>${esc(t('coach.notes'))}</h2></div>
      ${notes.length ? notes.map((n) => `<div class="coach-note"><div class="meta"><span class="badge">${esc(noteKind(n.kind))}</span>
        ${esc(n.coach_email)} · ${esc(n.created)}</div>${esc(n.text)}</div>`).join('')
        : emptyState(esc(t('coach.notesEmpty')), esc(t('coach.notesHelp')))}
    </div>`;

  const form = $('[data-lookup]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), t('coach.sending'), async () => {
      const found = await api('/api/coaches/lookup?email=' + encodeURIComponent(form.email.value));
      await post('/api/coaching/request', { coach_id: found.id });
      invalidate('mine');
      toast(t('coach.requestSent', { email: found.email }));
      rerender();
    });
  });
}
