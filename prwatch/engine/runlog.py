"""What a woken Claude session did, for the journal: the commits it made, the replies it posted, the threads it resolved.

The run itself is a black box (one report at the end): what it changed is read back afterwards, from the clone (new commits)
and from Bitbucket (comments carrying the reply marker, resolutions), so that the journal says it in a line each and links to it.
"""

import datetime
import subprocess

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import store as pwstore

COMMITS_MAX = 20  # a run that makes more is not listed beyond (the count says it)
SEPARATOR = '\x1f'


def _git(entry, *args):
    try:
        done = subprocess.run(['git', *args], cwd=entry.get('workDir') or entry['sessionCwd'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def head(entry):
    """The commit the clone is at, to compare with after the run (None when it cannot be told)."""
    return _git(entry, 'rev-parse', 'HEAD')


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def event_lines(events):
    """One line per event that woke the session: what it was asked to look at."""
    lines = []
    for event in events:
        text = ' '.join((event.get('text') or '').split())
        lines.append(f"- {event.get('kind', 'event')}: {text[:200]}")
    return lines


def new_commits(entry, before):
    """[(sha, subject, body)] made since `before`, oldest first."""
    if not before or head(entry) in (None, before):
        return []
    out = _git(entry, 'log', '--reverse', f'--format=%H{SEPARATOR}%s{SEPARATOR}%b{SEPARATOR}%x00', f'{before}..HEAD')
    commits = []
    for chunk in (out or '').split('\x00'):
        parts = chunk.strip('\n').split(SEPARATOR)
        if len(parts) >= 3 and parts[0].strip():
            commits.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return commits


def _moment(text):
    try:
        return datetime.datetime.fromisoformat((text or '').replace('Z', '+00:00'))
    except ValueError:
        return None


def comment_activity(entry, cfg, started):
    """([(id, first line)] replies carrying the signature, [id] threads resolved) since `started` (UTC)."""
    data = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': '-id'})
    replies, resolved = [], []
    if not data or 'values' not in data:
        return replies, resolved
    marker = cfg['claudeReplyMarker']
    for comment in data['values']:
        text = (comment.get('content') or {}).get('raw', '')
        created = _moment(comment.get('created_on'))
        if marker in text and not comment.get('deleted') and created and created >= started:
            line = next((part for part in text.splitlines() if part.strip() and marker not in part), '')
            replies.append((comment['id'], ' '.join(line.split())[:200]))
        done = _moment((comment.get('resolution') or {}).get('created_on'))
        if comment.get('resolution') is not None and not comment.get('parent') and done and done >= started:
            resolved.append(comment['id'])
    return sorted(replies), sorted(resolved)


def log_run(entry, cfg, before, started):
    """Journals what the run changed. Never raises: the journal of what was done must not break the cycle that did it."""
    key = pwstore.key_of(entry)
    try:
        commits = new_commits(entry, before)
        for sha, subject, body in commits[:COMMITS_MAX]:
            pwstore.log(f"{key}: claude committed {sha[:10]}: {subject}" + (f"\n{body}" if body else ''))
        if len(commits) > COMMITS_MAX:
            pwstore.log(f"{key}: claude committed {len(commits) - COMMITS_MAX} more commit(s), not listed")
        replies, resolved = comment_activity(entry, cfg, started)
        for number, line in replies:
            pwstore.log(f"{key}: claude replied on comment {number}" + (f": {line}" if line else ''))
        for number in resolved:
            pwstore.log(f"{key}: claude resolved the thread of comment {number}")
    except Exception as error:  # noqa: BLE001 - see the docstring
        pwstore.log(f"{key}: what claude did could not be listed ({type(error).__name__}: {error})")
