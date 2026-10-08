/* Beta feedback: a small modal reached from the user menu and the sidebar
   foot. The page, browser and language travel with the message. */
import { post } from '../core/api.js';
import { getLocale, t, translatePhrase } from '../core/i18n.js';
import { $, esc, toast } from '../core/ui.js';

const MAX_CHARS = 2000;

export function openFeedback() {
  const back = document.createElement('div');
  back.className = 'modal-back';
  back.innerHTML = `<form class="modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
    <h2 id="feedback-title">${esc(t('feedback.title'))}</h2>
    <p class="muted">${esc(t('feedback.intro'))}</p>
    <textarea data-feedback-text rows="5" maxlength="${MAX_CHARS}"
      placeholder="${esc(t('feedback.placeholder'))}"></textarea>
    <p class="error" data-feedback-error role="alert"></p>
    <div class="row-actions">
      <button type="button" class="ghost" data-cancel>${esc(t('ui.cancel'))}</button>
      <button type="submit" class="primary inline" data-send disabled>${esc(t('feedback.send'))}</button>
    </div>
  </form>`;

  const previous = document.activeElement;
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey);
    if (previous && previous.focus) previous.focus();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  back.addEventListener('click', (event) => { if (event.target === back) close(); });
  $('[data-cancel]', back).addEventListener('click', close);

  const text = $('[data-feedback-text]', back);
  const send = $('[data-send]', back);
  const error = $('[data-feedback-error]', back);
  text.addEventListener('input', () => { send.disabled = !text.value.trim(); });

  $('form', back).addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!text.value.trim()) return;
    send.disabled = true;
    error.textContent = '';
    try {
      await post('/api/feedback', {
        text: text.value.trim(),
        page: location.hash || '#/',
        user_agent: navigator.userAgent,
        lang: getLocale(),
      });
      close();
      toast(t('feedback.thanks'));
    } catch (failure) {
      error.textContent = translatePhrase(failure.message);
      send.disabled = false;
    }
  });

  document.body.appendChild(back);
  text.focus();
}
