"""What PR Watch is allowed to do with the accounts of the user: checked when the daemon starts, explained when missing.

Each check says whether something PR Watch needs is available (a token, a scope, a command). A missing one has a guide that
tells the user how to add it; the user may decline, and the functions that need it are then hidden (`hidden`). The checks
marked `required` are the minimum without which watching a pull request cannot work.

Bitbucket tells the scopes of an API token in the `X-OAuth-Scopes` header of every answer, even a refused one, so the
scopes are read without changing anything. Jira is probed with a read-only call.
"""
import base64
import json
import os
import shutil
import time
import urllib.error
import urllib.request

from prwatch.integrations import jira as pwjira
from prwatch.core import store as pwstore

BITBUCKET = 'https://api.bitbucket.org/2.0'

# id -> what it is for. `features` are the functions hidden when it is declined (see FEATURE_LABELS).
CHECKS = (
    {'id': 'claude', 'label': 'Commande claude (Claude Code)', 'required': True, 'features': [],
     'why': 'Réveiller la session de la PR et en créer de nouvelles.'},
    {'id': 'bitbucket-credentials', 'label': 'Bitbucket : identifiants', 'required': True, 'features': [],
     'why': 'Lire les pull requests, les pipelines et les commentaires.'},
    {'id': 'bitbucket-read', 'label': 'Bitbucket : lecture des pull requests et des pipelines', 'required': True, 'features': [],
     'why': 'Détecter les pipelines en échec, les commentaires et l’état des PR.'},
    {'id': 'bitbucket-write', 'label': 'Bitbucket : écriture des pull requests et des pipelines', 'required': False, 'features': ['merge'],
     'why': 'Merger une PR, relancer un pipeline, résoudre un fil de commentaire.'},
    {'id': 'bitbucket-repository', 'label': 'Bitbucket : lecture des dépôts', 'required': False, 'features': [],
     'why': 'Lire la stratégie de merge par défaut de Bitbucket (sinon « Rebase puis fast-forward » est proposé).'},
    {'id': 'identity', 'label': 'Votre identité (pour ne retenir que vos PR)', 'required': False, 'features': ['add-pr'],
     'why': 'Ajouter une PR à la main : seules celles dont vous êtes l’auteur sont acceptées.'},
    {'id': 'jira', 'label': 'Jira : jeton d’API Atlassian', 'required': False, 'features': ['ticket'],
     'why': 'Changer l’état du ticket d’une PR et renseigner les impacts.'},
)
FEATURE_LABELS = {'merge': 'merger une PR', 'add-pr': 'ajouter une PR à la main', 'ticket': 'changer l’état du ticket Jira'}

ATLASSIAN_TOKENS = 'https://id.atlassian.com/manage-profile/security/api-tokens'
ENVIRONMENT = (
    'Définissez la variable dans l’environnement du démon et du tableau de bord (par exemple dans ~/.profile, ou dans '
    '~/.config/environment.d/pr-watch.conf pour un lancement depuis le bureau), reconnectez-vous ou ouvrez un nouveau terminal, '
    'puis relancez : pr-watch stop, pr-watch ensure.'
)
GUIDES = {
    'claude': {
        'title': 'Installer Claude Code',
        'steps': ['Installez Claude Code (commande `claude`) en suivant la documentation.', 'Vérifiez avec `claude --version`, puis redémarrez le démon.'],
        'links': [{'label': 'Installer Claude Code', 'url': 'https://docs.claude.com/en/docs/claude-code/setup'}],
    },
    'bitbucket-credentials': {
        'title': 'Définir BITBUCKET_EMAIL et BITBUCKET_TOKEN',
        'steps': [
            'Ouvrez la page des jetons d’API Atlassian et choisissez « Créer un jeton d’API avec des portées ».',
            'Application : Bitbucket. Portées : read:pullrequest:bitbucket, write:pullrequest:bitbucket, read:pipeline:bitbucket, '
            'write:pipeline:bitbucket, read:user:bitbucket, read:repository:bitbucket.',
            'Copiez le jeton (il ne s’affiche qu’une fois) dans BITBUCKET_TOKEN, et l’adresse e-mail de votre compte Atlassian dans BITBUCKET_EMAIL.',
            ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
    'bitbucket-read': {
        'title': 'Ajouter les portées de lecture au jeton Bitbucket',
        'steps': [
            'Créez un nouveau jeton d’API avec des portées pour Bitbucket (un jeton existant ne peut pas être modifié).',
            'Cochez au minimum read:pullrequest:bitbucket et read:pipeline:bitbucket.',
            'Mettez-le dans BITBUCKET_TOKEN. ' + ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
    'bitbucket-write': {
        'title': 'Ajouter les portées d’écriture au jeton Bitbucket',
        'steps': [
            'Créez un nouveau jeton d’API avec des portées pour Bitbucket.',
            'Ajoutez write:pullrequest:bitbucket (merge, commentaires) et write:pipeline:bitbucket (relance des pipelines) à la lecture.',
            'Mettez-le dans BITBUCKET_TOKEN. ' + ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
    'bitbucket-repository': {
        'title': 'Ajouter la portée de lecture des dépôts au jeton Bitbucket',
        'steps': [
            'Créez un nouveau jeton d’API avec des portées pour Bitbucket.',
            'Ajoutez read:repository:bitbucket aux autres portées : PR Watch lit alors la stratégie de merge configurée pour la branche de destination et la propose par défaut.',
            'Mettez-le dans BITBUCKET_TOKEN. ' + ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
    'identity': {
        'title': 'Permettre à PR Watch de connaître votre compte',
        'steps': [
            'Soit ajoutez la portée read:user:bitbucket à un nouveau jeton Bitbucket (BITBUCKET_TOKEN).',
            'Soit définissez ATLASSIAN_API_TOKEN (voir « Jira ») : PR Watch en déduit votre compte.',
            ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
    'jira': {
        'title': 'Définir ATLASSIAN_API_TOKEN',
        'steps': [
            'Ouvrez la page des jetons d’API Atlassian et choisissez « Créer un jeton d’API » (sans portées).',
            'Copiez le jeton dans ATLASSIAN_API_TOKEN ; il s’utilise avec BITBUCKET_EMAIL, qui doit être l’adresse de votre compte Atlassian.',
            'Votre compte doit avoir le droit de modifier et de faire avancer les tickets du projet.',
            ENVIRONMENT,
        ],
        'links': [{'label': 'Jetons d’API Atlassian', 'url': ATLASSIAN_TOKENS}],
    },
}


def _file():
    return pwstore.DIR / 'permissions.json'


def _bitbucket(path):
    """(status, scopes, body) of a Bitbucket call; status 0 when it could not be made (network, credentials missing)."""
    email, token = os.environ.get('BITBUCKET_EMAIL'), os.environ.get('BITBUCKET_TOKEN')
    if not email or not token:
        return 0, None, None
    auth = 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode()
    request = urllib.request.Request(BITBUCKET + path, headers={'Authorization': auth})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, _scopes(response.headers), json.loads(response.read().decode('utf-8', 'replace') or 'null')
    except urllib.error.HTTPError as error:
        return error.code, _scopes(error.headers), None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None, None


def _scopes(headers):
    value = headers.get('X-OAuth-Scopes')
    return {s.strip() for s in value.split(',') if s.strip()} if value else None


def _jira_identity(site):
    """The Atlassian account id of the user, from Jira (None when Jira cannot be called)."""
    try:
        return (pwjira.request(site, 'GET', '/myself') or {}).get('accountId')
    except pwjira.JiraError:
        return None


_IDENTITY = {'at': 0.0, 'value': None}


def identity(site=None):
    """{'account_id', 'uuid'} of the user, read from Bitbucket (`read:user` scope) or else from Jira; None when unknown."""
    if _IDENTITY['value'] and time.time() - _IDENTITY['at'] < 3600:
        return _IDENTITY['value']
    status, _, body = _bitbucket('/user')
    found = None
    if status == 200 and body:
        found = {'account_id': body.get('account_id'), 'uuid': body.get('uuid')}
    elif site:
        account = _jira_identity(site)
        found = {'account_id': account, 'uuid': None} if account else None
    _IDENTITY.update(at=time.time(), value=found)
    return found


def is_mine(pr, site=None):
    """Whether the user wrote the pull request `pr` (the answer of Bitbucket); None when the user's account is unknown."""
    me = identity(site)
    author = pr.get('author') or {}
    if not me:
        return None
    return bool((me.get('account_id') and me['account_id'] == author.get('account_id')) or (me.get('uuid') and me['uuid'] == author.get('uuid')))


def run(site, claude='claude'):
    """Runs every check: a list of {'id', 'label', 'required', 'why', 'features', 'ok', 'detail'}."""
    status, scopes, _ = _bitbucket('/user')
    has_env = bool(os.environ.get('BITBUCKET_EMAIL') and os.environ.get('BITBUCKET_TOKEN'))
    _IDENTITY.update(at=0.0, value=None)
    me = identity(site)
    results = {}
    results['claude'] = (bool(shutil.which(claude)), 'trouvée' if shutil.which(claude) else f'« {claude} » introuvable dans le PATH')
    if not has_env:
        results['bitbucket-credentials'] = (False, 'BITBUCKET_EMAIL ou BITBUCKET_TOKEN absent de l’environnement')
    elif status == 401:
        results['bitbucket-credentials'] = (False, 'Bitbucket refuse ces identifiants (401)')
    elif status == 0:
        results['bitbucket-credentials'] = (False, 'Bitbucket injoignable : identifiants non vérifiés')
    else:
        results['bitbucket-credentials'] = (True, 'acceptés par Bitbucket')
    authenticated = results['bitbucket-credentials'][0]
    for check, needed in (('bitbucket-read', {'read:pullrequest:bitbucket', 'read:pipeline:bitbucket'}), ('bitbucket-write', {'write:pullrequest:bitbucket', 'write:pipeline:bitbucket'}),
                          ('bitbucket-repository', {'read:repository:bitbucket'})):
        if not authenticated:
            results[check] = (False, 'identifiants Bitbucket à corriger d’abord')
        elif scopes is None:
            results[check] = (True, 'portées non lisibles : supposées suffisantes')
        elif needed <= scopes:
            results[check] = (True, 'portées présentes')
        else:
            results[check] = (False, 'portées manquantes : ' + ', '.join(sorted(needed - scopes)))
    results['identity'] = (bool(me), 'compte reconnu' if me else 'compte inconnu : ni la portée read:user:bitbucket ni Jira ne l’indiquent')
    jira_ok = False
    if not os.environ.get('ATLASSIAN_API_TOKEN'):
        jira_detail = 'ATLASSIAN_API_TOKEN absent de l’environnement'
    else:
        jira_ok = _jira_identity(site) is not None
        jira_detail = 'accepté par Jira' if jira_ok else 'Jira refuse ce jeton ou est injoignable'
    results['jira'] = (jira_ok, jira_detail)
    return [{**{k: c[k] for k in ('id', 'label', 'required', 'why', 'features')}, 'ok': results[c['id']][0], 'detail': results[c['id']][1]} for c in CHECKS]


def describe(checks):
    """The checks as text for a terminal: one line each, then the guide of every one that is missing."""
    lines = []
    for check in checks:
        mark = '✔' if check['ok'] else ('✖' if check['required'] else '○')
        lines.append(f"{mark} {check['label']} — {check['detail']}")
    for check in checks:
        guide = GUIDES.get(check['id'])
        if check['ok'] or not guide:
            continue
        lines += ['', f"{guide['title']} ({'indispensable' if check['required'] else 'facultatif'})"]
        lines += [f'  {number}. {step}' for number, step in enumerate(guide['steps'], 1)]
        lines += [f"  {link['label']} : {link['url']}" for link in guide['links']]
    if all(c['ok'] for c in checks):
        lines += ['', 'Tout est en place.']
    return '\n'.join(lines)


def load():
    """{'at', 'checks', 'declined'} as last saved (empty when never checked)."""
    try:
        return json.loads(_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'at': None, 'checks': [], 'declined': []}


def _save(data):
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(data, indent=2), encoding='utf-8')


def check_and_save(site, claude='claude'):
    """Runs the checks and saves them; a declined permission that is now granted is forgotten."""
    checks = run(site, claude)
    failing = {c['id'] for c in checks if not c['ok']}
    declined = [d for d in load().get('declined', []) if d in failing]
    data = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'checks': checks, 'declined': declined}
    _save(data)
    return data


def decline(ids):
    data = load()
    known = {c['id'] for c in CHECKS}
    data['declined'] = sorted(set(data.get('declined', [])) | {i for i in ids if i in known})
    _save(data)
    return data


def granted(check_id):
    """Whether a permission was found in place at the last check: True or False, None when it was never checked."""
    found = next((c for c in load().get('checks', []) if c['id'] == check_id), None)
    return None if found is None else bool(found['ok'])


def hidden_features(data=None):
    """The functions to hide: those that need a permission the user declined and that is still missing."""
    data = data or load()
    failing = {c['id']: c for c in data.get('checks', []) if not c['ok']}
    return sorted({f for i in data.get('declined', []) if i in failing for f in failing[i]['features']})


def summary(data=None):
    """What the dashboard shows: the checks with their guide, what is still to decide, what is hidden."""
    data = data or load()
    checks = [{**c, 'declined': c['id'] in data.get('declined', []), 'guide': GUIDES.get(c['id'])} for c in data.get('checks', [])]
    return {
        'at': data.get('at'),
        'checks': checks,
        'undecided': [c['id'] for c in checks if not c['ok'] and not c['declined']],
        'blocking': [c['id'] for c in checks if not c['ok'] and c['required']],
        'hidden': hidden_features(data),
        'featureLabels': FEATURE_LABELS,
    }
