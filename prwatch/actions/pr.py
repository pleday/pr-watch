"""What can be done to a watched pull request: pause, reset, answer a question, wake, push, merge, settings."""

import json
import subprocess
import time

from prwatch.actions import common as pwcommon
from prwatch.core import config as pwconfig
from prwatch.actions import forms as pwforms
from prwatch.engine import merging as pwmerging
from prwatch.core import store as pwstore

FOCUS_FILE = 'focus.json'


def set_focus(key, source='notification'):
    """Asks the dashboard to show the pull request `key` ('repo#12').

    Written for the page, which polls for it. `source` 'tray' makes the page pulse the element for a moment; a notification
    marks the pull request until the user touches it.
    """
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    (pwstore.DIR / FOCUS_FILE).write_text(json.dumps({'key': key, 'at': int(time.time() * 1000), 'source': source}), encoding='utf-8')


def focus():
    """The last request of `set_focus`: {'key', 'at' (ms since the epoch)}, None when there is none."""
    try:
        data = json.loads((pwstore.DIR / FOCUS_FILE).read_text(encoding='utf-8'))
        return {'key': str(data['key']), 'at': int(data['at']), 'source': str(data.get('source') or 'notification')}
    except (OSError, ValueError, KeyError, TypeError):
        return None


QUIT_FILE = 'quit.json'


def request_quit():
    """Asks the open window to close (the status icon's "Quitter"): written for the page, which polls for it."""
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    (pwstore.DIR / QUIT_FILE).write_text(json.dumps({'at': int(time.time() * 1000)}), encoding='utf-8')


def quit_request():
    """{'at' (ms since the epoch)} of the last `request_quit`, None when there is none."""
    try:
        return {'at': int(json.loads((pwstore.DIR / QUIT_FILE).read_text(encoding='utf-8'))['at'])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def report(repo, pr):
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    return pwcommon._result(True, '', report=entry.get('lastReport') or '', at=entry.get('lastReportAt') or '')


def pause(repo, pr, paused=True):
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    pwstore.update_entry(entry, state='paused' if paused else 'watching')
    return pwcommon._result(True, 'Surveillance en pause.' if paused else 'Surveillance reprise.')


def reset(repo, pr):
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    pwstore.update_entry(entry, attempts=0, state='watching', needsAttention='')
    return pwcommon._result(True, 'Essais remis à zéro, surveillance reprise.')


def acknowledge(repo, pr):
    """Marks everything the card shows as seen: the attention and every "action done" of the pull request, in one go."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    pwstore.update_entry(entry, needsAttention='', activity=None)
    return pwcommon._result(True, 'Marquée comme vue.')


ANSWERS = {'yes': 'Oui', 'no': 'Non', 'other': 'Autre'}


def answer_question(repo, pr, id, choice, note=''):
    """The user's decision about a comment that asked something: handle it (yes), leave it (no) or do something else (other).

    `yes` and `other` become an event for the next cycle of the daemon, which wakes the Claude session with it.
    """
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if choice not in ANSWERS:
        return pwcommon._result(False, 'Réponse inconnue (oui, non ou autre attendu).')
    note = (note or '').strip() if isinstance(note, str) else ''
    if choice == 'other' and not note:
        return pwcommon._result(False, 'Dites ce qu’il faut faire à la place.')
    pending = entry.get('pendingQuestions') or []
    question = next((q for q in pending if q['id'] == id), None)
    if not question:
        return pwcommon._result(False, 'Cette question n’est plus en attente.')
    fields = {'pendingQuestions': [q for q in pending if q['id'] != id]}
    if choice != 'no':
        where = f" ({question['place']})" if question.get('place') else ''
        what = ('The user says: yes, handle this comment as you would any other.' if choice == 'yes'
                else f"The user says to do this instead of what the comment asks: {note}")
        fields['decisions'] = (entry.get('decisions') or []) + [
            {'id': id, 'text': f"Question of {question['author']}{where}, comment {id}:\n{question['text']}\n{what}"}]
    pwstore.update_entry(entry, **fields)
    pwstore.log(f"{pwstore.key_of(entry)}: question {id} answered: {ANSWERS[choice].lower()}" + (f" ({note})" if note else ''))
    messages = {'yes': 'Compris : Claude traitera ce commentaire au prochain cycle.', 'no': 'Compris : ce commentaire est laissé de côté.',
                'other': 'Compris : Claude suivra votre consigne au prochain cycle.'}
    return pwcommon._result(True, messages[choice])


def wake(repo, pr):
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    pwstore.update_entry(entry, forceWake=True)
    return pwcommon._result(True, f"Réveil demandé : le démon le traitera au prochain cycle (≤ {pwconfig.config()['pollSeconds']} s).")


def unwatch(repo, pr):
    with pwstore.registry_lock():
        entries = pwstore.load_registry()
        kept = [e for e in entries if not (e['repo'] == repo and e['pr'] == pr)]
        pwstore.save_registry(kept)
    return pwcommon._result(len(kept) != len(entries), 'Surveillance arrêtée.' if len(kept) != len(entries) else 'Pull request inconnue.')


def git_push(repo, pr, force=False):
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    command = ['git', 'push', *(['--force-with-lease'] if force else []), 'origin', entry['branch']]
    try:
        done = subprocess.run(command, cwd=entry['workDir'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        return pwcommon._result(False, f'Push impossible : {error}')
    output = f"$ {' '.join(command)}\n(dans {entry['workDir']})\n\n{done.stdout}{done.stderr}".strip()
    return pwcommon._result(done.returncode == 0, 'Push réussi.' if done.returncode == 0 else 'Push refusé ou en échec.', output=output)


def merge(repo, pr, strategy=None, close_source_branch=True):
    if (refused := pwcommon._hidden('merge')):
        return refused
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    strategy = strategy or pwforms.strategy_choices(entry)['defaultStrategy']
    if strategy not in (entry.get('mergeAllowed') or pwforms.MERGE_STRATEGY_IDS):  # what Bitbucket allows for the branch, when known
        return pwcommon._result(False, 'Stratégie de merge inconnue.')
    others = pwmerging.other_open_prs(entry)  # read before the merge: this PR is then finished
    ok, message = pwmerging.merge_pr(entry, strategy, bool(close_source_branch))
    return pwcommon._result(ok, message, otherPrs=others if ok else [])


def pr_settings(repo, pr, settings):
    """Sets the choices of one PR: {'autoPush': True|False|None, 'handling': {category: mode|None}}; None = the global choice."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if not isinstance(settings, dict) or set(settings) - {'autoPush', 'handling', 'autoMerge'}:
        return pwcommon._result(False, 'Réglages invalides.')
    if 'autoPush' in settings and settings['autoPush'] not in (True, False, None):
        return pwcommon._result(False, 'autoPush : vrai, faux ou null attendu')
    try:
        change = {key: (pwconfig.validate_handling(value, partial=True) if key == 'handling'
                        else pwconfig.validate_auto_merge(value, partial=True) if key == 'autoMerge' else value) for key, value in settings.items()}
    except ValueError as error:
        return pwcommon._result(False, str(error))
    pwstore.update_entry(entry, settings=pwconfig.merge_settings(entry, change))
    return pwcommon._result(True, 'Réglages de la PR enregistrés, pris en compte au prochain cycle du démon.', effective=pwconfig.effective(entry, pwconfig.config()))


def pr_settings_form(repo, pr, push, handling, merge=''):
    """`pr_settings` for the answer of the settings dialog, whose choices are ids ('yes', 'no', 'default', '' …)."""
    if not isinstance(push, str) or not isinstance(merge, str) or not isinstance(handling, dict) or not all(isinstance(v, str) for v in handling.values()):
        return pwcommon._result(False, 'Réglages invalides.')
    return pr_settings(repo, pr, pwforms.settings_from_form(push, handling, merge))
