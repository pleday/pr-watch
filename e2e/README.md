# Test de bout en bout du tableau de bord

*English version: [below](#end-to-end-test-of-the-dashboard-english).*

Lance le tableau de bord sur des données factices (répertoire de données propre, PR de chaque état, dépôt git local avec
remote nu) et le pilote dans un vrai Chrome avec Playwright : rendu, injection de HTML, actions, push et poussée en force
(`--force-with-lease`), merge refusé, journal, options, réglages, ajout d’une PR (clones locaux, Bitbucket simulé par `e2e/web_server.py`), protections du serveur, audit (accessibilité, clavier, contraste AA en clair et en sombre sur chaque onglet, panneau d’options et boîte de dialogue, écran de 360 px, rapport long dans le journal, avertissement du journal volumineux), captures clair et sombre
(dans `e2e/.work/`). Chrome tourne sans fenêtre (headless).

```sh
npm install --prefix e2e playwright-core        # une fois
CHROME_PATH=/usr/bin/google-chrome make e2e     # CHROME_PATH est facultatif (Playwright cherche Chrome sinon)
```

Variables : `PLAYWRIGHT_DIR` (dossier dont `node_modules` contient `playwright-core`, par défaut `e2e/`),
`E2E_SESSION` et `E2E_SESSION_TITLE` (une vraie session Claude, pour vérifier que son titre s'affiche).

Les tests unitaires et d'intégration (sans navigateur) sont dans `tests/` : `make test`.

---

# End-to-end test of the dashboard (English)

Starts the dashboard on fake data (dedicated data directory, a PR in each state, local git repository with a bare
remote) and drives it in a real Chrome with Playwright: rendering, HTML injection, actions, push and force push
(`--force-with-lease`), refused merge, log, options, settings, adding a PR (local clones, Bitbucket faked by `e2e/web_server.py`), server protections, audit (accessibility, keyboard, AA contrast in light and dark on every tab, options panel and dialog, 360 px screen, long report in the log, large-log warning), light and dark screenshots
(in `e2e/.work/`). Chrome runs without a window (headless).

```sh
npm install --prefix e2e playwright-core        # once
CHROME_PATH=/usr/bin/google-chrome make e2e     # CHROME_PATH is optional (otherwise Playwright looks for Chrome)
```

Variables: `PLAYWRIGHT_DIR` (folder whose `node_modules` contains `playwright-core`, `e2e/` by default),
`E2E_SESSION` and `E2E_SESSION_TITLE` (a real Claude session, to check that its title is displayed).

The unit and integration tests (without a browser) are in `tests/`: `make test`.
