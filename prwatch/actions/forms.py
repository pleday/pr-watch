"""The texts and forms the screens show in common: merge, confirmations, settings of a pull request."""

from prwatch.core import config as pwconfig

MERGE_STRATEGIES = (
    ('squash', 'Squash (un seul commit)'), ('merge_commit', 'Commit de merge'), ('fast_forward', 'Fast-forward'),
    ('squash_fast_forward', 'Squash puis fast-forward'), ('rebase_fast_forward', 'Rebase puis fast-forward'), ('rebase_merge', 'Rebase puis commit de merge'),
)


# Proposed when the strategy set in Bitbucket cannot be read.
FALLBACK_STRATEGY = 'rebase_fast_forward'


MERGE_STRATEGY_IDS = tuple(key for key, _ in MERGE_STRATEGIES)


# What the PR settings dialog offers for "does Claude push?"; the id goes back to `settings_from_form`.
_PUSH_CHOICES = (('yes', 'Oui : Claude pousse'), ('no', 'Non : il s’arrête au commit'))


def strategy_choices(entry):
    """The merge strategies to offer and the one to preselect: what Bitbucket set for the destination branch, when known."""
    allowed = entry.get('mergeAllowed') or []
    labels = dict(MERGE_STRATEGIES)
    offered = [{'id': key, 'label': label} for key, label in MERGE_STRATEGIES if not allowed or key in allowed]
    offered += [{'id': key, 'label': key} for key in allowed if key not in labels]
    ids = [s['id'] for s in offered]
    default = entry.get('mergeDefault')
    if default in ids:
        return {'strategies': offered, 'defaultStrategy': default, 'strategyNote': 'Stratégie par défaut configurée dans Bitbucket.', 'strategyHint': None}
    fallback = next((key for key in (FALLBACK_STRATEGY, 'squash') if key in ids), ids[0])
    return {'strategies': offered, 'defaultStrategy': fallback,
            'strategyNote': f"Stratégie par défaut de Bitbucket illisible : {labels.get(fallback, fallback)} proposé.",
            'strategyHint': 'Pour lire la stratégie configurée dans Bitbucket, il faut modifier le jeton Bitbucket (BITBUCKET_TOKEN) '
                            'et lui ajouter la portée « read:repository », puis relancer le démon.'}


def merge_form(entry):
    blockers = entry.get('mergeBlockers') or []
    ready = bool(entry.get('ready'))
    return {
        'title': 'Merger la PR',
        'heading': f"{entry['repo']} #{entry['pr']} — {entry.get('title') or entry['branch']}",
        'intro': 'Approuvée et pipelines au vert.' if ready else 'Pas encore prête : ' + (', '.join(blockers) or 'état inconnu') + '.',
        'ready': ready,
        **strategy_choices(entry),
        'closeLabel': 'Supprimer la branche source après le merge',
        'warning': 'Le merge est définitif : il ne peut pas être annulé depuis cette fenêtre.',
    }


def confirmations(entry, cfg):
    """The question asked before each risky action: {'title', 'body', 'label' of the button, 'destructive'}."""
    key = f"{entry['repo']} #{entry['pr']}"
    done = entry.get('state') == 'done'
    return {
        'wake': {
            'title': 'Forcer un réveil ?', 'label': 'Forcer', 'destructive': False,
            'body': f"La session {entry['sessionId'][:8]}… sera relancée au prochain cycle du démon (≤ {cfg['pollSeconds']} s), "
                    'même si elle est occupée ou si les essais sont épuisés, pour examiner la PR maintenant.',
        },
        'push': {
            'title': 'Pousser la branche ?', 'label': 'Pousser', 'destructive': False,
            'body': f"git push origin {entry['branch']}\ndepuis {entry['workDir']}",
        },
        'fetch': {
            'title': 'Récupérer la branche ?', 'label': 'Récupérer', 'destructive': False,
            'body': f"La branche {entry['branch']} a avancé sur origin : quelqu’un a poussé un commit que ce clone n’a pas.\n"
                    f"git fetch origin {entry['branch']}\ndepuis {entry['workDir']}\n"
                    'Seule la copie d’origin est mise à jour : la branche locale et les fichiers ne changent pas.',
        },
        'rebase': {
            'title': 'Rebaser la branche ?', 'label': 'Rebaser', 'destructive': False,
            'body': f"Origin a des commits que {entry['branch']} n’a pas : le push serait refusé.\n"
                    f"git rebase origin/{entry['branch']}\ndepuis {entry['workDir']}\n"
                    'Vos commits non poussés sont rejoués par-dessus. En cas de conflit, le rebase est annulé et rien ne change.',
        },
        'force-push': {
            'title': 'Pousser en force — action destructrice', 'label': 'Pousser en force', 'destructive': True,
            'body': f"Réécrit l’historique de origin/{entry['branch']} avec le contenu local de {entry['workDir']}.\n"
                    'Utilise --force-with-lease : refusé si quelqu’un d’autre a poussé depuis votre dernière récupération.\n'
                    'Les relecteurs de la PR verront l’historique changer.',
        },
        'unwatch': {
            'title': 'Retirer de la liste ?' if done else 'Arrêter la surveillance ?', 'label': 'Retirer' if done else 'Arrêter', 'destructive': True,
            'body': f'{key} sera retirée du démon. La PR et la session ne sont pas modifiées.',
        },
    }


def settings_form(entry, cfg, categories):
    """The per-PR settings dialog: each row has the id now chosen ('' / 'default' = follow the global choice) and its choices."""
    own = entry.get('settings') or {}
    push = {True: 'yes', False: 'no'}.get(own.get('autoPush'), 'default')
    rows = []
    for name, (label, modes) in categories.items():
        default_label = dict(modes)[cfg['handling'][name]]
        rows.append({
            'id': name, 'label': label, 'value': (own.get('handling') or {}).get(name) or '',
            'choices': [{'id': '', 'label': f'Par défaut ({default_label})'}, *({'id': m, 'label': ml} for m, ml in modes)],
        })
    return {
        'title': f"Réglages de {entry['repo']} #{entry['pr']}",
        'intro': 'Ces choix ne concernent que cette PR. « Par défaut » suit les réglages globaux.',
        'autoPush': {
            'label': 'Claude pousse ses corrections sur la branche', 'value': push,
            'choices': [{'id': 'default', 'label': f"Par défaut ({'oui' if cfg['autoPush'] else 'non'})"}, *({'id': k, 'label': v} for k, v in _PUSH_CHOICES)],
        },
        'autoMerge': {
            'label': 'Merge automatique', 'value': own.get('autoMerge') or '',
            'choices': [{'id': '', 'label': f"Par défaut ({dict(pwconfig.AUTO_MERGE_MODES)[cfg['autoMerge']].lower()})"}, *({'id': m, 'label': ml} for m, ml in pwconfig.AUTO_MERGE_MODES)],
        },
        'categories': rows,
    }


def handling_form(cfg, categories):
    """The dialog of the global choices: one row per kind of event, no 'follow the global choice' entry."""
    return {
        'intro': 'Ce que le démon fait de chaque type d’événement, pour toutes les PR qui n’ont pas leurs propres réglages.',
        'categories': [
            {'id': name, 'label': label, 'value': cfg['handling'][name], 'choices': [{'id': m, 'label': ml} for m, ml in modes]}
            for name, (label, modes) in categories.items()
        ],
    }


def settings_from_form(push, handling, merge=''):
    """The answer of the PR settings dialog turned into what `pr_settings` takes (None = follow the global choice)."""
    return {'autoPush': {'yes': True, 'no': False}.get(push), 'autoMerge': merge or None, 'handling': {name: mode or None for name, mode in handling.items()}}
