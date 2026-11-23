import { createRequire } from 'module';
import fs from 'fs';
import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';

// playwright-core: from PLAYWRIGHT_DIR (a directory whose node_modules has it) or the e2e/ folder itself.
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(`${process.env.PLAYWRIGHT_DIR || here}/`);
const { chromium } = require('playwright-core');
const ROOT = path.join(here, '.work');
const info = JSON.parse(fs.readFileSync(`${ROOT}/info.json`));
const URL = `http://127.0.0.1:${info.port}/?t=${info.token}`;
const registry = () => JSON.parse(fs.readFileSync(`${info.home}/registry.json`, 'utf8'));
const entryOf = (pr) => registry().find((e) => e.pr === pr);
// The registry is edited behind the page's back (the daemon is not running in this test): the page redraws within seconds.
const patchEntry = (pr, fields) => {
  const entries = registry();
  Object.assign(entries.find((e) => e.pr === pr), fields);
  fs.writeFileSync(`${info.home}/registry.json`, JSON.stringify(entries));
};
const git = (cwd, ...args) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', ...args], { cwd, encoding: 'utf8' }).trim();

let failures = 0;
const check = (label, ok, detail = '') => { console.log(`${ok ? 'ok   ' : 'FAIL '}${label}${ok ? '' : `  ${detail}`}`); if (!ok) failures += 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// CHROME_PATH points at a Chromium-based browser; without it Playwright looks for an installed Chrome.
const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const context = await browser.newContext({ viewport: { width: 1200, height: 900 } });
const page = await context.newPage();
const problems = [];
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });

const cardOf = (pr) => page.locator('.card').nth(pr - 1);
const toastText = async () => (await page.locator('.toast').last().textContent()) || '';

await page.goto(URL);
await page.waitForSelector('.card');
check('cards start collapsed', (await page.locator('.card.expanded').count()) === 0 && !(await cardOf(1).locator('.actions').isVisible()));
for (const toggle of await page.locator('.card .toggle').all()) await toggle.click();
check('toggle expands cards', (await cardOf(1).locator('.actions').isVisible()));

console.log('== a single window');
await page.evaluate(() => { globalThis.closedByItself = false; globalThis.close = () => { globalThis.closedByItself = true; }; });
const newer = await context.newPage();
let newerClosed = false;
await newer.evaluate(() => { globalThis.close = () => { globalThis.closedByItself = true; }; }).catch(() => {});
await newer.goto(URL);
await newer.evaluate(() => { globalThis.close = () => { globalThis.closedByItself = true; }; });
await sleep(800);
check('the window that was open closes itself when a newer one opens', await page.evaluate(() => globalThis.closedByItself === true));
check('and the newer one stays', await newer.evaluate(() => globalThis.closedByItself !== true));
await newer.close();
await page.evaluate(() => { globalThis.closedByItself = false; });

console.log('== external links');
const posted = [];
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  if (body.action !== 'open-link') return route.continue();
  posted.push(body.url);
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: '' }) });  // not really opened here
});
const popups = [];
context.on('page', (p) => popups.push(p));
await cardOf(1).locator('a.key').click();
await sleep(500);
check('a link to another site goes to the system browser', posted.length === 1 && posted[0].startsWith('https://bitbucket.org/'), JSON.stringify(posted));
check('and opens no window of the dashboard browser', popups.length === 0);
await page.unroute('**/api/action');

console.log('== refresh key');
const refreshes = [];
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  if (body.action !== 'refresh') return route.continue();
  refreshes.push(body.action);
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'Actualisation demandée.' }) });
});
await page.evaluate(() => { globalThis.stillHere = true; });
await page.keyboard.press('F5');
await sleep(500);
check('F5 asks for a refresh of the pull requests', refreshes.length === 1, String(refreshes.length));
check('and does not reload the page', await page.evaluate(() => globalThis.stillHere === true));
await page.unroute('**/api/action');

console.log('== rendering');
check('seven cards', (await page.locator('.card').count()) === 7);
const chips = await page.locator('.chip').allTextContents();
check('states shown', ['Surveillée', 'Prête à merger ✓', 'Votre attention', 'Abandonnée (essais épuisés)', 'En pause', 'Terminée', 'Décision attendue'].every((c) => chips.includes(c)), chips.join(' | '));
check('blockers listed', (await cardOf(1).locator('.blockers').textContent()).includes('approbations 0/1'));
check('attention banner', (await cardOf(3).locator('.banner').textContent()).includes('en échec'));
if (process.env.E2E_SESSION_TITLE) check('session title of the conversation shown', (await cardOf(1).textContent()).includes(process.env.E2E_SESSION_TITLE));
check('stopped daemon state is in the window header', (await page.locator('.brand #daemon-state').textContent()).includes('Démon arrêté')
  && (await page.locator('#daemon .daemon-toggle').getAttribute('aria-label')).includes('Reprendre le démon'));
await page.route('**/api/state*', async (route) => {
  const response = await route.fetch();
  const state = await response.json();
  state.schedule = { enabled: true, working: false, resumes: 'lundi 09:00' };
  await route.fulfill({ response, json: state });
});
await page.reload();
await page.waitForFunction(() => document.querySelector('#daemon-state')?.textContent.includes('Démon arrêté · reprise lundi à 9h'));
check('scheduled daemon state shows the next weekday and time', (await page.locator('#daemon-state').textContent()).includes('Démon arrêté · reprise lundi à 9h')
  && !(await page.locator('#daemon-state').textContent()).includes('Claude'));
await page.unroute('**/api/state*');
await page.reload();
await page.waitForSelector('.card');
await page.route('**/api/state*', async (route) => {
  const response = await route.fetch();
  const state = await response.json();
  state.daemon.running = true;
  state.daemon.pid = 123;
  state.suspension = { kind: 'usage', at: '2025-01-01 12:00', reason: 'seuil de consommation atteint' };
  await route.fulfill({ response, json: state });
});
await page.reload();
await page.waitForFunction(() => document.querySelector('#claude-state')?.textContent.includes('Claude désactivé'));
check('daemon and Claude have separate status pills', (await page.locator('#daemon-state').textContent()).includes('Démon en route')
  && (await page.locator('#claude-state').textContent()) === 'Claude désactivé');
check('quota suspension keeps daemon monitoring active', (await page.locator('#daemon-state').textContent()).includes('Démon en route')
  && (await page.locator('#daemon .daemon-toggle').getAttribute('aria-label')).includes('Arrêter le démon'));
check('quota suspension has an orange warning banner', (await page.locator('#suspension').getAttribute('class')).includes('warning')
  && (await page.locator('#suspension').textContent()).includes('temporairement converties en notifications')
  && (await page.locator('#suspension .btn').getAttribute('class')).includes('warning'));
await page.locator('#tabs button[data-tab="options"]').click();
check('language control is in options and power button is rightmost without a slash',
  (await page.locator('#options #language-select').count()) === 1
  && await page.locator('#daemon').evaluate((node) => node.lastElementChild?.classList.contains('daemon-toggle')
    && node.lastElementChild.previousElementSibling?.classList.contains('usage'))
  && !(await page.locator('#daemon .daemon-toggle .ico path').getAttribute('d') || '').includes('M5 5'));
await page.locator('#language-select').selectOption('en');
await page.waitForFunction(() => document.documentElement.lang === 'en' && document.querySelector('#daemon-state')?.textContent.includes('Daemon running'));
check('English language switches labels and localizes suspension date', (await page.locator('#tab-prs [data-i18n="filter.actionable"]').textContent()) === 'Needs attention'
  && (await page.locator('#suspension').textContent()).includes('Claude has been suspended since Jan 1, 2025'));
check('English language translates settings panels and options', (await page.locator('#options').textContent()).includes('Token savings')
  && (await page.locator('#options').textContent()).includes('Pause Claude when the plan quota is nearly exhausted'));
await page.locator('#tabs button[data-tab="prs"]').click();
await page.locator('#tabs button[data-tab="options"]').click();
await page.locator('#language-select').selectOption('fr');
await page.waitForFunction(() => document.documentElement.lang === 'fr' && document.querySelector('#daemon-state')?.textContent.includes('Démon en route'));
await page.locator('#tabs button[data-tab="prs"]').click();
const headerHeight = await page.locator('header.top').evaluate((node) => node.getBoundingClientRect().height);
for (const tab of ['log', 'options']) {
  await page.locator(`#tabs button[data-tab="${tab}"]`).click();
  check(`header keeps its height on the ${tab} tab`, Math.abs(await page.locator('header.top').evaluate((node) => node.getBoundingClientRect().height) - headerHeight) < 1);
}
await page.locator('#options .subtabs button[data-panel="system"]').click();
const stickyTabs = page.locator('#options .subtabs');
await page.locator('main').evaluate((node) => { node.scrollTop = node.scrollHeight; });
const optionsNav = await stickyTabs.evaluate((node) => {
  const main = document.querySelector('main');
  const button = node.querySelector('button[aria-selected="true"]');
  const rect = button.getBoundingClientRect();
  const topElement = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
  const mainTab = document.querySelector('#tabs button[data-tab="options"]');
  const mainButtonStyle = getComputedStyle(mainTab);
  const subButtonStyle = getComputedStyle(button);
  const markerStyle = getComputedStyle(mainTab, '::after');
  const styles = getComputedStyle(node);
  return {
    sticky: styles.position === 'sticky',
    noRepeatedTitle: !node.textContent.includes('Options'),
    parentMarker: markerStyle.content !== 'none' && markerStyle.width !== '0px' && markerStyle.height !== '0px'
      && markerStyle.clipPath !== 'none' && markerStyle.backgroundColor !== 'rgba(0, 0, 0, 0)',
    markerCentered: Math.abs(parseFloat(markerStyle.left) - mainTab.clientWidth / 2) < 1,
    noParentFrame: mainButtonStyle.borderTopStyle === 'none'
      && mainButtonStyle.borderLeftStyle === 'none'
      && mainButtonStyle.borderRightStyle === 'none',
    noSubmenuFrame: styles.borderTopStyle === 'none' && styles.borderLeftStyle === 'none'
      && styles.borderRightStyle === 'none' && getComputedStyle(node, '::before').content === 'none',
    matchingTabStyle: subButtonStyle.padding === mainButtonStyle.padding,
    staysAtTop: main.scrollHeight <= main.clientHeight
      || Math.abs(node.getBoundingClientRect().top - main.getBoundingClientRect().top) < 2,
    tabClickable: topElement === button || button.contains(topElement),
  };
});
check('options subtabs stay sticky and connect cleanly to the parent tab',
  Object.values(optionsNav).every(Boolean), JSON.stringify(optionsNav));
await page.locator('#tabs button[data-tab="prs"]').click();
await page.unroute('**/api/state*');
await page.reload();
await page.waitForSelector('.card');
check('badge counts the PRs needing a look', (await page.locator('#count').textContent()) === '3');
check('tab title carries the count', (await page.title()) === '(3) PR Watch');

console.log('== injection');
check('HTML in a title is text, not markup', (await cardOf(1).locator('.card-title img').count()) === 0 && (await cardOf(1).locator('.card-title').textContent()).includes('<img src=x'));
check('HTML in the attention message is text', (await cardOf(3).locator('.banner b').count()) === 0);
check('no script ran', (await page.evaluate(() => window.__xss)) === undefined);
check('the address of the window does not change (the browser needs it to find the window again)', page.url() === URL);

console.log('== safe actions');
await cardOf(1).getByRole('button', { name: 'Mettre en pause' }).click();
await page.waitForFunction(() => document.querySelectorAll('.card')[0].textContent.includes('Reprendre'));
check('pause', entryOf(1).state === 'paused');
await cardOf(1).getByRole('button', { name: 'Reprendre' }).click();
await page.waitForFunction(() => document.querySelectorAll('.card')[0].textContent.includes('Mettre en pause'));
check('resume', entryOf(1).state === 'watching');
await cardOf(3).getByRole('button', { name: 'Marquer comme vu' }).click();
await page.waitForFunction(() => !document.querySelectorAll('.card')[2].querySelector('.banner'));
check('acknowledge clears the attention', !entryOf(3).needsAttention);
await cardOf(4).getByRole('button', { name: 'Remettre les essais à 0' }).click();
await page.waitForFunction(() => document.querySelectorAll('.card')[3].textContent.includes('0 / 5'));
check('reset attempts and resume', entryOf(4).attempts === 0 && entryOf(4).state === 'watching');
await cardOf(2).getByRole('button', { name: 'Forcer un réveil' }).click();
await page.locator('dialog[open] button', { hasText: 'Forcer' }).last().click();
await sleep(500);
check('force wake asks first, then flags the PR', entryOf(2).forceWake === true);
await cardOf(1).getByRole('button', { name: 'Dernier rapport' }).click();
await page.waitForSelector('.entry.focus[data-report]');
check('last report opens the journal of the PR on its latest report', (await page.locator('#tab-log').isVisible())
  && (await page.inputValue('#log-pr')) === 'fake-repo#1'
  && (await page.locator('.entry.focus[data-report]').textContent()).includes('claude exited 1'));
await page.selectOption('#log-pr', '');
await page.locator('#tabs button[data-tab="prs"]').click();

console.log('== a click on a notification');
await page.locator('#tabs button[data-tab="options"]').click();
fs.writeFileSync(`${info.home}/focus.json`, JSON.stringify({ key: 'fake-repo#5', at: Date.now() }));
await page.waitForSelector('.card.focus', { timeout: 8000 });
check('the pull request of the notification is shown, on its tab', (await page.locator('#tab-prs').isVisible()) && (await page.locator('.card.focus').getAttribute('data-key')) === 'fake-repo#5');
await sleep(4500);
check('the mark of the notification stays on that pull request', (await page.locator('.card.focus .chip.notified').count()) === 1);
await page.locator('.card.focus').click();
check('and goes away once the card is touched', (await page.locator('.card.focus').count()) === 0);

console.log('== questions of the reviewers');
check('the question is shown as text', (await cardOf(7).locator('.q-text').textContent()).includes('<b>pas du html</b>') && (await cardOf(7).locator('.q-text b').count()) === 0);
await cardOf(7).getByRole('button', { name: 'Autre' }).click();
await sleep(400);
check('"other" without an instruction is refused', entryOf(7).pendingQuestions.length === 1 && (await toastText()).includes('à la place'));
await cardOf(7).locator('.q-answer input').fill('garde la version actuelle');
await cardOf(7).getByRole('button', { name: 'Autre' }).click();
await sleep(500);
check('"other" with an instruction is kept for Claude', entryOf(7).pendingQuestions.length === 0 && entryOf(7).decisions[0].text.includes('garde la version actuelle'));

console.log('== push and force push');
check('nothing to push: no push nor force push button', (await cardOf(1).getByRole('button', { name: /^Pousser/ }).count()) === 0);
fs.appendFileSync(`${info.work}/a.txt`, 'two\n');
git(info.work, 'commit', '-qam', 'second');
await page.waitForFunction(() => document.querySelectorAll('.card')[0].textContent.includes('À pousser'), null, { timeout: 8000 });
await cardOf(1).getByRole('button', { name: 'Pousser', exact: true }).click();
check('push asks for confirmation', (await page.locator('dialog[open] h2').textContent()).includes('Pousser'));
await page.locator('dialog[open] button', { hasText: 'Pousser' }).last().click();
await page.waitForSelector('dialog[open] pre');
check('push result shown', (await page.locator('dialog[open] pre').textContent()).includes('git push origin feature/x'));
await page.keyboard.press('Escape');
check('remote has the commit', git(info.remote, 'rev-parse', 'feature/x') === git(info.work, 'rev-parse', 'HEAD'));

const other = `${ROOT}/other`;
execFileSync('git', ['clone', '-q', '-b', 'feature/x', info.remote, other]);
fs.appendFileSync(`${other}/b.txt`, 'someone else\n');
git(other, 'add', '.');
git(other, 'commit', '-qm', 'someone elses commit');
git(other, 'push', '-q', 'origin', 'HEAD:feature/x');
git(info.work, 'commit', '-q', '--amend', '-m', 'second, rewritten');
await page.waitForFunction(() => document.querySelectorAll('.card')[0].textContent.includes('À pousser'), null, { timeout: 8000 });
await cardOf(1).getByRole('button', { name: 'Pousser en force…' }).click();
check('force push warns about rewriting history', (await page.locator('dialog[open] .body').textContent()).includes('Réécrit l’historique'));
await page.locator('dialog[open] button', { hasText: 'Pousser en force' }).last().click();
await page.waitForSelector('dialog[open] pre');
const refused = await page.locator('dialog[open] pre').textContent();
await page.keyboard.press('Escape');
check('--force-with-lease refuses to overwrite a commit pushed by someone else', refused.includes('--force-with-lease') && /rejected|stale|refused/i.test(refused), refused.slice(0, 200));
check('the remote still has the other commit', git(info.remote, 'log', '-1', '--format=%s', 'feature/x') === 'someone elses commit');
git(info.work, 'fetch', '-q', 'origin');
await cardOf(1).getByRole('button', { name: 'Pousser en force…' }).click();
await page.locator('dialog[open] button', { hasText: 'Pousser en force' }).last().click();
await page.waitForSelector('dialog[open] pre');
await page.keyboard.press('Escape');
check('after fetching, the force push goes through', git(info.remote, 'log', '-1', '--format=%s', 'feature/x') === 'second, rewritten');

console.log('== merge strategy not readable');
check('not approved, a failed pipeline: no merge button', (await cardOf(1).getByRole('button', { name: 'Merger la PR…' }).count()) === 0);
patchEntry(1, { mergeBlockers: ['1 tâche(s) ouverte(s)'] });
await cardOf(1).getByRole('button', { name: 'Merger la PR…' }).waitFor({ timeout: 8000 });
await cardOf(1).getByRole('button', { name: 'Merger la PR…' }).click();
check('rebase + fast-forward is proposed when Bitbucket\'s choice is unknown', (await page.locator('dialog[open] select').inputValue()) === 'rebase_fast_forward');
check('an info icon says the Bitbucket token must be changed', (await page.locator('dialog[open] .info').getAttribute('title')).includes('jeton Bitbucket'));
await page.keyboard.press('Escape');

console.log('== merge (refused: the repository does not exist)');
await cardOf(2).getByRole('button', { name: 'Merger la PR…' }).click();
check('merge dialog offers the strategies Bitbucket allows', (await page.locator('dialog[open] select option').count()) === 2);
check('the strategy set in Bitbucket is preselected', (await page.locator('dialog[open] select').inputValue()) === 'merge_commit' && (await page.locator('dialog[open]').textContent()).includes('configurée dans Bitbucket'));
await page.locator('dialog[open] button', { hasText: /^Merger$/ }).click();
await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some((n) => /refus|403|404|Bitbucket|mergée/i.test(n.textContent)), null, { timeout: 20000 });
const mergeToast = await page.evaluate(() => [...document.querySelectorAll('.toast')].map((n) => n.textContent).find((x) => /refus|403|404|Bitbucket|mergée/i.test(x)));
check('merge failure is reported', /refus|403|404|Bitbucket/i.test(mergeToast), mergeToast);
check('nothing was marked as merged', entryOf(2).state === 'watching');

console.log('== merge succeeded: the ticket is offered');
const ticketCalls = [];
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  const answer = (payload) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) });
  if (body.action === 'merge') { await sleep(900); return answer({ ok: true, message: 'PR mergée.' }); }  // slow, to see the loader  // Bitbucket is not reachable from here
  if (body.action === 'ticket-info') {
    ticketCalls.push(body.action);
    return answer({ ok: true, message: 'Ticket PROJ-16126 : En cours.', preselected: '31', suggestion: 'Changement : test.',
      ticket: { key: 'PROJ-16126', summary: 'Un ticket', status: 'En cours', impacts: '', impactsField: 'customfield_1', transitions: [{ id: '21', name: 'Terminer', to: 'Terminé' }, { id: '31', name: 'À tester', to: 'A tester' }] } });
  }
  if (body.action === 'ticket-draft') { await sleep(600); return answer({ ok: true, message: '', suggestion: 'Le filtre est corrigé.' }); }  // Claude takes seconds
  return route.continue();
});
await cardOf(2).getByRole('button', { name: 'Merger la PR…' }).click();
await page.locator('dialog[open] button', { hasText: /^Merger$/ }).click();
await page.waitForSelector('#busy[open]');
check('a slow treatment shows a loader', (await page.locator('#busy .label').textContent()).includes('Merge en cours'));
await page.keyboard.press('Escape');
check('Escape does not release the page', await page.locator('#busy[open]').count() === 1);
check('and a click elsewhere does nothing meanwhile', await page.locator('.tabs button[data-tab="log"], #tabs button[data-tab="log"]').first().click({ timeout: 400 }).then(() => false, () => true));
await page.waitForSelector('dialog[open] textarea[name="impacts"]');
check('the loader is gone once the treatment ends', await page.locator('#busy[open]').count() === 0);
check('after a merge the ticket dialog opens', ticketCalls.length === 1 && (await page.locator('dialog[open] h2').textContent()).includes('PROJ-16126'));
check('it preselects the state "A tester"', (await page.locator('dialog[open] select[name="transition"]').inputValue()) === '31');
check('and proposes the impacts description at once', (await page.locator('dialog[open] textarea[name="impacts"]').inputValue()).includes('Changement'));
check('a loader shows while Claude translates', (await page.locator('dialog[open] .spinner.small').count()) === 1 && (await page.locator('dialog[open] textarea.drafting').count()) === 1);
await page.waitForFunction(() => document.querySelector('dialog[open] textarea[name="impacts"]')?.value.includes('Le filtre est corrigé.'));
check('and it goes away once the text is there', (await page.locator('dialog[open] .spinner.small').count()) === 0);
check('then swaps in the French text written by Claude', true);
await page.locator('dialog[open] button', { hasText: 'Annuler' }).click();
await page.unroute('**/api/action');

console.log('== the ticket: open it, or change its state');
const ticketLinks = [];
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  const answer = (payload) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) });
  if (body.action === 'open-link') { ticketLinks.push(body.url); return answer({ ok: true, message: '' }); }
  if (body.action === 'ticket-info') return answer({ ok: false, message: 'Jira injoignable : test' });
  return route.continue();
});
await cardOf(2).getByRole('button', { name: /^Ouvrir PROJ-16126/ }).click();
await sleep(400);
check('a button opens the ticket in Jira at once', ticketLinks.length === 1 && ticketLinks[0].endsWith('/browse/PROJ-16126'), JSON.stringify(ticketLinks));
await cardOf(2).getByRole('button', { name: 'État du ticket…' }).click();
await page.waitForSelector('dialog[open]');
check('when Jira does not answer the dialog says why', (await page.locator('dialog[open]').textContent()).includes('Jira injoignable'));
await page.locator('dialog[open] button', { hasText: 'Ouvrir le ticket dans Jira' }).click();
await sleep(400);
check('and still offers the ticket itself', ticketLinks.length === 2);
await page.unroute('**/api/action');

console.log('== unwatch');
check('a finished PR offers no push, merge or wake', (await cardOf(6).getByRole('button', { name: /Pousser|Merger|Forcer/ }).count()) === 0);
await cardOf(6).getByRole('button', { name: 'Retirer de la liste' }).click();
await page.locator('dialog[open] button', { hasText: /^Retirer$/ }).click();
await page.waitForFunction(() => document.querySelectorAll('.card').length === 6);
check('PR removed from the registry', registry().length === 6 && !entryOf(6));

console.log('== journal');
await page.locator('#tabs button[data-tab="log"]').click();
await page.waitForSelector('.entry');
check('entries drawn with bullets', (await page.locator('.entry .bullet').count()) >= 8);
check('attention and error entries coloured', (await page.locator('.entry.k-attention').count()) === 1 && (await page.locator('.entry.k-error').count()) >= 2);
check('repeated lines folded with a count', (await page.locator('.entry .times').allTextContents()).some((t) => t.includes('×2')));
check('a report is drawn as bullets, bold and code', (await page.locator('.details .bulleted .mark').count()) >= 2 && (await page.locator('.details .bold').count()) >= 1 && (await page.locator('.details code').count()) >= 1);
check('verdict line coloured', (await page.locator('.details .verdict-ATTENTION').count()) === 1);
check('a script in the journal stayed text', (await page.locator('.log script').count()) === 0 && (await page.evaluate(() => window.__xss)) === undefined);
check('day separators', (await page.locator('.day').count()) >= 2);
await page.selectOption('#log-mode', 'important');
await page.waitForFunction(() => !document.querySelector('.entry.k-muted'));
check('filter keeps notifications and errors', (await page.locator('.entry.k-notify').count()) === 1);
await page.selectOption('#log-mode', 'all');
await page.fill('#log-search', 'mergée');
await page.waitForFunction(() => document.querySelectorAll('.entry').length === 1);
check('search narrows the journal', (await page.locator('.entry').textContent()).includes('prête à être mergée'));
await page.fill('#log-search', '');
await page.waitForFunction(() => document.querySelectorAll('.entry').length > 3);

console.log('== options');
await page.locator('#tabs button[data-tab="options"]').click();
await page.locator('#options .subtabs button', { hasText: 'Économie de jetons' }).click();
const config = () => fs.existsSync(`${info.home}/config.json`) ? JSON.parse(fs.readFileSync(`${info.home}/config.json`, 'utf8')) : {};
const scheduleBeforeTimeEdit = config().schedule || { enabled: false, days: [0, 1, 2, 3, 4], start: '09:00', end: '18:00' };
const editedStart = scheduleBeforeTimeEdit.start === '08:00' ? '07:00' : '08:00';
const scheduleStart = page.locator('input[name="schedule-start"]');
await scheduleStart.fill(editedStart);
await sleep(300);
check('schedule time remains focused and unsaved while editing',
  await scheduleStart.evaluate((input) => input === document.activeElement) && config().schedule?.start !== editedStart);
await page.locator('#options h2.section', { hasText: 'Horaires' }).click();
await sleep(500);
check('schedule time is saved after leaving the field', config().schedule?.start === editedStart);
await page.locator('#options .subtabs button', { hasText: 'Général' }).click();
await page.locator('input[name="pollSeconds"]').fill('120');
await page.locator('input[name="pollSeconds"]').dispatchEvent('change');
await sleep(500);
check('an option is saved', config().pollSeconds === 120);
await page.locator('input[name="autoPush"]').uncheck();
await sleep(500);
check('a switch is saved', config().autoPush === false);


console.log('== customisation');
await page.locator('#tabs button[data-tab="prs"]').click();
await page.waitForSelector('.card');
await cardOf(1).getByRole('button', { name: 'Réglages…' }).click();
check('the PR dialog lists a choice per kind of event', (await page.locator('dialog[open] select').count()) === 9);  // push, automatic merge, then one per kind of event
await page.locator('dialog[open] select').nth(0).selectOption('no');
await page.locator('dialog[open] select').nth(3).selectOption('notify');
await page.locator('dialog[open] button', { hasText: 'Enregistrer' }).click();
await sleep(600);
check('the choices of the PR are stored', JSON.stringify(entryOf(1).settings) === JSON.stringify({ autoPush: false, handling: { pipeline: null, comment: 'notify', 'review-bot': null, sonar: null, infra: null, ready: null } }) || (entryOf(1).settings.autoPush === false && entryOf(1).settings.handling.comment === 'notify'));
check('the card says what this PR does differently', (await cardOf(1).textContent()).includes('poussée automatique : non') && (await cardOf(1).textContent()).includes('commentaires des relecteurs : me notifier seulement'));
await page.locator('#tabs button[data-tab="options"]').click();
check('the options are split in several panels', (await page.locator('#options .subtabs button').count()) === 7 && (await page.locator('#options .panel:visible').count()) === 1);
await page.locator('#options .subtabs button', { hasText: 'Événements' }).click();
check('a panel only shows its own options', (await page.locator('input[name="pollSeconds"]').isVisible()) === false);
await page.locator('select[name="handling-sonar"]').selectOption('notify');
await sleep(600);
check('a global choice is stored', config().handling.sonar === 'notify');
await page.locator('#tabs button[data-tab="prs"]').click();
await cardOf(2).getByRole('button', { name: 'Réglages…' }).click();
check('"Par défaut" shows the global choice', (await page.locator('dialog[open] select').nth(6).locator('option').first().textContent()).includes('Me notifier seulement'));
await page.keyboard.press('Escape');

console.log('== the team settings');
await page.locator('#tabs button[data-tab="options"]').click();
await page.locator('#options .subtabs button', { hasText: 'Équipe' }).click();
check('the team panel shows the stored settings', (await page.locator('input[name="bitbucketWorkspace"]').inputValue()) === 'acme' && (await page.locator('input[name="jiraSite"]').inputValue()) === 'https://acme.atlassian.net');
check('every team field says what it expects, with an example', (await page.locator('#options .panel:visible .hint.example').count()) >= 7);
await page.locator('input[name="sonarPattern"]').fill('sonar|quality');
await page.locator('input[name="sonarPattern"]').blur();
await sleep(700);
check('a team setting is stored', config().sonarPattern === 'sonar|quality');
await page.locator('input[name="jiraSite"]').fill('http://insecure.example');
await page.locator('input[name="jiraSite"]').blur();
await sleep(1200);
check('an insecure Jira address is refused, and the stored one comes back', config().jiraSite === 'https://acme.atlassian.net' && (await page.locator('input[name="jiraSite"]').inputValue()) === 'https://acme.atlassian.net');
await page.locator('textarea[name="infraPatterns"]').fill('ECONNRESET\nBad Gateway');
await page.locator('textarea[name="infraPatterns"]').blur();
await sleep(700);
check('a list is one value per line', JSON.stringify(config().infraPatterns) === JSON.stringify(['ECONNRESET', 'Bad Gateway']));
check('the profile row offers a file', (await page.getByRole('button', { name: 'Charger un fichier…' }).count()) === 1);
await page.locator('#tabs button[data-tab="prs"]').click();

console.log('== the state a ticket moves to, by type');
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  if (body.action !== 'jira-metadata') return route.continue();
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: '', projects: ['RP'], types: ['Bug', 'Story', 'Tâche'],
    statuses: { Bug: ['A faire', 'Corrigé'], Story: ['A faire', 'Await deployment'], 'Tâche': ['A faire', 'Close'] }, allStatuses: ['A faire', 'Await deployment', 'Close', 'Corrigé'] }) });
});
await page.locator('#tabs button[data-tab="options"]').click();
await page.reload();
await page.waitForSelector('.card');
await page.locator('#tabs button[data-tab="options"]').click();
await page.locator('#options .subtabs button', { hasText: 'Ticket Jira' }).click();
await page.waitForSelector('.ticket-row select');
check('the types of ticket are listed, with no default state', (await page.locator('.ticket-row').count()) >= 1 && (await page.locator('input[aria-label="État par défaut"]').count()) === 0);
await page.getByRole('button', { name: 'Ajouter un type' }).click();
const newRow = () => page.locator('.ticket-row').last();
check('a new row can not choose a state before its type', await newRow().locator('select').nth(1).isDisabled());
await newRow().locator('select').first().selectOption('Story');
check('the states offered are the ones of the type chosen', JSON.stringify(await newRow().locator('select').nth(1).locator('option').allTextContents()) === JSON.stringify(['— état visé —', 'A faire', 'Await deployment']));
await newRow().locator('select').first().selectOption('Bug');
check('and change with the type', JSON.stringify(await newRow().locator('select').nth(1).locator('option').allTextContents()) === JSON.stringify(['— état visé —', 'A faire', 'Corrigé']));
await sleep(500);
check('a half filled row stays while it is being chosen', (await newRow().locator('select').first().inputValue()) === 'Bug');
await newRow().locator('select').nth(1).selectOption('Corrigé');
await sleep(600);
check('and is saved once complete', config().ticketTransitions.Bug === 'Corrigé' && !('default' in config().ticketTransitions), JSON.stringify(config().ticketTransitions));
await page.unroute('**/api/action');
await page.locator('#tabs button[data-tab="prs"]').click();

console.log('== add a pull request: the local clone');
await page.getByRole('button', { name: 'Ajouter une PR' }).click();
await page.fill('input[name="reference"]', 'acme/my-repo/pull-requests/12');
await page.getByRole('button', { name: 'Rechercher' }).click();
const cloneField = page.locator('input[name="workDir"]');
await cloneField.waitFor();
check('the clone that has the branch of the PR is filled in, though another carries the name of the repository', (await cloneField.inputValue()) === `${info.clones}/my-repo-bis`, await cloneField.inputValue());
check('the other clone is offered under the field', JSON.stringify(await page.locator('#dialog button[title]').allTextContents()) === JSON.stringify(['my-repo']));
check('a clone of another repository is not offered', !(await page.locator('#dialog').textContent()).includes('another'));
const sessionsAsked = page.waitForRequest((request) => request.postData()?.includes('claude-sessions'));
await page.locator('#dialog button[title]').click();
check('a click on it fills the field', (await cloneField.inputValue()) === `${info.clones}/my-repo`);
check('and loads the sessions of that folder', JSON.parse((await sessionsAsked).postData()).workDir === `${info.clones}/my-repo`);
git(`${info.clones}/my-repo-bis`, 'checkout', '-q', '-b', 'elsewhere');
await page.getByRole('button', { name: 'Rechercher' }).click();
await page.waitForFunction((expected) => document.querySelector('input[name="workDir"]')?.value === expected, `${info.clones}/my-repo`);
check('with no clone on the branch any more, the one named after the repository comes first again', (await page.locator('#dialog button[title]').allTextContents()).join() === 'my-repo-bis');
await page.keyboard.press('Escape');
await page.locator('#dialog').waitFor({ state: 'hidden' });

console.log('== protection');
const post = (headers, body, path = '/api/action') => new Promise((resolve) => {
  const data = JSON.stringify(body);
  const req = http.request({ host: '127.0.0.1', port: info.port, path, method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), ...headers } }, (res) => {
    let out = ''; res.on('data', (c) => { out += c; }); res.on('end', () => resolve({ status: res.statusCode, body: out }));
  });
  req.write(data); req.end();
});
check('no token: refused', (await post({}, { action: 'wake', repo: 'fake-repo', pr: 1 })).status === 403);
check('another web page (Origin) with the token: refused', (await post({ 'X-Token': info.token, Origin: 'http://evil.example' }, { action: 'wake', repo: 'fake-repo', pr: 1 })).status === 403);
check('rebinding (Host) with the token: refused', (await post({ 'X-Token': info.token, Host: 'evil.example' }, { action: 'wake', repo: 'fake-repo', pr: 1 })).status === 403);
check('destructive action without confirmation: refused', JSON.parse((await post({ 'X-Token': info.token }, { action: 'unwatch', repo: 'fake-repo', pr: 5 })).body).message === 'Action non confirmée.');
check('PR 5 is still watched', !!entryOf(5));
check('unknown option refused', JSON.parse((await post({ 'X-Token': info.token }, { action: 'options', values: { claudeBinary: '/bin/sh' } })).body).ok === false);
check('out-of-range option refused', JSON.parse((await post({ 'X-Token': info.token }, { action: 'options', values: { pollSeconds: 1 } })).body).ok === false);
check('a non-object body is refused with 400', (await post({ 'X-Token': info.token }, 'nope')).status === 400);
check('the server still answers after refused requests on the same connection', JSON.parse((await post({ 'X-Token': info.token }, { action: 'nope' })).body).message === 'Action inconnue.');
check('config.json untouched by refused options', config().pollSeconds === 120 && !('claudeBinary' in config()));

console.log('== screenshots');
await page.locator('#tabs button[data-tab="prs"]').click();
console.log('== the bar of the list');
const total = await page.locator('#prs .card').count();
await page.getByRole('button', { name: 'Tout replier' }).click();
check('collapse all', (await page.locator('#prs .card.expanded').count()) === 0);
await page.getByRole('button', { name: 'Tout développer' }).click();
check('expand all', (await page.locator('#prs .card.expanded').count()) === total);
await page.fill('#pr-filter', 'zzzz-no-match');
check('the filter hides what does not match', (await page.locator('#prs .card:visible').count()) === 0 && (await page.locator('#pr-count').textContent()).includes('Aucune'));
await page.fill('#pr-filter', '');
check('an empty filter shows everything again', (await page.locator('#prs .card:visible').count()) === total);
await page.check('#pr-actionable');
const actionable = await page.locator('#prs .card:visible').count();
check('"à traiter" keeps the pull requests that need an action', actionable > 0 && actionable < total, `${actionable}/${total}`);
await page.uncheck('#pr-actionable');
const barRefreshes = [];
await page.route('**/api/action', async (route) => {
  const body = JSON.parse(route.request().postData() || '{}');
  if (body.action !== 'refresh') return route.continue();
  barRefreshes.push(body.action);
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, message: 'Actualisation demandée.' }) });
});
await page.getByRole('button', { name: 'Actualiser' }).click();
await sleep(400);
check('the refresh button asks for a refresh', barRefreshes.length === 1);
await context.grantPermissions(['clipboard-read', 'clipboard-write']);
await page.locator('.toast').last().hover();
await page.locator('.toast .toast-copy').last().click();
await sleep(200);
check('the copy button of a toast puts its message in the clipboard', (await page.evaluate(() => navigator.clipboard.readText())) === 'Actualisation demandée.');
await page.unroute('**/api/action');

await page.waitForSelector('.card');
await page.screenshot({ path: `${ROOT}/prs-light.png`, fullPage: true });
await page.emulateMedia({ colorScheme: 'dark' });
await page.screenshot({ path: `${ROOT}/prs-dark.png`, fullPage: true });
await page.locator('#tabs button[data-tab="log"]').click();
await page.waitForSelector('.entry');
await page.screenshot({ path: `${ROOT}/log-dark.png`, fullPage: true });

check('no console or page error', problems.length === 0, problems.join(' / '));
await browser.close();
try { process.kill(info.pid); } catch (error) { /* already gone */ }  // the dashboard server of the test must not outlive it
console.log(failures ? `\n${failures} FAILURE(S)` : '\nall good');
process.exit(failures ? 1 : 0);
