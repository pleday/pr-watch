"""Adding a pull request to the list by hand: its address, the local clone, the Claude session to wake."""

import os
import re

from prwatch.core import bitbucket as pwbitbucket
from prwatch.actions import common as pwcommon
from prwatch.actions import status as pwstatus
from prwatch.core import config as pwconfig

from prwatch.integrations import jira as pwjira
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.system import claude as pwclaude
from prwatch.system import processes as pwprocesses
from prwatch.engine import registration as pwregistration

# The session id the add form sends to have a new Claude session started for the pull request.
NEW_SESSION = 'new'


PR_REFERENCE = re.compile(r'(?:bitbucket\.org/[^/\s]+/)?([A-Za-z0-9._-]+)(?:/pull-requests/|#|\s+)(\d+)')


def parse_pr_reference(text):
    """('repo', 12) from a Bitbucket address, `repo#12` or `repo 12`; None when it is none of these."""
    match = PR_REFERENCE.search((text or '').strip())
    return (match.group(1), int(match.group(2))) if match else None


def clone_guesses(repo, branch=None):
    """Folders that look like the local clone of `repo`: next to the clones of the PRs already watched, or in the usual places.
    The ones that already have `branch` checked out come first: with several clones of one repository, that is the one at work."""
    parents = []
    for entry in pwstore.load_registry():
        parents.append(os.path.dirname(entry['workDir'].rstrip('/')))
    parents += [os.path.expanduser(p) for p in pwconfig.config()['cloneFolders']]
    found = []

    def add(path):
        if path not in found:
            found.append(path)

    for parent in dict.fromkeys(parents):
        path = os.path.join(parent, repo)
        if os.path.exists(os.path.join(path, '.git')):  # a file, not a folder, in a worktree
            add(path)
    # A clone may carry another name than its repository: look at where the folders next to the known clones come from.
    for parent in dict.fromkeys(parents):
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            continue
        for name in names:
            path = os.path.join(parent, name)
            if os.path.exists(os.path.join(path, '.git')) and pwregistration.detect_defaults(path).get('repo') == repo:
                add(path)
    if branch:
        found.sort(key=lambda path: pwregistration.detect_defaults(path).get('branch') != branch)  # stable: the order above is kept otherwise
    return found


def session_dirs(work_dir):
    """Folders a session for the clone in `work_dir` may have been started in: the clone itself, the folders above it (a
    workspace holding several clones) and the ones the watched pull requests use."""
    path = os.path.abspath(os.path.expanduser(work_dir or ''))
    candidates = [path, os.path.dirname(path), os.path.dirname(os.path.dirname(path))]
    candidates += [e['sessionCwd'] for e in pwstore.load_registry() if e.get('sessionCwd')]
    return [d for d in dict.fromkeys(candidates) if d not in ('/', os.path.expanduser('~')) and os.path.isdir(d)]


def sessions_of(work_dir):
    """The latest Claude sessions that could be woken for that clone: [{'id', 'title', 'modified', 'cwd'}], newest first."""
    if not isinstance(work_dir, str) or not work_dir or not os.path.isdir(os.path.expanduser(work_dir)):
        return []
    found = [{**s, 'cwd': d, 'title': s['title'] or s['id'][:8] + '…'} for d in session_dirs(work_dir) for s in pwclaude.claude_sessions(d)]
    found.sort(key=lambda s: -s['modified'])
    return found[:20]


def already_watched(repo, pr):
    """The refusal to give when the pull request is on the list and still followed (a finished one may be added again), else None."""
    for entry in pwstore.load_registry():
        if entry['repo'] == repo and entry['pr'] == pr and entry.get('state') != 'done':
            return pwcommon._result(False, f"{repo}#{pr} est déjà dans la liste ({pwstatus.STATE_LABELS.get(entry.get('state') or 'watching', 'surveillée').lower()}) : rien à ajouter.", watched=True)
    return None


def pr_lookup(reference):
    """What the "add a pull request" form needs once the user names a PR: its title and branch, a guess of the local clone, its sessions."""
    if (refused := pwcommon._hidden('add-pr')):
        return refused
    if pwconfig.missing_setup(pwconfig.config()):
        return pwcommon._result(False, 'Renseignez d’abord l’espace Bitbucket (Options › Équipe).')
    parsed = parse_pr_reference(reference) if isinstance(reference, str) else None
    if not parsed:
        return pwcommon._result(False, 'Indiquez l’adresse de la PR, ou « dépôt#numéro ».')
    repo, number = parsed
    if (refused := already_watched(repo, number)):
        return refused
    pr = pwbitbucket.api(f"/repositories/{pwbitbucket.workspace()}/{repo}/pullrequests/{number}")
    if not pr:
        return pwcommon._result(False, f'PR introuvable : {repo}#{number} (dépôt, numéro ou droits du jeton ?).')
    if (refused := pwcommon._not_mine(pr)):
        return refused
    branch = pr['source']['branch']['name']
    guesses = clone_guesses(repo, branch)
    work_dir = guesses[0] if guesses else ''
    ticket = pwjira.ticket_key({'branch': branch, 'title': pr['title']})
    sessions = sessions_of(work_dir)
    if ticket:
        sessions.sort(key=lambda s: ticket not in s['title'])  # the session named after the ticket first; the others keep their order
    return pwcommon._result(True, f"{repo}#{number} : {pr['title']}", repo=repo, pr=number, title=pr['title'], branch=branch,
                   state=pr['state'], workDir=work_dir, clones=guesses, sessions=sessions, ticket=ticket)


def claude_sessions(workDir):
    return pwcommon._result(True, '', sessions=sessions_of(workDir))


def add_pr(repo, pr, workDir, sessionId):
    """Adds a pull request to the list by hand: the user names the PR, its local clone and the Claude session to wake for it."""
    if (refused := pwcommon._hidden('add-pr')):
        return refused
    if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9._-]+', repo) or isinstance(pr, bool) or not isinstance(pr, int):
        return pwcommon._result(False, 'Dépôt ou numéro invalide.')
    if pwconfig.missing_setup(pwconfig.config()):
        return pwcommon._result(False, 'Renseignez d’abord l’espace Bitbucket (Options › Équipe).')
    if (refused := already_watched(repo, pr)):  # before a new session is started for nothing
        return refused
    new = sessionId == NEW_SESSION
    if not isinstance(workDir, str) or not isinstance(sessionId, str) or not (new or re.fullmatch(r'[0-9a-fA-F-]{36}', sessionId)):
        return pwcommon._result(False, 'Dossier ou session invalide.')
    work_dir = os.path.abspath(os.path.expanduser(workDir))
    if not os.path.isdir(work_dir):
        return pwcommon._result(False, f'Dossier introuvable : {work_dir}')
    session_cwd = work_dir
    if not new:
        session_cwd = next((d for d in session_dirs(work_dir) if os.path.exists(os.path.join(pwclaude.claude_project_dir(d), f'{sessionId}.jsonl'))), None)
        if not session_cwd:
            return pwcommon._result(False, 'Cette session est introuvable parmi celles de ce dossier : elle ne pourrait pas être reprise.')
    found = pwbitbucket.api(f"/repositories/{pwbitbucket.workspace()}/{repo}/pullrequests/{pr}")
    if not found:
        return pwcommon._result(False, f'PR introuvable : {repo}#{pr}.')
    if (refused := pwcommon._not_mine(found)):
        return refused
    if found['state'] != 'OPEN':
        return pwcommon._result(False, f"La PR est {found['state'].lower()} : rien à surveiller.")
    if new:
        if pwstore.suspension():
            # a new session starts Claude right away; picking an existing session spends nothing, so it stays possible
            return pwcommon._result(False, 'Claude est suspendu : impossible de créer une nouvelle session. Choisissez une session existante.')
        sessionId, failure = pwwake.create_session(repo, pr, found['source']['branch']['name'], found['title'], work_dir, pwconfig.config())
        if failure:
            return pwcommon._result(False, failure)
    running = pwprocesses.find_session_process(sessionId)
    try:
        entry = pwregistration.register_pr(repo, pr, found['source']['branch']['name'], work_dir, sessionId, session_cwd, title=found['title'], unique=True,
                                           origin={'host': running['host'], 'entrypoint': running['entrypoint']} if running and running['host'] else None)
    except pwregistration.AlreadyWatched:  # added by someone else between the check and now
        return already_watched(repo, pr)
    return pwcommon._result(True, f"{pwstore.key_of(entry)} ajoutée à la surveillance.")
