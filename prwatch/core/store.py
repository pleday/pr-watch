"""Where PR Watch keeps its files (the data directory), the journal and the registry of watched pull requests."""

import json
import os
import time
from pathlib import Path

from prwatch.system import host as pwhost

# PR_WATCH_HOME lets tests (and anyone who wants a second instance) use another data directory.
DIR = Path(os.environ.get('PR_WATCH_HOME') or Path.home() / '.claude' / 'pr-watch')


REGISTRY = DIR / 'registry.json'


REGISTRY_LOCK = DIR / 'registry.lock'
GROUPS = DIR / 'groups.json'  # the names of the groups the user made, in their order (a group may be empty)


DAEMON_LOCK = DIR / 'daemon.lock'


PID_FILE = DIR / 'daemon.pid'


LOG = DIR / 'daemon.log'


CONFIG = DIR / 'config.json'


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


LOG_MAX_BYTES = 2 * 1024 * 1024


LOG_WARN_BYTES = LOG_MAX_BYTES * 3 // 4


def log_health():
    """{'bytes': size of the journal, 'large': whether it is close to the size at which the oldest entries are set aside}."""
    try:
        size = LOG.stat().st_size
    except OSError:
        size = 0
    return {'bytes': size, 'large': size >= LOG_WARN_BYTES}


def log(message):
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.stat().st_size > LOG_MAX_BYTES:  # the previous journal is kept once: it never grows without bound
            os.replace(LOG, LOG.with_name(LOG.name + '.1'))
    except OSError:
        pass
    with open(LOG, 'a', encoding='utf-8') as handle:
        handle.write(f'{now()} {message}\n')


ARCHIVES = DIR / 'journal-archives'
ARCHIVES_KEPT = 10


def archive_log():
    """Moves the journal to `journal-archives/daemon-<date>.log` and starts an empty one; (archive, lines), None if empty.

    The file is emptied in place, not renamed: the daemon's own output is attached to it and goes on writing in it. Only the
    latest `ARCHIVES_KEPT` archives are kept, so the folder does not grow without bound either.
    """
    try:
        data = LOG.read_bytes()
    except OSError:
        return None
    if not data.strip():
        return None
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    target = ARCHIVES / f"daemon-{time.strftime('%Y%m%d-%H%M%S')}.log"
    target.write_bytes(data)
    with open(LOG, 'r+b') as handle:
        handle.seek(len(data))
        rest = handle.read()  # written since the read: it stays in the new journal
        handle.seek(0)
        handle.truncate()
        handle.write(rest)
    for old in sorted(ARCHIVES.glob('daemon-*.log'))[:-ARCHIVES_KEPT]:
        old.unlink(missing_ok=True)
    lines = data.count(b'\n')
    log(f'journal archived: {target.name} ({lines} lines)')
    return target, lines


class registry_lock:
    def __enter__(self):
        DIR.mkdir(parents=True, exist_ok=True)
        self.handle = open(REGISTRY_LOCK, 'w')
        pwhost.lock(self.handle)
        return self

    def __exit__(self, *exc):
        pwhost.unlock(self.handle)
        self.handle.close()


SUSPENDED = DIR / 'suspended.json'
USAGE_OVERRIDE = DIR / 'usage-override.json'
SCHEDULE_OVERRIDE = DIR / 'schedule-override.json'
BITBUCKET_HEALTH = DIR / 'bitbucket-health.json'
BITBUCKET_DOWN_WINDOW = 600  # a failure older than this (ten minutes) says nothing about now: the daemon may simply have slept
_health = {'lastOk': 0.0, 'lastFail': 0.0, 'written': 0.0}


def note_bitbucket(ok):
    """Records that a call to Bitbucket worked or gave up (timeout, 5xx), for the banner of the dashboard.

    The daemon and the dashboard are two processes: the state goes through a file. Successes are written at most every
    30 seconds, unless they end a failing streak, so that a busy cycle does not rewrite it for every call.
    """
    now = time.time()
    if ok:
        if _health['lastFail'] <= _health['lastOk'] and now - _health['written'] < 30:
            _health['lastOk'] = now
            return
        _health['lastOk'] = now
    else:
        _health['lastFail'] = now
    _health['written'] = now
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        BITBUCKET_HEALTH.write_text(json.dumps({'lastOk': _health['lastOk'], 'lastFail': _health['lastFail']}), encoding='utf-8')
    except OSError:
        pass


def bitbucket_health():
    """{'down': whether the latest calls failed, 'lastOk': when Bitbucket last answered, as the journal writes dates (None if never seen)}."""
    try:
        data = json.loads(BITBUCKET_HEALTH.read_text(encoding='utf-8'))
        last_ok, last_fail = float(data.get('lastOk') or 0), float(data.get('lastFail') or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return {'down': False, 'lastOk': None}
    down = last_fail > last_ok and time.time() - last_fail < BITBUCKET_DOWN_WINDOW
    return {'down': down, 'lastOk': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_ok)) if last_ok else None}


def schedule_override():
    """The temporary schedule override, or None when it has expired or is invalid."""
    try:
        data = json.loads(SCHEDULE_OVERRIDE.read_text(encoding='utf-8'))
        if data['mode'] not in ('active', 'paused') or float(data['until']) <= time.time():
            SCHEDULE_OVERRIDE.unlink(missing_ok=True)
            return None
        return {'mode': data['mode'], 'until': float(data['until'])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def set_schedule_override(mode, until):
    if mode not in ('active', 'paused') or until <= time.time():
        raise ValueError('invalid schedule override')
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_OVERRIDE.with_suffix('.tmp')
    tmp.write_text(json.dumps({'mode': mode, 'until': until}), encoding='utf-8')
    tmp.replace(SCHEDULE_OVERRIDE)


def clear_schedule_override():
    SCHEDULE_OVERRIDE.unlink(missing_ok=True)


def suspension():
    """{'reason', 'at', 'kind', 'until'} while the daemon no longer wakes Claude, None when it works.

    `kind` is 'usage' (the plan is almost used up: it lifts by itself when the window starts over, `until`) or 'tokens'
    (the session failed for lack of tokens: only the user lifts it).
    """
    try:
        data = json.loads(SUSPENDED.read_text(encoding='utf-8'))
        return {'reason': str(data['reason']), 'at': str(data.get('at', '')), 'kind': data.get('kind', 'tokens'), 'until': float(data.get('until') or 0)}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def suspend(reason, kind='tokens', until=0):
    DIR.mkdir(parents=True, exist_ok=True)
    SUSPENDED.write_text(json.dumps({'reason': reason, 'at': now(), 'kind': kind, 'until': until}), encoding='utf-8')


def resume(override=True):
    """Lifts the suspension; True when there was one.

    By the user (`override`) it also silences the usage check until the exhausted window starts over: they accept to go on.
    """
    current = suspension()
    try:
        SUSPENDED.unlink()
    except OSError:
        return False
    if override and current and current['kind'] == 'usage':
        USAGE_OVERRIDE.write_text(json.dumps({'until': current['until']}), encoding='utf-8')
    recheck_missed()
    return True


def recheck_missed():
    """Asks for a check of the PRs whose events were only notified during the suspension: their cursors moved on, so nothing else would wake Claude."""
    with registry_lock():
        entries = load_registry()
        for entry in entries:
            if entry.pop('missedWhileSuspended', False) and entry.get('state', 'watching') == 'watching':
                entry['forceWake'] = True
        save_registry(entries)


def usage_override_active():
    try:
        return time.time() < float(json.loads(USAGE_OVERRIDE.read_text(encoding='utf-8'))['until'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def load_registry():
    if not REGISTRY.exists():
        return []
    return json.loads(REGISTRY.read_text(encoding='utf-8'))


def save_registry(entries):
    tmp = REGISTRY.with_suffix('.tmp')
    tmp.write_text(json.dumps(entries, indent=2), encoding='utf-8')
    tmp.replace(REGISTRY)


def load_groups():
    try:
        names = json.loads(GROUPS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def save_groups(names):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = GROUPS.with_suffix('.tmp')
    tmp.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(GROUPS)


def key_of(entry):
    return f"{entry['repo']}#{entry['pr']}"


def update_entry(entry, **fields):
    """Writes the given fields of one entry back, without touching what the CLI may have changed meanwhile."""
    with registry_lock():
        entries = load_registry()
        for current in entries:
            if key_of(current) == key_of(entry):
                current.update(fields)
        save_registry(entries)
    entry.update(fields)
