---
name: pr-watch-wake
description: Quoi faire quand cette session est réveillée par le démon PR Watch — message qui commence par « [pr-watch daemon] » (pipeline en échec, commentaire de relecteur, revue automatique, quality gate Sonar). Corriger seul, répondre en français sur la PR, résoudre les fils, puis terminer par la ligne PR-WATCH: OK ou PR-WATCH: ATTENTION. Handle a PR Watch wake-up message.
---

# Traiter un réveil de PR Watch

*English version: [below](#handle-a-pr-watch-wake-up-english).*

Un message qui commence par `[pr-watch daemon]` ne vient pas de l'utilisateur : c'est le démon que cette session a demandé en enregistrant la PR. Il décrit ce qui s'est passé (`[pipeline-failed]`, `[comment]`, `[question]`, `[review-bot]`, `[sonar-failed]`, `[user-decision]`, `[manual]` pour un réveil forcé par l'utilisateur). Travaille seul : n'embête l'utilisateur que par la ligne finale `PR-WATCH: ATTENTION`.

## Ce qu'il faut faire

- **Corriger** dans le clone indiqué, sur la branche de la PR (jamais de rebase, jamais de force push). Lance le lint et les tests liés, commite avec la référence du ticket, puis pousse si le message le dit (`do NOT push` : ne pousse pas et dis-le dans le rapport).
- **Pipeline en échec** : lis le résumé du log fourni. Une panne d'infrastructure (authentification au registre, réseau, runner) n'est pas un problème de code : ne change pas le code, dis-le, et relance le pipeline au plus une fois.
- **Commentaire de relecteur** : réponds **en français, sur la PR**, dans le fil du commentaire. Quand il n'y a pas de débat, résous le fil **après** avoir répondu. Si le commentaire pose une question ou demande une décision, réponds et laisse le fil ouvert, puis termine par `ATTENTION`.
- **Revue automatique** (`[review-bot]`) : corrige les remarques critiques et majeures, juge les mineures, réponds sur la PR. Les remarques en ligne de la même revue arrivent dans le même message, chacune avec son fil : traite chaque fil séparément.
- **Sonar** : quand l'analyse de la PR est disponible, lis la quality gate et les issues de la PR (`pullRequest = <numéro>`) avec le MCP Sonar et corrige ce que le changement introduit. Prends la clé de projet (`id`) et le `pullRequest` **exactement** dans l'adresse fournie ; n'invente jamais une clé de projet (une mauvaise répond 404).
- **Commentaire de l'auteur de la PR** (l'utilisateur, signalé « the author of the PR ») : ce n'est pas une revue à traiter d'office. Il peut donner du contexte à garder pour les réponses suivantes, répondre à un autre commentaire (lis-les ensemble : il peut le trancher) ou demander un changement. Fais ce qu'il dit, ne réponds sur la PR que s'il attend une réponse, et ne résous pas un fil que l'utilisateur discute encore.
- **`[user-decision]`** : c'est la réponse de l'utilisateur à une question d'un relecteur ; fais ce qu'elle dit, et réponds au relecteur sur la PR si c'est utile.

## Répondre et résoudre par l'API Bitbucket

**Termine chaque commentaire que tu publies par la ligne `— 🤖 Claude`** (`claudeReplyMarker` de la configuration) : tes réponses partent avec le compte de l'utilisateur, et cette signature est ce qui permet au démon de ne pas se réveiller sur elles.

Les identifiants sont dans l'environnement (`BITBUCKET_EMAIL`, `BITBUCKET_TOKEN`). Le dépôt et le numéro de PR sont dans le message ; l'espace de travail est celui de l'adresse `origin`.

```sh
# répondre dans le fil du commentaire <id>
curl -s -u "$BITBUCKET_EMAIL:$BITBUCKET_TOKEN" -H 'Content-Type: application/json' \
  -X POST "https://api.bitbucket.org/2.0/repositories/<espace>/<dépôt>/pullrequests/<pr>/comments" \
  -d '{"content":{"raw":"Ta réponse en français\n\n— 🤖 Claude"},"parent":{"id":<id>}}'

# résoudre le fil dont le premier commentaire est <id>
curl -s -u "$BITBUCKET_EMAIL:$BITBUCKET_TOKEN" -X POST \
  "https://api.bitbucket.org/2.0/repositories/<espace>/<dépôt>/pullrequests/<pr>/comments/<id>/resolve"
```

Lis la réponse de chaque appel : n'annonce que ce qui a réussi. **PR Watch vérifie ton rapport sur Bitbucket** : un fil que tu annonces répondu sans réponse visible fait passer la PR en « attention » ; un fil annoncé résolu mais resté ouvert est résolu par PR Watch ; un fil sans réponse n'est jamais résolu en silence. Ne dis donc jamais « répondu » ou « résolu » pour ce que tu n'as pas fait.

## Terminer

Fais un rapport court **en français** : ce que tu as vu, ce que tu as changé, ce qui reste ouvert. Si rien n'était à faire, une ligne suffit.

La **dernière ligne** de ta réponse est lue par le démon, obligatoire :

- `PR-WATCH: OK` — tout est traité, personne n'a rien à faire ;
- `PR-WATCH: ATTENTION <une phrase en français>` — l'utilisateur doit décider ou agir (question dans un commentaire, choix, problème que tu n'as pas pu régler seul, droits ou infrastructure, correctif que tu as choisi de ne pas pousser).

Sans cette ligne, ou après un échec, la PR est signalée à l'utilisateur comme demandant son attention.

---

# Handle a PR Watch wake-up (English)

A message that starts with `[pr-watch daemon]` does not come from the user: it is the daemon that this session asked for when it registered the PR. It describes what happened (`[pipeline-failed]`, `[comment]`, `[question]`, `[review-bot]`, `[sonar-failed]`, `[user-decision]`, `[manual]` for a wake-up forced by the user). Work alone: only bother the user through the final line `PR-WATCH: ATTENTION`.

## What to do

- **Fix** in the indicated clone, on the PR's branch (never rebase, never force push). Run the lint and the related tests, commit with the ticket reference, then push if the message says so (`do NOT push`: do not push and say so in the report).
- **Failed pipeline**: read the provided log summary. An infrastructure failure (registry authentication, network, runner) is not a code problem: do not change the code, say so, and rerun the pipeline at most once.
- **Reviewer comment**: reply **in French, on the PR**, in the comment's thread. When there is no debate, resolve the thread **after** replying. If the comment asks a question or requests a decision, reply and leave the thread open, then finish with `ATTENTION`.
- **Automatic review** (`[review-bot]`): fix the critical and major remarks, judge the minor ones, reply on the PR. The inline remarks of the same review arrive in the same message, each with its thread: handle each thread separately.
- **Sonar**: when the PR's analysis is available, read the quality gate and the PR's issues (`pullRequest = <number>`) with the Sonar MCP and fix what the change introduces. Take the project key (`id`) and the `pullRequest` **exactly** from the provided address; never invent a project key (a wrong one returns 404).
- **Comment from the PR's author** (the user, flagged "the author of the PR"): it is not a review to handle by default. It may give context to keep for the following replies, answer another comment (read them together: it may settle it) or request a change. Do what it says, only reply on the PR if it expects a reply, and do not resolve a thread the user is still discussing.
- **`[user-decision]`**: this is the user's answer to a reviewer's question; do what it says, and reply to the reviewer on the PR if useful.

## Reply and resolve through the Bitbucket API

**End every comment you publish with the line `— 🤖 Claude`** (`claudeReplyMarker` in the configuration): your replies go out with the user's account, and this signature is what lets the daemon not wake up on them.

The credentials are in the environment (`BITBUCKET_EMAIL`, `BITBUCKET_TOKEN`). The repository and the PR number are in the message; the workspace is the one in the `origin` address.

```sh
# reply in the thread of comment <id>
curl -s -u "$BITBUCKET_EMAIL:$BITBUCKET_TOKEN" -H 'Content-Type: application/json' \
  -X POST "https://api.bitbucket.org/2.0/repositories/<workspace>/<repository>/pullrequests/<pr>/comments" \
  -d '{"content":{"raw":"<your reply, written in French>\n\n— 🤖 Claude"},"parent":{"id":<id>}}'

# resolve the thread whose first comment is <id>
curl -s -u "$BITBUCKET_EMAIL:$BITBUCKET_TOKEN" -X POST \
  "https://api.bitbucket.org/2.0/repositories/<workspace>/<repository>/pullrequests/<pr>/comments/<id>/resolve"
```

Read the response of each call: only announce what succeeded. **PR Watch checks your report on Bitbucket**: a thread you announce as replied to without a visible reply makes the PR go to "attention"; a thread announced as resolved but still open is resolved by PR Watch; a thread without a reply is never resolved silently. So never say "replied" or "resolved" for something you did not do.

## Finish

Write a short report **in French**: what you saw, what you changed, what remains open. If there was nothing to do, one line is enough.

The **last line** of your answer is read by the daemon, and is mandatory:

- `PR-WATCH: OK` — everything is handled, nobody has anything to do;
- `PR-WATCH: ATTENTION <one sentence in French>` — the user must decide or act (question in a comment, choice, problem you could not settle alone, rights or infrastructure, fix you chose not to push).

Without this line, or after a failure, the PR is flagged to the user as needing their attention.
