# PR Watch

*English version: [below](#pr-watch-english).*

PR Watch surveille vos pull requests Bitbucket et **réveille la session Claude Code qui les a ouvertes** quand quelque
chose demande une réaction : pipeline en échec, commentaire de relecteur, revue automatique, quality gate Sonar. Claude
corrige seul ce qu'il peut, vous prévient quand il a besoin de vous, et vous êtes notifié quand la PR est prête à être
mergée.

```
        Claude Code                             Démon                           Bitbucket                 Vous
    (session de la PR)                        PR Watch
             │                                    │                                 │                       │
             │ ① pr-watch register                │                                 │                       │
             │ (PR, session, réglages)            │                                 │                       │
             ├───────────────────────────────────▶┤                                 │                       │
             │                                    │ enregistre la PR (registre)     │                       │
             │                                    │                                 │                       │
             │                                    │ ② toutes les 60 s : lit         │                       │
             │                                    ├────────────────────────────────▶┤                       │
             │                                    │                                 │                       │
             │                                    │ PR, pipelines, commentaires,    │                       │
             │                                    │ statuts                         │                       │
             │                                    ├◀────────────────────────────────┤                       │
             │                                    │                                 │                       │
             │ ③ claude --resume <session>        │                                 │                       │
             │ -p "<ce qui s'est passé>"          │                                 │                       │
             ├◀───────────────────────────────────┤                                 │                       │
             │                                    │ quand un événement demande une  │                       │
             │                                    │ réaction                        │                       │
             │                                    │                                 │                       │
             │ ④ corrige, teste, commite, pousse, répond sur la PR                  │                       │
             ├────────────────────────────────────┼────────────────────────────────▶┤                       │
             │                                    │                                 │                       │
             │ ⑤ dernière ligne de la réponse :   │                                 │                       │
             │ PR-WATCH: OK                       │                                 │                       │
             │ ou PR-WATCH: ATTENTION <raison>    │                                 │                       │
             ├───────────────────────────────────▶┤                                 │                       │
             │                                    │                                 │                       │
             │                                    │ ⑥ notification (urgente si ATTENTION)                   │
             │                                    ├─────────────────────────────────┼──────────────────────▶┤
             │                                    │                                 │                       │
```

Le **registre** garde, pour chaque PR, sa session Claude, ses réglages et ses curseurs (ce qui a déjà été traité). Le tableau
de bord web et l'icône de barre lisent et écrivent le même registre, et demandent au démon d'agir (réveil, pause, merge…).

## Installation

Prérequis : Python 3.10 ou plus (bibliothèque standard seulement), la commande `claude` (Claude Code) et un jeton
Bitbucket dans l'environnement du processus qui lance le démon :

```sh
export BITBUCKET_EMAIL=vous@exemple.com
export BITBUCKET_TOKEN=...          # portées : lecture/écriture des pull requests et des pipelines
pipx install git+ssh://git@bitbucket.org/<espace>/pr-watch.git   # installe la commande `pr-watch`
# depuis une release GitHub (dépôt privé : gh CLI connectée) :
#   gh release download vX.Y.Z -R <propriétaire>/pr-watch -p '*.whl' && pipx install ./pr_watch-*.whl
# depuis une copie locale : pipx install .   (ou : make link, sans pipx : la commande suit le code de la copie ; ou : python3 -m prwatch …)
```

La fenêtre de PR Watch est le tableau de bord web, identique sur Linux, macOS et Windows ; il lui faut Chrome, Edge,
Brave ou Chromium (sinon il s'ouvre dans le navigateur par défaut). Seule l'icône de la barre (Linux) a besoin de GTK 3,
que pip ne sait pas installer : `sudo apt install python3-gi gir1.2-gtk-3.0`, puis `pipx install --system-site-packages …`
pour que l'environnement de pipx voie `gi`. Sans GTK, `pr-watch tray` l'explique en une ligne et tout le reste fonctionne.

Pour changer l'état du ticket Jira d'une PR (voir plus bas), le processus qui sert le tableau de bord a aussi besoin de
`ATLASSIAN_API_TOKEN` (jeton d'API Atlassian, associé à `BITBUCKET_EMAIL`) ; le site Jira est `jiraSite` dans `config.json`.

Au premier démarrage, rien n'est surveillé tant que l'espace Bitbucket n'est pas renseigné (bandeau du tableau de bord). Les réglages
d'une équipe tiennent dans un **profil** : un fichier JSON `{"name": "…", "options": {…}}` qui ne peut régler que les clés listées
ci-dessous. Le dossier `profiles/` du dépôt en contient pour les équipes qui en ont un ; pour le charger :
`pr-watch profile load profiles/<équipe>.json`, ou Options › Équipe › Profil d'équipe › Charger un fichier.
Pour une autre équipe, copiez un de ces fichiers, adaptez-le et chargez-le de la même façon.

Ce qui dépend de l'équipe se règle dans l'onglet Options › Équipe du tableau de bord (chaque champ donne un exemple) ou dans `config.json`
(valeurs par défaut entre parenthèses) :

| Option | Rôle |
|---|---|
| `bitbucketWorkspace` (vide) | l'espace Bitbucket (`acme-corp` pour `https://bitbucket.org/acme-corp/api-server/…`) ; obligatoire, rien n'est surveillé sans lui |
| `jiraSite` (vide) | l'adresse de Jira Cloud (`https://acme-corp.atlassian.net`) ; vide = ni lien ni action sur les tickets |
| `jiraImpactsField` (vide) | le nom du champ que le workflow oblige à remplir avant certains états (ex. `Description des impacts potentiels`) ; vide = aucun champ |
| `sonarPattern` (`sonar`) | expression régulière des étapes et statuts du contrôle qualité ; vide = pas d'événement Sonar |
| `reviewStepPattern` (vide) | expression régulière de l'étape de revue automatique du pipeline (ex. `AI review\|pr-review`) ; vide = pas de revue |
| `infraPatterns` (erreurs réseau usuelles) | phrases qui font d'un échec une panne d'infrastructure (relancé une fois, sans réveiller Claude) |
| `cloneFolders` (`~/workspace`, `~/src`, …) | où chercher le clone local d'un dépôt quand on ajoute une PR |
| `followBotComments` (aucun) | les commentaires d'un compte d'application à suivre (`{author, startsWith, ignoreIfContains}`) ; seulement dans un profil ou `config.json` |
| `ticketTransitions` (aucun) | l'état proposé après le merge, par type de ticket (onglet Options › Ticket Jira) |

`claudeArgs` (les droits de la session réveillée) ne se règle que dans `config.json` : un profil ne peut pas le modifier.

Le jeton n'est jamais écrit dans le registre, le journal ni la configuration. Le résultat du quality gate est lu par le démon dans le log
de l'étape Sonar ; le détail des problèmes est lu par la session Claude réveillée, avec le serveur MCP Sonar qu'elle a déjà.

### Autorisations

Au démarrage, le démon vérifie d'abord ce dont PR Watch a besoin : la commande `claude`, les identifiants Bitbucket et leurs
portées (lecture et écriture des pull requests et des pipelines, lecture des dépôts ; lues dans l'en-tête `X-OAuth-Scopes`, sans rien modifier), votre compte, et le jeton Jira. Ce qui manque est
enregistré (`permissions.json`), journalisé, et le tableau de bord ouvre un **guide** pas à pas pour l'ajouter (création du
jeton, portées à cocher, variable d'environnement à définir). Vous pouvez **refuser** une autorisation facultative : les
fonctions qui en dépendent sont alors masquées (merger sans les portées d'écriture, ajouter une PR sans connaître votre compte,
changer l'état du ticket sans jeton Jira) ; sans la lecture des dépôts, le démon ne tente plus de lire la stratégie de merge de
Bitbucket (« Rebase puis fast-forward » est proposé). Sont indispensables : `claude`, les identifiants Bitbucket et la lecture des pull
requests et des pipelines ; sans eux un bandeau reste affiché. L'onglet *Options* liste l'état de chaque autorisation, rouvre le
guide et permet de revérifier. Seules vos propres pull requests peuvent être ajoutées à la main.

## Utilisation

| Commande | Rôle |
|---|---|
| `pr-watch register --repo R --pr N --branch B --work-dir D --session S --session-cwd C` | enregistre une PR (Claude le fait à chaque PR qu'il crée) |
| `pr-watch ensure` | démarre le démon s'il ne tourne pas (une seule instance, jamais deux) |
| `pr-watch ui [--select REPO#N [--pulse]]` | ouvre la fenêtre de PR Watch (le tableau de bord web), sur une PR si `--select` ; avec `--pulse` sa carte pulse un instant au lieu d'être marquée (ce que fait l'icône de barre) |
| `pr-watch profile load <fichier>` | charge les réglages d'une équipe (espace Bitbucket, Jira, Sonar, revue automatique…) depuis un fichier JSON, par exemple `profiles/<équipe>.json` |
| `pr-watch tray` | démarre l'icône de barre (Linux) ; un clic ouvre son menu natif : l'état (démon, Claude), les premières pull requests (5 par défaut, option `trayMaxPrs` de 1 à 20 ; celles qui demandent quelque chose d'abord : attention, décision attendue, essais épuisés, prête à merger, ticket à valider ; puis les autres en cours, les terminées en dernier ; un clic sur l'une ouvre la fenêtre sur elle, qui pulse un instant), une ligne qui compte le reste, puis « Ouvrir le tableau de bord », « Activer / Désactiver la surveillance » et « Quitter PR Watch ». Seules ces lignes sont cliquables ; macOS et Windows n'ont pas d'icône, tout passe par le tableau de bord (`pr-watch ui`) |
| `pr-watch list` / `pr-watch settings --repo R --pr N` | état du registre / réglages d'une PR |
| `pr-watch doctor` | vérifie ce dont PR Watch a besoin (jetons, portées, commandes) et guide l'ajout de ce qui manque |
| `pr-watch once [--dry-run]` | un seul cycle ; `--dry-run` montre ce qui serait envoyé à Claude |
| `pr-watch merge --repo R --pr N [--strategy squash\|merge_commit\|fast_forward] [--keep-branch]` | merge par l'API |
| `pr-watch stop` / `unregister` | arrête le démon / retire une PR |
| `pr-watch install-desktop` / `install-launcher` | enregistre PR Watch comme application du système |

Ce que le démon détecte, à chaque cycle et pour chaque PR :

- un **pipeline en échec** (avec le résumé du log de l'étape fautive) ;
- un **commentaire**, y compris ceux de l'auteur de la PR (contexte à retenir, réponse à un autre commentaire, demande de
  changement) ; seules les réponses de Claude, signées `claudeReplyMarker` (`— 🤖 Claude`, car elles partent avec le compte de
  l'auteur), sont écartées, et un commentaire de l'auteur n'est jamais proposé comme une question à lui poser ; et la **revue automatique** du pipeline (`followBotComments` et `reviewStepPattern` ; les autres comptes
  d'application, comme les liens de staging, sont ignorés). Quand l'étape de revue réussit sans publier son commentaire,
  son verdict est lu dans le log de l'étape (`reviewStepPattern`) ;
- un **quality gate Sonar** en échec ou une analyse terminée (`sonarPattern` reconnaît l'étape et le statut) ;
- une **panne d'infrastructure** (authentification au registre, réseau, runner) : pas de réveil de Claude, le pipeline est
  relancé une fois par commit, puis vous êtes prévenu ;
- une PR **prête à merger** : approuvée, pipelines verts sur le dernier commit, aucune tâche ouverte ;
- une PR **fusionnée ou refusée** : elle est marquée terminée.

## Personnaliser ce que Claude traite

Chaque type d'événement a un mode, au choix :

| Type | Modes (le premier est le défaut) |
|---|---|
| `pipeline` : pipelines en échec | `fix` Claude corrige · `notify` me notifier seulement · `ignore` |
| `comment` : commentaires des relecteurs | `fix` · `notify` · `ignore` |
| `question` : commentaire qui pose une question ou demande une validation | `ask` me demander (oui / non / autre) · `fix` · `notify` · `ignore` |
| `review-bot` : revue automatique | `fix` · `notify` · `ignore` |
| `sonar` : quality gate | `fix` · `notify` · `ignore` |
| `infra` : pannes d'infrastructure | `rerun` relancer une fois puis me notifier · `notify` · `ignore` |
| `ready` : PR prête à merger | `notify` · `ignore` |

Et **Claude pousse-t-il ses corrections ?** (`autoPush`, oui par défaut ; sinon il s'arrête au commit et le dit dans son
rapport, vous poussez depuis le tableau de bord.)

- **Globalement** : onglet *Options* du tableau de bord, bouton « Ce que Claude traite… », ou
  `~/.claude/pr-watch/config.json` (`{"autoPush": false, "handling": {"comment": "notify"}}`).
- **Pour une PR**, qui l'emporte sur le global : bouton *Réglages…* de sa carte, ou en ligne de commande :

```sh
pr-watch register … --auto-push no --handling comment=notify,sonar=ignore
pr-watch settings --repo my-repo --pr 12 --handling sonar=default   # default = suivre le global
pr-watch settings --repo my-repo --pr 12 --reset
```

Le démon attend que la session soit inactive (transcript non modifié depuis 90 s) avant de la réveiller, plafonne à
5 essais par PR (`maxAttempts`) puis vous prévient. Une PR fusionnée ou refusée quitte la liste `removeDoneAfterMinutes` minutes après sa fin (60 par défaut, `0` pour la garder ;
onglet *Options*). PR Watch suit la consommation du forfait Claude (session de 5 h et semaine) à partir des quotas que la CLI rapporte après chaque réponse, sans appeler le compte ni lire de jeton (une requête d'un mot sur le modèle le moins cher les rafraîchit avant un réveil quand la mesure a plus de 10 minutes, ou avec « Actualiser » dans le détail de la pastille) et suspend les réveils Claude quand l'une des deux a moins de marge sous son seuil (`usageSessionMarginPercent` et `usageWeekMarginPercent`, 5 % par défaut, réglables séparément), pour que les réveils n'entament pas les crédits de dépassement ; la surveillance des PR continue, et les actions normalement confiées à Claude sont temporairement converties en notifications, sans modifier les réglages enregistrés. Les réveils reprennent tout seuls à la remise à zéro du quota ou avec « Réactiver malgré tout ». Seuil, interrupteur (`usageGuard`) et horaires de fonctionnement (`schedule`) sont regroupés dans l’onglet *Options* › *Économie de jetons*. Hors horaires, aucune PR n'est vérifiée : l'interface indique « Démon arrêté par les horaires » et le service reprend automatiquement à la prochaine ouverture. Le bouton du démon peut forcer temporairement la reprise jusqu'à la prochaine fermeture ; pendant une plage, son arrêt propose soit une reprise au prochain horaire, soit une reprise manuelle. Les autres réglages (`pollSeconds`, `minApprovals`, `notify`,
`claudeArgs`, `followBotComments`, `infraPatterns`…) sont dans `config.json`.

### Attendre la validation du ticket

Option `keepUntilTicketValidated` (activée par défaut, onglet *Options* › *Général*), valable pour un merge manuel, automatique ou fait ailleurs :
une PR mergée dont le ticket Jira n'a pas changé d'état reste dans la liste avec le statut *En attente de validation du ticket*, au lieu de la quitter
après `removeDoneAfterMinutes`. L'état du ticket est lu au moment du merge ; dès qu'il diffère (changé depuis l'application, bouton *Ticket…*, ou dans Jira),
la PR repart avec le délai habituel. Quand un ticket a plusieurs PR en cours, seule la dernière mergée attend, et le dialogue du ticket ne s'ouvre
qu'après elle. Sans ticket, avec Jira injoignable ou l'option décochée, la PR quitte la liste comme avant.

### État proposé pour le ticket

Une fois la PR mergée, le dialogue du ticket propose un état selon le type du ticket (option `ticketTransitions`, onglet *Options* › *Ticket Jira*) : par type de ticket,
car chacun a son propre workflow (aucun état par défaut : un profil d'équipe peut en fournir, par exemple `{"Tâche": "Terminé(e)"}` ; sans état pour un type, vous choisissez). Les types et les états de chacun sont lus dans Jira. L'état est retrouvé par le nom de la transition ou de l'état visé, sans tenir compte de la casse ni des accents
(« close » trouve « Closed »). Rien n'est présélectionné si aucun état ne correspond.

### Merge automatique

Désactivé par défaut (`autoMerge`, onglet *Options* › *Général*). Deux modes :

- `ready` : la PR est mergée dès que ses conditions de merge sont remplies (approbations, pipelines verts, aucune tâche ouverte, voir « Prête à merger ») ;
- `resolved` : en plus, aucun fil de commentaire ne doit rester ouvert. Ne comptent pas : les fils résolus, les commentaires de l'auteur de la PR,
  la revue automatique qui n'a « rien d'important à signaler » et les commentaires positifs qui ne demandent rien (« LGTM », « super, merci »…,
  reconnus par `positiveCommentPatterns` ; un « mais », une question ou une demande de changement, `changeRequestPatterns`, les fait compter).

Le choix global peut être refait par PR (bouton *Réglages…* de la carte, ou `pr-watch settings --repo R --pr N --auto-merge off|ready|resolved|default`).
Le merge utilise la stratégie réglée dans Bitbucket pour la branche (sinon rebase + fast-forward). Il n'a jamais lieu tant qu'une question de relecteur
attend votre réponse ou que la PR demande votre attention ; un merge refusé n'est pas retenté pour le même commit. Ce qui l'attend est écrit sur la carte.

### Les questions des relecteurs

Un commentaire qui pose une question ou demande une validation (il contient un `?`, « valider », « d'accord », « can you
confirm »… : `questionPatterns` dans `config.json`, des expressions régulières) n'est **pas** confié à Claude : vous êtes
notifié (urgent), la carte de la PR passe en **❓ Décision attendue** et montre le commentaire avec la question
**À traiter ? Oui · Non · Autre [ … ]**. *Oui* : Claude traite le commentaire au prochain cycle ; *Non* : il est laissé de
côté ; *Autre* : Claude suit la consigne que vous écrivez. Le mode `fix` du type `question` rétablit le traitement
direct, pour une PR ou globalement.

### Le protocole avec Claude

Le message de réveil explique l'événement et demande de corriger seul. La **dernière ligne** de la réponse est lue par le
démon : `PR-WATCH: OK` (tout est traité) ou `PR-WATCH: ATTENTION <phrase>` (il faut vous). `ATTENTION` déclenche une
notification urgente, colore l'icône et s'affiche sur la carte de la PR ; l'absence de ligne ou un échec comptent comme
une demande d'attention. Le dernier rapport de chaque PR est consultable.

La revue automatique publie un commentaire de synthèse et un commentaire en ligne par remarque : seule la synthèse est suivie, mais
les remarques ouvertes publiées avec elle sont transmises à Claude dans le même message, avec chacune son fil à traiter.

Le rapport est ce que Claude croit avoir fait : quand il annonce avoir répondu ou résolu un fil, PR Watch le vérifie sur
Bitbucket. Un fil annoncé résolu qui a bien sa réponse mais est resté ouvert est résolu par PR Watch ; un fil sans réponse n'est
jamais résolu en silence. Dans les deux cas où Bitbucket ne montre pas ce que le rapport annonce, le rapport le dit et la PR
passe en « attention ». Les questions ne sont jamais résolues.

## Les interfaces

Une seule fenêtre, la même sur tous les systèmes : le tableau de bord web. Le code de l'interface n'existe qu'une fois
(`prwatch/web/static/`).

- **Tableau de bord web** (`pr-watch ui`, tous systèmes), ouvert comme une application (Chrome en mode application, profil
  à part) : onglets *Pull requests*, *Journal*, *Options*. Chaque carte
  montre l'état, ce qui bloque le merge, la session Claude, les essais, et propose : ouvrir la session, forcer un réveil,
  pause, remise à zéro, réglages, dernier rapport, pousser, pousser en force, merger, arrêter la surveillance. Le journal est
  coloré par type, avec les répétitions regroupées et les rapports de Claude mis en forme.
- **Cartes** : réduites à une ligne par défaut (la flèche ▸ les étend) avec jusqu'à 3 petits boutons et un menu « … » qui
  donne toutes les autres actions. Par défaut, les 3 boutons sont les plus pertinents pour l'état de la PR (merger si elle
  est prête, pousser s'il y a des commits en attente, marquer comme vu si elle demande votre attention…) ; l'onglet
  *Options* permet de les fixer (`quickActions` dans `config.json`). Après une action, le démon relit la PR aussitôt.
- **Barre de la liste** : un filtre (dépôt, numéro, titre, branche ou ticket ; la touche `/` y place le curseur, Échap l'efface),
  la case « À traiter » (PR qui demandent une action : attention, question, prête à merger, essais épuisés), *Actualiser*,
  *Tout développer*, *Tout replier*, *Tout marquer vu* (quand des PR demandent votre attention) et *Ajouter une PR*.
- **Actualiser** : F5 (ou Ctrl+R) fait relire les pull requests tout de suite par le démon et met l'affichage à jour, sans recharger la
  page. Un rechargement par le menu du navigateur fonctionne aussi : la page est publique, seul l'accès aux données demande le jeton.
- **Liens externes** : un lien vers un autre site (la PR sur Bitbucket, les guides d'autorisation…) s'ouvre dans le navigateur par
  défaut du système, pas dans la fenêtre de PR Watch ; seules les adresses `http` et `https` sont ouvertes.
- **Une seule fenêtre** : `pr-watch ui` (icône, notification, lanceur) relance le navigateur, qui ouvre une nouvelle fenêtre ; avec
  le jeton d'activation d'un clic du milieu sur l'icône de la barre ou d'un clic sur une notification (le shell GNOME le transmet,
  `ProvideXdgActivationToken`), Wayland lui donne le focus. La fenêtre qui était déjà ouverte se ferme alors d'elle-même
  (un `BroadcastChannel` les met en relation) : il n'en reste qu'une, la plus récente, celle qui est au premier plan. Les cartes
  dépliées sont retrouvées. Le démon et le serveur du tableau de bord se relancent seuls quand le code de PR Watch change.
- **Icône de la barre** (Linux, GTK) : le logo de PR Watch avec une pastille dans le coin bas droit, verte (démon actif), orange
  (une PR demande votre attention), bleue (une PR est prête à merger) ou rouge (démon arrêté, y compris par les horaires). Un clic ouvre
  son menu natif : une ligne d'état (« 🟢 Démon en route · 🟢 Claude activé » ; chaque rond est vert quand tout marche, orange quand
  il attend et repart seul — horaires, forfait presque épuisé —, rouge quand il faut intervenir), les 5 pull requests qui demandent
  le plus quelque chose (un clic ouvre la fenêtre sur la carte, qui pulse un instant), une ligne qui compte les autres, puis *Ouvrir
  le tableau de bord*, *Activer / Désactiver la surveillance* et *Quitter PR Watch* (ferme la fenêtre et l'icône ; le démon continue).
  Seules ces lignes sont cliquables. `pr-watch install-desktop` inscrit
  l'application dans la liste des applications (recherche, dock, sélecteur de fenêtres) ; sa fenêtre y porte le nom et
  l'icône de PR Watch et non ceux de Chrome ou de l'application qui l'a lancée.
- **État de chaque PR** : une icône avant son nom (⚠ attention, ✔ prête à merger, ✖ essais épuisés, ⏸ pause, ■ terminée,
  👁 surveillée) dont le tooltip donne la raison. Un ⬆ s'y ajoute quand des commits de la branche locale n'ont pas été
  poussés (le bouton *Pousser* les envoie) ; l'état est lu dans `origin/<branche>`, sans réseau.
- **Notifications** : titre, puis dépôt / numéro / titre de la PR, puis détail. Sous Linux (`notify-send` 0.8+), cliquer
  dessus ouvre le tableau de bord sur la PR (`pr-watch ui --select REPO#N`). Double-cliquer une carte ouvre le journal de la PR, et le bouton *Dernier rapport* l'ouvre sur son dernier rapport de Claude ;
  le journal montre les 200 dernières entrées et « Entrées plus anciennes » remonte plus loin. « Archiver » vide le journal et le range dans `journal-archives/` (10 archives conservées).
- **Ouvrir la session** retrouve le processus de la session et l'application qui l'héberge (si aucune ne l'héberge, une
  fenêtre demande où la reprendre, en ne proposant que les endroits qui rouvrent vraiment cette conversation : Claude Desktop s'il la connaît, IntelliJ ou VS Code avec un plugin Claude assez récent, ou Claude Code dans le terminal par défaut ; une ligne explique pourquoi un endroit attendu manque ; l'endroit d'où la session a été ouverte est enregistré quand la PR est ajoutée à PR Watch, il est proposé en premier avec la mention « Recommandé ») : Claude Desktop ouvre la
  conversation exacte (`claude://code/continue?session=local_…`, l'identifiant que Desktop donne à la conversation ; il ne
  peut pas ouvrir une conversation qu'il ne connaît pas) ; le lien est ouvert par la page, pour que l'application prenne le focus ; IntelliJ et VS Code aussi avec le plugin
  [Claude Chat](https://github.com/pleday/claudechat) 0.9.8 / 0.9.7 ou plus, sinon ils ramènent la fenêtre du projet. Le plugin Claude Code d'Anthropic pour IntelliJ ne sait pas ouvrir une conversation donnée : il n'est pas proposé, utilisez « Claude Code (terminal) », qui reprend la conversation dans le terminal par défaut du système (réglage GNOME, sinon `x-terminal-emulator`).

- **Ajouter une PR à la main** : le bouton *Ajouter une PR* demande l'adresse de la PR (ou `dépôt#numéro`), retrouve son
  titre et sa branche, devine le clone local et propose, en premier, « Nouvelle session » (Claude démarre une session pour la PR, qui sera réveillée plus tard comme
  les autres) puis les dernières sessions Claude (celle qui porte la clé du ticket est présélectionnée) ; la PR est surveillée à partir de ce moment, comme avec `pr-watch register`.
- **Ticket Jira** (si `jiraSite` est renseigné) : la clé du ticket (`PROJ-123`) est lue dans le nom de la branche, sinon dans le titre de la PR. L'action
  *Ticket …* (mise en avant quand la PR est prête, ou fusionnée) lit dans le ticket les états où il peut aller, les propose, et
  préremplit le champ des impacts (`jiraImpactsField`, s'il y en a un) avec une suggestion en français (rédigée par `claude -p` d'après le titre et
  les fichiers modifiés, lus dans le dépôt local ; un gabarit « changement et zones modifiées » si Claude ne répond pas) à corriger : le champ est écrit dans le ticket avant la transition, comme le workflow l'exige. Après une PR fusionnée,
  l'état prévu pour ce type de ticket (`ticketTransitions`) est présélectionné ; avant, c'est à vous de choisir. La suggestion est rédigée par Claude : elle n'est pas demandée tant que Claude est suspendu (forfait presque épuisé, plus de jetons). Un merge réussi depuis le tableau de bord ouvre
  directement ce dialogue.
- **Plugins Claude Chat** : PR Watch compare les versions installées (IntelliJ, VS Code) aux releases GitHub du plugin et
  affiche un bandeau quand une mise à jour existe ; le bouton la télécharge et l'installe (dossier du plugin JetBrains, ou
  `code --install-extension`). L'IDE doit ensuite être redémarré ou rechargé. L'onglet *Options* liste les versions.

## Sécurité

Le tableau de bord n'écoute que sur `127.0.0.1`. Une page ou un appel sans le **jeton secret** (dans `web.json`, lisible par vous seul) est refusé (le jeton est créé une fois, dans `web.token`, lisible par vous seul : la fenêtre garde ainsi la même adresse d'un redémarrage à l'autre et il n'y en a jamais deux) ; les en-têtes `Host` et `Origin` sont vérifiés (contre le
DNS rebinding et les autres pages web) ; les actions destructrices (merge, poussée en force, retrait, push) exigent une
confirmation explicite ; la politique de sécurité de contenu interdit tout script en ligne ; ce qui vient de Bitbucket ou
du journal est toujours affiché comme texte. La poussée en force utilise `--force-with-lease`.

## Plateformes

| | Linux | macOS | Windows |
|---|---|---|---|
| Démon, registre, merge, réglages | testé | porté, non testé | porté, non testé |
| Détection de la session et de son application | testé | testé sur sortie `ps` enregistrée | testé sur sortie PowerShell enregistrée |
| Notifications | `notify-send` | `osascript` | PowerShell |
| Tableau de bord web | testé (Chrome) | non testé | non testé |
| Icône de barre GTK | testé | — | — |
| Application dans la liste | entrée `.desktop` | `~/Applications/PR Watch.app` | raccourci du menu Démarrer |

Tout ce qui dépend du système est dans le paquet `prwatch/system/`.

## Skills pour Claude Code

Le projet fournit des skills (`.claude/skills/`) que Claude Code charge dans un workspace qui l'ajoute :

| Skill | Pour |
|---|---|
| `pr-watch-setup` | installer la commande `pr-watch` (`make link` ou pipx), charger le profil d'équipe, configurer les jetons (guidé par `pr-watch doctor`), démarrer le démon et le tableau de bord |
| `pr-watch-register` | mettre une PR sous surveillance juste après l'avoir créée : `pr-watch register --pr N` devine le dépôt, la branche, le clone et la session |
| `pr-watch-wake` | savoir traiter un réveil du démon : corriger, répondre et résoudre les fils par l'API, terminer par `PR-WATCH: OK` ou `ATTENTION` |
| `pr-watch-diagnose` | comprendre ce qui ne marche pas : `pr-watch doctor`, journaux, symptômes fréquents et corrections |

`pr-watch doctor` affiche l'état de chaque autorisation (✔ / ✖ / ○) et le guide de ce qui manque ; il sort avec un code non nul s'il
manque quelque chose d'indispensable.

## Données

Le répertoire de données est `~/.claude/pr-watch` (`PR_WATCH_HOME` pour en changer) : `registry.json` (PR surveillées,
curseurs, réglages, derniers rapports), `config.json`, `daemon.log`, `daemon.pid`, `web.json`, `web.token`, et les demandes de l'icône de barre à la
fenêtre ouverte (`focus.json`, `quit.json`).

## Développement

```sh
make test      # tests unitaires et d'intégration (unittest, sans dépendance)
make e2e       # tableau de bord dans un vrai Chrome (voir e2e/README.md)
```

```
prwatch/
  cli.py            la ligne de commande (`pr-watch …`)
  core/             les fondations
    store.py          répertoire de données, journal, registre des PR surveillées
    config.py         réglages : ce qui est fait pour chaque type d'événement, options globales et de chaque PR
    bitbucket.py      l'API Bitbucket (identifiants, appels)
    permissions.py    autorisations nécessaires : vérification au démarrage, guides, fonctions masquées
    journal.py        lecture du journal (types d'entrées, répétitions regroupées, Markdown des rapports)
  engine/           ce que fait le démon
    cycle.py          un tour sur chaque PR : que s'est-il passé, que faire
    events.py         détection : pipelines, commentaires, revue automatique, Sonar
    wake.py           réveil de la session Claude : message, exécution, verdict, vérification du rapport
    merging.py        merge d'une PR, stratégies, PR prête à merger, retrait des PR terminées
    registration.py   ajout d'une PR à la liste
    service.py        les processus : boucle du démon, serveur du tableau de bord, icône de barre
  integrations/     les services autour de Bitbucket
    jira.py           le ticket Jira d'une PR : états possibles, impacts, transition
    plugins.py        versions installées et mises à jour des plugins Claude Chat
  system/           tout ce qui dépend du système (Linux, macOS, Windows)
    host.py           le système, verrous, processus détachés, environnement des processus fils
    notifications.py  notifications et ouverture de liens
    terminal.py       le terminal par défaut
    processes.py      table des processus, application qui héberge une session Claude
    claude.py         transcripts, titres et sessions de Claude Code et Claude Desktop
    editors.py        IntelliJ, VS Code et leurs plugins Claude
    opening.py        ouvrir une conversation dans l'application qui convient
    window.py         la fenêtre du tableau de bord (navigateur en mode application)
    desktop.py        PR Watch comme application du système (liste des applications, lanceur)
  actions/          ce que les interfaces peuvent demander, et les textes qu'elles affichent en commun
    status.py         état de chaque PR, boutons pertinents, état du tableau de bord
    forms.py          formulaires de merge, confirmations, réglages
    pr.py             actions sur une PR : pause, réponse à une question, réveil, push, merge, réglages
    sessions.py       ouvrir la session dans une application ou un terminal
    tickets.py        ticket Jira
    adding.py         ajouter une PR à la main
    options.py        options, profils d'équipe, démon, autorisations, plugins, liens
    common.py         ce qu'elles partagent ; dispatch.py : la table des actions
  web/              le tableau de bord
    server.py         serveur (stdlib, loopback, jeton)
    static/           la page : index.html, style.css, icon.svg et les modules JavaScript de js/
      js/core.js        base : jeton, état partagé, icônes, appels au serveur, dialogues
      js/cards.js       la carte d'une PR : état, boutons pertinents, menu « … », détails
      js/dialogs.js     dialogues : ouvrir la session, ticket, ajout, merge, push, réglages
      js/journal.js     l'onglet Journal
      js/options.js     l'onglet Options : options, autorisations et leurs guides, plugins
      js/app.js         point d'entrée : dessine l'état du serveur, onglets, rafraîchissement
  tray/             l'icône de la barre (GTK 3, Linux), la seule partie hors du tableau de bord
    app.py            l'icône et son menu natif (D-Bus : StatusNotifierItem et dbusmenu)
    summary.py        le contenu du menu : l'état, les PR à traiter, la note sur les autres
    icon.py           son image : le logo et la pastille de l'état, dessinés sans dépendance
profiles/           réglages d'équipe prêts à charger (JSON), hors du paquet installé
tests/              unittest ; chaque test a son propre répertoire de données
e2e/                test de bout en bout avec Playwright
```

Limites connues : si le jeton Bitbucket n'a pas la portée `read:repository`, les statuts de commit ne sont pas lus (ceux
de la PR le sont) et la stratégie de merge par défaut configurée dans Bitbucket n'est pas connue (le dialogue de merge propose
alors « Rebase puis fast-forward » et le précise, avec une icône ℹ qui dit de modifier le jeton) ; Wayland interdit de ramener la fenêtre d'un terminal depuis une autre application.

## Licence

MIT : voir [LICENSE](LICENSE).

---

# PR Watch (English)

PR Watch watches your Bitbucket pull requests and **wakes the Claude Code session that opened them** when something calls
for a reaction: failed pipeline, reviewer comment, automated review, Sonar quality gate. Claude fixes on its own what it
can, tells you when it needs you, and you are notified when the PR is ready to be merged.

```
        Claude Code                             Daemon                          Bitbucket                 You
    (PR session)                              PR Watch
             │                                    │                                 │                       │
             │ ① pr-watch register                │                                 │                       │
             │ (PR, session, settings)            │                                 │                       │
             ├───────────────────────────────────▶┤                                 │                       │
             │                                    │ stores the PR (registry)        │                       │
             │                                    │                                 │                       │
             │                                    │ ② every 60 s: reads             │                       │
             │                                    ├────────────────────────────────▶┤                       │
             │                                    │                                 │                       │
             │                                    │ PR, pipelines, comments,        │                       │
             │                                    │ statuses                        │                       │
             │                                    ├◀────────────────────────────────┤                       │
             │                                    │                                 │                       │
             │ ③ claude --resume <session>        │                                 │                       │
             │ -p "<what happened>"               │                                 │                       │
             ├◀───────────────────────────────────┤                                 │                       │
             │                                    │ when an event requires a        │                       │
             │                                    │ reaction                        │                       │
             │                                    │                                 │                       │
             │ ④ fixes, tests, commits, pushes, replies on the PR                   │                       │
             ├────────────────────────────────────┼────────────────────────────────▶┤                       │
             │                                    │                                 │                       │
             │ ⑤ last line of the reply:          │                                 │                       │
             │ PR-WATCH: OK                       │                                 │                       │
             │ or PR-WATCH: ATTENTION <reason>    │                                 │                       │
             ├───────────────────────────────────▶┤                                 │                       │
             │                                    │                                 │                       │
             │                                    │ ⑥ notification (urgent if ATTENTION)                    │
             │                                    ├─────────────────────────────────┼──────────────────────▶┤
             │                                    │                                 │                       │
```

The **registry** keeps, for each PR, its Claude session, its settings and its cursors (what has already been handled). The
web dashboard and the tray icon read and write the same registry, and ask the daemon to act (wake-up, pause, merge…).

## Installation

Requirements: Python 3.10 or later (standard library only), the `claude` command (Claude Code) and a Bitbucket token in
the environment of the process that starts the daemon:

```sh
export BITBUCKET_EMAIL=you@example.com
export BITBUCKET_TOKEN=...          # scopes: read/write on pull requests and pipelines
pipx install git+ssh://git@bitbucket.org/<workspace>/pr-watch.git   # installs the `pr-watch` command
# from a GitHub release (private repository: logged-in gh CLI):
#   gh release download vX.Y.Z -R <owner>/pr-watch -p '*.whl' && pipx install ./pr_watch-*.whl
# from a local copy: pipx install .   (or: make link, without pipx: the command follows the code of the copy; or: python3 -m prwatch …)
```

The PR Watch window is the web dashboard, identical on Linux, macOS and Windows; it needs Chrome, Edge, Brave or
Chromium (otherwise it opens in the default browser). Only the tray icon (Linux) needs GTK 3, which pip cannot install:
`sudo apt install python3-gi gir1.2-gtk-3.0`, then `pipx install --system-site-packages …` so that the pipx environment
sees `gi`. Without GTK, `pr-watch tray` explains it in one line and everything else works.

To change the state of a PR's Jira ticket (see below), the process that serves the dashboard also needs
`ATLASSIAN_API_TOKEN` (Atlassian API token, associated with `BITBUCKET_EMAIL`); the Jira site is `jiraSite` in `config.json`.

On first start, nothing is watched until the Bitbucket workspace is set (banner in the dashboard). A team's settings fit in a
**profile**: a JSON file `{"name": "…", "options": {…}}` that can only set the keys listed below. The `profiles/` folder of
the repository contains some for the teams that have one; to load one:
`pr-watch profile load profiles/<team>.json`, or Settings › Team › Team profile › Load a file….
For another team, copy one of these files, adapt it and load it the same way.

What depends on the team is set in the Settings › Team tab of the dashboard (each field gives an example) or in `config.json`
(default values in parentheses):

| Option | Purpose |
|---|---|
| `bitbucketWorkspace` (empty) | the Bitbucket workspace (`acme-corp` for `https://bitbucket.org/acme-corp/api-server/…`); required, nothing is watched without it |
| `jiraSite` (empty) | the address of Jira Cloud (`https://acme-corp.atlassian.net`); empty = no links or actions on tickets |
| `jiraImpactsField` (empty) | the name of the field that the workflow requires before some states (e.g. `Description des impacts potentiels`); empty = no field |
| `sonarPattern` (`sonar`) | regular expression of the quality check steps and statuses; empty = no Sonar event |
| `reviewStepPattern` (empty) | regular expression of the pipeline's automated review step (e.g. `AI review\|pr-review`); empty = no review |
| `infraPatterns` (usual network errors) | phrases that make a failure an infrastructure outage (re-run once, without waking Claude) |
| `cloneFolders` (`~/workspace`, `~/src`, …) | where to look for a repository's local clone when a PR is added |
| `followBotComments` (none) | the comments of an application account to follow (`{author, startsWith, ignoreIfContains}`); only in a profile or `config.json` |
| `ticketTransitions` (none) | the state proposed after the merge, per ticket type (Settings › Jira issue tab) |

`claudeArgs` (the permissions of the woken session) can only be set in `config.json`: a profile cannot change it.

The token is never written to the registry, the log or the configuration. The quality gate result is read by the daemon from the log
of the Sonar step; the details of the problems are read by the woken Claude session, with the Sonar MCP server it already has.

### Permissions

At startup, the daemon first checks what PR Watch needs: the `claude` command, the Bitbucket credentials and their
scopes (read and write on pull requests and pipelines, read on repositories; read from the `X-OAuth-Scopes` header, without changing anything), your account, and the Jira token. What is missing is
recorded (`permissions.json`), logged, and the dashboard opens a step-by-step **guide** to add it (token creation,
scopes to tick, environment variable to define). You can **decline** an optional permission: the features that depend on it
are then hidden (merging without the write scopes, adding a PR without knowing your account, changing the ticket state
without a Jira token); without repository read access, the daemon no longer tries to read Bitbucket's merge strategy
("Rebase then fast-forward" is proposed). Required: `claude`, the Bitbucket credentials and read access to pull
requests and pipelines; without them a banner stays displayed. The *Settings* tab lists the state of each permission, reopens the
guide and lets you check again. Only your own pull requests can be added by hand.

## Usage

| Command | Purpose |
|---|---|
| `pr-watch register --repo R --pr N --branch B --work-dir D --session S --session-cwd C` | registers a PR (Claude does it for every PR it creates) |
| `pr-watch ensure` | starts the daemon if it is not running (a single instance, never two) |
| `pr-watch ui [--select REPO#N [--pulse]]` | opens the PR Watch window (the web dashboard), on a PR if `--select`; with `--pulse` its card pulses for a moment instead of being marked (what the tray icon does) |
| `pr-watch profile load <file>` | loads a team's settings (Bitbucket workspace, Jira, Sonar, automated review…) from a JSON file, for example `profiles/<team>.json` |
| `pr-watch tray` | starts the tray icon (Linux); a click opens its native menu: the state (daemon, Claude), the first pull requests (5 by default, option `trayMaxPrs` from 1 to 20; those that ask for something first: needs attention, decision needed, attempts exhausted, ready to merge, ticket to validate; then the others in progress, finished ones last; a click on one opens the window on it, which pulses for a moment), a line counting the rest, then "Open the dashboard", "Enable / Disable monitoring" and "Quit PR Watch". Only these lines are clickable; macOS and Windows have no icon, everything goes through the dashboard (`pr-watch ui`) |
| `pr-watch list` / `pr-watch settings --repo R --pr N` | registry state / settings of a PR |
| `pr-watch doctor` | checks what PR Watch needs (tokens, scopes, commands) and guides you through adding what is missing |
| `pr-watch once [--dry-run]` | a single cycle; `--dry-run` shows what would be sent to Claude |
| `pr-watch merge --repo R --pr N [--strategy squash\|merge_commit\|fast_forward] [--keep-branch]` | merge through the API |
| `pr-watch stop` / `unregister` | stops the daemon / removes a PR |
| `pr-watch install-desktop` / `install-launcher` | registers PR Watch as a system application |

What the daemon detects, on each cycle and for each PR:

- a **failed pipeline** (with the summary of the failing step's log);
- a **comment**, including those of the PR author (context to remember, reply to another comment, change
  request); only Claude's replies, signed `claudeReplyMarker` (`— 🤖 Claude`, because they are sent with the author's
  account), are set aside, and an author's comment is never proposed as a question to ask you; and the pipeline's **automated review** (`followBotComments` and `reviewStepPattern`; other application
  accounts, such as staging links, are ignored). When the review step succeeds without posting its comment,
  its verdict is read from the step's log (`reviewStepPattern`);
- a failed **Sonar quality gate** or a completed analysis (`sonarPattern` recognizes the step and the status);
- an **infrastructure outage** (registry authentication, network, runner): Claude is not woken, the pipeline is
  re-run once per commit, then you are notified;
- a PR **ready to merge**: approved, pipelines green on the latest commit, no open task;
- a **merged or declined** PR: it is marked as done.

## Customizing what Claude handles

Each event type has a mode, to choose from:

| Type | Modes (the first is the default) |
|---|---|
| `pipeline`: failed pipelines | `fix` Claude fixes it · `notify` only notify me · `ignore` |
| `comment`: reviewer comments | `fix` · `notify` · `ignore` |
| `question`: comment that asks a question or requests approval | `ask` ask me (yes / no / other) · `fix` · `notify` · `ignore` |
| `review-bot`: automated review | `fix` · `notify` · `ignore` |
| `sonar`: quality gate | `fix` · `notify` · `ignore` |
| `infra`: infrastructure outages | `rerun` re-run once then notify me · `notify` · `ignore` |
| `ready`: PR ready to merge | `notify` · `ignore` |

And **does Claude push its fixes?** (`autoPush`, yes by default; otherwise it stops at the commit and says so in its
report, and you push from the dashboard.)

- **Globally**: *Settings* tab of the dashboard, "What Claude handles…" button, or
  `~/.claude/pr-watch/config.json` (`{"autoPush": false, "handling": {"comment": "notify"}}`).
- **For one PR**, which takes precedence over the global setting: the *Settings…* button on its card, or on the command line:

```sh
pr-watch register … --auto-push no --handling comment=notify,sonar=ignore
pr-watch settings --repo my-repo --pr 12 --handling sonar=default   # default = follow the global setting
pr-watch settings --repo my-repo --pr 12 --reset
```

The daemon waits for the session to be idle (transcript unchanged for 90 s) before waking it, caps at
5 attempts per PR (`maxAttempts`) then notifies you. A merged or declined PR leaves the list `removeDoneAfterMinutes` minutes after it ends (60 by default, `0` to keep it;
*Settings* tab). PR Watch tracks Claude plan usage (5-hour session and week) from the quotas the CLI reports after each reply, without calling the account or reading any token (a one-word request on the cheapest model refreshes them before a wake-up when the measurement is more than 10 minutes old, or with "Refresh" in the badge details) and suspends Claude wake-ups when either has less margin than its threshold (`usageSessionMarginPercent` and `usageWeekMarginPercent`, 5% by default, adjustable separately), so that wake-ups do not eat into overage credits; PR monitoring continues, and the actions normally handed to Claude are temporarily converted into notifications, without changing the saved settings. Wake-ups resume on their own when the quota resets or with "Resume anyway". Threshold, switch (`usageGuard`) and operating hours (`schedule`) are grouped in the *Settings* › *Token savings* tab. Outside the hours, no PR is checked: the interface shows "Daemon stopped by schedule" and the service resumes automatically at the next opening. The daemon button can temporarily force it to resume until the next closing; during a time slot, stopping it offers either resuming at the next scheduled time or resuming manually. The other settings (`pollSeconds`, `minApprovals`, `notify`,
`claudeArgs`, `followBotComments`, `infraPatterns`…) are in `config.json`.

### Waiting for ticket validation

Option `keepUntilTicketValidated` (enabled by default, *Settings* › *General* tab), valid for a manual, automatic or external merge:
a merged PR whose Jira ticket has not changed state stays in the list with the status *Waiting for issue validation*, instead of leaving it
after `removeDoneAfterMinutes`. The ticket state is read at merge time; as soon as it differs (changed from the application, *Issue status…* button, or in Jira),
the PR resumes with the usual delay. When a ticket has several PRs in progress, only the last one merged waits, and the ticket dialog opens
only after it. Without a ticket, with Jira unreachable or the option unchecked, the PR leaves the list as before.

### State proposed for the ticket

Once the PR is merged, the ticket dialog proposes a state according to the ticket type (option `ticketTransitions`, *Settings* › *Jira issue* tab): per ticket type,
because each has its own workflow (no default state: a team profile can provide some, for example `{"Task": "Done"}`; with no state for a type, you choose). The types and the states of each are read from Jira. The state is found by the name of the transition or of the target state, ignoring case and accents
("close" finds "Closed"). Nothing is preselected if no state matches.

### Automatic merge

Disabled by default (`autoMerge`, *Settings* › *General* tab). Two modes:

- `ready`: the PR is merged as soon as its merge conditions are met (approvals, green pipelines, no open task, see "Ready to merge");
- `resolved`: in addition, no comment thread may remain open. Not counted: resolved threads, comments by the PR author,
  the automated review that has "nothing important to report" and positive comments that ask for nothing ("LGTM", "great, thanks"…,
  recognized by `positiveCommentPatterns`; a "but", a question or a change request, `changeRequestPatterns`, makes them count).

The global choice can be overridden per PR (*Settings…* button on the card, or `pr-watch settings --repo R --pr N --auto-merge off|ready|resolved|default`).
The merge uses the strategy set in Bitbucket for the branch (otherwise rebase + fast-forward). It never happens while a reviewer's question
is waiting for your answer or the PR asks for your attention; a refused merge is not retried for the same commit. What it is waiting for is written on the card.

### Reviewer questions

A comment that asks a question or requests approval (it contains a `?`, "valider", "d'accord", "can you
confirm"…: `questionPatterns` in `config.json`, regular expressions) is **not** handed to Claude: you are
notified (urgent), the PR card switches to **❓ Decision needed** and shows the comment with the question
**Handle this? Yes · No · Other [ … ]**. *Yes*: Claude handles the comment on the next cycle; *No*: it is set
aside; *Other*: Claude follows the instruction you write. The `fix` mode of the `question` type restores direct
handling, for one PR or globally.

### The protocol with Claude

The wake-up message explains the event and asks Claude to fix it on its own. The **last line** of the reply is read by the
daemon: `PR-WATCH: OK` (everything is handled) or `PR-WATCH: ATTENTION <sentence>` (you are needed). `ATTENTION` triggers an urgent
notification, colors the icon and is shown on the PR card; a missing line or a failure counts as a
request for attention. The latest report of each PR can be viewed.

The automated review posts a summary comment and one inline comment per remark: only the summary is followed, but
the open remarks posted with it are passed to Claude in the same message, each with its thread to handle.

The report is what Claude believes it did: when it announces that it replied to or resolved a thread, PR Watch checks it on
Bitbucket. A thread announced as resolved that does have its reply but stayed open is resolved by PR Watch; a thread without a reply is
never resolved silently. In both cases where Bitbucket does not show what the report announces, the report says so and the PR
switches to "attention". Questions are never resolved.

## The interfaces

A single window, the same on all systems: the web dashboard. The interface code exists only once
(`prwatch/web/static/`).

- **Web dashboard** (`pr-watch ui`, all systems), opened like an application (Chrome in application mode, separate
  profile): *Pull requests*, *Activity log*, *Settings* tabs. Each card
  shows the state, what blocks the merge, the Claude session and the attempts, and offers: open the session, force a wake-up,
  pause, reset, settings, latest report, push, force push, merge, stop watching. The log is
  colored by type, with repetitions grouped and Claude's reports formatted.
- **Cards**: collapsed to one line by default (the ▸ arrow expands them) with up to 3 small buttons and a "…" menu that
  gives all the other actions. By default, the 3 buttons are the most relevant for the PR's state (merge if it
  is ready, push if there are pending commits, mark as seen if it asks for your attention…); the
  *Settings* tab lets you pin them (`quickActions` in `config.json`). After an action, the daemon re-reads the PR immediately.
- **List bar**: a filter (repository, number, title, branch or ticket; the `/` key puts the cursor in it, Esc clears it),
  the "Needs attention" checkbox (PRs that call for an action: attention, question, ready to merge, attempts exhausted), *Refresh*,
  *Expand all*, *Collapse all*, *Mark all as seen* (when PRs ask for your attention) and *Add a PR*.
- **Refresh**: F5 (or Ctrl+R) makes the daemon re-read the pull requests right away and updates the display, without reloading the
  page. Reloading from the browser menu also works: the page is public, only access to the data requires the token.
- **External links**: a link to another site (the PR on Bitbucket, the permission guides…) opens in the system's default
  browser, not in the PR Watch window; only `http` and `https` addresses are opened.
- **A single window**: `pr-watch ui` (icon, notification, launcher) relaunches the browser, which opens a new window; with
  the activation token of a middle click on the tray icon or of a click on a notification (the GNOME shell passes it on,
  `ProvideXdgActivationToken`), Wayland gives it focus. The window that was already open then closes by itself
  (a `BroadcastChannel` links them): only one remains, the most recent, the one in the foreground. Expanded
  cards are restored. The daemon and the dashboard server restart on their own when the PR Watch code changes.
- **Tray icon** (Linux, GTK): the PR Watch logo with a badge in the bottom right corner, green (daemon active), orange
  (a PR asks for your attention), blue (a PR is ready to merge) or red (daemon stopped, including by the schedule). A click opens
  its native menu: a state line ("🟢 Daemon running · 🟢 Claude enabled"; each dot is green when everything works, orange when
  it is waiting and will restart on its own — schedule, plan almost exhausted —, red when intervention is needed), the 5 pull requests that ask
  for something the most (a click opens the window on the card, which pulses for a moment), a line counting the others, then *Open
  the dashboard*, *Enable / Disable monitoring* and *Quit PR Watch* (closes the window and the icon; the daemon keeps running).
  Only these lines are clickable. `pr-watch install-desktop` registers
  the application in the applications list (search, dock, window switcher); its window carries the name and the
  icon of PR Watch and not those of Chrome or of the application that launched it.
- **State of each PR**: an icon before its name (⚠ attention, ✔ ready to merge, ✖ attempts exhausted, ⏸ paused, ■ done,
  👁 watched) whose tooltip gives the reason. A ⬆ is added when commits of the local branch have not been
  pushed (the *Push* button sends them); the state is read from `origin/<branch>`, without network.
- **Notifications**: title, then repository / number / title of the PR, then details. On Linux (`notify-send` 0.8+), clicking
  on one opens the dashboard on the PR (`pr-watch ui --select REPO#N`). Double-clicking a card opens the PR's log, and the *Latest report* button opens it on Claude's latest report;
  the log shows the last 200 entries and "Show older entries" goes further back. "Archive" empties the log and stores it in `journal-archives/` (10 archives kept).
- **Open session** finds the session's process and the application that hosts it (if none hosts it, a
  window asks where to resume it, only offering the places that really reopen this conversation: Claude Desktop if it knows it, IntelliJ or VS Code with a recent enough Claude plugin, or Claude Code in the default terminal; a line explains why an expected place is missing; the place the session was opened from is recorded when the PR is added to PR Watch, and is offered first with the "Recommended" label): Claude Desktop opens the
  exact conversation (`claude://code/continue?session=local_…`, the identifier Desktop gives the conversation; it cannot
  open a conversation it does not know); the link is opened by the page, so that the application takes focus; IntelliJ and VS Code do too with the
  [Claude Chat](https://github.com/pleday/claudechat) plugin 0.9.8 / 0.9.7 or later, otherwise they bring back the project window. Anthropic's Claude Code plugin for IntelliJ cannot open a given conversation: it is not offered, use "Claude Code (terminal)", which resumes the conversation in the system's default terminal (GNOME setting, otherwise `x-terminal-emulator`).

- **Adding a PR by hand**: the *Add a PR* button asks for the PR address (or `repository#number`), finds its
  title and branch, guesses the local clone and offers, first, "New session" (Claude starts a session for the PR, which will be woken later like
  the others) then the latest Claude sessions (the one carrying the ticket key is preselected); the PR is watched from that moment on, as with `pr-watch register`.
- **Jira ticket** (if `jiraSite` is set): the ticket key (`PROJ-123`) is read from the branch name, otherwise from the PR title. The
  *Issue status…* action (highlighted when the PR is ready, or merged) reads from the ticket the states it can move to, proposes them, and
  prefills the impacts field (`jiraImpactsField`, if there is one) with a suggestion in French (written by `claude -p` from the title and
  the modified files, read from the local repository; a "change and modified areas" template if Claude does not answer) for you to correct: the field is written to the ticket before the transition, as the workflow requires. After a merged PR,
  the state planned for this ticket type (`ticketTransitions`) is preselected; before that, it is up to you to choose. The suggestion is written by Claude: it is not requested while Claude is suspended (plan almost exhausted, no tokens left). A successful merge from the dashboard opens
  this dialog directly.
- **Claude Chat plugins**: PR Watch compares the installed versions (IntelliJ, VS Code) with the plugin's GitHub releases and
  displays a banner when an update exists; the button downloads and installs it (JetBrains plugin folder, or
  `code --install-extension`). The IDE must then be restarted or reloaded. The *Settings* tab lists the versions.

## Security

The dashboard only listens on `127.0.0.1`. A page or call without the **secret token** (in `web.json`, readable by you only) is refused (the token is created once, in `web.token`, readable by you only: the window thus keeps the same address from one restart to the next and there are never two); the `Host` and `Origin` headers are checked (against
DNS rebinding and other web pages); destructive actions (merge, force push, removal, push) require an
explicit confirmation; the content security policy forbids any inline script; what comes from Bitbucket or
the log is always displayed as text. Force push uses `--force-with-lease`.

## Platforms

| | Linux | macOS | Windows |
|---|---|---|---|
| Daemon, registry, merge, settings | tested | ported, untested | ported, untested |
| Detection of the session and its application | tested | tested on recorded `ps` output | tested on recorded PowerShell output |
| Notifications | `notify-send` | `osascript` | PowerShell |
| Web dashboard | tested (Chrome) | untested | untested |
| GTK tray icon | tested | — | — |
| Application in the list | `.desktop` entry | `~/Applications/PR Watch.app` | Start menu shortcut |

Everything that depends on the system is in the `prwatch/system/` package.

## Skills for Claude Code

The project provides skills (`.claude/skills/`) that Claude Code loads in a workspace that adds it:

| Skill | For |
|---|---|
| `pr-watch-setup` | installing the `pr-watch` command (`make link` or pipx), loading the team profile, configuring the tokens (guided by `pr-watch doctor`), starting the daemon and the dashboard |
| `pr-watch-register` | putting a PR under watch right after creating it: `pr-watch register --pr N` guesses the repository, the branch, the clone and the session |
| `pr-watch-wake` | knowing how to handle a daemon wake-up: fix, reply and resolve threads through the API, end with `PR-WATCH: OK` or `ATTENTION` |
| `pr-watch-diagnose` | understanding what does not work: `pr-watch doctor`, logs, common symptoms and fixes |

`pr-watch doctor` shows the state of each permission (✔ / ✖ / ○) and the guide for what is missing; it exits with a non-zero code if
something required is missing.

## Data

The data directory is `~/.claude/pr-watch` (`PR_WATCH_HOME` to change it): `registry.json` (watched PRs,
cursors, settings, latest reports), `config.json`, `daemon.log`, `daemon.pid`, `web.json`, `web.token`, and the tray icon's requests to the
open window (`focus.json`, `quit.json`).

## Development

```sh
make test      # unit and integration tests (unittest, no dependency)
make e2e       # dashboard in a real Chrome (see e2e/README.md)
```

```
prwatch/
  cli.py            the command line (`pr-watch …`)
  core/             the foundations
    store.py          data directory, log, registry of watched PRs
    config.py         settings: what is done for each event type, global and per-PR options
    bitbucket.py      the Bitbucket API (credentials, calls)
    permissions.py    required permissions: startup check, guides, hidden features
    journal.py        log reading (entry types, grouped repetitions, Markdown of reports)
  engine/           what the daemon does
    cycle.py          one pass over each PR: what happened, what to do
    events.py         detection: pipelines, comments, automated review, Sonar
    wake.py           waking the Claude session: message, execution, verdict, report verification
    merging.py        merging a PR, strategies, PR ready to merge, removal of finished PRs
    registration.py   adding a PR to the list
    service.py        the processes: daemon loop, dashboard server, tray icon
  integrations/     the services around Bitbucket
    jira.py           a PR's Jira ticket: possible states, impacts, transition
    plugins.py        installed versions and updates of the Claude Chat plugins
  system/           everything that depends on the system (Linux, macOS, Windows)
    host.py           the system, locks, detached processes, environment of child processes
    notifications.py  notifications and opening links
    terminal.py       the default terminal
    processes.py      process table, application that hosts a Claude session
    claude.py         transcripts, titles and sessions of Claude Code and Claude Desktop
    editors.py        IntelliJ, VS Code and their Claude plugins
    opening.py        opening a conversation in the suitable application
    window.py         the dashboard window (browser in application mode)
    desktop.py        PR Watch as a system application (applications list, launcher)
  actions/          what the interfaces can ask for, and the texts they display in common
    status.py         state of each PR, relevant buttons, dashboard state
    forms.py          merge forms, confirmations, settings
    pr.py             actions on a PR: pause, answer to a question, wake-up, push, merge, settings
    sessions.py       opening the session in an application or a terminal
    tickets.py        Jira ticket
    adding.py         adding a PR by hand
    options.py        options, team profiles, daemon, permissions, plugins, links
    common.py         what they share; dispatch.py: the action table
  web/              the dashboard
    server.py         server (stdlib, loopback, token)
    static/           the page: index.html, style.css, icon.svg and the JavaScript modules of js/
      js/core.js        base: token, shared state, icons, server calls, dialogs
      js/cards.js       a PR's card: state, relevant buttons, "…" menu, details
      js/dialogs.js     dialogs: open the session, ticket, add, merge, push, settings
      js/journal.js     the Activity log tab
      js/options.js     the Settings tab: options, permissions and their guides, plugins
      js/app.js         entry point: draws the server state, tabs, refresh
  tray/             the tray icon (GTK 3, Linux), the only part outside the dashboard
    app.py            the icon and its native menu (D-Bus: StatusNotifierItem and dbusmenu)
    summary.py        the menu content: the state, the PRs to handle, the note about the others
    icon.py           its image: the logo and the state badge, drawn without dependencies
profiles/           ready-to-load team settings (JSON), outside the installed package
tests/              unittest; each test has its own data directory
e2e/                end-to-end test with Playwright
```

Known limitations: if the Bitbucket token lacks the `read:repository` scope, commit statuses are not read (those
of the PR are) and the default merge strategy configured in Bitbucket is not known (the merge dialog then proposes
"Rebase then fast-forward" and says so, with an ℹ icon telling you to change the token); Wayland forbids bringing a terminal's window back to the front from another application.

## License

MIT: see [LICENSE](LICENSE).
