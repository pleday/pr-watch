---
name: pr-watch-diagnose
description: Diagnostique PR Watch quand quelque chose ne marche pas — le démon ne réveille pas la session, une PR n'apparaît pas, erreurs 403 de Bitbucket, autorisations ou jetons manquants, fenêtre ou icône de barre qui ne s'ouvre pas, doublons de fenêtre, `pr-watch` introuvable. Lit les journaux et l'état, propose la correction. Troubleshoot PR Watch.
---

# Diagnostiquer PR Watch

*English version: [below](#troubleshoot-pr-watch-english).*

Procède du plus simple au plus fin, et dis à l'utilisateur ce que tu constates avant de corriger. Ne tue jamais un processus avec un motif `pkill -f` ou `pgrep -f` qui apparaît dans ta propre commande (tu tuerais ton shell) : repère le PID avec `ps`, puis `kill <pid>`.

## 1. État général

```sh
pr-watch doctor          # jetons, portées, commande claude : ✔ / ✖ / ○ et le guide de ce qui manque
pr-watch list            # PR surveillées : état, essais, session
pr-watch once --dry-run  # un tour de surveillance, imprime ce qui serait envoyé à Claude sans rien envoyer
```

Les journaux sont dans `~/.claude/pr-watch/` : `daemon.log` (le démon : démarrages, appels refusés, réveils, notifications), `web.log` (serveur du tableau de bord), `tray.log` (icône de barre). Lis la fin de `daemon.log` d'abord (`tail -n 60`).

## 2. Symptômes fréquents

| Symptôme | Cause probable | Correction |
|---|---|---|
| Rien n'est surveillé, bandeau « Première configuration » | l'espace Bitbucket n'est pas renseigné (`pr-watch once --dry-run` le dit) | `pr-watch profile load profiles/<équipe>.json`, ou Options › Équipe ; `pr-watch register` refuse aussi tant qu'il est vide |
| `pr-watch` : commande introuvable | pas installée | `make link` (ou `pipx install .`), vérifier `~/.local/bin` dans le `PATH` |
| `permission missing` dans le journal, bandeau rouge | jeton ou portée absents | suivre le guide de `pr-watch doctor` ; le jeton doit être dans l'environnement du démon, puis `pr-watch stop` et `pr-watch ensure` |
| `API 401` ou `403` dans le journal | identifiants refusés, ou portée manquante | `pr-watch doctor` nomme les portées manquantes ; un jeton existant ne se modifie pas, il faut en créer un nouveau avec les portées |
| Le démon tourne mais la session n'est jamais réveillée | session en cours d'utilisation (transcript modifié depuis moins de 90 s) ; PR en pause ; essais épuisés (`gave-up`, 5 par défaut) ; type d'événement réglé sur `notify` ou `ignore` ; commentaire qui est une question (attend la décision de l'utilisateur) | regarder la carte de la PR dans `pr-watch ui` ; « Remettre les essais à 0 », « Reprendre », ou ses réglages ; « Forcer un réveil » pour tester |
| Une PR n'apparaît pas | jamais enregistrée, ou fusionnée / refusée (retirée au bout de `removeDoneAfterMinutes`) | `pr-watch register --pr N` depuis le clone (skill `pr-watch-register`) |
| Ajouter une PR est refusé | seules les PR dont on est l'auteur sont acceptées ; le compte n'est pas reconnu | `pr-watch doctor` (ligne « Votre identité ») |
| « Fonction masquée » dans le tableau de bord | une autorisation facultative a été refusée | Options → Autorisations → guide, puis « Revérifier » |
| La fenêtre ne s'ouvre pas | pas de navigateur Chromium, ou serveur arrêté | `pr-watch ui` affiche l'adresse ; lire `web.log` ; `pr-watch stop` puis `pr-watch ui` |
| Une seconde fenêtre s'ouvre | la nouvelle fenêtre remplace l'ancienne : elle doit la fermer d'elle-même | si deux restent, fermer l'ancienne à la main ; vérifier la version du code (`git log -1`) |
| L'icône de barre est absente ou rouge | GTK 3 absent, ou démon arrêté | `pr-watch tray` donne la raison (`sudo apt install python3-gi gir1.2-gtk-3.0`) ; rouge = démon arrêté : `pr-watch ensure` |
| Le clic sur l'icône n'ouvre aucun menu, ou le menu est vide | l'extension AppIndicator de GNOME est absente ou désactivée, ou l'icône tourne avec un ancien code | `gnome-extensions list --enabled` (ubuntu-appindicators) ; relancer l'icône (`pr-watch tray`) |
| Pas de focus à l'ouverture de la fenêtre (Linux Wayland) | le shell n'a pas transmis de jeton d'activation | clic du milieu sur l'icône, ou clic sur une notification ; Wayland interdit sinon de ramener une fenêtre d'une autre application |
| Les actions ne tiennent pas compte d'une modification du code | processus lancé avec l'ancien code | le démon et le serveur se relancent seuls ; sinon `pr-watch stop` puis `pr-watch ui` ; l'icône de barre se relance à la main |

## 3. Redémarrer proprement

```sh
pr-watch stop      # arrête le démon
pr-watch ui        # relance le démon (s'il ne tourne pas), le serveur et ouvre la fenêtre
```

Les données (registre, réglages, journal, jeton local du tableau de bord) sont dans `~/.claude/pr-watch` (`PR_WATCH_HOME` pour en changer) : ne les supprime pas sans l'accord de l'utilisateur.

## 4. Si le démon ne se laisse pas expliquer

Lis `daemon.log` entre deux redémarrages, cherche `Traceback`, `permission missing`, `API 4xx`, `claude exited`. `once --dry-run` rejoue un tour de surveillance au premier plan : toute exception y apparaît directement. Pour un bug dans PR Watch lui-même, `make test` (tests unitaires) et `make e2e` (tableau de bord dans un vrai Chrome) disent si le code est sain.

---

# Troubleshoot PR Watch (English)

Proceed from the simplest to the finest, and tell the user what you observe before fixing. Never kill a process with a `pkill -f` or `pgrep -f` pattern that appears in your own command (you would kill your shell): find the PID with `ps`, then `kill <pid>`.

## 1. General state

```sh
pr-watch doctor          # tokens, scopes, claude command: ✔ / ✖ / ○ and the guide for what is missing
pr-watch list            # watched PRs: state, attempts, session
pr-watch once --dry-run  # one watch round, prints what would be sent to Claude without sending anything
```

The logs are in `~/.claude/pr-watch/`: `daemon.log` (the daemon: starts, refused calls, wake-ups, notifications), `web.log` (dashboard server), `tray.log` (tray icon). Read the end of `daemon.log` first (`tail -n 60`).

## 2. Common symptoms

| Symptom | Likely cause | Fix |
|---|---|---|
| Nothing is watched, "First setup" banner | the Bitbucket workspace is not set (`pr-watch once --dry-run` says so) | `pr-watch profile load profiles/<team>.json`, or Settings › Team; `pr-watch register` also refuses while it is empty |
| `pr-watch`: command not found | not installed | `make link` (or `pipx install .`), check `~/.local/bin` is in the `PATH` |
| `permission missing` in the log, red banner | token or scope missing | follow the `pr-watch doctor` guide; the token must be in the daemon's environment, then `pr-watch stop` and `pr-watch ensure` |
| `API 401` or `403` in the log | credentials refused, or scope missing | `pr-watch doctor` names the missing scopes; an existing token cannot be modified, a new one with the scopes must be created |
| The daemon runs but the session is never woken | session in use (transcript modified less than 90 s ago); PR paused; attempts exhausted (`gave-up`, 5 by default); event type set to `notify` or `ignore`; comment that is a question (waits for the user's decision) | look at the PR's card in `pr-watch ui`; "Reset attempts", "Resume", or its settings; "Force a wake-up" to test |
| A PR does not appear | never registered, or merged / declined (removed after `removeDoneAfterMinutes`) | `pr-watch register --pr N` from the clone (`pr-watch-register` skill) |
| Adding a PR is refused | only PRs one authored are accepted; the account is not recognized | `pr-watch doctor` ("Your identity" line) |
| "Feature hidden" in the dashboard | an optional permission was declined | Settings → Permissions → guide, then "Check again" |
| The window does not open | no Chromium browser, or server stopped | `pr-watch ui` prints the address; read `web.log`; `pr-watch stop` then `pr-watch ui` |
| A second window opens | the new window replaces the old one: it should close itself | if two remain, close the old one by hand; check the code version (`git log -1`) |
| The tray icon is missing or red | GTK 3 missing, or daemon stopped | `pr-watch tray` gives the reason (`sudo apt install python3-gi gir1.2-gtk-3.0`); red = daemon stopped: `pr-watch ensure` |
| Clicking the icon opens no menu, or the menu is empty | the GNOME AppIndicator extension is missing or disabled, or the icon runs with old code | `gnome-extensions list --enabled` (ubuntu-appindicators); restart the icon (`pr-watch tray`) |
| No focus when the window opens (Linux Wayland) | the shell did not pass an activation token | middle-click on the icon, or click a notification; Wayland otherwise forbids bringing another application's window forward |
| Actions do not reflect a code change | process started with the old code | the daemon and the server restart on their own; otherwise `pr-watch stop` then `pr-watch ui`; the tray icon is restarted by hand |

## 3. Restart cleanly

```sh
pr-watch stop      # stops the daemon
pr-watch ui        # restarts the daemon (if it is not running) and the server, and opens the window
```

The data (registry, settings, log, the dashboard's local token) is in `~/.claude/pr-watch` (`PR_WATCH_HOME` to change it): do not delete it without the user's consent.

## 4. If the daemon will not explain itself

Read `daemon.log` between two restarts, look for `Traceback`, `permission missing`, `API 4xx`, `claude exited`. `once --dry-run` replays a watch round in the foreground: any exception shows up there directly. For a bug in PR Watch itself, `make test` (unit tests) and `make e2e` (dashboard in a real Chrome) tell whether the code is healthy.
