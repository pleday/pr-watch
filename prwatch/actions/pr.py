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


TAB_FILE = 'tab.json'

# A tab hidden for a long time wakes its timers only about once a minute: it still counts as open for a bit longer than that.
TAB_FRESH_SECONDS = 150


def tab_seen(tab):
    """The page of a browser tab says it is there (with each refresh): `pr-watch ui` then has no need to open another."""
    if not isinstance(tab, str) or not 0 < len(tab) <= 40:
        return
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    (pwstore.DIR / TAB_FILE).write_text(json.dumps({'tab': tab, 'at': time.time()}), encoding='utf-8')


def tab_closed(tab):
    """The tab was closed or left: forgotten, unless another tab has taken its place since."""
    try:
        known = json.loads((pwstore.DIR / TAB_FILE).read_text(encoding='utf-8'))
        if known.get('tab') == tab:
            (pwstore.DIR / TAB_FILE).unlink()
    except (OSError, ValueError, AttributeError):
        pass
    return pwcommon._result(True, '')


def tab_age():
    """Seconds since a browser tab last showed the dashboard, None when none is known."""
    try:
        return time.time() - float(json.loads((pwstore.DIR / TAB_FILE).read_text(encoding='utf-8'))['at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# A page that is open polls every 3 s: a signal older than this may come from a tab closed since, so it is asked to answer.
TAB_ALIVE_SECONDS = 6


def tab_is_open(wait=True):
    """Whether a browser tab is showing the dashboard right now.

    A closed tab cannot always say so (the browser may drop its last message), and a hidden one polls only slowly: a signal
    that is not recent is not believed: the tab has a few seconds to give a new one (a live page polls every 3 s).
    """
    age = tab_age()
    if age is None or age > TAB_FRESH_SECONDS:
        return False
    if age < TAB_ALIVE_SECONDS:
        return True
    for _ in range(2 * TAB_ALIVE_SECONDS if wait else 0):
        time.sleep(0.5)
        if (tab_age() or TAB_FRESH_SECONDS) < age:
            return True
    return False


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


def read_reports(repo, pr):
    """Marks the reports of the pull request as read (the badge of its report button goes away)."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if entry.get('reportsUnread'):
        pwstore.update_entry(entry, reportsUnread=0)
    return pwcommon._result(True, '')


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


GROUP_NAME_MAX = 60


def set_layout(order, groups, names=None):
    """Saves how the user arranged the list: the order of the pull requests (the registry's own order) and their groups.

    `order` lists `repo#N` keys, top to bottom; the entries it does not name (added meanwhile) keep their relative order
    after the others. `groups` maps a key to its group name, '' meaning no group; the keys it does not name keep theirs.
    `names`, when given, is the list of the groups in their order, empty ones included (it replaces the stored list).
    """
    if not isinstance(order, list) or not all(isinstance(k, str) for k in order) or not isinstance(groups, dict) or not (names is None or isinstance(names, list)):
        return pwcommon._result(False, 'Ordre ou groupes invalides.')
    invalid = pwcommon._result(False, f'Nom de groupe invalide (au plus {GROUP_NAME_MAX} caractères).')
    clean_name = lambda name: ' '.join(name.split()) if isinstance(name, str) and not any(ord(c) < 32 for c in name) and len(name) <= GROUP_NAME_MAX else None
    members = {}
    for key, name in groups.items():
        if (clean := clean_name(name)) is None:
            return invalid
        members[key] = clean
    listed = None
    if names is not None:
        cleaned = [clean_name(n) for n in names]
        if any(not n for n in cleaned):
            return invalid
        listed = list(dict.fromkeys(cleaned))
        if len({n.casefold() for n in listed}) != len(listed):
            return pwcommon._result(False, 'Deux groupes portent le même nom.')
    with pwstore.registry_lock():
        entries = pwstore.load_registry()
        by_key = {pwstore.key_of(e): e for e in entries}
        wanted = [by_key[k] for k in dict.fromkeys(order) if k in by_key]
        rest = [e for e in entries if e not in wanted]
        for key, name in members.items():
            if key not in by_key:
                continue
            if name:
                by_key[key]['group'] = name
            else:
                by_key[key].pop('group', None)
        pwstore.save_registry(wanted + rest)
        if listed is not None:
            pwstore.save_groups(listed)
    return pwcommon._result(True, 'Rangement enregistré.')


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


def _git(entry, *args, timeout=60):
    """Runs git in the clone of a pull request: the finished process, None when git cannot run or takes too long."""
    try:
        return subprocess.run(['git', *args], cwd=entry['workDir'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _remote_branch_unknown(entry):
    """True when the branch on origin is at a commit this clone does not have: someone pushed, and only a fetch brings it.

    The push hook of the repository cannot list the commits it sends then (`Invalid revision range`), and Bitbucket refuses
    the push anyway. A network or git error says nothing: the push goes on and reports it itself.
    """
    listed = _git(entry, 'ls-remote', '--heads', 'origin', entry['branch'], timeout=30)
    tip = listed.stdout.split()[0] if listed and listed.returncode == 0 and listed.stdout.strip() else ''
    if not tip:
        return False
    known = _git(entry, 'cat-file', '-e', f'{tip}^{{commit}}', timeout=10)
    return bool(known) and known.returncode != 0


def _commits_behind(entry):
    """How many commits origin/<branch> has that the local branch lacks (as last fetched); 0 when it cannot tell.

    The branch itself is compared, not HEAD: the clone may have another branch checked out (a PR added by hand).
    """
    counted = _git(entry, 'rev-list', '--count', f"{entry['branch']}..origin/{entry['branch']}", timeout=10)
    try:
        return int(counted.stdout) if counted and counted.returncode == 0 else 0
    except ValueError:
        return 0


def git_push(repo, pr, force=False):
    """Pushes the branch; before a plain push, a branch that moved on origin is reported instead of pushed.

    `needsFetch`: origin has commits this clone has never seen. `needsRebase`: it has them, and the local branch lacks them.
    """
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if not force and _remote_branch_unknown(entry):
        return pwcommon._result(False, 'La branche a avancé sur origin : récupérez-la avant de pousser.', needsFetch=True)
    if not force and _commits_behind(entry):
        return pwcommon._result(False, 'La branche locale est en retard sur origin : rebasez-la avant de pousser.', needsRebase=True)
    command = ['git', 'push', *(['--force-with-lease'] if force else []), 'origin', entry['branch']]
    try:
        done = subprocess.run(command, cwd=entry['workDir'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        return pwcommon._result(False, f'Push impossible : {error}')
    output = f"$ {' '.join(command)}\n(dans {entry['workDir']})\n\n{done.stdout}{done.stderr}".strip()
    return pwcommon._result(done.returncode == 0, 'Push réussi.' if done.returncode == 0 else 'Push refusé ou en échec.', output=output)


def git_fetch(repo, pr):
    """`git fetch origin <branch>`: updates origin/<branch> only; the local branch and the working tree are not touched."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    command = ['git', 'fetch', 'origin', entry['branch']]
    done = _git(entry, *command[1:], timeout=180)
    if done is None:
        return pwcommon._result(False, 'Récupération impossible : git ne répond pas.')
    output = f"$ {' '.join(command)}\n(dans {entry['workDir']})\n\n{done.stdout}{done.stderr}".strip()
    return pwcommon._result(done.returncode == 0, 'Branche récupérée.' if done.returncode == 0 else 'Récupération en échec.', output=output)


def git_rebase(repo, pr):
    """`git rebase origin/<branch>`; on a conflict the rebase is aborted, so the clone is left as it was."""
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    current = _git(entry, 'rev-parse', '--abbrev-ref', 'HEAD', timeout=10)
    if current is None or current.stdout.strip() != entry['branch']:
        # rebasing another branch would mean switching the checkout under the user's feet
        return pwcommon._result(False, f"Rebase impossible : {entry['workDir']} n’est pas sur la branche {entry['branch']} (faites `git checkout {entry['branch']}` d’abord).")
    command = ['git', 'rebase', f"origin/{entry['branch']}"]
    done = _git(entry, *command[1:], timeout=180)
    if done is None:
        return pwcommon._result(False, 'Rebase impossible : git ne répond pas.')
    output = f"$ {' '.join(command)}\n(dans {entry['workDir']})\n\n{done.stdout}{done.stderr}".strip()
    if done.returncode != 0:
        aborted = _git(entry, 'rebase', '--abort', timeout=30)
        if aborted and aborted.returncode == 0:
            output += '\n\n$ git rebase --abort\nRebase annulé : le clone est revenu à son état d’avant.'
    return pwcommon._result(done.returncode == 0, 'Branche rebasée.' if done.returncode == 0 else 'Rebase en échec (conflits ou fichiers modifiés).', output=output)


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
