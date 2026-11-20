"""The editors of the machine and their Claude plugins: where they are installed, which version, and what they answer to."""

import glob
import json
import os
import shutil
import subprocess
import zipfile

from prwatch.system import host as pwhost

OPEN_URL = '@open-url'


def launcher_of(exe):
    """The command that opens a project in the IDE already running: the snap wrapper for a snap, else the binary."""
    if pwhost.SYSTEM == 'linux' and exe.startswith('/snap/'):
        wrapper = f"/snap/bin/{exe.split('/')[2]}"
        if os.path.exists(wrapper):
            return wrapper
    return exe


def vscode_cli(exe):
    """VS Code's `code` command: the one on the PATH, else the one inside the macOS bundle, else the binary itself."""
    found = shutil.which('code')
    if found:
        return found
    if pwhost.SYSTEM == 'macos' and '.app/' in exe:
        bundled = exe.split('.app/')[0] + '.app/Contents/Resources/app/bin/code'
        if os.path.exists(bundled):
            return bundled
    return launcher_of(exe)


def plugin_dirs():
    if pwhost.SYSTEM == 'macos':
        return [os.path.expanduser('~/Library/Application Support/JetBrains/*/claude-chat/lib/*.jar')]
    if pwhost.SYSTEM == 'windows':
        return [os.path.join(os.environ.get('APPDATA', ''), 'JetBrains', '*', 'claude-chat', 'lib', '*.jar')]
    return [os.path.expanduser('~/.local/share/JetBrains/*/claude-chat/lib/*.jar')]


def jetbrains_plugin_has_links():
    """Whether the Claude Chat plugin installed in the IDEs knows the `claude-chat` link (version 0.9.8 and later)."""
    for pattern in plugin_dirs():
        for jar in glob.glob(pattern):
            try:
                with zipfile.ZipFile(jar) as archive:
                    if 'dev/claudechat/OpenSessionCommand.class' in archive.namelist():
                        return True
            except (OSError, zipfile.BadZipFile):
                continue
    return False


def jetbrains_plugins():
    """Which Claude plugins the IDEs of this machine have: 'chat' (Claude Chat), 'code' (Anthropic's Claude Code)."""
    if pwhost.SYSTEM == 'macos':
        roots = [os.path.expanduser('~/Library/Application Support/JetBrains/*')]
    elif pwhost.SYSTEM == 'windows':
        roots = [os.path.join(os.environ.get('APPDATA', ''), 'JetBrains', '*')]
    else:
        roots = [os.path.expanduser('~/.local/share/JetBrains/*')]
    found = set()
    for root in roots:
        if glob.glob(os.path.join(root, 'claude-chat')):
            found.add('chat')
        if glob.glob(os.path.join(root, 'claude-code-jetbrains-plugin')):
            found.add('code')
    return found


def jetbrains_plugin_dirs(name='claude-chat'):
    """The folders of the plugin `name` in the settings of each IDE of the machine (one per IDE version)."""
    if pwhost.SYSTEM == 'macos':
        roots = [os.path.expanduser('~/Library/Application Support/JetBrains/*')]
    elif pwhost.SYSTEM == 'windows':
        roots = [os.path.join(os.environ.get('APPDATA', ''), 'JetBrains', '*')]
    else:
        roots = [os.path.expanduser('~/.local/share/JetBrains/*')]
    return sorted(path for root in roots for path in glob.glob(os.path.join(root, name)) if os.path.isdir(path))


def vscode_extension_dirs(prefix='pleday.claude-chat-vscode-'):
    """The folders of the installed versions of a VS Code extension."""
    return sorted(glob.glob(os.path.expanduser(f'~/.vscode/extensions/{prefix}*')))


def install_vscode_extension(vsix):
    """Installs (or updates) an extension from a .vsix file with the `code` command; returns (ok, what it printed)."""
    code = shutil.which('code')
    if not code:
        return False, 'La commande `code` est introuvable.'
    try:
        done = subprocess.run([code, '--install-extension', vsix, '--force'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def vscode_plugins():
    """Which Claude extensions VS Code has: 'chat' (Claude Chat), 'code' (Anthropic's Claude Code)."""
    root = os.path.expanduser('~/.vscode/extensions')
    found = set()
    if glob.glob(os.path.join(root, 'pleday.claude-chat-vscode-*')):
        found.add('chat')
    if glob.glob(os.path.join(root, 'anthropic.claude-code-*')):
        found.add('code')
    return found


def vscode_extension_has_links():
    """Whether the Claude Chat extension installed in VS Code answers to `vscode://pleday.claude-chat-vscode/session`."""
    for manifest in glob.glob(os.path.expanduser('~/.vscode/extensions/pleday.claude-chat-vscode-*/package.json')):
        try:
            with open(manifest, encoding='utf-8') as handle:
                if 'onUri' in json.load(handle).get('activationEvents', []):
                    return True
        except (OSError, ValueError):
            continue
    return False


def _jetbrains_launcher():
    for name in ('idea', 'idea.sh', 'intellij-idea-ultimate', 'intellij-idea-community', 'pycharm', 'webstorm', 'goland', 'phpstorm', 'rider', 'clion'):
        found = shutil.which(name) or (f'/snap/bin/{name}' if os.path.exists(f'/snap/bin/{name}') else None)
        if found:
            return found
    return None


def _vscode_installed():
    return bool(shutil.which('code') or (pwhost.SYSTEM == 'macos' and os.path.exists('/Applications/Visual Studio Code.app')))
