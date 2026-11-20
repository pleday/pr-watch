"""How much of the Claude plan is used: the 5-hour session and the week, as the Claude CLI reports them.

The CLI tells the quotas after every answer (a `rate_limit_event` of its stream-json output): PR Watch keeps the last one it
saw, from the sessions it wakes, and asks for a new one with a tiny request when it is old. No account call, no token read.
"""

import json
import subprocess
import tempfile
import time

from prwatch.core import store as pwstore
from prwatch.system import host as pwhost

WINDOWS = (('session', 'five_hour', 'la session de 5 h'), ('week', 'seven_day', 'la semaine'))

CACHE_FILE = 'usage.json'
FRESH = 600  # seconds a reading is trusted before a wake-up asks for a new one
STALE = 6 * 3600  # beyond this a reading is not shown any more


def parse_event(event):
    """{'session': {'used': 11.0, 'resetsAt': epoch}, 'week': {...}} from a `rate_limit_event`; None when it carries no windows."""
    try:
        windows = event['rate_limit_info']['unifiedWindows']
        return {name: {'used': round(float(windows[key]['utilization']) * 100, 1), 'resetsAt': float(windows[key]['resetsAt'])} for name, key, _ in WINDOWS}
    except (KeyError, TypeError, ValueError):
        return None


def parse_stream(text):
    """(last quotas seen, text of the result) in what `claude --output-format stream-json` printed; (None, None) for neither."""
    usage, result = None, None
    for line in (text or '').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'rate_limit_event':
            usage = parse_event(event) or usage
        elif event.get('type') == 'result':
            result = event
    return usage, result


def record(usage):
    if usage is None:
        return
    try:
        pwstore.DIR.mkdir(parents=True, exist_ok=True)
        (pwstore.DIR / CACHE_FILE).write_text(json.dumps({'at': time.time(), 'value': usage}), encoding='utf-8')
    except OSError:
        pass


def read():
    """(quotas, epoch of the measure) as last recorded; (None, 0) when nothing is known or the reading is too old."""
    try:
        data = json.loads((pwstore.DIR / CACHE_FILE).read_text(encoding='utf-8'))
        value, at = data['value'], float(data['at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None, 0.0
    return (value, at) if value and time.time() - at < STALE else (None, 0.0)


def probe(binary, timeout=90):
    """Asks the CLI for the quotas with the smallest request there is (one word, the cheapest model); None when it fails."""
    try:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([binary, '-p', 'ok', '--model', 'haiku', '--output-format', 'stream-json', '--verbose'], capture_output=True, text=True,
                                  timeout=timeout, cwd=folder, env=pwhost.child_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    usage, _ = parse_stream(done.stdout)
    record(usage)
    return usage


def current(binary, max_age=FRESH):
    """The quotas, measured again through the CLI when the last measure is older than `max_age` seconds."""
    value, at = read()
    if value is None or time.time() - at >= max_age:
        value = probe(binary) or value
    return value


def exhausted(usage, margins):
    """(reason, epoch when the last exhausted window starts over) when a window has its margin (%, `margins` by window name) or less left."""
    reasons, until = [], 0.0
    for name, _, label in WINDOWS:
        window = (usage or {}).get(name)
        if window and 100 - window['used'] <= margins[name]:
            from datetime import datetime
            back = datetime.fromtimestamp(window['resetsAt']).strftime('%d/%m %H:%M')
            reasons.append(f"{label} est utilisée à {window['used']:.0f} % (remise à zéro le {back})")
            until = max(until, window['resetsAt'])
    return ('; '.join(reasons), until)
