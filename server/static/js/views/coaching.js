/* Coaching: the AI coach (briefing and chat) and, for athletes, the human
   coach they're linked to. The conversation survives moving between pages. */
import { api, post } from '../core/api.js';
import { rerender } from '../core/router.js';
import { cached, invalidate, role, state } from '../core/state.js';
import { $, $$, busy, emptyState, esc, loadingPage, markdown, skeleton, subnav, toast } from '../core/ui.js';

const SUGGESTIONS = [
  'How is my training load trending, and is recovery keeping up?',
  'What should I do this week?',
  'Am I sleeping enough for this volume?',
  'Anything in the last month that looks like a warning sign?',
];

export async function render(root, ctx = {}) {
  const tabs = role() === 'athlete' ? [['ai', 'AI coach'], ['coach', 'My coach']] : [['ai', 'AI coach']];
  const wanted = (ctx.path || [])[0];
  const sub = tabs.some(([key]) => key === wanted) ? wanted : 'ai';
  root.innerHTML = `${tabs.length > 1 ? subnav('#/coaching', tabs, sub) : ''}<div data-sub>${loadingPage()}</div>`;
  const host = $('[data-sub]', root);
  if (sub === 'coach') return humanCoach(host);
  return aiCoach(host);
}

// ---- AI coach -----------------------------------------------------------------
function aiCoach(host) {
  const status = state.status;
  if (!status.coach) {
    host.innerHTML = `<div class="card">${emptyState('The AI coach is off on this site',
      'The site owner needs to add an OpenAI or Anthropic API key. Everything else works without it.')}</div>`;
    return;
  }
  const limit = status.coach_limit
    ? `<span class="muted">${status.coach_used} of ${status.coach_limit} questions used today</span>` : '';
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>Briefing</h2>
        <button type="button" class="ghost small" data-rebrief>Refresh</button></div>
      <div class="prose" data-brief>${skeleton('line', 4)}</div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Ask the coach</h2>${limit}</div>
      <p class="muted">It reads your recent load, recovery, plan and races before answering.</p>
      <div class="chat" data-chat aria-live="polite"></div>
      <div class="suggestions" data-suggestions>${SUGGESTIONS.map((s) =>
        `<button type="button">${esc(s)}</button>`).join('')}</div>
      <form class="composer" data-chat-form>
        <textarea rows="2" placeholder="Ask anything about your training… (Ctrl/⌘+Enter to send)"
          aria-label="Your question" data-question></textarea>
        <button class="primary" type="submit" data-ask>Ask</button>
      </form>
    </div>`;

  const brief = $('[data-brief]', host);
  const loadBrief = (refresh) => {
    if (refresh) invalidate('brief');
    brief.innerHTML = '<p class="muted">Reading your last few weeks…</p>';
    return cached('brief', () => api('/api/brief' + (refresh ? '?refresh=1' : '')))
      .then((body) => { brief.innerHTML = markdown(body.text || ''); })
      .catch((error) => { brief.innerHTML = `<p class="error">${esc(error.message)}</p>`; });
  };
  loadBrief(false);
  const rebrief = $('[data-rebrief]', host);
  rebrief.addEventListener('click', () => busy(rebrief, 'Refreshing…', () => loadBrief(true)));

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
        body: JSON.stringify({ question: text, history: state.history.slice(-8) }),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || body.error || 'The coach is unavailable.');
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
        <div class="card-head"><h2>Your coaches</h2></div>
        ${links.length ? links.map((l) => `<div class="list-row"><span>${esc(l.coach_email)}</span>
          <span class="badge ${l.status === 'accepted' ? 'ok' : l.status === 'rejected' ? 'bad' : ''}">${esc(l.status)}</span></div>`).join('')
          : '<p class="muted">No coach yet. A coach sees your data and plan, can leave notes and assign workouts.</p>'}
      </div>
      <div class="card">
        <div class="card-head"><h2>Add a coach</h2></div>
        <form class="connect-form" data-lookup>
          <label class="field"><span>Your coach's email</span>
            <input type="email" name="email" required placeholder="coach@example.com"></label>
          <button class="primary" type="submit">Send request</button>
          <p class="muted">They need a coach account on this site. You can remove access any time.</p>
        </form>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h2>Notes from your coach</h2></div>
      ${notes.length ? notes.map((n) => `<div class="coach-note"><div class="meta"><span class="badge">${esc(n.kind)}</span>
        ${esc(n.coach_email)} · ${esc(n.created)}</div>${esc(n.text)}</div>`).join('')
        : emptyState('Nothing yet', 'Comments and feedback from your coach show up here and on Today.')}
    </div>`;

  const form = $('[data-lookup]', host);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    busy($('button[type="submit"]', form), 'Sending…', async () => {
      const found = await api('/api/coaches/lookup?email=' + encodeURIComponent(form.email.value));
      await post('/api/coaching/request', { coach_id: found.id });
      invalidate('mine');
      toast(`Request sent to ${found.email}`);
      rerender();
    });
  });
}
