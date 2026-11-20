import io
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from prwatch.system import desktop as pwdesktop
from prwatch.system import claude as pwclaude
from prwatch.system import editors as pweditors
from prwatch.system import host as pwhost
from prwatch.system import notifications as pwnotifications
from prwatch.system import opening as pwopening
from prwatch.system import processes as pwprocesses
from prwatch.system import terminal as pwterminal
from prwatch.system import window as pwwindow

SESSION = '65f47066-1c3d-445a-b2b1-8106dd96a66f'
ENTRY = {'sessionId': SESSION, 'sessionCwd': '/work/proj'}


class MacTableTest(unittest.TestCase):
    COMM = """  100     1 /Applications/IntelliJ IDEA.app/Contents/MacOS/idea
  200   100 /Users/me/.local/bin/claude
  300     1 /Applications/Claude.app/Contents/MacOS/Claude
  400   300 /Users/me/.local/bin/claude
  500     1 /Applications/Visual Studio Code.app/Contents/MacOS/Electron
  600   500 /Users/me/.local/bin/claude
  700     1 /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal
  800   700 /Users/me/.local/bin/claude
"""
    ARGS = f"""  100 /Applications/IntelliJ IDEA.app/Contents/MacOS/idea
  200 /Users/me/.local/bin/claude -p --input-format stream-json --resume {SESSION} --model x
  300 /Applications/Claude.app/Contents/MacOS/Claude
  400 /Users/me/.local/bin/claude -p --resume desktop-session-id-0000
  500 /Applications/Visual Studio Code.app/Contents/MacOS/Electron
  600 /Users/me/.local/bin/claude -p --resume vscode-session-id-0000
  700 /System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal
  800 /Users/me/.local/bin/claude --resume terminal-session-id-0000
"""

    def setUp(self):
        self.table = pwprocesses.parse_ps(self.COMM, self.ARGS)

    def test_paths_with_spaces_survive(self):
        self.assertEqual(self.table[100]['exe'], '/Applications/IntelliJ IDEA.app/Contents/MacOS/idea')
        self.assertEqual(self.table[200]['ppid'], 100)

    def test_each_host_is_recognised(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'macos'):
            for session, host in ((SESSION, 'jetbrains'), ('desktop-session-id-0000', 'desktop'), ('vscode-session-id-0000', 'vscode'), ('terminal-session-id-0000', 'terminal')):
                with self.subTest(host=host):
                    found = pwprocesses.find_session_process(session, self.table)
                    self.assertEqual(found['host'], host)

    def test_unknown_session(self):
        self.assertIsNone(pwprocesses.find_session_process('0' * 36, self.table))

    def test_notification_and_terminal_commands(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'macos'):
            command = pwnotifications.notification_command('a "b"', 'c')
            self.assertEqual(command[0], 'osascript')
            self.assertIn('\\"b\\"', command[2])
            self.assertEqual(pwterminal.terminal_command('/Users/me/my proj', ['claude', '--resume', 'x'])[0], 'osascript')

    def test_linux_notification_is_named_clickable_and_escaped(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.object(shutil, 'which', return_value='/usr/bin/notify-send'):
            command = pwnotifications.notification_command('Titre', 'a <b> & c', urgent=True, clickable=True)
        self.assertIn('--action=default=Ouvrir dans PR Watch', command)
        self.assertIn('--wait', command)  # libnotify 0.8 drops the action without it
        self.assertEqual(command[command.index('-a') + 1], 'PR Watch')
        self.assertEqual(command[command.index('-u') + 1], 'critical')
        self.assertEqual(command[-1], 'a &lt;b&gt; &amp; c')
        with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.object(shutil, 'which', return_value='/usr/bin/notify-send'):
            self.assertFalse(any(word.startswith('--action') for word in pwnotifications.notification_command('t', 'b')))

    def test_the_notification_belongs_to_the_application_and_can_hand_over_its_token(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.object(shutil, 'which', return_value='/usr/bin/notify-send'):
            command = pwnotifications.notification_command('t', 'b', clickable=True, token_fd=7)
        self.assertIn('--hint=string:desktop-entry:org.prwatch.PrWatchTray', command)
        self.assertIn('--activation-token-fd=7', command)

    def test_the_activation_token_of_the_click_reaches_the_command(self):
        read, write = os.pipe()
        os.write(write, b'token-123\n')
        os.close(write)
        calls = []

        class Done:
            stdout = io.StringIO('12\n')

            def communicate(self, timeout=None):
                return 'default\n', ''

        def popen(argv, **kwargs):
            calls.append((argv, kwargs))
            return Done()

        with mock.patch.object(subprocess, 'Popen', side_effect=popen):
            pwnotifications._wait_for_click(['notify-send', '--activation-token-fd=9'], ['pr-watch', 'ui'], (read, None))
        self.assertEqual(calls[0][1]['pass_fds'], (9,))
        env = calls[1][1]['env']
        self.assertEqual((env['XDG_ACTIVATION_TOKEN'], env['DESKTOP_STARTUP_ID']), ('token-123', 'token-123'))

    def test_the_window_opened_by_a_click_is_given_the_token(self):
        seen = {}
        with mock.patch.object(pwwindow, 'app_window_command', return_value=['chrome']), \
                mock.patch.object(pwhost, 'spawn_detached', side_effect=lambda argv, log, env=None, keep_launch_vars=False: seen.update(env=env, keep=keep_launch_vars)), \
                mock.patch.dict(os.environ, {'XDG_ACTIVATION_TOKEN': 'tok'}):
            pwwindow.open_app_window('http://127.0.0.1:1/')
        self.assertEqual((seen['env']['XDG_ACTIVATION_TOKEN'], seen['keep']), ('tok', True))

    def test_a_click_on_the_notification_runs_the_command(self):
        class Done:
            stdout = io.StringIO('12\n')

            def communicate(self, timeout=None):
                return 'default\n', ''
        launched = []
        with mock.patch.object(subprocess, 'Popen', side_effect=lambda argv, **kw: launched.append(argv) or Done()):
            pwnotifications._wait_for_click(['notify-send'], ['pr-watch', 'tray', '--select', 'r#1'])
        self.assertEqual(launched, [['notify-send'], ['pr-watch', 'tray', '--select', 'r#1']])

    def test_closing_a_pull_request_closes_all_its_notifications(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(pwnotifications.pwstore, 'DIR', Path(home)):
            for number in (7, 8, 9):
                pwnotifications._remember('r#1', number)
            pwnotifications._remember('r#2', 20)
            pwnotifications._forget('r#1', 8)  # clicked by hand
            with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.object(shutil, 'which', return_value='/usr/bin/gdbus'), \
                    mock.patch.object(subprocess, 'run') as run:
                pwnotifications.close_group('r#1')
                pwnotifications.close_group('r#1')  # nothing left the second time
            self.assertEqual([call.args[0][-1] for call in run.call_args_list], ['7', '9'])
            self.assertEqual(pwnotifications._group_ids(), {'r#2': [20]})

    def test_children_do_not_inherit_the_launcher_of_another_application(self):
        env = {'PATH': '/bin', 'DESKTOP_STARTUP_ID': 'gnome-shell/IntelliJ IDEA/1', 'XDG_ACTIVATION_TOKEN': 't',
               'GIO_LAUNCHED_DESKTOP_FILE': 'idea.desktop', 'GIO_LAUNCHED_DESKTOP_FILE_PID': '1',
               'SNAP': '/snap/intellij-idea-ultimate/854', 'SNAP_NAME': 'intellij-idea-ultimate'}  # libnotify then drops notification actions
        self.assertEqual(pwhost.clean_env(env), {'PATH': '/bin'})
        with mock.patch.dict(os.environ, env):
            self.assertNotIn('DESKTOP_STARTUP_ID', pwhost.child_env())

    def test_a_program_gets_its_own_scope_named_after_the_application(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'linux'), mock.patch.dict(os.environ, {'XDG_RUNTIME_DIR': '/run/user/1'}), \
                mock.patch.object(shutil, 'which', return_value='/usr/bin/systemd-run'):
            command = pwhost.in_app_scope(['prwatch', 'tray'], 'org.prwatch.PrWatchTray')
        self.assertEqual(command[:4], ['systemd-run', '--user', '--scope', '--quiet'])
        self.assertTrue(any(w.startswith('--unit=app-gnome-org.prwatch.PrWatchTray-') and w.endswith('.scope') for w in command))
        self.assertEqual(command[-2:], ['prwatch', 'tray'])
        with mock.patch.object(pwhost, 'SYSTEM', 'macos'):
            self.assertEqual(pwhost.in_app_scope(['x'], 'id'), ['x'])

    def test_the_application_bundle(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'macos'):
            pwdesktop.install_launcher()
        app = Path(os.environ['HOME']) / 'Applications' / 'PR Watch.app'
        plist = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
        self.assertEqual((plist['CFBundleExecutable'], plist['CFBundleName']), ('pr-watch-ui', 'PR Watch'))
        launcher = app / 'Contents' / 'MacOS' / 'pr-watch-ui'
        self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
        text = launcher.read_text()
        self.assertTrue(text.startswith('#!/bin/sh') and text.rstrip().endswith(' ui'), text)
        self.assertIn('-m prwatch', text)


class WindowsTableTest(unittest.TestCase):
    CIM = json.dumps([
        {'ProcessId': 10, 'ParentProcessId': 1, 'ExecutablePath': 'C:\\Program Files\\JetBrains\\IntelliJ IDEA 2026.3\\bin\\idea64.exe', 'CommandLine': '"idea64.exe"'},
        {'ProcessId': 20, 'ParentProcessId': 10, 'ExecutablePath': 'C:\\Users\\me\\.local\\bin\\claude.exe', 'CommandLine': f'"C:\\Users\\me\\.local\\bin\\claude.exe" -p --resume {SESSION}'},
        {'ProcessId': 30, 'ParentProcessId': 1, 'ExecutablePath': 'C:\\Users\\me\\AppData\\Local\\AnthropicClaude\\app-1.0\\Claude.exe', 'CommandLine': 'Claude.exe'},
        {'ProcessId': 40, 'ParentProcessId': 30, 'ExecutablePath': 'C:\\Users\\me\\.local\\bin\\claude.exe', 'CommandLine': '"claude.exe" --resume desktop-session-id-0000'},
        {'ProcessId': 50, 'ParentProcessId': 1, 'ExecutablePath': 'C:\\Users\\me\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe', 'CommandLine': 'Code.exe'},
        {'ProcessId': 60, 'ParentProcessId': 50, 'ExecutablePath': 'C:\\Users\\me\\.local\\bin\\claude.exe', 'CommandLine': '"claude.exe" --resume vscode-session-id-0000'},
    ])

    def test_hosts(self):
        table = pwprocesses.parse_cim(self.CIM)
        with mock.patch.object(pwhost, 'SYSTEM', 'windows'):
            for session, host in ((SESSION, 'jetbrains'), ('desktop-session-id-0000', 'desktop'), ('vscode-session-id-0000', 'vscode')):
                with self.subTest(host=host):
                    self.assertEqual(pwprocesses.find_session_process(session, table)['host'], host)

    def test_a_single_process_is_a_dict_not_a_list(self):
        self.assertIn(7, pwprocesses.parse_cim(json.dumps({'ProcessId': 7, 'ParentProcessId': 1, 'ExecutablePath': 'x', 'CommandLine': 'y'})))

    def test_plan_uses_the_ide_binary_and_the_project(self):
        table = pwprocesses.parse_cim(self.CIM)
        with mock.patch.object(pwhost, 'SYSTEM', 'windows'):
            found = pwprocesses.find_session_process(SESSION, table)
            found['cwd'] = None
            commands, _ = pwopening.open_plan(ENTRY, found, 'T')
        self.assertTrue(commands[0][0].endswith('idea64.exe'))
        self.assertEqual(commands[0][1], '/work/proj')

    def test_commands(self):
        with mock.patch.object(pwhost, 'SYSTEM', 'windows'):
            command = pwnotifications.notification_command("it's", 'x')
            self.assertEqual(command[0], 'powershell')
            self.assertIn("it''s", command[-1])
            self.assertEqual(pwterminal.terminal_command('C:\\p', ['claude', '--resume', 'x'])[:3], ['cmd', '/c', 'start'])

    def test_the_cli_itself_is_not_an_application(self):
        self.assertIsNone(pwprocesses.classify('C:\\Users\\me\\.local\\bin\\claude.exe'))


class PlansTest(unittest.TestCase):
    def test_claude_desktop_opens_the_exact_conversation(self):
        found = {'host': 'desktop', 'host_exe': 'x', 'cwd': None, 'pid': 1}
        with mock.patch.object(pwclaude, 'desktop_session_id', return_value='local_abc-123'):
            commands, _ = pwopening.open_plan(ENTRY, found, 'T')
        self.assertEqual(commands, [[pweditors.OPEN_URL, 'claude://code/continue?session=local_abc-123&source=pr-watch']])
        with mock.patch.object(pwclaude, 'desktop_session_id', return_value=None):  # a conversation Desktop does not know
            commands, message = pwopening.open_plan(ENTRY, found, 'T')
        self.assertEqual(commands, [])
        self.assertIn('sans conversation connue', message)

    def test_the_editors_get_a_link_once_their_plugin_knows_it(self):
        found = {'host': 'jetbrains', 'host_exe': '/snap/intellij-idea-ultimate/854/bin/idea', 'cwd': '/work/my proj', 'pid': 2}
        with mock.patch.object(pweditors, 'jetbrains_plugin_has_links', return_value=True):
            commands, _ = pwopening.open_plan(ENTRY, found, 'T')
        self.assertEqual(commands[0][1], f'jetbrains://idea/claude-chat/{SESSION}?cwd=%2Fwork%2Fmy%20proj')
        found = {'host': 'vscode', 'host_exe': '/snap/code/200/usr/share/code/code', 'cwd': '/work/proj', 'pid': 3}
        with mock.patch.object(pweditors, 'vscode_extension_has_links', return_value=True), mock.patch.object(pweditors, 'vscode_cli', return_value='code'):
            commands, _ = pwopening.open_plan(ENTRY, found, 'T')
        self.assertEqual([c[:2] for c in commands], [['code', '--reuse-window'], ['code', '--open-url']])

    def test_without_the_plugin_only_the_window_is_shown(self):
        found = {'host': 'jetbrains', 'host_exe': '/x/idea', 'cwd': '/work/proj', 'pid': 2}
        with mock.patch.object(pweditors, 'jetbrains_plugin_has_links', return_value=False):
            commands, message = pwopening.open_plan(ENTRY, found, 'Mon titre')
        self.assertEqual(commands, [['/x/idea', '/work/proj']])
        self.assertIn('Mon titre', message)

    def test_a_terminal_cannot_be_raised(self):
        commands, message = pwopening.open_plan(ENTRY, {'host': 'terminal', 'host_exe': 'x', 'cwd': None, 'pid': 4}, 'T')
        self.assertEqual(commands, [])
        self.assertIn('4', message)


class ChosenAppTest(unittest.TestCase):
    def test_apps_offered_always_include_desktop_and_terminal(self):
        ids = [a['id'] for a in pwopening.available_apps()]
        self.assertEqual((ids[0], ids[-1]), ('desktop', 'terminal'))

    def test_desktop_plan_opens_the_conversation_link(self):
        with mock.patch.object(pwclaude, 'desktop_session_id', return_value='local_abc-123'):
            commands, _ = pwopening.open_in_plan('desktop', ENTRY, 'T')
        self.assertEqual(commands[0][0], pweditors.OPEN_URL)
        self.assertIn('local_abc-123', commands[0][1])

    def test_unknown_app_has_no_plan(self):
        self.assertIsNone(pwopening.open_in_plan('emacs', ENTRY, 'T'))


class SingleWindowTest(unittest.TestCase):
    def test_the_window_is_launched_again_with_the_same_address_so_the_browser_reuses_it(self):
        launched = []
        with mock.patch.object(pwwindow, 'app_window_command', side_effect=lambda url, profile: ['chrome', f'--app={url}']), \
                mock.patch.object(pwhost, 'spawn_detached', side_effect=lambda argv, log, env=None, keep_launch_vars=False: launched.append(argv)):
            pwwindow.open_app_window('http://127.0.0.1:8765/?t=x', '/p/chrome')
            pwwindow.open_app_window('http://127.0.0.1:8765/?t=x', '/p/chrome')
        self.assertEqual(len(launched), 2)
        self.assertEqual(launched[0], launched[1])  # same command, same address: the browser brings its window to the front


class LinkTest(unittest.TestCase):
    def test_desktop_link_uses_the_desktop_id_of_the_conversation(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, 'acct', 'org')
            os.makedirs(folder)
            with open(os.path.join(folder, 'local_abc-123.json'), 'w', encoding='utf-8') as handle:
                json.dump({'sessionId': 'local_abc-123', 'cliSessionId': 'other', 'previousCliSessionIds': [SESSION]}, handle)
            with mock.patch.object(pwclaude, 'desktop_sessions_dirs', return_value=[root]):
                self.assertEqual(pwclaude.desktop_session_id(SESSION), 'local_abc-123')
                self.assertIn('session=local_abc-123', pwopening.link_for('desktop', ENTRY, '/work/proj'))
                self.assertIsNone(pwclaude.desktop_session_id('unknown'))
                self.assertIn('desktop', [a['id'] for a in pwopening.available_apps(ENTRY)])
            with mock.patch.object(pwclaude, 'desktop_sessions_dirs', return_value=[root + '/none']):
                self.assertIsNone(pwopening.link_for('desktop', ENTRY, '/work/proj'))
                self.assertNotIn('desktop', [a['id'] for a in pwopening.available_apps(ENTRY)])

    def test_desktop_and_vscode_open_by_link(self):
        with mock.patch.object(pweditors, 'vscode_extension_has_links', return_value=True):
            self.assertTrue(pwopening.link_for('vscode', ENTRY, '/work/proj').startswith('vscode://'))
        with mock.patch.object(pweditors, 'vscode_extension_has_links', return_value=False):
            self.assertIsNone(pwopening.link_for('vscode', ENTRY, '/work/proj'))
        self.assertIsNone(pwopening.link_for('jetbrains', ENTRY, '/work/proj'))


class PluginChoiceTest(unittest.TestCase):
    def ids(self, jetbrains, vscode, jb_links=True, vs_links=True):
        with mock.patch.object(pweditors, '_jetbrains_launcher', return_value='/bin/idea'), mock.patch.object(pweditors, '_vscode_installed', return_value=True), \
                mock.patch.object(pweditors, 'jetbrains_plugins', return_value=jetbrains), mock.patch.object(pweditors, 'vscode_plugins', return_value=vscode), \
                mock.patch.object(pweditors, 'jetbrains_plugin_has_links', return_value=jb_links), mock.patch.object(pweditors, 'vscode_extension_has_links', return_value=vs_links):
            return [a['id'] for a in pwopening.available_apps()], pwopening.apps_hints()

    def test_only_the_places_that_can_open_that_conversation_are_offered(self):
        self.assertEqual(self.ids({'chat', 'code'}, {'chat'})[0], ['desktop', 'jetbrains-chat', 'vscode-chat', 'terminal'])
        self.assertEqual(self.ids({'code'}, {'code'})[0], ['desktop', 'vscode-code', 'terminal'])  # that IDE plugin cannot open a conversation
        self.assertEqual(self.ids(set(), set())[0], ['desktop', 'terminal'])  # an editor with no Claude plugin shows a window at best

    def test_a_plugin_too_old_is_not_offered_and_says_why(self):
        ids, hints = self.ids({'chat'}, {'chat'}, jb_links=False, vs_links=False)
        self.assertEqual(ids, ['desktop', 'terminal'])
        self.assertEqual(len(hints), 2)
        self.assertIn('0.9.8', hints[0])
        self.assertIn('0.9.7', hints[1])

    def test_plans_of_the_plugins(self):
        with mock.patch.object(shutil, 'which', return_value='/bin/code'):
            commands, _ = pwopening.open_in_plan('vscode-code', ENTRY, 'T')
        self.assertEqual(commands[-1][:2], ['/bin/code', '--open-url'])
        self.assertEqual(pwopening.link_for('vscode-code', ENTRY, '/work/proj'), f'vscode://anthropic.claude-code/open?session={SESSION}')

    def test_detection_of_installed_plugins(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, '.local/share/JetBrains/IntelliJIdea2026.3/claude-code-jetbrains-plugin'))
            os.makedirs(os.path.join(home, '.vscode/extensions/anthropic.claude-code-2.0.1'))
            with mock.patch.dict(os.environ, {'HOME': home}), mock.patch.object(pwhost, 'SYSTEM', 'linux'):
                self.assertEqual(pweditors.jetbrains_plugins(), {'code'})
                self.assertEqual(pweditors.vscode_plugins(), {'code'})


class DefaultTerminalTest(unittest.TestCase):
    def test_the_terminal_set_in_gnome_wins_with_its_own_argument(self):
        answers = {'exec': "'terminator'\n", 'exec-arg': "'-x'\n"}
        with mock.patch.object(pwhost, '_run', side_effect=lambda argv, timeout=20: answers[argv[-1]]), mock.patch.object(shutil, 'which', side_effect=lambda n: f'/usr/bin/{n}'):
            self.assertEqual(pwterminal.default_terminal(), ['/usr/bin/terminator', '-x'])

    def test_then_the_alternative_with_the_argument_of_the_real_terminal(self):
        with mock.patch.object(pwhost, '_run', return_value=''), mock.patch.object(shutil, 'which', side_effect=lambda n: '/usr/bin/x-terminal-emulator' if n == 'x-terminal-emulator' else None), \
                mock.patch.object(os.path, 'realpath', return_value='/usr/bin/gnome-terminal.wrapper'.replace('.wrapper', '')):
            self.assertEqual(pwterminal.default_terminal(), ['/usr/bin/x-terminal-emulator', '--'])


class OriginTest(unittest.TestCase):
    TABLE = {
        1: {'pid': 1, 'ppid': 0, 'exe': '/sbin/init', 'cmd': 'init'},
        10: {'pid': 10, 'ppid': 1, 'exe': '/usr/share/code/code', 'cmd': '/usr/share/code/code --type=extensionHost'},
        20: {'pid': 20, 'ppid': 10, 'exe': '/home/me/.local/bin/claude', 'cmd': 'claude'},
        30: {'pid': 30, 'ppid': 20, 'exe': '/usr/bin/python3', 'cmd': 'python3 -m prwatch register'},
    }

    def test_the_host_is_found_above_the_register_command(self):
        with mock.patch.object(os, 'getpid', return_value=30), mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'claude-vscode'}):
            origin = pwprocesses.origin_of_this_process(self.TABLE)
        self.assertEqual(origin, {'host': 'vscode', 'entrypoint': 'claude-vscode'})

    def test_a_terminal_session_is_recognised_by_its_entrypoint(self):
        with mock.patch.object(os, 'getpid', return_value=1), mock.patch.dict(os.environ, {'CLAUDE_CODE_ENTRYPOINT': 'cli'}):
            self.assertEqual(pwprocesses.origin_of_this_process(self.TABLE), {'host': 'terminal', 'entrypoint': 'cli'})

    def test_the_app_that_matches_the_origin_is_recommended(self):
        offered = ['desktop', 'jetbrains-chat', 'vscode-chat', 'vscode-code', 'terminal']
        self.assertEqual(pwprocesses.recommended_app({'host': 'vscode', 'entrypoint': 'claude-vscode'}, offered), 'vscode-code')
        self.assertEqual(pwprocesses.recommended_app({'host': 'vscode', 'entrypoint': ''}, offered), 'vscode-chat')
        self.assertEqual(pwprocesses.recommended_app({'host': 'jetbrains'}, offered), 'jetbrains-chat')
        self.assertEqual(pwprocesses.recommended_app({'host': 'desktop'}, offered), 'desktop')
        self.assertEqual(pwprocesses.recommended_app({'host': 'terminal'}, offered), 'terminal')
        self.assertIsNone(pwprocesses.recommended_app({'host': 'desktop'}, ['terminal']))  # Desktop does not know the conversation
        self.assertIsNone(pwprocesses.recommended_app(None, offered))


class MiscTest(unittest.TestCase):
    def test_app_window_command_uses_app_mode(self):
        with mock.patch.object(pwwindow, 'chromium_candidates', return_value=[os.path.realpath('/bin/sh')]):
            command = pwwindow.app_window_command('http://127.0.0.1:1/')
        self.assertTrue(any(a.startswith('--app=http://127.0.0.1:1/') for a in command))
        self.assertFalse(any(a.startswith('--user-data-dir') for a in command))
        with mock.patch.object(pwwindow, 'chromium_candidates', return_value=[os.path.realpath('/bin/sh')]):
            own = pwwindow.app_window_command('http://127.0.0.1:1/', '/data/chrome')
        self.assertIn('--user-data-dir=/data/chrome', own)

    def test_no_browser_means_no_command(self):
        with mock.patch.object(pwwindow, 'chromium_candidates', return_value=[]):
            self.assertIsNone(pwwindow.app_window_command('http://x/'))

    def test_the_lock_is_exclusive(self):
        first, second = open(Path(os.environ['PR_WATCH_HOME']) / 'x.lock', 'a+'), open(Path(os.environ['PR_WATCH_HOME']) / 'x.lock', 'a+')
        try:
            self.assertTrue(pwhost.lock(first, blocking=False))
            self.assertFalse(pwhost.lock(second, blocking=False))
            pwhost.unlock(first)
            self.assertTrue(pwhost.lock(second, blocking=False))
        finally:
            first.close()
            second.close()

    def test_a_checkout_is_put_on_the_pythonpath_of_children(self):
        env = pwhost.child_env()
        if pwhost.from_checkout():
            self.assertIn(pwhost.package_root(), env['PYTHONPATH'].split(os.pathsep))


if __name__ == '__main__':
    unittest.main()
