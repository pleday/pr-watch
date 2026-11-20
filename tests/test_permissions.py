import os
import unittest
from unittest import mock

from prwatch.core import permissions

from .helpers import reset_config
from prwatch.engine import service as pwservice
from prwatch.engine import wake as pwwake

ALL = {'read:pullrequest:bitbucket', 'write:pullrequest:bitbucket', 'read:pipeline:bitbucket', 'write:pipeline:bitbucket', 'read:repository:bitbucket'}


class RunTest(unittest.TestCase):
    def run_checks(self, status=403, scopes=ALL, jira=None, env=None, claude=True):
        reset_config()
        env = {'BITBUCKET_EMAIL': 'a@b', 'BITBUCKET_TOKEN': 't', 'ATLASSIAN_API_TOKEN': 'j', **(env or {})}
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(permissions, '_bitbucket', return_value=(status, scopes, None)), \
                mock.patch.object(permissions, '_jira_identity', return_value=jira), mock.patch.object(permissions.shutil, 'which', return_value='/bin/claude' if claude else None):
            return {c['id']: c for c in permissions.run('https://x')}

    def test_everything_granted(self):
        checks = self.run_checks(jira='acc')
        self.assertTrue(all(c['ok'] for c in checks.values()), {k: c['detail'] for k, c in checks.items() if not c['ok']})

    def test_missing_write_scopes_are_named(self):
        checks = self.run_checks(scopes={'read:pullrequest:bitbucket', 'read:pipeline:bitbucket'}, jira='acc')
        self.assertFalse(checks['bitbucket-write']['ok'])
        self.assertIn('write:pullrequest:bitbucket', checks['bitbucket-write']['detail'])
        self.assertTrue(checks['bitbucket-read']['ok'])

    def test_the_repository_scope_is_facultative_and_named_when_missing(self):
        checks = self.run_checks(scopes=ALL - {'read:repository:bitbucket'}, jira='acc')
        self.assertFalse(checks['bitbucket-repository']['ok'])
        self.assertFalse(checks['bitbucket-repository']['required'])
        self.assertIn('read:repository:bitbucket', checks['bitbucket-repository']['detail'])
        self.assertTrue(all(c['ok'] for k, c in checks.items() if k != 'bitbucket-repository'))

    def test_no_read_scope_is_a_blocking_problem(self):
        checks = self.run_checks(scopes={'read:issue:bitbucket'}, jira='acc')
        self.assertFalse(checks['bitbucket-read']['ok'])
        self.assertTrue(checks['bitbucket-read']['required'])

    def test_missing_variables_and_command(self):
        checks = self.run_checks(env={'BITBUCKET_TOKEN': '', 'ATLASSIAN_API_TOKEN': ''}, claude=False)
        self.assertFalse(checks['bitbucket-credentials']['ok'])
        self.assertFalse(checks['jira']['ok'])
        self.assertFalse(checks['claude']['ok'])

    def test_refused_credentials(self):
        self.assertFalse(self.run_checks(status=401, scopes=None)['bitbucket-credentials']['ok'])

    def test_the_account_is_known_through_jira_when_bitbucket_does_not_say(self):
        self.assertTrue(self.run_checks(status=403, jira='acc')['identity']['ok'])
        self.assertFalse(self.run_checks(status=403, jira=None, env={'ATLASSIAN_API_TOKEN': ''})['identity']['ok'])


class DeclineTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        permissions._save({'at': 'x', 'checks': [
            {'id': 'jira', 'label': 'Jira', 'required': False, 'why': '', 'features': ['ticket'], 'ok': False, 'detail': ''},
            {'id': 'bitbucket-write', 'label': 'W', 'required': False, 'why': '', 'features': ['merge'], 'ok': False, 'detail': ''},
            {'id': 'claude', 'label': 'C', 'required': True, 'why': '', 'features': [], 'ok': True, 'detail': ''}], 'declined': []})

    def test_nothing_is_hidden_until_the_user_declines(self):
        self.assertEqual(permissions.hidden_features(), [])
        self.assertEqual(sorted(permissions.summary()['undecided']), ['bitbucket-write', 'jira'])

    def test_declining_hides_the_functions_that_need_it(self):
        permissions.decline(['jira'])
        self.assertEqual(permissions.hidden_features(), ['ticket'])
        self.assertEqual(permissions.summary()['undecided'], ['bitbucket-write'])

    def test_a_permission_granted_later_is_forgotten(self):
        permissions.decline(['jira'])
        with mock.patch.object(permissions, 'run', return_value=[{'id': 'jira', 'ok': True, 'features': ['ticket'], 'label': '', 'required': False, 'why': '', 'detail': ''}]):
            data = permissions.check_and_save('https://x')
        self.assertEqual(data['declined'], [])
        self.assertEqual(permissions.hidden_features(data), [])

    def test_the_blocking_ones_are_the_required_ones_that_fail(self):
        data = permissions.load()
        data['checks'][2]['ok'] = False
        self.assertEqual(permissions.summary(data)['blocking'], ['claude'])


class IsMineTest(unittest.TestCase):
    def test_the_author_is_compared_by_account_or_uuid(self):
        with mock.patch.object(permissions, 'identity', return_value={'account_id': 'me', 'uuid': '{u}'}):
            self.assertTrue(permissions.is_mine({'author': {'account_id': 'me'}}))
            self.assertTrue(permissions.is_mine({'author': {'uuid': '{u}'}}))
            self.assertFalse(permissions.is_mine({'author': {'account_id': 'other', 'uuid': '{o}'}}))
        with mock.patch.object(permissions, 'identity', return_value=None):
            self.assertIsNone(permissions.is_mine({'author': {'account_id': 'me'}}))


class DescribeTest(unittest.TestCase):
    def test_the_report_names_each_check_and_guides_the_missing_ones(self):
        checks = [{'id': 'claude', 'label': 'Claude', 'required': True, 'ok': True, 'detail': 'trouvée'},
                  {'id': 'jira', 'label': 'Jira', 'required': False, 'ok': False, 'detail': 'absent'}]
        text = permissions.describe(checks)
        self.assertIn('✔ Claude — trouvée', text)
        self.assertIn('○ Jira — absent', text)
        self.assertIn('ATLASSIAN_API_TOKEN', text)  # the steps of the guide
        self.assertNotIn('Tout est en place', text)
        self.assertIn('Tout est en place', permissions.describe(checks[:1]))


class StartupTest(unittest.TestCase):
    def test_the_daemon_saves_what_is_missing_and_warns_when_it_blocks(self):
        reset_config()
        checks = [{'id': 'claude', 'label': 'Claude', 'required': True, 'why': '', 'features': [], 'ok': False, 'detail': 'introuvable'},
                  {'id': 'jira', 'label': 'Jira', 'required': False, 'why': '', 'features': ['ticket'], 'ok': False, 'detail': 'absent'}]
        with mock.patch.object(permissions, 'run', return_value=checks), mock.patch.object(pwwake, 'notify') as notify:
            data = pwservice.check_permissions()
        self.assertEqual(permissions.load()['checks'], data['checks'])
        self.assertEqual(permissions.summary()['blocking'], ['claude'])
        self.assertIn('Claude', notify.call_args.args[1])
        self.assertTrue(notify.call_args.kwargs['urgent'])


if __name__ == '__main__':
    unittest.main()
