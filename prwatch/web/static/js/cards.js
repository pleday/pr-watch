// The card of a pull request: its state, its quick buttons, its menu, its expanded details.
import { ICONS, S, act, askServer, featureHidden, h, icon, loadedAt } from './core.js';
import { formatDateTime } from './i18n.js';
import { confirmAction, mergeDialog, openSession, ownChoices, pushDialog, settingsDialog, ticketDialog } from './dialogs.js';
import { selectTab, showJournal } from './journal.js';

export function questionBox(entry, question) {
  const note = h('input', { type: 'text', placeholder: 'Autre : dites ce qu’il faut faire…', 'aria-label': 'Autre consigne' });
  const answer = (choice) => act('answer-question', { repo: entry.repo, pr: entry.pr, id: question.id, choice, note: note.value });
  const button = (text, choice, extra = '') => h('button', { class: `btn ${extra}`, type: 'button', onclick: () => answer(choice) }, text);
  note.addEventListener('keydown', (event) => { if (event.key === 'Enter') answer('other'); });
  return h('div', { class: 'question', role: 'group', 'aria-label': 'Question d’un relecteur' },
    h('div', { class: 'q-head' }, `❓ ${question.author}`, question.place ? h('span', { class: 'muted' }, ` · ${question.place}`) : null),
    h('div', { class: 'q-text' }, question.text),
    h('div', { class: 'q-answer' }, h('strong', {}, 'À traiter ?'), button('Oui', 'yes', 'primary'), button('Non', 'no'), note, button('Autre', 'other')));
}

// Cards are compact by default; the ones the user expanded stay so across the periodic re-renders.
// They are kept in the session too, so a reload (the dashboard opened again) shows the cards as they were.
export const expandedCards = new Set((() => { try { return JSON.parse(localStorage.getItem('pw-expanded') || '[]'); } catch (error) { return []; } })());

/** Shows only the cards that match the filter box and the "à traiter" box, and says how many that is. */
export function applyFilter() {
  const words = document.getElementById('pr-filter').value.toLowerCase().split(/\s+/).filter(Boolean);
  const actionable = document.getElementById('pr-actionable').checked;
  const cards = [...document.querySelectorAll('#prs .card')];
  let shown = 0;
  for (const card of cards) {
    const match = words.every((word) => card.dataset.search.includes(word)) && (!actionable || card.dataset.actionable);
    card.hidden = !match;
    if (match) shown += 1;
  }
  const filtering = words.length > 0 || actionable;
  document.getElementById('pr-count').textContent = !filtering || !cards.length ? '' : shown ? `${shown} sur ${cards.length}` : 'Aucune PR ne correspond';
}

export function setExpanded(article, open) {
  article.classList.toggle('expanded', open);
  const toggle = article.querySelector('.toggle');
  toggle.setAttribute('aria-expanded', String(open));
  if (open) expandedCards.add(article.dataset.key); else expandedCards.delete(article.dataset.key);
  try { localStorage.setItem('pw-expanded', JSON.stringify([...expandedCards])); } catch (error) { /* the cards just start collapsed next time */ }
}

export function cardActions(entry) {
  const done = entry.state === 'done';
  const target = { repo: entry.repo, pr: entry.pr };
  const paused = entry.state === 'paused';
  const unpushed = Boolean(entry.status.unpushed);  // nothing to push, nothing to force
  return [
    { id: 'open', label: 'Ouvrir la session', icon: 'open', kind: 'primary', show: true, run: () => openSession(entry) },
    { id: 'wake', label: 'Forcer un réveil', icon: 'wake', show: !done, run: () => confirmAction(entry, 'wake').then((ok) => ok && act('wake', target)) },
    { id: 'pause', label: paused ? 'Reprendre' : 'Mettre en pause', icon: paused ? 'play' : 'pause', show: !done, run: () => act('pause', { ...target, paused: !paused }) },
    { id: 'ack', label: 'Marquer comme vu', icon: 'check', show: Boolean(entry.needsAttention || entry.activity), run: () => act('acknowledge', target) },
    { id: 'reset', label: 'Remettre les essais à 0', icon: 'reset', show: !done && ((entry.attempts || 0) > 0 || entry.state === 'gave-up'), run: () => act('reset', target) },
    { id: 'report', label: 'Dernier rapport', icon: 'report', show: Boolean(entry.hasReport), run: () => { if (entry.reportsUnread) askServer('read-reports', target).then(() => S.refresh(true)); showJournal(`${entry.repo}#${entry.pr}`, true); } },
    { id: 'settings', label: 'Réglages…', icon: 'settings', show: !done, run: () => settingsDialog(entry) },
    { id: 'push', label: 'Pousser', icon: 'push', show: !done && unpushed, run: () => pushDialog(entry, false) },
    { id: 'forcePush', label: 'Pousser en force…', icon: 'forcePush', kind: 'danger', show: !done && unpushed, run: () => pushDialog(entry, true) },
    { id: 'ticketOpen', label: `Ouvrir ${entry.ticket || 'le ticket'}`, icon: 'ticket', show: Boolean(entry.ticketUrl), run: () => act('open-link', { url: entry.ticketUrl }) },
    { id: 'ticket', label: 'État du ticket…', icon: 'status', show: Boolean(entry.ticket) && !featureHidden('ticket'), run: () => ticketDialog(entry) },
    { id: 'merge', label: 'Merger la PR…', icon: 'merge', kind: entry.ready ? 'good' : '', show: !done && !entry.mergePointless && !featureHidden('merge'), run: () => mergeDialog(entry) },
    { id: 'unwatch', label: done ? 'Retirer de la liste' : 'Arrêter la surveillance', icon: 'stop', kind: 'danger', show: true, run: () => confirmAction(entry, 'unwatch').then((ok) => ok && act('unwatch', target, true)) },
  ].filter((action) => action.show);
}

/** The "…" button of a collapsed card: a menu of the actions that have no small button of their own. */

export function moreMenu(actions) {
  const wrap = h('span', { class: 'more' });
  const close = () => { wrap.querySelector('.menu')?.remove(); document.removeEventListener('click', outside, true); document.removeEventListener('keydown', escape, true); };
  const outside = (event) => { if (!wrap.contains(event.target)) close(); };
  const escape = (event) => { if (event.key === 'Escape') close(); };
  wrap.append(h('button', { class: 'btn mini', type: 'button', title: 'Toutes les actions', 'aria-label': 'Toutes les actions', 'aria-haspopup': 'menu', onclick: () => {
    if (wrap.querySelector('.menu')) { close(); return; }
    wrap.append(h('div', { class: 'menu', role: 'menu' }, ...actions.map((action) => h('button', { class: `menu-item ${action.kind || ''}`, type: 'button', role: 'menuitem', onclick: () => { close(); action.run(); } }, icon(action.icon), action.label))));
    document.addEventListener('click', outside, true);
    document.addEventListener('keydown', escape, true);
  } }, icon('more')));
  return wrap;
}

export function card(entry, state) {
  const maxAttempts = state.maxAttempts;
  const target = { repo: entry.repo, pr: entry.pr };
  const { kind, label } = entry.status;
  const blockers = entry.state === 'watching' && !entry.ready ? entry.mergeBlockers || [] : [];
  const actions = cardActions(entry);
  const quickIds = entry.quickActions || [];
  const quick = quickIds.map((id) => actions.find((action) => action.id === id)).filter(Boolean);
  const others = actions.filter((action) => !quick.includes(action));
  // unread reports: a pill on the report button, the total in its tooltip
  const unread = (action) => (action.id === 'report' && entry.reportsUnread > 0 ? h('span', { class: 'pill', 'aria-hidden': 'true' }, String(entry.reportsUnread)) : null);
  const reportTip = (action) => (action.id === 'report' ? `${action.label} — ${entry.reportsUnread} non lu${entry.reportsUnread > 1 ? 's' : ''} (${entry.reportsTotal} au total)` : action.label);
  const button = (action) => h('button', { class: `btn ${action.kind || ''} ${unread(action) ? 'has-pill' : ''}`, type: 'button', title: reportTip(action), onclick: action.run }, icon(action.icon), action.label, unread(action));
  const mini = (action) => h('button', { class: `btn mini ${action.kind === 'primary' || action.kind === 'good' ? action.kind : ''} ${unread(action) ? 'has-pill' : ''}`, type: 'button', title: reportTip(action), 'aria-label': reportTip(action), onclick: action.run }, icon(action.icon), unread(action));
  const article = h('article', { class: `card ${entry.needsAttention ? 'attention' : ''} ${entry.ready && !entry.needsAttention ? 'ready' : ''} ${entry.state === 'done' ? 'done' : ''} ${S.focusKey === `${entry.repo}#${entry.pr}` ? 'focus' : ''}` },
    h('div', { class: 'card-head' },
      h('button', { class: 'toggle', type: 'button', 'aria-expanded': 'false', title: 'Étendre / réduire', 'aria-label': 'Étendre ou réduire la carte', onclick: () => setExpanded(article, !article.classList.contains('expanded')) }, icon('chevron')),
      h('span', { class: `state-icon ${kind}`, title: entry.status.tip, 'aria-label': entry.status.tip }, ICONS[kind] ? icon(kind) : entry.status.icon),
      h('a', { class: 'key', href: entry.url, target: '_blank', rel: 'noopener noreferrer' }, `${entry.repo} #${entry.pr}`),
      h('span', { class: 'card-title' }, entry.title || ''),
      h('span', { class: `chip ${kind}`, title: entry.status.tip }, label),
      S.focusKey === `${entry.repo}#${entry.pr}` ? h('span', { class: 'chip notified', title: 'Vous êtes arrivé ici depuis une notification.' }, '🔔 Notification') : null,
      entry.activity ? h('button', { class: 'chip acted', type: 'button', title: `${entry.activity.text} (${formatDateTime(entry.activity.at)}) — cliquer pour tout marquer comme vu`, onclick: () => act('acknowledge', target) }, entry.activity.count > 1 ? `● Action faite ×${entry.activity.count}` : '● Action faite') : null,
      entry.status.unpushed ? h('span', { class: 'chip unpushed', title: `${entry.status.unpushed} : le bouton « Pousser » les envoie sur la PR.` }, '⬆ À pousser') : null,
      h('span', { class: 'mini-actions' }, ...quick.map(mini), others.length ? moreMenu(others) : null)),
    entry.needsAttention ? h('div', { class: 'banner', role: 'alert' }, `⚠ ${entry.needsAttention}`) : null,
    (entry.pendingQuestions || []).map((question) => questionBox(entry, question)),
    h('div', { class: 'card-more' },
      h('div', { class: 'branch muted' }, h('code', {}, entry.branch)),
      entry.removeInMinutes === null || entry.removeInMinutes === undefined ? null : h('div', { class: 'muted' }, `Sera retirée de la liste dans ${entry.removeInMinutes} min (délai compté depuis la fin de la PR).`),
      entry.autoMergeNote && entry.effective?.autoMerge !== 'off' ? h('div', { class: 'muted' }, `Merge automatique en attente : ${entry.autoMergeNote}.`) : null,
      blockers.length ? h('ul', { class: 'blockers' }, h('li', {}, 'Bloque le merge :'), ...blockers.map((b) => h('li', {}, b))) : null,
      h('dl', { class: 'meta' },
        h('div', {}, h('dt', {}, 'Session Claude'), h('dd', {}, entry.sessionTitle || entry.sessionId.slice(0, 8) + '…', h('div', { class: 'muted' }, `${entry.sessionId.slice(0, 8)}… · ${entry.sessionActivity}`))),
        h('div', {}, h('dt', {}, 'Essais automatiques'), h('dd', {}, `${entry.attempts || 0} / ${maxAttempts}`)),
        h('div', {}, h('dt', {}, 'Dernier pipeline traité'), h('dd', {}, entry.lastPipeline ? `#${entry.lastPipeline}` : '—')),
        h('div', {}, h('dt', {}, 'Dernier rapport'), h('dd', {}, entry.lastReportAt ? formatDateTime(entry.lastReportAt) : '—')),
        h('div', {}, h('dt', {}, 'Réglages propres à la PR'), h('dd', {}, ownChoices(entry, state).join(' · ') || 'Aucun : suit les réglages par défaut'))),
      h('div', { class: 'actions' },
        ...actions.flatMap((action) => (action.id === 'push' ? [h('span', { class: 'sep' }), button(action)] : [button(action)])))));
  article.dataset.key = `${entry.repo}#${entry.pr}`;
  // what the filter of the list looks at
  article.dataset.search = [entry.repo, `#${entry.pr}`, entry.title, entry.branch, entry.ticket].filter(Boolean).join(' ').toLowerCase();
  article.dataset.actionable = entry.needsAttention || entry.status.kind === 'ticket-wait' || entry.pendingQuestions?.length || entry.ready || entry.state === 'gave-up' ? '1' : '';
  setExpanded(article, expandedCards.has(article.dataset.key));
  // the mark of a notification stays until the user touches the card: the cards are rebuilt at every change
  article.addEventListener('pointerdown', () => {
    if (S.focusKey !== article.dataset.key) return;
    S.focusKey = null;
    article.classList.remove('focus');
    article.querySelector('.chip.notified')?.remove();
  });
  // a double click anywhere on the card but its buttons and links opens the journal of this pull request
  article.addEventListener('dblclick', (event) => {
    if (!event.target.closest('button, a')) showJournal(`${entry.repo}#${entry.pr}`);
  });
  return article;
}

// `pr-watch ui --select repo#12` (a click on a notification) asks the page, through the state, to show that pull request;
// a request from before this page was opened is old news.
// A click on a pull request in the status icon: show it and make it pulse for a moment (a notification, on the other hand,
// marks its pull request until the user touches it).
function pulse(key) {
  selectTab('prs');
  document.getElementById('pr-filter').value = '';  // the pull request asked for must not stay hidden by the filter
  document.getElementById('pr-actionable').checked = false;
  applyFilter();
  const target = [...document.querySelectorAll('#prs .card')].find((card) => card.dataset.key === key);
  if (!target) return;
  setExpanded(target, true);
  target.scrollIntoView({ block: 'center' });
  target.classList.remove('pulse');
  void target.offsetWidth;  // restarts the animation when the same pull request is asked for twice
  target.classList.add('pulse');
  setTimeout(() => target.classList.remove('pulse'), 2800);
  globalThis.focus();
}

export function applyFocus(focus) {
  if (!focus || focus.at === S.lastFocusAt || focus.at < loadedAt - 30000) return;
  S.lastFocusAt = focus.at;
  if (!focus.key) return;
  if (focus.source === 'tray') { pulse(focus.key); return; }
  selectTab('prs');
  document.getElementById('pr-filter').value = '';  // the pull request asked for must not stay hidden by the filter
  document.getElementById('pr-actionable').checked = false;
  applyFilter();
  const target = [...document.querySelectorAll('#prs .card')].find((card) => card.dataset.key === focus.key);
  if (!target) return;
  setExpanded(target, true);
  target.scrollIntoView({ block: 'center' });
  for (const other of document.querySelectorAll('#prs .card.focus')) {  // the mark of an earlier notification
    if (other === target) continue;
    other.classList.remove('focus');
    other.querySelector('.chip.notified')?.remove();
  }
  S.focusKey = focus.key;
  if (!target.classList.contains('focus')) {
    target.classList.add('focus');
    target.querySelector('.card-title')?.before(h('span', { class: 'chip notified', title: 'Vous êtes arrivé ici depuis une notification.' }, '🔔 Notification'));
  }
  globalThis.focus();  // the window itself is raised by the launcher; this covers a window that was already focused-able
}
