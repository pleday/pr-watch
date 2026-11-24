"""The table of actions a front end can ask for, and the function that runs one from a request."""

from prwatch.actions import adding as pwadding
from prwatch.actions import common as pwcommon
from prwatch.actions import options as pwoptions
from prwatch.actions import pr as pwpr
from prwatch.engine import service as pwservice
from prwatch.system import notifications as pwnotifications
from prwatch.actions import sessions as pwsessions
from prwatch.actions import tickets as pwtickets

# action name -> (function, fields it needs from the request)
ACTIONS = {
    'pause': (pwpr.pause, ('repo', 'pr', 'paused')),
    'reset': (pwpr.reset, ('repo', 'pr')),
    'acknowledge': (pwpr.acknowledge, ('repo', 'pr')),
    'read-reports': (pwpr.read_reports, ('repo', 'pr')),
    'wake': (pwpr.wake, ('repo', 'pr')),
    'answer-question': (pwpr.answer_question, ('repo', 'pr', 'id', 'choice', 'note')),
    'unwatch': (pwpr.unwatch, ('repo', 'pr')),
    'open-session': (pwsessions.open_session, ('repo', 'pr')),
    'open-terminal': (pwsessions.open_terminal, ('repo', 'pr')),
    'open-in': (pwsessions.open_in, ('repo', 'pr', 'app')),
    'pr-lookup': (pwadding.pr_lookup, ('reference',)),
    'claude-sessions': (pwadding.claude_sessions, ('workDir',)),
    'add-pr': (pwadding.add_pr, ('repo', 'pr', 'workDir', 'sessionId')),
    'ticket-info': (pwtickets.ticket_info, ('repo', 'pr')),
    'jira-metadata': (pwtickets.jira_metadata, ()),
    'ticket-draft': (pwtickets.ticket_draft, ('repo', 'pr')),
    'ticket-transition': (pwtickets.ticket_transition, ('repo', 'pr', 'transition', 'impacts')),
    'open-link': (pwoptions.open_link, ('url',)),
    'refresh': (pwoptions.refresh, ()),
    'journal-archive': (pwoptions.journal_archive, ()),
    'permissions-check': (pwoptions.permissions_check, ()),
    'permissions-decline': (pwoptions.permissions_decline, ('ids',)),
    'plugins-status': (pwoptions.plugins_status, ()),
    'plugins-update': (pwoptions.plugins_update, ('kind',)),
    'push': (lambda repo, pr: pwpr.git_push(repo, pr, False), ('repo', 'pr')),
    'force-push': (lambda repo, pr: pwpr.git_push(repo, pr, True), ('repo', 'pr')),
    'fetch': (pwpr.git_fetch, ('repo', 'pr')),
    'rebase': (pwpr.git_rebase, ('repo', 'pr')),
    'merge': (pwpr.merge, ('repo', 'pr', 'strategy', 'closeSourceBranch')),
    'pr-settings': (pwpr.pr_settings, ('repo', 'pr', 'settings')),
    'pr-settings-form': (pwpr.pr_settings_form, ('repo', 'pr', 'push', 'handling', 'merge')),
    'daemon': (pwoptions.daemon_command, ('command',)),
    'options': (pwoptions.set_options, ('values',)),
    'profile-load': (pwoptions.load_profile, ('profile',)),
    'resume': (pwoptions.resume, ()),
    'usage-refresh': (pwoptions.usage_refresh, ()),
}


# Actions after which the daemon polls the pull requests without waiting.
REFRESHING = {'resume', 'refresh', 'pause', 'reset', 'acknowledge', 'read-reports', 'wake', 'answer-question', 'push', 'force-push', 'fetch', 'rebase', 'merge', 'pr-settings', 'pr-settings-form'}


# Actions that cannot be undone, or that leave the machine: the request must say the user confirmed.
CONFIRMED = {'journal-archive', 'unwatch', 'force-push', 'merge', 'push', 'fetch', 'rebase', 'ticket-transition', 'plugins-update'}


# Actions on a pull request that only read: they do not settle its notification.
READING = {'ticket-info', 'ticket-draft', 'jira-metadata'}


def perform(request):
    """Runs the action named in a request (a dict decoded from JSON) and returns its result."""
    name = request.get('action')
    if name not in ACTIONS:
        return pwcommon._result(False, 'Action inconnue.')
    if name in CONFIRMED and request.get('confirmed') is not True:
        return pwcommon._result(False, 'Action non confirmée.')
    function, fields = ACTIONS[name]
    arguments = {}
    for field in fields:
        if field in ('strategy', 'closeSourceBranch', 'paused', 'note', 'impacts', 'merge') and field not in request:
            continue
        if field not in request:
            return pwcommon._result(False, f'Champ manquant : {field}')
        arguments[{'closeSourceBranch': 'close_source_branch'}.get(field, field)] = request[field]
    if 'pr' in arguments and (isinstance(arguments['pr'], bool) or not isinstance(arguments['pr'], int)):
        return pwcommon._result(False, 'Numéro de pull request invalide.')
    if 'id' in arguments and (isinstance(arguments['id'], bool) or not isinstance(arguments['id'], int)):
        return pwcommon._result(False, 'Identifiant de commentaire invalide.')
    if 'repo' in arguments and not isinstance(arguments['repo'], str):
        return pwcommon._result(False, 'Dépôt invalide.')
    if 'values' in arguments and not isinstance(arguments['values'], dict):
        return pwcommon._result(False, 'Valeurs invalides.')
    if 'settings' in arguments and not isinstance(arguments['settings'], dict):
        return pwcommon._result(False, 'Réglages invalides.')
    try:
        result = function(**arguments)
    except Exception as error:  # an action must answer, not drop the connection
        return pwcommon._result(False, f'{type(error).__name__} : {error}')
    # Whatever the user does about a pull request settles its notification: one click, however many events piled up.
    if result.get('ok') and name not in READING and isinstance(arguments.get('repo'), str) and 'pr' in arguments:
        pwnotifications.close_group(f"{arguments['repo']}#{arguments['pr']}")
    # What the action changed is read back from Bitbucket at once, not at the end of the daemon's sleep.
    if name in REFRESHING and result.get('ok'):
        pwservice.request_cycle()
    return result
