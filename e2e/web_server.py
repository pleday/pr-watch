"""The dashboard server of the end-to-end test: the real one, except that Bitbucket is faked (no network, no token)."""
import sys
from unittest import mock

from prwatch import cli
from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import permissions

# The one pull request the form can look up; its branch is the one checked out in the second clone of the test.
PR = {'state': 'OPEN', 'title': 'PROJ-123 Ajouter la recherche', 'author': {'account_id': 'me', 'display_name': 'Moi'},
      'source': {'branch': {'name': 'PROJ-123-recherche'}}}


def fake_api(path, params=None, raw=False):
    return PR if path.endswith('/repositories/acme/my-repo/pullrequests/12') else None


with mock.patch.object(pwbitbucket, 'api', fake_api), mock.patch.object(permissions, 'identity', return_value={'account_id': 'me', 'uuid': None}):
    sys.argv = ['prwatch', 'web', 'serve', *sys.argv[1:]]
    cli.main()
