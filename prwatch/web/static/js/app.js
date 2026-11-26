// The entry of the page: it draws the state the server sends, wires the tabs and starts the periodic refresh.
import { allowTabNotifications, applyFilter, applyFocus, card, setExpanded } from './cards.js';
import { S, act, ask, askServer, call, confirmDialog, h, icon, toast, token } from './core.js';
import { addPrDialog, usageDialog, usageLevel } from './dialogs.js';
import { refreshLog, renderLogPrs, selectTab } from './journal.js';
import { applyPermissions, checkPermissions, checkPlugins, permissionsDialog, renderOptions } from './options.js';
import { applyStaticTranslations, formatDateTime, language, locale, t } from './i18n.js';

/** A battery filled to `percent`, in the colour of its `level`. */
function battery(percent, level) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 28 14', width: '24', height: '12', 'aria-hidden': 'true', class: `battery ${level}` })) svg.setAttribute(key, value);
  const add = (tag, attrs) => { const node = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v); svg.append(node); };
  add('rect', { x: '0.75', y: '0.75', width: '23', height: '12.5', rx: '3', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5' });
  add('rect', { x: '25', y: '4.5', width: '2.5', height: '5', rx: '1', fill: 'currentColor' });
  add('rect', { x: '2.5', y: '2.5', width: String(Math.max(0, Math.min(100, percent)) * 0.195), height: '9', rx: '1.5', fill: 'currentColor' });
  return svg;
}

/** The battery of the 5-hour session, with what is left of it; a click opens the detail of the plan. */
function usagePill(usage) {
  const quotas = usage.quotas;
  const left = quotas ? [100 - Math.round(quotas.session.used), 100 - Math.round(quotas.week.used)] : null;
  const level = left ? usageLevel(quotas.session.used) : '';  // the icon is the current session; the week is in the tooltip and the detail
  return h('button', { class: `pill usage ${level}`, type: 'button', 'aria-label': left ? `Session de 5 h : ${left[0]} % restant` : 'Consommation inconnue', title: left ? `Forfait Claude : ${left[0]} % restant sur la session de 5 h, ${left[1]} % sur la semaine. Cliquer pour le détail.` : 'Consommation du forfait inconnue pour l’instant. Cliquer pour la mesurer.', onclick: () => usageDialog(usage) },
    battery(left ? left[0] : 0, level), left ? `${left[0]} %` : t('usage.unknown'));
}

function formatScheduleResume(resumes) {
  const match = /^(.*) (\d{2}:\d{2})$/.exec(resumes || '');
  if (!match) return resumes;
  const [, dayName, time] = match;
  const dayIndex = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'].indexOf(dayName);
  const day = dayIndex < 0 ? dayName : new Intl.DateTimeFormat(locale(), { weekday: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, dayIndex + 1)));
  const [hours, minutes] = time.split(':');
  if (language() === 'en') {
    const formattedTime = new Intl.DateTimeFormat(locale(), { hour: 'numeric', minute: '2-digit', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1, Number(hours), Number(minutes))));
    return `${day} at ${formattedTime}`;
  }
  return `${day} à ${Number(hours)}h${minutes === '00' ? '' : minutes}`;
}

function renderState(state) {
  if (state.permissions) {
    const first = !S.permissionState;
    applyPermissions(state.permissions);
    if (!state.permissions.checks.length && first) checkPermissions(); // never checked: the daemon has not saved a result yet
    else if (state.permissions.undecided.length && !S.permissionPrompted) { S.permissionPrompted = true; permissionsDialog(); }
  }
  const daemon = document.getElementById('daemon');
  const scheduledOff = state.schedule?.enabled && !state.schedule.working;
  const claudeDisabled = Boolean(state.suspension);
  const daemonLabel = scheduledOff
    ? state.schedule.resumes ? t('daemon.resumeAt', { when: formatScheduleResume(state.schedule.resumes) }) : t('daemon.resumeNext')
    : state.daemon.running ? t('daemon.running') : t('daemon.stopped');
  const daemonAction = scheduledOff ? t('daemon.resumeScheduled') : state.daemon.running ? t('daemon.stop') : t('daemon.resume');
  const daemonTitle = scheduledOff
    ? t('daemon.titleWaiting', { when: formatScheduleResume(state.schedule.resumes) || (language() === 'en' ? 'the next scheduled time' : 'la prochaine plage') })
    : state.daemon.running
      ? state.schedule?.override === 'active' ? t('daemon.titleOverride') : t('daemon.titleActive', { pid: state.daemon.pid })
      : t('daemon.titleStopped');
  document.getElementById('daemon-state').replaceChildren(
    h('span', { class: 'status-dot', 'aria-hidden': 'true' }),
    daemonLabel,
  );
  const daemonState = document.getElementById('daemon-state');
  daemonState.className = `status-pill daemon-state ${state.daemon.running && !scheduledOff ? 'on' : 'off'}`;
  daemonState.title = daemonTitle;
  const claudeState = document.getElementById('claude-state');
  claudeState.className = `status-pill claude-state ${claudeDisabled ? 'off' : 'on'}`;
  claudeState.replaceChildren(h('span', { class: 'status-dot', 'aria-hidden': 'true' }), t(claudeDisabled ? 'claude.disabled' : 'claude.enabled'));
  claudeState.title = claudeDisabled
    ? t(state.suspension.kind === 'usage' ? 'daemon.claudeDisabledQuota' : 'daemon.claudeDisabledTokens')
    : t('daemon.claudeEnabled');
  const toggleDaemon = async () => {
    if (scheduledOff) {
      await act('daemon', { command: 'force-schedule' });
      return;
    }
    if (!state.daemon.running) {
      await act('daemon', { command: 'ensure' });
      return;
    }
    if (state.schedule?.enabled) {
      const { value } = await ask({
        title: t('schedule.stopTitle'),
        body: t('schedule.stopBody'),
        buttons: [
          { label: t('schedule.cancel'), value: 'cancel' },
          { label: t('schedule.next'), value: 'scheduled' },
          { label: t('schedule.manual'), value: 'manual', kind: 'danger solid' },
        ],
      });
      if (value === 'scheduled') await act('daemon', { command: 'pause-schedule' });
      else if (value === 'manual') await act('daemon', { command: 'stop' });
      return;
    }
    if (await confirmDialog(t('schedule.stopConfirm'), t('schedule.stopDescription'), t('daemon.stop'))) {
      await act('daemon', { command: 'stop' });
    }
  };
  daemon.replaceChildren(...[
    state.usage ? usagePill(state.usage) : null,
    h('button', {
      class: `btn daemon-toggle icon-button ${state.daemon.running && !scheduledOff ? 'danger' : 'primary'}`,
      type: 'button',
      title: `${t('daemon.powerTitle')}: ${daemonAction}. ${daemonTitle}`,
      'aria-label': `${daemonAction}. ${daemonTitle}`,
      onclick: toggleDaemon,
    }, icon(state.daemon.running && !scheduledOff ? 'powerOff' : 'powerOn')),
  ].filter(Boolean));
  const setup = document.getElementById('setup-banner');
  setup.hidden = !state.setupMissing?.length;
  if (!setup.hidden) {
    setup.replaceChildren(
      h('span', {}, t('setup.workspace')),
      h('button', { class: 'btn', type: 'button', onclick: () => { S.optionsPanel = 'team'; selectTab('options'); } }, t('setup.open')));
  }
  const bitbucket = document.getElementById('bitbucket-banner');
  bitbucket.hidden = !state.bitbucket?.down;
  if (!bitbucket.hidden) {
    bitbucket.replaceChildren(h('span', {}, state.bitbucket.lastOk ? t('bitbucket.down', { at: formatDateTime(state.bitbucket.lastOk) }) : t('bitbucket.downNever')));
  }
  const logBanner = document.getElementById('log-banner');
  logBanner.hidden = !state.log?.large;
  if (!logBanner.hidden) logBanner.replaceChildren(h('span', {}, t('log.large', { size: (state.log.bytes / 1048576).toFixed(1) })));
  renderAlertsBanner();
  const suspension = document.getElementById('suspension');
  suspension.hidden = !state.suspension;
  if (state.suspension) {
    suspension.className = 'banner notice warning';
    suspension.replaceChildren(
      h('span', {}, state.suspension.kind === 'usage'
        ? t('suspension.quota', { at: formatDateTime(state.suspension.at), reason: state.suspension.reason })
        : t('suspension.tokens', { at: formatDateTime(state.suspension.at), reason: state.suspension.reason })),
      h('button', { class: 'btn warning', type: 'button', title: t('suspension.resumeAnyway'), onclick: () => act('resume') }, t('suspension.resumeButton')));
  }
  const prs = document.getElementById('prs');
  prs.replaceChildren(...(state.entries.length ? state.entries.map((entry) => card(entry, state)) : [h('div', { class: 'empty' }, t('pr.none'))]));
  renderLogPrs(state.entries);
  applyFocus(state.focus);
  applyFilter();
  document.getElementById('ack-all').hidden = !state.entries.some((e) => e.needsAttention || e.activity);
  const attention = state.entries.filter((e) => e.needsAttention || e.state === 'gave-up' || e.pendingQuestions?.length).length;
  const badge = document.getElementById('count');
  badge.hidden = attention === 0;
  badge.textContent = attention;
  document.title = attention ? `(${attention}) PR Watch` : 'PR Watch';
  renderOptions(state.options, state.categories, state.quickActionChoices, state.maxQuickActions, state.options.autoMergeChoices);
  // "Quitter" of the status icon: a window opened before the request closes itself
  if (state.quit && state.quit.at > performance.timeOrigin) globalThis.close();
}

// The page tells the server it is open, so that `pr-watch ui` (option "in the browser") reuses this tab instead of opening another.
const tabId = Math.random().toString(36).slice(2);
// Every page registers except the application window, which PR Watch opens with `app=1` in its address (kept for the reloads):
// a tab opened by hand, from a bookmark for instance, is then reused as well.
const inTab = (() => {
  try {
    if (new URLSearchParams(globalThis.location.search).get('app') === '1') sessionStorage.setItem('pw-app', '1');
    return sessionStorage.getItem('pw-app') !== '1';
  } catch { return true; }
})();
if (inTab) globalThis.addEventListener('pagehide', () => navigator.sendBeacon(`/api/action?t=${encodeURIComponent(token)}`, new Blob([JSON.stringify({ action: 'tab-closed', tab: tabId })], { type: 'application/json' })));

// In a browser tab: offers once to allow the notifications that bring the tab back to the front. It is asked here, in the tab that
// stays, and not while switching to it: the window being left would take the browser's question down with it.
function renderAlertsBanner() {
  const banner = document.getElementById('alerts-banner');
  let declined = false;
  try { declined = localStorage.getItem('pw-alerts-declined') === '1'; } catch { /* asked again next time */ }
  banner.hidden = !(inTab && 'Notification' in globalThis && Notification.permission === 'default' && !declined);
  if (banner.hidden || banner.childElementCount) return;
  const done = () => { banner.hidden = true; banner.replaceChildren(); };
  banner.replaceChildren(
    h('span', {}, 'Autoriser les notifications du navigateur ? PR Watch pourra ainsi vous rappeler cet onglet quand il est caché.'),
    h('button', { class: 'btn primary', type: 'button', onclick: () => allowTabNotifications().then(done) }, 'Autoriser'),
    h('button', { class: 'btn', type: 'button', onclick: () => { try { localStorage.setItem('pw-alerts-declined', '1'); } catch { /* shown again */ } done(); } }, 'Non merci'));
}

async function refreshState(force = false) {
  try {
    const state = await call(inTab ? `/api/state?tab=${tabId}` : '/api/state');
    if (S.codeVersion !== null && state.codeVersion !== S.codeVersion) { globalThis.location.replace(`${globalThis.location.pathname}?t=${encodeURIComponent(token)}`); return; }
    S.codeVersion = state.codeVersion;
    const json = JSON.stringify(state);
    // The page is rebuilt only when something changed, so a button under the pointer is not replaced mid-click.
    // ... and not while the user types in one of its fields (the answer to a question of a reviewer)
    const typing = !force && (document.activeElement?.matches?.('#prs input') || document.querySelector('#prs .menu'));
    if ((force || json !== S.lastStateJson) && !typing) {
      S.lastStateJson = json;
      S.lastState = state;
      renderState(state);
    }
  } catch (error) {
    if (error.message !== 'forbidden') document.getElementById('daemon').replaceChildren(h('span', { class: 'pill off' }, 'Tableau de bord injoignable'));
  }
}

// ── journal ─────────────────────────────────────────────────────────────────────────────────────────────────────────

document.getElementById('tabs').addEventListener('click', (event) => {
  const tab = event.target.closest('button[data-tab]');
  if (tab) selectTab(tab.dataset.tab);
});

document.addEventListener('pw-language-changed', () => {
  renderToolbarLabels();
  S.lastStateJson = '';
  S.optionsKey = '';
  S.refresh(true);
  refreshLog(true);
});

const filterChanged = () => { S.logLimit = 200; refreshLog(true); refreshState(); };

document.getElementById('log-mode').addEventListener('change', filterChanged);

document.getElementById('log-pr').addEventListener('change', filterChanged);

document.getElementById('log-refresh').addEventListener('click', () => refreshLog(true));
document.getElementById('log-archive').addEventListener('click', async () => {
  if (!await confirmDialog(t('log.archiveTitle'), t('log.archiveBody'), t('log.archive'), 'solid')) return;
  if ((await act('journal-archive', {}, true)).ok) { S.logLimit = 200; refreshLog(true); }
});

document.getElementById('log-search').addEventListener('input', () => { clearTimeout(S.searchTimer); S.searchTimer = setTimeout(filterChanged, 250); });

// a click on a notification brings the window to the front: look at once at what it asks to show
globalThis.addEventListener('focus', () => refreshState());

document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });

S.refresh = refreshState;
document.getElementById('add-pr').addEventListener('click', addPrDialog);

function renderToolbarLabels() {
  for (const [id, name, key] of [
    ['refresh-prs', 'reset', 'common.refresh'],
    ['expand-all', 'expand', 'toolbar.expand'],
    ['collapse-all', 'collapse', 'toolbar.collapse'],
    ['ack-all', 'check', 'toolbar.acknowledge'],
    ['add-pr', 'add', 'pr.add'],
  ]) {
    const node = document.getElementById(id);
    node.replaceChildren(icon(name), t(key));
  }
}

renderToolbarLabels();
applyStaticTranslations();

refreshState(true);

checkPlugins();

setInterval(checkPlugins, 3600 * 1000);

setInterval(() => {
  refreshState();
  if (!document.getElementById('tab-log').hidden && document.getElementById('log-follow').checked) refreshLog();
}, 3000);

// ── a single window ─────────────────────────────────────────────────────────────────────────────────────────────────
// Opening the dashboard again (a double click on the icon, a notification) launches the browser once more, which opens a new
// window and gives it the focus; the window that was already there then closes itself, so exactly one remains: the newest.
// (A window may close itself only while its history has one entry: the address of this page never changes.)
const channel = new BroadcastChannel('pr-watch-window');
const me = { id: Math.random().toString(36).slice(2), at: performance.timeOrigin };
channel.addEventListener('message', ({ data }) => {
  if (data.type === 'hello' && (data.at > me.at || (data.at === me.at && data.id > me.id))) globalThis.close();
});
channel.postMessage({ type: 'hello', ...me });

// ── the bar of the list ─────────────────────────────────────────────────────────────────────────────────────────────
document.getElementById('refresh-prs').addEventListener('click', () => act('refresh'));
document.getElementById('expand-all').addEventListener('click', () => document.querySelectorAll('#prs .card').forEach((card) => setExpanded(card, true)));
document.getElementById('collapse-all').addEventListener('click', () => document.querySelectorAll('#prs .card').forEach((card) => setExpanded(card, false)));
document.getElementById('ack-all').addEventListener('click', async () => {
  const targets = (S.lastState?.entries || []).filter((entry) => entry.needsAttention || entry.activity);
  for (const entry of targets) await askServer('acknowledge', { repo: entry.repo, pr: entry.pr });
  toast(`${targets.length} pull request${targets.length > 1 ? 's' : ''} marquée${targets.length > 1 ? 's' : ''} comme vue${targets.length > 1 ? 's' : ''}.`);
  S.refresh(true);
});
const filterBox = document.getElementById('pr-filter');
filterBox.addEventListener('input', applyFilter);
filterBox.addEventListener('keydown', (event) => { if (event.key === 'Escape') { filterBox.value = ''; applyFilter(); filterBox.blur(); } });
document.getElementById('pr-actionable').addEventListener('change', applyFilter);
document.addEventListener('keydown', (event) => {
  const typing = event.target.matches?.('input, textarea, select');
  if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey && !document.querySelector('dialog[open]')) { event.preventDefault(); filterBox.focus(); }
});

// ── refresh keys ────────────────────────────────────────────────────────────────────────────────────────────────────
// F5 and Ctrl+R look at the pull requests again (the daemon polls Bitbucket at once) instead of reloading the page.
document.addEventListener('keydown', (event) => {
  const reload = event.key === 'F5' || ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'r');
  if (!reload || event.altKey) return;
  event.preventDefault();
  act('refresh');
});

// ── external links ──────────────────────────────────────────────────────────────────────────────────────────────────
// The window is a browser of its own: a link to another site would open in it. The system opens it in the default browser.
const openExternally = (event) => {
  const link = event.target.closest?.('a[href]');
  if (!link || event.defaultPrevented) return;
  let url;
  try { url = new URL(link.href); } catch (error) { return; }
  if (!['http:', 'https:'].includes(url.protocol) || url.origin === globalThis.location.origin) return;
  event.preventDefault();
  askServer('open-link', { url: url.href }).then((result) => { if (!result.ok) toast(result.message, true); });
};

document.addEventListener('click', openExternally);

document.addEventListener('auxclick', openExternally); // middle click
