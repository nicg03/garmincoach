/* Coaching: the AI coach (briefing, saved conversations and memory) and, for
   athletes, the human coach they're linked to. Conversations live on the
   server, so they survive reloads and other devices. */
import { api, del, patch, post } from '../core/api.js';
import { info } from '../core/glossary.js';
import { getLocale, t, translatePhrase } from '../core/i18n.js';
import { navigate, rerender } from '../core/router.js';
import { cached, invalidate, role, state } from '../core/state.js';
import {
  $, $$, busy, confirmDialog, daysUntil, emptyState, esc, loadingPage, markdown, promptDialog,
  shortDate, skeleton, subnav, toast, todayIso,
} from '../core/ui.js';

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
  return aiCoach(host, (ctx.path || [])[1]);
}

// ---- AI coach -----------------------------------------------------------------
const PAGE = 20;
const REMEMBER_LINE = /^[ \t>*_`]*REMEMBER:.*$/gm;

function visibleAnswer(text) {
  return (text || '').replace(REMEMBER_LINE, '').trim();
}

function plainText(text) {
  return visibleAnswer(text).replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim();
}

function chatGroup(updated) {
  const age = -daysUntil((updated || todayIso()).slice(0, 10));
  if (age <= 0) return 'today';
  if (age === 1) return 'yesterday';
  if (age < 7) return 'week';
  return 'older';
}

function chatWhen(updated) {
  if (!updated) return '';
  return chatGroup(updated) === 'today' ? updated.slice(11, 16) : shortDate(updated);
}

function aiCoach(host, wantedId) {
  const status = state.status;
  if (!status.coach) {
    host.innerHTML = `<div class="card">${emptyState(esc(t('coach.off')), esc(t('coach.offHelp')))}</div>`;
    return;
  }
  const limit = status.coach_limit
    ? `<span class="muted" data-limit>${esc(t('coach.used', { used: status.coach_used, limit: status.coach_limit }))}</span>` : '';
  host.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>${esc(t('coach.briefing'))} ${info('coaching_ai')}</h2>
        <button type="button" class="ghost small" data-rebrief>${esc(t('coach.refresh'))}</button></div>
      <div class="prose" data-brief>${skeleton('line', 4)}</div>
    </div>
    <div class="card">
      <div class="chat-layout" data-layout>
        <aside class="chat-side" aria-label="${esc(t('coach.history'))}">
          <div class="chat-side-head"><h2>${esc(t('coach.history'))}</h2>
            <button type="button" class="ghost small" data-new>${esc(t('coach.newChat'))}</button></div>
          <div class="chat-list" data-chat-list>${skeleton('line', 3)}</div>
          <button type="button" class="link hidden" data-more>${esc(t('coach.more'))}</button>
          <button type="button" class="link quiet hidden" data-clear>${esc(t('coach.clearAll'))}</button>
        </aside>
        <section class="chat-main">
          <div class="card-head chat-head">
            <button type="button" class="ghost small chat-side-toggle" data-side-toggle
              aria-expanded="false">${esc(t('coach.history'))}</button>
            <h2 data-chat-title>${esc(t('coach.newChatTitle'))}</h2>${limit}
          </div>
          <div class="chat-tools">
            <label class="check" title="${esc(t('coach.recallHelp'))}">
              <input type="checkbox" data-recall> <span>${esc(t('coach.recall'))}</span></label>
            <button type="button" class="link" data-memory>${esc(t('coach.memory'))}</button>
          </div>
          <p class="muted" data-help>${esc(t('coach.askHelp'))}</p>
          <div class="chat" data-chat aria-live="polite"></div>
          <div class="suggestions hidden" data-suggestions>${suggestions().map((s) =>
            `<button type="button">${esc(s)}</button>`).join('')}</div>
          <form class="composer" data-chat-form>
            <textarea rows="2" placeholder="${esc(t('coach.placeholder'))}"
              aria-label="${esc(t('coach.question'))}" data-question></textarea>
            <button class="primary" type="submit" data-ask>${esc(t('coach.ask'))}</button>
          </form>
        </section>
      </div>
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

  const layout = $('[data-layout]', host);
  const chat = $('[data-chat]', host);
  const question = $('[data-question]', host);
  const askButton = $('[data-ask]', host);
  const titleEl = $('[data-chat-title]', host);
  const recall = $('[data-recall]', host);
  const help = $('[data-help]', host);
  const suggestionsEl = $('[data-suggestions]', host);
  const memoryButton = $('[data-memory]', host);
  const listHost = $('[data-chat-list]', host);
  const more = $('[data-more]', host);
  const clear = $('[data-clear]', host);

  // null while the conversation hasn't been sent yet: it only exists once asked.
  let current = null;
  let pendingRecall = false;
  let loaded = [];
  let total = 0;

  const startNew = () => {
    current = null;
    state.chatId = null;
    titleEl.textContent = t('coach.newChatTitle');
    recall.checked = pendingRecall = false;
    suggestionsEl.classList.remove('hidden');
    help.classList.remove('hidden');
  };

  const showMemoryCount = (count) => {
    memoryButton.textContent = count ? t('coach.memoryCount', { n: count }) : t('coach.memory');
  };
  cached('coach-memory', () => api('/api/coach/memory'))
    .then((body) => showMemoryCount(body.memories.length)).catch(() => {});
  const memoryChanged = (count) => {
    invalidate('coach-memory');
    showMemoryCount(count);
  };

  // ---- conversation list
  const groupLabel = {
    today: t('coach.today'), yesterday: t('coach.yesterday'),
    week: t('coach.lastWeek'), older: t('coach.older'),
  };
  const drawList = () => {
    more.classList.toggle('hidden', loaded.length >= total);
    clear.classList.toggle('hidden', !loaded.length);
    if (!loaded.length) {
      listHost.innerHTML = `<p class="muted">${esc(t('coach.historyEmpty'))}</p>`;
      return;
    }
    let group = null;
    listHost.innerHTML = loaded.map((c) => {
      const g = chatGroup(c.updated);
      const heading = g !== group ? `<div class="chat-group">${esc(groupLabel[g])}</div>` : '';
      group = g;
      const active = current && current.id === c.id;
      return `${heading}<div class="chat-row${active ? ' active' : ''}">
        <a href="#/coaching/ai/${c.id}"${active ? ' aria-current="page"' : ''}>
          <span class="chat-row-title">${esc(c.title)}</span>
          <span class="chat-row-when">${esc(chatWhen(c.updated))}</span></a>
        <details class="row-menu"><summary aria-label="${esc(t('coach.chatActions'))}">⋯</summary>
          <div class="row-menu-pop">
            <button type="button" data-rename="${c.id}">${esc(t('coach.rename'))}</button>
            <button type="button" class="danger-text" data-delete="${c.id}">${esc(t('coach.delete'))}</button>
          </div></details>
      </div>`;
    }).join('');
  };
  const loadList = async (fresh = false) => {
    if (fresh) invalidate('chats');
    try {
      const body = await cached('chats', () => api(`/api/chats?limit=${PAGE}`));
      loaded = body.chats.slice();
      total = body.total;
      drawList();
    } catch (error) {
      listHost.innerHTML = `<p class="error">${esc(error.message)}</p>`;
    }
  };
  more.addEventListener('click', () => busy(more, '', async () => {
    const body = await api(`/api/chats?limit=${PAGE}&offset=${loaded.length}`);
    loaded = loaded.concat(body.chats);
    total = body.total;
    drawList();
  }));

  const renameChat = async (id) => {
    const found = loaded.find((c) => c.id === id) || current;
    const title = await promptDialog({
      title: t('coach.renameTitle'), value: (found && found.title) || '',
      confirm: t('coach.save'), maxLength: 60,
    });
    if (!title) return;
    const saved = await patch(`/api/chats/${id}`, { title });
    if (current && current.id === id) {
      current = { ...current, title: saved.title };
      titleEl.textContent = saved.title;
    }
    await loadList(true);
  };
  const deleteChat = async (id) => {
    const found = loaded.find((c) => c.id === id);
    const ok = await confirmDialog({
      title: t('coach.deleteTitle'),
      body: esc(t('coach.deleteBody', { title: (found && found.title) || '' })),
      confirm: t('coach.delete'), danger: true,
    });
    if (!ok) return;
    await del(`/api/chats/${id}`);
    invalidate('chats');
    if (current && current.id === id) {
      state.chatId = null;
      navigate('coaching/ai');
      return;
    }
    await loadList(true);
  };
  listHost.addEventListener('click', (event) => {
    const rename = event.target.closest('[data-rename]');
    const remove = event.target.closest('[data-delete]');
    const target = rename || remove;
    if (!target) return;
    const menu = target.closest('details');
    if (menu) menu.open = false;
    const id = Number(rename ? rename.dataset.rename : remove.dataset.delete);
    (rename ? renameChat(id) : deleteChat(id)).catch((error) => toast(error.message, 'bad'));
  });
  clear.addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: t('coach.clearTitle'), body: esc(t('coach.clearBody')),
      confirm: t('coach.clearAll'), danger: true,
    });
    if (!ok) return;
    const cleared = await busy(clear, '', () => del('/api/chats'));
    if (!cleared) return;
    invalidate('chats');
    state.chatId = null;
    navigate('coaching/ai');
  });
  $('[data-new]', host).addEventListener('click', () => {
    state.chatId = null;
    navigate('coaching/ai');
  });
  const sideToggle = $('[data-side-toggle]', host);
  sideToggle.addEventListener('click', () => {
    const open = layout.classList.toggle('side-open');
    sideToggle.setAttribute('aria-expanded', String(open));
  });

  // ---- messages
  const rememberFrom = async (text) => {
    const value = await promptDialog({
      title: t('coach.rememberTitle'), body: esc(t('coach.rememberHelp')),
      value: plainText(text), confirm: t('coach.rememberSave'), maxLength: 300, multiline: true,
    });
    if (!value) return;
    try {
      await post('/api/coach/memory', { text: value, chat_id: current ? current.id : null });
      const body = await api('/api/coach/memory');
      memoryChanged(body.memories.length);
      toast(t('coach.memorySaved'));
    } catch (error) {
      toast(translatePhrase(error.message), 'bad');
    }
  };
  const addActions = (wrap, text) => {
    if (!text || wrap.querySelector('.msg-action')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'msg-action';
    button.textContent = t('coach.remember');
    button.addEventListener('click', () => rememberFrom(text));
    wrap.appendChild(button);
  };
  const add = (cls, text) => {
    const wrap = document.createElement('div');
    wrap.className = `msg-wrap ${cls}`;
    const bubble = document.createElement('div');
    bubble.className = `msg ${cls}`;
    bubble.innerHTML = cls === 'me' ? esc(text) : markdown(visibleAnswer(text));
    wrap.appendChild(bubble);
    chat.appendChild(wrap);
    addActions(wrap, text);
    return { wrap, bubble };
  };

  const openChat = async (id) => {
    chat.innerHTML = skeleton('line', 3);
    try {
      const body = await api(`/api/chats/${id}`);
      current = body.chat;
      state.chatId = current.id;
      titleEl.textContent = current.title;
      recall.checked = Boolean(current.use_memory);
      chat.innerHTML = '';
      body.messages.forEach((m) => add(m.role === 'user' ? 'me' : 'claude', m.content));
      suggestionsEl.classList.add('hidden');
      help.classList.add('hidden');
      drawList();
    } catch {
      chat.innerHTML = '';
      state.chatId = null;
      navigate('coaching/ai', { replace: true });
    }
  };

  recall.addEventListener('change', async () => {
    if (!current) {
      pendingRecall = recall.checked;
      return;
    }
    try {
      current = { ...current, ...(await patch(`/api/chats/${current.id}`, { use_memory: recall.checked })) };
    } catch (error) {
      recall.checked = !recall.checked;
      toast(error.message, 'bad');
    }
  });

  memoryButton.addEventListener('click', () => memoryPanel(memoryChanged));

  const ask = async (text) => {
    if (!text.trim() || askButton.disabled) return;
    suggestionsEl.classList.add('hidden');
    help.classList.add('hidden');
    add('me', text);
    question.value = '';
    const { wrap, bubble } = add('claude', '');
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
          chat_id: current ? current.id : null,
          use_memory: current ? undefined : pendingRecall,
          lang: getLocale(),
        }),
      });
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || body.error || t('coach.unavailable'));
      }
      if (state.status.coach_limit) {
        state.status.coach_used += 1;
        const counter = $('[data-limit]', host);
        if (counter) counter.textContent = t('coach.used', { used: state.status.coach_used, limit: state.status.coach_limit });
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
          if (payload.chat && !current) {
            current = payload.chat;
            state.chatId = current.id;
            titleEl.textContent = current.title;
            history.replaceState(null, '', `#/coaching/ai/${current.id}`);
          }
          if (payload.delta) {
            answer += payload.delta;
            bubble.innerHTML = markdown(visibleAnswer(answer));
            bubble.scrollIntoView({ block: 'nearest' });
          }
          if (payload.memory) {
            invalidate('coach-memory');
            cached('coach-memory', () => api('/api/coach/memory'))
              .then((body) => showMemoryCount(body.memories.length)).catch(() => {});
            toast(t('coach.memorySavedAuto', { text: payload.memory.text }));
          }
          if (payload.memory_full) toast(t('coach.memoryFull'), 'bad');
          if (payload.done) {
            answer = payload.text != null ? payload.text : visibleAnswer(answer);
            bubble.innerHTML = markdown(answer);
            addActions(wrap, answer);
          }
        }
      }
    } catch (error) {
      bubble.innerHTML = `<span class="error">${esc(error.message)}</span>`;
    } finally {
      bubble.classList.remove('pending');
      askButton.disabled = false;
      loadList(true);
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

  const id = Number(wantedId || state.chatId) || null;
  if (id) {
    if (!wantedId) history.replaceState(null, '', `#/coaching/ai/${id}`);
    openChat(id);
  } else {
    startNew();
  }
  loadList();
}

// ---- coach memory panel ---------------------------------------------------------
function memoryPanel(onChange) {
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<div class="modal memory-panel" role="dialog" aria-modal="true" aria-labelledby="memory-title">
    <div class="card-head"><h2 id="memory-title">${esc(t('coach.memoryTitle'))}</h2>
      <span class="muted" data-count></span></div>
    <p class="muted">${esc(t('coach.memoryIntro'))}</p>
    <div class="memory-list" data-list>${skeleton('line', 2)}</div>
    <form class="composer" data-add>
      <input type="text" maxlength="300" data-text placeholder="${esc(t('coach.memoryPlaceholder'))}"
        aria-label="${esc(t('coach.memoryPlaceholder'))}">
      <button type="submit" class="primary">${esc(t('coach.memoryAdd'))}</button>
    </form>
    <p class="muted memory-recall">${esc(t('coach.memoryRecall'))}</p>
    <div class="row-actions"><button type="button" class="ghost" data-close>${esc(t('coach.close'))}</button></div>
  </div>`;

  const previous = document.activeElement;
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey);
    if (previous && previous.focus) previous.focus();
  };
  // A rename or edit dialog may sit on top; Escape belongs to that one.
  const onKey = (event) => {
    if (event.key === 'Escape' && document.querySelectorAll('.modal-back').length === 1) close();
  };
  document.addEventListener('keydown', onKey);
  back.addEventListener('click', (event) => { if (event.target === back) close(); });
  $('[data-close]', back).addEventListener('click', close);

  const list = $('[data-list]', back);
  const count = $('[data-count]', back);
  const form = $('[data-add]', back);
  const input = $('[data-text]', back);
  let memories = [];
  let limit = 30;

  const draw = () => {
    count.textContent = t('coach.memoryUsed', { n: memories.length, limit });
    form.classList.toggle('hidden', memories.length >= limit);
    list.innerHTML = memories.length ? memories.map((m) => `<div class="memory-row">
      <span class="memory-text">${esc(m.text)}</span>
      <span class="memory-meta">${esc(shortDate(m.created))}</span>
      <button type="button" class="link" data-edit="${m.id}">${esc(t('coach.edit'))}</button>
      <button type="button" class="link danger-text" data-del="${m.id}">${esc(t('coach.delete'))}</button>
    </div>`).join('') : `<p class="muted">${esc(t('coach.memoryEmpty'))}</p>`;
    onChange(memories.length);
  };
  const load = async () => {
    invalidate('coach-memory');
    const body = await cached('coach-memory', () => api('/api/coach/memory'));
    memories = body.memories;
    limit = body.limit || limit;
    draw();
  };

  list.addEventListener('click', async (event) => {
    const edit = event.target.closest('[data-edit]');
    const remove = event.target.closest('[data-del]');
    try {
      if (edit) {
        const found = memories.find((m) => m.id === Number(edit.dataset.edit));
        const text = await promptDialog({
          title: t('coach.memoryEdit'), value: found ? found.text : '',
          confirm: t('coach.save'), maxLength: 300, multiline: true,
        });
        if (!text) return;
        await patch(`/api/coach/memory/${edit.dataset.edit}`, { text });
        await load();
      } else if (remove) {
        await del(`/api/coach/memory/${remove.dataset.del}`);
        await load();
      }
    } catch (error) {
      toast(translatePhrase(error.message), 'bad');
    }
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!input.value.trim()) return;
    busy($('button[type="submit"]', form), '', async () => {
      await post('/api/coach/memory', { text: input.value.trim() });
      input.value = '';
      await load();
    });
  });

  document.body.appendChild(back);
  load().catch((error) => { list.innerHTML = `<p class="error">${esc(error.message)}</p>`; });
  input.focus();
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
