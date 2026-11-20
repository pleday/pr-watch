"""What Claude Code and Claude Desktop keep on disk: the transcripts and titles of conversations, the sessions of a folder."""

import glob
import json
import os
import re
import subprocess

from prwatch.system import host as pwhost

_TITLES = {}


def session_title(path):
    """The title the editors show on the tab of a conversation: the last `ai-title` of its transcript."""
    if not path:
        return ''
    # The transcript is several MB and the window refreshes every few seconds: read it again only when it changed.
    stamp = os.path.getmtime(path)
    cached = _TITLES.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    title = ''
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if '"ai-title"' in line:
                try:
                    title = json.loads(line).get('aiTitle') or title
                except ValueError:
                    pass
    _TITLES[path] = (stamp, title)
    return title


def claude_project_dir(cwd):
    """The folder where Claude Code keeps the transcripts of the sessions started in `cwd`."""
    return os.path.join(os.path.expanduser('~/.claude/projects'), re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(cwd)))


def claude_sessions(cwd, limit=15):
    """The latest sessions started in `cwd`, newest first: [{'id', 'title', 'modified' (epoch seconds)}]."""
    found = []
    for path in glob.glob(os.path.join(claude_project_dir(cwd), '*.jsonl')):
        try:
            found.append((os.path.getmtime(path), path))
        except OSError:
            continue
    found.sort(reverse=True)
    return [{'id': os.path.basename(path)[:-6], 'title': session_title(path), 'modified': modified} for modified, path in found[:limit]]


def desktop_sessions_dirs():
    if pwhost.SYSTEM == 'macos':
        return [os.path.expanduser('~/Library/Application Support/Claude/claude-code-sessions')]
    if pwhost.SYSTEM == 'windows':
        return [os.path.join(os.environ.get('APPDATA', ''), 'Claude', 'claude-code-sessions')]
    return [os.path.expanduser('~/.config/Claude/claude-code-sessions')]


def desktop_session_id(cli_session_id):
    """The id Claude Desktop gives (`local_…`) to the conversation of a Claude Code session, None when it has none.

    Desktop keeps one record per conversation, naming the Claude Code session it runs (`cliSessionId`, and the earlier ones
    after a resume). Its `claude://code/continue?session=` link accepts only that `local_…` id (or `last`): the session id of
    Claude Code is refused, and the app then just comes up on whatever it showed last.
    """
    for root in desktop_sessions_dirs():
        for path in glob.glob(os.path.join(root, '*', '*', 'local_*.json')):
            try:
                with open(path, encoding='utf-8') as handle:
                    record = json.load(handle)
            except (OSError, ValueError):
                continue
            if cli_session_id == record.get('cliSessionId') or cli_session_id in (record.get('previousCliSessionIds') or []):
                return record.get('sessionId') or os.path.basename(path).removesuffix('.json')
    return None


def session_cwd(session_id):
    """The folder a Claude Code session was started in, read from its transcript (None when the transcript is not found)."""
    for path in glob.glob(os.path.join(os.path.expanduser('~/.claude/projects'), '*', f'{session_id}.jsonl')):
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    if '"cwd"' in line:
                        found = json.loads(line).get('cwd')
                        if found:
                            return found
        except (OSError, ValueError):
            continue
    return None


def ask(binary, prompt, timeout=90):
    """One answer from `claude -p` to a prompt that needs no tool, None when it cannot be had (not installed, too slow, failed)."""
    try:
        done = subprocess.run([binary, '-p'], input=prompt, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    answer = done.stdout.strip()
    return answer if done.returncode == 0 and answer else None
