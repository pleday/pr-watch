import json
from datetime import datetime, timedelta
import argparse
import os
import time
import unittest
from pathlib import Path
from unittest import mock


from .helpers import entry, registry, reset_config, set_registry
from prwatch.core import bitbucket as pwbitbucket
from prwatch.core import config as pwconfig
from prwatch.system import desktop as pwdesktop
from prwatch.engine import events as pwevents
from prwatch.engine import merging as pwmerging
from prwatch.engine import service as pwservice
from prwatch.core import store as pwstore
from prwatch.engine import wake as pwwake
from prwatch.actions import tickets as pwtickets
from prwatch.actions import status as pwstatus
from prwatch.actions import pr as pwpr
from prwatch.actions import options as pwoptions
from prwatch.system import host as pwhost
from prwatch.system import window as pwwindow
from prwatch import cli as pwcli
from prwatch.engine import cycle as pwcycle

BOT = {'display_name': 'Bitbucket Pipelines', 'type': 'app_user'}
PR = {'title': 'Titre', 'author': {'uuid': 'me'}, 'destination': {'branch': {'name': 'develop'}}, 'id': 1}


class CommentsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = pwconfig.config()

    def test_only_the_automatic_review_with_something_to_say_is_followed(self):
        self.assertTrue(pwevents.followed_bot(BOT, '## 🤖 Revue automatique\n\n| 🔴 | 1 |', self.cfg))
        self.assertFalse(pwevents.followed_bot(BOT, "## 🤖 Revue automatique\n\n✅ Rien d'important à signaler.", self.cfg))
        self.assertFalse(pwevents.followed_bot(BOT, 'Staging: https://x', self.cfg))
        self.assertFalse(pwevents.followed_bot({'display_name': 'Bitbucket-CreatePR-fromPipeline'}, 'Jira ticket created', self.cfg))

    def test_comment_events(self):
        comments = {'values': [
            {'id': 1, 'user': {'uuid': 'me', 'display_name': 'Moi'}, 'content': {'raw': 'Réponse de Claude\n\n— 🤖 Claude'}},
            {'id': 2, 'user': {'uuid': 'u2', 'display_name': 'Relecteur'}, 'content': {'raw': 'please fix'}, 'inline': {'path': 'a.js', 'to': 12}},
            {'id': 3, 'user': {'uuid': 'b', 'display_name': 'Bitbucket Pipelines', 'type': 'app_user'}, 'content': {'raw': '## 🤖 Revue automatique\n1 majeur'}},
            {'id': 4, 'user': {'uuid': 'b', 'display_name': 'Bitbucket Pipelines', 'type': 'app_user'}, 'content': {'raw': 'Staging link'}},
            {'id': 5, 'user': {'uuid': 'u2', 'display_name': 'Relecteur'}, 'content': {'raw': 'gone'}, 'deleted': True},
        ]}
        with mock.patch.object(pwbitbucket, 'api', return_value=comments):
            events, newest = pwevents.comment_events({'repo': 'r', 'pr': 1, 'lastComment': 0}, PR, self.cfg)
        self.assertEqual([e['kind'] for e in events], ['comment', 'review-bot'])
        self.assertIn('a.js:12', events[0]['text'])
        self.assertEqual(newest, 5)

    def test_the_comments_of_the_author_are_followed_except_the_replies_of_claude(self):
        comments = {'values': [
            {'id': 1, 'user': {'uuid': 'me', 'display_name': 'Moi'}, 'content': {'raw': 'Le contexte : on garde l\'ancien format. Ok ?'}},
            {'id': 2, 'user': {'uuid': 'me', 'display_name': 'Moi'}, 'content': {'raw': 'Réponse\n\n— 🤖 Claude'}, 'parent': {'id': 1}},
            {'id': 3, 'user': {'uuid': 'me', 'display_name': 'Moi'}, 'content': {'raw': 'Non, applique plutôt la remarque du relecteur'}, 'parent': {'id': 9}},
        ]}
        with mock.patch.object(pwbitbucket, 'api', return_value=comments):
            events, newest = pwevents.comment_events({'repo': 'r', 'pr': 1, 'lastComment': 0}, PR, self.cfg)
        self.assertEqual([e['kind'] for e in events], ['comment', 'comment'], 'never a question put back to the user')
        self.assertIn('the author of the PR', events[0]['text'])
        self.assertIn('reply to comment 9', events[1]['text'])
        self.assertEqual(newest, 3)

    def test_the_review_summary_carries_the_inline_findings_posted_with_it(self):
        bot = {'uuid': 'b', 'display_name': 'Bitbucket Pipelines', 'type': 'app_user'}
        comments = {'values': [
            {'id': 1, 'user': bot, 'content': {'raw': '### Minor · variable'}, 'inline': {'path': 'a.html', 'to': 3}, 'created_on': '2026-11-18T11:29:00.000000+00:00'},
            {'id': 2, 'user': bot, 'content': {'raw': '## 🤖 Revue automatique\n1 mineur'}, 'created_on': '2026-11-18T11:30:00.000000+00:00'},
        ]}
        with mock.patch.object(pwbitbucket, 'api', return_value=comments):
            events, _ = pwevents.comment_events({'repo': 'r', 'pr': 1, 'lastComment': 0}, PR, self.cfg)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['threads'], [1])
        self.assertIn('Inline comment 1 of the same review, on a.html:3', events[0]['text'])


class LogsTest(unittest.TestCase):
    def test_summary_keeps_the_errors_and_folds_repeated_codes(self):
        noise = '\n'.join(f'➤ YN0041: │ pkg{i}@npm:1.0.0: Invalid authentication' for i in range(50))
        text = 'Unpacking libfoo\n(Reading database ... 5%\n' + noise + '\n➤ YN0000: · Failed with errors\n'
        summary = pwevents.summarize_log(text)
        self.assertNotIn('Reading database', summary)
        self.assertEqual(summary.count('Invalid authentication'), 3)
        self.assertIn('50 lines of YN0041', summary)

    def test_infrastructure_patterns(self):
        self.assertTrue(any(p in 'xx Invalid authentication (as an unknown user)' for p in pwconfig.config()['infraPatterns']))


class PurgeDoneTest(unittest.TestCase):
    def purge(self, entries, minutes=60):
        set_registry(entries)
        reset_config({'removeDoneAfterMinutes': minutes})
        pwmerging.purge_done(pwconfig.config())
        return [e['pr'] for e in registry()]

    def test_a_finished_pr_leaves_the_list_after_the_delay_and_only_then(self):
        now = time.time()
        kept = self.purge([entry(1, state='done', doneAt=now - 61 * 60), entry(2, state='done', doneAt=now - 10 * 60), entry(3), entry(4, state='paused')])
        self.assertEqual(kept, [2, 3, 4], 'an open PR is never removed, however old')

    def test_zero_means_never(self):
        self.assertEqual(self.purge([entry(1, state='done', doneAt=time.time() - 10**6)], minutes=0), [1])

    def test_a_pr_finished_before_the_time_was_recorded_is_counted_from_now(self):
        self.assertEqual(self.purge([entry(1, state='done')]), [1])
        self.assertAlmostEqual(registry()[0]['doneAt'], time.time(), delta=5)

    def test_the_default_is_one_hour(self):
        reset_config()
        self.assertEqual(pwconfig.config()['removeDoneAfterMinutes'], 60)

    def test_a_merge_records_when_it_happened(self):
        set_registry([entry(1)])
        with mock.patch.object(pwbitbucket, 'api', return_value={'state': 'MERGED'}), mock.patch.object(pwbitbucket, 'auth_header', return_value='Basic x'), \
                mock.patch.object(pwmerging.urllib.request, 'urlopen') as opened:
            opened.return_value.__enter__.return_value.status = 200
            self.assertTrue(pwmerging.merge_pr(registry()[0], 'squash', True)[0])
        self.assertAlmostEqual(registry()[0]['doneAt'], time.time(), delta=5)


class LogRotationTest(unittest.TestCase):
    def test_a_journal_over_the_limit_is_kept_once_and_started_again(self):
        pwstore.DIR.mkdir(parents=True, exist_ok=True)
        pwstore.LOG.write_text('x' * 100, encoding='utf-8')
        with mock.patch.object(pwstore, 'LOG_MAX_BYTES', 50):
            pwstore.log('after')
        self.assertEqual(pwstore.LOG.read_text(encoding='utf-8').count('after'), 1)
        self.assertEqual(pwstore.LOG.with_name('daemon.log.1').read_text(encoding='utf-8'), 'x' * 100)
        pwstore.LOG.unlink()
        pwstore.LOG.with_name('daemon.log.1').unlink()


class MergeStrategiesNoScopeTest(unittest.TestCase):
    def test_the_branch_is_not_read_when_the_scope_is_known_to_be_missing(self):
        from prwatch.core import permissions
        pwmerging._MERGE_INFO.clear()
        permissions._save({'at': 'x', 'checks': [{'id': 'bitbucket-repository', 'label': '', 'required': False, 'why': '', 'features': [], 'ok': False, 'detail': ''}], 'declined': []})
        pr = {'destination': {'branch': {'name': 'develop'}}}
        with mock.patch.object(pwbitbucket, 'api') as api:
            self.assertEqual(pwmerging.merge_strategies({'repo': 'r', 'pr': 1}, pr), {})
        api.assert_not_called()
        permissions._save({'at': None, 'checks': [], 'declined': []})  # never checked: the branch is tried
        pwmerging._MERGE_INFO.clear()
        with mock.patch.object(pwbitbucket, 'api', return_value={'default_merge_strategy': 'squash'}) as api:
            self.assertEqual(pwmerging.merge_strategies({'repo': 'r', 'pr': 1}, pr), {'default': 'squash'})
        api.assert_called_once()
        pwmerging._MERGE_INFO.clear()


class MergeStrategiesTest(unittest.TestCase):
    PR = {'destination': {'branch': {'name': 'release/1.0'}}}

    def setUp(self):
        pwmerging._MERGE_INFO.clear()

    def test_they_are_read_from_the_destination_branch_once_in_a_while(self):
        branch = {'default_merge_strategy': 'squash', 'merge_strategies': ['squash', 'merge_commit']}
        with mock.patch.object(pwbitbucket, 'api', return_value=branch) as api:
            first = pwmerging.merge_strategies({'repo': 'r'}, self.PR)
            pwmerging.merge_strategies({'repo': 'r'}, self.PR)
        self.assertEqual(first, {'default': 'squash', 'allowed': ['squash', 'merge_commit']})
        self.assertEqual(api.call_count, 1)
        self.assertIn('release%2F1.0', api.call_args.args[0])

    def test_an_unreadable_branch_gives_nothing_and_is_not_asked_again_at_once(self):
        with mock.patch.object(pwbitbucket, 'api', return_value=None) as api:
            self.assertEqual(pwmerging.merge_strategies({'repo': 'r'}, self.PR), {})
            pwmerging.merge_strategies({'repo': 'r'}, self.PR)
        self.assertEqual(api.call_count, 1)

    def test_the_cycle_keeps_them_on_the_entry(self):
        set_registry([entry(1)])
        reset_config()
        pr = {**PR, 'state': 'OPEN', 'title': 'PR 1', 'destination': {'branch': {'name': 'develop'}}}
        with mock.patch.object(pwbitbucket, 'api', return_value=pr), mock.patch.object(pwmerging, 'merge_strategies', return_value={'default': 'squash', 'allowed': ['squash']}), \
                mock.patch.object(pwmerging, 'sync_ready'), mock.patch.object(pwevents, 'pipeline_events', return_value=([], 101)), \
                mock.patch.object(pwevents, 'comment_events', return_value=([], 7)), mock.patch.object(pwevents, 'status_events', return_value=([], [])):
            pwcycle.handle(registry()[0], pwconfig.config(), False)
        self.assertEqual((registry()[0]['mergeDefault'], registry()[0]['mergeAllowed']), ('squash', ['squash']))


class PipelineEventsTest(unittest.TestCase):
    ENTRY = {'repo': 'r', 'pr': 1, 'branch': 'b', 'lastPipeline': 0}

    def events(self, *states, log='[INFO] QUALITY GATE STATUS: PASSED - View details on https://sonar.example/dashboard?id=org.x%3Aname&pullRequest=7'):
        pipelines = [{'build_number': n, 'uuid': f'{{u{n}}}', 'state': {'name': name, 'result': {'name': 'SUCCESSFUL'} if name == 'COMPLETED' else None}}
                     for n, name in states]
        steps = {'values': [{'name': 'Sonar analysis', 'uuid': '{s}', 'state': {'name': 'COMPLETED', 'result': {'name': 'SUCCESSFUL'}}}]}
        def api(path, *a, **k):
            if path.endswith('/log'):
                return log
            return {'values': pipelines} if path.endswith('/pipelines/') else steps

        with mock.patch.object(pwbitbucket, 'api', side_effect=api):
            return pwevents.pipeline_events(dict(self.ENTRY))

    def review_events(self, comments):
        chunks = ''.join(json.dumps({'sessionId': 's', 'update': {'sessionUpdate': 'agent_message_chunk', 'messageId': 'm', 'content': {'text': t}}}) + '\n'
                         for t in ('1 minor ', 'point'))
        pipelines = [{'build_number': 1, 'uuid': '{u1}', 'created_on': '2026-01-01T10:00:00Z', 'state': {'name': 'COMPLETED', 'result': {'name': 'SUCCESSFUL'}}}]
        steps = {'values': [{'name': 'Pull request AI review', 'uuid': '{s}', 'state': {'name': 'COMPLETED', 'result': {'name': 'SUCCESSFUL'}}}]}

        def api(path, *a, **k):
            if path.endswith('/log'):
                return 'noise\n' + chunks
            if path.endswith('/comments'):
                return {'values': comments}
            return {'values': pipelines} if path.endswith('/pipelines/') else steps

        with mock.patch.object(pwbitbucket, 'api', side_effect=api):
            return pwevents.pipeline_events(dict(self.ENTRY))[0]

    def test_a_review_that_posted_nothing_is_read_in_the_log(self):
        events = self.review_events([])
        self.assertEqual([e['kind'] for e in events], ['review-bot', 'pipeline-ok'])
        self.assertIn('1 minor point', events[0]['text'])

    def test_a_review_that_was_posted_is_not_read_twice(self):
        posted = {'user': {'type': 'app_user', 'display_name': 'Bitbucket Pipelines'}, 'created_on': '2026-01-01T10:01:00Z',
                  'content': {'raw': '## 🤖 Revue automatique\n1 mineur'}}
        self.assertEqual([e['kind'] for e in self.review_events([posted])], ['pipeline-ok'])

    def test_a_success_with_sonar_waits_while_another_pipeline_of_the_branch_runs(self):
        events, newest = self.events((1, 'COMPLETED'), (2, 'IN_PROGRESS'))
        self.assertEqual((events, newest), ([], 0), 'its Sonar analysis is not published yet: nothing to read, cursor not moved')

    def test_it_is_looked_at_once_nothing_runs_any_more(self):
        events, newest = self.events((1, 'COMPLETED'), (2, 'COMPLETED'), log='no verdict in this log')
        self.assertEqual([e['actionable'] for e in events], [True, True])
        self.assertEqual(newest, 2)

    def test_a_quality_gate_the_scanner_says_passed_does_not_wake_claude(self):
        events, newest = self.events((1, 'COMPLETED'))
        self.assertEqual([e['actionable'] for e in events], [False])
        self.assertIn('PASSED', events[0]['text'])
        self.assertEqual(newest, 1)

    def test_a_failed_gate_gives_claude_the_exact_project_key(self):
        events, _ = self.events((1, 'COMPLETED'), log='x\n[INFO] QUALITY GATE STATUS: FAILED - View details on https://s/dashboard?id=org.acme%3Aacme-security&pullRequest=135\n')
        self.assertTrue(events[0]['actionable'])
        self.assertIn("project key 'org.acme:acme-security'", events[0]['text'])
        self.assertIn("pullRequest '135'", events[0]['text'])

    def test_the_address_of_a_dashboard_is_read(self):
        self.assertEqual(pwevents.sonar_target('https://s/dashboard?id=org.acme%3Aacme-security&pullRequest=135'), ('org.acme:acme-security', '135'))
        self.assertEqual(pwevents.sonar_target(None), (None, None))


class ConclusionTest(unittest.TestCase):
    def conclude(self, ok, report):
        with mock.patch.object(pwwake, 'notify') as notify:
            fields = pwwake.conclude({'pr': 1}, {'title': 't'}, ok, report, [1], pwconfig.config())
        return fields, notify

    def test_ok(self):
        fields, notify = self.conclude(True, 'fait\nPR-WATCH: OK')
        self.assertEqual(fields['needsAttention'], '')
        self.assertFalse(notify.call_args.kwargs.get('urgent'))

    def test_attention_notifies_urgently(self):
        fields, notify = self.conclude(True, 'à décider\nPR-WATCH: ATTENTION le relecteur demande un choix')
        self.assertEqual(fields['needsAttention'], 'le relecteur demande un choix')
        self.assertTrue(notify.call_args.kwargs['urgent'])

    def test_no_verdict_or_a_failed_run_needs_the_user(self):
        self.assertIn('sans conclusion', self.conclude(True, 'pas de marqueur')[0]['needsAttention'])
        self.assertIn('échoué', self.conclude(False, 'PR-WATCH: OK')[0]['needsAttention'])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        self.cfg = pwconfig.config()

    def test_defaults(self):
        self.assertEqual(self.cfg['handling'], {'pipeline': 'fix', 'comment': 'fix', 'question': 'ask', 'review-bot': 'fix', 'sonar': 'fix', 'infra': 'rerun', 'ready': 'notify'})
        self.assertTrue(self.cfg['autoPush'])

    def test_the_team_specific_values_are_options(self):
        from prwatch.core import bitbucket as pwbitbucket
        from prwatch.integrations import jira as pwjira
        pwstore.CONFIG.write_text('{}', encoding='utf-8')
        first = pwconfig.config()
        self.assertEqual((first['bitbucketWorkspace'], first['jiraSite'], first['jiraImpactsField']), ('', '', ''))
        self.assertEqual(pwconfig.missing_setup(first), ['bitbucketWorkspace'])
        with mock.patch.object(pwevents, 'pipeline_events') as looked, mock.patch.object(pwbitbucket, 'api') as api:
            pwcycle.cycle()
        looked.assert_not_called()
        api.assert_not_called()
        self.assertEqual(pwconfig.missing_setup(pwconfig.config()), ['bitbucketWorkspace'])
        reset_config({'bitbucketWorkspace': 'acme', 'sonarPattern': 'code.?quality', 'jiraImpactsField': '', 'cloneFolders': ['~/code']})
        self.assertEqual(pwbitbucket.workspace(), 'acme')
        self.assertEqual(pwbitbucket.repo_path({'repo': 'r'}), '/repositories/acme/r')
        cfg = pwconfig.config()
        self.assertTrue(pwconfig.sonar_matches(cfg, 'Code Quality gate'))
        self.assertFalse(pwconfig.sonar_matches(cfg, 'Sonar analysis'))
        self.assertIsNone(pwjira.impacts_field('https://x'), 'no impacts field configured: Jira is not even asked')
        reset_config({'sonarPattern': ''})
        self.assertFalse(pwconfig.sonar_matches(pwconfig.config(), 'Sonar analysis'))
        reset_config({'sonarPattern': '('})
        self.assertTrue(pwconfig.sonar_matches(pwconfig.config(), 'Sonar analysis'), 'a broken pattern falls back to the default word')
        reset_config()

    def test_global_choices_are_merged_with_the_defaults_and_bad_ones_ignored(self):
        reset_config({'handling': {'comment': 'notify', 'sonar': 'nonsense'}})
        self.assertEqual(pwconfig.config()['handling']['comment'], 'notify')
        self.assertEqual(pwconfig.config()['handling']['sonar'], 'fix')
        reset_config()

    def test_a_pr_overrides_the_global_choices(self):
        own = {'settings': {'autoPush': False, 'handling': {'comment': 'notify', 'sonar': 'ignore', 'pipeline': None}}}
        effective = pwconfig.effective(own, self.cfg)
        self.assertFalse(effective['autoPush'])
        self.assertEqual((effective['handling']['comment'], effective['handling']['sonar'], effective['handling']['pipeline']), ('notify', 'ignore', 'fix'))

    def test_validation(self):
        self.assertEqual(pwconfig.validate_handling({'comment': 'ignore'}), {'comment': 'ignore'})
        for bad in ({'nope': 'fix'}, {'comment': 'rerun'}, {'infra': 'fix'}, {'comment': None}, 'x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pwconfig.validate_handling(bad)
        self.assertEqual(pwconfig.validate_handling({'comment': None}, partial=True), {'comment': None})

    def test_routing(self):
        events = [
            {'kind': 'pipeline-failed', 'actionable': True, 'text': 'Pipeline #1', 'infra': False},
            {'kind': 'pipeline-failed', 'actionable': True, 'text': 'Pipeline #2', 'infra': True},
            {'kind': 'comment', 'actionable': True, 'text': 'c'},
            {'kind': 'review-bot', 'actionable': True, 'text': 'r'},
            {'kind': 'pipeline-ok', 'actionable': True, 'text': 'sonar'},
            {'kind': 'manual', 'actionable': True, 'text': 'm'},
        ]
        settings = pwconfig.effective({'settings': {'handling': {'comment': 'notify', 'sonar': 'ignore'}}}, self.cfg)
        to_claude, to_rerun, to_tell = pwconfig.route(events, settings)
        self.assertEqual([e['kind'] for e in to_claude], ['pipeline-failed', 'review-bot', 'manual'])
        self.assertEqual([e['text'] for e in to_rerun], ['Pipeline #2'])
        self.assertEqual([e['kind'] for e in to_tell], ['comment'])

    def test_suspension_temporarily_routes_claude_events_to_notifications(self):
        events = [
            {'kind': 'comment', 'actionable': True, 'text': 'c'},
            {'kind': 'question', 'actionable': True, 'text': 'q'},
            {'kind': 'pipeline-failed', 'actionable': True, 'text': 'infra', 'infra': True},
            {'kind': 'manual', 'actionable': True, 'text': 'm'},
        ]
        settings = pwconfig.effective({'settings': {'handling': {'question': 'fix', 'comment': 'ignore'}}}, self.cfg)
        to_claude, to_rerun, to_tell = pwconfig.route(events, settings, claude_disabled=True)
        self.assertEqual(to_claude, [])
        self.assertEqual([e['kind'] for e in to_rerun], ['pipeline-failed'])
        self.assertEqual([e['kind'] for e in to_tell], ['question', 'manual'])

    def test_the_prompt_follows_the_push_choice_of_the_pr(self):
        pushing = pwwake.build_prompt(entry(1), PR, [{'kind': 'comment', 'text': 'x'}], self.cfg)
        self.assertIn('then push the fix to the same branch', pushing)
        quiet = pwwake.build_prompt(entry(1, settings={'autoPush': False}), PR, [{'kind': 'comment', 'text': 'x'}], self.cfg)
        self.assertIn('do NOT push', quiet)

    def test_command_line_settings(self):
        args = argparse.Namespace(auto_push='no', handling='comment=notify,sonar=default')
        given = pwcli.settings_from_arguments(args)
        self.assertEqual(given, {'autoPush': False, 'handling': {'comment': 'notify', 'sonar': None}})
        merged = pwconfig.merge_settings({'settings': {'autoPush': True, 'handling': {'sonar': 'ignore', 'pipeline': 'notify'}}}, given)
        self.assertEqual(merged, {'autoPush': False, 'handling': {'pipeline': 'notify', 'comment': 'notify'}})
        with self.assertRaises(SystemExit):
            pwcli.settings_from_arguments(argparse.Namespace(auto_push=None, handling='comment=rerun'))


class HandleTest(unittest.TestCase):
    """handle(): what the settings do with the events of a cycle."""

    def run_cycle(self, settings, events, suspension=None, suspension_after_gate=False):
        set_registry([entry(1, settings=settings)])
        reset_config()
        cfg = pwconfig.config()
        pr = {**PR, 'state': 'OPEN', 'title': 'PR 1'}
        with mock.patch.object(pwbitbucket, 'api', return_value=pr), \
                mock.patch.object(pwmerging, 'sync_ready'), \
                mock.patch.object(pwevents, 'pipeline_events', return_value=([e for e in events if e['kind'].startswith('pipeline')], 101)), \
                mock.patch.object(pwevents, 'comment_events', return_value=([e for e in events if e['kind'] in ('comment', 'question', 'review-bot')], 7)), \
                mock.patch.object(pwevents, 'status_events', return_value=([e for e in events if e['kind'] == 'sonar-failed'], [])), \
                mock.patch.object(pwwake, 'session_busy', return_value=False), \
                mock.patch.object(pwwake, 'wake_claude', return_value=(True, 'ok\nPR-WATCH: OK')) as wake, \
                mock.patch.object(pwstore, 'suspension', side_effect=[None, {'kind': 'usage'}] if suspension_after_gate else None, return_value=suspension), \
                mock.patch.object(pwcycle, 'usage_gate'), \
                mock.patch.object(pwwake, 'notify') as notify:
            pwcycle.handle(registry()[0], cfg, False)
        return wake, notify

    COMMENT = {'kind': 'comment', 'actionable': True, 'text': 'Comment 9 by Relecteur:\nplease fix'}
    SONAR = {'kind': 'sonar-failed', 'actionable': True, 'text': 'Sonar FAILED'}

    QUESTION = {'kind': 'question', 'actionable': True, 'text': 'Comment 9 by Relecteur:\nOn valide ?',
                'comment': {'id': 9, 'author': 'Relecteur', 'place': 'a.ts:3', 'text': 'On valide ?'}}

    def test_a_question_is_put_to_the_user_and_does_not_wake_claude(self):
        wake, notify = self.run_cycle({}, [self.QUESTION])
        self.assertEqual(wake.call_count, 0)
        self.assertIn('❓', notify.call_args.args[0])
        self.assertTrue(notify.call_args.kwargs['urgent'])
        stored = registry()[0]
        self.assertEqual([q['id'] for q in stored['pendingQuestions']], [9])
        self.assertEqual((stored['askedComments'], stored['lastComment']), ([9], 7))

    def test_a_question_is_asked_only_once(self):
        self.run_cycle({}, [self.QUESTION])
        set_registry(registry())
        with mock.patch.object(pwbitbucket, 'api', return_value={**PR, 'state': 'OPEN', 'title': 'PR 1'}), mock.patch.object(pwmerging, 'sync_ready'), \
                mock.patch.object(pwevents, 'pipeline_events', return_value=([], 101)), mock.patch.object(pwevents, 'comment_events', return_value=([self.QUESTION], 7)), \
                mock.patch.object(pwevents, 'status_events', return_value=([], [])), mock.patch.object(pwwake, 'notify') as notify:
            pwcycle.handle(registry()[0], pwconfig.config(), False)
        self.assertEqual(notify.call_count, 0)
        self.assertEqual(len(registry()[0]['pendingQuestions']), 1)

    def test_a_question_can_go_straight_to_claude_when_the_pr_says_so(self):
        wake, _ = self.run_cycle({'handling': {'question': 'fix'}}, [self.QUESTION])
        self.assertEqual(wake.call_count, 1)
        self.assertNotIn('pendingQuestions', registry()[0])

    def test_the_decision_of_the_user_wakes_claude_once(self):
        set_registry([entry(1, decisions=[{'id': 9, 'text': 'Question of Relecteur: the user says: yes'}])])
        reset_config()
        with mock.patch.object(pwbitbucket, 'api', return_value={**PR, 'state': 'OPEN', 'title': 'PR 1'}), mock.patch.object(pwmerging, 'sync_ready'), \
                mock.patch.object(pwevents, 'pipeline_events', return_value=([], 101)), mock.patch.object(pwevents, 'comment_events', return_value=([], 7)), \
                mock.patch.object(pwevents, 'status_events', return_value=([], [])), mock.patch.object(pwwake, 'session_busy', return_value=False), \
                mock.patch.object(pwwake, 'wake_claude', return_value=(True, 'PR-WATCH: OK')) as wake, mock.patch.object(pwwake, 'notify'):
            pwcycle.handle(registry()[0], pwconfig.config(), False)
        self.assertEqual([e['kind'] for e in wake.call_args.args[2]], ['user-decision'])
        self.assertEqual(registry()[0]['decisions'], [])

    def test_what_counts_as_a_question(self):
        cfg = pwconfig.config()
        for text in ('Pourquoi ce choix ?', 'Peux-tu valider cette approche', "Je suis d'accord avec la modif", 'Do you agree with this?'):
            self.assertTrue(pwevents.needs_decision(text, cfg), text)
        self.assertFalse(pwevents.needs_decision('Renomme cette variable en count.', cfg))
        self.assertTrue(pwevents.needs_decision('x', {**cfg, 'questionPatterns': ['(']}) is False)  # a broken pattern is ignored

    def test_default_wakes_claude(self):
        wake, _ = self.run_cycle({}, [self.COMMENT])
        self.assertEqual(wake.call_count, 1)
        self.assertEqual(registry()[0]['lastComment'], 7)

    def test_notify_only_tells_the_user_and_does_not_wake_claude(self):
        wake, notify = self.run_cycle({'handling': {'comment': 'notify'}}, [self.COMMENT])
        self.assertEqual(wake.call_count, 0)
        self.assertIn('Commentaires des relecteurs', notify.call_args.args[0])
        self.assertEqual(registry()[0]['lastComment'], 7, 'the comment counts as seen')

    def test_suspension_notifies_instead_of_waking_claude_without_changing_preferences(self):
        wake, notify = self.run_cycle({}, [self.COMMENT, self.SONAR], suspension={'kind': 'usage'})
        self.assertEqual(wake.call_count, 0)
        self.assertEqual(notify.call_count, 2)
        self.assertIn('Commentaires des relecteurs', notify.call_args_list[0].args[0])
        self.assertIn('Sonar', notify.call_args_list[1].args[0])
        self.assertEqual(pwconfig.config()['handling']['comment'], 'fix')
        self.assertEqual((registry()[0]['lastComment'], registry()[0]['seenStatuses']), (7, []))

    def test_quota_suspension_found_before_wake_notifies_the_pending_events(self):
        wake, notify = self.run_cycle({}, [self.COMMENT], suspension_after_gate=True)
        self.assertEqual(wake.call_count, 0)
        self.assertEqual(notify.call_count, 1)
        self.assertIn('Commentaires des relecteurs', notify.call_args.args[0])
        self.assertEqual(registry()[0]['lastComment'], 7)

    def test_ignore_drops_the_event_silently(self):
        wake, notify = self.run_cycle({'handling': {'sonar': 'ignore'}}, [self.SONAR])
        self.assertEqual((wake.call_count, notify.call_count), (0, 0))

    def test_only_the_events_asked_for_reach_claude(self):
        wake, _ = self.run_cycle({'handling': {'comment': 'notify'}}, [self.COMMENT, self.SONAR])
        sent = wake.call_args.args[2]
        self.assertEqual([e['kind'] for e in sent if e['actionable']], ['sonar-failed'])


class SettleThreadsTest(unittest.TestCase):
    """The report says it replied and resolved: Bitbucket is asked, and what can be fixed is fixed."""

    ME = {'uuid': '{me}'}
    BOT = {'uuid': '{bot}', 'type': 'app_user'}
    PR = {'author': {'uuid': '{me}'}}
    EVENT = {'kind': 'review-bot', 'actionable': True, 'text': 'Comment 5', 'comment': {'id': 5, 'author': 'Bot', 'place': '', 'text': 'x'}}
    REPORT = "J'ai répondu puis résolu le fil.\nPR-WATCH: OK"

    def run_settle(self, comments, events=None, report=REPORT, posts=(True, '')):
        reset_config()
        with mock.patch.object(pwbitbucket, 'all_comments', return_value=comments), mock.patch.object(pwbitbucket, 'api_post', return_value=posts) as post:
            return pwwake.settle_threads(entry(1), self.PR, events or [self.EVENT], report), post

    def bot(self, id, **extra):
        return {'id': id, 'user': self.BOT, **extra}

    def mine(self, id, parent):
        return {'id': id, 'user': self.ME, 'parent': {'id': parent}}

    def test_an_open_thread_with_a_reply_that_the_report_calls_resolved_is_resolved(self):
        (notes, failed), post = self.run_settle([self.bot(5), self.mine(6, 5)])
        self.assertFalse(failed)
        self.assertIn('a résolu le fil du commentaire 5', notes[0])
        self.assertTrue(post.call_args.args[0].endswith('/comments/5/resolve'))

    def test_an_empty_resolution_object_means_resolved(self):
        (notes, failed), post = self.run_settle([self.bot(5, resolution={}), self.mine(6, 5)])
        self.assertEqual((notes, failed), ([], False))
        post.assert_not_called()

    def test_a_reply_the_report_claims_but_bitbucket_does_not_show_is_reported_and_nothing_is_resolved(self):
        (notes, failed), post = self.run_settle([self.bot(5)])
        self.assertTrue(failed)
        self.assertIn("aucune réponse", notes[0])
        post.assert_not_called()

    def test_a_refused_resolution_is_reported(self):
        (notes, failed), _ = self.run_settle([self.bot(5), self.mine(6, 5)], posts=(False, '403'))
        self.assertTrue(failed)
        self.assertIn('encore ouvert', notes[0])

    def test_a_reply_deep_in_the_thread_counts_for_its_root(self):
        (notes, failed), post = self.run_settle([self.bot(5), {'id': 6, 'user': self.BOT, 'parent': {'id': 5}}, self.mine(7, 6)])
        self.assertTrue(post.call_args.args[0].endswith('/comments/5/resolve'))

    def test_the_inline_comments_of_a_review_are_checked_too(self):
        event = {**self.EVENT, 'threads': [4]}
        (notes, failed), _ = self.run_settle([self.bot(4), self.bot(5, resolution={}), self.mine(6, 5)], events=[event])
        self.assertTrue(failed)
        self.assertIn('commentaire 4', notes[0])

    def test_nothing_is_checked_when_the_report_claims_nothing(self):
        (notes, failed), post = self.run_settle([self.bot(5)], report='Laissé ouvert.\nPR-WATCH: OK')
        self.assertEqual((notes, failed), ([], False))
        post.assert_not_called()

    def test_a_question_is_never_resolved(self):
        (notes, _), post = self.run_settle([self.bot(5), self.mine(6, 5)], events=[{**self.EVENT, 'kind': 'question'}])
        self.assertEqual(notes, [])
        post.assert_not_called()


class ReviewThreadsTest(unittest.TestCase):
    def comment(self, id, at, **extra):
        return {'id': id, 'user': {'uuid': '{bot}'}, 'created_on': f'2026-11-18T11:{at}:00.000000+00:00', **extra}

    def test_the_open_inline_comments_posted_with_the_summary_come_with_it(self):
        summary = self.comment(10, '30')
        inline = self.comment(9, '29', inline={'path': 'a.html', 'to': 3}, content={'raw': 'Minor'})
        others = [
            inline,
            self.comment(8, '29', inline={'path': 'b.html', 'to': 1}, resolution={}),        # already resolved
            self.comment(7, '29', inline={'path': 'c.html', 'to': 1}, parent={'id': 1}),      # a reply
            self.comment(6, '50', inline={'path': 'd.html', 'to': 1}),                         # another review, much later
            {**self.comment(5, '29', inline={'path': 'e.html', 'to': 1}), 'user': {'uuid': '{someone}'}},
            self.comment(4, '29'),                                                              # not inline
        ]
        found = pwevents.review_threads(summary, [summary, *others])
        self.assertEqual([t['id'] for t in found], [9])
        self.assertEqual(found[0]['place'], 'a.html:3')


class ReadinessTest(unittest.TestCase):
    HEAD = 'abc123def456'

    def setUp(self):
        pwmerging._APPROVALS.clear()

    def pipelines(self, *runs):
        return {'values': [{'build_number': n, 'state': {'name': state, 'result': {'name': result}} if result else {'name': state}, 'target': {'commit': {'hash': self.HEAD + '0' * 28}, 'selector': {'pattern': pattern}}} for n, state, result, pattern in runs]}

    def blockers(self, approvals=1, tasks=0, state='', runs=()):
        pr = {'participants': [{'approved': True, 'state': state} for _ in range(approvals)], 'task_count': tasks, 'source': {'commit': {'hash': self.HEAD}}, 'destination': {'branch': {'name': 'develop'}}}
        with mock.patch.object(pwbitbucket, 'api', return_value=self.pipelines(*runs)):
            return pwmerging.merge_blockers({'repo': 'r', 'pr': 1, 'branch': 'b'}, pr, pwconfig.config())

    GREEN = ((1, 'COMPLETED', 'SUCCESSFUL', '**'),)

    def test_the_approvals_of_the_repository_replace_the_setting(self):
        restrictions = {'values': [
            {'kind': 'require_approvals_to_merge', 'value': 2, 'branch_match_kind': 'glob', 'pattern': 'develop'},
            {'kind': 'require_approvals_to_merge', 'value': 3, 'branch_match_kind': 'glob', 'pattern': 'main'},
        ]}
        pr = {'destination': {'branch': {'name': 'develop'}}}
        with mock.patch.object(pwbitbucket, 'api', return_value=restrictions) as api:
            self.assertEqual(pwmerging.required_approvals({'repo': 'r'}, pr, pwconfig.config()), 2)
            pwmerging.required_approvals({'repo': 'r'}, pr, pwconfig.config())
        api.assert_called_once()

    def test_the_approvals_setting_stands_when_the_restrictions_cannot_be_read_or_do_not_match(self):
        pr = {'destination': {'branch': {'name': 'develop'}}}
        for answer in (None, {'values': [{'kind': 'require_approvals_to_merge', 'value': 3, 'branch_match_kind': 'glob', 'pattern': 'main'}]}):
            pwmerging._APPROVALS.clear()
            with mock.patch.object(pwbitbucket, 'api', return_value=answer):
                self.assertEqual(pwmerging.required_approvals({'repo': 'r'}, pr, pwconfig.config()), pwconfig.config()['minApprovals'])

    def test_ready(self):
        self.assertEqual(self.blockers(runs=self.GREEN), [])

    def test_blockers(self):
        self.assertIn('approbations 0/1', self.blockers(approvals=0, runs=self.GREEN))
        self.assertIn('modifications demandées', self.blockers(state='changes_requested', runs=self.GREEN))
        self.assertIn('2 tâche(s) ouverte(s)', self.blockers(tasks=2, runs=self.GREEN))
        self.assertIn('aucun pipeline sur le dernier commit', self.blockers(runs=()))
        self.assertIn('pipeline #1 en cours', self.blockers(runs=((1, 'IN_PROGRESS', None, '**'),)))

    def test_a_failed_run_that_was_restarted_successfully_no_longer_blocks(self):
        self.assertEqual(self.blockers(runs=((1, 'COMPLETED', 'FAILED', '**'), (2, 'COMPLETED', 'SUCCESSFUL', '**'))), [])
        self.assertEqual(self.blockers(runs=((2, 'COMPLETED', 'FAILED', '**'), (1, 'COMPLETED', 'SUCCESSFUL', '**'))), ['pipeline #2 en échec'])

    def test_the_announcement_comes_once_and_follows_the_setting(self):
        for setting, expected in (('notify', 1), ('ignore', 0)):
            with self.subTest(setting=setting):
                set_registry([entry(1, settings={'handling': {'ready': setting}})])
                with mock.patch.object(pwmerging, 'merge_blockers', return_value=[]), mock.patch.object(pwwake, 'notify') as notify:
                    for _ in range(3):
                        pwmerging.sync_ready(registry()[0], {'title': 't'}, pwconfig.config(), False)
                self.assertEqual(notify.call_count, expected)
                self.assertTrue(registry()[0]['ready'])


class DesktopTest(unittest.TestCase):
    def test_exec_quoting(self):
        self.assertEqual(pwdesktop._exec_quote('plain/path-1.2'), 'plain/path-1.2')
        self.assertEqual(pwdesktop._exec_quote('my dir/$x'), '"my dir/\\$x"')

    def test_the_application_entry(self):
        data = Path(os.environ['HOME']) / 'xdg'
        with mock.patch.dict(os.environ, {'XDG_DATA_HOME': str(data)}), mock.patch.object(pwhost, 'SYSTEM', 'linux'):
            pwdesktop.install_desktop()
        entry_file = data / 'applications' / 'org.prwatch.PrWatchTray.desktop'
        text = entry_file.read_text()
        self.assertIn('Name=PR Watch', text)
        # the window is the web dashboard in Chrome: the dock finds the application by the class Chrome gives that window
        self.assertIn(f'StartupWMClass={pwwindow.WEB_WINDOW_CLASS}', text)
        self.assertIn('-m prwatch ui', text)
        self.assertNotIn('-m prwatch tray', text)
        self.assertTrue((data / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'org.prwatch.PrWatchTray.svg').exists())

    def test_registering_the_application_in_the_background_does_not_print_its_path(self):
        data = Path(os.environ['HOME']) / 'xdg'
        with (mock.patch.dict(os.environ, {'XDG_DATA_HOME': str(data)}),
              mock.patch.object(pwhost, 'SYSTEM', 'linux'),
              mock.patch('builtins.print') as print_output):
            pwdesktop.install_desktop(announce=False)
        print_output.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class TrayEntryTest(unittest.TestCase):
    def test_a_missing_gtk_gives_a_clear_message(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.dict('sys.modules', {'prwatch.tray.app': None}):
            with self.assertRaises(SystemExit) as raised:
                pwservice.tray_main()
        self.assertIn('GTK 3 est introuvable', str(raised.exception))
        self.assertIn('pr-watch ui', str(raised.exception))

    def test_other_systems_are_pointed_to_the_web_dashboard(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'darwin'):
            with self.assertRaises(SystemExit) as raised:
                pwservice.tray_main()
        self.assertIn('prwatch ui', str(raised.exception))


class OutOfTokensTest(unittest.TestCase):
    def tearDown(self):
        pwstore.resume()

    def test_the_message_of_a_used_up_session_is_recognised(self):
        self.assertIn('usage limit', pwwake.out_of_tokens('Claude AI usage limit reached|1795050000'))
        self.assertEqual(pwwake.out_of_tokens('x\nCredit balance is too low\ny'), 'Credit balance is too low')
        self.assertEqual(pwwake.out_of_tokens('PR-WATCH: OK\nrien à signaler'), '')

    def test_a_failed_wake_with_that_message_raises(self):
        done = mock.Mock(returncode=1, stdout='Claude AI usage limit reached', stderr='')
        with mock.patch.object(pwwake.subprocess, 'run', return_value=done):
            with self.assertRaises(pwwake.OutOfTokens):
                pwwake.wake_claude({'repo': 'r', 'pr': 1, 'branch': 'b', 'sessionId': 's', 'sessionCwd': '.', 'workDir': '.'},
                                   {'title': 't', 'destination': {'branch': {'name': 'main'}}}, [{'kind': 'x', 'text': 't'}], pwconfig.config(), False)

    def test_suspension_is_lifted_by_resume(self):
        self.assertIsNone(pwstore.suspension())
        pwstore.suspend('usage limit reached')
        self.assertEqual(pwstore.suspension()['reason'], 'usage limit reached')
        self.assertTrue(pwstore.resume())
        self.assertIsNone(pwstore.suspension())
        self.assertFalse(pwstore.resume())


class UsageGateTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        self.cfg = pwconfig.config()
        pwstore.resume()
        pwstore.USAGE_OVERRIDE.unlink(missing_ok=True)

    def tearDown(self):
        pwstore.resume()
        pwstore.USAGE_OVERRIDE.unlink(missing_ok=True)

    @staticmethod
    def usage(session, week, back=None):
        back = back or time.time() + 3600
        return {'session': {'used': session, 'resetsAt': back}, 'week': {'used': week, 'resetsAt': back + 86400}}

    def gate(self, usage):
        with mock.patch.object(pwcycle.pwusage, 'read', return_value=(usage, time.time())), mock.patch.object(pwwake, 'notify') as notify:
            pwcycle.usage_gate(self.cfg)
        return notify

    EVENT = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'unifiedWindows': {
        'five_hour': {'utilization': 0.11, 'resetsAt': 1795044600, 'status': 'allowed'}, 'seven_day': {'utilization': 0.73, 'resetsAt': 1795237200, 'status': 'allowed'}}}}

    def test_the_quotas_reported_by_the_cli_are_read(self):
        parsed = pwcycle.pwusage.parse_event(self.EVENT)
        self.assertEqual((parsed['session']['used'], parsed['week']['used'], parsed['week']['resetsAt']), (11.0, 73.0, 1795237200.0))
        self.assertIsNone(pwcycle.pwusage.parse_event({'type': 'rate_limit_event', 'rate_limit_info': {}}))

    def test_the_stream_gives_the_last_quotas_and_the_answer(self):
        stream = '\n'.join(json.dumps(e) for e in ({'type': 'system'}, self.EVENT, {'type': 'result', 'result': 'fait\nPR-WATCH: OK', 'is_error': False})) + '\nnot json'
        usage, result = pwcycle.pwusage.parse_stream(stream)
        self.assertEqual((usage['session']['used'], result['result']), (11.0, 'fait\nPR-WATCH: OK'))
        self.assertEqual(pwcycle.pwusage.parse_stream('rien'), (None, None))

    def test_a_suspension_for_usage_ends_when_its_window_starts_over(self):
        pwstore.suspend('x', 'usage', time.time() - 5)
        self.gate(None)
        self.assertIsNone(pwstore.suspension())

    def test_room_left_changes_nothing(self):
        self.assertEqual(self.gate(self.usage(50, 72)).call_count, 0)
        self.assertIsNone(pwstore.suspension())

    def test_a_window_almost_used_up_suspends_and_says_so_once(self):
        self.assertEqual(self.gate(self.usage(96, 40)).call_count, 1)
        suspension = pwstore.suspension()
        self.assertEqual(suspension['kind'], 'usage')
        self.assertIn('5 h', suspension['reason'])
        self.assertEqual(self.gate(self.usage(97, 40)).call_count, 0)

    def test_the_week_counts_too(self):
        self.gate(self.usage(10, 99))
        self.assertIn('semaine', pwstore.suspension()['reason'])

    def test_it_starts_again_when_the_window_starts_over(self):
        self.gate(self.usage(99, 40))
        self.gate(self.usage(2, 40))
        self.assertIsNone(pwstore.suspension())

    def test_a_suspension_for_lack_of_tokens_is_not_lifted_by_the_check(self):
        pwstore.suspend('usage limit reached')
        self.gate(self.usage(2, 40))
        self.assertIsNotNone(pwstore.suspension())

    def test_the_user_can_go_on_anyway_until_the_window_starts_over(self):
        self.gate(self.usage(99, 40))
        self.assertTrue(pwstore.resume())
        self.gate(self.usage(99, 40))
        self.assertIsNone(pwstore.suspension())

    def test_unknown_usage_and_a_guard_turned_off_never_stop_anything(self):
        self.gate(None)
        self.assertIsNone(pwstore.suspension())
        self.cfg['usageGuard'] = False
        self.gate(self.usage(100, 100))
        self.assertIsNone(pwstore.suspension())

    def test_the_threshold_is_the_one_set(self):
        self.cfg['usageSessionMarginPercent'] = 20
        self.gate(self.usage(85, 10))
        self.assertIsNotNone(pwstore.suspension())

    def test_each_window_has_its_own_threshold(self):
        self.cfg['usageSessionMarginPercent'] = 20
        self.cfg['usageWeekMarginPercent'] = 1
        self.gate(self.usage(10, 90))  # 10 % left of the week, above its 1 % threshold
        self.assertIsNone(pwstore.suspension())
        self.gate(self.usage(81, 10))  # 19 % left of the session, below its 20 %
        self.assertIn('5 h', pwstore.suspension()['reason'])

    def test_the_single_threshold_of_before_applies_to_both(self):
        pwstore.CONFIG.write_text(json.dumps({'usageMarginPercent': 12}), encoding='utf-8')
        cfg = pwconfig.config()
        self.assertEqual((cfg['usageSessionMarginPercent'], cfg['usageWeekMarginPercent']), (12, 12))
        reset_config()


class ScheduleTest(unittest.TestCase):
    SCHEDULE = {'enabled': True, 'days': [0, 1, 2, 3, 4], 'start': '08:00', 'end': '19:00'}

    def state(self, when, schedule=None):
        return pwconfig.schedule_state({'schedule': schedule or self.SCHEDULE}, datetime(2026, 11, *when))  # 16 Nov 2026 is a Monday

    def test_without_a_schedule_it_always_works(self):
        self.assertEqual(pwconfig.schedule_state({'schedule': {**self.SCHEDULE, 'enabled': False}}, datetime(2026, 11, 22, 3, 0)), (True, ''))
        self.assertEqual(pwconfig.schedule_state({}, datetime(2026, 11, 22, 3, 0))[0], True)

    def test_inside_and_outside_the_hours(self):
        self.assertEqual(self.state((16, 8, 0)), (True, ''))
        self.assertEqual(self.state((16, 18, 59)), (True, ''))
        self.assertEqual(self.state((16, 19, 0)), (False, 'demain 08:00'))
        self.assertEqual(self.state((16, 7, 0)), (False, 'aujourd’hui 08:00'))

    def test_the_weekend_waits_for_monday(self):
        self.assertEqual(self.state((21, 10, 0)), (False, 'lundi 08:00'))
        self.assertEqual(self.state((20, 20, 0)), (False, 'lundi 08:00'))

    def test_schedule_changes_wake_the_daemon_at_the_next_boundary(self):
        cfg = {'schedule': self.SCHEDULE}
        self.assertEqual(pwconfig.seconds_until_schedule_change(cfg, datetime(2026, 11, 16, 7, 0)), 3600)
        self.assertEqual(pwconfig.seconds_until_schedule_change(cfg, datetime(2026, 11, 16, 8, 0)), 11 * 3600)
        self.assertEqual(pwconfig.seconds_until_schedule_change(cfg, datetime(2026, 11, 21, 10, 0)), 2 * 24 * 3600 - 2 * 3600)
        self.assertIsNone(pwconfig.seconds_until_schedule_change({'schedule': {**self.SCHEDULE, 'enabled': False}}))

    def test_schedule_overrides_run_until_the_next_matching_boundary(self):
        cfg = {'schedule': self.SCHEDULE}
        now = datetime(2099, 11, 16, 7, 0)
        end_day = next(now + timedelta(days=ahead) for ahead in range(8) if (now + timedelta(days=ahead)).weekday() in self.SCHEDULE['days'])
        end = end_day.replace(hour=19, minute=0)
        pwstore.set_schedule_override('active', end.timestamp())
        self.addCleanup(pwstore.clear_schedule_override)
        self.assertEqual(pwconfig.schedule_effective_state(cfg, now), (True, '', 'active'))
        self.assertEqual(pwconfig.next_schedule_boundary(cfg, 'active', now), end.timestamp())
        self.assertEqual(pwconfig.next_schedule_boundary(cfg, 'paused', datetime(2026, 11, 16, 10, 0)),
                         datetime(2026, 11, 17, 8, 0).timestamp())

        pwstore.set_schedule_override('paused', datetime(2099, 11, 20, 12, 0).timestamp())
        self.assertFalse(pwconfig.schedule_effective_state(cfg, datetime(2099, 11, 16, 10, 0))[0])
        monday_cfg = {'schedule': {**self.SCHEDULE, 'start': '09:00'}}
        self.assertEqual(
            pwconfig.schedule_effective_state(monday_cfg, datetime(2026, 11, 20, 10, 0)),
            (False, 'lundi 09:00', 'paused'),
        )

    def test_a_cycle_does_not_start_another_pull_request_after_closing_time(self):
        set_registry([entry(1), entry(2)])
        with (mock.patch.object(pwconfig, 'schedule_state', side_effect=[(True, ''), (True, ''), (False, '')]),
              mock.patch.object(pwcycle, 'usage_gate'),
              mock.patch.object(pwcycle.pwmerging, 'refresh_ticket_waits'),
              mock.patch.object(pwcycle.pwmerging, 'purge_done'),
              mock.patch.object(pwcycle, 'handle') as handle):
            pwcycle.cycle()
        self.assertEqual(handle.call_count, 1)

    def test_a_schedule_that_makes_no_sense_is_refused(self):
        for bad in ({**SCHEDULE_BAD, 'start': '19:00', 'end': '08:00'}, {**SCHEDULE_BAD, 'days': []}, {**SCHEDULE_BAD, 'start': '25:00'}, {**SCHEDULE_BAD, 'days': [7]}, 'x'):
            with self.assertRaises(ValueError):
                pwconfig.validate_schedule(bad)

    def test_a_broken_stored_schedule_does_not_stop_the_daemon(self):
        self.assertEqual(pwconfig.schedule_state({'schedule': 'x'}, datetime(2026, 11, 22, 3, 0))[0], True)


SCHEDULE_BAD = ScheduleTest.SCHEDULE


class UsageCacheTest(unittest.TestCase):
    def setUp(self):
        (pwstore.DIR / pwcycle.pwusage.CACHE_FILE).unlink(missing_ok=True)

    tearDown = setUp

    USAGE = {'session': {'used': 8.0, 'resetsAt': 1795044600.0}, 'week': {'used': 73.0, 'resetsAt': 1795237200.0}}

    def test_nothing_known_stays_unknown(self):
        self.assertEqual(pwcycle.pwusage.read(), (None, 0.0))

    def test_the_last_measure_is_kept_with_its_age(self):
        pwcycle.pwusage.record(self.USAGE)
        value, at = pwcycle.pwusage.read()
        self.assertEqual(value['week']['used'], 73.0)
        self.assertLess(time.time() - at, 5)

    def test_a_fresh_measure_is_not_asked_again_but_an_old_one_is(self):
        pwcycle.pwusage.record(self.USAGE)
        with mock.patch.object(pwcycle.pwusage, 'probe', return_value=None) as probe:
            self.assertEqual(pwcycle.pwusage.current('claude')['session']['used'], 8.0)
            self.assertEqual(probe.call_count, 0)
            pwcycle.pwusage.current('claude', max_age=-1)
            self.assertEqual(probe.call_count, 1)

    def test_a_measure_too_old_is_not_shown(self):
        pwcycle.pwusage.record(self.USAGE)
        with mock.patch.object(pwcycle.pwusage.time, 'time', return_value=time.time() + 7 * 3600):
            self.assertEqual(pwcycle.pwusage.read(), (None, 0.0))


class AutoMergeTest(unittest.TestCase):
    PULL = {**PR, 'author': {'uuid': 'me'}, 'source': {'commit': {'hash': 'abc123'}}}

    def setUp(self):
        reset_config()
        self.cfg = pwconfig.config()

    @staticmethod
    def comment(number, text, user=None, **extra):
        return {'id': number, 'content': {'raw': text}, 'user': user or {'uuid': 'reviewer', 'display_name': 'Alice'}, **extra}

    def run_auto_merge(self, entry_fields=None, global_mode='off', comments=(), merged=(True, 'PR mergée.')):
        self.cfg['autoMerge'] = global_mode
        set_registry([entry(1, ready=True, **(entry_fields or {}))])
        with mock.patch.object(pwbitbucket, 'api', return_value={'values': list(comments)}), \
                mock.patch.object(pwmerging, 'merge_pr', return_value=merged) as merge, mock.patch.object(pwwake, 'notify') as notify:
            done = pwmerging.auto_merge(registry()[0], self.PULL, self.cfg, False)
        return done, merge, notify

    def test_it_is_off_by_default(self):
        self.assertEqual(self.cfg['autoMerge'], 'off')
        done, merge, _ = self.run_auto_merge()
        self.assertEqual((done, merge.call_count), (False, 0))

    def test_ready_merges_as_soon_as_the_pull_request_can_be_merged(self):
        done, merge, notify = self.run_auto_merge(global_mode='ready')
        self.assertTrue(done)
        self.assertEqual(merge.call_args.args[1], 'rebase_fast_forward')
        self.assertIn('automatiquement', notify.call_args.args[0])

    def test_it_never_merges_a_pull_request_that_is_not_ready(self):
        self.cfg['autoMerge'] = 'ready'
        set_registry([entry(1, ready=False)])
        with mock.patch.object(pwmerging, 'merge_pr') as merge:
            self.assertFalse(pwmerging.auto_merge(registry()[0], self.PULL, self.cfg, False))
        self.assertEqual(merge.call_count, 0)

    def test_a_pull_request_can_override_the_global_choice_both_ways(self):
        self.assertEqual(self.run_auto_merge({'settings': {'autoMerge': 'off'}}, 'ready')[1].call_count, 0)
        self.assertEqual(self.run_auto_merge({'settings': {'autoMerge': 'ready'}}, 'off')[1].call_count, 1)

    def test_the_strategy_set_in_bitbucket_is_used(self):
        _, merge, _ = self.run_auto_merge({'mergeDefault': 'squash', 'mergeAllowed': ['squash', 'merge_commit']}, 'ready')
        self.assertEqual(merge.call_args.args[1], 'squash')
        self.assertEqual(pwmerging.merge_strategy_for({'mergeDefault': 'fast_forward', 'mergeAllowed': ['squash']}), 'squash')

    def test_the_user_being_awaited_always_stops_it(self):
        for fields in ({'pendingQuestions': [{'id': 5}]}, {'needsAttention': 'à regarder'}):
            done, merge, _ = self.run_auto_merge(fields, 'ready')
            self.assertEqual((done, merge.call_count), (False, 0))

    def test_a_refused_merge_is_not_tried_again_for_the_same_commit(self):
        self.run_auto_merge(global_mode='ready', merged=(False, 'refusé'))
        self.assertEqual(registry()[0]['autoMergeFailedFor'], 'abc123')
        done, merge, _ = self.run_auto_merge({'autoMergeFailedFor': 'abc123'}, 'ready')
        self.assertEqual((done, merge.call_count), (False, 0))

    def test_resolved_waits_for_the_open_comments_and_says_so(self):
        done, merge, _ = self.run_auto_merge(global_mode='resolved', comments=[self.comment(7, 'Cette méthode devrait être privée'), self.comment(8, 'Ce nom est trompeur')])
        self.assertEqual((done, merge.call_count), (False, 0))
        self.assertEqual(registry()[0]['autoMergeNote'], '2 commentaires non résolus')

    def test_resolved_merges_when_nothing_is_left_to_handle(self):
        comments = [
            self.comment(1, 'Cette méthode devrait être privée', resolution={'type': 'x'}),  # resolved
            self.comment(2, 'LGTM 👍'), self.comment(3, 'Super boulot, merci !'),  # compliments
            self.comment(4, 'Rien de neuf', user={'uuid': 'me'}),  # the author
            self.comment(5, 'Une réponse', parent={'id': 1}),  # a reply, not a thread
            self.comment(6, 'Supprimé', deleted=True),
            self.comment(9, '## 🤖 Revue automatique\n\nRien d\'important à signaler', user={'type': 'app_user', 'display_name': 'Bitbucket Pipelines'}),
        ]
        done, merge, _ = self.run_auto_merge(global_mode='resolved', comments=comments)
        self.assertEqual((done, merge.call_count), (True, 1))

    def test_a_compliment_that_still_asks_for_something_counts(self):
        for text in ('Super, mais renomme cette variable', 'Bravo ! Tu peux ajouter un test ?', 'LGTM sauf la ligne 12', 'Merci de corriger'):
            self.assertFalse(pwmerging.is_positive(text, self.cfg), text)
        for text in ('LGTM', 'Parfait 👌', 'Bien joué, merci', 'ok'):
            self.assertTrue(pwmerging.is_positive(text, self.cfg), text)

    def test_the_automatic_review_with_findings_counts_until_it_is_resolved(self):
        review = self.comment(9, '## 🤖 Revue automatique\n\n| 🔴 | 1 |', user={'type': 'app_user', 'display_name': 'Bitbucket Pipelines'})
        self.assertEqual(self.run_auto_merge(global_mode='resolved', comments=[review])[1].call_count, 0)
        self.assertEqual(self.run_auto_merge(global_mode='resolved', comments=[{**review, 'resolution': {}}])[1].call_count, 1)

    def test_the_choice_is_validated(self):
        self.assertEqual(pwconfig.validate_auto_merge('resolved'), 'resolved')
        self.assertIsNone(pwconfig.validate_auto_merge(None, partial=True))
        for bad in ('maybe', None, True):
            with self.assertRaises(ValueError):
                pwconfig.validate_auto_merge(bad)
        self.assertFalse(pwoptions.set_options({'autoMerge': 'maybe'})['ok'])
        self.assertTrue(pwoptions.set_options({'autoMerge': 'ready'})['ok'])
        self.assertEqual(pwconfig.config()['autoMerge'], 'ready')
        self.assertFalse(pwpr.pr_settings('fake-repo', 1, {'autoMerge': 'maybe'})['ok'])
        reset_config()

    def test_the_choice_of_a_pull_request_is_stored_and_reset(self):
        set_registry([entry(1)])
        self.assertTrue(pwpr.pr_settings('fake-repo', 1, {'autoMerge': 'resolved'})['ok'])
        self.assertEqual(registry()[0]['settings']['autoMerge'], 'resolved')
        self.assertTrue(pwpr.pr_settings('fake-repo', 1, {'autoMerge': None})['ok'])
        self.assertNotIn('autoMerge', registry()[0]['settings'])


class TicketWaitTest(unittest.TestCase):
    def setUp(self):
        reset_config()
        self.cfg = pwconfig.config()
        pwmerging._TICKET_CHECKS.clear()

    @staticmethod
    def merged(pr, **fields):
        return entry(pr, branch=f'feature/PROJ-100-p{pr}', state='done', doneReason='MERGED', doneAt=time.time() - 7200, **fields)

    def mark(self, entries, index=0, status='En cours', error=None):
        set_registry(entries)
        side = {'side_effect': error} if error else {'return_value': status}
        with mock.patch.object(pwmerging.pwjira, 'status', **side):
            pwmerging.mark_merged(registry()[index], self.cfg)
        return registry()[index]

    def test_a_merged_pull_request_remembers_the_state_of_its_ticket(self):
        done = self.mark([entry(1, branch='feature/PROJ-100-x')])
        self.assertEqual((done['state'], done['doneReason'], done['ticketWatch']), ('done', 'MERGED', {'key': 'PROJ-100', 'status': 'En cours'}))
        self.assertTrue(pwmerging.ticket_waiting(done, self.cfg))

    def test_only_the_last_pull_request_of_a_ticket_waits(self):
        first = self.mark([entry(1, branch='feature/PROJ-100-a'), entry(2, branch='feature/PROJ-100-b')])
        self.assertNotIn('ticketWatch', first)
        self.assertFalse(pwmerging.ticket_waiting(first, self.cfg))
        self.assertEqual(pwmerging.other_open_prs(registry()[1]), [])  # the first one is finished now
        last = self.mark([first, entry(2, branch='feature/PROJ-100-b')], index=1)
        self.assertIn('ticketWatch', last)

    def test_other_tickets_and_finished_pull_requests_do_not_count(self):
        set_registry([entry(1, branch='feature/PROJ-100-a'), entry(2, branch='feature/PROJ-200-b'), self.merged(3)])
        self.assertEqual(pwmerging.other_open_prs(registry()[0]), [])

    def test_no_waiting_without_a_ticket_with_the_option_off_or_when_jira_fails(self):
        self.assertNotIn('ticketWatch', self.mark([entry(1, branch='feature/sans-ticket', title='rien')]))
        self.cfg['keepUntilTicketValidated'] = False
        self.assertNotIn('ticketWatch', self.mark([entry(1, branch='feature/PROJ-100-x')]))
        self.cfg['keepUntilTicketValidated'] = True
        self.assertNotIn('ticketWatch', self.mark([entry(1, branch='feature/PROJ-100-x')], error=pwmerging.pwjira.JiraError('down')))

    def test_the_option_is_on_by_default(self):
        self.assertTrue(self.cfg['keepUntilTicketValidated'])

    def waiting(self):
        return self.merged(1, ticketWatch={'key': 'PROJ-100', 'status': 'En cours'}, ticketValidated=False)

    def refresh(self, status=None, error=None):
        side = {'side_effect': error} if error else {'return_value': status}
        with mock.patch.object(pwmerging.pwjira, 'status', **side):
            pwmerging.refresh_ticket_waits(self.cfg)
        pwmerging.purge_done(self.cfg)
        return registry()

    def test_it_stays_while_the_ticket_has_not_changed_however_long_it_takes(self):
        set_registry([self.waiting()])
        self.assertEqual(len(self.refresh('En cours')), 1)
        self.assertFalse(registry()[0]['ticketValidated'])

    def test_it_stays_when_jira_cannot_be_read(self):
        set_registry([self.waiting()])
        self.assertEqual(len(self.refresh(error=pwmerging.pwjira.JiraError('down'))), 1)

    def test_it_is_released_when_the_ticket_changes_and_leaves_after_the_usual_delay(self):
        set_registry([self.waiting()])
        self.assertEqual(len(self.refresh('A tester')), 1)  # released now, the delay counts from now
        self.assertTrue(registry()[0]['ticketValidated'])
        self.assertLess(time.time() - registry()[0]['doneAt'], 5)
        data = registry()
        data[0]['doneAt'] = time.time() - 7200
        set_registry(data)
        pwmerging.purge_done(self.cfg)
        self.assertEqual(registry(), [])

    def test_with_the_option_off_a_waiting_pull_request_leaves_as_before(self):
        set_registry([self.waiting()])
        self.cfg['keepUntilTicketValidated'] = False
        pwmerging.purge_done(self.cfg)
        self.assertEqual(registry(), [])

    def test_the_card_says_what_it_waits_for(self):
        waiting = self.waiting()
        status = pwstatus.status(waiting, None, self.cfg)
        self.assertEqual((status['kind'], status['label']), ('ticket-wait', 'En attente de validation du ticket'))
        self.assertIn('PROJ-100', status['tip'])
        self.assertIsNone(pwstatus.remove_in(waiting, self.cfg))
        self.assertEqual(pwstatus.status({**waiting, 'ticketValidated': True}, None, self.cfg)['kind'], 'done')

    def test_moving_the_ticket_from_the_application_releases_every_pull_request_waiting_for_it(self):
        set_registry([self.waiting(), self.merged(2, ticketWatch={'key': 'PROJ-100', 'status': 'En cours'}, ticketValidated=False), self.merged(3)])
        with mock.patch.object(pwtickets.pwjira, 'move', return_value='Ticket déplacé.'):
            self.assertTrue(pwtickets.ticket_transition('fake-repo', 1, '31')['ok'])
        self.assertEqual([e.get('ticketValidated') for e in registry()], [True, True, None])

    def test_the_merge_says_when_the_ticket_has_other_pull_requests_in_progress(self):
        set_registry([entry(1, branch='feature/PROJ-100-a'), entry(2, branch='feature/PROJ-100-b')])
        with mock.patch.object(pwmerging, 'merge_pr', return_value=(True, 'PR mergée.')):
            result = pwpr.merge('fake-repo', 1, 'squash')
        self.assertEqual(result['otherPrs'], ['fake-repo#2'])
