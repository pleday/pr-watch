"""Reading the journal of the pr-watch daemon: entries, their kind, repeated lines folded, simple Markdown in the reports.

Pure Python (no GTK): the window that shows the journal only draws what this module computes.
"""
import datetime
import re

ENTRY = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.*)$')
PR_PREFIX = re.compile(r'^([\w.-]+#\d+): (.*)$')
INLINE = re.compile(r'(\*\*[^*]+\*\*|`[^`]+`)')
VERDICT = re.compile(r'^\s*PR-WATCH:\s*(OK|ATTENTION)\b\s*(.*)$')

# kind -> (label shown in the filter, importance)
IMPORTANT = {'attention', 'notify', 'error', 'warn', 'ok-merge'}


def classify(message):
    """The kind of an entry, from its text: it decides the colour and the filters."""
    low = message.lower()
    if message.startswith('NOTIFY '):
        return 'attention' if 'attention' in low else 'notify'
    if re.search(r'claude exited [1-9]|timed out|refused|traceback|keyerror|typeerror|valueerror|^api [45]\d\d', low):
        return 'error'
    if 'infrastructure failure' in low:
        return 'warn'
    if re.search(r'\bclaude committed\b', low):
        return 'commit'
    if re.search(r'\bclaude (replied|resolved)\b', low):
        return 'reply'
    if 'resuming session' in low:
        return 'claude'
    if re.search(r'claude exited 0', low):
        return 'ok'
    if re.search(r': merged|\bmerged \(', low):
        return 'ok-merge'
    if re.search(r'daemon (started|stopped)|application registered', low):
        return 'system'
    if 'session in use' in low:
        return 'muted'
    return 'info'


def is_report(entry):
    """Whether the entry is the report of a woken Claude session (the daemon logs it right after the run)."""
    return entry['message'].startswith('claude exited')


def parse(text):
    """Entries of the journal, oldest first: {date, time, pr, message, kind, details, count, last_time}."""
    entries, current = [], None
    for line in text.splitlines():
        match = ENTRY.match(line)
        if match:
            message = match.group(3)
            pr = None
            pr_match = PR_PREFIX.match(message)
            if pr_match:
                pr, message_wo_pr = pr_match.groups()
            else:
                message_wo_pr = message
            current = {
                'date': match.group(1),
                'time': match.group(2),
                'last_time': match.group(2),
                'pr': pr,
                'message': message_wo_pr,
                'kind': classify(message_wo_pr),
                'details': [],
                'count': 1,
            }
            entries.append(current)
        elif current is not None:
            current['details'].append(line)
    for entry in entries:
        # the lines after the first line of a message belong to it; blank lines at its end are only padding
        while entry['details'] and not entry['details'][-1].strip():
            entry['details'].pop()
    return entries


def _shape(entry):
    """What makes two entries 'the same line': commit hashes, uuids and numbers do not count."""
    message = re.sub(r'%7B[^/\s]*|\b[0-9a-f]{6,}\b', 'H', entry['message'])
    return (entry['kind'], entry['pr'], re.sub(r'\d+', 'N', message))


# Lines the daemon repeats while it waits or while something keeps failing: folded even when other such lines sit between them.
NOISE = {'muted', 'error', 'warn'}


def collapse(entries):
    """Folds repeated entries into one with a count and a time range.

    The daemon says the same thing every minute while a session is busy or an API call keeps failing, often alternating
    two lines: repeats are folded until something significant (a run of Claude, a notification, a restart) happens,
    after which the same line starts a new group.
    """
    folded, open_groups = [], {}
    for entry in entries:
        shape = _shape(entry)
        group = open_groups.get(shape)
        if entry['kind'] in NOISE and not entry['details'] and group and group['date'] == entry['date']:
            group['count'] += 1
            group['last_time'] = entry['time']
            group['message'] = entry['message']
            continue
        copy = dict(entry)
        folded.append(copy)
        if entry['kind'] in NOISE and not entry['details']:
            open_groups[shape] = copy
        else:
            open_groups.clear()
    return folded


# What the user can show or hide: id -> (label, the kinds it stands for). An entry is shown when its kind belongs to a ticked group.
KIND_GROUPS = (
    ('claude', 'Réveils de Claude', ('claude', 'ok')),
    ('commit', 'Commits de Claude', ('commit',)),
    ('reply', 'Réponses et fils résolus', ('reply',)),
    ('notify', 'Notifications', ('notify', 'attention')),
    ('error', 'Erreurs et alertes', ('error', 'warn')),
    ('merge', 'Merges', ('ok-merge',)),
    ('system', 'Démon et système', ('system',)),
    ('info', 'Autres lignes', ('info', 'muted')),
)

PERIODS = (('', 'Toute la période'), ('1', 'Dernière heure'), ('today', 'Aujourd’hui'), ('24', '24 dernières heures'), ('168', '7 derniers jours'))


def cutoff(period, now=None):
    """The oldest moment a period shows, as the journal writes it ('YYYY-MM-DD HH:MM:SS'); '' when there is no limit."""
    now = now or datetime.datetime.now()
    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif isinstance(period, str) and period.isdigit() and int(period) > 0:
        start = now - datetime.timedelta(hours=int(period))
    else:
        return ''
    return start.strftime('%Y-%m-%d %H:%M:%S')


def links(entry, workspace):
    """Where Bitbucket shows what an entry talks about: [{label, url}] (the pull request, the commit, the comment)."""
    if not workspace or not entry['pr']:
        return []
    repo, number = entry['pr'].split('#')
    base = f"https://bitbucket.org/{workspace}/{repo}"
    found = [{'label': entry['pr'], 'url': f"{base}/pull-requests/{number}", 'target': 'pr'}]
    for sha in re.findall(r'\bcommitted ([0-9a-f]{7,40})\b', entry['message']):
        found.append({'label': f"commit {sha[:7]}", 'url': f"{base}/commits/{sha}"})
    for comment in re.findall(r'\bcomment (\d+)\b', entry['message']) if entry['kind'] == 'reply' else []:
        found.append({'label': f"commentaire {comment}", 'url': f"{base}/pull-requests/{number}#comment-{comment}"})
    return found


MODES = (('all', 'Tout'), ('important', 'Notifications et erreurs'), ('claude', 'Claude'))


PAGE = 200  # entries shown at first; "older entries" adds as many again


def latest(entries, limit):
    """The last `limit` entries and how many older ones are left out (limit 0 = all of them)."""
    if not limit or len(entries) <= limit:
        return entries, 0
    return entries[-limit:], len(entries) - limit


def visible(entry, mode='all', search='', pr='', kinds=None, since=''):
    """Whether the filters let an entry through: `kinds` is the list of ticked group ids (None = all), `since` a `cutoff()`."""
    if pr and entry['pr'] != pr:
        return False
    if mode == 'important' and entry['kind'] not in IMPORTANT:
        return False
    if mode == 'claude' and entry['kind'] not in ('claude', 'ok', 'attention', 'notify', 'commit', 'reply'):
        return False
    if kinds is not None and entry['kind'] not in {k for group, _, members in KIND_GROUPS if group in kinds for k in members}:
        return False
    if since and f"{entry['date']} {entry['last_time']}" < since:
        return False
    if search:
        haystack = ' '.join([entry['message'], entry['pr'] or '', *entry['details']]).lower()
        return search.lower() in haystack
    return True


def runs(text):
    """Splits `**bold**` and `code` spans: [(text, style)] with style None, 'bold' or 'code'."""
    pieces = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            pieces.append((part[2:-2], 'bold'))
        elif part.startswith('`') and part.endswith('`') and len(part) > 2:
            pieces.append((part[1:-1], 'code'))
        else:
            pieces.append((part, None))
    return pieces


def detail_line(line):
    """How a line of a report is drawn: {indent, bullet, runs, verdict} (verdict is 'OK' or 'ATTENTION' for the last line)."""
    verdict = VERDICT.match(line)
    if verdict:
        label = 'PR-WATCH : ' + verdict.group(1) + (' — ' + verdict.group(2) if verdict.group(2) else '')
        return {'indent': 0, 'bullet': None, 'runs': [(label, None)], 'verdict': verdict.group(1)}
    stripped = line.lstrip()
    indent = (len(line) - len(stripped)) // 2
    bullet = None
    for marker in ('- ', '* ', '• '):
        if stripped.startswith(marker):
            bullet, stripped = ('•' if indent == 0 else '◦'), stripped[len(marker):]
            break
    numbered = re.match(r'^(\d+)[.)] (.*)$', stripped)
    if numbered:
        bullet, stripped = numbered.group(1) + '.', numbered.group(2)
    return {'indent': indent, 'bullet': bullet, 'runs': runs(stripped), 'verdict': None}
