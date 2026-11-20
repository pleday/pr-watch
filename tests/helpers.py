import json
import subprocess
from pathlib import Path

from prwatch.core import store as pwstore

SESSION = '65f47066-1c3d-445a-b2b1-8106dd96a66f'


def entry(pr, **fields):
    base = {
        'repo': 'fake-repo', 'pr': pr, 'branch': 'feature/x', 'workDir': '/nowhere', 'sessionId': SESSION,
        'sessionCwd': '/work/proj', 'state': 'watching', 'attempts': 1, 'lastPipeline': 100, 'lastComment': 0,
        'title': f'PR {pr}',
    }
    base.update(fields)
    return base


def set_registry(entries):
    pwstore.DIR.mkdir(parents=True, exist_ok=True)
    pwstore.REGISTRY.write_text(json.dumps(entries), encoding='utf-8')


def registry():
    return json.loads(pwstore.REGISTRY.read_text(encoding='utf-8'))


# The options of a configured installation: PR Watch has no team of its own, so the tests have one.
TEAM = {
    'bitbucketWorkspace': 'acme', 'jiraSite': 'https://acme.atlassian.net', 'jiraImpactsField': 'Description des impacts potentiels',
    'sonarPattern': 'sonar', 'reviewStepPattern': 'AI review|pr-review', 'infraPatterns': ['Invalid authentication', 'ECONNRESET'],
    'followBotComments': [{'author': 'Bitbucket Pipelines', 'startsWith': '## 🤖 Revue automatique', 'ignoreIfContains': "Rien d'important à signaler"}],
    'ticketTransitions': {'Tâche': 'Terminé(e)', 'Bug': 'Awaiting deployment'},
}


def reset_config(values=None):
    pwstore.CONFIG.write_text(json.dumps({**TEAM, **(values or {})}), encoding='utf-8')


def git(cwd, *args):
    """Git for the throw-away repositories of the tests: the hooks of the developer's own git config stay out of it."""
    command = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repo_with_remote(root):
    """A working repository on branch feature/x and the bare repository it pushes to."""
    root = Path(root)
    remote, work = root / 'remote.git', root / 'work'
    subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
    work.mkdir()
    git(work, 'init', '-q', '-b', 'feature/x')
    git(work, 'config', 'core.hooksPath', '/dev/null')
    git(work, 'remote', 'add', 'origin', str(remote))
    (work / 'a.txt').write_text('one\n')
    git(work, 'add', '.')
    git(work, 'commit', '-q', '-m', 'first')
    git(work, 'push', '-q', 'origin', 'feature/x')
    return work, remote
