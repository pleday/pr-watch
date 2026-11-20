"""Bringing a Claude conversation on screen: the links and commands that open it in each application, and where it can be opened."""

import os
import shutil
import subprocess
import threading
import time
import urllib.parse

from prwatch.system import claude as pwclaude
from prwatch.system import editors as pweditors
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications

def link_for(host, entry, cwd):
    """The link that opens the conversation in `host`, when the application answers to one (Claude Desktop, VS Code).

    The dashboard opens it itself (the browser hands it to the system with the activation token of the click), which is
    what lets the application take the focus on Wayland: a program started in the background is refused it.
    """
    session = entry['sessionId']
    if host == 'desktop':
        local = pwclaude.desktop_session_id(session)
        return f"claude://code/continue?session={local}&source=pr-watch" if local else None
    if host == 'vscode-code':
        return f"vscode://anthropic.claude-code/open?session={session}"
    if host in ('vscode', 'vscode-chat') and pweditors.vscode_extension_has_links():
        return f"vscode://pleday.claude-chat-vscode/session?id={session}&cwd={urllib.parse.quote(cwd, safe='')}"
    return None


def open_plan(entry, found, title):
    """What to run to bring the conversation of `found` on screen, and what to tell the user.

    Returns (list of commands run one after the other, message). A command is an argv, or `[OPEN_URL, url]` to hand a
    link to the system. Claude Desktop, and the editors once their Claude Chat plugin is recent enough, open the precise
    conversation through a link carrying the session id. Otherwise an editor can only be asked to show the window of the
    project, and the tab is left to pick.
    """
    label = f"« {title} »" if title else entry['sessionId'][:8] + '…'
    host, cwd, session = found['host'], found['cwd'] or entry['sessionCwd'], entry['sessionId']
    if host == 'desktop':
        link = link_for('desktop', entry, cwd)
        if not link:
            return [], f"Session {label} : active dans Claude Desktop, mais sans conversation connue de lui : ouvrez-la à la main."
        return [[pweditors.OPEN_URL, link]], f"Session {label} : ouverte dans Claude Desktop."
    if host == 'jetbrains':
        launcher = pweditors.launcher_of(found['host_exe'])
        if pweditors.jetbrains_plugin_has_links():
            product = pwhost._name(found['host_exe']).rstrip('0123456789') or 'idea'
            url = f"jetbrains://{product}/claude-chat/{session}?cwd={urllib.parse.quote(cwd, safe='')}"
            return [[launcher, url]], f"Session {label} : onglet sélectionné dans l’IDE."
        return (
            [[launcher, cwd]],
            f"Session {label} : fenêtre de l’IDE au premier plan. Choisissez l’onglet « {title or 'Claude Chat'} » dans "
            'Claude Chat. Installez le plugin Claude Chat 0.9.8 ou plus pour que l’onglet s’ouvre tout seul.',
        )
    if host == 'vscode':
        code = pweditors.vscode_cli(found['host_exe'])
        focus = [code, '--reuse-window', cwd]
        if pweditors.vscode_extension_has_links():
            url = f"vscode://pleday.claude-chat-vscode/session?id={session}&cwd={urllib.parse.quote(cwd, safe='')}"
            return [focus, [code, '--open-url', url]], f"Session {label} : conversation affichée dans VS Code."
        return (
            [focus],
            f"Session {label} : fenêtre VS Code au premier plan. Choisissez la conversation « {title or 'Claude Chat'} » "
            'dans Claude Chat. Installez l’extension Claude Chat 0.9.7 ou plus pour qu’elle s’ouvre toute seule.',
        )
    if host == 'terminal':
        why = ' Wayland ne permet pas de ramener la fenêtre d’une autre application :' if pwhost.SYSTEM == 'linux' else ''
        return [], f"Session {label} : active dans un terminal (processus {found['pid']}).{why} ouvrez-la à la main."
    return [], f"Session {label} : active (processus {found['pid']}) dans une application que je ne sais pas ouvrir."


APP_LABELS = {
    'desktop': 'Claude Desktop', 'terminal': 'Claude Code (terminal)',
    'jetbrains': 'IntelliJ / IDE JetBrains', 'jetbrains-chat': 'IntelliJ · Claude Chat',
    'vscode': 'VS Code', 'vscode-chat': 'VS Code · Claude Chat', 'vscode-code': 'VS Code · Claude Code',
}


# The part of an application id before the first dash: the application itself (the rest names the plugin).
def app_of(app):
    return app.split('-')[0]


def available_apps(entry=None):
    """Where a conversation that no application hosts can really be resumed: [{'id', 'label'}, ...].

    Only the places that open that very conversation, not just a window: Claude Desktop when it knows it, an editor with
    a Claude plugin recent enough to open a conversation from a link, and Claude Code in the default terminal.
    """
    ids = ['desktop'] if entry is None or pwclaude.desktop_session_id(entry['sessionId']) else []
    if pweditors._jetbrains_launcher() and 'chat' in pweditors.jetbrains_plugins() and pweditors.jetbrains_plugin_has_links():
        ids.append('jetbrains-chat')
    if pweditors._vscode_installed():
        plugins = pweditors.vscode_plugins()
        if 'chat' in plugins and pweditors.vscode_extension_has_links():
            ids.append('vscode-chat')
        if 'code' in plugins:
            ids.append('vscode-code')
    ids.append('terminal')
    return [{'id': i, 'label': APP_LABELS[i]} for i in ids]


def apps_hints(entry=None):
    """Why an application the user may expect is not offered, one sentence each (empty when nothing is missing)."""
    hints = []
    if entry is not None and not pwclaude.desktop_session_id(entry['sessionId']) and os.path.isdir(os.path.dirname(next(iter(pwclaude.desktop_sessions_dirs()), ''))):
        hints.append('Claude Desktop : cette conversation n’existe pas dans Desktop.')
    if pweditors._jetbrains_launcher() and 'chat' in pweditors.jetbrains_plugins() and not pweditors.jetbrains_plugin_has_links():
        hints.append('IntelliJ · Claude Chat : plugin trop ancien, il faut la version 0.9.8 ou plus pour reprendre une conversation.')
    if pweditors._vscode_installed() and 'chat' in pweditors.vscode_plugins() and not pweditors.vscode_extension_has_links():
        hints.append('VS Code · Claude Chat : extension trop ancienne, il faut la version 0.9.7 ou plus pour reprendre une conversation.')
    return hints


def open_in_plan(app, entry, title):
    """Like `open_plan`, for an application chosen by the user instead of the one found running (the session is not active)."""
    host = app_of(app)
    if host == 'jetbrains':
        exe = pweditors._jetbrains_launcher()
    elif host == 'vscode':
        exe = shutil.which('code') or 'code'
    elif host == 'desktop':
        exe = ''
    else:
        return None
    if exe is None:
        return None
    if app == 'vscode-code':
        code = exe
        url = link_for(app, entry, entry['sessionCwd'])
        label = f"« {title} »" if title else entry['sessionId'][:8] + '…'
        return [[code, '--reuse-window', entry['sessionCwd']], [code, '--open-url', url]], f"Session {label} : ouverte dans l’extension Claude Code de VS Code."
    return open_plan(entry, {'host': host, 'host_exe': exe, 'cwd': entry['sessionCwd'], 'pid': 0}, title)


# What the window manager calls the windows of each application (WM_CLASS), for `raise_app`.
WINDOW_CLASSES = {'desktop': 'claude', 'jetbrains': 'jetbrains', 'vscode': 'code', 'dashboard': 'chrome-127.0.0.1'}


def raise_app(app, delay=1.5):
    """Best effort: brings the window of `app` to the front once it had the time to open it.

    X11 only (wmctrl or xdotool): Wayland does not let a program raise the window of another one. The editors and
    Claude Desktop usually come to the front by themselves when asked to open something; this is for when they do not.
    """
    name = WINDOW_CLASSES.get(app_of(app))
    if pwhost.SYSTEM != 'linux' or not name or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        return
    import threading

    def work():
        time.sleep(delay)
        if shutil.which('wmctrl'):
            pwhost._run(['wmctrl', '-x', '-a', name], timeout=5)
        elif shutil.which('xdotool'):
            pwhost._run(['xdotool', 'search', '--class', name, 'windowactivate'], timeout=5)

    threading.Thread(target=work, daemon=True).start()


def run_commands(commands, cwd=None):
    """Runs the commands of an `open_plan`; all but the last are waited for (the window must be in front before the link)."""
    for index, argv in enumerate(commands):
        last = index == len(commands) - 1
        if argv[0] == pweditors.OPEN_URL:
            pwnotifications.open_url(argv[1])
        elif last:
            subprocess.Popen(argv, cwd=cwd, **({'start_new_session': True} if pwhost.SYSTEM != 'windows' else {}))
        else:
            subprocess.run(argv, cwd=cwd, check=False, timeout=20)
