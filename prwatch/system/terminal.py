"""The terminal of the system: the command that opens one running a program."""

import os
import shlex
import shutil
import subprocess

from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications

TERMINALS = (
    'gnome-terminal', 'kgx', 'konsole', 'alacritty', 'kitty', 'xterm', 'tilix', 'tmux', 'wezterm', 'foot',
    'terminal', 'iterm2', 'windowsterminal', 'cmd', 'powershell', 'pwsh', 'conhost',
)


def terminal_command(cwd, argv):
    """A command opening a new terminal in `cwd` that runs `argv`; None when no terminal is known."""
    if pwhost.SYSTEM == 'macos':
        line = f"cd {shlex.quote(cwd)} && {' '.join(shlex.quote(a) for a in argv)}"
        return ['osascript', '-e', f'tell application "Terminal" to do script "{pwnotifications._applescript(line)}"', '-e', 'tell application "Terminal" to activate']
    if pwhost.SYSTEM == 'windows':
        return ['cmd', '/c', 'start', 'cmd', '/k', f'cd /d "{cwd}" && ' + subprocess.list2cmdline(argv)]
    prefix = default_terminal()
    return [*prefix, *argv] if prefix else None


# How each terminal is told to run a command (the arguments that come before it); `-e` for the ones not listed.
TERMINAL_RUN_ARGS = {
    'gnome-terminal': ['--'], 'kgx': ['--'], 'ptyxis': ['--'], 'terminator': ['-x'], 'tilix': ['-e'], 'konsole': ['-e'], 'xterm': ['-e'],
    'alacritty': ['-e'], 'kitty': [], 'foot': [], 'wezterm': ['start', '--'], 'xfce4-terminal': ['-x'], 'mate-terminal': ['-x'],
    'lxterminal': ['-e'], 'urxvt': ['-e'], 'st': ['-e'],
}


def default_terminal():
    """The command line, without the program to run, of the terminal the system has set as its default (Linux).

    GNOME's own setting first (it names the program and the argument that introduces the command), then the Debian
    `x-terminal-emulator` alternative, then the freedesktop launcher, then the usual terminals.
    """
    out = pwhost._run(['gsettings', 'get', 'org.gnome.desktop.default-applications.terminal', 'exec']).strip().strip("'")
    if out and shutil.which(out):
        arg = pwhost._run(['gsettings', 'get', 'org.gnome.desktop.default-applications.terminal', 'exec-arg']).strip().strip("'")
        return [shutil.which(out), *([arg] if arg else TERMINAL_RUN_ARGS.get(os.path.basename(out), ['-e']))]
    alternative = shutil.which('x-terminal-emulator')
    if alternative:
        name = os.path.basename(os.path.realpath(alternative))
        return [alternative, *TERMINAL_RUN_ARGS.get(name, ['-e'])]
    if shutil.which('xdg-terminal-exec'):
        return [shutil.which('xdg-terminal-exec')]
    for name in ('gnome-terminal', 'ptyxis', 'konsole', 'xfce4-terminal', 'xterm'):
        found = shutil.which(name)
        if found:
            return [found, *TERMINAL_RUN_ARGS.get(name, ['-e'])]
    return None
