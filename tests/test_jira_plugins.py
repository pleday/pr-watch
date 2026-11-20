import os
import tempfile
import unittest
import zipfile
from unittest import mock

from prwatch import actions
from prwatch.integrations import jira
from prwatch.integrations import plugins

from .helpers import entry, reset_config, set_registry
from prwatch.actions import status as pwstatus
from prwatch.core import config as pwconfig
from prwatch.actions import tickets as pwtickets
from prwatch.system import editors as pweditors


class TicketKeyTest(unittest.TestCase):
    def test_the_key_comes_from_the_branch_then_the_title(self):
        self.assertEqual(jira.ticket_key({'branch': 'bugfix/PROJ-16126-section', 'title': 'x [PROJ-1]'}), 'PROJ-16126')
        self.assertEqual(jira.ticket_key({'branch': 'main', 'title': 'fix(a): b [PROJ-16107]'}), 'PROJ-16107')
        self.assertIsNone(jira.ticket_key({'branch': 'main', 'title': 'no ticket'}))


class JiraTest(unittest.TestCase):
    def test_the_states_are_read_from_the_ticket(self):
        answers = {
            '/field': [{'id': 'customfield_1', 'name': '📝 Description des impacts potentiels'}],
            '/issue/PROJ-1?fields=summary,status,issuetype,customfield_1': {'fields': {'summary': 'S', 'status': {'name': 'En revue'}, 'issuetype': {'name': 'Tâche'}, 'customfield_1': 'déjà'}},
            '/issue/PROJ-1/transitions': {'transitions': [{'id': '171', 'name': 'To test', 'to': {'name': 'A tester'}}]},
        }
        jira._FIELD_IDS.clear()
        with mock.patch.object(jira, 'request', side_effect=lambda site, method, path, body=None: answers[path]):
            info = jira.info('https://x', 'PROJ-1')
        self.assertEqual((info['status'], info['type']), ('En revue', 'Tâche'))
        self.assertEqual(info['transitions'], [{'id': '171', 'name': 'To test', 'to': 'A tester'}])
        self.assertEqual((info['impacts'], info['impactsField']), ('déjà', 'customfield_1'))

    def test_the_impacts_are_written_before_the_transition(self):
        calls = []
        jira._FIELD_IDS[('https://x', jira.IMPACTS_FIELD_NAME)] = 'customfield_1'
        with mock.patch.object(jira, 'request', side_effect=lambda site, method, path, body=None: calls.append((method, path, body))):
            message = jira.move('https://x', 'PROJ-1', '171', ' texte ')
        self.assertEqual([c[:2] for c in calls], [('PUT', '/issue/PROJ-1'), ('POST', '/issue/PROJ-1/transitions')])
        self.assertEqual(calls[0][2], {'fields': {'customfield_1': 'texte'}})
        self.assertEqual(calls[1][2], {'transition': {'id': '171'}})
        self.assertIn('impacts renseignés', message)

    def test_a_refusal_of_jira_reaches_the_user(self):
        jira._FIELD_IDS[('https://x', jira.IMPACTS_FIELD_NAME)] = 'customfield_1'
        with mock.patch.object(jira, 'request', side_effect=[None, jira.JiraError('Jira a refusé (400) : impacts requis')]):
            with self.assertRaises(jira.JiraError):
                jira.move('https://x', 'PROJ-1', '171', 'x')

    def test_the_draft_names_the_change_and_the_zones(self):
        text = jira.suggest_impacts('fix(content-view): honour a view [PROJ-1]', ['src/a/b/x.ts', 'src/a/b/y.ts', 'README.md'])
        self.assertIn('Changement : honour a view.', text)
        self.assertIn('src/a/b (2 fichiers)', text)

    def test_without_a_token_it_says_so(self):
        with mock.patch.dict(os.environ, {'ATLASSIAN_API_TOKEN': ''}):
            with self.assertRaises(jira.JiraError):
                jira.request('https://x', 'GET', '/field')


class TicketActionsTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        set_registry([entry(1, branch='PROJ-9-x', title='fix: y [PROJ-9]')])

    def test_the_french_proposal_comes_apart_from_the_dialog_data(self):
        info = {'key': 'PROJ-9', 'summary': 's', 'status': 'En revue', 'impacts': '', 'impactsField': 'f', 'transitions': []}
        with mock.patch.object(jira, 'info', return_value=info), mock.patch.object(pwtickets, 'changed_files', return_value=['a/b.ts']), \
                mock.patch.object(pwtickets.pwclaude, 'ask') as ask:
            result = actions.perform({'action': 'ticket-info', 'repo': 'fake-repo', 'pr': 1})
        ask.assert_not_called()  # the dialog must not wait for Claude
        self.assertIn('Zones modifiées', result['suggestion'])
        with mock.patch.object(pwtickets, 'changed_files', return_value=['a/b.ts']), \
                mock.patch.object(pwtickets.pwclaude, 'ask', return_value='Le filtre est corrigé.') as ask:
            result = actions.perform({'action': 'ticket-draft', 'repo': 'fake-repo', 'pr': 1})
        self.assertEqual(result['suggestion'], 'Le filtre est corrigé.')
        self.assertIn('en français', ask.call_args.args[1])
        self.assertIn('a/b.ts', ask.call_args.args[1])
        with mock.patch.object(pwtickets, 'changed_files', return_value=[]), mock.patch.object(pwtickets.pwclaude, 'ask', return_value=None):
            self.assertFalse(actions.perform({'action': 'ticket-draft', 'repo': 'fake-repo', 'pr': 1})['ok'])

    def test_suspension_blocks_the_french_impacts_draft_before_calling_claude(self):
        suspension = {'kind': 'tokens', 'reason': 'quota exhausted'}
        with mock.patch.object(pwtickets.pwstore, 'suspension', return_value=suspension), \
                mock.patch.object(pwtickets, 'changed_files') as changed_files, \
                mock.patch.object(pwtickets.pwclaude, 'ask') as ask:
            result = actions.perform({'action': 'ticket-draft', 'repo': 'fake-repo', 'pr': 1})

        self.assertFalse(result['ok'])
        self.assertTrue(result['suspended'])
        self.assertIn('Claude est suspendu', result['message'])
        changed_files.assert_not_called()
        ask.assert_not_called()

    def test_suspension_during_file_lookup_blocks_the_impacts_draft(self):
        suspension = {'kind': 'tokens', 'reason': 'quota exhausted'}
        with mock.patch.object(pwtickets.pwstore, 'suspension', side_effect=[None, suspension, suspension]), \
                mock.patch.object(pwtickets, 'changed_files', return_value=['a/b.ts']), \
                mock.patch.object(pwtickets.pwclaude, 'ask') as ask:
            result = actions.perform({'action': 'ticket-draft', 'repo': 'fake-repo', 'pr': 1})

        self.assertFalse(result['ok'])
        self.assertTrue(result['suspended'])
        ask.assert_not_called()

    def test_ticket_info_preselects_the_test_state_once_merged(self):
        info = {'key': 'PROJ-9', 'summary': 's', 'status': 'En revue', 'type': 'Story', 'impacts': '', 'impactsField': 'f',
                'transitions': [{'id': '151', 'name': 'Done', 'to': 'Terminé(e)'}, {'id': '171', 'name': 'To test', 'to': 'A tester'}]}
        info['transitions'].insert(0, {'id': '41', 'name': 'Retour de Qualification', 'to': 'RETOUR DE TEST'})
        with mock.patch.object(jira, 'info', return_value=info), mock.patch.object(pwtickets, 'changed_files', return_value=['a/b.ts']):
            result = actions.perform({'action': 'ticket-info', 'repo': 'fake-repo', 'pr': 1})
        self.assertTrue(result['ok'])
        self.assertIsNone(result['preselected'])  # not merged: the user chooses
        set_registry([entry(1, branch='PROJ-9-x', title='fix: y [PROJ-9]', state='done', doneReason='MERGED')])
        reset_config({'ticketTransitions': {'Story': 'A tester'}})
        with mock.patch.object(jira, 'info', return_value=info), mock.patch.object(pwtickets, 'changed_files', return_value=['a/b.ts']):
            result = actions.perform({'action': 'ticket-info', 'repo': 'fake-repo', 'pr': 1})
        self.assertEqual(result['preselected'], '171')  # "A tester", not "RETOUR DE TEST"
        self.assertIn('a (1 fichier)', result['suggestion'])
        reset_config()

    TRANSITIONS = [{'id': '1', 'name': 'Retour', 'to': 'RETOUR DE TEST'}, {'id': '2', 'name': 'Livrer', 'to': 'Await deployment'},
                   {'id': '3', 'name': 'Fermer', 'to': 'Closed'}, {'id': '4', 'name': 'Terminer', 'to': 'Terminé(e)'}]

    def test_the_state_wanted_depends_on_the_type_of_ticket(self):
        mapping = {'Story': 'await deployment', 'Tâche': 'close'}
        self.assertEqual(jira.wanted_transition(mapping, 'Story', self.TRANSITIONS), '2')
        self.assertEqual(jira.wanted_transition(mapping, 'Tâche', self.TRANSITIONS), '3')  # "close" finds "Closed"
        self.assertEqual(jira.wanted_transition(mapping, 'tache', self.TRANSITIONS), '3')  # case and accents do not matter
        self.assertEqual(jira.wanted_transition({'Bug': 'terminé'}, 'Bug', self.TRANSITIONS), '4')

    def test_a_type_that_is_not_listed_has_nothing_preselected(self):
        self.assertIsNone(jira.wanted_transition({'Tâche': 'close'}, 'Bug', self.TRANSITIONS))  # each type has a workflow of its own
        self.assertIsNone(jira.wanted_transition({'Tâche': 'close'}, '', self.TRANSITIONS))
        self.assertIsNone(jira.wanted_transition({'default': 'close'}, 'Bug', self.TRANSITIONS))  # no default state
        self.assertIsNone(jira.wanted_transition({'Bug': 'Inconnu'}, 'Bug', self.TRANSITIONS))
        self.assertIsNone(jira.wanted_transition({}, 'Bug', self.TRANSITIONS))
        self.assertIsNone(jira.wanted_transition({'Bug': 'x'}, 'Bug', []))

    def test_the_state_is_matched_on_the_name_of_the_transition_too(self):
        self.assertEqual(jira.wanted_transition({'Bug': 'livrer'}, 'Bug', self.TRANSITIONS), '2')
        self.assertEqual(jira.wanted_transition({'Bug': 'await deployment'}, 'Bug', self.TRANSITIONS), '2')  # exactly, before a part of another

    def test_the_mapping_is_validated_when_saved(self):
        from prwatch.actions import options as pwoptions
        self.assertTrue(pwoptions.set_options({'ticketTransitions': {' Story ': ' Await deployment ', 'Bug': 'Close'}})['ok'])
        self.assertEqual(pwconfig.config()['ticketTransitions'], {'Story': 'Await deployment', 'Bug': 'Close'})
        for bad in ('x', {'Bug': ''}, {'': 'Close'}, {'Bug': 3}, {'default': 'Close'}, {f't{i}': 'x' for i in range(41)}):
            self.assertFalse(pwoptions.set_options({'ticketTransitions': bad})['ok'], bad)
        reset_config()
        self.assertNotIn('default', pwconfig.config()['ticketTransitions'])  # no default state: each type has its workflow
        self.assertEqual(pwconfig.config()['ticketTransitions']['Tâche'], 'Terminé(e)')

    def test_the_transition_needs_confirmation_and_a_numeric_id(self):
        self.assertEqual(actions.perform({'action': 'ticket-transition', 'repo': 'fake-repo', 'pr': 1, 'transition': '171'})['message'], 'Action non confirmée.')
        self.assertFalse(actions.perform({'action': 'ticket-transition', 'repo': 'fake-repo', 'pr': 1, 'transition': '1; drop', 'confirmed': True})['ok'])
        with mock.patch.object(jira, 'move', return_value='Ticket PROJ-9 : état changé.') as move:
            result = actions.perform({'action': 'ticket-transition', 'repo': 'fake-repo', 'pr': 1, 'transition': '171', 'impacts': 'x', 'confirmed': True})
        self.assertTrue(result['ok'])
        self.assertEqual(move.call_args.args[2:], ('171', 'x'))

    def test_the_ticket_is_a_quick_action_once_the_pr_is_merged(self):
        st = {'kind': 'done', 'unpushed': ''}
        self.assertEqual(pwstatus.auto_quick_actions({'state': 'done', 'doneReason': 'MERGED', 'branch': 'PROJ-9-x'}, st)[0], 'ticket')
        self.assertNotIn('ticket', pwstatus.auto_quick_actions({'state': 'watching', 'branch': 'main', 'title': 'x'}, {'kind': 'watching', 'unpushed': ''}))


class PluginsTest(unittest.TestCase):
    LATEST = {'jetbrains': {'version': '0.9.10', 'url': 'https://github.com/pleday/claudechat/releases/download/v0.9.10/p.zip'},
              'vscode': {'version': '0.9.9', 'url': 'https://github.com/pleday/claudechat/releases/download/vscode-v0.9.9/p.vsix'}}

    def test_versions_compare_as_numbers(self):
        self.assertLess(plugins.version_key('0.9.8'), plugins.version_key('0.9.10'))

    def test_only_an_older_plugin_is_outdated(self):
        with mock.patch.object(plugins, 'installed', return_value={'jetbrains': [('/a', '0.9.8')], 'vscode': [('/b', '0.9.9')]}), mock.patch.object(plugins, 'latest', return_value=self.LATEST):
            lines = {p['id']: p for p in plugins.status()}
        self.assertTrue(lines['jetbrains']['outdated'])
        self.assertFalse(lines['vscode']['outdated'])

    def test_unknown_latest_is_not_an_update(self):
        with mock.patch.object(plugins, 'installed', return_value={'jetbrains': [('/a', '0.9.8')], 'vscode': []}), mock.patch.object(plugins, 'latest', return_value={}):
            self.assertFalse(plugins.status()[0]['outdated'])

    def test_a_zip_leaving_the_plugin_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as work:
            bad = os.path.join(work, 'bad.zip')
            with zipfile.ZipFile(bad, 'w') as bundle:
                bundle.writestr('claude-chat/../../evil', 'x')
            with self.assertRaises(ValueError):
                plugins._unpack_jetbrains(bad, os.path.join(work, 'out'))

    def test_a_download_from_elsewhere_is_refused(self):
        with self.assertRaises(ValueError):
            plugins._download('https://evil.example/p.zip', '/tmp')

    def test_the_jetbrains_plugin_folder_is_replaced_and_the_old_one_removed(self):
        with tempfile.TemporaryDirectory() as work:
            plugin = os.path.join(work, 'IntelliJ', 'claude-chat')
            os.makedirs(os.path.join(plugin, 'lib'))
            open(os.path.join(plugin, 'lib', 'claude-chat-0.9.8.jar'), 'w').close()
            archive = os.path.join(work, 'new.zip')
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('claude-chat/lib/claude-chat-0.9.10.jar', 'x')
            with mock.patch.object(plugins, 'latest', return_value=self.LATEST), mock.patch.object(plugins, 'installed', return_value={'jetbrains': [(plugin, '0.9.8')]}), \
                    mock.patch.object(plugins, '_download', return_value=archive):
                ok, message = plugins.update('jetbrains')
            self.assertTrue(ok, message)
            self.assertEqual(os.listdir(os.path.join(plugin, 'lib')), ['claude-chat-0.9.10.jar'])
            self.assertFalse(os.path.exists(plugin + '.old'))

    def test_the_vscode_extension_goes_through_the_code_command(self):
        with mock.patch.object(plugins, 'latest', return_value=self.LATEST), mock.patch.object(plugins, 'installed', return_value={'vscode': [('/b', '0.9.4')]}), \
                mock.patch.object(plugins, '_download', return_value='/tmp/x.vsix'), mock.patch.object(pweditors, 'install_vscode_extension', return_value=(True, 'ok')) as install:
            ok, _ = plugins.update('vscode')
        self.assertTrue(ok)
        install.assert_called_once_with('/tmp/x.vsix')


if __name__ == '__main__':
    unittest.main()


class JiraMetadataTest(unittest.TestCase):
    PROJECT = [{'name': 'Tâche', 'statuses': [{'name': 'A faire'}, {'name': 'Close'}]},
               {'name': 'Story', 'statuses': [{'name': 'A faire'}, {'name': 'Await deployment'}]}, {'name': '', 'statuses': [{'name': 'x'}]}]

    def read(self, answers, projects):
        calls = []

        def request(site, method, path, body=None):
            calls.append(path)
            return answers[path]
        with mock.patch.object(jira, 'request', side_effect=request):
            return jira.metadata('https://x', projects), calls

    def test_the_types_come_with_the_states_of_their_own_workflow(self):
        data, calls = self.read({'/project/RP/statuses': self.PROJECT}, ['RP'])
        self.assertEqual(data['types'], ['Story', 'Tâche'])
        self.assertEqual(data['statuses'], {'Tâche': ['A faire', 'Close'], 'Story': ['A faire', 'Await deployment']})
        self.assertEqual(data['allStatuses'], ['A faire', 'Await deployment', 'Close'])
        self.assertEqual(calls, ['/project/RP/statuses'])

    def test_without_a_watched_ticket_every_project_of_the_site_is_read(self):
        data, calls = self.read({'/project': [{'key': 'RP'}, {'key': 'KP'}, {}], '/project/RP/statuses': self.PROJECT,
                                 '/project/KP/statuses': [{'name': 'Tâche', 'statuses': [{'name': 'Fait'}]}, {'name': 'Bug', 'statuses': [{'name': 'Corrigé'}]}]}, [])
        self.assertEqual(data['types'], ['Bug', 'Story', 'Tâche'])
        self.assertEqual(data['statuses']['Tâche'], ['A faire', 'Close', 'Fait'])  # the workflows of a type in several projects add up
        self.assertEqual(data['statuses']['Bug'], ['Corrigé'])
        self.assertEqual(sorted(calls), ['/project', '/project/KP/statuses', '/project/RP/statuses'])

    def test_a_project_that_cannot_be_read_is_skipped(self):
        def request(site, method, path, body=None):
            if path == '/project':
                return [{'key': 'RP'}, {'key': 'XX'}]
            if path == '/project/XX/statuses':
                raise jira.JiraError('refusé')
            return self.PROJECT
        with mock.patch.object(jira, 'request', side_effect=request):
            data = jira.metadata('https://x', [])
        self.assertEqual(data['types'], ['Story', 'Tâche'])

    def test_without_any_workflow_the_types_and_states_of_the_site_are_listed(self):
        data, _ = self.read({'/project': [], '/issuetype': [{'name': 'Bug'}, {'name': 'Tâche'}, {}], '/status': [{'name': 'Close'}, {'name': 'A faire'}]}, [])
        self.assertEqual(data, {'types': ['Bug', 'Tâche'], 'statuses': {}, 'allStatuses': ['A faire', 'Close']})

    def test_the_action_reads_the_projects_of_the_watched_tickets_and_keeps_the_answer(self):
        from prwatch.actions import tickets as tickets
        set_registry([entry(1, branch='feature/PROJ-1-a'), entry(2, branch='feature/KP-2-b'), entry(3, branch='x', title='rien')])
        tickets._METADATA[:] = [0.0, None]
        with mock.patch.object(jira, 'metadata', return_value={'types': ['Bug'], 'statuses': {}, 'allStatuses': []}) as read:
            first = actions.perform({'action': 'jira-metadata'})
            second = actions.perform({'action': 'jira-metadata'})
        self.assertEqual(read.call_args.args[1], ['KP', 'PROJ'])
        self.assertEqual((first['ok'], first['types'], second['projects']), (True, ['Bug'], ['KP', 'PROJ']))
        self.assertEqual(read.call_count, 1)
        tickets._METADATA[:] = [0.0, None]
        with mock.patch.object(jira, 'metadata', side_effect=jira.JiraError('Jira injoignable')):
            self.assertFalse(actions.perform({'action': 'jira-metadata'})['ok'])
