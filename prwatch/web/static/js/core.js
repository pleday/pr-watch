// The basics of the page: the token, the shared state, icons, DOM helper, toasts, calls to the server, dialogs.
import { translate } from './i18n.js';

const params = new URLSearchParams(globalThis.location.search);

export let token = params.get('t') || sessionStorage.getItem('pw-token') || '';
// The token stays in the address of the window: the browser reuses an application window only when asked for the very
// address it shows, so the address must not change (a second launch from the icon would open another window).
if (params.get('t')) sessionStorage.setItem('pw-token', token);

export const loadedAt = Date.now();

// ── icons ───────────────────────────────────────────────────────────────────────────────────────────────────────────
// One set, drawn the same way (24 grid, 2 px round strokes, the colour of the text), so no two actions look alike by accident.
export const ICONS = {
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  check: 'M5 13l4 4L19 7',
  open: 'M7 17L17 7M8 7h9v9',
  wake: 'M13 2L4 14h7l-1 8 9-12h-7z',
  pause: 'M8 5v14M16 5v14',
  play: 'M7 4l13 8-13 8z',
  reset: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
  report: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6',
  settings: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4',
  push: 'M12 19V5M5 12l7-7 7 7',
  forcePush: 'M12 17V4M6 10l6-6 6 6M5 21h14',
  merge: 'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  check: 'M5 12l5 5L20 7',
  stop: 'M6 6l12 12M18 6L6 18',
  power: 'M12 3v9M6.3 7.3a8 8 0 1 0 11.4 0',
  powerOn: 'M12 3v9M6.3 7.3a8 8 0 1 0 11.4 0',
  powerOff: 'M12 3v9M6.3 7.3a8 8 0 1 0 11.4 0',
  chevron: 'M9 6l6 6-6 6',
  ticket: 'M3 3h8l10 10-8 8L3 11zM7.5 7.5h0',
  status: 'M7 7h13M16 3l4 4-4 4M17 17H4M8 13l-4 4 4 4',
  add: 'M12 5v14M5 12h14',
  update: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  more: 'M5 12h0M12 12h0M19 12h0',
  expand: 'M7 7l5 5 5-5M7 13l5 5 5-5',
  collapse: 'M7 11l5-5 5 5M7 17l5-5 5 5',
  attention: 'M12 3l10 18H2zM12 10v5M12 18h0',
  group: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  archive: 'M3 4h18v4H3zM5 8v12h14V8M10 12h4',
  failed: 'M8 3h8l5 5v8l-5 5H8l-5-5V8zM9 9l6 6M15 9l-6 6',
  changes: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 9h8M8 12h8M8 15h5',
  ready: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12l3 3 5-6',
  'gave-up': 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9 9l6 6M15 9l-6 6',
  paused: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM10 9v6M14 9v6',
  done: 'M6 6h12v12H6z',
  watching: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  question: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h0',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', width: '16', height: '16', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'ico' })) svg.setAttribute(key, value);
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name] || ICONS.open);
  svg.append(path);
  return svg;
}

// ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────────────

export function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : (['title', 'aria-label', 'placeholder'].includes(key) ? translate(String(value)) : value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(translate(String(child))));
  }
  return node;
}

export function toast(message, bad = false) {
  const copy = h('button', { class: 'toast-copy', type: 'button', title: 'Copier', 'aria-label': 'Copier' }, icon('copy'));
  const node = h('div', { class: `toast${bad ? ' bad' : ''}`, role: 'status' }, h('span', { class: 'toast-text' }, message), copy);
  const box = document.getElementById('toasts');
  box.append(node);
  // At most three at once: a series of actions must not bury the page under messages.
  while (box.children.length > 3) box.firstElementChild.remove();
  // The message stays while the pointer is on it (to read it, to copy it), then lingers a little before going.
  let timer = null;
  const arm = (ms) => { clearTimeout(timer); timer = setTimeout(() => node.remove(), ms); };
  node.addEventListener('mouseenter', () => clearTimeout(timer));
  node.addEventListener('mouseleave', () => arm(2500));
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(String(message));
      copy.replaceChildren(icon('check'));
      copy.title = translate('Copié');
    } catch {
      copy.disabled = true; // the browser refused the clipboard: nothing to hide, nothing to promise
    }
  });
  arm(bad ? 9000 : 4500);
}

export async function call(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'X-Token': token, 'Content-Type': 'application/json', ...(options.headers || {}) } });
  if (response.status === 403) {
    document.getElementById('prs').replaceChildren(h('div', { class: 'empty' }, 'Session expirée ou jeton invalide : ouvrez le tableau de bord avec « pr-watch ui ».'));
    throw new Error('forbidden');
  }
  return response.json();
}

export function followUp() {
  clearInterval(S.followUpTimer);
  let left = 15;
  S.followUpTimer = setInterval(() => { S.refresh(); if (--left <= 0) clearInterval(S.followUpTimer); }, 1000);
}

// A treatment that lasts shows a loader over the whole page, dialogs included, so nothing else can be started meanwhile.
// It appears after a short delay: a quick action must not make it flash. A modal <dialog> is used because it covers the
// ones already open (a fixed <div> would stay under them) and makes the rest of the page inert.
const WAITING = { 'usage-refresh': 'Mesure de la consommation…', merge: 'Merge en cours…', push: 'Poussée en cours…', fetch: 'Récupération en cours…', rebase: 'Rebase en cours…', 'force-push': 'Poussée en force en cours…', 'ticket-info': 'Lecture du ticket…', 'ticket-transition': 'Mise à jour du ticket…', 'add-pr': 'Ajout de la pull request…', 'pr-lookup': 'Recherche de la pull request…', 'plugins-update': 'Installation du plugin…' };
const QUIET = new Set(['refresh', 'ticket-draft', 'jira-metadata']); // background work the user does not wait for
let working = 0;
let loaderTimer = null;

async function withLoader(action, work) {
  if (QUIET.has(action)) return work();
  const dialog = document.getElementById('busy');
  if (!dialog.dataset.guarded) { dialog.dataset.guarded = '1'; dialog.addEventListener('cancel', (event) => event.preventDefault()); } // Escape must not release the page
  if (++working === 1) {
    loaderTimer = setTimeout(() => {
      dialog.querySelector('.label').textContent = WAITING[action] || 'Traitement en cours…';
      if (!dialog.open) dialog.showModal();
    }, 300);
  }
  try {
    return await work();
  } finally {
    if (--working <= 0) {
      working = 0;
      clearTimeout(loaderTimer);
      if (dialog.open) dialog.close();
    }
  }
}

export async function act(action, payload = {}, confirmed = false) {
  try {
    const result = await withLoader(action, () => call('/api/action', { method: 'POST', body: JSON.stringify({ action, ...payload, ...(confirmed ? { confirmed: true } : {}) }) }));
    toast(result.message || (result.ok ? 'Fait.' : 'Échec.'), !result.ok);
    S.refresh(true);
    if (result.ok) followUp();
    return result;
  } catch (error) {
    if (error.message !== 'forbidden') toast(`Erreur : ${error.message}`, true);
    return { ok: false };
  }
}

// ── dialogs ─────────────────────────────────────────────────────────────────────────────────────────────────────────

export function ask({ title, body, buttons }) {
  const dialog = document.getElementById('dialog');
  return new Promise((resolve) => {
    const collect = {};
    const bodyNode = typeof body === 'function' ? body(collect) : h('p', {}, body);
    dialog.replaceChildren(
      h('h2', {}, title),
      h('div', { class: 'body' }, bodyNode),
      h('div', { class: 'buttons' }, buttons.map((b) => {
        // `keepOpen` buttons run their own `onclick` and leave the dialog open; `ref` hands the node to the body through `collect`
        const node = h('button', { class: `btn ${b.kind || ''}`, type: 'button', disabled: b.disabled, title: b.title, onclick: b.keepOpen ? b.onclick : () => { dialog.close(); resolve({ value: b.value, collect }); } }, b.icon ? icon(b.icon) : null, b.label);
        if (b.ref) collect[b.ref] = node;
        return node;
      })),
    );
    dialog.addEventListener('close', () => resolve({ value: null, collect }), { once: true });
    dialog.showModal();
  });
}

export const confirmDialog = async (title, body, label, kind = 'danger solid') =>
  (await ask({ title, body, buttons: [{ label: 'Annuler', value: false }, { label, value: true, kind }] })).value === true;

export const showText = (title, text) => ask({ title, body: () => h('pre', {}, text), buttons: [{ label: 'Fermer', value: true }] });

// ── pull requests ───────────────────────────────────────────────────────────────────────────────────────────────────

// A link to an application (claude://, vscode://) is opened from here, not by the daemon: the browser hands it to the system
// with the activation token of the click, which is what lets the application come to the front on Wayland.
export function openLink(url) {
  const link = h('a', { href: url, rel: 'noopener' });
  document.body.append(link);
  link.click();
  link.remove();
}

// An action whose answer is data for a dialog, not news for a toast.
export async function askServer(action, payload = {}, confirmed = false) {
  try {
    return await withLoader(action, () => call('/api/action', { method: 'POST', body: JSON.stringify({ action, ...payload, ...(confirmed ? { confirmed: true } : {}) }) }));
  } catch (error) {
    return { ok: false, message: error.message === 'forbidden' ? 'Session expirée.' : `Erreur : ${error.message}` };
  }
}

/** Moves the Jira ticket of the PR to another state, filling "Description des impacts potentiels" first when it is given. */

// Every action of a card, once: the small buttons of a collapsed card, its menu and the buttons of an expanded card all
// come from this list. `show` says whether the action makes sense for the pull request in its current state.
export const featureHidden = (feature) => Boolean(S.permissionState?.hidden?.includes(feature));

// The state the modules share (a `let` cannot be assigned from another module).
export const S = {
  codeVersion: null, // of the program that served this page: a different one means the page itself is out of date
  lastState: null,
  lastStateJson: '',
  lastFocusAt: 0,
  focusKey: null, // the pull request a notification led to: marked until the user touches it
  followUpTimer: null,
  lastLogJson: '',
  logLimit: 200, // entries asked of the server: "older entries" adds a page
  wantReport: false, // the journal was opened on a report: scroll to the last one of the pull request, loading older entries if needed
  permissionState: null,
  permissionPrompted: false,
  permissionRefresh: null, // redraws the open permissions dialog
  pluginLines: [],
  optionsKey: '',
  optionsPanel: (() => { try { return localStorage.getItem('pw-options-panel'); } catch (error) { return null; } })(), // the panel of the options tab last shown
  searchTimer: 0,
  refresh: () => {}, // redraws the page from the server (set by app.js: core cannot import it without a cycle)
};
