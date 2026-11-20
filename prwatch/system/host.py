"""The machine PR Watch runs on: which system it is, files locks, detached processes, the environment of a child, and the watch on the program's own files.

Everything that differs between Linux, macOS and Windows lives in this layer (modules `host`, `notifications`, `terminal`,
`processes`, `claude`, `editors`, `opening`, `window`, `desktop`): the daemon and the status icon call these functions instead
of reaching for /proc, fcntl, notify-send or xdg-open themselves. Linux is the only system this was run on; the macOS and
Windows branches follow the documented behaviour of `ps`, `lsof`, `osascript`, `open`, PowerShell and `msvcrt`, and are
covered by tests that feed them recorded output."""

import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

SYSTEM = {'Linux': 'linux', 'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system(), 'linux')


if SYSTEM == 'windows':
    import msvcrt

    def lock(handle, blocking=True):
        """Takes an exclusive lock on the file; False when `blocking` is off and someone else holds it."""
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(0.05)

    def unlock(handle):
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def lock(handle, blocking=True):
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except OSError:
            return False

    def unlock(handle):
        fcntl.flock(handle, fcntl.LOCK_UN)


# Set by the desktop for the application it just launched. A process that inherits them (the daemon started by a Claude
# session that IntelliJ launched, say) gets its windows grouped under that other application in the dock.
LAUNCH_VARS = ('DESKTOP_STARTUP_ID', 'XDG_ACTIVATION_TOKEN', 'GIO_LAUNCHED_DESKTOP_FILE', 'GIO_LAUNCHED_DESKTOP_FILE_PID')


def clean_env(env=None):
    """`env` (default: the environment of this process) without the variables that tie a window to the launching application."""
    # SNAP_* too: libnotify, seeing them, takes the program for a confined snap and drops the actions of a notification,
    # so a click on it does nothing (the daemon started from the terminal of an IDE installed as a snap inherits them).
    return {k: v for k, v in (os.environ if env is None else env).items() if k not in LAUNCH_VARS and not k.startswith('SNAP')}


def in_app_scope(argv, app_id):
    """`argv` wrapped to run in its own systemd scope named after the application `app_id` (Linux with a user systemd).

    GNOME Shell finds the application of a window from the cgroup of its process when the window itself is not
    enough: a program started from IntelliJ's terminal or a session it hosts sits in IntelliJ's scope, and its window
    shows up under IntelliJ in the dock. A scope `app-gnome-<id>-<n>.scope` is how the shell names its own launches.
    """
    if SYSTEM != 'linux' or not os.environ.get('XDG_RUNTIME_DIR') or not shutil.which('systemd-run'):
        return argv
    unit = f'app-gnome-{app_id}-{os.getpid()}{int(time.time())}.scope'
    return ['systemd-run', '--user', '--scope', '--quiet', '--collect', f'--unit={unit}', '--', *argv]


def spawn_detached(argv, log_file, env=None, keep_launch_vars=False):
    """Starts a process that outlives its parent and the terminal it was started from.

    The launch variables of the environment are dropped (see `LAUNCH_VARS`) unless `keep_launch_vars`: an activation token
    that was given for exactly this launch is the one case where they are wanted.
    """
    kwargs = {'env': (env if keep_launch_vars and env else clean_env(env))}
    if SYSTEM == 'windows':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, **kwargs)


def terminate(pid):
    os.kill(pid, signal.SIGTERM)


def claude_binary():
    """The `claude` command: the one on the PATH, else where the installer puts it."""
    found = shutil.which('claude')
    if found:
        return found
    return str(Path.home() / '.local' / 'bin' / ('claude.exe' if SYSTEM == 'windows' else 'claude'))


APP_ID = 'org.prwatch.PrWatchTray'  # the desktop entry, its icon, the D-Bus name of the status icon


def _run(argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def _name(exe):
    return os.path.basename((exe or '').replace('\\', '/')).lower().removesuffix('.exe')


def package_root():
    """The directory that holds the `prwatch` package (the repository when running from a checkout)."""
    return str(Path(__file__).resolve().parents[2])


def from_checkout():
    """Whether the program runs from a source checkout rather than from an installed package."""
    return (Path(package_root()) / 'pyproject.toml').exists()


def module_command(*args):
    """The command line that runs this program as a module (`python -m prwatch <args>`)."""
    return [sys.executable, '-m', 'prwatch', *args]


def child_env():
    """The environment for a program started from here: a checkout is put on its PYTHONPATH so `-m prwatch` is found."""
    env = clean_env()
    if from_checkout():
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [package_root(), env.get('PYTHONPATH')]))
    return env


def code_stamp():
    """The latest modification time of the program's own files: it changes when the code is edited or upgraded."""
    root = Path(__file__).resolve().parents[1]
    latest = 0.0
    for path in root.rglob('*'):
        if path.suffix in ('.py', '.js', '.css', '.html'):
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                pass
    return latest


def restart_when_code_changes(every=3):
    """Starts a thread that runs this same process again (same command line and id) once its code changed on disk.

    A long-running process (the dashboard server) would otherwise keep serving the version it started with. Not on
    Windows, where running a program again means a new process id.
    """
    if SYSTEM == 'windows':
        return
    import threading

    start = code_stamp()

    def watch():
        while True:
            time.sleep(every)
            if code_stamp() != start:
                time.sleep(1)  # an edit is often several files: let it finish
                try:
                    os.execv(sys.executable, sys.orig_argv)
                except OSError:
                    return

    threading.Thread(target=watch, daemon=True).start()
