---
name: pr-watch-register
description: Met une pull request Bitbucket sous la surveillance de PR Watch (`pr-watch register`) pour que cette session soit réveillée quand un pipeline échoue, qu'un relecteur commente, que la revue automatique ou Sonar signale quelque chose. À utiliser juste après avoir créé une pull request, ou quand l'utilisateur demande de surveiller / suivre / retirer une PR. Watch a pull request with PR Watch.
---

# Mettre une PR sous surveillance

*English version: [below](#put-a-pr-under-watch-english).*

Après la création d'une pull request, enregistre-la : le démon PR Watch réveillera **cette session** (`claude --resume`) quand quelque chose demande une réaction, et l'utilisateur sera notifié quand la PR est prête à merger.

## Enregistrer

Place-toi dans le clone de la PR, sur sa branche, et lance :

```sh
pr-watch register --pr <numéro>
```

La commande trouve seule le dépôt (l'adresse Bitbucket de `origin`), la branche courante, le clone (la racine git), la session (`CLAUDE_CODE_SESSION_ID`) et le dossier où elle a été démarrée. Si elle répond « cannot tell … », passe ce qui manque : `--repo`, `--branch`, `--work-dir`, `--session`, `--session-cwd` (voir `pr-watch register --help`).

Seules les pull requests dont l'utilisateur est l'auteur sont à enregistrer. La surveillance commence à cet instant : les commentaires et pipelines déjà présents ne sont pas des événements (`--since-pipeline N` pour en traiter à partir d'un numéro).

Ensuite :

1. Vérifie avec `pr-watch list` que la PR figure dans le registre, état `watching`.
2. Lance `pr-watch ensure` si le démon ne tourne pas (sans effet s'il tourne déjà).
3. Dis à l'utilisateur que la PR apparaît dans le tableau de bord (`pr-watch ui`) et que tu seras réveillé pour la traiter.

## Réglages d'une PR (facultatif)

À l'enregistrement ou plus tard, ils l'emportent sur les réglages globaux :

```sh
pr-watch register --pr 12 --auto-push no --handling comment=notify,sonar=ignore
pr-watch settings --repo <dépôt> --pr 12 --handling sonar=default   # default = suivre le global
pr-watch settings --repo <dépôt> --pr 12 --reset
```

Types d'événement : `pipeline`, `comment`, `question`, `review-bot`, `sonar`, `infra`, `ready` ; modes : `fix` (Claude corrige), `notify` (seulement prévenir), `ignore`, et `ask` pour `question`, `rerun` pour `infra` (voir « Personnaliser ce que Claude traite » dans le `README.md`). `--auto-push no` : tu t'arrêtes au commit et l'utilisateur pousse depuis le tableau de bord.

## Retirer

`pr-watch unregister --repo <dépôt> --pr <numéro>`, ou le bouton « Arrêter la surveillance » du tableau de bord. Une PR fusionnée ou refusée quitte la liste toute seule.

## Si ça échoue

`pr-watch doctor` dit ce qui manque (jetons, portées). Si la commande répond « Bitbucket workspace not set », l'espace Bitbucket n'est pas renseigné : `pr-watch profile load profiles/<équipe>.json` ou Options › Équipe (skill `pr-watch-setup`). Un autre souci : skill `pr-watch-diagnose`.

---

# Put a PR under watch (English)

After creating a pull request, register it: the PR Watch daemon will wake **this session** (`claude --resume`) when something calls for a reaction, and the user will be notified when the PR is ready to merge.

## Register

Go to the PR's clone, on its branch, and run:

```sh
pr-watch register --pr <number>
```

The command finds on its own the repository (the Bitbucket address of `origin`), the current branch, the clone (the git root), the session (`CLAUDE_CODE_SESSION_ID`) and the folder where it was started. If it answers "cannot tell …", pass what is missing: `--repo`, `--branch`, `--work-dir`, `--session`, `--session-cwd` (see `pr-watch register --help`).

Only pull requests the user authored are to be registered. Watching starts at that moment: comments and pipelines already present are not events (`--since-pipeline N` to process those from a given number).

Then:

1. Check with `pr-watch list` that the PR is in the registry, state `watching`.
2. Run `pr-watch ensure` if the daemon is not running (no effect if it already is).
3. Tell the user that the PR appears in the dashboard (`pr-watch ui`) and that you will be woken to handle it.

## Per-PR settings (optional)

At registration or later, they take precedence over the global settings:

```sh
pr-watch register --pr 12 --auto-push no --handling comment=notify,sonar=ignore
pr-watch settings --repo <repository> --pr 12 --handling sonar=default   # default = follow the global setting
pr-watch settings --repo <repository> --pr 12 --reset
```

Event types: `pipeline`, `comment`, `question`, `review-bot`, `sonar`, `infra`, `ready`; modes: `fix` (Claude fixes), `notify` (only notify), `ignore`, and `ask` for `question`, `rerun` for `infra` (see "Customizing what Claude handles" in `README.md`). `--auto-push no`: you stop at the commit and the user pushes from the dashboard.

## Remove

`pr-watch unregister --repo <repository> --pr <number>`, or the "Stop watching" button of the dashboard. A merged or declined PR leaves the list by itself.

## If it fails

`pr-watch doctor` says what is missing (tokens, scopes). If the command answers "Bitbucket workspace not set", the Bitbucket workspace is not set: `pr-watch profile load profiles/<team>.json` or Settings › Team (`pr-watch-setup` skill). Any other problem: `pr-watch-diagnose` skill.
