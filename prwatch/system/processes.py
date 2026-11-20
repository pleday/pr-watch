"""The processes of the machine: the table, which application hosts a Claude session, and where a session was opened from."""

import json
import os
import re

from prwatch.system import host as pwhost
from prwatch.system import terminal as pwterminal

JETBRAINS = ('idea', 'pycharm', 'webstorm', 'goland', 'clion', 'phpstorm', 'rider', 'rubymine', 'datagrip', 'studio')


def parse_ps(comm_output, args_output):
    """macOS: `ps -axo pid=,ppid=,comm=` (executable, may hold spaces) and `ps -axo pid=,args=` (whole command line)."""
    table = {}
    for line in comm_output.splitlines():
        match = re.match(r'\s*(\d+)\s+(\d+)\s+(.*\S)\s*$', line)
        if match:
            table[int(match.group(1))] = {'pid': int(match.group(1)), 'ppid': int(match.group(2)), 'exe': match.group(3), 'cmd': ''}
    for line in args_output.splitlines():
        match = re.match(r'\s*(\d+)\s+(.*\S)\s*$', line)
        if match and int(match.group(1)) in table:
            table[int(match.group(1))]['cmd'] = match.group(2)
    return table


def parse_cim(output):
    """Windows: the JSON of `Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,CommandLine,ExecutablePath`."""
    try:
        data = json.loads(output or '[]')
    except ValueError:
        return {}
    if isinstance(data, dict):
        data = [data]
    return {
        int(p['ProcessId']): {
            'pid': int(p['ProcessId']),
            'ppid': int(p.get('ParentProcessId') or 0),
            'exe': p.get('ExecutablePath') or '',
            'cmd': p.get('CommandLine') or '',
        }
        for p in data
        if p.get('ProcessId') is not None
    }


def _linux_table():
    table = {}
    for pid in (int(p) for p in os.listdir('/proc') if p.isdigit()):
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as handle:
                args = handle.read().decode(errors='replace').split('\0')
            with open(f'/proc/{pid}/stat', 'rb') as handle:
                ppid = int(handle.read().decode().rsplit(')', 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        if args and args[0]:
            table[pid] = {'pid': pid, 'ppid': ppid, 'exe': args[0], 'cmd': ' '.join(a for a in args if a)}
    return table


def process_table():
    """pid -> {pid, ppid, exe, cmd} for the processes of the machine."""
    if pwhost.SYSTEM == 'macos':
        return parse_ps(pwhost._run(['ps', '-axo', 'pid=,ppid=,comm=']), pwhost._run(['ps', '-axo', 'pid=,args=']))
    if pwhost.SYSTEM == 'windows':
        script = 'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine,ExecutablePath | ConvertTo-Json -Compress'
        return parse_cim(pwhost._run(['powershell', '-NoProfile', '-Command', script], timeout=60))
    return _linux_table()


def classify(exe, cmd=''):
    """The kind of application a process is: the IDE, VS Code, Claude Desktop or a terminal (None for anything else)."""
    name, low = pwhost._name(exe), (exe or cmd or '').replace('\\', '/').lower()
    if any(name.startswith(n) for n in JETBRAINS):
        return 'jetbrains'
    if 'claude-desktop' in low or '/claude.app/' in low or '/anthropicclaude/' in low or low.endswith('/claude/claude.exe'):
        return 'desktop'
    if name in ('code', 'code-insiders', 'cursor') or 'visual studio code' in low or '/microsoft vs code/' in low or '/code/' in low:
        return 'vscode'
    if name in pwterminal.TERMINALS or any(t in name for t in pwterminal.TERMINALS if len(t) > 5):
        return 'terminal'
    return None


def _cwd_of(pid):
    if pwhost.SYSTEM == 'linux':
        try:
            return os.readlink(f'/proc/{pid}/cwd')
        except OSError:
            return None
    if pwhost.SYSTEM == 'macos':
        for line in pwhost._run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn']).splitlines():
            if line.startswith('n'):
                return line[1:]
    return None


def _entrypoint_of(pid):
    if pwhost.SYSTEM != 'linux':
        return ''
    try:
        with open(f'/proc/{pid}/environ', 'rb') as handle:
            for item in handle.read().decode(errors='replace').split('\0'):
                if item.startswith('CLAUDE_CODE_ENTRYPOINT='):
                    return item.split('=', 1)[1]
    except OSError:
        pass
    return ''


def find_session_process(session_id, table=None):
    """The running `claude` process of a session, with the application hosting it (IDE, desktop app, terminal).

    The editors and the desktop app keep one `claude --resume <id>` child per open conversation, so the session is
    "active" exactly when such a process exists.
    """
    table = table if table is not None else process_table()
    for pid, process in table.items():
        tokens = process['cmd'].replace('"', ' ').split()
        if session_id not in tokens or 'claude' not in pwhost._name(process['exe']):
            continue
        host, cursor, parent = None, pid, None
        for _ in range(12):
            cursor = table.get(cursor, {}).get('ppid', 0)
            parent = table.get(cursor)
            if not parent or cursor <= 1:
                break
            host = classify(parent['exe'], parent['cmd'])
            if host:
                break
        entrypoint = _entrypoint_of(pid)
        if not host and entrypoint.startswith('jetbrains'):
            host = 'jetbrains'
        return {
            'pid': pid,
            'host': host,
            'host_exe': parent['exe'] if host and parent else None,
            'host_pid': cursor if host else None,
            'cwd': _cwd_of(pid),
            'entrypoint': entrypoint,
        }
    return None


def origin_of_this_process(table=None):
    """Where the command that runs this code was launched from: {'host', 'entrypoint'} (the application that hosts the Claude
    session whose tool started it), None when no application is recognised above it.

    `pr-watch register` is run by Claude Code itself, so its ancestors lead to the IDE, the desktop app or the terminal that
    holds the session, and `CLAUDE_CODE_ENTRYPOINT` in its environment names the front end (`claude-vscode`, `cli`…).
    """
    table = table if table is not None else process_table()
    entrypoint = os.environ.get('CLAUDE_CODE_ENTRYPOINT', '')
    cursor, host = os.getpid(), None
    for _ in range(25):
        parent = table.get(table.get(cursor, {}).get('ppid', 0))
        if not parent:
            break
        cursor = parent['pid']
        host = classify(parent['exe'], parent['cmd'])
        if host:
            break
    if not host and entrypoint.startswith('jetbrains'):
        host = 'jetbrains'
    if not host and entrypoint == 'cli':
        host = 'terminal'
    return {'host': host, 'entrypoint': entrypoint} if host or entrypoint else None


ORIGIN_LABELS = {'desktop': 'Claude Desktop', 'jetbrains': 'IntelliJ', 'vscode': 'VS Code', 'terminal': 'un terminal'}


def recommended_app(origin, offered):
    """Among the ids `offered` (see `available_apps`), the one that matches where the session was opened, None if none does."""
    if not origin:
        return None
    host, entrypoint = origin.get('host'), origin.get('entrypoint') or ''
    if host == 'vscode':
        wanted = ['vscode-code', 'vscode-chat'] if entrypoint.startswith('claude-vscode') else ['vscode-chat', 'vscode-code']
    elif host == 'jetbrains':
        wanted = ['jetbrains-chat']
    elif host == 'desktop':
        wanted = ['desktop']
    else:
        wanted = ['terminal']
    return next((app for app in wanted if app in offered), None)
