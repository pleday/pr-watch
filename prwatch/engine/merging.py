"""Merging a pull request: what blocks it, the strategies Bitbucket allows, the merge itself, and the end of finished pull requests."""

import fnmatch
import json
import re
import time
import urllib.parse
import urllib.request

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.core import store as pwstore
from prwatch.engine import events as pwevents
from prwatch.integrations import jira as pwjira
from prwatch.engine import wake as pwwake

_MERGE_INFO = {}  # (repo, destination branch) -> (when read, {'default', 'allowed'})
_APPROVALS = {}  # (repo, destination branch) -> (when read, approvals the repository requires or None)


MERGE_INFO_SECONDS = 600


def merge_strategies(entry, pr):
    """The merge strategies Bitbucket has set for the destination branch: {'default': id, 'allowed': [ids]}.

    Empty when they cannot be read: the branch is a resource of the repository, which the token reads only with the
    `read:repository` scope (not even tried when the start-up check found it missing). Read at most every `MERGE_INFO_SECONDS`,
    failures included (they are logged by `api`).
    """
    destination = pr['destination']['branch']['name']
    key = (entry['repo'], destination)
    cached = _MERGE_INFO.get(key)
    if cached and time.time() - cached[0] < MERGE_INFO_SECONDS:
        return cached[1]
    from prwatch.core import permissions

    if permissions.granted('bitbucket-repository') is False:
        return {}  # the token lacks `read:repository` (said at start): the call would only be refused, and logged, every time
    branch = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/refs/branches/{urllib.parse.quote(destination, safe='')}") or {}
    info = {}
    if isinstance(branch.get('default_merge_strategy'), str):
        info['default'] = branch['default_merge_strategy']
    if isinstance(branch.get('merge_strategies'), list):
        info['allowed'] = [s for s in branch['merge_strategies'] if isinstance(s, str)]
    _MERGE_INFO[key] = (time.time(), info)
    return info


# What Bitbucket itself refuses to merge, so that nobody can force it: not enough approvals, a pipeline still running or failed.
# The other blockers (changes requested, open tasks, no pipeline) are only what PR Watch waits for before saying "ready".
HARD_BLOCKERS = ('approbations',)
HARD_BLOCKER_ENDINGS = ('en cours', 'en échec')


def required_approvals(entry, pr, cfg):
    """The approvals Bitbucket requires to merge into the destination branch, else the `minApprovals` setting.

    Read from the branch restrictions of the repository (kind `require_approvals_to_merge`, the highest of the ones that match
    the branch), which the token reads only with the repository administration scope: without it, or when no restriction
    matches, the setting stands. Read at most every `MERGE_INFO_SECONDS`, failures included, so that a refusal is logged once.
    """
    destination = pr['destination']['branch']['name']
    key = (entry['repo'], destination)
    cached = _APPROVALS.get(key)
    if not cached or time.time() - cached[0] >= MERGE_INFO_SECONDS:
        cached = (time.time(), _read_required_approvals(entry, destination))
        _APPROVALS[key] = cached
    return cfg['minApprovals'] if cached[1] is None else cached[1]


def _read_required_approvals(entry, destination):
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/branch-restrictions", {'pagelen': 100, 'kind': 'require_approvals_to_merge'})
    if data is None:
        return None
    model = None  # read only when a restriction follows the branching model
    values = []
    for restriction in data.get('values', []):
        if restriction.get('kind') != 'require_approvals_to_merge' or not isinstance(restriction.get('value'), int):
            continue
        if restriction.get('branch_match_kind') == 'glob':
            matches = fnmatch.fnmatchcase(destination, restriction.get('pattern') or '')
        elif restriction.get('branch_match_kind') == 'branching_model':
            if model is None:
                model = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/branching-model") or {}
            branch = ((model.get(restriction.get('branch_type')) or {}).get('branch') or {}).get('name')
            matches = branch == destination
        else:
            matches = False
        if matches:
            values.append(restriction['value'])
    return max(values) if values else None


def merge_is_pointless(blockers):
    """Whether Bitbucket would refuse the merge now (approvals missing, a pipeline running or failed): the button is not offered."""
    return any(b.startswith(HARD_BLOCKERS) or b.endswith(HARD_BLOCKER_ENDINGS) for b in blockers or [])


def merge_blockers(entry, pr, cfg):
    """What still prevents the merge: an empty list means the PR is approved, its pipelines passed and no task is open."""
    blockers = []
    participants = pr.get('participants', [])
    approvals = [p for p in participants if p.get('approved')]
    needed = required_approvals(entry, pr, cfg)
    if len(approvals) < needed:
        blockers.append(f"approbations {len(approvals)}/{needed}")
    if any(p.get('state') == 'changes_requested' for p in participants):
        blockers.append('modifications demandées')
    if pr.get('task_count', 0):
        blockers.append(f"{pr['task_count']} tâche(s) ouverte(s)")
    head = pr['source']['commit']['hash']
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pipelines/", {'target.branch': entry['branch'], 'sort': '-created_on', 'pagelen': 10}) or {}
    for_head = [p for p in data.get('values', []) if ((p.get('target') or {}).get('commit') or {}).get('hash', '').startswith(head)]
    if not for_head:
        blockers.append('aucun pipeline sur le dernier commit')
    # Only the latest run of each kind of pipeline counts: a failed run that was restarted successfully no longer blocks.
    latest = {}
    for pipeline in sorted(for_head, key=lambda p: p['build_number']):
        latest[json.dumps((pipeline.get('target') or {}).get('selector'), sort_keys=True)] = pipeline
    for pipeline in latest.values():
        state = pipeline['state']
        if state['name'] != 'COMPLETED':
            blockers.append(f"pipeline #{pipeline['build_number']} en cours")
        elif (state.get('result') or {}).get('name') != 'SUCCESSFUL':
            blockers.append(f"pipeline #{pipeline['build_number']} en échec")
    return blockers


def sync_ready(entry, pr, cfg, dry_run):
    """Announces once that the PR can be merged, and forgets it when it can not any more (a new commit, a new comment)."""
    blockers = merge_blockers(entry, pr, cfg)
    if dry_run:
        print(f"{pwstore.key_of(entry)}: {'ready to merge' if not blockers else 'blocked: ' + ', '.join(blockers)}")
        return
    if not blockers and not entry.get('readyNotified'):
        if pwconfig.effective(entry, cfg)['handling']['ready'] == 'notify':
            title, where = pwwake.pr_notice(entry, pr, '✔ Prête à être mergée')
            pwwake.notify(title, where, cfg, entry=entry)
        pwstore.update_entry(entry, ready=True, readyNotified=True, mergeBlockers=[])
    elif blockers and (entry.get('ready') or entry.get('mergeBlockers') != blockers):
        pwstore.update_entry(entry, ready=False, readyNotified=False, mergeBlockers=blockers)


def _matches(patterns, text):
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return True
        except re.error as error:
            pwstore.log(f'ignored pattern {pattern!r}: {error}')
    return False


def is_positive(text, cfg):
    """Whether a comment is only a compliment: it matches `positiveCommentPatterns` and asks for nothing."""
    return (_matches(cfg['positiveCommentPatterns'], text) and not _matches(cfg['changeRequestPatterns'], text)
            and not pwevents.needs_decision(text, cfg))


def unresolved_comments(entry, pr, cfg):
    """The ids of the open comment threads that still ask for something, for the mode 'resolved' of the automatic merge.

    Not counted: the threads already resolved, the comments of the author of the PR, deleted ones, the comments of application
    accounts the daemon does not follow (this is how "nothing important to report" of the automatic review passes), and the
    positive comments that ask for no change.
    """
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': 'id'}) or {}
    author = pr['author']['uuid']
    open_threads = []
    for comment in data.get('values', []):
        user = comment.get('user') or {}
        text = (comment.get('content') or {}).get('raw', '')
        if comment.get('parent') or comment.get('deleted') or comment.get('resolution') is not None or user.get('uuid') == author:
            continue
        if user.get('type') == 'app_user' and not pwevents.followed_bot(user, text, cfg):
            continue
        if user.get('display_name') in cfg['ignoreCommentAuthors'] or is_positive(text, cfg):
            continue
        open_threads.append(comment['id'])
    return open_threads


def merge_strategy_for(entry):
    """The strategy an automatic merge uses: the one Bitbucket sets for the branch, else rebase + fast-forward."""
    chosen = entry.get('mergeDefault') or 'rebase_fast_forward'
    allowed = entry.get('mergeAllowed')
    return chosen if not allowed or chosen in allowed else allowed[0]


def auto_merge(entry, pr, cfg, dry_run):
    """Merges the pull request when its automatic merge is on and its conditions are met; True when it was merged.

    Never when the user is awaited (a question of a reviewer to answer, something to look at). A refused merge is not tried
    again for the same commit. What it waits for is written in `autoMergeNote`, which the card shows.
    """
    mode = pwconfig.effective(entry, cfg)['autoMerge']
    if mode == 'off' or not entry.get('ready'):
        if entry.get('autoMergeNote') and not dry_run:
            pwstore.update_entry(entry, autoMergeNote='')
        return False
    head = pr['source']['commit']['hash']
    waiting = ''
    if entry.get('pendingQuestions'):
        waiting = 'une question d’un relecteur attend votre réponse'
    elif entry.get('needsAttention'):
        waiting = 'votre attention est demandée'
    elif entry.get('autoMergeFailedFor') == head:
        waiting = 'le merge automatique a été refusé pour ce commit'
    elif mode == 'resolved':
        threads = unresolved_comments(entry, pr, cfg)
        if threads:
            waiting = f"{len(threads)} commentaire{'s' if len(threads) > 1 else ''} non résolu{'s' if len(threads) > 1 else ''}"
    if waiting:
        if dry_run:
            print(f"{pwstore.key_of(entry)}: automatic merge waits: {waiting}")
        elif entry.get('autoMergeNote') != waiting:
            pwstore.update_entry(entry, autoMergeNote=waiting)
        return False
    if dry_run:
        print(f"{pwstore.key_of(entry)}: would merge automatically ({mode})")
        return False
    strategy = merge_strategy_for(entry)
    ok, message = merge_pr(entry, strategy, True)
    title, where = pwwake.pr_notice(entry, pr, '✔ Mergée automatiquement' if ok else '⚠ Merge automatique refusé')
    if ok:
        pwstore.update_entry(entry, autoMergeNote='')
        pwwake.notify(title, f"{where}\nStratégie : {strategy}.", cfg, entry=entry)
    else:
        pwstore.update_entry(entry, autoMergeFailedFor=head, autoMergeNote='le merge automatique a été refusé pour ce commit')
        pwwake.notify(title, f"{where}\n{message}", cfg, urgent=True, entry=entry)
    return ok


def merge_pr(entry, strategy='squash', close_source_branch=True):
    """Merges the PR through the API. Returns (ok, message)."""
    body = {'type': 'pullrequest_merge_parameters', 'merge_strategy': strategy, 'close_source_branch': close_source_branch}
    request = urllib.request.Request(
        f"{pwbitbucket.API}{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/merge",
        data=json.dumps(body).encode(),
        headers={'Authorization': pwbitbucket.auth_header(), 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail)['error']['message']
        except (ValueError, KeyError):
            pass
        pwstore.log(f"{pwstore.key_of(entry)}: merge refused ({error.code}): {detail[:300]}")
        return False, f'Bitbucket a refusé le merge ({error.code}) : {detail[:400]}'
    attempts = 40 if status == 202 else 1  # 202: Bitbucket merges in the background, looked at every second
    for attempt in range(attempts):
        pr = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}")
        if pr and pr['state'] == 'MERGED':
            mark_merged(entry, pwconfig.config())
            pwstore.log(f"{pwstore.key_of(entry)}: merged ({strategy})")
            return True, 'PR mergée.'
        if attempt < attempts - 1:
            time.sleep(1)
    return status == 200, 'Merge demandé, Bitbucket ne l’a pas encore terminé.' if status == 202 else 'Merge envoyé.'


def other_open_prs(entry):
    """The keys of the other pull requests of the same Jira ticket that are not finished yet (watched, paused, given up)."""
    key = pwjira.ticket_key(entry)
    if not key:
        return []
    return [pwstore.key_of(e) for e in pwstore.load_registry()
            if pwstore.key_of(e) != pwstore.key_of(entry) and e.get('state') != 'done' and pwjira.ticket_key(e) == key]


def mark_merged(entry, cfg):
    """A merged pull request: finished, and awaiting the validation of its ticket when that is what the user asked for.

    The state of the ticket is read now, before anyone moves it: the PR waits until it differs. A ticket that still has
    other pull requests in progress is not waited for (its last merged pull request is), nor is one Jira can not tell about.
    """
    fields = {'state': 'done', 'doneReason': 'MERGED', 'ready': False, 'doneAt': time.time(), 'autoMergeNote': '', 'ticketValidated': False}
    key = pwjira.ticket_key(entry)
    if cfg['keepUntilTicketValidated'] and key and not other_open_prs(entry):
        try:
            fields['ticketWatch'] = {'key': key, 'status': pwjira.status(cfg['jiraSite'], key)}
        except pwjira.JiraError as error:
            pwstore.log(f"{pwstore.key_of(entry)}: ticket {key} not read, the PR will not wait for it ({error})")
    pwstore.update_entry(entry, **fields)


def ticket_waiting(entry, cfg):
    """Whether a finished pull request stays in the list for the validation of its ticket."""
    return bool(cfg['keepUntilTicketValidated'] and entry.get('state') == 'done' and entry.get('doneReason') == 'MERGED'
                and entry.get('ticketWatch') and not entry.get('ticketValidated'))


def ticket_validated(entry):
    """The ticket moved on: the pull request leaves the list after the usual delay, counted from now."""
    pwstore.update_entry(entry, ticketValidated=True, doneAt=time.time())
    pwstore.log(f"{pwstore.key_of(entry)}: ticket {entry['ticketWatch']['key']} changed state, the PR will leave the list")


TICKET_CHECK_SECONDS = 120
_TICKET_CHECKS = {}  # ticket key -> (when read, its state)


def refresh_ticket_waits(cfg):
    """Looks at the ticket of every merged pull request that waits for it, and releases the ones whose ticket changed state."""
    for entry in pwstore.load_registry():
        if not ticket_waiting(entry, cfg):
            continue
        key = entry['ticketWatch']['key']
        read = _TICKET_CHECKS.get(key)
        if not read or time.time() - read[0] >= TICKET_CHECK_SECONDS:
            try:
                read = (time.time(), pwjira.status(cfg['jiraSite'], key))
            except pwjira.JiraError as error:
                pwstore.log(f"{pwstore.key_of(entry)}: ticket {key} not read ({error})")  # it keeps waiting
                continue
            _TICKET_CHECKS[key] = read
        if read[1] != entry['ticketWatch']['status']:
            ticket_validated(entry)


def purge_done(cfg):
    """Takes the finished pull requests out of the registry once `removeDoneAfterMinutes` passed (0 = never)."""
    minutes = cfg['removeDoneAfterMinutes']
    with pwstore.registry_lock():
        kept, changed = [], False
        for entry in pwstore.load_registry():
            if entry.get('state') == 'done' and not ticket_waiting(entry, cfg):
                if not entry.get('doneAt'):
                    entry['doneAt'], changed = time.time(), True  # finished before the time was recorded: counted from now
                elif minutes and time.time() - entry['doneAt'] >= minutes * 60:
                    pwstore.log(f"{pwstore.key_of(entry)}: taken off the list, finished {minutes} min ago")
                    changed = True
                    continue
            kept.append(entry)
        if changed:
            pwstore.save_registry(kept)
