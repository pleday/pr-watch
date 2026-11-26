import os
import subprocess
import json
import tempfile
import unittest
from unittest import mock

from prwatch import actions
from prwatch.core import permissions

from .helpers import TEAM, entry, git, make_repo_with_remote, registry, reset_config, set_registry
from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.engine import merging as pwmerging
from prwatch.engine import service as pwservice
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.actions import adding as pwadding
from prwatch.actions import forms as pwforms
from prwatch.actions import options as pwoptions
from prwatch.actions import pr as pwpr
from prwatch.actions import status as pwstatus
from prwatch.system import claude as pwclaude
from prwatch.system import notifications as pwnotifications
from prwatch.system import opening as pwopening
from prwatch.system import processes as pwprocesses


class RegistryActionsTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        set_registry([entry(1, needsAttention='à voir'), entry(2, state='gave-up', attempts=5), entry(3, state='done')])

    def test_overview(self):
        view = actions.overview()
        self.assertEqual([e['pr'] for e in view['entries']], [1, 2, 3])
        self.assertEqual(view['entries'][0]['effective']['autoPush'], True)
        self.assertEqual([c['id'] for c in view['categories']], list(pwconfig.CATEGORIES))
        self.assertEqual(view['options']['handling']['infra'], 'rerun')
        self.assertIn('running', view['daemon'])

    def test_the_texts_both_screens_show_come_from_the_overview(self):
        first, second, third = actions.overview()['entries']
        self.assertEqual({k: first['status'][k] for k in ('kind', 'label')}, {'kind': 'attention', 'label': 'Votre attention'})
        self.assertEqual({k: second['status'][k] for k in ('kind', 'label')}, {'kind': 'gave-up', 'label': 'Abandonnée (essais épuisés)'})
        self.assertEqual((first['status']['icon'], second['status']['icon']), ('⚠', '✖'))
        self.assertEqual(third['status']['label'], 'Terminée')
        self.assertIn('Pas encore prête', first['mergeForm']['intro'])
        self.assertEqual([s['id'] for s in first['mergeForm']['strategies']], list(pwforms.MERGE_STRATEGY_IDS))
        self.assertTrue(first['confirmations']['force-push']['destructive'])
        self.assertIn('origin/', first['confirmations']['force-push']['body'])
        self.assertEqual(third['confirmations']['unwatch']['label'], 'Retirer')
        self.assertEqual(first['confirmations']['unwatch']['label'], 'Arrêter')

    def test_a_ready_pull_request_is_announced_as_such(self):
        set_registry([entry(1, ready=True)])
        item = actions.overview()['entries'][0]
        self.assertEqual(item['status']['kind'], 'ready')
        self.assertEqual(item['mergeForm']['intro'], 'Approuvée et pipelines au vert.')

    def test_the_settings_form_and_its_answer(self):
        set_registry([entry(1, settings={'autoPush': False, 'handling': {'sonar': 'ignore'}})])
        form = actions.overview()['entries'][0]['settingsForm']
        self.assertEqual(form['autoPush']['value'], 'no')
        self.assertEqual(form['autoPush']['choices'][0]['label'], 'Par défaut (oui)')
        rows = {row['id']: row for row in form['categories']}
        self.assertEqual(rows['sonar']['value'], 'ignore')
        self.assertEqual(rows['comment']['value'], '')
        self.assertTrue(rows['comment']['choices'][0]['label'].startswith('Par défaut ('))
        self.assertEqual(pwforms.settings_from_form('yes', {'sonar': '', 'comment': 'notify'}),
                         {'autoPush': True, 'autoMerge': None, 'handling': {'sonar': None, 'comment': 'notify'}})
        self.assertEqual(pwforms.settings_from_form('yes', {}, 'ready')['autoMerge'], 'ready')
        self.assertEqual(form['autoMerge']['value'], '')
        self.assertTrue(form['autoMerge']['choices'][0]['label'].startswith('Par défaut (désactivé'))
        self.assertEqual(pwforms.settings_from_form('default', {})['autoPush'], None)

    def test_the_settings_form_answer_is_applied(self):
        set_registry([entry(1)])
        result = actions.perform({'action': 'pr-settings-form', 'repo': 'fake-repo', 'pr': 1, 'push': 'no', 'handling': {'comment': 'notify', 'sonar': ''}})
        self.assertTrue(result['ok'], result)
        self.assertEqual(registry()[0]['settings'], {'autoPush': False, 'handling': {'comment': 'notify'}})
        bad = actions.perform({'action': 'pr-settings-form', 'repo': 'fake-repo', 'pr': 1, 'push': 'no', 'handling': {'comment': 3}})
        self.assertFalse(bad['ok'])

    def test_session_activity_wording(self):
        self.assertEqual(pwstatus.session_activity(None, False), 'session introuvable')
        self.assertEqual(pwstatus.session_activity(10, True), 'en cours d’utilisation')
        self.assertEqual(pwstatus.session_activity(600, False), 'inactive depuis 10 min')
        self.assertEqual(pwstatus.session_activity(7300, False), 'inactive depuis 2 h')

    def test_pause_and_resume(self):
        self.assertTrue(pwpr.pause('fake-repo', 1, True)['ok'])
        self.assertEqual(registry()[0]['state'], 'paused')
        self.assertTrue(pwpr.pause('fake-repo', 1, False)['ok'])
        self.assertEqual(registry()[0]['state'], 'watching')

    def test_acknowledge_reset_wake(self):
        pwpr.acknowledge('fake-repo', 1)
        self.assertEqual(registry()[0]['needsAttention'], '')
        pwpr.reset('fake-repo', 2)
        self.assertEqual((registry()[1]['attempts'], registry()[1]['state']), (0, 'watching'))
        pwpr.wake('fake-repo', 1)
        self.assertTrue(registry()[0]['forceWake'])

    def test_a_notification_marks_the_pull_request_until_it_is_acknowledged(self):
        with mock.patch.object(pwnotifications, 'notify'):
            pwwake.notify('✔ Traitée par Claude', 'fake-repo #1', pwconfig.config(), entry=registry()[0])
        self.assertEqual(registry()[0]['activity']['text'], '✔ Traitée par Claude')
        self.assertNotIn('activity', registry()[1])
        with mock.patch.object(pwnotifications, 'notify'):
            pwwake.notify('✔ Traitée par Claude', 'fake-repo #1', pwconfig.config(), entry=registry()[0])
        self.assertEqual(registry()[0]['activity']['count'], 2)
        with mock.patch.object(pwnotifications, 'notify'):
            pwwake.notify('✔ Mergée', 'fake-repo #1\nStratégie : squash.', pwconfig.config(), entry=registry()[0])
        items = registry()[0]['activity']['items']
        self.assertEqual([i['text'] for i in items], ['✔ Traitée par Claude', '✔ Traitée par Claude', '✔ Mergée'])
        self.assertEqual(items[-1]['detail'], 'Stratégie : squash.')
        pwpr.acknowledge('fake-repo', 1)  # one click settles every action of the pull request
        self.assertIsNone(registry()[0]['activity'])

    def test_acting_on_a_pull_request_clears_its_action_done_chip(self):
        with mock.patch.object(pwnotifications, 'notify'):
            pwwake.notify('✔ Traitée par Claude', 'fake-repo #1', pwconfig.config(), entry=registry()[0])
        actions.perform({'action': 'ticket-info', 'repo': 'fake-repo', 'pr': 1})  # reading does not settle anything
        self.assertTrue(registry()[0]['activity'])
        self.assertTrue(actions.perform({'action': 'read-reports', 'repo': 'fake-repo', 'pr': 1})['ok'])
        self.assertIsNone(registry()[0]['activity'])

    def test_a_browser_tab_is_remembered_until_it_is_closed_or_silent(self):
        self.assertFalse(actions.tab_is_open())
        pwpr.tab_seen('abc')
        self.assertTrue(actions.tab_is_open())
        pwpr.tab_closed('other')  # another tab leaving does not forget this one
        self.assertTrue(actions.tab_is_open())
        self.assertTrue(actions.perform({'action': 'tab-closed', 'tab': 'abc'})['ok'])
        self.assertFalse(actions.tab_is_open())
        pwpr.tab_seen('abc')
        with mock.patch('time.time', return_value=__import__('time').time() + pwpr.TAB_FRESH_SECONDS + 1):
            self.assertFalse(actions.tab_is_open())
        pwpr.tab_closed('abc')

    def test_a_tab_that_does_not_answer_is_not_believed(self):
        pwpr.tab_seen('abc')
        stale = json.loads((pwstore.DIR / pwpr.TAB_FILE).read_text())
        stale['at'] -= 60  # last heard of a minute ago: closed, or hidden for long
        (pwstore.DIR / pwpr.TAB_FILE).write_text(json.dumps(stale))
        with mock.patch('time.sleep') as sleeping:
            self.assertFalse(actions.tab_is_open())  # nobody answers during the wait
            self.assertTrue(sleeping.called)
            sleeping.side_effect = lambda _: pwpr.tab_seen('abc')
            (pwstore.DIR / pwpr.TAB_FILE).write_text(json.dumps(stale))
            self.assertTrue(actions.tab_is_open())  # the page polled again: it is there
        pwpr.tab_closed('abc')

    def test_the_dashboard_opens_in_the_browser_only_when_no_tab_is_open(self):
        with mock.patch.object(pwservice, 'ensure_web', return_value='http://x/'), mock.patch.object(pwservice, 'ensure'), \
                mock.patch.object(pwconfig, 'config', return_value={**pwconfig.config(), 'dashboardInBrowser': True}), \
                mock.patch.object(pwservice.pwnotifications, 'open_url') as browser, mock.patch.object(pwservice.pwwindow, 'open_app_window') as window:
            pwservice.open_dashboard()
            browser.assert_called_once_with('http://x/')
            pwpr.tab_seen('abc')
            pwservice.open_dashboard()
            browser.assert_called_once()  # the tab picks the request up by itself
            window.assert_not_called()

    def test_merge_is_pointless_when_bitbucket_would_refuse_it(self):
        for blockers in (['approbations 0/1'], ['pipeline #3 en échec'], ['pipeline #3 en cours'], ['1 tâche(s) ouverte(s)', 'approbations 1/2']):
            self.assertTrue(pwmerging.merge_is_pointless(blockers), blockers)
        for blockers in ([], None, ['1 tâche(s) ouverte(s)'], ['modifications demandées'], ['aucun pipeline sur le dernier commit']):
            self.assertFalse(pwmerging.merge_is_pointless(blockers), blockers)

    def test_the_ticket_can_be_opened_and_its_state_changed_separately(self):
        set_registry([entry(1, branch='feature/PROJ-100-x'), entry(2, branch='feature/sans-ticket', title='rien')])
        first, second = actions.overview()['entries']
        self.assertTrue(first['ticketUrl'].endswith('/browse/PROJ-100'))
        self.assertIsNone(second['ticketUrl'])
        self.assertEqual([k for k, _ in pwstatus.QUICK_ACTIONS if k.startswith('ticket')], ['ticketOpen', 'ticket'])
        self.assertGreater(pwstatus.action_weights(first, first['status'])['ticketOpen'], 0)
        self.assertEqual(pwstatus.action_weights(second, second['status'])['ticketOpen'], 0)

    def test_unwatch(self):
        self.assertTrue(pwpr.unwatch('fake-repo', 3)['ok'])
        self.assertEqual([e['pr'] for e in registry()], [1, 2])
        self.assertFalse(pwpr.unwatch('fake-repo', 3)['ok'])

    def test_an_unknown_pull_request(self):
        for call in (pwpr.pause, pwpr.reset, pwpr.acknowledge, pwpr.wake):
            self.assertFalse(call('nope', 1)['ok'])


class SettingsActionsTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        set_registry([entry(1)])

    def test_pr_settings_are_merged_and_none_goes_back_to_the_default(self):
        result = pwpr.pr_settings('fake-repo', 1, {'autoPush': False, 'handling': {'comment': 'notify', 'sonar': 'ignore'}})
        self.assertTrue(result['ok'])
        self.assertEqual(result['effective']['handling']['comment'], 'notify')
        pwpr.pr_settings('fake-repo', 1, {'handling': {'sonar': None}})
        stored = registry()[0]['settings']
        self.assertEqual(stored, {'autoPush': False, 'handling': {'comment': 'notify'}})
        pwpr.pr_settings('fake-repo', 1, {'autoPush': None})
        self.assertIsNone(registry()[0]['settings']['autoPush'])

    def test_invalid_pr_settings_change_nothing(self):
        before = registry()
        for bad in ({'handling': {'comment': 'rerun'}}, {'handling': {'nope': 'fix'}}, {'autoPush': 'yes'}, {'other': 1}):
            with self.subTest(bad=bad):
                self.assertFalse(pwpr.pr_settings('fake-repo', 1, bad)['ok'])
        self.assertEqual(registry(), before)

    def test_options(self):
        self.assertTrue(pwoptions.set_options({'pollSeconds': 120, 'autoPush': False, 'handling': {'comment': 'notify'}})['ok'])
        config = pwconfig.config()
        self.assertEqual((config['pollSeconds'], config['autoPush'], config['handling']['comment'], config['handling']['sonar']), (120, False, 'notify', 'fix'))

    def test_the_team_options_are_saved_and_checked(self):
        good = {'bitbucketWorkspace': ' acme ', 'jiraSite': 'https://acme.atlassian.net/', 'jiraImpactsField': '', 'sonarPattern': 'sonar|quality', 'cloneFolders': ['~/code', ' ~/src ']}
        self.assertTrue(pwoptions.set_options(good)['ok'])
        config = pwconfig.config()
        self.assertEqual((config['bitbucketWorkspace'], config['jiraSite'], config['jiraImpactsField'], config['sonarPattern'], config['cloneFolders']),
                         ('acme', 'https://acme.atlassian.net', '', 'sonar|quality', ['~/code', '~/src']))
        self.assertTrue(pwoptions.set_options({'jiraSite': ''})['ok'])
        for bad in ({'bitbucketWorkspace': ''}, {'bitbucketWorkspace': 'a/b'}, {'jiraSite': 'http://x.net'}, {'sonarPattern': '('},
                    {'cloneFolders': 'x'}, {'cloneFolders': ['']}, {'jiraImpactsField': 3}):
            with self.subTest(bad=bad):
                self.assertFalse(pwoptions.set_options(bad)['ok'])
        self.assertEqual(pwconfig.config()['bitbucketWorkspace'], 'acme')

    def test_a_profile_loads_the_team_settings_and_nothing_else(self):
        import json
        import os
        import pathlib
        pwstore.CONFIG.write_text(json.dumps({'pollSeconds': 120}), encoding='utf-8')
        first = pwconfig.config()
        self.assertEqual((first['ticketTransitions'], first['followBotComments'], first['reviewStepPattern']), ({}, [], ''))
        self.assertNotIn('Invalid authentication', first['infraPatterns'])
        shipped = sorted(pathlib.Path(__file__).resolve().parent.parent.glob('profiles/*.json'))
        self.assertTrue(shipped, 'the clone ships its team profiles in profiles/')
        for path in shipped:  # every shipped profile loads, whatever the team
            with self.subTest(profile=path.name):
                pwstore.CONFIG.write_text(json.dumps({'pollSeconds': 120}), encoding='utf-8')
                done = pwoptions.load_profile(str(path))
                self.assertTrue(done['ok'], done['message'])
                config = pwconfig.config()
                wanted = json.loads(path.read_text(encoding='utf-8'))['options']
                self.assertEqual({key: config[key] for key in wanted}, wanted)
                self.assertEqual(config['pollSeconds'], 120)
        pwstore.CONFIG.write_text(json.dumps({'pollSeconds': 120}), encoding='utf-8')
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'acme.json')
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'name': 'acme', 'options': {'bitbucketWorkspace': 'acme', 'jiraSite': ''}}, handle)
            self.assertTrue(pwoptions.load_profile(path)['ok'])
        self.assertEqual((pwconfig.config()['bitbucketWorkspace'], pwconfig.config()['pollSeconds']), ('acme', 120))
        for bad in ({'options': {'claudeArgs': ['--dangerously-skip-permissions']}}, {'options': {'reviewStepPattern': '('}}, {'options': {}}, {'nope': 1},
                    {'options': {'followBotComments': [{'author': 'x', 'run': 'y'}]}}, {'options': {'handling': {'comment': 'notify'}}}, '/does/not/exist.json', 'x' * 5):
            with self.subTest(bad=bad):
                self.assertFalse(pwoptions.load_profile(bad)['ok'])
        self.assertEqual(pwconfig.config()['bitbucketWorkspace'], 'acme')
        self.assertNotIn('claudeArgs', json.loads(pwstore.CONFIG.read_text()))
        reset_config()

    def test_the_focus_remembers_where_it_comes_from_and_a_quit_can_be_requested(self):
        pwpr.set_focus('api#12')
        self.assertEqual(pwpr.focus()['source'], 'notification')
        pwpr.set_focus('daemon', 'tray')
        self.assertEqual((pwpr.focus()['key'], pwpr.focus()['source']), ('daemon', 'tray'))
        self.assertIsNone(pwpr.quit_request())
        pwpr.request_quit()
        self.assertGreater(pwpr.quit_request()['at'], 0)
        self.assertEqual(pwstatus.overview()['quit'], pwpr.quit_request())
        (pwstore.DIR / pwpr.QUIT_FILE).unlink()

    def test_changing_the_schedule_wakes_the_daemon(self):
        schedule = {'enabled': True, 'days': [0], 'start': '09:00', 'end': '18:00'}
        with mock.patch.object(pwservice, 'request_cycle') as request:
            self.assertTrue(pwoptions.set_options({'schedule': schedule})['ok'])
        request.assert_called_once_with()

    def test_changing_the_schedule_keeps_a_manual_pause_until_the_new_next_opening(self):
        import time

        schedule = {'enabled': True, 'days': list(range(7)), 'start': '00:00', 'end': '23:59'}
        pwstore.set_schedule_override('paused', time.time() + 3600)
        self.addCleanup(pwstore.clear_schedule_override)
        next_opening = time.time() + 7200
        with (mock.patch.object(pwconfig, 'next_schedule_boundary', return_value=next_opening) as boundary,
              mock.patch.object(pwservice, 'request_cycle')):
            result = pwoptions.set_options({'schedule': schedule})

        self.assertTrue(result['ok'])
        boundary.assert_called_once_with({**TEAM, 'schedule': schedule}, 'paused')
        self.assertEqual(pwstore.schedule_override(), {'mode': 'paused', 'until': next_opening})
        self.assertFalse(pwconfig.schedule_effective_state(pwconfig.config())[0])

    def test_force_schedule_starts_or_wakes_the_daemon_until_closing_time(self):
        pwoptions.set_options({'schedule': {'enabled': True, 'days': list(range(7)), 'start': '09:00', 'end': '18:00'}})
        self.addCleanup(pwstore.clear_schedule_override)
        with (mock.patch.object(pwservice, 'is_running', return_value=True),
              mock.patch.object(pwservice, 'request_cycle') as request,
              mock.patch.object(pwoptions.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='daemon already running', stderr='')) as run):
            result = pwoptions.daemon_command('force-schedule')
        self.assertTrue(result['ok'])
        self.assertEqual(pwstore.schedule_override()['mode'], 'active')
        request.assert_called_once_with()
        self.assertEqual(run.call_args.args[0][-1], 'ensure')

    def test_invalid_options_are_refused(self):
        for bad in ({'claudeBinary': '/bin/sh'}, {'pollSeconds': 1}, {'pollSeconds': True}, {'autoPush': 'x'}, {'handling': {'comment': 'nope'}}):
            with self.subTest(bad=bad):
                self.assertFalse(pwoptions.set_options(bad)['ok'])
        self.assertEqual(json.loads(pwstore.CONFIG.read_text()).get('pollSeconds'), None)


class PerformTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        set_registry([entry(1)])

    def test_destructive_actions_need_the_confirmation(self):
        for name in ('unwatch', 'merge', 'push', 'force-push'):
            with self.subTest(name=name):
                self.assertEqual(actions.perform({'action': name, 'repo': 'fake-repo', 'pr': 1})['message'], 'Action non confirmée.')
        self.assertEqual(len(registry()), 1)

    def test_bad_requests(self):
        self.assertEqual(actions.perform({'action': 'nope'})['message'], 'Action inconnue.')
        self.assertIn('Champ manquant', actions.perform({'action': 'wake', 'repo': 'fake-repo'})['message'])
        self.assertFalse(actions.perform({'action': 'wake', 'repo': 'fake-repo', 'pr': '1'})['ok'])
        self.assertFalse(actions.perform({'action': 'options', 'values': 'x'})['ok'])

    def test_an_action_asks_the_daemon_for_an_immediate_cycle(self):
        pwstore.DIR.joinpath('poke').unlink(missing_ok=True)
        self.assertTrue(actions.perform({'action': 'wake', 'repo': 'fake-repo', 'pr': 1})['ok'])
        self.assertTrue(pwstore.DIR.joinpath('poke').exists())
        pwservice.sleep_until_next_cycle(30)  # returns at once, and consumes the request
        self.assertFalse(pwstore.DIR.joinpath('poke').exists())

    def test_quick_actions_follow_the_state_unless_forced(self):
        self.assertIsNone(pwconfig.config()['quickActions'])
        idle = {'state': 'watching'}
        st = lambda kind='watching', unpushed='': {'kind': kind, 'unpushed': unpushed}  # noqa: E731
        self.assertEqual(pwstatus.quick_actions(idle, st(), {}), ['open', 'wake', 'pause'])
        self.assertEqual(pwstatus.quick_actions({'state': 'watching', 'ready': True}, st('ready'), {}), ['merge', 'open', 'wake'])
        self.assertEqual(pwstatus.quick_actions(idle, st(unpushed='2 commits'), {}), ['push', 'open', 'wake'])
        self.assertEqual(pwstatus.quick_actions({'state': 'watching', 'needsAttention': 'x', 'lastReport': 'r'}, st('attention'), {}), ['ack', 'open', 'report'])
        self.assertEqual(pwstatus.quick_actions({'state': 'watching'}, st('gave-up'), {}), ['reset', 'open', 'wake'])
        self.assertEqual(pwstatus.quick_actions({'state': 'paused'}, st('paused'), {}), ['pause', 'open', 'wake'])
        self.assertEqual(pwstatus.quick_actions({'state': 'done', 'lastReport': 'r'}, st('done'), {}), ['report', 'unwatch', 'open'])
        # several things at once: the three that matter most win, the others stay in the menu
        busy = {'state': 'watching', 'ready': True, 'needsAttention': 'x'}
        self.assertEqual(pwstatus.quick_actions(busy, st('attention', '1 commit'), {}), ['merge', 'ack', 'push'])
        self.assertEqual(pwstatus.quick_actions(idle, st(), {'quickActions': ['merge']}), ['merge'])  # forced
        self.assertEqual(pwstatus.quick_actions(idle, st(), {'quickActions': []}), [])

    def test_quick_actions_option(self):
        self.assertTrue(actions.perform({'action': 'options', 'values': {'quickActions': ['merge', 'open']}})['ok'])
        self.assertEqual(pwconfig.config()['quickActions'], ['merge', 'open'])
        self.assertTrue(actions.perform({'action': 'options', 'values': {'quickActions': None}})['ok'])
        self.assertIsNone(pwconfig.config()['quickActions'])
        for bad in (['open', 'wake', 'push', 'merge'], ['open', 'open'], ['nope'], 'open'):
            with self.subTest(bad=bad):
                self.assertFalse(actions.perform({'action': 'options', 'values': {'quickActions': bad}})['ok'])
        reset_config()

    def test_reopening_an_inactive_session_recommends_where_it_was_opened(self):
        set_registry([entry(1, origin={'host': 'vscode', 'entrypoint': 'claude-vscode'})])
        apps = [{'id': 'desktop', 'label': 'D'}, {'id': 'vscode-code', 'label': 'V'}, {'id': 'terminal', 'label': 'T'}]
        with mock.patch.object(pwprocesses, 'find_session_process', return_value=None), mock.patch.object(pwopening, 'available_apps', return_value=apps), \
                mock.patch.object(pwopening, 'apps_hints', return_value=[]), mock.patch.object(pwclaude, 'session_title', return_value=''):
            result = actions.perform({'action': 'open-session', 'repo': 'fake-repo', 'pr': 1})
        self.assertEqual(result['recommended'], 'vscode-code')
        self.assertEqual([a['id'] for a in result['apps']], ['vscode-code', 'desktop', 'terminal'])
        self.assertEqual(result['originLabel'], 'VS Code')
        with mock.patch.object(pwprocesses, 'find_session_process', return_value=None), mock.patch.object(pwopening, 'available_apps', return_value=[{'id': 'desktop', 'label': 'D'}, {'id': 'terminal', 'label': 'T'}]), \
                mock.patch.object(pwopening, 'apps_hints', return_value=[]), mock.patch.object(pwclaude, 'session_title', return_value=''):
            result = actions.perform({'action': 'open-session', 'repo': 'fake-repo', 'pr': 1})
        self.assertIsNone(result['recommended'])
        self.assertIn('ouverte depuis VS Code', result['appHints'][0])

    def test_a_pull_request_reference_is_read_in_every_form(self):
        self.assertEqual(pwadding.parse_pr_reference('https://bitbucket.org/acme/my-repo/pull-requests/12/diff'), ('my-repo', 12))
        self.assertEqual(pwadding.parse_pr_reference('my-repo#12'), ('my-repo', 12))
        self.assertEqual(pwadding.parse_pr_reference('my-repo 12'), ('my-repo', 12))
        self.assertIsNone(pwadding.parse_pr_reference('nothing'))

    def test_adding_a_pull_request_by_hand(self):
        import os
        with tempfile.TemporaryDirectory() as home, mock.patch.object(os.path, 'expanduser', side_effect=lambda p: p.replace('~', home)):
            work = os.path.join(home, 'ws', 'repo')
            os.makedirs(work)
            session = '12345678-1234-1234-1234-123456789abc'
            project = pwclaude.claude_project_dir(os.path.join(home, 'ws'))
            os.makedirs(project)
            open(os.path.join(project, f'{session}.jsonl'), 'w').close()
            set_registry([])
            pr = {'state': 'OPEN', 'title': 'T [PROJ-1]', 'source': {'branch': {'name': 'PROJ-1-x'}}}
            with mock.patch.object(permissions, 'is_mine', return_value=True), mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(pwbitbucket, 'max_comment_id', return_value=7), mock.patch.object(pwbitbucket, 'max_pipeline', return_value=3), \
                    mock.patch.object(pwprocesses, 'find_session_process', return_value=None):
                bad = actions.perform({'action': 'add-pr', 'repo': 'repo', 'pr': 5, 'workDir': work, 'sessionId': '87654321-4321-4321-4321-123456789abc'})
                self.assertFalse(bad['ok'])
                good = actions.perform({'action': 'add-pr', 'repo': 'repo', 'pr': 5, 'workDir': work, 'sessionId': session})
            self.assertTrue(good['ok'], good['message'])
            added = registry()[0]
            self.assertEqual((added['branch'], added['workDir'], added['sessionCwd'], added['lastComment'], added['lastPipeline']), ('PROJ-1-x', work, os.path.join(home, 'ws'), 7, 3))
            with mock.patch.object(permissions, 'is_mine', return_value=True), mock.patch.object(pwbitbucket, 'api', return_value={**pr, 'state': 'MERGED'}):
                self.assertFalse(actions.perform({'action': 'add-pr', 'repo': 'repo', 'pr': 5, 'workDir': work, 'sessionId': session})['ok'])
        self.assertFalse(actions.perform({'action': 'add-pr', 'repo': '../x', 'pr': 5, 'workDir': '/tmp', 'sessionId': 'x'})['ok'])

    def test_adding_a_pull_request_with_a_new_session(self):
        import os
        with tempfile.TemporaryDirectory() as work:
            set_registry([])
            pr = {'state': 'OPEN', 'title': 'T [PROJ-1]', 'source': {'branch': {'name': 'PROJ-1-x'}}}
            with mock.patch.object(permissions, 'is_mine', return_value=True), mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(pwbitbucket, 'max_comment_id', return_value=0), mock.patch.object(pwbitbucket, 'max_pipeline', return_value=0), \
                    mock.patch.object(pwwake, 'create_session', return_value=('11111111-2222-3333-4444-555555555555', None)) as create:
                result = actions.perform({'action': 'add-pr', 'repo': 'repo', 'pr': 5, 'workDir': work, 'sessionId': 'new'})
            self.assertTrue(result['ok'], result['message'])
            self.assertEqual(create.call_args.args[:5], ('repo', 5, 'PROJ-1-x', 'T [PROJ-1]', work))
            added = registry()[0]
            self.assertEqual((added['sessionId'], added['sessionCwd']), ('11111111-2222-3333-4444-555555555555', work))
            with mock.patch.object(permissions, 'is_mine', return_value=True), mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(pwwake, 'create_session', return_value=(None, 'Claude a échoué')):
                failed = actions.perform({'action': 'add-pr', 'repo': 'repo2', 'pr': 6, 'workDir': work, 'sessionId': 'new'})
            self.assertFalse(failed['ok'])
            self.assertEqual(len(registry()), 1)
            with mock.patch.object(permissions, 'is_mine', return_value=True), mock.patch.object(pwbitbucket, 'api', return_value=pr), \
                    mock.patch.object(pwstore, 'suspension', return_value={'reason': 'x', 'kind': 'usage'}), \
                    mock.patch.object(pwwake, 'create_session') as create:
                blocked = actions.perform({'action': 'add-pr', 'repo': 'repo3', 'pr': 7, 'workDir': work, 'sessionId': 'new'})
            self.assertFalse(blocked['ok'])
            create.assert_not_called()

    def test_a_new_session_is_started_with_its_own_id_in_the_clone(self):
        import subprocess
        reset_config()
        done = subprocess.CompletedProcess([], 0, stdout='Prêt.', stderr='')
        with mock.patch.object(pwwake.subprocess, 'run', return_value=done) as run:
            session, failure = pwwake.create_session('repo', 5, 'b', 'T', '/work', pwconfig.config())
        self.assertIsNone(failure)
        command = run.call_args.args[0]
        self.assertEqual(command[1:3], ['--session-id', session])
        self.assertEqual(run.call_args.kwargs['cwd'], '/work')
        with mock.patch.object(pwwake.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, stdout='', stderr='boom')):
            self.assertEqual(pwwake.create_session('repo', 5, 'b', 'T', '/work', pwconfig.config())[0], None)

    def test_only_ones_own_pull_requests_can_be_added(self):
        pr = {'state': 'OPEN', 'title': 'T', 'author': {'display_name': 'Autre', 'account_id': 'other'}, 'source': {'branch': {'name': 'b'}}}
        with mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(permissions, 'identity', return_value={'account_id': 'me', 'uuid': None}):
            lookup = actions.perform({'action': 'pr-lookup', 'reference': 'repo#5'})
            added = actions.perform({'action': 'add-pr', 'repo': 'repo', 'pr': 5, 'workDir': '/tmp', 'sessionId': 'new'})
        for result in (lookup, added):
            self.assertFalse(result['ok'])
            self.assertIn('de Autre', result['message'])
        with mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(permissions, 'identity', return_value=None):
            self.assertIn('pas connu', actions.perform({'action': 'pr-lookup', 'reference': 'repo#5'})['message'])

    def test_a_function_whose_permission_was_declined_is_hidden_and_refused(self):
        permissions._save({'at': 'x', 'checks': [{'id': 'jira', 'label': 'Jira', 'required': False, 'why': '', 'features': ['ticket'], 'ok': False, 'detail': ''}], 'declined': ['jira']})
        self.assertEqual(permissions.hidden_features(), ['ticket'])
        self.assertIn('masquée', actions.perform({'action': 'ticket-info', 'repo': 'fake-repo', 'pr': 1})['message'])
        self.assertNotIn('ticket', pwstatus.auto_quick_actions({'state': 'done', 'doneReason': 'MERGED', 'branch': 'PROJ-9-x'}, {'kind': 'done', 'unpushed': ''}))
        permissions._save({'at': None, 'checks': [], 'declined': []})

    def test_only_web_addresses_are_opened_in_the_system_browser(self):
        with mock.patch.object(pwnotifications, 'open_url') as opened:
            self.assertTrue(actions.perform({'action': 'open-link', 'url': 'https://bitbucket.org/x'})['ok'])
            opened.assert_called_once_with('https://bitbucket.org/x')
            for bad in ('file:///etc/passwd', 'javascript:alert(1)', 'claude://code/new', 'https://', '', 5):
                self.assertFalse(actions.perform({'action': 'open-link', 'url': bad})['ok'])
            self.assertEqual(opened.call_count, 1)

    def test_actions_refuse_arguments_of_the_wrong_type_without_an_exception(self):
        for action, field in (('claude-sessions', 'workDir'), ('pr-lookup', 'reference'), ('plugins-update', 'kind')):
            for junk in (5, 1.5, True, [], {}, None):
                message = actions.perform({'action': action, 'confirmed': True, field: junk})['message']
                self.assertNotRegex(message, r'Error :', (action, junk))

    def test_a_refresh_asks_the_daemon_for_a_cycle_at_once(self):
        poke = pwstore.DIR / 'poke'
        poke.unlink(missing_ok=True)
        with mock.patch.object(pwbitbucket, 'api', return_value=None):  # never the real Bitbucket: the test must pass with no token, as on CI
            self.assertTrue(actions.perform({'action': 'refresh'})['ok'])
        self.assertTrue(poke.exists())
        poke.unlink()

    def test_archiving_the_journal_keeps_it_aside_and_starts_a_new_one(self):
        pwstore.LOG.write_text('2026-11-17 10:00:00 one\n2026-11-17 10:00:01 two\n', encoding='utf-8')
        self.assertEqual(actions.perform({'action': 'journal-archive'})['message'], 'Action non confirmée.')
        self.assertTrue(pwstore.LOG.read_text(encoding='utf-8').startswith('2026-11-17 10:00:00 one'))
        result = actions.perform({'action': 'journal-archive', 'confirmed': True})
        self.assertTrue(result['ok'], result)
        (archive,) = sorted(pwstore.ARCHIVES.glob('daemon-*.log'))
        self.assertEqual(archive.read_text(encoding='utf-8'), '2026-11-17 10:00:00 one\n2026-11-17 10:00:01 two\n')
        self.assertEqual(len(pwstore.LOG.read_text(encoding='utf-8').splitlines()), 1)  # the line saying it was archived
        self.assertIn('journal archived', pwstore.LOG.read_text(encoding='utf-8'))

    def test_only_the_latest_archives_are_kept(self):
        pwstore.ARCHIVES.mkdir(parents=True, exist_ok=True)
        for old in pwstore.ARCHIVES.glob('*'):
            old.unlink()
        for day in range(1, 13):
            (pwstore.ARCHIVES / f'daemon-202611{day:02d}-000000.log').write_text('x\n', encoding='utf-8')
        pwstore.LOG.write_text('2026-11-17 10:00:00 one\n', encoding='utf-8')
        pwstore.archive_log()
        names = sorted(p.name for p in pwstore.ARCHIVES.glob('daemon-*.log'))
        self.assertEqual(len(names), pwstore.ARCHIVES_KEPT)
        self.assertNotIn('daemon-20261101-000000.log', names)

    def test_a_large_journal_is_flagged(self):
        pwstore.LOG.write_text('x\n', encoding='utf-8')
        self.assertFalse(pwstore.log_health()['large'])
        pwstore.LOG.write_text('x' * pwstore.LOG_WARN_BYTES, encoding='utf-8')
        self.assertTrue(pwstore.log_health()['large'])

    def test_an_empty_journal_has_nothing_to_archive(self):
        pwstore.LOG.write_text('', encoding='utf-8')
        self.assertFalse(actions.perform({'action': 'journal-archive', 'confirmed': True})['ok'])

    def test_merge(self):
        with mock.patch.object(pwmerging, 'merge_pr', return_value=(True, 'PR mergée.')) as merge:
            result = actions.perform({'action': 'merge', 'repo': 'fake-repo', 'pr': 1, 'strategy': 'squash', 'closeSourceBranch': False, 'confirmed': True})
        self.assertTrue(result['ok'])
        self.assertEqual(merge.call_args.args[1:], ('squash', False))
        self.assertFalse(actions.perform({'action': 'merge', 'repo': 'fake-repo', 'pr': 1, 'strategy': 'rebase', 'confirmed': True})['ok'])


class CloneGuessTest(unittest.TestCase):
    def clone(self, root, name, branch):
        path = os.path.join(root, name)
        os.makedirs(path)
        git(path, 'init', '-q', '-b', branch)
        git(path, 'remote', 'add', 'origin', 'git@bitbucket.org:acme/my-repo.git')
        return path

    def guesses(self, root, branch=None):
        reset_config()
        set_registry([])
        with mock.patch.object(pwconfig, 'config', return_value={**pwconfig.config(), 'cloneFolders': [root]}):
            return pwadding.clone_guesses('my-repo', branch)

    def test_a_clone_with_another_name_is_found_by_its_origin(self):
        with tempfile.TemporaryDirectory() as root:
            renamed = self.clone(root, 'my-repo-2', 'main')
            os.makedirs(os.path.join(root, 'other', '.git'))
            self.assertEqual(self.guesses(root), [renamed])

    def test_the_clone_already_on_the_branch_comes_first(self):
        with tempfile.TemporaryDirectory() as root:
            named = self.clone(root, 'my-repo', 'main')
            working = self.clone(root, 'my-repo-2', 'PROJ-1-x')
            self.assertEqual(self.guesses(root), [named, working])
            self.assertEqual(self.guesses(root, 'PROJ-1-x'), [working, named])


class QuestionAnswerTest(unittest.TestCase):
    QUESTION = {'id': 9, 'author': 'Relecteur', 'place': 'a.ts:3', 'text': 'On valide ?', 'at': 'x'}

    def setUp(self):
        set_registry([entry(1, pendingQuestions=[self.QUESTION])])

    def test_no_leaves_the_comment_alone(self):
        self.assertTrue(actions.perform({'action': 'answer-question', 'repo': 'fake-repo', 'pr': 1, 'id': 9, 'choice': 'no'})['ok'])
        stored = registry()[0]
        self.assertEqual((stored['pendingQuestions'], stored.get('decisions')), ([], None))

    def test_yes_and_other_become_a_decision_for_the_daemon(self):
        self.assertTrue(pwpr.answer_question('fake-repo', 1, 9, 'yes')['ok'])
        self.assertIn('yes, handle this comment', registry()[0]['decisions'][0]['text'])
        set_registry([entry(1, pendingQuestions=[self.QUESTION])])
        self.assertTrue(pwpr.answer_question('fake-repo', 1, 9, 'other', ' garde la version actuelle ')['ok'])
        text = registry()[0]['decisions'][0]['text']
        self.assertIn('garde la version actuelle', text)
        self.assertIn('On valide ?', text)

    def test_bad_answers_change_nothing(self):
        self.assertFalse(pwpr.answer_question('fake-repo', 1, 9, 'other', '  ')['ok'])
        self.assertFalse(pwpr.answer_question('fake-repo', 1, 9, 'maybe')['ok'])
        self.assertFalse(pwpr.answer_question('fake-repo', 1, 10, 'yes')['ok'])
        self.assertFalse(actions.perform({'action': 'answer-question', 'repo': 'fake-repo', 'pr': 1, 'id': '9', 'choice': 'yes'})['ok'])
        self.assertEqual(len(registry()[0]['pendingQuestions']), 1)

    def test_the_card_shows_the_question(self):
        status = actions.overview()['entries'][0]['status']
        self.assertEqual((status['kind'], status['icon']), ('question', '❓'))
        self.assertEqual(actions.overview()['entries'][0]['pendingQuestions'][0]['id'], 9)


class TrayMaxPrsTest(unittest.TestCase):
    def test_the_option_defaults_to_5_and_stays_within_1_and_20(self):
        reset_config()
        self.assertEqual(pwconfig.config()['trayMaxPrs'], 5)
        self.assertTrue(pwoptions.set_options({'trayMaxPrs': 8})['ok'])
        self.assertEqual(pwconfig.config()['trayMaxPrs'], 8)
        for bad in (0, 21, 'dix', True):
            self.assertFalse(pwoptions.set_options({'trayMaxPrs': bad})['ok'], bad)
        reset_config()


class RemoveDoneTest(unittest.TestCase):
    def test_the_option_accepts_zero_for_never_and_refuses_the_rest(self):
        reset_config()
        self.assertTrue(pwoptions.set_options({'removeDoneAfterMinutes': 0})['ok'])
        self.assertEqual(pwconfig.config()['removeDoneAfterMinutes'], 0)
        for bad in (-1, 'jamais', True, 10**9):
            self.assertFalse(pwoptions.set_options({'removeDoneAfterMinutes': bad})['ok'], bad)

    def test_a_finished_pr_says_when_it_will_leave_the_list(self):
        import time
        reset_config()
        set_registry([entry(1, state='done', doneAt=time.time() - 50 * 60), entry(2), entry(3, state='done')])
        first, second, third = (e['removeInMinutes'] for e in actions.overview()['entries'])
        self.assertIn(first, (9, 10))
        self.assertEqual((second, third), (None, None))
        reset_config({'removeDoneAfterMinutes': 0})
        self.assertIsNone(actions.overview()['entries'][0]['removeInMinutes'])


class MergeStrategyTest(unittest.TestCase):
    def test_the_strategy_of_bitbucket_is_preselected_and_the_others_it_forbids_are_not_offered(self):
        form = pwforms.merge_form(entry(1, mergeDefault='fast_forward', mergeAllowed=['merge_commit', 'fast_forward']))
        self.assertEqual(form['defaultStrategy'], 'fast_forward')
        self.assertEqual([s['id'] for s in form['strategies']], ['merge_commit', 'fast_forward'])
        self.assertIn('configurée dans Bitbucket', form['strategyNote'])

    def test_a_strategy_this_program_does_not_know_is_still_offered(self):
        form = pwforms.merge_form(entry(1, mergeDefault='new_one', mergeAllowed=['squash', 'new_one']))
        self.assertEqual((form['defaultStrategy'], [s['id'] for s in form['strategies']]), ('new_one', ['squash', 'new_one']))

    def test_when_it_cannot_be_read_rebase_fast_forward_is_proposed_and_the_hint_says_how_to_fix_it(self):
        form = pwforms.merge_form(entry(1))
        self.assertEqual(form['defaultStrategy'], 'rebase_fast_forward')
        self.assertIn('illisible', form['strategyNote'])
        self.assertIn('jeton Bitbucket', form['strategyHint'])
        self.assertIn('read:repository', form['strategyHint'])
        self.assertEqual(len(form['strategies']), len(pwforms.MERGE_STRATEGIES))

    def test_when_it_is_known_there_is_no_hint(self):
        self.assertIsNone(pwforms.merge_form(entry(1, mergeDefault='squash', mergeAllowed=['squash']))['strategyHint'])

    def test_the_fallback_is_squash_when_the_branch_does_not_allow_it(self):
        self.assertEqual(pwforms.merge_form(entry(1, mergeAllowed=['squash', 'merge_commit']))['defaultStrategy'], 'squash')

    def test_a_merge_without_a_strategy_uses_the_one_of_bitbucket(self):
        set_registry([entry(1, mergeDefault='merge_commit', mergeAllowed=['merge_commit', 'squash'])])
        with mock.patch.object(pwmerging, 'merge_pr', return_value=(True, 'ok')) as merge_pr:
            self.assertTrue(actions.perform({'action': 'merge', 'repo': 'fake-repo', 'pr': 1, 'confirmed': True})['ok'])
        self.assertEqual(merge_pr.call_args.args[1], 'merge_commit')
        self.assertFalse(actions.perform({'action': 'merge', 'repo': 'fake-repo', 'pr': 1, 'strategy': 'fast_forward', 'confirmed': True})['ok'], 'the branch does not allow it')


class FocusTest(unittest.TestCase):
    def test_a_request_to_show_a_pull_request_reaches_the_dashboard_state(self):
        set_registry([entry(1)])
        self.assertIsNone(actions.overview()['focus'])
        actions.set_focus('fake-repo#1')
        focus = actions.overview()['focus']
        self.assertEqual(focus['key'], 'fake-repo#1')
        self.assertGreater(focus['at'], 1_700_000_000_000)


class StatusIconTest(unittest.TestCase):
    def test_each_state_has_an_icon_and_a_tooltip(self):
        cases = [
            ({'needsAttention': 'à voir'}, '⚠', 'à voir'),
            ({'ready': True}, '✔', 'Prête à merger'),
            ({'state': 'gave-up'}, '✖', 'épuisés'),
            ({'state': 'paused'}, '⏸', 'pause'),
            ({'state': 'done', 'doneReason': 'MERGED'}, '■', 'Fusionnée'),
            ({'mergeBlockers': ['2 tâche(s) ouverte(s)']}, '👁', 'Bloque le merge : 2 tâche'),
        ]
        for fields, icon, words in cases:
            with self.subTest(fields=fields):
                result = pwstatus.status(entry(1, **fields))
                self.assertEqual(result['icon'], icon)
                self.assertIn(words, result['tip'])
                self.assertIsNone(result['unpushed'])

    def test_commits_waiting_for_a_push_are_flagged(self):
        result = pwstatus.status(entry(1, ready=True), {'commits': 2, 'neverPushed': False})
        self.assertEqual(result['icon'], '✔⬆')
        self.assertEqual(result['unpushed'], '2 commits en attente de push')
        self.assertIn('Pousser', result['tip'])
        self.assertIn('jamais', pwstatus.status(entry(1), {'commits': 1, 'neverPushed': True})['unpushed'])
        self.assertIsNone(pwstatus.status(entry(1), {'commits': 0, 'neverPushed': False})['unpushed'])


class RegisterDefaultsTest(unittest.TestCase):
    def test_register_guesses_the_repository_branch_clone_and_session(self):
        import os
        from prwatch.engine import registration as pwregistration
        with tempfile.TemporaryDirectory() as home:
            work = os.path.join(home, 'ws', 'my-repo')
            os.makedirs(work)
            git(work, 'init', '-q', '-b', 'PROJ-7-feature')
            git(work, 'remote', 'add', 'origin', 'git@bitbucket.org:acme/my-repo.git')
            project = os.path.join(home, '.claude', 'projects', '-ws')
            os.makedirs(project)
            with open(os.path.join(project, 'abc-123.jsonl'), 'w', encoding='utf-8') as handle:
                handle.write('{"type":"summary"}\n{"cwd": "' + os.path.join(home, 'ws') + '", "type": "user"}\n')
            with mock.patch.dict(os.environ, {'HOME': home}):
                found = pwregistration.detect_defaults(os.path.join(work), {'CLAUDE_CODE_SESSION_ID': 'abc-123'})
                bare = pwregistration.detect_defaults(home, {})
        self.assertEqual(found, {'work_dir': os.path.realpath(work), 'branch': 'PROJ-7-feature', 'repo': 'my-repo', 'session': 'abc-123', 'session_cwd': os.path.join(home, 'ws')})
        self.assertEqual(bare, {})  # not in a clone, not in a Claude session: nothing is guessed

    def test_the_command_says_what_it_cannot_tell(self):
        import subprocess
        import sys
        import json
        import os
        import pathlib

        def register(config):
            data = tempfile.mkdtemp()
            pathlib.Path(data, 'config.json').write_text(json.dumps(config), encoding='utf-8')
            return subprocess.run([sys.executable, '-m', 'prwatch', 'register', '--pr', '1'], cwd=tempfile.gettempdir(), capture_output=True, text=True,
                                  env={'PATH': os.environ['PATH'], 'PYTHONPATH': str(pathlib.Path(__file__).resolve().parent.parent), 'PR_WATCH_HOME': data})
        done = register(TEAM)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('cannot tell', done.stderr)
        first_start = register({})
        self.assertNotEqual(first_start.returncode, 0)
        self.assertIn('workspace not set', first_start.stderr)


class GitActionsTest(unittest.TestCase):
    def setUp(self):
        # a rebase commits: it needs an identity, which the throw-away repositories do not have
        identity = {'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t', 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t'}
        patch = mock.patch.dict(os.environ, identity)
        patch.start()
        self.addCleanup(patch.stop)

    def test_unpushed_commits_are_counted_from_the_remote_branch(self):
        with tempfile.TemporaryDirectory() as root:
            work, _ = make_repo_with_remote(root)
            pr = entry(1, workDir=str(work))
            self.assertEqual(pwstatus.unpushed(pr), {'commits': 0, 'neverPushed': False})
            (work / 'a.txt').write_text('one\ntwo\n')
            git(work, 'commit', '-qam', 'second')
            self.assertEqual(pwstatus.unpushed(pr)['commits'], 1)
            pwpr.git_push('fake-repo', 1) if registry() else None
            git(work, 'push', '-q', 'origin', 'feature/x')
            self.assertEqual(pwstatus.unpushed(pr)['commits'], 0)
            self.assertIsNone(pwstatus.unpushed(entry(1, workDir='/nowhere')))
            self.assertIsNone(pwstatus.unpushed(entry(1, workDir=str(work), state='done')))

    def test_a_branch_that_moved_on_origin_is_fetched_before_the_push(self):
        with tempfile.TemporaryDirectory() as root:
            work, remote = make_repo_with_remote(root)
            set_registry([entry(1, workDir=str(work))])
            other = work.parent / 'other'
            git(root, 'clone', '-q', '-b', 'feature/x', str(remote), str(other))
            (other / 'b.txt').write_text('theirs\n')
            git(other, 'add', '.')
            git(other, 'commit', '-qm', 'theirs')
            git(other, 'push', '-q', 'origin', 'HEAD:feature/x')
            theirs = git(other, 'rev-parse', 'HEAD')

            asked = pwpr.git_push('fake-repo', 1)
            self.assertFalse(asked['ok'])
            self.assertTrue(asked['needsFetch'])
            self.assertEqual(actions.perform({'action': 'fetch', 'repo': 'fake-repo', 'pr': 1})['message'], 'Action non confirmée.')

            fetched = actions.perform({'action': 'fetch', 'repo': 'fake-repo', 'pr': 1, 'confirmed': True})
            self.assertTrue(fetched['ok'], fetched)
            self.assertEqual(git(work, 'rev-parse', 'origin/feature/x'), theirs)
            (work / 'c.txt').write_text('mine\n')
            git(work, 'add', '.')
            git(work, 'commit', '-qm', 'mine')
            behind = pwpr.git_push('fake-repo', 1)  # known now, but the local branch lacks it
            self.assertTrue(behind['needsRebase'])
            self.assertNotIn('needsFetch', behind)
            self.assertEqual(actions.perform({'action': 'rebase', 'repo': 'fake-repo', 'pr': 1})['message'], 'Action non confirmée.')
            rebased = actions.perform({'action': 'rebase', 'repo': 'fake-repo', 'pr': 1, 'confirmed': True})
            self.assertTrue(rebased['ok'], rebased)
            self.assertTrue(pwpr.git_push('fake-repo', 1)['ok'])
            self.assertEqual(git(remote, 'rev-parse', 'feature/x'), git(work, 'rev-parse', 'HEAD'))

    def test_a_rebase_does_not_switch_the_checked_out_branch(self):
        with tempfile.TemporaryDirectory() as root:
            work, remote = make_repo_with_remote(root)
            set_registry([entry(1, workDir=str(work))])
            git(work, 'checkout', '-q', '-b', 'other')
            other = work.parent / 'other-clone'
            git(root, 'clone', '-q', '-b', 'feature/x', str(remote), str(other))
            (other / 'b.txt').write_text('theirs\n')
            git(other, 'add', '.')
            git(other, 'commit', '-qm', 'theirs')
            git(other, 'push', '-q', 'origin', 'HEAD:feature/x')
            self.assertTrue(pwpr.git_fetch('fake-repo', 1)['ok'])
            self.assertTrue(pwpr.git_push('fake-repo', 1)['needsRebase'])  # HEAD is on `other`, the branch is the one compared
            result = pwpr.git_rebase('fake-repo', 1)
            self.assertFalse(result['ok'])
            self.assertIn('git checkout feature/x', result['message'])
            self.assertEqual(git(work, 'rev-parse', '--abbrev-ref', 'HEAD'), 'other')

    def test_a_conflicting_rebase_is_aborted(self):
        with tempfile.TemporaryDirectory() as root:
            work, remote = make_repo_with_remote(root)
            set_registry([entry(1, workDir=str(work))])
            other = work.parent / 'other'
            git(root, 'clone', '-q', '-b', 'feature/x', str(remote), str(other))
            (other / 'a.txt').write_text('theirs\n')
            git(other, 'commit', '-qam', 'theirs')
            git(other, 'push', '-q', 'origin', 'HEAD:feature/x')
            (work / 'a.txt').write_text('mine\n')
            git(work, 'commit', '-qam', 'mine')
            before = git(work, 'rev-parse', 'HEAD')
            self.assertTrue(pwpr.git_fetch('fake-repo', 1)['ok'])
            result = pwpr.git_rebase('fake-repo', 1)
            self.assertFalse(result['ok'])
            self.assertIn('Rebase annulé', result['output'])
            self.assertEqual(git(work, 'rev-parse', 'HEAD'), before)

    def test_push_and_force_push_with_lease(self):
        with tempfile.TemporaryDirectory() as root:
            work, remote = make_repo_with_remote(root)
            set_registry([entry(1, workDir=str(work))])

            (work / 'a.txt').write_text('one\ntwo\n')
            git(work, 'commit', '-qam', 'second')
            self.assertTrue(pwpr.git_push('fake-repo', 1)['ok'])
            self.assertEqual(git(remote, 'rev-parse', 'feature/x'), git(work, 'rev-parse', 'HEAD'))

            # someone else pushes meanwhile, then the local history is rewritten
            other = work.parent / 'other'
            git(root, 'clone', '-q', '-b', 'feature/x', str(remote), str(other))
            (other / 'b.txt').write_text('theirs\n')
            git(other, 'add', '.')
            git(other, 'commit', '-qm', 'theirs')
            git(other, 'push', '-q', 'origin', 'HEAD:feature/x')
            git(work, 'commit', '-q', '--amend', '-m', 'second, rewritten')

            refused = pwpr.git_push('fake-repo', 1, force=True)
            self.assertFalse(refused['ok'], refused['output'])
            self.assertIn('--force-with-lease', refused['output'])
            self.assertEqual(git(remote, 'log', '-1', '--format=%s', 'feature/x'), 'theirs')

            git(work, 'fetch', '-q', 'origin')
            self.assertTrue(pwpr.git_push('fake-repo', 1, force=True)['ok'])
            self.assertEqual(git(remote, 'log', '-1', '--format=%s', 'feature/x'), 'second, rewritten')

    def test_a_directory_that_is_not_a_repository(self):
        set_registry([entry(1, workDir=tempfile.gettempdir())])
        self.assertFalse(pwpr.git_push('fake-repo', 1)['ok'])


class JournalActionTest(unittest.TestCase):
    def test_journal(self):
        pwstore.LOG.write_text('2026-11-17 10:00:00 daemon started (pid 1)\n2026-11-17 10:00:01 API 403 on /x\n', encoding='utf-8')
        data = actions.journal('important', '')
        self.assertEqual([e['kind'] for e in data['entries']], ['error'])
        self.assertEqual(data['total'], 2)
        self.assertEqual(len(actions.journal('all', 'daemon')['entries']), 1)

    def test_older_entries_are_left_out_then_asked_for(self):
        lines = ''.join(f'2026-11-17 10:{i // 60:02d}:{i % 60:02d} line {i} in a-repo\n' for i in range(5))
        pwstore.LOG.write_text(lines, encoding='utf-8')
        page = actions.journal('all', '', '', 2)
        self.assertEqual([e['message'] for e in page['entries']], ['line 3 in a-repo', 'line 4 in a-repo'])
        self.assertEqual((page['older'], page['shown'], page['total']), (3, 5, 5))
        self.assertEqual(actions.journal('all', '', '', 0)['older'], 0)

    def test_the_report_of_a_session_is_marked(self):
        pwstore.LOG.write_text(
            '2026-11-17 10:00:00 repo-a#1: resuming session abc for 1 event(s)\n'
            '2026-11-17 10:05:00 repo-a#1: claude exited 0\nrésumé\nPR-WATCH: OK\n'
            '2026-11-17 10:06:00 repo-a#1: session in use, 1 event(s) kept for later\n', encoding='utf-8')
        self.assertEqual([e['report'] for e in actions.journal('all', '', 'repo-a#1')['entries']], [False, True, False])

    def test_the_journal_of_one_pull_request(self):
        pwstore.LOG.write_text(
            '2026-11-17 10:00:00 repo-a#1: claude exited 0\n'
            '2026-11-17 10:00:01 repo-a#12: claude exited 0\n'
            '2026-11-17 10:00:02 repo-a#1: NOTIFY ⚠ Votre attention est nécessaire: à voir\n',
            encoding='utf-8')
        data = actions.journal('all', '', 'repo-a#1')
        self.assertEqual([e['pr'] for e in data['entries']], ['repo-a#1', 'repo-a#1'])
        self.assertEqual(data['entries'][1]['kind'], 'attention')  # a notification keeps its kind under the PR prefix


if __name__ == '__main__':
    unittest.main()
