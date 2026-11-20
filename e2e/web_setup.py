"""Fixtures of the end-to-end test: a data directory of its own, fake PRs of every state, a git repo with a local remote, the dashboard started on port 8799."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / 'e2e' / '.work'
HOME = ROOT / 'home'
WORK = ROOT / 'work'
REMOTE = ROOT / 'remote.git'
CLONES = ROOT / 'clones'  # where the local clones of fake-bitbucket repositories are looked for
REAL_SESSION = os.environ.get('E2E_SESSION', '00000000-0000-0000-0000-000000000001')

previous = ROOT / 'info.json'
if previous.exists():
    try:
        os.kill(json.loads(previous.read_text())['pid'], 15)
        time.sleep(0.5)
    except (OSError, ValueError, KeyError):
        pass
subprocess.run(['rm', '-rf', str(ROOT)])
HOME.mkdir(parents=True)


def git(*args, cwd=WORK):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


subprocess.run(['git', 'init', '-q', '--bare', str(REMOTE)], check=True)
WORK.mkdir()
git('init', '-q', '-b', 'feature/x')
git('remote', 'add', 'origin', str(REMOTE))
git('config', 'core.hooksPath', '/dev/null')
(WORK / 'a.txt').write_text('one\n')
git('add', '.')
git('commit', '-q', '-m', 'first')
git('push', '-q', 'origin', 'feature/x')


def entry(pr, **fields):
    base = {
        'repo': 'fake-repo', 'pr': pr, 'branch': 'feature/x', 'workDir': str(WORK), 'sessionId': REAL_SESSION,
        'sessionCwd': '/work/proj', 'state': 'watching', 'attempts': 1, 'lastPipeline': 100 + pr,
        'title': f'PR numéro {pr}', 'registeredAt': '2026-11-17 10:00:00',
    }
    base.update(fields)
    return base


# Two clones of the same Bitbucket repository, one under another name and holding the branch of the PR that the form looks up, and an
# unrelated one: no network is needed, only the address of `origin` is read.
CLONES.mkdir()
for name, origin, branch in (('my-repo', 'acme/my-repo', 'develop'), ('my-repo-bis', 'acme/my-repo', 'PROJ-123-recherche'), ('another', 'acme/another', 'develop')):
    git('init', '-q', '-b', branch, name, cwd=CLONES)
    git('remote', 'add', 'origin', f'git@bitbucket.org:{origin}.git', cwd=CLONES / name)

registry = [
    entry(1, title='<img src=x onerror="window.__xss=1"> Remplacer Shepherd', attempts=2, mergeBlockers=['approbations 0/1', 'pipeline #6150 en échec'],
          lastReport='Rapport de test\n\n- **Cause :** `npmAuthToken` absent\n\nPR-WATCH: OK', lastReportAt='2026-11-17 10:31:21'),
    entry(2, title='Prête à être mergée', branch='feature/PROJ-16126-prete', ready=True, mergeBlockers=[], mergeDefault='merge_commit', mergeAllowed=['squash', 'merge_commit']),
    entry(3, title='Demande votre attention', needsAttention='Pipeline #1 en échec <b>gras</b> pour une raison d\'infrastructure'),
    entry(4, title='Abandonnée', state='gave-up', attempts=5),
    entry(5, title='En pause', state='paused'),
    entry(6, title='Terminée', state='done', doneReason='MERGED'),
    entry(7, title='Un relecteur pose une question', pendingQuestions=[
        {'id': 41, 'author': 'Relecteur', 'place': 'src/a.ts:12', 'text': 'On valide cette approche ?\n<b>pas du html</b>', 'at': '2026-11-17 10:40:00'}]),
]
(HOME / 'registry.json').write_text(json.dumps(registry, indent=2), encoding='utf-8')
# every permission granted: no guide pops up over the page the test clicks on
(HOME / 'permissions.json').write_text(json.dumps({'at': '2026-01-01 00:00:00', 'checks': [{'id': 'claude', 'label': 'claude', 'required': True, 'why': '', 'features': [], 'ok': True, 'detail': 'ok'}], 'declined': []}), encoding='utf-8')
# a configured installation: with no team settings the page shows the first-start banner and has no ticket links
(HOME / 'config.json').write_text(json.dumps({'bitbucketWorkspace': 'acme', 'jiraSite': 'https://acme.atlassian.net', 'jiraImpactsField': 'Description des impacts potentiels',
                                                'ticketTransitions': {'Tâche': 'Close', 'Bug': 'Corrigé'}, 'cloneFolders': [str(CLONES)]}), encoding='utf-8')
(HOME / 'daemon.log').write_text(
    """2026-11-17 10:00:01 daemon started (pid 12)
2026-11-17 10:00:07 fake-repo#1: session in use, 1 event(s) kept for later
2026-11-17 10:00:08 API 403 on /repositories/x/commit/e418edb16a12/statuses
2026-11-17 10:01:07 fake-repo#1: session in use, 1 event(s) kept for later
2026-11-17 10:01:08 API 403 on /repositories/x/commit/e418edb16a12/statuses
2026-11-17 10:04:00 fake-repo#1: resuming session 65f4 for 2 event(s)
2026-11-17 10:05:00 fake-repo#1: claude exited 0
Le pipeline a échoué <script>window.__xss=2</script>.

- **Cause :** `npmAuthToken` absent
  - détail imbriqué
1. première étape

PR-WATCH: ATTENTION le relecteur demande un choix
2026-11-17 10:05:01 NOTIFY PR 1 : votre attention est nécessaire: le relecteur demande un choix
2026-11-18 09:00:00 NOTIFY PR 2 prête à être mergée: fake-repo : titre
2026-11-18 09:01:00 fake-repo#1: claude exited 1
""",
    encoding='utf-8',
)

env = {**os.environ, 'PR_WATCH_HOME': str(HOME), 'PYTHONPATH': str(REPO)}
subprocess.Popen([sys.executable, str(REPO / 'e2e' / 'web_server.py'), '8799'], env=env, cwd=REPO, stdout=open(ROOT / 'web.out', 'w'), stderr=subprocess.STDOUT, start_new_session=True)
for _ in range(50):
    time.sleep(0.2)
    if (HOME / 'web.json').exists():
        break
info = json.loads((HOME / 'web.json').read_text())
(ROOT / 'info.json').write_text(json.dumps({**info, 'home': str(HOME), 'work': str(WORK), 'remote': str(REMOTE), 'clones': str(CLONES)}))
print(json.dumps({'port': info['port'], 'pid': info['pid']}))
