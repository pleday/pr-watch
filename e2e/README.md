# Test de bout en bout du tableau de bord

*English version: [below](#end-to-end-test-of-the-dashboard-english).*

Lance le tableau de bord sur des données factices (répertoire de données propre, PR de chaque état, dépôt git local avec
remote nu) et le pilote dans un vrai Chrome avec Playwright : rendu, injection de HTML, actions, push et poussée en force
(`--force-with-lease`), merge refusé, journal, options, réglages, protections du serveur, captures clair et sombre
(dans `e2e/.work/`).

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
(`--force-with-lease`), refused merge, log, options, settings, server protections, light and dark screenshots
(in `e2e/.work/`).

```sh
npm install --prefix e2e playwright-core        # once
CHROME_PATH=/usr/bin/google-chrome make e2e     # CHROME_PATH is optional (otherwise Playwright looks for Chrome)
```

Variables: `PLAYWRIGHT_DIR` (folder whose `node_modules` contains `playwright-core`, `e2e/` by default),
`E2E_SESSION` and `E2E_SESSION_TITLE` (a real Claude session, to check that its title is displayed).

The unit and integration tests (without a browser) are in `tests/`: `make test`.
