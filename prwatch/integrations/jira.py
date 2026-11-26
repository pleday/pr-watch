"""The Jira ticket of a pull request: its state, the states it can move to (read from the ticket itself), and the move.

The ticket key is read from the branch or the title of the PR. Jira Cloud is called with the Atlassian account of
`BITBUCKET_EMAIL` and an API token in `ATLASSIAN_API_TOKEN`, both in the environment of the process that serves the
dashboard. The REST API version 2 is used: its multi-line text fields take plain text.
"""
import base64
import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
import os
import re
import urllib.error
import urllib.parse
import urllib.request

KEY = re.compile(r'\b[A-Z][A-Z0-9]+-\d+\b')
# The field the workflow of the project asks to fill before a ticket leaves some states.
IMPACTS_FIELD_NAME = 'Description des impacts potentiels'  # the default of `jiraImpactsField`

_FIELD_IDS = {}  # (site, field name) -> id


def ticket_key(entry):
    """The key of the Jira ticket the pull request belongs to ('PROJ-123'), None when neither branch nor title has one."""
    for text in (entry.get('branch') or '', entry.get('title') or ''):
        match = KEY.search(text)
        if match:
            return match.group(0)
    return None


class JiraError(Exception):
    """What went wrong, in a sentence fit for the user."""


def _auth():
    email, token = os.environ.get('BITBUCKET_EMAIL'), os.environ.get('ATLASSIAN_API_TOKEN')
    if not email or not token:
        raise JiraError('Jira : ATLASSIAN_API_TOKEN (et BITBUCKET_EMAIL) doivent être définis dans l’environnement du tableau de bord.')
    return 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode()


def request(site, method, path, body=None):
    """Calls Jira and returns the decoded answer (None when it has no body); raises JiraError with Jira's own message."""
    if not site:
        raise JiraError('Jira : renseignez le site Jira (Options › Équipe).')
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{site.rstrip('/')}/rest/api/2{path}", data=data, method=method,
        headers={'Authorization': _auth(), 'Accept': 'application/json', **({'Content-Type': 'application/json'} if data else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')
        try:
            parsed = json.loads(detail)
            detail = ' ; '.join([*parsed.get('errorMessages', []), *[f'{k} : {v}' for k, v in (parsed.get('errors') or {}).items()]]) or detail
        except ValueError:
            if re.search(r'<\s*(html|!doctype)', detail, re.I):  # an error page of the site (503...): its title says it, its markup is noise
                title = re.search(r'<title>(.*?)</title>', detail, re.I | re.S)
                detail = f"page d'erreur du site ({' '.join(title.group(1).split())})" if title else "page d'erreur du site"
        raise JiraError(f'Jira a refusé ({error.code}) : {detail[:400]}') from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise JiraError(f'Jira injoignable : {error}') from error
    return json.loads(text) if text.strip() else None


def impacts_field_name():
    """The name of the impacts field (`jiraImpactsField` of the configuration); '' when the project has none."""
    from prwatch.core import config as pwconfig
    return pwconfig.config()['jiraImpactsField'].strip()


def impacts_field(site):
    """The id of the impacts field (customfield_…), None when none is configured or the site has no such field."""
    name = impacts_field_name()
    if not name:
        return None
    key = (site, name)
    if key not in _FIELD_IDS:
        found = next((f['id'] for f in request(site, 'GET', '/field') or [] if name.lower() in f.get('name', '').lower()), None)
        _FIELD_IDS[key] = found
    return _FIELD_IDS[key]


def _plain(text):
    """Lower case, no accents, words separated by single spaces: 'Terminé(e)' and 'termine e' compare equal."""
    folded = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.findall(r'[a-z0-9]+', folded))


def wanted_transition(mapping, issue_type, transitions):
    """The id of the transition the user wants by default for a ticket of this type, None when there is none.

    `mapping` goes from a type of ticket ('Tâche') to a state name; a type that is not in it has nothing preselected, since each
    type has a workflow of its own. A state is found by the name of the transition or of the state it leads to (exactly, then as
    part of it), without regard to case or accents.
    """
    wanted = _plain(next((state for kind, state in (mapping or {}).items() if _plain(kind) == _plain(issue_type) and _plain(kind)), ''))
    if not wanted:
        return None
    for same in (lambda text: text == wanted, lambda text: wanted in text):
        for field in ('to', 'name'):
            for transition in transitions:
                if same(_plain(transition.get(field))):
                    return transition['id']
    return None


MAX_PROJECTS = 40


def metadata(site, projects):
    """The types of ticket and the states of their workflows, read from Jira: {'types': [...], 'statuses': {type: [state, ...]}, 'allStatuses': [...]}.

    The workflow of a type is the one it has in the projects (`/project/KEY/statuses`); with no project given, every project
    of the site is read (`MAX_PROJECTS` at most), so that the states of a type are known whatever the tickets being watched.
    A project that cannot be read is skipped. Raises JiraError when Jira can not be asked at all.
    """
    keys = list(projects)
    if not keys:
        keys = [p['key'] for p in request(site, 'GET', '/project') or [] if p.get('key')][:MAX_PROJECTS]

    def workflows(key):
        try:
            return request(site, 'GET', f"/project/{urllib.parse.quote(key)}/statuses") or []
        except JiraError:
            return []

    per_type = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for kinds in pool.map(workflows, keys):
            for kind in kinds:
                states = per_type.setdefault(kind.get('name', ''), [])
                states.extend(s['name'] for s in kind.get('statuses', []) if s.get('name') and s['name'] not in states)
    per_type.pop('', None)
    if per_type:
        everything = sorted({s for states in per_type.values() for s in states})
        return {'types': sorted(per_type), 'statuses': {k: sorted(v) for k, v in per_type.items()}, 'allStatuses': everything}
    types = sorted({t['name'] for t in request(site, 'GET', '/issuetype') or [] if t.get('name')})
    states = sorted({s['name'] for s in request(site, 'GET', '/status') or [] if s.get('name')})
    return {'types': types, 'statuses': {}, 'allStatuses': states}


def ticket_url(site, key):
    """The address of the ticket in Jira, None without a key."""
    return f"{site.rstrip('/')}/browse/{key}" if key and site else None


def status(site, key):
    """The state of the ticket ('En cours'), read alone; raises JiraError."""
    issue = request(site, 'GET', f"/issue/{urllib.parse.quote(key)}?fields=status") or {}
    return ((issue.get('fields') or {}).get('status') or {}).get('name') or '?'


def info(site, key):
    """{'key', 'summary', 'status', 'transitions': [{'id', 'name', 'to'}], 'impacts', 'impactsField'} read from the ticket."""
    field = impacts_field(site)
    wanted = ['summary', 'status', 'issuetype'] + ([field] if field else [])
    issue = request(site, 'GET', f"/issue/{urllib.parse.quote(key)}?fields={','.join(wanted)}")
    moves = (request(site, 'GET', f"/issue/{urllib.parse.quote(key)}/transitions") or {}).get('transitions', [])
    fields = issue.get('fields', {})
    return {
        'key': key,
        'summary': fields.get('summary', ''),
        'status': (fields.get('status') or {}).get('name', '?'),
        'type': (fields.get('issuetype') or {}).get('name', ''),
        'transitions': [{'id': t['id'], 'name': t.get('name', ''), 'to': (t.get('to') or {}).get('name', '')} for t in moves],
        'impacts': (fields.get(field) or '') if field else '',
        'impactsField': field,
    }


def suggest_impacts(title, paths):
    """A first draft of "Description des impacts potentiels", from the title of the PR and the files it changes.

    Only a starting point for the user to correct: it names what changed and where, which is what a tester needs to know.
    """
    subject = KEY.sub('', re.sub(r'^\w+(\([^)]*\))?!?:\s*', '', title or '')).strip(' []-')
    zones = {}
    for path in paths:
        parts = path.split('/')
        zone = '/'.join(parts[:-1][:3]) or parts[-1]
        zones[zone] = zones.get(zone, 0) + 1
    listed = ', '.join(f"{zone} ({count} fichier{'s' if count > 1 else ''})" for zone, count in sorted(zones.items(), key=lambda z: -z[1])[:6])
    lines = [f'Changement : {subject}.' if subject else 'Changement : voir la pull request.']
    if listed:
        lines.append(f"Zones modifiées : {listed}{' …' if len(zones) > 6 else ''}.")
    lines.append('À vérifier : le comportement des écrans et fonctionnalités qui s’appuient sur ces zones.')
    return '\n'.join(lines)


def move(site, key, transition, impacts=None):
    """Writes the impacts (when given) then runs the transition. Returns a sentence saying what was done; raises JiraError."""
    done = []
    if impacts and impacts.strip():
        field = impacts_field(site)
        if not field:
            raise JiraError(f'Jira : le champ « {impacts_field_name() or "impacts"} » est introuvable sur ce site.')
        request(site, 'PUT', f'/issue/{urllib.parse.quote(key)}', {'fields': {field: impacts.strip()}})
        done.append('impacts renseignés')
    request(site, 'POST', f'/issue/{urllib.parse.quote(key)}/transitions', {'transition': {'id': str(transition)}})
    done.append('état changé')
    return f"Ticket {key} : {' et '.join(done)}."
