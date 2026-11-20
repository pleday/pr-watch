"""Every test runs against a data directory and a home of its own, never the real ones."""
import os
import tempfile

_ROOT = tempfile.mkdtemp(prefix='prwatch-tests-')
os.environ['PR_WATCH_HOME'] = os.path.join(_ROOT, 'data')
os.environ['HOME'] = os.path.join(_ROOT, 'home')
os.environ['USERPROFILE'] = os.environ['HOME']
os.environ.pop('XDG_DATA_HOME', None)
os.makedirs(os.environ['PR_WATCH_HOME'])
os.makedirs(os.environ['HOME'])

# Without a config file the team options are empty (first start): the tests start from a configured installation.
import json  # noqa: E402

from tests.helpers import TEAM  # noqa: E402

with open(os.path.join(os.environ['PR_WATCH_HOME'], 'config.json'), 'w', encoding='utf-8') as _file:
    json.dump(TEAM, _file)

# No test may start the real Claude CLI to measure the quotas of the developer's plan.
from prwatch.integrations import usage as _usage  # noqa: E402

_usage.probe = lambda *args, **kwargs: None
