// The dialogs of the actions on a pull request: open a session, ticket, add, merge, push, settings.
import { S, act, ask, askServer, confirmDialog, featureHidden, h, icon, openLink, showText, toast } from './core.js';
import { formatDateTime, locale } from './i18n.js';

export async function openSession(entry) {
  const result = await act('open-session', { repo: entry.repo, pr: entry.pr });
  if (result.ok && result.openUrl) openLink(result.openUrl);
  if (result.ok || !result.needsTerminal) return;
  // No application hosts the session: ask where to resume it before opening anything.
  document.getElementById('dialog').returnValue = ''; // close() keeps the last one, which Escape must not reuse
  const badges = { desktop: 'CD', jetbrains: 'IJ', vscode: 'VS', terminal: '>_' }; // keyed by the application: the id of a plugin starts with it
  const apps = result.apps || [];
  const { value } = await ask({
    title: 'Ouvrir la session dans…',
    body: () => h('div', { class: 'row' }, h('p', { class: 'muted' }, result.message, result.originLabel ? ` Elle avait été ouverte depuis ${result.originLabel}.` : ''),
      apps.length ? h('div', { class: 'apps' }, ...apps.map((app) => h('button', { class: `app${app.id === result.recommended ? ' recommended' : ''}`, type: 'button', autofocus: true, 'data-app': app.id, onclick: () => document.getElementById('dialog').close(app.id) },
        h('span', { class: `badge ${app.id.split('-')[0]}` }, badges[app.id.split('-')[0]] || '?'), h('span', {}, app.label, app.id === result.recommended ? h('span', { class: 'tag' }, 'Recommandé') : null))))
        : h('p', { class: 'banner' }, 'Le démon tourne avec une ancienne version : redémarrez-le (pr-watch stop, puis pr-watch ensure) pour choisir l’application.'),
      ...(result.appHints || []).map((hint) => h('p', { class: 'muted hint' }, `ℹ ${hint}`))),
    buttons: [{ label: 'Annuler', value: false }],
  });
  const dialog = document.getElementById('dialog');
  const chosen = dialog.returnValue;
  if (chosen && value === null) {
    const opened = await act('open-in', { repo: entry.repo, pr: entry.pr, app: chosen });
    if (opened.ok && opened.openUrl) openLink(opened.openUrl);
  }
}

export async function ticketDialog(entry) {
  const info = await askServer('ticket-info', { repo: entry.repo, pr: entry.pr });
  if (!info.ok) {
    // Jira did not answer (no token, no network, ...): say why, and still offer the ticket itself
    const { value: open } = await ask({ title: `Ticket ${entry.ticket} : état illisible`, body: info.message,
      buttons: [{ label: 'Fermer', value: false }, ...(entry.ticketUrl ? [{ label: 'Ouvrir le ticket dans Jira', value: true, kind: 'primary' }] : [])] });
    if (open) act('open-link', { url: entry.ticketUrl });
    return;
  }
  const ticket = info.ticket;
  const { value, collect } = await ask({
    title: `Ticket ${ticket.key} : ${ticket.status}`,
    body: (data) => {
      data.transition = h('select', { name: 'transition' }, h('option', { value: '' }, '— choisir un état —'), ...ticket.transitions.map((t) => h('option', { value: t.id }, t.to && t.to !== t.name ? `${t.to} (${t.name})` : t.name)));
      data.transition.value = info.preselected || '';
      data.impacts = h('textarea', { name: 'impacts', rows: 6, placeholder: 'Description des impacts potentiels' });
      data.impacts.value = ticket.impacts || info.suggestion;
      data.hint = h('div', { class: 'hint' }, ticket.impacts ? 'Déjà renseignée dans le ticket.' : 'Proposition à corriger : le ticket l’exige avant de changer d’état.');
      if (ticket.impactsField && !ticket.impacts) {
        const template = data.impacts.value;
        if (info.claudeSuspended) {
          data.hint.textContent = 'Claude est suspendu : la traduction automatique est désactivée.';
        } else {
          let hint = 'Proposition à corriger : le ticket l’exige avant de changer d’état.';
          data.hint.replaceChildren(h('span', { class: 'spinner small', 'aria-hidden': 'true' }), 'Traduction en cours avec Claude…');
          data.impacts.classList.add('drafting');
          data.impacts.setAttribute('aria-busy', 'true');
          askServer('ticket-draft', { repo: entry.repo, pr: entry.pr }).then((draft) => {
            if (draft.ok && draft.suggestion && data.impacts.value === template) data.impacts.value = draft.suggestion;
            if (draft.suspended) hint = draft.message;
          }).finally(() => {
            data.impacts.classList.remove('drafting');
            data.impacts.removeAttribute('aria-busy');
            data.hint.textContent = hint;
          });
        }
      }
      return h('div', { class: 'row' },
        h('div', {}, ticket.summary),
        h('label', { class: 'option' }, h('span', {}, 'Nouvel état', h('div', { class: 'hint' }, 'Les états possibles sont lus dans le ticket.')), data.transition),
        ticket.impactsField ? h('label', { class: 'column' },
          h('span', {}, '📝 Description des impacts potentiels', data.hint), data.impacts) : null);
    },
    buttons: [{ label: 'Annuler', value: false }, { label: 'Changer l’état', value: true, kind: 'primary' }],
  });
  if (!value) return;
  if (!collect.transition.value) { toast('Choisissez le nouvel état du ticket.', true); return; }
  const impacts = collect.impacts.value.trim();
  act('ticket-transition', { repo: entry.repo, pr: entry.pr, transition: collect.transition.value, ...(impacts ? { impacts } : {}) }, true);
}

/** Adds a pull request by hand: its address, the local clone, and the Claude session to wake for it. */

export async function addPrDialog() {
  const dialog = document.getElementById('dialog');
  const reference = h('input', { type: 'text', name: 'reference', placeholder: 'https://bitbucket.org/…/pull-requests/12 ou dépôt#12', 'aria-label': 'Adresse de la pull request' });
  const details = h('div', { class: 'row' });
  const state = { found: null };
  const lookup = async () => {
    details.replaceChildren(h('p', { class: 'muted' }, 'Recherche…'));
    const result = await askServer('pr-lookup', { reference: reference.value });
    if (!result.ok) { state.found = null; details.replaceChildren(h('p', { class: 'banner' }, result.message)); return; }
    state.found = result;
    const workDir = h('input', { type: 'text', name: 'workDir', value: result.workDir, 'aria-label': 'Dossier du clone local' });
    const sessionBox = h('div', { class: 'sessions' });
    // "New session" comes first; the one named after the ticket is preselected when there is one, else the new session.
    const draw = (sessions) => {
      const named = result.ticket ? sessions.find((s) => s.title.includes(result.ticket)) : null;
      const choices = [{ id: 'new', title: 'Nouvelle session', note: 'Claude démarre une session pour cette PR' }, ...sessions.map((s) => ({ id: s.id, title: s.title, note: new Intl.DateTimeFormat(locale(), { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(s.modified * 1000)) }))];
      sessionBox.replaceChildren(...choices.map((s) => h('label', { class: 'session' },
        h('input', { type: 'radio', name: 'session', value: s.id, checked: s.id === (named ? named.id : 'new') }),
        h('span', {}, s.id === 'new' ? h('strong', {}, s.title) : s.title, h('span', { class: 'muted' }, ` · ${s.note}`)))));
    };
    draw(result.sessions);
    const reload = async () => draw((await askServer('claude-sessions', { workDir: workDir.value })).sessions || []);
    workDir.addEventListener('change', reload);
    // Several clones of the same repository: the first is filled in, the others are one click away.
    const others = h('div', { class: 'muted' });
    if (result.clones.length > 1) {
      others.append('Autres clones trouvés : ', ...result.clones.slice(1).flatMap((path) => [
        h('button', { class: 'btn', type: 'button', title: path, onclick: () => { workDir.value = path; reload(); } }, path.split('/').pop() || path), ' ']));
    }
    const add = h('button', { class: 'btn primary', type: 'button', disabled: result.state !== 'OPEN', onclick: async () => {
      const chosen = sessionBox.querySelector('input[name="session"]:checked');
      if (!chosen) { toast('Choisissez la session Claude à réveiller pour cette PR.', true); return; }
      if (chosen.value === 'new') toast('Création de la session Claude…');
      add.disabled = true;
      const done = await act('add-pr', { repo: result.repo, pr: result.pr, workDir: workDir.value, sessionId: chosen.value });
      add.disabled = false;
      if (done.ok) dialog.close();
    } }, icon('add'), 'Ajouter à la surveillance');
    details.replaceChildren(
      h('p', {}, h('strong', {}, `${result.repo} #${result.pr}`), ` ${result.title}`, h('div', { class: 'muted' }, `Branche ${result.branch}${result.state !== 'OPEN' ? ` · ${result.state.toLowerCase()} : rien à surveiller` : ''}`)),
      h('label', { class: 'column' }, h('span', {}, 'Dossier du clone local'), workDir, others),
      h('div', { class: 'column' }, h('span', {}, 'Session Claude à réveiller pour cette PR'), sessionBox), add);
  };
  reference.addEventListener('keydown', (event) => { if (event.key === 'Enter') lookup(); });
  await ask({
    title: 'Ajouter une pull request',
    body: () => h('div', { class: 'row' }, h('div', { class: 'inline' }, reference, h('button', { class: 'btn', type: 'button', onclick: lookup }, 'Rechercher')), details),
    buttons: [{ label: 'Fermer', value: false }],
  });
}

export async function mergeDialog(entry) {
  const form = entry.mergeForm;
  const { value, collect } = await ask({
    title: form.title,
    body: (data) => {
      data.strategy = h('select', {}, ...form.strategies.map((s) => h('option', { value: s.id }, s.label)));
      data.strategy.value = form.defaultStrategy; // what Bitbucket has set for the destination branch
      data.close = h('input', { type: 'checkbox', checked: true });
      return h('div', { class: 'row' }, h('div', {}, form.heading), h('div', { class: form.ready ? '' : 'muted' }, form.intro), data.strategy,
        h('div', { class: 'muted' }, form.strategyNote,
          form.strategyHint ? h('span', { class: 'info', tabindex: '0', role: 'img', title: form.strategyHint, 'aria-label': form.strategyHint }, 'ℹ') : null),
        h('label', { class: 'check' }, data.close, form.closeLabel), h('div', { class: 'muted' }, form.warning));
    },
    buttons: [{ label: 'Annuler', value: false }, { label: 'Merger', value: true, kind: form.ready ? 'good' : 'danger solid' }],
  });
  if (!value) return;
  const result = await act('merge', { repo: entry.repo, pr: entry.pr, strategy: collect.strategy.value, closeSourceBranch: collect.close.checked }, true);
  // The ticket moves on with the merge: offer its new state and the impacts field right away, as the workflow asks for it.
  // ... unless other pull requests are still open on this ticket: it moves after the last one.
  if (result.ok && result.otherPrs?.length) toast(`Le ticket ${entry.ticket} a encore ${result.otherPrs.length} PR en cours (${result.otherPrs.join(', ')}) : son état se changera après la dernière.`);
  else if (result.ok && entry.ticket && !featureHidden('ticket')) await ticketDialog(entry);
}

/** Asks the question the server worded for this action (see `confirmations` in actions.py). */

export async function confirmAction(entry, name) {
  const question = entry.confirmations[name];
  return confirmDialog(question.title, question.body, question.label, question.destructive ? 'danger solid' : 'primary');
}

export async function pushDialog(entry, force) {
  if (!(await confirmAction(entry, force ? 'force-push' : 'push'))) return;
  toast('Push en cours…');
  let result = await act(force ? 'force-push' : 'push', { repo: entry.repo, pr: entry.pr }, true);
  // The branch moved on origin: pushing would fail. Ask before fetching it, then push again.
  if (result.needsFetch) {
    if (!(await confirmAction(entry, 'fetch'))) return;
    const fetched = await act('fetch', { repo: entry.repo, pr: entry.pr }, true);
    if (!fetched.ok) { if (fetched.output) showText('Résultat de la récupération', fetched.output); return; }
    result = await act('push', { repo: entry.repo, pr: entry.pr }, true);
  }
  // Fetched, but the local branch lacks what origin has: offer the rebase, then push again.
  if (result.needsRebase) {
    if (!(await confirmAction(entry, 'rebase'))) return;
    const rebased = await act('rebase', { repo: entry.repo, pr: entry.pr }, true);
    if (!rebased.ok) { if (rebased.output) showText('Résultat du rebase', rebased.output); return; }
    result = await act('push', { repo: entry.repo, pr: entry.pr }, true);
  }
  if (result.output) showText('Résultat du push', result.output);
}

export function modeLabel(category, mode) {
  return (category.modes.find((m) => m.id === mode) || { label: mode }).label;
}

/** What this PR does differently from the global choices, as short phrases (empty when it follows them). */

export function ownChoices(entry, state) {
  const own = entry.settings || {};
  const parts = [];
  if (own.autoPush === true || own.autoPush === false) parts.push(`poussée automatique : ${own.autoPush ? 'oui' : 'non'}`);
  const merge = (state.options.autoMergeChoices || []).find((c) => c.id === own.autoMerge);
  if (merge) parts.push(`merge automatique : ${merge.label.toLowerCase()}`);
  for (const category of state.categories) {
    const mode = (own.handling || {})[category.id];
    if (mode) parts.push(`${category.label.toLowerCase()} : ${modeLabel(category, mode).toLowerCase()}`);
  }
  return parts;
}

export async function settingsDialog(entry) {
  const form = entry.settingsForm;
  const select = (spec) => {
    const element = h('select', {}, ...spec.choices.map((c) => h('option', { value: c.id }, c.label)));
    element.value = spec.value;
    return element;
  };
  const { value, collect } = await ask({
    title: form.title,
    body: (data) => {
      data.push = select(form.autoPush);
      data.merge = select(form.autoMerge);
      data.categories = Object.fromEntries(form.categories.map((spec) => [spec.id, select(spec)]));
      const rows = form.categories.map((spec) => h('label', { class: 'option' }, h('span', {}, spec.label), data.categories[spec.id]));
      return h('div', { class: 'row' }, h('p', { class: 'muted' }, form.intro), h('label', { class: 'option' }, h('span', {}, form.autoPush.label), data.push), h('label', { class: 'option' }, h('span', {}, form.autoMerge.label), data.merge), ...rows);
    },
    buttons: [{ label: 'Annuler', value: false }, { label: 'Enregistrer', value: true, kind: 'primary' }],
  });
  if (!value) return;
  const handling = Object.fromEntries(Object.entries(collect.categories).map(([id, element]) => [id, element.value]));
  // The server turns the form's ids into settings (`settings_from_form`): '' and 'default' follow the global choice.
  act('pr-settings-form', { repo: entry.repo, pr: entry.pr, push: collect.push.value, merge: collect.merge.value, handling });
}

/** A comment that asks something: the user answers whether Claude should handle it (yes / no / other). */

// ── the use of the Claude plan ─────────────────────────────────────────────────────────────────────────────────────

const WINDOW_LABELS = { session: 'Session de 5 h', week: 'Semaine (7 jours)' };

/** "4 h 19" or "2 j 9 h": the time left before a window starts over. */
function timeLeft(epoch) {
  const minutes = Math.max(0, Math.round((epoch * 1000 - Date.now()) / 60000));
  if (minutes >= 1440) return `${Math.floor(minutes / 1440)} j ${Math.floor((minutes % 1440) / 60)} h`;
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')}` : `${minutes} min`;
}

/** The colour class of a window by what is used: green, orange from 70 %, red from 90 %. */
export const usageLevel = (used) => (used >= 90 ? 'crit' : used >= 70 ? 'warn' : 'ok');

/** The quotas of the plan as the Claude CLI last reported them, with a button to measure them again. */
export async function usageDialog(usage) {
  const quotas = usage?.quotas;
  const age = usage?.at ? Math.max(0, Math.round((Date.now() / 1000 - usage.at) / 60)) : null;
  const bars = quotas ? Object.entries(WINDOW_LABELS).map(([name, label]) => {
    const window = quotas[name];
    const used = Math.round(window.used);
    const back = formatDateTime(new Date(window.resetsAt * 1000), { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    // set as a property: the page's content security policy refuses a style attribute
    const fill = h('div', { class: `fill ${usageLevel(used)}` });
    fill.style.width = `${Math.min(100, used)}%`;
    return h('div', { class: 'quota' },
      h('div', {}, h('strong', {}, label), ` : ${used} % utilisé · ${100 - used} % restant`),
      h('div', { class: 'bar', role: 'progressbar', 'aria-valuenow': used, 'aria-valuemin': 0, 'aria-valuemax': 100 }, fill),
      h('div', { class: 'muted' }, `réinitialisation dans ${timeLeft(window.resetsAt)} (${back})`));
  }) : [h('p', {}, 'Aucune mesure pour l’instant : le démon en prend une avant de réveiller une session, ou vous pouvez la demander.')];
  const again = h('button', { class: 'btn', type: 'button', onclick: async () => {
    document.getElementById('dialog').close();
    await act('usage-refresh');
    setTimeout(() => usageDialog(S.lastState?.usage), 0);
  } }, icon('update'), 'Actualiser');
  await ask({
    title: 'Utilisation de Claude',
    body: () => h('div', { class: 'row' }, ...bars,
      h('p', { class: 'muted' }, `${age === null ? 'Jamais mesuré' : `Dernière mesure il y a ${age} min`}. Les quotas sont fournis par la CLI Claude après chaque réponse ; « Actualiser » en demande un avec une requête minimale.`), again),
    buttons: [{ label: 'Fermer', value: true }],
  });
}
