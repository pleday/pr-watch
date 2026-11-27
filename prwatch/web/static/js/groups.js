// The list of pull requests, arranged: by hand (groups the user names, in the order they want, cards dragged into place, all kept
// by the server) or by ticket or repository (computed, read-only). The cards themselves come from cards.js.
import { S, act, ask, confirmDialog, h, icon, toast } from './core.js';
import { t } from './i18n.js';

const MODE_KEY = 'pw-group-by';
const COLLAPSED_KEY = 'pw-groups-collapsed';
export const MODES = ['none', 'manual', 'ticket', 'repo'];  // no grouping, the user's groups, by ticket, by repository

const read = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; } };
const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* the choice lasts until the page closes */ } };

export const groupMode = () => (MODES.includes(read(MODE_KEY, 'manual')) ? read(MODE_KEY, 'manual') : 'manual');
export const setGroupMode = (mode) => write(MODE_KEY, mode);
const collapsed = () => new Set(read(COLLAPSED_KEY, []));

const keyOf = (entry) => `${entry.repo}#${entry.pr}`;
const cardsOf = (body) => [...body.children].filter((node) => node.classList.contains('card'));

// What a card says about its pull request, from the worst to the best: the level (colour) and the icon of the group follow the worst.
const RED = ['failed', 'attention', 'gave-up', 'question'];
const ORDER = [...RED, 'changes', 'ready', 'ticket-wait', 'watching', 'paused', 'done'];

/** 'red' when a PR failed or asks for the user, else 'orange' when changes are requested, else 'green' when one is ready to merge;
 * null (neutral) when none of them is in one of these states, and for an empty group. */
export function levelOf(entries) {
  const kinds = entries.map((e) => e.status.kind);
  if (kinds.some((k) => RED.includes(k))) return 'red';
  if (kinds.includes('changes')) return 'orange';
  return kinds.includes('ready') ? 'green' : null;
}
const LEVEL_ICON = { red: 'failed', orange: 'changes', green: 'ready' };

/** The sections to draw: [{name, label, entries, ...}] — name '' is the PRs of no group (manual mode). */
export function sectionsOf(entries, mode, names = []) {
  if (mode === 'none') return [{ name: '', label: '', entries, flat: true }];
  if (mode === 'manual') {
    const listed = [...new Set([...names, ...entries.map((e) => e.group).filter(Boolean)])];
    const loose = entries.filter((entry) => !entry.group);
    if (!listed.length) return [{ name: '', label: '', entries: loose, flat: true }];
    return [...(loose.length ? [{ name: '', label: t('groups.none'), entries: loose, loose: true }] : []), ...listed.map((name) => ({ name, label: name, entries: entries.filter((e) => e.group === name), movable: true }))];
  }
  const by = new Map();
  for (const entry of entries) {
    const name = mode === 'ticket' ? (entry.ticket || t('groups.noTicket')) : entry.repo;
    by.set(name, [...(by.get(name) || []), entry]);
  }
  return [...by].map(([name, list]) => ({ name, label: name, entries: list, readOnly: true }));
}

/** What the order and the groups are on screen, as the server stores them. */
export function currentLayout() {
  const order = []; const groups = {}; const names = [];
  if (groupMode() === 'none') {  // the list is flat whatever the groups are: only its order is the user's to change
    for (const card of document.querySelectorAll('#prs .card')) order.push(card.dataset.key);
    return { order, groups };
  }
  for (const section of document.querySelectorAll('#prs .group')) {
    if (section.dataset.group) names.push(section.dataset.group);
    for (const card of cardsOf(section.querySelector('.group-body'))) { order.push(card.dataset.key); groups[card.dataset.key] = section.dataset.group; }
  }
  return { order, groups, names };
}

const save = (layout = currentLayout()) => act('layout', layout);

/** The essentials of a group at a glance: its worst state as an icon, its name, how many PRs, and how many are in each state. */
function header(section, mode, open, onToggle) {
  const level = section.loose ? null : levelOf(section.entries);  // the PRs of no group are not a group: no colour of their own
  const counts = new Map();
  for (const entry of section.entries) counts.set(entry.status.kind, (counts.get(entry.status.kind) || 0) + 1);
  const chips = ORDER.filter((kind) => counts.has(kind)).map((kind) => {
    const text = t(`groups.kind.${kind}`, { count: counts.get(kind) });
    return h('span', { class: `gchip ${kind}`, title: text, 'aria-label': text }, h('span', { class: 'gcount' }, String(counts.get(kind))), ` ${t(`groups.short.${kind}`)}`);
  });
  const tools = section.movable
    ? [h('button', { class: 'btn mini wide', type: 'button', title: t('groups.rename'), 'aria-label': t('groups.rename'), onclick: () => renameDialog(section.name) }, '✎ ', t('groups.renameShort')),
      h('button', { class: 'btn mini wide danger', type: 'button', title: t('groups.dissolve'), 'aria-label': t('groups.dissolve'), onclick: () => dissolve(section) }, '✕ ', t('groups.dissolveShort'))]
    : [];
  const grip = section.movable ? groupGrip(section.name) : null;
  const levelText = !section.entries.length ? t('groups.empty') : level ? t(`groups.level.${level}`) : t('groups.level.neutral');
  return h('div', { class: `group-head ${level || 'neutral'}` },
    grip,
    h('button', { class: 'toggle', type: 'button', 'aria-expanded': String(open), 'aria-label': t('groups.toggle'), title: t('groups.toggle'), onclick: onToggle }, icon('chevron')),
    h('span', { class: `group-state ${level || 'neutral'}`, role: 'img', title: levelText, 'aria-label': levelText }, icon(LEVEL_ICON[level] || 'group')),
    h('strong', { class: 'group-name' }, section.label),
    h('span', { class: 'group-count' }, t('groups.count', { count: section.entries.length })),
    ...chips, h('span', { class: 'spacer' }), ...tools);
}

export function renderList(container, entries, state, makeCard) {
  const mode = groupMode();
  const closed = collapsed();
  const sections = sectionsOf(entries, mode, state.groups || []);
  container.classList.toggle('arranged', mode === 'manual' || mode === 'none');
  container.replaceChildren(...sections.map((section) => {
    const id = `${mode}:${section.name}`;
    const body = h('div', { class: 'group-body' }, ...section.entries.map((entry) => makeCard(entry, state, mode === 'manual' || mode === 'none')),
      section.entries.length ? null : h('div', { class: 'group-empty muted' }, t('groups.emptyHint')));
    const node = h('section', { class: `group ${section.flat ? 'flat' : ''} level-${(section.loose ? null : levelOf(section.entries)) || 'neutral'} ${section.name || section.flat ? '' : 'loose'}`, 'data-group': section.name });
    const open = !closed.has(id);
    node.classList.toggle('collapsed', !open);
    if (section.flat) { node.append(body); return node; }
    const head = header(section, mode, open, () => {
      const next = collapsed(); const nowOpen = next.has(id);
      if (nowOpen) next.delete(id); else next.add(id);
      write(COLLAPSED_KEY, [...next]);
      node.classList.toggle('collapsed', !nowOpen);
      head.querySelector('.toggle').setAttribute('aria-expanded', String(nowOpen));
    });
    node.append(head, body);
    return node;
  }));
  if (S.keepHandle) {
    const sel = S.keepHandle.startsWith('group:') ? `.group[data-group="${CSS.escape(S.keepHandle.slice(6))}"] .group-grip` : `.card[data-key="${CSS.escape(S.keepHandle)}"] .drag-handle`;
    container.querySelector(sel)?.focus();
    S.keepHandle = null;
  }
}

/** Moves a node one place up or down among its siblings of the same kind; false when it is already at the end. */
function step(node, up, same) {
  const sibling = up ? node.previousElementSibling : node.nextElementSibling;
  if (!sibling || !same(sibling)) return false;
  if (up) node.parentElement.insertBefore(node, sibling); else sibling.after(node);
  return true;
}

/** The grip of a card: drag it, or focus it and press the up and down arrows. Only in the manual arrangement. */
export function dragHandle(entry) {
  const key = keyOf(entry);
  const handle = h('button', { class: 'drag-handle', type: 'button', draggable: 'true', title: t('groups.drag'), 'aria-label': t('groups.drag') }, '⠿');
  handle.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (step(handle.closest('.card'), event.key === 'ArrowUp', (n) => n.classList.contains('card'))) { S.keepHandle = key; save(); }
  });
  return handle;
}

/** The grip of a group: same gestures, to put the groups in the order the user wants. */
function groupGrip(name) {
  const grip = h('button', { class: 'group-grip', type: 'button', draggable: 'true', title: t('groups.dragGroup'), 'aria-label': t('groups.dragGroup') }, '⠿');
  grip.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    if (step(grip.closest('.group'), event.key === 'ArrowUp', (n) => n.classList.contains('group') && Boolean(n.dataset.group))) { S.keepHandle = `group:${name}`; save(); }
  });
  return grip;
}

export function enableDrag(container) {
  let dragged = null;
  const clear = () => container.querySelectorAll('.drop-before, .drop-after, .drop-into').forEach((n) => n.classList.remove('drop-before', 'drop-after', 'drop-into'));
  const isGroup = () => dragged?.classList.contains('group');
  container.addEventListener('dragstart', (event) => {
    const grip = event.target.closest?.('.drag-handle, .group-grip');
    if (!grip) return;
    dragged = grip.closest(grip.classList.contains('group-grip') ? '.group' : '.card');
    S.dragging = true;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', dragged.dataset.key || dragged.dataset.group);
    event.dataTransfer.setDragImage(isGroup() ? dragged.querySelector('.group-head') : dragged, 12, 12);
    requestAnimationFrame(() => dragged?.classList.add('dragging'));
  });
  container.addEventListener('dragover', (event) => {
    if (!dragged) return;
    const over = event.target.closest(isGroup() ? '.group' : '.card, .group');
    if (!over || over === dragged || (isGroup() && !over.dataset.group)) return;
    event.preventDefault();
    clear();
    const rect = (isGroup() ? over.querySelector('.group-head') : over).getBoundingClientRect();  // a group is told by its head, whatever its height
    const upper = event.clientY < rect.top + rect.height / 2;
    if (isGroup() || over.classList.contains('card')) over.classList.add(upper ? 'drop-before' : 'drop-after');
    else over.classList.add('drop-into');
  });
  container.addEventListener('drop', (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest(isGroup() ? '.group' : '.card, .group');
    if (!target || target === dragged) return;
    const rect = (isGroup() ? target.querySelector('.group-head') : target).getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2;
    if (target.classList.contains('card') || isGroup()) { if (before) target.before(dragged); else target.after(dragged); }
    else target.querySelector('.group-body').append(dragged);
    clear();
    S.keepHandle = isGroup() ? `group:${dragged.dataset.group}` : dragged.dataset.key;
    save();
  });
  container.addEventListener('dragend', () => { dragged?.classList.remove('dragging'); dragged = null; S.dragging = false; clear(); });
}

const sameName = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** Asks for the name of a group (a new one, or the new name of an existing one); null when cancelled or empty. */
async function askName(title, name = '') {
  const { value, collect } = await ask({
    title,
    body: (data) => {
      data.name = h('input', { type: 'text', maxlength: '60', value: name, placeholder: t('groups.namePlaceholder'), 'aria-label': t('groups.namePlaceholder') });
      data.name.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); data.ok?.click(); } });
      setTimeout(() => data.name.focus(), 0);
      return h('div', { class: 'row' }, data.name);
    },
    buttons: [{ label: 'Annuler', value: false }, { label: t('groups.apply'), value: true, kind: 'primary', ref: 'ok' }],
  });
  const chosen = collect.name?.value.trim().replace(/\s+/g, ' ');
  return value && chosen ? chosen : null;
}

const currentNames = () => currentLayout().names;

/** "Nouveau groupe…" of the bar: a group with a name of the user's choice, empty until PRs are put in it. */
export async function newGroupDialog() {
  const name = await askName(t('groups.new'));
  if (!name) return;
  if (currentNames().some((n) => sameName(n, name))) { toast(t('groups.exists', { name }), true); return; }
  const layout = currentLayout();
  layout.names = [...layout.names, name];
  await save(layout);
}

/** "Grouper…" of a card: pick a group, make a new one, or leave the group. */
export async function groupDialog(entry) {
  const names = currentNames();
  const NEW = '\u0000new';
  const { value, collect } = await ask({
    title: t('groups.title', { pr: keyOf(entry) }),
    body: (data) => {
      data.select = h('select', { 'aria-label': t('groups.choose') }, h('option', { value: '' }, t('groups.none')), ...names.map((n) => h('option', { value: n }, n)), h('option', { value: NEW }, t('groups.new')));
      data.select.value = entry.group || '';
      data.name = h('input', { type: 'text', maxlength: '60', placeholder: t('groups.namePlaceholder'), 'aria-label': t('groups.namePlaceholder') });
      data.name.hidden = true;
      data.select.addEventListener('change', () => { data.name.hidden = data.select.value !== NEW; if (!data.name.hidden) data.name.focus(); });
      return h('div', { class: 'row' }, data.select, data.name);
    },
    buttons: [{ label: 'Annuler', value: false }, { label: t('groups.apply'), value: true, kind: 'primary' }],
  });
  if (!value) return;
  const creating = collect.select.value === NEW;
  const chosen = creating ? collect.name.value.trim().replace(/\s+/g, ' ') : collect.select.value;
  if (creating && !chosen) return;
  const layout = currentLayout();
  const existing = layout.names.find((n) => sameName(n, chosen));
  layout.groups = { [keyOf(entry)]: existing || chosen };
  if (chosen && !existing) layout.names = [...layout.names, chosen];
  await save(layout);
}

async function renameDialog(name) {
  const next = await askName(t('groups.rename'), name);
  if (!next || next === name) return;
  if (currentNames().some((n) => n !== name && sameName(n, next))) { toast(t('groups.exists', { name: next }), true); return; }
  const layout = currentLayout();
  layout.groups = Object.fromEntries(Object.entries(layout.groups).filter(([, g]) => g === name).map(([key]) => [key, next]));
  layout.names = layout.names.map((n) => (n === name ? next : n));
  await save(layout);
}

async function dissolve(section) {
  if (!await confirmDialog(t('groups.dissolve'), t('groups.dissolveBody', { name: section.name }), t('groups.dissolveShort'), 'danger solid')) return;
  const layout = currentLayout();
  layout.groups = Object.fromEntries(section.entries.map((e) => [keyOf(e), '']));
  layout.names = layout.names.filter((n) => n !== section.name);
  await save(layout);
}
