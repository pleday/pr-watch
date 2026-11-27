"""What the status icon tells, as the lines of its native menu: the state, the first pull requests (those that ask for something
come first, then the others in progress), a note on the rest.

The pull requests and their labels are those of the window (`actions.status`): one source of truth. A row with a `target` leads
to that pull request in the window; the others (state, headings, the note on the rest) are plain text, never clickable.
"""

from prwatch.actions import status as pwstatus

# What asks something of the user, most urgent first; the other pull requests are listed after these.
ACTIONS = ('attention', 'question', 'gave-up', 'failed', 'changes', 'ready', 'ticket-wait')
SHOWN = 5  # pull requests listed unless the option `trayMaxPrs` says otherwise; the menu would otherwise grow with the number of PRs
# A coloured dot in front of each label: the native menu of the shell writes a line in plain text and puts the icon of an item at its far
# right, so a coloured character is the only way to a dot beside its words.
DOTS = {'green': '🟢', 'orange': '🟠', 'red': '🔴'}
LINE = 84  # characters of a pull request's line: a native menu is as wide as its longest line


def toggle_of(running, scheduled_off):
    """(label, command) of the item that switches the watching on or off, depending on what it is now."""
    if running and not scheduled_off:
        return 'Désactiver la surveillance', 'stop'
    return 'Activer la surveillance', 'start'


def _line(entry, status):
    """`• repo #12 · title — state`, the title taking what is left of LINE."""
    label = status['label']
    if status['kind'] == 'question' and entry.get('pendingQuestions'):
        label += f" ({len(entry['pendingQuestions'])})"
    head = f"• {entry['repo']} #{entry['pr']}"
    tail = f' — {label}'
    room = LINE - len(head) - len(tail) - 3
    title = ' '.join(str(entry.get('title') or '').split())
    if room >= 12 and title:
        title = title if len(title) <= room else title[:room - 1] + '…'
        return f'{head} · {title}{tail}'
    return head + tail


def rows(running, scheduled_off, resumes, entries, active_sessions=None, suspension=None, max_attempts=None, cfg=None):
    """[{'kind', 'text', 'target'}]; `target` is the pull request ('repo#12') the window shows when the row is clicked, None for
    the rows that are only text. The state row also says `dots`: the colours of the daemon and of Claude."""
    # the same words as the two pills of the window, each with its colour (the dots of the line): green it works, orange it waits
    # and starts again by itself (the hours, the quota), red it needs the user (stopped, out of tokens)
    if not running:
        daemon, daemon_dot = 'Démon arrêté', 'red'
    elif scheduled_off:
        daemon, daemon_dot = 'Démon en pause (horaires)' + (f' jusqu’à {resumes}' if resumes else ''), 'orange'
    else:
        daemon, daemon_dot = 'Démon en route', 'green'
    if not suspension:
        claude, claude_dot = 'Claude activé', 'green'
    elif suspension.get('kind') == 'usage':
        claude, claude_dot = 'Claude désactivé (forfait presque épuisé)', 'orange'
    else:
        claude, claude_dot = 'Claude désactivé (plus de jetons)', 'red'
    out = [{'kind': 'status', 'text': f'{DOTS[daemon_dot]} {daemon} · {DOTS[claude_dot]} {claude}', 'target': None, 'dots': [daemon_dot, claude_dot]}]

    pairs = [(entry, pwstatus.status(entry, None, cfg)) for entry in entries]
    # what asks something of the user comes first (most urgent first), then the other pull requests, finished ones last; both stable
    order = lambda p: ACTIONS.index(p[1]['kind']) if p[1]['kind'] in ACTIONS else len(ACTIONS) + (p[1]['kind'] == 'done')  # noqa: E731
    pairs.sort(key=order)
    todo = [p for p in pairs if p[1]['kind'] in ACTIONS]
    shown = pairs[:(cfg or {}).get('trayMaxPrs', SHOWN)]
    hidden_todo = len(todo) - sum(1 for p in shown if p[1]['kind'] in ACTIONS)
    hidden_others = len(pairs) - len(shown) - hidden_todo
    if not pairs:
        out.append({'kind': 'heading', 'text': 'Aucune pull request.', 'target': None})
    elif todo:
        out.append({'kind': 'heading', 'text': f'Pull requests ({len(pairs)}) · {len(todo)} à traiter :', 'target': None})
    else:
        out.append({'kind': 'heading', 'text': f'Pull requests ({len(pairs)}) · rien à traiter :', 'target': None})
    for entry, status in shown:
        out.append({'kind': 'pr', 'text': _line(entry, status), 'target': f"{entry['repo']}#{entry['pr']}"})
    notes = []
    if hidden_todo:
        notes.append(f"{hidden_todo} autre{'s' if hidden_todo > 1 else ''} à traiter")
    if hidden_others:
        notes.append(f"{hidden_others} autre{'s' if hidden_others > 1 else ''} en cours")
    if notes:
        out.append({'kind': 'more', 'text': '+ ' + ' · '.join(notes), 'target': None})
    return out


def tooltip_text(running, scheduled_off, resumes, entries, active_sessions=None, suspension=None, max_attempts=None, cfg=None):
    return '\n'.join(row['text'] for row in rows(running, scheduled_off, resumes, entries, active_sessions, suspension, max_attempts, cfg))
