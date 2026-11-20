// The options tab: the options, the permissions with their guides, the plugins to update.
import { S, act, ask, askServer, confirmDialog, featureHidden, h, icon, toast } from './core.js';
import { applyStaticTranslations, language, setLanguage, t } from './i18n.js';

export function applyPermissions(summary) {
  S.permissionState = summary;
  document.getElementById('add-pr').hidden = featureHidden('add-pr');
  const blocking = (summary.checks || []).filter((c) => !c.ok && c.required);
  const box = document.getElementById('permission-banner');
  box.replaceChildren(...blocking.map((c) => h('div', { class: 'banner', role: 'alert' },
    h('span', {}, `⚠ ${c.label} : ${c.detail}. PR Watch ne peut pas surveiller correctement sans cela.`),
    h('button', { class: 'btn', type: 'button', onclick: () => permissionsDialog() }, 'Voir le guide'))));
  box.hidden = !blocking.length;
  if (S.permissionRefresh) S.permissionRefresh();
}

export async function checkPermissions() {
  const result = await askServer('permissions-check');
  if (result.ok) applyPermissions(result.permissions);
}

/** Lists every permission with its state and its guide: open when the permission is missing, closed when it is in place.
 *  A missing one may be declined, which hides the functions that need it. */

export async function permissionsDialog() {
  const body = h('div', { class: 'row' });
  const labels = (check) => (check.features || []).map((f) => S.permissionState.featureLabels[f]);
  const opened = new Map();  // id of a check -> its guide open or closed, as the user left it: a redraw must not undo it
  let drawn = '';
  const draw = () => {
    const checks = S.permissionState?.checks || [];
    // the page refreshes the permissions every few seconds: redrawing the same thing would close the guide being read
    const signature = JSON.stringify([checks, S.permissionState?.featureLabels]);
    if (signature === drawn) return;
    drawn = signature;
    const missing = checks.filter((c) => !c.ok);
    body.replaceChildren(
      h('p', { class: 'muted' }, missing.length ? 'Il manque des autorisations. Suivez leur guide, puis revérifiez ; ou refusez les facultatives : les fonctions concernées sont alors masquées.' : '✔ Toutes les autorisations sont en place.'),
      ...checks.map((check) => {
        const state = check.ok ? { text: '✔ En place', cls: 'ok' } : check.declined ? { text: '○ Refusée', cls: 'declined' } : check.required ? { text: '✖ Manquante', cls: 'ko' } : { text: '○ Manquante', cls: 'warn' };
        return h('section', { class: `perm ${check.ok ? 'granted' : ''}` },
          h('h3', {}, check.label, h('span', { class: `status ${state.cls}` }, state.text), h('span', { class: `tag ${check.required ? 'required' : ''}` }, check.required ? 'Indispensable' : 'Facultatif')),
          h('p', {}, check.why), h('p', { class: 'muted' }, check.detail),
          check.guide ? h('details', { open: opened.has(check.id) ? opened.get(check.id) : !check.ok, ontoggle: (event) => opened.set(check.id, event.target.open) }, h('summary', {}, check.guide.title),
            h('ol', {}, ...check.guide.steps.map((step) => h('li', {}, step))),
            h('div', { class: 'links' }, ...check.guide.links.map((l) => h('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer' }, `${l.label} ↗`)))) : null,
          check.ok ? null : check.declined
            ? h('p', { class: 'muted' }, `Fonctions masquées : ${labels(check).join(', ') || 'aucune'}.`)
            : h('button', { class: 'btn', type: 'button', onclick: async () => {
              if (check.required && !(await confirmDialog('Refuser une autorisation indispensable ?', 'PR Watch ne pourra pas surveiller correctement vos pull requests sans elle.', 'Refuser', 'danger solid'))) return;
              const result = await askServer('permissions-decline', { ids: [check.id] });
              if (result.ok) { applyPermissions(result.permissions); toast(labels(check).length ? `Masqué : ${labels(check).join(', ')}.` : 'Refusé.'); } else toast(result.message, true);
            } }, 'Refuser et masquer' + (labels(check).length ? ` (${labels(check).join(', ')})` : '')));
      }),
      h('button', { class: 'btn', type: 'button', onclick: async () => { toast('Vérification…'); await checkPermissions(); } }, icon('update'), 'Revérifier'));
  };
  S.permissionRefresh = draw;
  draw();
  await ask({ title: 'Autorisations de PR Watch', body: () => body, buttons: [{ label: 'Fermer', value: true }] });
  S.permissionRefresh = null;
}

// ── plugins ─────────────────────────────────────────────────────────────────────────────────────────────────────────

export function renderPluginBanner() {
  const box = document.getElementById('updates');
  const outdated = S.pluginLines.filter((p) => p.outdated);
  box.replaceChildren(...outdated.map((plugin) => h('div', { class: 'banner notice', role: 'status' },
    h('span', {}, `${plugin.label} : ${plugin.installed} installé, ${plugin.latest} disponible.`),
    h('button', { class: 'btn', type: 'button', onclick: () => updatePlugin(plugin) }, icon('update'), 'Mettre à jour'))));
  box.hidden = !outdated.length;
}

export async function updatePlugin(plugin) {
  if (!(await confirmDialog(`Mettre à jour ${plugin.label} ?`, `${plugin.installed} → ${plugin.latest}. La nouvelle version est téléchargée depuis les releases GitHub du plugin ; l’éditeur doit ensuite être redémarré (IntelliJ) ou rechargé (VS Code).`, 'Mettre à jour', 'primary'))) return;
  toast('Mise à jour en cours…');
  const result = await askServer('plugins-update', { kind: plugin.id }, true);
  toast(result.message, !result.ok);
  if (result.plugins) S.pluginLines = result.plugins;
  renderPluginBanner();
  S.optionsKey = '';
  if (S.lastState) renderOptions(S.lastState.options, S.lastState.categories, S.lastState.quickActionChoices, S.lastState.maxQuickActions);
}

export async function checkPlugins() {
  const result = await askServer('plugins-status');
  if (result.ok) { S.pluginLines = result.plugins; renderPluginBanner(); }
}

export const OPTION_FIELDS = [
  ['autoPush', 'checkbox', 'Les corrections poussent automatiquement sur la branche', 'Sinon la session réveillée s’arrête au commit.', 'general'],
  ['keepUntilTicketValidated', 'checkbox', 'Garder une PR mergée tant que son ticket Jira n’a pas changé d’état', 'Elle passe en « en attente de validation du ticket » ; avec plusieurs PR sur un ticket, c’est la dernière mergée qui attend.', 'general'],
  ['notify', 'checkbox', 'Notifications de bureau', '', 'general'],
  ['pollSeconds', 'number', 'Intervalle de surveillance (s)', '15 à 900', 'general'],
  ['maxAttempts', 'number', 'Essais automatiques maximum par PR', '1 à 20', 'general'],
  ['minApprovals', 'number', 'Approbations avant « prête à merger »', '0 à 10', 'general'],
  ['usageGuard', 'checkbox', 'Se désactiver quand le forfait Claude est presque épuisé', 'Évite que les réveils piochent dans les crédits de dépassement ; repart tout seul à la remise à zéro.', 'usage'],
  ['usageSessionMarginPercent', 'number', 'Seuil de la session de 5 h : désactiver quand il reste moins de … %', '0 à 50', 'usage'],
  ['usageWeekMarginPercent', 'number', 'Seuil de la semaine : désactiver quand il reste moins de … %', '0 à 50', 'usage'],
  ['removeDoneAfterMinutes', 'number', 'Retirer de la liste une PR fusionnée ou refusée après (minutes)', '0 = jamais', 'general'],
  ['bitbucketWorkspace', 'text', 'Espace Bitbucket (workspace)', 'Le nom qui précède le dépôt dans l’adresse de vos pull requests. Obligatoire : tant qu’il est vide, rien n’est surveillé.', 'team', 'Exemple : pour https://bitbucket.org/acme-corp/api-server/pull-requests/12, saisissez acme-corp.', 'acme-corp'],
  ['jiraSite', 'text', 'Site Jira', 'L’adresse de votre Jira Cloud, utilisée pour lire un ticket et changer son état. Vide : aucun lien ni action sur les tickets.', 'team', 'Exemple : https://acme-corp.atlassian.net', 'https://acme-corp.atlassian.net'],
  ['jiraImpactsField', 'text', 'Champ Jira des impacts', 'Le nom d’un champ de vos tickets que votre workflow oblige à remplir avant certaines transitions ; PR Watch propose un texte, rédigé par Claude, à l’état du ticket. Vide : aucun champ.', 'team', 'Exemple : Description des impacts potentiels', 'Description des impacts potentiels'],
  ['sonarPattern', 'text', 'Étapes et statuts du contrôle qualité (Sonar)', 'Une expression régulière (casse ignorée) comparée au nom des étapes du pipeline et des statuts de la PR : un résultat en échec réveille Claude. Vide : pas de contrôle qualité.', 'team', 'Exemple : sonar reconnaît l’étape « Sonar analysis » et le statut « SonarQube Code Analysis ».', 'sonar'],
  ['reviewStepPattern', 'text', 'Étape de revue automatique du pipeline', 'Une expression régulière sur le nom de l’étape de votre pipeline qui fait relire la PR par une IA : si elle réussit sans publier son commentaire, son verdict est lu dans le journal. Vide : pas de revue automatique. Les commentaires à suivre se règlent dans un profil.', 'team', 'Exemple : AI review|pr-review reconnaît une étape nommée « AI review » ou « pr-review ».', 'AI review|pr-review'],
  ['infraPatterns', 'lines', 'Erreurs d’infrastructure', 'Une phrase par ligne : une étape en échec dont le journal en contient une est relancée une fois, sans réveiller Claude (ce n’est pas un problème de code).', 'team', 'Exemples : ECONNRESET · No space left on device · Could not resolve host · Service Unavailable', 'ECONNRESET'],
  ['cloneFolders', 'lines', 'Dossiers où chercher les clones locaux', 'Un dossier par ligne, en plus de ceux des PR déjà suivies : à l’ajout d’une PR, le clone du dépôt est cherché dans <dossier>/<nom du dépôt>.', 'team', 'Exemple : ~/projets, pour retrouver ~/projets/api-server.', '~/projets'],
];

let jiraMeta = null;  // the types of ticket and states read from Jira, asked once per page

/** The state a merged pull request's ticket moves to by default, by type of ticket (each type has its own workflow). */
function ticketNodes(mapping) {
  const list = h('div', { class: 'ticket-rows' });
  const note = h('p', { class: 'muted' }, 'Lecture des types et des états dans Jira…');
  // The states of a type are the ones of its own workflow; for a type Jira does not know, every state of the site.
  const statesOf = (kind) => {
    const own = jiraMeta && Object.entries(jiraMeta.statuses).find(([name]) => name.toLowerCase() === kind.trim().toLowerCase());
    if (!kind.trim()) return [];  // no type chosen yet: no state to choose either
    return own ? own[1] : jiraMeta?.allStatuses || [];
  };
  // a list to choose from once Jira answered (a value that is not in it stays), else a text field
  const choice = (options, value, placeholder, label) => {
    if (!jiraMeta) return h('input', { type: 'text', value, placeholder, 'aria-label': label });
    const box = h('select', { 'aria-label': label });
    fillChoice(box, options, value, placeholder);
    return box;
  };
  const fillChoice = (box, options, value, placeholder) => {
    const values = value && !options.includes(value) ? [value, ...options] : options;
    box.replaceChildren(h('option', { value: '' }, placeholder), ...values.map((name) => h('option', { value: name }, name)));
    box.value = value;
  };
  const save = () => {
    const next = {};
    for (const line of list.querySelectorAll('.ticket-row')) {
      const [kind, state] = line.querySelectorAll('input, select');
      if (kind.value.trim() && state.value.trim()) next[kind.value.trim()] = state.value.trim();  // a half-filled row waits
    }
    // Nothing new to save (a row still half filled): saving would redraw the form and drop that row under the user's fingers.
    const same = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
    if (same(next, S.lastState?.options?.ticketTransitions || mapping)) return null;
    return act('options', { values: { ticketTransitions: next } });
  };
  const row = (kind = '', state = '') => {
    const kindBox = choice(jiraMeta?.types || [], kind, jiraMeta ? '— type de ticket —' : 'Type (ex. Bug)', 'Type de ticket');
    const stateBox = choice(statesOf(kind), state, jiraMeta ? '— état visé —' : 'État visé (ex. Await deployment)', 'État visé');
    stateBox.disabled = Boolean(jiraMeta) && !kindBox.value;
    kindBox.addEventListener('change', () => {
      if (jiraMeta) {  // the states follow the type chosen
        fillChoice(stateBox, statesOf(kindBox.value), '', '— état visé —');
        stateBox.disabled = !kindBox.value;
      }
      save();
    });
    stateBox.addEventListener('change', save);
    const line = h('div', { class: 'ticket-row' }, kindBox, stateBox,
      h('button', { class: 'btn mini', type: 'button', title: 'Retirer ce type', 'aria-label': 'Retirer ce type', onclick: () => { line.remove(); save(); } }, icon('stop')));
    return line;
  };
  const drawRows = (pairs) => list.replaceChildren(...pairs.map(([kind, state]) => row(kind, state)));
  drawRows(Object.entries(mapping));
  const loaded = () => {
    // Jira answered after the rows were drawn: draw them again as lists, keeping what is typed so far
    const pairs = [...list.querySelectorAll('.ticket-row')].map((line) => [...line.querySelectorAll('input, select')].map((box) => box.value));
    note.textContent = `Types et états lus dans Jira${jiraMeta.projects?.length ? ` (projet${jiraMeta.projects.length > 1 ? 's' : ''} ${jiraMeta.projects.join(', ')})` : ' (tous les projets)'} : les états proposés suivent le workflow du type choisi.`;
    drawRows(pairs);
  };
  if (jiraMeta) loaded();
  else askServer('jira-metadata').then((result) => {
    if (result.ok) { jiraMeta = result; loaded(); } else note.textContent = `Liste Jira indisponible (${result.message}) : saisie libre.`;
  });
  return [
    h('p', { class: 'muted' }, 'État vers lequel le ticket est proposé une fois la PR mergée, selon son type : chaque type a son propre workflow, donc pas d’état par défaut. Un type absent de la liste ne présélectionne rien. L’état est retrouvé par le nom de la transition ou de l’état visé, sans tenir compte de la casse ni des accents (« close » trouve « Closed »).'),
    note, list,
    h('button', { class: 'btn', type: 'button', onclick: () => { const line = row(); list.append(line); line.querySelector('input, select').focus(); } }, icon('add'), 'Ajouter un type'),
  ];
}

const DAYS = ['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.'];

/** The hours PR Watch works: toggles save on change; opening and closing times save when leaving the field. */
function scheduleNodes(schedule) {
  const enabled = h('input', { type: 'checkbox', name: 'schedule-enabled', checked: schedule.enabled });
  const days = DAYS.map((label, index) => h('label', { class: 'check day' }, h('input', { type: 'checkbox', name: `schedule-day-${index}`, checked: schedule.days.includes(index) }), label));
  const start = h('input', { type: 'time', name: 'schedule-start', value: schedule.start, required: true });
  const end = h('input', { type: 'time', name: 'schedule-end', value: schedule.end, required: true });
  const save = async () => {
    const chosen = days.map((label, index) => (label.querySelector('input').checked ? index : -1)).filter((index) => index >= 0);
    const result = await act('options', { values: { schedule: { enabled: enabled.checked, days: chosen, start: start.value, end: end.value } } });
    if (!result.ok) { S.optionsKey = ''; S.refresh(true); }  // back to what is stored
  };
  for (const input of [enabled, ...days.map((label) => label.querySelector('input'))]) input.addEventListener('change', save);
  for (const input of [start, end]) input.addEventListener('blur', save);
  return [
    h('label', { class: 'option' }, h('span', {}, 'Limiter PR Watch à des horaires', h('div', { class: 'hint' }, 'Hors horaires, le démon se met en attente : aucune PR n’est vérifiée ni aucune notification envoyée. Il reprend automatiquement à la prochaine ouverture ; le bouton du démon peut forcer la reprise jusqu’à la prochaine fermeture.')), enabled),
    h('div', { class: 'option' }, h('span', {}, 'Jours'), h('div', { class: 'days' }, ...days)),
    h('label', { class: 'option' }, h('span', {}, 'De'), start),
    h('label', { class: 'option' }, h('span', {}, 'À'), end),
  ];
}

/** Loads a file of team settings: one that comes with PR Watch, or a JSON file of the user's (read here, checked by the server). */
function profileRow() {
  const apply = async (profile) => {
    if (!(await confirmDialog('Charger ce profil ?', 'Il remplace les réglages d’équipe qu’il contient (espace Bitbucket, Jira, Sonar, revue automatique, états des tickets) ; les autres restent.', 'Charger', 'primary'))) return;
    await act('profile-load', { profile });
  };
  const file = h('input', { type: 'file', accept: '.json,application/json', hidden: true });
  file.addEventListener('change', async () => {
    const chosen = file.files[0];
    file.value = '';
    if (!chosen) return;
    try { await apply(JSON.parse(await chosen.text())); } catch (error) { toast('Ce fichier n’est pas du JSON valide.', true); }
  });
  return h('div', { class: 'option' }, h('span', {}, 'Profil d’équipe', h('div', { class: 'hint' }, 'Un fichier de réglages d’équipe (dossier profiles/ du dépôt) à charger d’un coup.')),
    h('button', { class: 'btn', type: 'button', onclick: () => file.click() }, 'Charger un fichier…'), file);
}

export function renderOptions(values, categories, quickChoices, maxQuick, autoMergeChoices = []) {
  const key = JSON.stringify([values, categories, S.pluginLines, S.permissionState]);
  if (key === S.optionsKey) return;
  S.optionsKey = key;
  const form = document.getElementById('options');
  const handling = categories.map((category) => {
    const select = h('select', { name: `handling-${category.id}` }, ...category.modes.map((m) => h('option', { value: m.id }, m.label)));
    select.value = values.handling[category.id];
    select.addEventListener('change', () => act('options', { values: { handling: { [category.id]: select.value } } }));
    return h('label', { class: 'option' }, h('span', {}, category.label), select);
  });
  const auto = !Array.isArray(values.quickActions);
  const chosen = new Set(values.quickActions || []);
  const autoBox = h('input', { type: 'checkbox', name: 'quick-auto', checked: auto });
  autoBox.addEventListener('change', () => act('options', { values: { quickActions: autoBox.checked ? null : quickChoices.filter((c) => ['open', 'wake', 'push'].includes(c.id)).map((c) => c.id) } }));
  const boxes = quickChoices.map((choice) => {
    const box = h('input', { type: 'checkbox', name: `quick-${choice.id}`, checked: chosen.has(choice.id), disabled: auto });
    box.addEventListener('change', () => {
      const ids = quickChoices.filter((c) => form.elements[`quick-${c.id}`].checked).map((c) => c.id);
      if (ids.length > maxQuick) { box.checked = false; toast(`${maxQuick} actions au plus.`, true); return; }
      act('options', { values: { quickActions: ids } });
    });
    return h('label', { class: 'check quick' }, box, choice.label);
  });
  const field = ([name, type, label, hint, , example, placeholder]) => {
    const input = type === 'lines'
      ? Object.assign(h('textarea', { name, rows: 4, placeholder: placeholder || '' }), { value: (values[name] || []).join('\n') })  // a textarea has no `value` attribute
      : h('input', { type, name, placeholder: placeholder || '', ...(type === 'number' ? { value: values[name], min: 0 } : type === 'text' ? { value: values[name] || '' } : { checked: values[name] }) });
    const read = () => (type === 'checkbox' ? input.checked : type === 'number' ? Number.parseInt(input.value, 10)
      : type === 'lines' ? input.value.split('\n').map((line) => line.trim()).filter(Boolean) : input.value);
    input.addEventListener('change', async () => {
      const result = await act('options', { values: { [name]: read() } });
      if (!result.ok) { S.optionsKey = ''; S.refresh(true); }  // back to what is stored
    });
    return h('label', { class: 'option' }, h('span', {}, label, hint ? h('div', { class: 'hint' }, hint) : null, example ? h('div', { class: 'hint example' }, example) : null), input);
  };
  const mergeSelect = h('select', { name: 'autoMerge' }, ...autoMergeChoices.map((c) => h('option', { value: c.id }, c.label)));
  mergeSelect.value = values.autoMerge;
  mergeSelect.addEventListener('change', () => act('options', { values: { autoMerge: mergeSelect.value } }));
  const autoMergeRow = h('label', { class: 'option' }, h('span', {}, 'Merge automatique', h('div', { class: 'hint' }, 'Désactivé par défaut. Jamais tant qu’une question de relecteur attend votre réponse. Chaque PR peut avoir son propre choix (« Réglages… » de sa carte).')), mergeSelect);
  const languageSelect = h('select', {
    id: 'language-select',
    class: 'language-select',
    'aria-label': t('language.label'),
    onchange: (event) => {
      setLanguage(event.target.value);
      applyStaticTranslations();
      document.dispatchEvent(new Event('pw-language-changed'));
    },
  },
  h('option', { value: 'fr' }, t('language.french')),
  h('option', { value: 'en' }, t('language.english')));
  languageSelect.value = language();
  const languageRow = h('label', { class: 'option' }, h('span', {}, t('language.label')), languageSelect);
  const fieldsOf = (group) => OPTION_FIELDS.filter((f) => f[4] === group).map(field);
  const permissions = [h('div', { class: 'plugins' }, ...(S.permissionState?.checks || []).map((c) => h('div', { class: 'plugin' },
    h('span', {}, c.label), h('span', { class: 'muted' }, c.ok ? c.detail : `${c.declined ? 'refusée · ' : ''}${c.detail}`), h('span', {}, c.ok ? '✔' : c.required ? '✖' : '○')))),
  h('div', { class: 'plugin' }, h('span', { class: 'muted' }, S.permissionState?.at ? `Vérifié le ${S.permissionState.at}` : ''), h('button', { class: 'btn', type: 'button', onclick: () => permissionsDialog() }, 'Guide'), h('button', { class: 'btn', type: 'button', onclick: checkPermissions }, icon('update'), 'Revérifier'))];
  const plugins = [h('div', { class: 'plugins' }, ...(S.pluginLines.length ? S.pluginLines.map((plugin) => h('div', { class: 'plugin' },
    h('span', {}, plugin.label), h('span', { class: 'muted' }, plugin.latest ? `${plugin.installed} installé · dernière version ${plugin.latest}` : `${plugin.installed} installé`),
    plugin.outdated ? h('button', { class: 'btn', type: 'button', onclick: () => updatePlugin(plugin) }, icon('update'), 'Mettre à jour') : h('span', { class: 'muted' }, plugin.latest ? '✔ à jour' : ''))) : [h('p', { class: 'muted' }, 'Aucun plugin Claude Chat installé.')]))];
  // one panel per kind of option; the user's last choice of panel is kept
  const panels = [
    ['general', 'Général', [...fieldsOf('general'), autoMergeRow, languageRow]],
    ['tokens', 'Économie de jetons', [
      h('p', { class: 'muted' }, 'Deux garde-fous pour limiter la consommation de votre forfait Claude : des horaires de fonctionnement, et l’arrêt avant d’entamer les crédits de dépassement.'),
      h('h2', { class: 'section' }, 'Horaires'), ...scheduleNodes(values.schedule),
      h('h2', { class: 'section' }, 'Forfait'), ...fieldsOf('usage')]],
    ['team', 'Équipe', [h('p', { class: 'muted' }, 'Ce qui dépend de votre équipe : où sont les pull requests, le site Jira, le contrôle qualité.'), profileRow(), ...fieldsOf('team')]],
    ['ticket', 'Ticket Jira', ticketNodes(values.ticketTransitions)],
    ['events', 'Événements', [h('p', { class: 'muted' }, 'Ce que Claude traite, par défaut. Chaque PR peut avoir ses propres choix (bouton « Réglages… » de sa carte).'), ...handling]],
    ['cards', 'Cartes', [h('label', { class: 'check' }, autoBox, 'Automatique : les actions qui conviennent à l’état de chaque PR (prête à merger, à pousser, attention…)'), h('p', { class: 'muted' }, `Sinon, ces actions sont toujours affichées (${maxQuick} au plus) sur une carte réduite ; les autres sont dans le menu « … » de la carte.`), h('div', { class: 'quick-list' }, ...boxes)]],
    ['system', 'Système', [h('h2', { class: 'section' }, 'Autorisations'), ...permissions, h('h2', { class: 'section' }, 'Plugins Claude Chat'), ...plugins]],
  ];
  let current = panels.some(([id]) => id === S.optionsPanel) ? S.optionsPanel : panels[0][0];
  const show = (id) => {
    S.optionsPanel = id;
    try { localStorage.setItem('pw-options-panel', id); } catch (error) { /* the first panel next time */ }
    for (const button of form.querySelectorAll('.subtabs button')) button.setAttribute('aria-selected', String(button.dataset.panel === id));
    for (const panel of form.querySelectorAll('.panel')) panel.hidden = panel.dataset.panel !== id;
  };
  form.replaceChildren(
    h('nav', { class: 'tabs subtabs', role: 'tablist', 'aria-label': 'Catégories d’options' },
      ...panels.map(([id, label]) => h('button', { type: 'button', role: 'tab', 'data-panel': id, onclick: () => show(id) }, label))),
    ...panels.map(([id, , nodes]) => h('div', { class: 'panel', role: 'tabpanel', 'data-panel': id }, ...nodes)));
  show(current);
}

// ── tabs and start ──────────────────────────────────────────────────────────────────────────────────────────────────
