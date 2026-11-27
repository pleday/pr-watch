"""Putting a pull request on the list, and the defaults `pr-watch register` guesses from where it is run."""
import os
import re
import subprocess

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.core import store as pwstore
from prwatch.system import claude as pwclaude

class AlreadyWatched(Exception):
    """The pull request is already on the list and `unique` was asked: nothing is replaced."""


def register_pr(repo, pr, branch, work_dir, session, session_cwd, since_pipeline=None, settings=None, origin=None, title=None, unique=False):
    """Puts a pull request on the list (or replaces it): what `pr-watch register` and the dashboard's "add" both do.

    Only what happens after this moment is looked at: the comments and pipelines that exist now are not events.
    With `unique` (the dashboard's "add"), a pull request already followed is refused (`AlreadyWatched`) instead of replaced:
    the command line replaces it on purpose, to point it at another session or clone.
    """
    entry = {
        'repo': repo,
        'pr': pr,
        'branch': branch,
        'workDir': work_dir,
        'sessionId': session,
        'sessionCwd': session_cwd,
        'state': 'watching',
        'attempts': 0,
        'registeredAt': pwstore.now(),
    }
    if title:
        entry['title'] = title
    if origin:
        entry['origin'] = origin  # the application the session was opened from: proposed first when it is reopened
    if settings:
        entry['settings'] = pwconfig.merge_settings(entry, settings)
    entry['lastComment'] = pwbitbucket.max_comment_id(entry)
    entry['lastPipeline'] = since_pipeline if since_pipeline is not None else pwbitbucket.max_pipeline(entry)
    with pwstore.registry_lock():
        current = pwstore.load_registry()
        if unique and any(pwstore.key_of(e) == pwstore.key_of(entry) and e.get('state') != 'done' for e in current):
            raise AlreadyWatched(pwstore.key_of(entry))
        entries = [e for e in current if pwstore.key_of(e) != pwstore.key_of(entry)]
        pwstore.save_registry(entries + [entry])
    return entry


def _git(cwd, *args):
    try:
        done = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ''
    return done.stdout.strip() if done.returncode == 0 else ''


def detect_defaults(cwd=None, environ=None):
    """What `pr-watch register` can find out by itself: the clone (git root of `cwd`), its repository (the Bitbucket address of
    `origin`), its branch, and the Claude session that runs the command (`CLAUDE_CODE_SESSION_ID`, started in some folder)."""
    cwd = cwd or os.getcwd()
    environ = os.environ if environ is None else environ
    found = {}
    root = _git(cwd, 'rev-parse', '--show-toplevel')
    if root:
        found['work_dir'] = root
        branch = _git(root, 'symbolic-ref', '--short', '-q', 'HEAD')  # empty on a detached HEAD
        if branch:
            found['branch'] = branch
        match = re.search(r'bitbucket\.org[:/][^/]+/([^/\s]+?)(?:\.git)?$', _git(root, 'remote', 'get-url', 'origin'))
        if match:
            found['repo'] = match.group(1)
    session = environ.get('CLAUDE_CODE_SESSION_ID')
    if session:
        found['session'] = session
        started = pwclaude.session_cwd(session)
        if started:
            found['session_cwd'] = started
    return found
