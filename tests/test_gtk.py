import os
import unittest
from unittest import mock

from .helpers import entry, set_registry

SCREEN = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


@unittest.skipUnless(SCREEN, 'needs a desktop session')
class StatusIconTest(unittest.TestCase):
    """The status icon starts and follows the state of the registry (skipped without GTK 3 or without a desktop)."""

    @classmethod
    def setUpClass(cls):
        try:
            from prwatch.tray import app as gtkapp
        except (ImportError, ValueError) as error:
            raise unittest.SkipTest(f'GTK 3 is not available: {error}')
        cls.gtkapp = gtkapp

    def test_the_icon_takes_the_colour_of_the_state(self):
        opened = []
        app = self.gtkapp.Application()
        app.indicator = self.gtkapp.Indicator(lambda **kw: opened.append(kw))
        with mock.patch('prwatch.engine.service.is_running', return_value=True):
            set_registry([entry(1, needsAttention='à voir')])
            self.assertTrue(app._tick())
            self.assertIn('attention', app.indicator.tooltip[0])
            set_registry([entry(1, ready=True)])
            app._tick()
            self.assertIn('prête', app.indicator.tooltip[0])
        with mock.patch('prwatch.engine.service.is_running', return_value=False):
            app._tick()
            self.assertIn('arrêté', app.indicator.tooltip[0])

    def test_the_activation_token_of_the_click_reaches_the_dashboard(self):
        from gi.repository import GLib
        calls = []
        indicator = self.gtkapp.Indicator.__new__(self.gtkapp.Indicator)  # no second export of the D-Bus object
        indicator.on_activate, indicator.token = (lambda **kw: calls.append(kw)), None
        answered = mock.Mock()
        with mock.patch.object(self.gtkapp.GLib, 'idle_add', side_effect=lambda fn, *args: fn(*args)):
            indicator._on_method(None, None, None, None, 'ProvideXdgActivationToken', GLib.Variant('(s)', ('tok-1',)), answered)
            indicator._on_method(None, None, None, None, 'Activate', GLib.Variant('(ii)', (0, 0)), answered)
            indicator._on_method(None, None, None, None, 'SecondaryActivate', GLib.Variant('(ii)', (0, 0)), answered)
        self.assertEqual(calls, [{'token': 'tok-1'}])

    def test_a_row_of_the_summary_opens_the_dashboard_on_it_with_a_pulse(self):
        with mock.patch.object(self.gtkapp.subprocess, 'Popen') as popen:
            self.gtkapp.Application().open_target('api#12')
            self.gtkapp.open_dashboard(select='api#12')
        self.assertEqual(popen.call_args_list[0].args[0][-3:], ['--select', 'api#12', '--pulse'])
        self.assertNotIn('--pulse', popen.call_args_list[1].args[0])

    def test_the_dashboard_process_is_given_the_token(self):
        with mock.patch.object(self.gtkapp.subprocess, 'Popen') as popen:
            self.gtkapp.open_dashboard(token='tok-2')
            self.gtkapp.open_dashboard()
        self.assertEqual(popen.call_args_list[0].kwargs['env']['XDG_ACTIVATION_TOKEN'], 'tok-2')
        self.assertNotIn('XDG_ACTIVATION_TOKEN', popen.call_args_list[1].kwargs['env'])

    def test_status_icon_uses_a_native_menu_instead_of_a_window(self):
        indicator = self.gtkapp.Indicator.__new__(self.gtkapp.Indicator)
        indicator.color, indicator.tooltip, indicator.status = (0, 0, 0), ('title', 'summary'), 'Active'
        indicator.menu = mock.Mock()

        values = {name: indicator._on_get_property(None, None, None, None, name).unpack()
                  for name in ('ItemIsMenu', 'Menu')}

        self.assertEqual(values, {'ItemIsMenu': True, 'Menu': '/Menu'})
        info = self.gtkapp.Gio.DBusNodeInfo.new_for_xml(self.gtkapp.ITEM_XML).interfaces[0]
        self.assertIsNone(info.lookup_method('Activate'))

    def test_native_menu_contains_the_summary_and_dashboard_action(self):
        from gi.repository import GLib
        menu = self.gtkapp.DBusMenu.__new__(self.gtkapp.DBusMenu)
        menu.items, menu.text, menu.revision = [], None, 0
        menu.connection = mock.Mock()
        menu.update('Démon : actif\nClaude : 1 session active\nPR en cours (1)\napi #12 — prête')
        invocation = mock.Mock()

        menu._on_method(None, None, None, None, 'GetLayout', GLib.Variant('(iias)', (0, -1, [])), invocation)

        layout = invocation.return_value.call_args.args[0]
        self.assertEqual(layout.get_type_string(), '(u(ia{sv}av))')
        children = layout.unpack()[1][2]
        self.assertEqual([c[1]['label'] for c in children[-3:]], ['Ouvrir le tableau de bord', 'Activer la surveillance', 'Quitter PR Watch'])
        self.assertIn('api #12 — prête', [props['label'] for _item_id, props, _children in children if 'label' in props])

    def test_only_the_lines_that_lead_somewhere_can_be_clicked(self):
        from gi.repository import GLib
        from prwatch.tray import summary as traysummary
        opened = []
        menu = self.gtkapp.DBusMenu.__new__(self.gtkapp.DBusMenu)
        menu.items, menu.text, menu.revision = [], None, 0
        menu.connection = mock.Mock()
        menu.handlers = {'open': opened.append}
        entries = [{'repo': 'api', 'pr': 12, 'title': 'fix', 'ready': True}, {'repo': 'web', 'pr': 3, 'state': 'watching'}]
        rows = traysummary.rows(True, False, None, entries)
        menu.update('\n'.join(row['text'] for row in rows), 'Désactiver la surveillance', self.gtkapp.Indicator._targets_of(rows))
        enabled = {props['label'].unpack(): props['enabled'].unpack() for _id, props in menu.items if 'label' in props}
        self.assertEqual({label: on for label, on in enabled.items() if not label.startswith(('Ouvrir', 'Désactiver', 'Quitter'))},
                         {'🟢 Démon en route · 🟢 Claude activé': False, 'Pull requests (2) · 1 à traiter :': False, '• api #12 · fix — Prête à merger ✓': True, '• web #3 — Surveillée': True})
        self.assertTrue(all(enabled[label] for label in ('Ouvrir le tableau de bord', 'Désactiver la surveillance', 'Quitter PR Watch')))
        by_label = {props['label'].unpack(): item_id for item_id, props in menu.items if 'label' in props}
        with mock.patch.object(self.gtkapp.GLib, 'idle_add', side_effect=lambda fn, *args: fn(*args)):
            for label in ('• api #12 · fix — Prête à merger ✓', '🟢 Démon en route · 🟢 Claude activé', 'Pull requests (2) · 1 à traiter :', '• web #3 — Surveillée'):
                menu._on_method(None, None, None, None, 'Event', GLib.Variant('(isvu)', (by_label[label], 'clicked', GLib.Variant('s', ''), 0)), mock.Mock())
        self.assertEqual(opened, ['api#12', 'web#3'], 'one line per listed pull request leads somewhere; the state and the heading do nothing')

    def test_a_menu_item_never_gets_an_id_the_shell_knew_for_something_else(self):
        menu = self.gtkapp.DBusMenu.__new__(self.gtkapp.DBusMenu)
        menu.items, menu.text, menu.revision = [], None, 0
        menu.connection = mock.Mock()
        menu.update('🟢 Démon en route · 🟢 Claude activé\nAucune pull request.')
        before = {item_id: props['label'].unpack() if 'label' in props else props['type'].unpack() for item_id, props in menu.items}
        menu.update('🟢 Démon en route · 🟢 Claude activé\nPull requests (1) · 1 à traiter :\n• api #12 — Votre attention', 'Activer la surveillance', {'• api #12 — Votre attention': 'api#12'})
        after = {item_id: props for item_id, props in menu.items}
        reused = [item_id for item_id in before if item_id in after and item_id < menu.DASHBOARD_ITEM - 2]
        self.assertEqual(reused, [], 'the ids of the first layout are not used again')
        self.assertTrue(all('type' in props for props in after.values()), 'every property is given every time')
        self.assertEqual({props['type'].unpack() for _id, props in after.items() if 'label' in props}, {'standard'})

    def test_the_menu_items_switch_the_watching_and_quit(self):
        from gi.repository import GLib
        menu = self.gtkapp.DBusMenu.__new__(self.gtkapp.DBusMenu)
        menu.items, menu.text, menu.revision = [], None, 0
        menu.connection = mock.Mock()
        menu.handlers = {'toggle': mock.Mock(), 'quit': mock.Mock()}
        menu.update('Démon : actif', 'Désactiver la surveillance')
        self.assertIn('Désactiver la surveillance', [props['label'].unpack() for _id, props in menu.items if 'label' in props])
        with mock.patch.object(self.gtkapp.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)):
            for item in (menu.TOGGLE_ITEM, menu.QUIT_ITEM):
                menu._on_method(None, None, None, None, 'Event', GLib.Variant('(isvu)', (item, 'clicked', GLib.Variant('s', ''), 0)), mock.Mock())
        menu.handlers['toggle'].assert_called_once_with()
        menu.handlers['quit'].assert_called_once_with()

    def test_the_toggle_follows_the_state_of_the_daemon(self):
        app = self.gtkapp.Application()
        started = []

        class Thread:
            def __init__(self, target, args, daemon):
                self.args = args

            def start(self):
                started.append(self.args[0])

        with mock.patch.object(self.gtkapp.threading, 'Thread', Thread), mock.patch.object(self.gtkapp.GLib, 'timeout_add'):
            for state in ((True, False), (False, False), (True, True)):
                app.state = state
                app.toggle_daemon()
        self.assertEqual(started, ['stop', 'ensure', 'force-schedule'])

    def test_quit_asks_the_window_to_close_then_ends_the_icon(self):
        app = self.gtkapp.Application()
        with mock.patch.object(self.gtkapp.pwpr, 'request_quit') as request, mock.patch.object(self.gtkapp.GLib, 'timeout_add') as later:
            app.quit_all()
        request.assert_called_once_with()
        self.assertEqual(later.call_args.args[1], app.quit)

    def test_native_menu_dashboard_item_opens_the_dashboard(self):
        from gi.repository import GLib
        menu = self.gtkapp.DBusMenu.__new__(self.gtkapp.DBusMenu)
        menu.items, menu.text, menu.revision = [], None, 0
        menu.connection = mock.Mock()
        menu.on_activate = mock.Mock()
        menu.update('Démon : actif')
        invocation = mock.Mock()
        with mock.patch.object(self.gtkapp.GLib, 'idle_add', side_effect=lambda callback: callback()):
            menu._on_method(None, None, None, None, 'Event',
                            GLib.Variant('(isvu)', (menu.DASHBOARD_ITEM, 'clicked', GLib.Variant('s', ''), 0)),
                            invocation)

        invocation.return_value.assert_called_once_with(None)
        menu.on_activate.assert_called_once_with(token=None)
