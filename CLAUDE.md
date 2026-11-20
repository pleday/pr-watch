# PR Watch

*English version: [below](#english).*

Surveille les pull requests Bitbucket et réveille la session Claude Code qui les a ouvertes (voir `README.md`).

## Conventions

- Bibliothèque standard seulement : aucune dépendance Python à ajouter. GTK 3 (`gi`) n'est importé que par `prwatch/tray/app.py`, sur Linux.
- Code, identifiants et commentaires en anglais ; textes affichés à l'utilisateur en français. Un commentaire explique une raison que le code ne montre pas.
- Tout ce qui dépend du système passe par le paquet `prwatch/system/` (verrous, processus, notifications, liens, terminal) : ne pas appeler `/proc`, `fcntl`, `notify-send` ou `xdg-open` ailleurs.
- Les actions que proposent les interfaces vivent dans le paquet `prwatch/actions/` ; les écrans (web et GTK) ne font que les appeler.
- Le contenu venant de Bitbucket ou du journal est toujours affiché comme texte (jamais comme HTML).
- Rien de propre à une équipe dans le code (espace Bitbucket, site Jira, champ des impacts, Sonar, revue automatique, états des tickets…) : ce sont des options de `prwatch/core/config.py`, vides ou neutres par défaut, saisies dans Options › Équipe ou chargées depuis un profil (`profiles/<équipe>.json`, hors du paquet, `pr-watch profile load`). Un profil ne règle que ces clés, jamais `claudeArgs`. Tests, exemples et documentation : données neutres (`acme`, `PROJ-123`, `my-repo`).
- Tout appel à Claude que lance l'application (réveil, texte des impacts du ticket, nouvelle session) vérifie d'abord `pwstore.suspension()` : suspendu, il n'a pas lieu. Seule la mesure du forfait demandée à la main (« Actualiser ») y échappe, puisqu'elle sert à savoir si la suspension peut être levée.
- L'icône de barre (`prwatch/tray/`) est le seul code GTK : son contenu vient de `tray/summary.py`, qui s'appuie sur `actions/status.py` (mêmes PR, mêmes libellés que la fenêtre). Seul ce qui agit est cliquable.
- Tests : `make test` (unittest, sans navigateur) et `make e2e` (tableau de bord dans un vrai Chrome) ; `PR_WATCH_HOME` déplace le répertoire de données, les tests l'isolent.
- Documentation : `README.md`, `CHANGELOG.md` (section « Non publié »), `CLAUDE.md` et les skills de `.claude/skills/` suivent le code ; chaque fichier en français a son pendant en anglais dans le même fichier.
- Commits : Conventional Commits (`feat(web): …`), sujet à l'impératif, avec la référence du ticket exigée par le hook du dépôt (`[Ticket: XXX-123]`).

---

## English

Watches Bitbucket pull requests and wakes the Claude Code session that opened them (see `README.md`).

### Conventions

- Standard library only: no Python dependency to add. GTK 3 (`gi`) is imported only by `prwatch/tray/app.py`, on Linux.
- Code, identifiers and comments in English; text displayed to the user in French. A comment explains a reason that the code does not show.
- Everything that depends on the system goes through the `prwatch/system/` package (locks, processes, notifications, links, terminal): do not call `/proc`, `fcntl`, `notify-send` or `xdg-open` elsewhere.
- The actions offered by the interfaces live in the `prwatch/actions/` package; the screens (web and GTK) only call them.
- Content coming from Bitbucket or the log is always displayed as text (never as HTML).
- Nothing team-specific in the code (Bitbucket workspace, Jira site, impacts field, Sonar, automatic review, issue statuses…): these are options of `prwatch/core/config.py`, empty or neutral by default, entered in Settings › Team or loaded from a profile (`profiles/<team>.json`, outside the package, `pr-watch profile load`). A profile sets only these keys, never `claudeArgs`. Tests, examples and documentation: neutral data (`acme`, `PROJ-123`, `my-repo`).
- Every call to Claude launched by the application (wake-up, issue impacts text, new session) first checks `pwstore.suspension()`: when suspended, it does not happen. Only the plan measurement requested by hand ("Refresh") escapes it, since it is used to know whether the suspension can be lifted.
- The tray icon (`prwatch/tray/`) is the only GTK code: its content comes from `tray/summary.py`, which relies on `actions/status.py` (same PRs, same labels as the window). Only what acts is clickable.
- Tests: `make test` (unittest, no browser) and `make e2e` (dashboard in a real Chrome); `PR_WATCH_HOME` moves the data directory, the tests isolate it.
- Documentation: `README.md`, `CHANGELOG.md` ("Unreleased" section), `CLAUDE.md` and the skills in `.claude/skills/` follow the code; each French file has its English counterpart in the same file.
- Commits: Conventional Commits (`feat(web): …`), imperative subject, with the issue reference required by the repository hook (`[Ticket: XXX-123]`).
