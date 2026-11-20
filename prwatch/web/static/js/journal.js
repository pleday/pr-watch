// The journal tab: entries grouped and coloured by type, reports drawn from their Markdown.
import { S, call, h, toast } from './core.js';
import { formatDate, t } from './i18n.js';

export function renderLogPrs(entries) {
  const select = document.getElementById('log-pr');
  const keys = entries.map((e) => `${e.repo}#${e.pr}`);
  const wanted = select.value;
  const have = [...select.options].slice(1).map((o) => o.value);
  if (have.join() === keys.join()) return;
  select.replaceChildren(h('option', { value: '' }, 'Toutes les PR'), ...keys.map((k) => h('option', { value: k }, k.replace('#', ' #'))));
  select.value = keys.includes(wanted) ? wanted : '';
}

export function showJournal(key, report = false) {
  document.getElementById('log-pr').value = key;
  if (report) {
    // a report is in "all" and "Claude" views, but a search could hide it
    document.getElementById('log-mode').value = 'all';
    document.getElementById('log-search').value = '';
  }
  S.wantReport = report;
  S.logLimit = 200;
  selectTab('log');
}

export function runsNodes(runs) {
  return runs.map(([text, style]) => (style === 'bold' ? h('span', { class: 'bold' }, text) : style === 'code' ? h('code', {}, text) : text));
}

export function detailNode(line) {
  if (line.blank) return h('div', { class: 'line' });
  if (line.verdict) return h('div', { class: `line verdict-${line.verdict}` }, runsNodes(line.runs));
  const indent = '  '.repeat(line.indent || 0);
  return line.bullet
    ? h('div', { class: 'line bulleted' }, indent, h('span', { class: 'mark' }, line.bullet), runsNodes(line.runs))
    : h('div', { class: 'line' }, indent, runsNodes(line.runs));
}

export function renderLog(data, keepPosition = 0) {
  const modeSelect = document.getElementById('log-mode');
  if (!modeSelect.options.length) {
    modeSelect.replaceChildren(...data.modes.map((m) => h('option', { value: m.id }, m.label)));
  }
  const log = document.getElementById('log');
  const scroller = document.querySelector('main');
  const nearBottom = scroller.clientHeight + scroller.scrollTop >= scroller.scrollHeight - 80;
  const nodes = [];
  if (data.older) {
    nodes.push(h('button', { class: 'btn older', type: 'button', onclick: () => { S.logLimit += 200; refreshLog(true, true); } }, t('date.older', { count: data.older })));
  }
  let lastDate = null;
  for (const entry of data.entries) {
    if (entry.date !== lastDate) {
      lastDate = entry.date;
      nodes.push(h('div', { class: 'day' }, formatDate(new Date(`${entry.date}T12:00:00`))));
    }
    const when = entry.count > 1 ? `${entry.time}–${entry.last_time}` : entry.time;
    const message = entry.message.startsWith('NOTIFY ') ? entry.message.slice(7) : entry.message;
    nodes.push(h('div', { class: `entry k-${entry.kind}`, 'data-report': entry.report ? '1' : null }, h('span', { class: 'bullet' }, '•'), h('span', { class: 'time' }, when), entry.pr ? h('span', { class: 'pr' }, entry.pr) : null, message, entry.count > 1 ? h('span', { class: 'times' }, `×${entry.count}`) : null));
    if (entry.details.length) nodes.push(h('div', { class: 'details' }, entry.details.map(detailNode)));
  }
  log.replaceChildren(...(nodes.length ? nodes : [h('div', { class: 'muted' }, t('date.emptyLog'))]));
  const shown = data.shown - data.older;
  document.getElementById('log-summary').textContent = `${t('date.shown', { shown, total: data.shown })}${data.shown < data.total ? t('date.total', { total: data.total }) : ''}`;
  if (S.wantReport && revealReport(data)) return;
  if (keepPosition) scroller.scrollTo(0, scroller.scrollHeight - keepPosition);
  else if (nearBottom) scroller.scrollTo(0, scroller.scrollHeight);
}

// Brings the last report on screen; false when it is not among the entries loaded yet and older ones were asked for.
export function revealReport(data) {
  const reports = [...document.querySelectorAll('#log .entry[data-report]')];
  if (!reports.length && data.older) {
    S.logLimit += 200;
    refreshLog(true);
    return true;
  }
  S.wantReport = false;
  const target = reports.at(-1);
  if (!target) {
    toast('Aucun rapport de Claude dans le journal pour cette PR.');
    return false;
  }
  target.scrollIntoView({ block: 'start' });
  target.classList.add('focus');
  setTimeout(() => target.classList.remove('focus'), 4000);
  return true;
}

export async function refreshLog(force = false, older = false) {
  const mode = document.getElementById('log-mode').value || 'all';
  const search = document.getElementById('log-search').value.trim();
  const pr = document.getElementById('log-pr').value;
  // loading older entries keeps the same entries in view: the distance to the bottom of the page is what stays
  const scroller = document.querySelector('main');
  const fromBottom = older ? scroller.scrollHeight - scroller.scrollTop : 0;
  try {
    const data = await call(`/api/log?mode=${encodeURIComponent(mode)}&search=${encodeURIComponent(search)}&pr=${encodeURIComponent(pr)}&limit=${S.logLimit}`);
    const json = JSON.stringify(data);
    if (force || json !== S.lastLogJson) {
      S.lastLogJson = json;
      renderLog(data, fromBottom);
    }
  } catch { /* shown by call() */ }
}

// ── options ─────────────────────────────────────────────────────────────────────────────────────────────────────────

// ── permissions ─────────────────────────────────────────────────────────────────────────────────────────────────────

export function selectTab(name) {
  document.body.classList.toggle('options-active', name === 'options');
  for (const button of document.querySelectorAll('#tabs button')) button.setAttribute('aria-selected', String(button.dataset.tab === name));
  for (const id of ['prs', 'log', 'options']) document.getElementById(`tab-${id}`).hidden = id !== name;
  if (name === 'log') refreshLog(true);
}
