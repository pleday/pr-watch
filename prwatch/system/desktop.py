"""PR Watch as an application of the system: its entry in the list of applications and its launcher."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from prwatch.core import store as pwstore
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications
from prwatch.system import window as pwwindow

def _exec_quote(word):
    """One word of an Exec= line, quoted the way the Desktop Entry specification wants."""
    if re.fullmatch(r'[\w@+=:,./-]+', word):
        return word
    return '"' + re.sub(r'(["`$\\])', r'\\\1', word) + '"'


def exec_line(*args):
    """The Exec= line of a desktop entry that runs `prwatch <args>` (a checkout is put on the PYTHONPATH)."""
    words = pwhost.module_command(*args)
    if pwhost.from_checkout():
        words = ['env', f'PYTHONPATH={pwhost.package_root()}', *words]
    return ' '.join(_exec_quote(word) for word in words)


APP_ID = pwhost.APP_ID


ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#1f6feb"/>
  <circle cx="20" cy="18" r="6" fill="none" stroke="#fff" stroke-width="4"/>
  <circle cx="20" cy="46" r="6" fill="none" stroke="#fff" stroke-width="4"/>
  <circle cx="46" cy="46" r="6" fill="none" stroke="#fff" stroke-width="4"/>
  <path d="M20 24v16M46 40V26a8 8 0 0 0-8-8h-6" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round"/>
  <path d="M36 11l-6 7 6 7" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def install_desktop(announce=True):
    """Registers PR Watch as an application of the desktop (launcher entry and icon), so it is listed and found by name."""
    if pwhost.SYSTEM != 'linux':
        return
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    icon = data / 'icons' / 'hicolor' / 'scalable' / 'apps' / f'{APP_ID}.svg'
    entry = data / 'applications' / f'{APP_ID}.desktop'
    icon.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    if not icon.exists() or icon.read_text(encoding='utf-8') != ICON_SVG:
        icon.write_text(ICON_SVG, encoding='utf-8')
    content = (
        '[Desktop Entry]\n'
        'Type=Application\n'
        'Name=PR Watch\n'
        'GenericName=Pull request watcher\n'
        'Comment=Pull requests watched by Claude: pipelines, reviews, Sonar, merge\n'
        'Comment[fr]=Pull requests surveillées par Claude : pipelines, revues, Sonar, merge\n'
        f'Exec={exec_line("ui")}\n'
        f'Icon={APP_ID}\n'
        'Terminal=false\n'
        'Categories=Development;\n'
        'Keywords=pull request;bitbucket;claude;pipeline;sonar;\n'
        'StartupNotify=true\n'
        f'StartupWMClass={pwwindow.WEB_WINDOW_CLASS}\n'
    )
    if not entry.exists() or entry.read_text(encoding='utf-8') != content:
        entry.write_text(content, encoding='utf-8')
        try:
            subprocess.run(['update-desktop-database', str(entry.parent)], capture_output=True, check=False)
        except OSError:
            pass  # the tool is optional: desktops also notice the new entry on their own
        pwstore.log(f'application registered: {entry}')
    if announce:
        print(f'application: {entry}')


def install_launcher():
    """Registers PR Watch as an application on this system (the dashboard is the window on macOS and Windows)."""
    if pwhost.SYSTEM == 'linux':
        return install_desktop()
    if pwhost.SYSTEM == 'macos':
        app = Path.home() / 'Applications' / 'PR Watch.app'
        (app / 'Contents' / 'MacOS').mkdir(parents=True, exist_ok=True)
        (app / 'Contents' / 'Info.plist').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict><key>CFBundleName</key><string>PR Watch</string><key>CFBundleDisplayName</key><string>PR Watch</string>'
            '<key>CFBundleIdentifier</key><string>org.prwatch.PrWatch</string><key>CFBundleExecutable</key><string>pr-watch-ui</string>'
            '<key>CFBundlePackageType</key><string>APPL</string><key>LSUIElement</key><false/></dict></plist>\n',
            encoding='utf-8',
        )
        launcher = app / 'Contents' / 'MacOS' / 'pr-watch-ui'
        command = ' '.join(shlex.quote(word) for word in pwhost.module_command('ui'))
        path = f'export PYTHONPATH={shlex.quote(pwhost.package_root())}${{PYTHONPATH:+:$PYTHONPATH}}\n' if pwhost.from_checkout() else ''
        launcher.write_text(f'#!/bin/sh\n{path}exec {command}\n', encoding='utf-8')
        launcher.chmod(0o755)
        print(f'application: {app}')
        return
    if pwhost.SYSTEM == 'windows':
        programs = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        shell = str(pythonw if pythonw.exists() else sys.executable)
        shortcut = programs / 'PR Watch.lnk'
        command = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('" + pwnotifications._powershell(str(shortcut)) + "'); "
            "$s.TargetPath = '" + pwnotifications._powershell(shell) + "'; "
            "$s.Arguments = '-m prwatch ui'; $s.WorkingDirectory = '" + pwnotifications._powershell(pwhost.package_root() if pwhost.from_checkout() else str(Path.home())) + "'; "
            "$s.Description = 'PR Watch'; $s.Save()"
        )
        subprocess.run(['powershell', '-NoProfile', '-Command', command], check=False)
        print(f'application: {shortcut}')
