---
name: pr-watch-setup
description: Installe et configure PR Watch (surveillance des pull requests Bitbucket) pour l'utilisateur — commande `pr-watch`, jetons Bitbucket et Jira, démon, tableau de bord, icône de barre. À utiliser quand l'utilisateur vient d'ajouter ce projet à son workspace, veut installer, démarrer, configurer ou vérifier PR Watch, ou dit que `pr-watch` est introuvable. Install and set up PR Watch.
---

# Installer et configurer PR Watch

*English version: [below](#install-and-set-up-pr-watch-english).*

PR Watch surveille les pull requests Bitbucket de l'utilisateur et réveille la session Claude Code qui les a ouvertes quand quelque chose demande une réaction (voir `README.md`). Ce skill amène l'utilisateur d'un clone du projet à une installation qui marche.

Règles : ne demande jamais à l'utilisateur de coller un jeton dans la conversation ; n'écris aucun jeton dans un fichier du dépôt, du registre ou de la configuration ; ne modifie pas ses fichiers de profil shell (`~/.bashrc`, `~/.profile`…) sans son accord explicite, montre-lui la ligne à ajouter ; n'installe aucun paquet système sans lui demander.

## 1. Prérequis

Vérifie, depuis la racine du projet : `python3 --version` (3.10 ou plus), `git --version`, `claude --version` (Claude Code). S'il en manque un, dis lequel et comment l'installer ; ne continue pas sans `python3` ni `claude`.

## 2. La commande `pr-watch`

Teste `pr-watch list`. Si la commande est introuvable :

- Sans pipx, depuis le clone : `make link` — crée `~/.local/bin/pr-watch` qui suit le code du clone (vérifie que `~/.local/bin` est dans le `PATH` ; sinon, dis à l'utilisateur d'ouvrir un nouveau terminal ou de l'ajouter).
- Avec pipx : `pipx install .` (ajoute `--system-site-packages` si l'utilisateur veut l'icône de barre GTK).

Contrôle ensuite `pr-watch list` (une liste vide est normale).

## 3. Le profil d'équipe

Rien n'est surveillé tant que l'espace Bitbucket n'est pas renseigné. Regarde `ls profiles/` dans le clone : si l'équipe de l'utilisateur y a un fichier, charge-le avec `pr-watch profile load profiles/<équipe>.json` (il règle l'espace Bitbucket, Jira, Sonar, la revue automatique…). Sinon, demande-lui l'espace Bitbucket (la première partie de `https://bitbucket.org/<espace>/<dépôt>`) et, s'il utilise Jira, l'adresse de son site : ils se saisissent dans le tableau de bord (Options › Équipe) ou dans `config.json` (`bitbucketWorkspace`, `jiraSite`). Vérifie avec `pr-watch once --dry-run`.

## 4. Les autorisations

Lance `pr-watch doctor`. Il vérifie la commande `claude`, les identifiants Bitbucket et leurs portées, le compte de l'utilisateur et le jeton Jira, et imprime un guide pas à pas pour chaque manque. Fais-le suivre à l'utilisateur :

- `BITBUCKET_EMAIL` (adresse du compte Atlassian) et `BITBUCKET_TOKEN` : jeton d'API **avec portées** (Bitbucket) : `read:pullrequest:bitbucket`, `write:pullrequest:bitbucket`, `read:pipeline:bitbucket`, `write:pipeline:bitbucket`, `read:repository:bitbucket`, `read:user:bitbucket`. Indispensables : lecture des pull requests et des pipelines.
- `ATLASSIAN_API_TOKEN` (facultatif) : jeton d'API sans portées, pour changer l'état du ticket Jira d'une PR.

Ces variables doivent être dans l'environnement du processus qui lance le démon : propose la ligne `export NOM=…` à ajouter à son profil (il met lui-même les valeurs), puis un nouveau terminal. Relance `pr-watch doctor` jusqu'à ce que tout ce qui est indispensable soit ✔ (le code de sortie est 0).

## 5. Démarrer

- `pr-watch ensure` démarre le démon (un seul tourne jamais) ; `pr-watch ui` ouvre le tableau de bord (Chrome, Edge, Brave ou Chromium en mode application, sinon le navigateur par défaut).
- Linux : `pr-watch install-desktop` inscrit PR Watch dans la liste des applications. L'icône de barre demande GTK 3 (`sudo apt install python3-gi gir1.2-gtk-3.0`, avec l'accord de l'utilisateur) puis `pr-watch tray` ; sans GTK tout le reste marche.
- macOS et Windows : `pr-watch install-launcher`.

## 6. Brancher le reste du workflow

Dis à l'utilisateur que, pour que ses futures PR soient surveillées, la session qui crée une PR doit lancer `pr-watch register --pr <numéro>` : c'est le skill `pr-watch-register`. Propose d'ajouter une phrase à sa mémoire ou à son `CLAUDE.md` (« après avoir créé une pull request Bitbucket, enregistre-la avec le skill pr-watch-register ») ; ne l'écris qu'avec son accord.

Termine par `pr-watch once --dry-run` (un tour de surveillance sans rien envoyer à Claude) et résume ce qui est en place. En cas de problème, passe au skill `pr-watch-diagnose`.

---

# Install and set up PR Watch (English)

PR Watch watches the user's Bitbucket pull requests and wakes the Claude Code session that opened them when something calls for a reaction (see `README.md`). This skill takes the user from a clone of the project to a working installation.

Rules: never ask the user to paste a token into the conversation; do not write any token into a file of the repository, the registry or the configuration; do not modify their shell profile files (`~/.bashrc`, `~/.profile`…) without their explicit consent, show them the line to add instead; do not install any system package without asking them.

## 1. Prerequisites

Check, from the project root: `python3 --version` (3.10 or later), `git --version`, `claude --version` (Claude Code). If one is missing, say which one and how to install it; do not continue without `python3` or `claude`.

## 2. The `pr-watch` command

Test `pr-watch list`. If the command is not found:

- Without pipx, from the clone: `make link` — creates `~/.local/bin/pr-watch`, which follows the clone's code (check that `~/.local/bin` is in the `PATH`; otherwise, tell the user to open a new terminal or to add it).
- With pipx: `pipx install .` (add `--system-site-packages` if the user wants the GTK tray icon).

Then check `pr-watch list` (an empty list is normal).

## 3. The team profile

Nothing is watched until the Bitbucket workspace is set. Look at `ls profiles/` in the clone: if the user's team has a file there, load it with `pr-watch profile load profiles/<team>.json` (it sets the Bitbucket workspace, Jira, Sonar, the automatic review…). Otherwise, ask them for the Bitbucket workspace (the first part of `https://bitbucket.org/<workspace>/<repository>`) and, if they use Jira, the address of their site: these are entered in the dashboard (Settings › Team) or in `config.json` (`bitbucketWorkspace`, `jiraSite`). Check with `pr-watch once --dry-run`.

## 4. The permissions

Run `pr-watch doctor`. It checks the `claude` command, the Bitbucket credentials and their scopes, the user's account and the Jira token, and prints a step-by-step guide for each missing item. Have the user follow it:

- `BITBUCKET_EMAIL` (Atlassian account address) and `BITBUCKET_TOKEN`: API token **with scopes** (Bitbucket): `read:pullrequest:bitbucket`, `write:pullrequest:bitbucket`, `read:pipeline:bitbucket`, `write:pipeline:bitbucket`, `read:repository:bitbucket`, `read:user:bitbucket`. Required: reading pull requests and pipelines.
- `ATLASSIAN_API_TOKEN` (optional): API token without scopes, to change the state of a PR's Jira ticket.

These variables must be in the environment of the process that starts the daemon: suggest the `export NAME=…` line to add to their profile (they fill in the values themselves), then a new terminal. Run `pr-watch doctor` again until everything required is ✔ (the exit code is 0).

## 5. Start

- `pr-watch ensure` starts the daemon (only one ever runs); `pr-watch ui` opens the dashboard (Chrome, Edge, Brave or Chromium in app mode, otherwise the default browser).
- Linux: `pr-watch install-desktop` registers PR Watch in the application list. The tray icon needs GTK 3 (`sudo apt install python3-gi gir1.2-gtk-3.0`, with the user's consent) then `pr-watch tray`; without GTK everything else works.
- macOS and Windows: `pr-watch install-launcher`.

## 6. Hook up the rest of the workflow

Tell the user that, for their future PRs to be watched, the session that creates a PR must run `pr-watch register --pr <number>`: this is the `pr-watch-register` skill. Offer to add a sentence to their memory or to their `CLAUDE.md` ("after creating a Bitbucket pull request, register it with the pr-watch-register skill"); only write it with their consent.

Finish with `pr-watch once --dry-run` (one watch round without sending anything to Claude) and summarize what is in place. If there is a problem, switch to the `pr-watch-diagnose` skill.
