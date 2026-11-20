"""Desktop notifications, with a click that opens the dashboard on the pull request, and the opening of links."""

import json
import os
import shutil
import subprocess
import threading

from prwatch.core import store as pwstore
from prwatch.system import host as pwhost

def _applescript(text):
    return text.replace('\\', '\\\\').replace('"', '\\"')


def _powershell(text):
    return text.replace("'", "''")


NOTIFY_APP = 'PR Watch'


NOTIFY_ICON = pwhost.APP_ID


_actions_supported = None


_token_supported = [False]  # whether notify-send can hand over the activation token of the click


def notify_send_has_actions():
    """Whether `notify-send` can wait for a click on the notification (libnotify 0.8 and later)."""
    global _actions_supported
    if _actions_supported is None:
        try:
            help_text = subprocess.run(['notify-send', '--help'], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            help_text = ''
        _actions_supported = '--action' in help_text
        _token_supported[0] = '--activation-token-fd' in help_text
    return _actions_supported


IDS_FILE = 'notification-ids.json'  # group (a pull request) -> ids of its notifications on screen, shared by the daemon and the dashboard


def _group_ids():
    try:
        data = json.loads((pwstore.DIR / IDS_FILE).read_text(encoding='utf-8'))
        return {str(k): [int(i) for i in v] for k, v in data.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_ids(ids):
    try:
        (pwstore.DIR / IDS_FILE).write_text(json.dumps(ids), encoding='utf-8')
    except OSError:
        pass


def _remember(group, notification_id):
    ids = _group_ids()
    if notification_id not in ids.setdefault(group, []):
        ids[group].append(notification_id)
    _save_ids(ids)


def _forget(group, notification_id):
    ids = _group_ids()
    if notification_id in ids.get(group, []):
        ids[group].remove(notification_id)
        if not ids[group]:
            del ids[group]
        _save_ids(ids)


def close_group(group):
    """Takes every notification of a group off the screen (Linux): the count on the icon of the application goes down with them."""
    ids = _group_ids()
    found = ids.pop(group, [])
    if not found or pwhost.SYSTEM != 'linux' or not shutil.which('gdbus'):
        return
    _save_ids(ids)
    for notification_id in found:
        try:
            subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications', '--object-path', '/org/freedesktop/Notifications',
                            '--method', 'org.freedesktop.Notifications.CloseNotification', str(notification_id)],
                           check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=pwhost.clean_env())
        except (OSError, subprocess.SubprocessError):
            pass


def notification_command(title, body, urgent=False, clickable=False, token_fd=None):
    """The command that shows a desktop notification on this system; None when there is no way to.

    `clickable` (Linux only) makes the command wait and print `default` when the user clicks the notification; with
    `token_fd` it also writes there the activation token of the click, which lets the window that opens take the focus.
    """
    if pwhost.SYSTEM == 'macos':
        return ['osascript', '-e', f'display notification "{_applescript(body)}" with title "{_applescript(title)}"']
    if pwhost.SYSTEM == 'windows':
        script = (
            'Add-Type -AssemblyName System.Windows.Forms, System.Drawing; '
            '$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; '
            f"$n.Visible = $true; $n.ShowBalloonTip(10000, '{_powershell(title)}', '{_powershell(body)}', 'Info'); "
            'Start-Sleep -Seconds 11; $n.Dispose()'
        )
        return ['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', script]
    if shutil.which('notify-send'):
        # desktop-entry ties the notification to the PR Watch application: GNOME shows its name and icon, and a click raises
        # the window of the application (found through the StartupWMClass of the entry) besides running the action.
        command = ['notify-send', '-a', NOTIFY_APP, '-i', NOTIFY_ICON, '-u', 'critical' if urgent else 'normal', f'--hint=string:desktop-entry:{pwhost.APP_ID}']
        if clickable:
            # without --wait libnotify 0.8 refuses the action ("not supported by this server") and exits at once, so a click is lost
            command.append('--wait')
            command.append('--print-id')  # first line of the output: the id, to close this notification later
            command.append('--action=default=Ouvrir dans PR Watch')
            if token_fd is not None:
                command.append(f'--activation-token-fd={token_fd}')
        # the body of a notification is Pango markup: what comes from a pull request must not be read as tags
        return [*command, title, body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')]
    return None


def _wait_for_click(command, on_click, token_pipe=None, group=None):
    """Waits for the notification to be clicked or closed, then runs `on_click` if it was clicked.

    `token_pipe` is the (read, write) ends of the pipe the activation token of the click is written to: the command run
    gets the token in XDG_ACTIVATION_TOKEN, which is what lets its window come to the front under Wayland.
    """
    token_read, token_write = token_pipe or (None, None)
    try:
        passed = tuple(int(word.split('=', 1)[1]) for word in command if word.startswith('--activation-token-fd='))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=pwhost.clean_env(), pass_fds=passed)
        if token_write is not None:
            os.close(token_write)  # only the child keeps it, so that reading the pipe ends when the child does
            token_write = None
        first = process.stdout.readline().strip()  # the id of the notification, printed as soon as it is shown
        if group and first.isdigit():
            _remember(group, int(first))
        try:
            out, _ = process.communicate(timeout=3600)
        except subprocess.TimeoutExpired:
            process.kill()
            return
        if group and first.isdigit():
            _forget(group, int(first))  # clicked or dismissed: the shell took it away
        env = pwhost.child_env()
        if token_read is not None:
            token = os.read(token_read, 4096).decode(errors='replace').strip()
            if token:
                env.update({'XDG_ACTIVATION_TOKEN': token, 'DESKTOP_STARTUP_ID': token})
        if out.strip() == 'default':
            subprocess.Popen(on_click, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        for descriptor in (token_read, token_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def notify(title, body, urgent=False, on_click=None, group=None):
    """Shows a notification. `on_click` is a command run when the user clicks it, where the system lets us know (Linux)."""
    clickable = bool(on_click) and pwhost.SYSTEM == 'linux' and notify_send_has_actions()
    pipe = os.pipe() if clickable and _token_supported[0] else None
    command = notification_command(title, body, urgent, clickable, pipe[1] if pipe else None)
    if not command:
        return
    try:
        if clickable:
            threading.Thread(target=_wait_for_click, args=(command, on_click, pipe, group), daemon=True).start()
        elif pwhost.SYSTEM == 'windows':
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # the balloon stays 11 s
        else:
            subprocess.run(command, check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def open_url(url):
    """Hands a link (https://, claude://, ...) to the application registered for it."""
    if pwhost.SYSTEM == 'windows':
        os.startfile(url)  # noqa: S606 - the link comes from this program
    elif pwhost.SYSTEM == 'macos':
        subprocess.Popen(['open', url])
    else:
        subprocess.Popen(['xdg-open', url])
