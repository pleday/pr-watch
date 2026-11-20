"""The window of the web dashboard: a browser opened in application mode, or the default browser."""

import os
import shutil
import subprocess

from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications

def chromium_candidates():
    if pwhost.SYSTEM == 'macos':
        return [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ]
    if pwhost.SYSTEM == 'windows':
        roots = [os.environ.get(k, '') for k in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
        parts = [('Google', 'Chrome', 'Application', 'chrome.exe'), ('Microsoft', 'Edge', 'Application', 'msedge.exe'), ('BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe')]
        return [os.path.join(root, *part) for part in parts for root in roots if root]
    names = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'brave-browser')
    return [found for found in (shutil.which(n) for n in names) if found]


# What Chrome calls the window of `--app=http://127.0.0.1:<port>/` (Wayland app id, X11 class): `chrome-<host>__<path>-<profile>`.
# The desktop entry of PR Watch declares it as its StartupWMClass, which is how the dock gives the window the PR Watch name and icon.
WEB_WINDOW_CLASS = 'chrome-127.0.0.1__-Default'


def app_window_command(url, profile=None):
    """A Chromium-based browser opened in 'app' mode (a window with no tabs or address bar), or None when there is none.

    `profile` is a directory for a profile of its own: the window then lives in a browser process of its own, apart from
    the tabs of the user, and a second call reuses that window instead of opening another one.
    """
    for exe in chromium_candidates():
        if os.path.exists(exe):
            command = [exe, f'--app={url}', '--window-size=1180,780', '--no-first-run', '--no-default-browser-check']
            if profile:
                command.append(f'--user-data-dir={profile}')
            if pwhost.SYSTEM == 'linux':
                command.append(f'--class={WEB_WINDOW_CLASS}')  # X11; Wayland builds the id from the address
            return command
    return None


def open_app_window(url, profile=None):
    """Shows a page as an application window when a Chromium-based browser is installed, else in the default browser.

    Every launch opens a window, even for an address already shown. With the activation token of a click on the icon or a
    notification (`XDG_ACTIVATION_TOKEN`) Wayland lets the new window take the focus, and the window that was open closes
    itself on seeing it (see the dashboard's "a single window"): the one that remains is the one in front.
    """
    command = app_window_command(url, profile)
    if command:
        # a click on a notification hands over an activation token: it lets the window come to the front (Wayland)
        token = os.environ.get('XDG_ACTIVATION_TOKEN')
        env = {**pwhost.clean_env(), 'XDG_ACTIVATION_TOKEN': token, 'DESKTOP_STARTUP_ID': token} if token else None
        pwhost.spawn_detached(pwhost.in_app_scope(command, pwhost.APP_ID), subprocess.DEVNULL, env=env, keep_launch_vars=bool(token))
    else:
        pwnotifications.open_url(url)
