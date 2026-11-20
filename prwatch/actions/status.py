"""The state shown for each pull request: its chip, what is waiting to be pushed, which buttons fit it, and the state of the whole dashboard."""

import math
import os
import subprocess
import time

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.actions import forms as pwforms
from prwatch.integrations import jira as pwjira
from prwatch.integrations import usage as pwusage
from prwatch.core import journal as pwlog
from prwatch.actions import options as pwoptions
from prwatch.core import permissions as pwperm
from prwatch.actions import pr as pwpr
from prwatch.engine import merging as pwmerging
from prwatch.engine import service as pwservice
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.system import claude as pwclaude
from prwatch.system import host as pwhost

# The actions of a pull request card, in the order of its menu; the user picks up to MAX_QUICK_ACTIONS of them to show
# as small buttons on a collapsed card (option `quickActions`).
QUICK_ACTIONS = (
    ('open', 'Ouvrir la session'), ('wake', 'Forcer un réveil'), ('pause', 'Mettre en pause / reprendre'), ('ack', 'Marquer comme vu'),
    ('reset', 'Remettre les essais à 0'), ('report', 'Dernier rapport'), ('settings', 'Réglages…'), ('push', 'Pousser'),
    ('forcePush', 'Pousser en force…'), ('merge', 'Merger la PR…'), ('ticketOpen', 'Ouvrir le ticket Jira'), ('ticket', 'Changer l’état du ticket…'), ('unwatch', 'Arrêter la surveillance'),
)


MAX_QUICK_ACTIONS = 3


def action_weights(entry, st):
    """How much each action matters for a pull request in its current state (`st` is its `status`): 0 = not worth a button.

    The weights are what puts the most useful buttons on a collapsed card, whatever the combination of states.
    """
    attention = bool(entry.get('needsAttention'))
    gave_up = st['kind'] == 'gave-up'
    paused = entry.get('state') == 'paused'
    has_report = bool(entry.get('lastReport'))
    has_key = bool(pwjira.ticket_key(entry))
    ticket = has_key and 'ticket' not in pwperm.hidden_features()
    if entry.get('state') == 'done':
        # a merged PR is when its ticket moves on (to test, or done)
        return {'ticket': (90 if entry.get('doneReason') == 'MERGED' else 30) if ticket else 0, 'ticketOpen': 25 if has_key else 0, 'report': 80 if has_report else 0, 'open': 60, 'unwatch': 70}
    return {
        'merge': 100 if entry.get('ready') and 'merge' not in pwperm.hidden_features() else 0,       # the PR can be merged: that is what is left to do
        'ack': 90 if attention else 0,                    # something asked for the user, who has now seen it
        'reset': 85 if gave_up else (15 if entry.get('attempts') else 0),  # no automatic try left; nothing to reset otherwise
        'push': 80 if st['unpushed'] else 0,              # commits wait on the machine
        'pause': 75 if paused else 20,                    # resume a paused PR
        'open': 70 if attention or gave_up or paused else 60,
        'report': (65 if attention or gave_up else 40) if has_report else 0,
        'wake': 55 if attention else 50,
        'ticket': (72 if entry.get('ready') else 12) if ticket else 0,
        'ticketOpen': 14 if has_key else 0,
        'settings': 10,
        'forcePush': 0,
        'unwatch': 5,
    }


def auto_quick_actions(entry, st):
    """The MAX_QUICK_ACTIONS most relevant small buttons for a pull request, heaviest first."""
    weights = action_weights(entry, st)
    order = {key: index for index, (key, _) in enumerate(QUICK_ACTIONS)}
    ranked = sorted((key for key, weight in weights.items() if weight > 0), key=lambda key: (-weights[key], order[key]))
    return ranked[:MAX_QUICK_ACTIONS]


def quick_actions(entry, st, cfg):
    """What the user chose to show (a list), else what fits the state of the pull request."""
    chosen = cfg.get('quickActions')
    return chosen if isinstance(chosen, list) else auto_quick_actions(entry, st)


STATE_LABELS = {'watching': 'Surveillée', 'paused': 'En pause', 'gave-up': 'Abandonnée (essais épuisés)', 'done': 'Terminée'}


STATE_ICONS = {'ticket-wait': '⏳', 'question': '❓', 'attention': '⚠', 'ready': '✔', 'gave-up': '✖', 'paused': '⏸', 'done': '■', 'watching': '👁'}


PUSH_ICON = '⬆'


def unpushed(entry):
    """What the local branch of a PR has that the remote does not: {'commits': n, 'neverPushed': bool}, None when unknown.

    Reads the remote-tracking branch git already has (no network): it is as fresh as the last fetch or push.
    """
    def git(*args):
        return subprocess.run(['git', *args], cwd=entry.get('workDir') or '.', capture_output=True, text=True, timeout=5)

    branch = entry.get('branch')
    if not branch or entry.get('state') == 'done' or not os.path.isdir(entry.get('workDir') or ''):
        return None
    try:
        ahead = git('rev-list', '--count', f'origin/{branch}..{branch}')
        if ahead.returncode == 0:
            return {'commits': int(ahead.stdout.strip() or 0), 'neverPushed': False}
        if git('rev-parse', '--verify', '--quiet', f'refs/heads/{branch}').returncode == 0:
            return {'commits': 1, 'neverPushed': True}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def status(entry, pending_push=None, cfg=None):
    """The chip of a pull request: {'kind', 'label', 'icon', 'tip', 'unpushed'}.

    `icon` is the small symbol shown before the PR and `tip` its tooltip; `pending_push` is `unpushed(entry)`: commits
    that wait for a push are flagged on every state, since the fix Claude made is not on the PR until then.
    """
    state = entry.get('state') or 'watching'
    if entry.get('needsAttention'):
        kind, label, tip = 'attention', 'Votre attention', f"Votre attention est nécessaire : {entry['needsAttention']}"
    elif entry.get('pendingQuestions'):
        count = len(entry['pendingQuestions'])
        kind, label = 'question', 'Décision attendue'
        tip = f"{count} commentaire{'s' if count > 1 else ''} de relecteur pose{'nt' if count > 1 else ''} une question : dites s'il faut {'les' if count > 1 else 'le'} traiter (oui, non ou autre)."
    elif pwmerging.ticket_waiting(entry, cfg or pwconfig.config()):
        watch = entry['ticketWatch']
        kind, label = 'ticket-wait', 'En attente de validation du ticket'
        tip = f"Mergée. Le ticket {watch['key']} est toujours « {watch['status']} » : changez son état (bouton « Ticket… ») pour que la PR quitte la liste."
    elif state == 'watching' and entry.get('ready'):
        kind, label, tip = 'ready', 'Prête à merger ✓', 'Prête à merger : approuvée, pipelines verts, aucune tâche ouverte.'
    else:
        kind, label = state, STATE_LABELS.get(state, state)
        tip = {
            'watching': 'Surveillée : le démon la regarde à chaque cycle.',
            'paused': 'Surveillance en pause : rien n’est détecté ni envoyé à Claude.',
            'gave-up': 'Essais automatiques épuisés : à regarder vous-même.',
            'done': {'MERGED': 'Fusionnée.', 'DECLINED': 'Refusée.'}.get(entry.get('doneReason'), 'Terminée.'),
        }.get(state, label)
        if state == 'watching' and entry.get('mergeBlockers'):
            tip += ' Bloque le merge : ' + ', '.join(entry['mergeBlockers']) + '.'
    icon = STATE_ICONS.get(kind, '●')
    waiting = None
    if pending_push:
        count = pending_push['commits']
        waiting = ('La branche n’a jamais été poussée' if pending_push['neverPushed']
                   else f"{count} commit{'s' if count > 1 else ''} en attente de push") if count else None
    if waiting:
        icon += PUSH_ICON
        tip += f' {waiting} : le bouton « Pousser » les envoie sur la PR.'
    return {'kind': kind, 'label': label, 'icon': icon, 'tip': tip, 'unpushed': waiting}


def session_activity(age_seconds, busy):
    if age_seconds is None:
        return 'session introuvable'
    if busy:
        return 'en cours d’utilisation'
    minutes = int(age_seconds // 60)
    return f'inactive depuis {minutes} min' if minutes < 60 else f'inactive depuis {minutes // 60} h'


def remove_in(entry, cfg):
    """Minutes left before a finished PR leaves the list; None when it stays (not finished, or the delay is 0)."""
    minutes = cfg['removeDoneAfterMinutes']
    if entry.get('state') != 'done' or not minutes or not entry.get('doneAt') or pwmerging.ticket_waiting(entry, cfg):
        return None
    return max(0, math.ceil((entry['doneAt'] + minutes * 60 - time.time()) / 60))


def overview():
    """Everything the dashboard shows: the daemon, the watched pull requests with their session, the options."""
    cfg = pwconfig.config()
    entries = []
    for entry in pwstore.load_registry():
        path = pwwake.session_file(entry)
        age = time.time() - os.path.getmtime(path) if path else None
        entries.append(
            {
                **{key: entry.get(key) for key in (
                    'repo', 'pr', 'title', 'branch', 'state', 'attempts', 'lastPipeline', 'ready', 'mergeBlockers',
                    'needsAttention', 'lastReportAt', 'sessionId', 'sessionCwd', 'workDir', 'forceWake', 'doneReason', 'pendingQuestions',
                    'mergeDefault', 'mergeAllowed', 'activity', 'autoMergeNote',
                )},
                'removeInMinutes': remove_in(entry, cfg),
                'hasReport': bool(entry.get('lastReport')),
                'settings': entry.get('settings') or {},
                'effective': pwconfig.effective(entry, cfg),
                'sessionTitle': pwclaude.session_title(path),
                'sessionAgeSeconds': age,
                'sessionBusy': age is not None and age < cfg['sessionBusySeconds'],
                'sessionActivity': session_activity(age, age is not None and age < cfg['sessionBusySeconds']),
                'status': (st := status(entry, unpushed(entry), cfg)),
                'quickActions': quick_actions(entry, st, cfg),
                'mergePointless': entry.get('state') == 'watching' and pwmerging.merge_is_pointless(entry.get('mergeBlockers')),
                'ticket': pwjira.ticket_key(entry),
                'ticketUrl': pwjira.ticket_url(cfg['jiraSite'], pwjira.ticket_key(entry)),
                'mergeForm': pwforms.merge_form(entry),
                'confirmations': pwforms.confirmations(entry, cfg),
                'settingsForm': pwforms.settings_form(entry, cfg, pwconfig.CATEGORIES),
                'url': f"https://bitbucket.org/{cfg['bitbucketWorkspace']}/{entry['repo']}/pull-requests/{entry['pr']}" if cfg['bitbucketWorkspace'] else None,
            }
        )
    return {
        'system': pwhost.SYSTEM,
        'daemon': {'running': pwservice.is_running(), 'pid': pwservice.daemon_pid()},
        'suspension': pwstore.suspension(),
        'setupMissing': pwconfig.missing_setup(cfg),
        'schedule': {**pwconfig.schedule_of(cfg), **dict(zip(('working', 'resumes', 'override'), pwconfig.schedule_effective_state(cfg)))},
        'usage': dict(zip(('quotas', 'at'), pwusage.read())) if cfg['usageGuard'] else None,
        'entries': entries,
        'options': {**{key: cfg[key] for key in pwoptions.OPTIONS}, 'schedule': pwconfig.schedule_of(cfg), 'ticketTransitions': cfg['ticketTransitions'], 'autoMergeChoices': [{'id': m, 'label': l} for m, l in pwconfig.AUTO_MERGE_MODES], 'handling': cfg['handling'], 'quickActions': cfg['quickActions']},
        'quickActionChoices': [{'id': k, 'label': label} for k, label in QUICK_ACTIONS], 'maxQuickActions': MAX_QUICK_ACTIONS,
        'handlingForm': pwforms.handling_form(cfg, pwconfig.CATEGORIES),
        'categories': [{'id': name, 'label': label, 'modes': [{'id': m, 'label': ml} for m, ml in modes]} for name, (label, modes) in pwconfig.CATEGORIES.items()],
        'maxAttempts': cfg['maxAttempts'],
        'focus': pwpr.focus(),
        'quit': pwpr.quit_request(),
        'permissions': pwperm.summary(),
        'codeVersion': pwhost.code_stamp(),  # the page reloads itself when the code it was loaded with is no longer the served one
    }


def journal(mode='all', search='', pr='', limit=pwlog.PAGE):
    """The journal entries to show, oldest first, repeated lines folded, reports split into drawable lines.

    Only the last `limit` entries are returned (0 = all); `older` counts those left out, so a front end can offer them.
    """
    text = pwstore.LOG.read_text(encoding='utf-8', errors='replace') if pwstore.LOG.exists() else ''
    entries = pwlog.collapse(pwlog.parse(text))
    shown = [e for e in entries if pwlog.visible(e, mode, search, pr)]
    page, older = pwlog.latest(shown, limit)
    out = []
    for entry in page:
        details = [{'blank': True} if not line.strip() else pwlog.detail_line(line) for line in entry['details']]
        out.append({**{k: entry[k] for k in ('date', 'time', 'last_time', 'pr', 'message', 'kind', 'count')}, 'details': details,
                    'report': pwlog.is_report(entry)})
    return {'entries': out, 'total': len(entries), 'shown': len(shown), 'older': older, 'modes': [{'id': k, 'label': v} for k, v in pwlog.MODES]}
