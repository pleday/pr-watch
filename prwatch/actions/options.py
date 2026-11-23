"""The options of PR Watch, the daemon, the permissions, the plugins and the links to other sites."""

import json
import re
import subprocess
from pathlib import Path
import urllib.parse

from prwatch.actions import common as pwcommon
from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.core import permissions as pwperm
from prwatch.integrations import plugins as pwplugins
from prwatch.integrations import usage as pwusage
from prwatch.actions import status as pwstatus
from prwatch.core import store as pwstore
from prwatch.engine import merging as pwmerging
from prwatch.engine import service as pwservice
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications

# The options a front end may change, with their type and bounds: nothing else reaches config.json.
OPTIONS = {
    'autoPush': ('bool',),
    'keepUntilTicketValidated': ('bool',),
    'autoMerge': ('choice', [m for m, _ in pwconfig.AUTO_MERGE_MODES]),
    'notify': ('bool',),
    'pollSeconds': ('int', 15, 900),
    'maxAttempts': ('int', 1, 20),
    'minApprovals': ('int', 0, 10),
    'usageGuard': ('bool',),
    'usageSessionMarginPercent': ('int', 0, 50),
    'usageWeekMarginPercent': ('int', 0, 50),
    'trayMaxPrs': ('int', 1, 20),  # pull requests listed in the menu of the tray icon
    'removeDoneAfterMinutes': ('int', 0, 43200),  # 0 = a finished PR stays in the list
    # What depends on the team (Bitbucket workspace, Jira site and field, quality tool, where the clones are): see `validate_text`.
    'bitbucketWorkspace': ('text', 'workspace'),
    'jiraSite': ('text', 'site'),
    'jiraImpactsField': ('text', 'free'),
    'sonarPattern': ('text', 'regex'),
    'cloneFolders': ('lines', 20),
    'reviewStepPattern': ('text', 'regex'),
    'infraPatterns': ('lines', 40),
}

# What a profile (a file of team settings, see `load_profile`) may set: nothing that widens what Claude is allowed to do.
PROFILE_OPTIONS = ('bitbucketWorkspace', 'jiraSite', 'jiraImpactsField', 'sonarPattern', 'reviewStepPattern', 'followBotComments',
                   'infraPatterns', 'ticketTransitions')


def validate_bot_rules(value):
    """Why `value` is not a usable list of followed bot comments ({'author', 'startsWith', 'ignoreIfContains'}), '' when it is."""
    if not isinstance(value, list) or len(value) > 20:
        return 'une liste de 20 règles au plus est attendue'
    for rule in value:
        if not isinstance(rule, dict) or not isinstance(rule.get('author'), str) or not rule['author'].strip() or set(rule) - {'author', 'startsWith', 'ignoreIfContains'}:
            return 'chaque règle a un auteur et, au choix, startsWith et ignoreIfContains'
        if any(not isinstance(v, str) or len(v) > 300 for v in rule.values()):
            return 'texte trop long'
    return ''


def validate_text(kind, value):
    """(clean value, '') or (None, why not) for a text option."""
    if not isinstance(value, str):
        return None, 'texte attendu'
    value = value.strip()
    if len(value) > 300:
        return None, 'texte trop long'
    if kind == 'workspace' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        return None, 'nom d’espace Bitbucket attendu (lettres, chiffres, - _ .)'
    if kind == 'site' and value:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme != 'https' or not parsed.netloc:
            return None, 'adresse https attendue (https://votre-site.atlassian.net)'  # the Jira token goes there: never in clear
        value = value.rstrip('/')
    if kind == 'regex':
        try:
            re.compile(value)
        except re.error as error:
            return None, f'expression régulière invalide ({error})'
    return value, ''


def usage_refresh():
    """Measures the quotas again through the CLI (a request of one word on the cheapest model)."""
    usage = pwusage.probe(pwconfig.config()['claudeBinary'])
    return pwcommon._result(usage is not None, 'Consommation mise à jour.' if usage else 'La CLI Claude n’a pas donné les quotas : réessayez dans un instant.')


def resume():
    """Turns PR Watch back on after the session ran out of tokens (also to try again before the limit is lifted)."""
    return pwcommon._result(True, 'PR Watch réactivé.' if pwstore.resume() else 'PR Watch n’était pas désactivé.')


def daemon_command(command):
    """`ensure` or `stop` of the daemon, run as the user would from a terminal."""
    if command not in ('ensure', 'stop', 'force-schedule', 'pause-schedule'):
        return pwcommon._result(False, 'Commande inconnue.')
    if command in ('force-schedule', 'pause-schedule'):
        mode = 'active' if command == 'force-schedule' else 'paused'
        until = pwconfig.next_schedule_boundary(pwconfig.config(), mode)
        if until is None:
            return pwcommon._result(False, 'Aucun prochain horaire disponible.')
        pwstore.set_schedule_override(mode, until)
        if command == 'pause-schedule':
            pwservice.request_cycle()
            return pwcommon._result(True, 'Surveillance mise en attente jusqu’au prochain horaire.')
        if pwservice.is_running():
            pwservice.request_cycle()
        command = 'ensure'
    done = subprocess.run(pwhost.module_command(command), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, env=pwhost.child_env())
    return pwcommon._result(done.returncode == 0, (done.stdout + done.stderr).strip() or 'ok')


def set_options(values):
    clean = {}
    for key, value in values.items():
        if key == 'handling':
            try:
                clean['handling'] = {**pwconfig.config()['handling'], **{k: v for k, v in pwconfig.validate_handling(value).items()}}
            except ValueError as error:
                return pwcommon._result(False, f'handling : {error}')
            continue
        if key == 'ticketTransitions':
            if (error := pwconfig.validate_ticket_transitions(value)):
                return pwcommon._result(False, f'États des tickets : {error}')
            clean[key] = {k.strip(): v.strip() for k, v in value.items()}  # the keys are the types of ticket, nothing else
            continue
        if key == 'followBotComments':
            if (error := validate_bot_rules(value)):
                return pwcommon._result(False, f'Commentaires suivis : {error}')
            clean[key] = value
            continue
        if key == 'schedule':
            try:
                clean['schedule'] = pwconfig.validate_schedule(value)
            except ValueError as error:
                return pwcommon._result(False, f'Horaires : {error}')
            continue
        if key == 'quickActions' and value is None:
            clean[key] = None  # automatic
            continue
        if key == 'quickActions':
            known = [k for k, _ in pwstatus.QUICK_ACTIONS]
            if not isinstance(value, list) or len(value) > pwstatus.MAX_QUICK_ACTIONS or len(set(value)) != len(value) or any(v not in known for v in value):
                return pwcommon._result(False, f'quickActions : {pwstatus.MAX_QUICK_ACTIONS} actions différentes au plus')
            clean[key] = value
            continue
        spec = OPTIONS.get(key)
        if not spec:
            return pwcommon._result(False, f'Option inconnue : {key}')
        if spec[0] == 'choice':
            if value not in spec[1]:
                return pwcommon._result(False, f"{key} : l'un de {', '.join(spec[1])} attendu")
            clean[key] = value
        elif spec[0] == 'text':
            text, why = validate_text(spec[1], value)
            if why:
                return pwcommon._result(False, f'{key} : {why}')
            clean[key] = text
        elif spec[0] == 'lines':
            if not isinstance(value, list) or len(value) > spec[1] or any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in value):
                return pwcommon._result(False, f'{key} : {spec[1]} lignes au plus, une valeur par ligne')
            clean[key] = [v.strip() for v in value]
        elif spec[0] == 'bool':
            if not isinstance(value, bool):
                return pwcommon._result(False, f'{key} : vrai ou faux attendu')
            clean[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int) or not spec[1] <= value <= spec[2]:
                return pwcommon._result(False, f'{key} : entier entre {spec[1]} et {spec[2]} attendu')
            clean[key] = value
    schedule_override = pwstore.schedule_override() if 'schedule' in clean else None
    current = json.loads(pwstore.CONFIG.read_text(encoding='utf-8')) if pwstore.CONFIG.exists() else {}
    current.update(clean)
    pwstore.CONFIG.write_text(json.dumps(current, indent=2), encoding='utf-8')
    if 'schedule' in clean:
        if schedule_override and clean['schedule']['enabled']:
            until = pwconfig.next_schedule_boundary(current, schedule_override['mode'])
            if until is not None:
                pwstore.set_schedule_override(schedule_override['mode'], until)
            else:
                pwstore.clear_schedule_override()
        else:
            pwstore.clear_schedule_override()
        pwservice.request_cycle()
    return pwcommon._result(True, 'Réglage enregistré, pris en compte au prochain cycle du démon.')


def load_profile(profile):
    """Applies a profile of team settings (see PROFILE_OPTIONS): the path of a JSON file, or its decoded content.

    The file looks like {"name": ..., "options": {...}}; the options it lists replace the current ones, the others stay as they are.
    """
    try:
        if isinstance(profile, str):
            path = Path(profile).expanduser()
            if path.stat().st_size > 100_000:
                return pwcommon._result(False, 'Profil trop gros.')
            profile = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return pwcommon._result(False, f'Profil illisible : {error}')
    options = profile.get('options') if isinstance(profile, dict) else None
    if not isinstance(options, dict) or not options:
        return pwcommon._result(False, 'Profil invalide : un objet « options » est attendu.')
    unknown = [key for key in options if key not in PROFILE_OPTIONS]
    if unknown:
        return pwcommon._result(False, f"Profil refusé, réglage non autorisé : {', '.join(map(str, unknown))}.")
    result = set_options(options)
    name = profile.get('name') if isinstance(profile.get('name'), str) else ''
    return {**result, 'message': f"Profil{' ' + name if name else ''} chargé : {len(options)} réglages." if result['ok'] else result['message']}


def refresh():
    """Reads the title and the merge readiness of every watched pull request now (F5), in parallel, and wakes the daemon too.

    The daemon may be in the middle of a slow cycle (Bitbucket timeouts): the list must not wait for it.
    """
    from concurrent.futures import ThreadPoolExecutor

    cfg = pwconfig.config()
    with pwstore.registry_lock():
        entries = [e for e in pwstore.load_registry() if e.get('state', 'watching') == 'watching']

    def look(entry):
        try:
            pr = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}")
            if pr and pr.get('state') == 'OPEN':
                if entry.get('title') != pr['title']:
                    pwstore.update_entry(entry, title=pr['title'])
                pwmerging.sync_ready(entry, pr, cfg, False, announce=False)
        except Exception as error:  # one PR must not stop the others
            pwstore.log(f'{pwstore.key_of(entry)}: refresh: {type(error).__name__}: {error}')

    if entries:
        with ThreadPoolExecutor(max_workers=min(4, len(entries))) as pool:
            list(pool.map(look, entries))
    return pwcommon._result(True, 'Pull requests actualisées.')


def open_link(url):
    """Opens a web address in the default browser of the system (the dashboard window is a browser of its own)."""
    parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in ('http', 'https') or not parsed.netloc or len(url) > 2000:
        return pwcommon._result(False, 'Seules les adresses http et https sont ouvertes.')
    pwnotifications.open_url(url)
    return pwcommon._result(True, '')


def permissions_check():
    """Checks the tokens and rights again (the answer of the dashboard's "Revérifier")."""
    cfg = pwconfig.config()
    return pwcommon._result(True, '', permissions=pwperm.summary(pwperm.check_and_save(cfg['jiraSite'], cfg['claudeBinary'])))


def permissions_decline(ids):
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return pwcommon._result(False, 'Demande invalide.')
    return pwcommon._result(True, 'Fonctions masquées.', permissions=pwperm.summary(pwperm.decline(ids)))


def plugins_status():
    """The Claude Chat plugins installed on the machine and whether a newer release exists."""
    return pwcommon._result(True, '', plugins=pwplugins.status())


def plugins_update(kind):
    if not isinstance(kind, str) or kind not in pwplugins.TAGS:
        return pwcommon._result(False, 'Plugin inconnu.')
    ok, message = pwplugins.update(kind)
    return pwcommon._result(ok, message, plugins=pwplugins.status())
