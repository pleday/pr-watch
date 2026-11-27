import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prwatch.engine import runlog


def git(cwd, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=cwd, check=True, capture_output=True)


class RunLogTest(unittest.TestCase):
    def test_commits_made_during_the_run_are_listed_with_their_body(self):
        with tempfile.TemporaryDirectory() as folder:
            git(folder, 'init', '-q')
            git(folder, 'commit', '--allow-empty', '-q', '-m', 'first')
            entry = {'workDir': folder, 'sessionCwd': folder}
            before = runlog.head(entry)
            self.assertEqual(runlog.new_commits(entry, before), [])
            Path(folder, 'a').write_text('a')
            git(folder, 'add', 'a')
            git(folder, 'commit', '-q', '-m', 'fix(x): one', '-m', 'Because.')
            commits = runlog.new_commits(entry, before)
            self.assertEqual([(c[1], c[2]) for c in commits], [('fix(x): one', 'Because.')])
            self.assertEqual(runlog.new_commits(entry, None), [])

    def test_replies_and_resolutions_since_the_start_of_the_run(self):
        started = runlog.utc_now().replace(year=2026, month=11, day=26, hour=10, minute=0, second=0, microsecond=0)
        comments = {'values': [
            {'id': 5, 'created_on': '2026-11-26T10:05:00.000000+00:00', 'content': {'raw': 'Corrigé.\n\n— 🤖 Claude'}},
            {'id': 4, 'created_on': '2026-11-26T09:00:00.000000+00:00', 'content': {'raw': 'old\n— 🤖 Claude'}},
            {'id': 3, 'created_on': '2026-11-26T08:00:00+00:00', 'content': {'raw': 'q ?'}, 'resolution': {'created_on': '2026-11-26T10:06:00+00:00'}},
            {'id': 2, 'created_on': '2026-11-26T08:00:00+00:00', 'content': {'raw': 'q ?'}, 'resolution': {'created_on': '2026-11-26T08:30:00+00:00'}},
        ]}
        with mock.patch.object(runlog.pwbitbucket, 'api', return_value=comments), mock.patch.object(runlog.pwbitbucket, 'repo_path', return_value='/r'):
            replies, resolved = runlog.comment_activity({'pr': 7}, {'claudeReplyMarker': '— 🤖 Claude'}, started)
        self.assertEqual((replies, resolved), ([(5, 'Corrigé.')], [3]))

    def test_the_events_that_woke_the_session_are_listed(self):
        self.assertEqual(runlog.event_lines([{'kind': 'comment', 'text': 'Pourquoi\n ce choix ?'}]), ['- comment: Pourquoi ce choix ?'])


if __name__ == '__main__':
    unittest.main()
