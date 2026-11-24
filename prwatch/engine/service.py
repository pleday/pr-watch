"""The processes: the daemon loop, the web dashboard server and the status icon, how they are started, found and stopped."""

import importlib.util
import json
import os
import subprocess
import sys
import time

from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig

from prwatch.system import desktop as pwdesktop
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications
from prwatch.system import window as pwwindow
from prwatch.engine import cycle as pwcycle

def request_cycle():
    """Asks the running daemon to poll right now instead of at the end of its sleep (what a screen does after an action)."""
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    (pwstore.DIR / 'poke').touch()


def sleep_until_next_cycle(seconds):
    """Sleeps `seconds`, cut short when a screen asked for a cycle (`request_cycle`)."""
    poke = pwstore.DIR / 'poke'
    for _ in range(max(1, int(seconds))):
        if poke.exists():
            poke.unlink(missing_ok=True)
            return
        time.sleep(1)


def check_permissions():
    """Checks the tokens and rights PR Watch needs (see permissions.py), journals what is missing and tells the user once."""
    from prwatch.core import permissions

    cfg = pwconfig.config()
    data = permissions.check_and_save(cfg['jiraSite'], cfg['claudeBinary'])
    missing = [c for c in data['checks'] if not c['ok']]
    for check in missing:
        pwstore.log(f"permission missing ({'required' if check['required'] else 'optional'}) {check['id']}: {check['detail']}")
    blocking = [c for c in missing if c['required']]
    if blocking:
        pwwake.notify('⚠ Autorisations manquantes', 'PR Watch ne peut pas surveiller : ' + ', '.join(c['label'] for c in blocking) + '.\nOuvrez la fenêtre de PR Watch pour le guide.', cfg, urgent=True)
    return data


def run_daemon():
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    handle = open(pwstore.DAEMON_LOCK, 'a+')
    if not pwhost.lock(handle, blocking=False):
        print('already running')
        return
    pwstore.PID_FILE.write_text(str(os.getpid()), encoding='utf-8')
    pwstore.log(f'daemon started (pid {os.getpid()})')
    check_permissions()  # the first thing: what is missing is saved for the dashboard, which explains how to add it
    pwbitbucket.auth_header()
    started = pwhost.code_stamp()
    while True:
        cfg = pwcycle.cycle()
        until_schedule_change = pwconfig.seconds_until_schedule_change(cfg)
        if until_schedule_change is None:
            sleep_seconds = cfg['pollSeconds']
        elif pwconfig.schedule_effective_state(cfg)[0]:
            sleep_seconds = min(cfg['pollSeconds'], until_schedule_change)
        else:
            sleep_seconds = min(900, until_schedule_change)
        sleep_until_next_cycle(sleep_seconds)
        # Between two cycles only, so a session being woken is never cut short by the restart.
        if pwhost.SYSTEM != 'windows' and pwhost.code_stamp() != started:
            pwstore.log('daemon restarting: its code changed')
            os.execv(sys.executable, sys.orig_argv)


def is_running():
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    handle = open(pwstore.DAEMON_LOCK, 'a+')
    if not pwhost.lock(handle, blocking=False):
        handle.close()
        return True
    pwhost.unlock(handle)
    handle.close()
    return False


def daemon_pid():
    try:
        return int(pwstore.PID_FILE.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None


def stop_daemon():
    pwstore.clear_schedule_override()
    pid = daemon_pid()
    if not is_running() or not pid:
        print('daemon not running')
        return
    pwhost.terminate(pid)
    pwstore.log(f'daemon stopped (pid {pid})')
    print('daemon stopped')


def tray_running():
    """Whether the status icon is already there (it owns a name on the session bus)."""
    owner = subprocess.run(
        ['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus',
         '--method', 'org.freedesktop.DBus.NameHasOwner', pwhost.APP_ID],
        capture_output=True,
        text=True,
    )
    return 'true' in owner.stdout


def gtk_desktop():
    """Whether this machine can show the GTK window: Linux, a screen, and PyGObject."""
    has_screen = os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')
    return pwhost.SYSTEM == 'linux' and bool(has_screen) and importlib.util.find_spec('gi') is not None


def ensure_tray():
    """Starts the status icon and registers the application, when there is a Linux desktop to show them on."""
    if not gtk_desktop():
        return
    pwdesktop.install_desktop(announce=False)
    if tray_running():
        return
    pwhost.spawn_detached(pwhost.in_app_scope(pwhost.module_command('tray', '--background'), pwdesktop.APP_ID), open(pwstore.DIR / 'tray.log', 'a', encoding='utf-8'), env=pwhost.child_env())


def web_info():
    """The running dashboard's {port, token, pid} (web.json), or None."""
    try:
        info = json.loads((pwstore.DIR / 'web.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    lock = open(pwstore.DIR / 'web.lock', 'a+')
    free = pwhost.lock(lock, blocking=False)
    if free:
        pwhost.unlock(lock)
    lock.close()
    return None if free else info


def ensure_web():
    """Starts the web dashboard when it is not running, and returns its address with the secret token."""
    info = web_info()
    if not info:
        pwhost.spawn_detached(pwhost.module_command('web', 'serve'), open(pwstore.DIR / 'web.log', 'a', encoding='utf-8'), env=pwhost.child_env())
        for _ in range(50):
            time.sleep(0.1)
            info = web_info()
            if info:
                break
    if not info:
        raise SystemExit('the web dashboard did not start, see ' + str(pwstore.DIR / 'web.log'))
    return f"http://127.0.0.1:{info['port']}/?t={info['token']}"


def open_dashboard(open_window=True, select=None, pulse=False):
    """`pr-watch ui`: the one window of PR Watch, the web dashboard, as an application window (or in the default browser).

    `select` ('repo#12') makes the dashboard show that pull request (a click on its notification): it is written down
    for the page, which looks at it with every refresh. `pulse` (the status icon) only makes the page pulse it for a moment.
    """
    url = ensure_web()
    ensure(announce=False)  # the daemon the dashboard talks about
    # Always written, even without a pull request to select: a window that is already open reloads itself on seeing it.
    from prwatch import actions
    recent = actions.focus()
    # GNOME also launches the desktop entry (no pull request to select) when a notification is clicked: it must not wipe the
    # request of the click's own action, which arrives within the same second.
    if select or not (recent and recent['key'] and time.time() * 1000 - recent['at'] < 15000):
        actions.set_focus(select or '', 'tray' if pulse else 'notification')
    if open_window and pwconfig.config()['dashboardInBrowser']:
        # a tab that is already showing the dashboard picks up the request above by itself: no second tab
        if not actions.tab_is_open():
            pwnotifications.open_url(url)
    elif open_window:
        pwwindow.open_app_window(url, pwstore.DIR / 'chrome')
    if not open_window:
        print(url)

def ensure(announce=True):
    ensure_tray()
    if is_running():
        if announce:
            print('daemon already running')
        return
    pwbitbucket.auth_header()
    pwhost.spawn_detached(pwhost.module_command('daemon'), open(pwstore.LOG, 'a', encoding='utf-8'), env=pwhost.child_env())
    time.sleep(1.5)
    if announce:
        print('daemon started' if is_running() else 'daemon failed to start, see ' + str(pwstore.LOG))


def tray_main(argv=None):
    """Open the GTK status icon; GNOME shows its summary on hover in an anchored panel when the companion extension is active."""
    if pwhost.SYSTEM != 'linux':
        raise SystemExit('The window of the desktop needs Linux (GTK 3). Use `prwatch ui` for the web dashboard.')
    try:
        from prwatch.tray import app as gtkapp
    except (ImportError, ValueError) as error:  # ValueError: gi.require_version when the typelib is absent
        raise SystemExit(
            f'GTK 3 est introuvable ({error}). Sur Ubuntu : sudo apt install python3-gi gir1.2-gtk-3.0 ; '
            'avec pipx : pipx install --system-site-packages . Le tableau de bord web (`pr-watch ui`) fonctionne sans GTK.')
    return gtkapp.main(['prwatch', *(argv or [])])
