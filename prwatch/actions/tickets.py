"""The Jira ticket of a pull request: its states, the draft of the impacts, the move."""

import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from prwatch.core import bitbucket as pwbitbucket
from prwatch.actions import common as pwcommon
from prwatch.core import config as pwconfig
from prwatch.engine import merging as pwmerging
from prwatch.integrations import jira as pwjira
from prwatch.core import store as pwstore
from prwatch.system import claude as pwclaude

def changed_files(entry):
    """The files the PR changes, read from the local clone (the diffstat of Bitbucket needs a scope the token may lack)."""
    pr = pwbitbucket.api(f"{pwbitbucket.repo_path(entry)}/pullrequests/{entry['pr']}") or {}
    destination = ((pr.get('destination') or {}).get('branch') or {}).get('name')
    if destination:
        for source in (f"origin/{entry['branch']}", entry['branch']):
            try:
                done = subprocess.run(['git', 'diff', '--name-only', f'origin/{destination}...{source}'], cwd=entry['workDir'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
            except (OSError, subprocess.SubprocessError):
                return []
            if done.returncode == 0:
                return [line for line in done.stdout.splitlines() if line]
    return []


def french_impacts(entry, paths):
    """The draft written in French by Claude from the title and the files of the PR (titles and commits are in English).

    None when Claude is not reachable.
    """
    prompt = (
        "Rédige en français, pour un testeur, la « Description des impacts potentiels » d'un ticket Jira "
        "dont la pull request vient d'être mergée. 3 à 5 lignes courtes, sans titre ni markdown : ce qui change, "
        "les écrans ou fonctionnalités touchés, ce qu'il faut vérifier. Ne rien inventer au-delà de ces informations. "
        "Réponds uniquement par le texte du champ.\n\n"
        f"Titre de la PR : {entry.get('title') or ''}\nFichiers modifiés :\n" + '\n'.join(paths[:60])
    )
    if pwstore.suspension():
        return None
    return pwclaude.ask(pwconfig.config()['claudeBinary'], prompt)


def ticket_info(repo, pr):
    """The Jira ticket of the PR: its state, the states it can move to, and a draft of the impacts description."""
    if (refused := pwcommon._hidden('ticket')):
        return refused
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    key = pwjira.ticket_key(entry)
    if not key:
        return pwcommon._result(False, 'Aucun ticket Jira dans le nom de la branche ni dans le titre.')
    site = pwconfig.config()['jiraSite']
    # Jira, Bitbucket and git are asked at the same time: the dialog waits for the slowest, not for the sum.
    with ThreadPoolExecutor(max_workers=2) as pool:
        files = pool.submit(changed_files, entry)
        try:
            info = pwjira.info(site, key)
        except pwjira.JiraError as error:
            return pwcommon._result(False, str(error))
        paths = [p for p in files.result() if p]
    suggestion = pwjira.suggest_impacts(entry.get('title'), paths)
    merged = entry.get('doneReason') == 'MERGED'
    # Once the PR is merged the state the user chose for this type of ticket is preselected (`ticketTransitions`); before that
    # the user chooses.
    preselected = pwjira.wanted_transition(pwconfig.config()['ticketTransitions'], info.get('type'), info['transitions']) if merged else None
    return pwcommon._result(True, f"Ticket {key} : {info['status']}.", ticket=info, suggestion=suggestion, preselected=preselected,
                             url=f"{site.rstrip('/')}/browse/{key}", claudeSuspended=bool(pwstore.suspension()))


_METADATA = [0.0, None]


def jira_metadata():
    """The types of ticket and the states of their workflows, for the settings of the state to move to (kept ten minutes)."""
    if (refused := pwcommon._hidden('ticket')):
        return refused
    if _METADATA[1] and time.time() - _METADATA[0] < 600:
        return pwcommon._result(True, '', **_METADATA[1])
    projects = sorted({key.split('-')[0] for key in map(pwjira.ticket_key, pwstore.load_registry()) if key})
    try:
        data = pwjira.metadata(pwconfig.config()['jiraSite'], projects)
    except pwjira.JiraError as error:
        return pwcommon._result(False, str(error))
    _METADATA[:] = [time.time(), {**data, 'projects': projects}]
    return pwcommon._result(True, '', **_METADATA[1])


def ticket_draft(repo, pr):
    """The proposal of the impacts description written in French by Claude. Asked apart from `ticket_info` because it takes
    seconds: the dialog opens with the template and swaps this text in when it arrives."""
    if (refused := pwcommon._hidden('ticket')):
        return refused
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    if pwstore.suspension():
        return pwcommon._result(False, 'Claude est suspendu : la traduction automatique est désactivée.', suspended=True)
    paths = [p for p in changed_files(entry) if p]
    text = french_impacts(entry, paths)
    if pwstore.suspension():
        return pwcommon._result(False, 'Claude est suspendu : la traduction automatique est désactivée.', suspended=True)
    return pwcommon._result(bool(text), 'Proposition rédigée.' if text else 'Claude n’a pas répondu.', suggestion=text)


def ticket_transition(repo, pr, transition, impacts=None):
    if (refused := pwcommon._hidden('ticket')):
        return refused
    entry = pwcommon._entry(repo, pr)
    if not entry:
        return pwcommon._result(False, 'Pull request inconnue.')
    key = pwjira.ticket_key(entry)
    if not key:
        return pwcommon._result(False, 'Aucun ticket Jira dans le nom de la branche ni dans le titre.')
    if not isinstance(transition, str) or not transition.isdigit() or (impacts is not None and not isinstance(impacts, str)):
        return pwcommon._result(False, 'Demande invalide.')
    try:
        message = pwjira.move(pwconfig.config()['jiraSite'], key, transition, impacts)
    except pwjira.JiraError as error:
        return pwcommon._result(False, str(error))
    pwstore.log(f"{pwstore.key_of(entry)}: {message}")
    for waiting in pwstore.load_registry():  # every merged PR of this ticket was waiting for exactly this
        if pwjira.ticket_key(waiting) == key and waiting.get('ticketWatch') and not waiting.get('ticketValidated'):
            pwmerging.ticket_validated(waiting)
    return pwcommon._result(True, message)
