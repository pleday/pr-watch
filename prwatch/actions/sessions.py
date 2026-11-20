"""Bringing the Claude conversation of a pull request on screen, in the application that suits it."""

import subprocess

from prwatch.actions import common as pwcommon
from prwatch.core import config as pwconfig
from prwatch.engine import wake as pwwake
from prwatch.system import claude as pwclaude
from prwatch.system import opening as pwopening
from prwatch.system import processes as pwprocesses
from prwatch.system import terminal as pwterminal

def open_session(repo, pr):
    """Brings the Claude conversation of the PR on screen, in whatever application hosts it."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    title = pwclaude.session_title(pwwake.session_file(entry))
    found = pwprocesses.find_session_process(entry['sessionId'])
    if not found:
        label = f"« {title} »" if title else entry['sessionId'][:8] + '…'
        apps = pwopening.available_apps(entry)
        origin = entry.get('origin') or {}
        recommended = pwprocesses.recommended_app(origin, [a['id'] for a in apps])
        hints = pwopening.apps_hints(entry)
        if origin.get('host') and not recommended:
            hints.insert(0, f"Cette session a été ouverte depuis {pwprocesses.ORIGIN_LABELS.get(origin['host'], origin['host'])}, qui ne peut pas la rouvrir d'ici.")
        apps.sort(key=lambda a: a['id'] != recommended)  # stable: the others keep their order
        return pwcommon._result(False, f"Aucune instance active de la session {label}.", needsTerminal=True, apps=apps, appHints=hints,
                       recommended=recommended, originLabel=pwprocesses.ORIGIN_LABELS.get(origin.get('host')) if origin.get('host') else None)
    cwd = found['cwd'] or entry['sessionCwd']
    commands, message = pwopening.open_plan(entry, found, title)
    link = pwopening.link_for(found['host'], entry, cwd)
    if link:
        pwopening.run_commands(commands[:-1], cwd=cwd)  # the window of the project first, the link (last command) is the page's
        return pwcommon._result(True, message, openUrl=link)  # see `link_for`
    pwopening.run_commands(commands, cwd=cwd)
    pwopening.raise_app(found['host'])
    return pwcommon._result(True, message)


def open_in(repo, pr, app):
    """Opens the conversation in the application the user picked (a session no application hosts)."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if app == 'terminal':
        return open_terminal(repo, pr)
    if app not in [a['id'] for a in pwopening.available_apps(entry)]:
        return pwcommon._result(False, 'Application inconnue.')
    plan = pwopening.open_in_plan(app, entry, pwclaude.session_title(pwwake.session_file(entry)))
    if not plan:
        return pwcommon._result(False, 'Application introuvable.')
    commands, message = plan
    link = pwopening.link_for(app, entry, entry['sessionCwd'])
    if link:
        pwopening.run_commands(commands[:-1], cwd=entry['sessionCwd'])
        return pwcommon._result(True, message, openUrl=link)  # see `link_for`
    pwopening.run_commands(commands, cwd=entry['sessionCwd'])
    pwopening.raise_app(app)
    return pwcommon._result(True, message)


def open_terminal(repo, pr):
    """Resumes the session in a new terminal (for a session no application hosts)."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    command = pwterminal.terminal_command(entry['sessionCwd'], [pwconfig.config()['claudeBinary'], '--resume', entry['sessionId']])
    if not command:
        return pwcommon._result(False, 'Aucun terminal trouvé.')
    subprocess.Popen(command, cwd=entry['sessionCwd'])
    return pwcommon._result(True, 'Session reprise dans un nouveau terminal.')
