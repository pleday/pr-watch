"""The Bitbucket Cloud API: credentials, calls, and the few reads the other modules share."""

import base64
import json
import os
import re
import time
import urllib.request

from prwatch.core import store as pwstore

API = 'https://api.bitbucket.org/2.0'


def workspace():
    """The Bitbucket workspace of the pull requests (`bitbucketWorkspace` of the configuration)."""
    from prwatch.core import config as pwconfig  # imported here: the configuration does not depend on this module, but keep it that way
    return pwconfig.config()['bitbucketWorkspace']


def auth_header():
    email, token = os.environ.get('BITBUCKET_EMAIL'), os.environ.get('BITBUCKET_TOKEN')
    if not email or not token:
        raise SystemExit('BITBUCKET_EMAIL and BITBUCKET_TOKEN must be set in the environment of the daemon')
    return 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode()


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Step logs are served from a signed URL of another host, which refuses a request that also carries our token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            new.remove_header('Authorization')
        return new


_OPENER = urllib.request.build_opener(_DropAuthOnRedirect)


def api(path, params=None, raw=False):
    url = path if path.startswith('http') else f'{API}{path}'
    if params:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'Authorization': auth_header()})
    for attempt in range(3):
        try:
            with _OPENER.open(request, timeout=30) as response:
                body = response.read().decode('utf-8', 'replace')
                pwstore.note_bitbucket(True)
                return body if raw else json.loads(body)
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            pwstore.log(f'API {error.code} on {path}')
            # 4xx (a missing right, an unknown page) is an answer: Bitbucket is up. Only the overload and server errors count.
            pwstore.note_bitbucket(error.code < 500 and error.code != 429)
            return None
        except (urllib.error.URLError, TimeoutError) as error:
            pwstore.log(f'API error on {path}: {error}')
            pwstore.note_bitbucket(False)
            time.sleep(3)
    return None


def repo_path(entry):
    return f"/repositories/{workspace()}/{entry['repo']}"


def max_comment_id(entry):
    data = api(f"{repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': '-id'}) or {}
    return max([c['id'] for c in data.get('values', [])] or [0])


def max_pipeline(entry):
    data = api(
        f"{repo_path(entry)}/pipelines/",
        {'target.branch': entry['branch'], 'sort': '-created_on', 'pagelen': 1},
    ) or {}
    return max([p['build_number'] for p in data.get('values', [])] or [0])


def api_post(path):
    """POSTs to Bitbucket with no body: (ok, what it answered). Failures are logged, as `api` does."""
    request = urllib.request.Request(f'{API}{path}', data=b'', headers={'Authorization': auth_header()}, method='POST')
    try:
        with _OPENER.open(request, timeout=30) as response:
            return True, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        pwstore.log(f'API {error.code} on POST {path}')
        return False, f'{error.code}'
    except (urllib.error.URLError, TimeoutError) as error:
        pwstore.log(f'API error on POST {path}: {error}')
        return False, str(error)


def all_comments(entry):
    """Every comment of the PR (at most five pages of 100), oldest first."""
    comments, path, params = [], f"{repo_path(entry)}/pullrequests/{entry['pr']}/comments", {'pagelen': 100, 'sort': 'id'}
    for _ in range(5):
        data = api(path, params) or {}
        comments += data.get('values', [])
        if not data.get('next'):
            break
        path, params = data['next'], None
    return comments


SCOPE = re.compile(r'^\w+!?\(([^)]+)\)')


def commit_scopes(entry):
    """The Conventional Commits scopes of the PR's commits ('feat(e2e): …' -> 'e2e'), one per commit that has one; None when the read fails."""
    found = api(f"{repo_path(entry)}/pullrequests/{entry['pr']}/commits", {'pagelen': 50})
    if found is None:
        return None
    return [m.group(1).strip() for c in found.get('values', []) if (m := SCOPE.match((c.get('message') or '').strip()))]


def rerun_pipeline(entry, pr):
    """Starts the pull-request pipeline again on the latest commit. Returns (ok, message)."""
    body = {
        'target': {
            'type': 'pipeline_pullrequest_target',
            'source': pr['source']['branch']['name'],
            'destination': pr['destination']['branch']['name'],
            'commit': {'type': 'commit', 'hash': pr['source']['commit']['hash']},
            'destination_commit': {'hash': pr['destination']['commit']['hash']},
            'pullrequest': {'id': pr['id']},
            'selector': {'type': 'pull-requests', 'pattern': '**'},
        }
    }
    request = urllib.request.Request(
        f"{API}{repo_path(entry)}/pipelines/",
        data=json.dumps(body).encode(),
        headers={'Authorization': auth_header(), 'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            created = json.loads(response.read())
        return True, f"pipeline #{created.get('build_number')} relancé"
    except urllib.error.HTTPError as error:
        return False, f"relance refusée ({error.code})"
