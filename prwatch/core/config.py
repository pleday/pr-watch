"""The settings: what is done for each kind of event, the global options (config.json) and the choices of one pull request over them."""

import json
import re
from datetime import datetime, timedelta

from prwatch.core import store as pwstore
from prwatch.system import host as pwhost

_FIX_MODES = (('fix', 'Claude corrige'), ('notify', 'Me notifier seulement'), ('ignore', 'Ignorer'))


# What the daemon does with each kind of event: category -> (label, modes). The first mode is the default.
# `fix` wakes the Claude session, `notify` only tells the user, `ignore` drops the event, `rerun` runs the pipeline
# again once per commit (and tells the user when it fails again).
CATEGORIES = {
    'pipeline': ('Pipelines en échec', _FIX_MODES),
    'comment': ('Commentaires des relecteurs', _FIX_MODES),
    # A comment that asks something or wants a validation is not for Claude to answer alone: the user decides.
    'question': ('Commentaires qui posent une question ou demandent une validation', (
        ('ask', 'Me demander quoi faire (oui / non / autre)'), ('fix', 'Claude traite directement'),
        ('notify', 'Me notifier seulement'), ('ignore', 'Ignorer'))),
    'review-bot': ('Revue automatique (pr-review)', _FIX_MODES),
    'sonar': ('Sonar (quality gate)', _FIX_MODES),
    'infra': ('Pannes d’infrastructure du pipeline', (('rerun', 'Relancer une fois, puis me notifier'), ('notify', 'Me notifier seulement'), ('ignore', 'Ignorer'))),
    'ready': ('PR prête à merger', (('notify', 'Me notifier'), ('ignore', 'Ignorer'))),
}


DEFAULT_HANDLING = {category: modes[0][0] for category, (_label, modes) in CATEGORIES.items()}


def valid_choices(values):
    """The valid entries of a {category: mode} mapping (a None mode, meaning 'default', is dropped); the rest is ignored."""
    kept = {}
    for name, mode in (values or {}).items():
        try:
            kept.update({k: v for k, v in validate_handling({name: mode}, partial=True).items() if v})
        except ValueError as error:
            pwstore.log(f'ignored choice {name}={mode!r}: {error}')
    return kept


def validate_handling(values, partial=False):
    """Checks a {category: mode} mapping; with `partial`, a None mode means 'back to the default'. Raises ValueError."""
    if not isinstance(values, dict):
        raise ValueError('handling: a mapping is expected')
    clean = {}
    for category, mode in values.items():
        if category not in CATEGORIES:
            raise ValueError(f'unknown category: {category}')
        if mode is None and partial:
            clean[category] = None
        elif mode in [m for m, _label in CATEGORIES[category][1]]:
            clean[category] = mode
        else:
            raise ValueError(f'{category}: unknown mode {mode!r}')
    return clean


# Automatic merge of a pull request: off (the default), as soon as it can be merged, or also once no comment is left to handle.
AUTO_MERGE_MODES = (
    ('off', 'Désactivé'),
    ('ready', 'Dès que la PR est prête à merger'),
    ('resolved', 'Prête à merger et sans commentaire à traiter'),
)


def validate_auto_merge(mode, partial=False):
    """`mode` when it is a mode of AUTO_MERGE_MODES; with `partial`, None (follow the global choice) passes. Raises ValueError."""
    if mode is None and partial:
        return None
    if mode not in [m for m, _ in AUTO_MERGE_MODES]:
        raise ValueError(f'autoMerge : mode inconnu {mode!r}')
    return mode


def validate_ticket_transitions(value):
    """Why `value` is not a usable {type of ticket: state} mapping, '' when it is."""
    if not isinstance(value, dict) or len(value) > 40:
        return 'une liste de 40 types au plus est attendue'
    for kind, state in value.items():
        if not isinstance(kind, str) or not isinstance(state, str) or not kind.strip() or not state.strip():
            return 'chaque type doit avoir un état'
        if kind.strip().lower() == 'default':
            return 'pas d’état par défaut : chaque type de ticket a son propre workflow'
        if len(kind) > 60 or len(state) > 80:
            return 'texte trop long'
    return ''


DEFAULT_CONFIG = {
    'pollSeconds': 60,
    # A session whose transcript changed within this window is considered in use: it is not resumed meanwhile.
    'sessionBusySeconds': 90,
    'maxAttempts': 5,
    'claudeTimeoutSeconds': 3600,
    'claudeBinary': pwhost.claude_binary(),
    'claudeArgs': [
        '--permission-mode',
        'acceptEdits',
        '--allowedTools',
        'Bash(git:*)',
        'Bash(yarn:*)',
        'Bash(curl:*)',
        'Bash(python3:*)',
        'Bash(node:*)',
        'Bash(ls:*)',
        'Bash(cat:*)',
        'Bash(grep:*)',
        'mcp__sonarqube__*',
    ],
    # Comments by these display names are never forwarded (bots).
    'ignoreCommentAuthors': [],
    # Claude posts its replies with the token of the author of the PR, so they look like the author's own comments: every reply
    # ends with this signature, which is how the daemon tells them from what the author wrote and does not wake itself on them.
    'claudeReplyMarker': '— 🤖 Claude',
    'notify': True,
    # Show the dashboard in a tab of the default browser instead of an application window; a tab that is already open is reused.
    'dashboardInBrowser': False,
    # PR Watch stops waking Claude when the 5-hour session or the week of the plan has this much (%) or less left, so that the
    # woken sessions never dip into the paid overage credits; it starts again by itself when the window starts over.
    # `usageGuard` false turns the check off altogether.
    # The hours PR Watch works: outside them nothing is looked at, woken or notified, and the events wait (`days` 0 = Monday).
    'schedule': {'enabled': False, 'days': [0, 1, 2, 3, 4], 'start': '09:00', 'end': '18:00'},
    'usageGuard': True,
    'usageSessionMarginPercent': 5,  # of the 5-hour session
    'usageWeekMarginPercent': 5,  # of the week
    # A pull request that was merged or declined stays in the list this many minutes, then leaves it (0 = it stays).
    # How many pull requests the menu of the tray icon lists (those that ask for something first); the rest is counted.
    'trayMaxPrs': 5,
    'removeDoneAfterMinutes': 60,
    # The actions shown as small buttons on a collapsed card: null = the ones that fit the state of the pull request
    # (actions.auto_quick_actions), else a list of 3 ids at most of actions.QUICK_ACTIONS. The others are in the card's menu.
    'quickActions': None,
    # The Bitbucket Cloud workspace the pull requests live in (the first part of https://bitbucket.org/<workspace>/<repo>).
    # Required: nothing is watched until it is set (Options › Équipe, see `missing_setup`).
    'bitbucketWorkspace': '',
    # The Jira site of the tickets of the pull requests (its API is called with ATLASSIAN_API_TOKEN, see jira.py).
    # '' = no Jira: no ticket link and no action on tickets.
    'jiraSite': '',
    # The Jira field the workflow asks to fill before a ticket leaves some states (found by its name, case-insensitive,
    # on the site, e.g. 'Description des impacts potentiels'); '' = no such field: the impacts are neither read, proposed nor written.
    'jiraImpactsField': '',
    # The pipeline steps and build statuses that belong to the code-quality tool (regular expression, case-insensitive, on the
    # step name or the status key and name): their result is read as the quality gate. '' = no such tool: no Sonar event.
    'sonarPattern': 'sonar',
    # Where the local clones of the repositories are looked for when a PR is added, besides the folders of the PRs already watched.
    'cloneFolders': ['~/projet/workspace', '~/projet', '~/workspace', '~/src', '~'],
    # Whether the woken session pushes its fixes to the branch of the PR (otherwise it stops at the commit).
    'autoPush': True,
    # What is done with each kind of event, for every PR that does not say otherwise (see CATEGORIES).
    'handling': dict(DEFAULT_HANDLING),
    # A comment of a reviewer matching one of these (regular expressions, case-insensitive) asks a question or a validation:
    # it is kept for the user to decide (handle it: yes / no / other) instead of waking Claude (see `question` in CATEGORIES).
    'questionPatterns': [
        r'\?',
        r"\b(valider|validation|valid[ée]e?s?|confirmer|confirmes?|confirmez|d['’]accord|ok pour)\b",
        r'\b(do you agree|can you confirm|could you confirm|please confirm|ok to|should we|are you ok|is it ok)\b',
    ],
    # Approvals needed before the PR is announced as ready to merge.
    'minApprovals': 1,
    # The state a merged pull request's ticket is proposed to move to, by type of ticket: every type has its own workflow, so there
    # is no state for the others (the user chooses). A state is found by the name of the transition or of the state it leads to,
    # without regard to case or accents.
    # Empty until set (Options › Équipe, or a profile file: `pr-watch profile load`).
    'ticketTransitions': {},
    # A merged pull request whose Jira ticket has not changed state stays in the list, as awaiting the validation of the ticket,
    # instead of leaving it after `removeDoneAfterMinutes`. With several pull requests on one ticket the last one to be merged waits.
    'keepUntilTicketValidated': True,
    # Merge a pull request by itself, for every PR that does not say otherwise (see AUTO_MERGE_MODES).
    'autoMerge': 'off',
    # For the mode 'resolved': a root comment still open counts as a comment to handle, except the ones that are positive and ask
    # for nothing: matching one of `positiveCommentPatterns` and none of `changeRequestPatterns` (nor `questionPatterns`).
    'positiveCommentPatterns': [
        r'\blgtm\b', r'👍|👌|✅|🎉|🙌|💯', r'^\s*ok\b',
        r'\b(bravo|super|parfait|nickel|excellent|génial|top|merci|bien joué|looks good|nice|great|well done|thanks|thank you|perfect|approved?)\b',
    ],
    'changeRequestPatterns': [
        r'\?', r'\bmais\b|\bbut\b|\bhowever\b|\bsauf\b',
        r'\b(peux-tu|peut-on|pouvez-vous|pourrais|pourriez|il faut|faudrait|faut|merci de|à corriger|à changer|à revoir|à vérifier|renomm\w*|supprim\w*|enlev\w*|ajout\w*|corrig\w*|modifi\w*|change\w*|please|should|could you|can you|must|need to|needs to|fix|remove|rename|add|todo)\b',
    ],
    # Comments of application accounts are ignored (staging links, Jira tickets...) except the ones matching a rule here:
    # e.g. the automatic review of a pipeline, which posts as an application account: {'author', 'startsWith', 'ignoreIfContains'}.
    'followBotComments': [],
    # The AI review step of the pipeline. It sometimes ends without posting its comment: its verdict is then read in the log.
    'reviewStepPattern': '',  # '' = no such step
    # A failed step whose log matches one of these is an infrastructure problem, not something to fix in the code.
    'infraPatterns': [
        'ECONNRESET',
        'ETIMEDOUT',
        'EAI_AGAIN',
        'No space left on device',
        'Could not resolve host',
        'Bad Gateway',
        'Service Unavailable',
    ],
}


def config():
    cfg = dict(DEFAULT_CONFIG)
    cfg['handling'] = dict(DEFAULT_HANDLING)
    if pwstore.CONFIG.exists():
        stored = json.loads(pwstore.CONFIG.read_text(encoding='utf-8'))
        handling = stored.pop('handling', {})
        if 'usageMarginPercent' in stored:  # the single threshold of before: it becomes both
            old = stored.pop('usageMarginPercent')
            stored.setdefault('usageSessionMarginPercent', old)
            stored.setdefault('usageWeekMarginPercent', old)
        cfg.update(stored)
        cfg['handling'].update(valid_choices(handling if isinstance(handling, dict) else {}))
    return cfg


def missing_setup(cfg):
    """The options PR Watch cannot work without and that are still empty (first start): a list of option names."""
    return [] if (cfg.get('bitbucketWorkspace') or '').strip() else ['bitbucketWorkspace']


def sonar_matches(cfg, text):
    """Whether `text` (a step name, a build status) belongs to the code-quality tool of `sonarPattern`; False when none is set."""
    pattern = cfg.get('sonarPattern') or ''
    if not pattern:
        return False
    try:
        return bool(re.search(pattern, text or '', re.IGNORECASE))
    except re.error:
        return 'sonar' in (text or '').lower()  # a broken pattern must not silence the quality gate


DAY_NAMES = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')


def _minutes(text):
    hours, minutes = text.split(':')
    if not (hours.isdigit() and minutes.isdigit() and len(minutes) == 2 and int(hours) < 24 and int(minutes) < 60):
        raise ValueError(text)
    return int(hours) * 60 + int(minutes)


def validate_schedule(value):
    """The schedule as stored: {'enabled', 'days', 'start', 'end'}; ValueError when it makes no sense."""
    if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
        raise ValueError('horaires invalides')
    days = value.get('days')
    if not isinstance(days, list) or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days) or (value['enabled'] and not days):
        raise ValueError('choisissez au moins un jour')
    try:
        start, end = str(value['start']), str(value['end'])
        if _minutes(start) >= _minutes(end):
            raise ValueError("l'heure de fin doit suivre l'heure de début")
    except (KeyError, ValueError, AttributeError) as error:
        raise ValueError(str(error) or 'heures invalides')
    return {'enabled': value['enabled'], 'days': sorted(set(days)), 'start': start, 'end': end}


def schedule_of(cfg):
    """The schedule of `cfg` when it is valid, else the default one (switched off)."""
    try:
        return validate_schedule(cfg.get('schedule'))
    except ValueError:
        return dict(DEFAULT_CONFIG['schedule'])


def schedule_state(cfg, now=None):
    """(working now?, when it starts again as text or '') for the schedule of `cfg`; always working when it is not enabled."""
    schedule = schedule_of(cfg)  # a broken setting must not stop the daemon
    if not schedule['enabled']:
        return True, ''
    now = now or datetime.now()
    start, end = _minutes(schedule['start']), _minutes(schedule['end'])
    minute = now.hour * 60 + now.minute
    if now.weekday() in schedule['days'] and start <= minute < end:
        return True, ''
    return False, _next_schedule_start(schedule, now)


def _next_schedule_start(schedule, now):
    for ahead in range(8):
        day = (now + timedelta(days=ahead))
        boundary = day.replace(
            hour=int(schedule['start'][:2]), minute=int(schedule['start'][3:]), second=0, microsecond=0,
        )
        if day.weekday() in schedule['days'] and boundary > now:
            return ('aujourd’hui' if ahead == 0 else 'demain' if ahead == 1 else DAY_NAMES[day.weekday()]) + f" {schedule['start']}"
    return ''


def schedule_effective_state(cfg, now=None):
    """The effective schedule state after a temporary user override."""
    now = now or datetime.now()
    working, resumes = schedule_state(cfg, now)
    override = pwstore.schedule_override()
    if override and schedule_of(cfg)['enabled']:
        if override['mode'] == 'active':
            return True, '', 'active'
        return False, _next_schedule_start(schedule_of(cfg), now), 'paused'
    return working, resumes, ''


def next_schedule_boundary(cfg, mode, now=None):
    """Epoch timestamp for the next scheduled opening or closing boundary."""
    schedule = schedule_of(cfg)
    if not schedule['enabled'] or mode not in ('active', 'paused'):
        return None
    now = now or datetime.now()
    target = 'end' if mode == 'active' else 'start'
    hours, minutes = map(int, schedule[target].split(':'))
    for ahead in range(8):
        day = now + timedelta(days=ahead)
        if day.weekday() not in schedule['days']:
            continue
        boundary = day.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if boundary > now:
            return boundary.timestamp()
    return None


def seconds_until_schedule_change(cfg, now=None):
    """Seconds until the next opening or closing time, or None when no schedule is enabled."""
    override = pwstore.schedule_override()
    if override and schedule_of(cfg)['enabled']:
        return max(1, int(override['until'] - (now or datetime.now()).timestamp() + 0.999999))
    schedule = schedule_of(cfg)
    if not schedule['enabled']:
        return None
    now = now or datetime.now()
    candidates = []
    for ahead in range(8):
        day = now + timedelta(days=ahead)
        if day.weekday() not in schedule['days']:
            continue
        for text in (schedule['start'], schedule['end']):
            hours, minutes = map(int, text.split(':'))
            change = day.replace(hour=hours, minute=minutes, second=0, microsecond=0)
            if change > now:
                candidates.append(change)
    return max(1, int((min(candidates) - now).total_seconds() + 0.999999))


def effective(entry, cfg):
    """The settings of one PR: its own choices (`settings` in the registry) over the global ones."""
    own = entry.get('settings') or {}
    handling = dict(cfg['handling'])
    handling.update(valid_choices(own.get('handling') if isinstance(own.get('handling'), dict) else {}))
    auto_push = own.get('autoPush')
    own_merge = own.get('autoMerge')
    auto_merge = own_merge if own_merge in [m for m, _ in AUTO_MERGE_MODES] else cfg.get('autoMerge', 'off')
    return {'handling': handling, 'autoPush': cfg['autoPush'] if auto_push is None else bool(auto_push), 'autoMerge': auto_merge}


def category(event):
    """The category of an event (None for the user's own request, which is always handled)."""
    kind = event['kind']
    if kind == 'pipeline-failed':
        return 'infra' if event.get('infra') else 'pipeline'
    if kind in ('pipeline-ok', 'sonar-failed'):
        return 'sonar'
    return kind if kind in ('comment', 'question', 'review-bot') else None


def route(events, settings, claude_disabled=False):
    """Routes events; when Claude is suspended, turn only its work into notifications without changing saved settings."""
    to_claude, to_rerun, to_tell = [], [], []
    for event in events:
        name = category(event)
        mode = settings['handling'].get(name, 'fix') if name else 'fix'
        if claude_disabled and mode == 'fix':
            mode = 'notify'
        if mode == 'ignore':
            continue
        if mode == 'rerun':
            to_rerun.append(event)
        elif mode == 'notify':
            to_tell.append(event)
        else:
            to_claude.append(event)
    return to_claude, to_rerun, to_tell


def merge_settings(entry, change):
    """The `settings` of an entry once `change` is applied: a None value removes a choice."""
    settings = {'autoPush': (entry.get('settings') or {}).get('autoPush'), 'handling': dict((entry.get('settings') or {}).get('handling') or {}),
                'autoMerge': (entry.get('settings') or {}).get('autoMerge')}
    if 'autoPush' in change:
        settings['autoPush'] = change['autoPush']
    if 'autoMerge' in change:
        settings['autoMerge'] = change['autoMerge']
    if settings['autoMerge'] is None:
        del settings['autoMerge']  # follow the global choice
    for name, mode in (change.get('handling') or {}).items():
        if mode is None:
            settings['handling'].pop(name, None)
        else:
            settings['handling'][name] = mode
    return settings
