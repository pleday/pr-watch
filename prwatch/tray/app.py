#!/usr/bin/env python3
"""The status icon of PR Watch (Linux with GTK 3): the only part of the application that is not the web dashboard.

The icon sits in the top bar (StatusNotifierItem, shown by the Ubuntu AppIndicator extension): green when the daemon
runs, orange when a PR needs a look, blue when a PR is ready to merge, red when the daemon is stopped. A click opens the
native status menu: the state, the few pull requests that ask for something (a click on one opens the window on it), and
the actions: open the dashboard, switch the watching on or off, quit.
"""
import os
import subprocess
import threading
import sys

import gi  # noqa: E402  (imported only on Linux, see `prwatch tray`)

gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from prwatch.tray import icon as trayicon  # noqa: E402
from prwatch.tray import summary as traysummary
from prwatch.core import config as pwconfig
from prwatch.actions import options as pwoptions
from prwatch.actions import pr as pwpr
from prwatch.engine import service as pwservice
from prwatch.core import store as pwstore
from prwatch.system import host as pwhost
from prwatch.system import processes as pwprocesses

APP_ID = pwhost.APP_ID
REFRESH_SECONDS = 3
GREEN, ORANGE, RED, BLUE = (46, 160, 67), (227, 140, 20), (207, 34, 46), (31, 111, 235)

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ProvideXdgActivationToken"><arg type="s" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
    <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
    <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
    <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
    <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
    <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
    <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
    <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
  </interface>
</node>
"""


ICON_SIZES = (22, 32, 48, 64)  # the panel picks the closest to what it draws


class Indicator:
    """The icon of the top bar, speaking the StatusNotifierItem protocol over D-Bus."""

    def __init__(self, on_activate, handlers=None):
        self.on_activate = on_activate
        self.handlers = handlers or {}  # 'open' (a target), 'toggle', 'quit': what the panel and the menu ask for
        self.rows, self.toggle = [], ('Activer la surveillance', 'start')
        self.color, self.tooltip, self.status = RED, ('pr-watch', ''), 'Active'
        self.token = None  # the activation token the panel hands over just before a click (Wayland focus)
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.bus_name = f'org.kde.StatusNotifierItem-{os.getpid()}-1'
        self.menu = DBusMenu(self.connection, on_activate, self.handlers)
        info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        self.connection.register_object(
            '/StatusNotifierItem', info, self._on_method, self._on_get_property, None
        )

        Gio.bus_own_name_on_connection(
            self.connection, self.bus_name, Gio.BusNameOwnerFlags.NONE, self._on_name_acquired, None
        )

    def _on_name_acquired(self, connection, name):
        connection.call(
            'org.kde.StatusNotifierWatcher',
            '/StatusNotifierWatcher',
            'org.kde.StatusNotifierWatcher',
            'RegisterStatusNotifierItem',
            GLib.Variant('(s)', (name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )

    def _on_method(self, connection, sender, path, interface, method, parameters, invocation):
        if method == 'ProvideXdgActivationToken':
            self.token = parameters.unpack()[0]
        elif method == 'SecondaryActivate':
            token, self.token = self.token, None
            GLib.idle_add(self._activate, token)
        invocation.return_value(None)

    @staticmethod
    def _targets_of(rows):
        """{line of the summary text: target} for the rows that lead somewhere."""
        targets = {}
        for row in rows:
            if row.get('target'):
                targets[row['text']] = row['target']
        return targets

    def _activate(self, token):
        self.on_activate(token=token)
        return False  # once

    def _on_get_property(self, connection, sender, path, interface, name):
        values = {
            'Category': GLib.Variant('s', 'ApplicationStatus'),
            'Id': GLib.Variant('s', 'pr-watch'),
            'Title': GLib.Variant('s', 'pr-watch'),
            'Status': GLib.Variant('s', self.status),
            'WindowId': GLib.Variant('u', 0),
            'IconName': GLib.Variant('s', ''),
            'IconPixmap': GLib.Variant('a(iiay)', [trayicon.pixmap(size, self.color) for size in ICON_SIZES]),
            'OverlayIconName': GLib.Variant('s', ''),
            'AttentionIconName': GLib.Variant('s', ''),
            'ToolTip': GLib.Variant('(sa(iiay)ss)', ('', [], *self.tooltip)),
            'ItemIsMenu': GLib.Variant('b', True),
            'Menu': GLib.Variant('o', '/Menu'),
        }
        return values.get(name)

    def update(self, color, title, text, rows=None, toggle=None):
        changed = color != self.color or (title, text) != self.tooltip
        self.color, self.tooltip = color, (title, text)
        self.rows, self.toggle = rows or [], toggle or self.toggle
        self.menu.update(text, self.toggle[0], self._targets_of(self.rows))
        if changed:
            for signal in ('NewIcon', 'NewToolTip'):
                self.connection.emit_signal(None, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', signal, None)


class DBusMenu:
    """The native StatusNotifier popup menu, rendered by the desktop shell next to the icon."""

    DASHBOARD_ITEM = 2_147_483_647
    TOGGLE_ITEM = 2_147_483_646
    QUIT_ITEM = 2_147_483_645

    def __init__(self, connection, on_activate, handlers=None):
        self.connection = connection
        self.on_activate = on_activate
        self.handlers = handlers or {}
        self._last_id = 0
        self.revision = 0
        self.items = []
        self.text = None
        info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        connection.register_object('/Menu', info, self._on_method, self._on_get_property, None)

    @staticmethod
    def _properties(label=None, enabled=True, item_type=None):
        # every property is always given: a shell keeps what it knew of an item for the properties it is not told again
        props = {'visible': GLib.Variant('b', True), 'enabled': GLib.Variant('b', enabled), 'type': GLib.Variant('s', item_type or 'standard')}
        if label is not None:
            props['label'] = GLib.Variant('s', label)
        return props

    def update(self, text, toggle_label='Activer la surveillance', targets=None):
        """`targets` maps a line of `text` to what the window shows when it is clicked: only those lines can be clicked."""
        targets = targets or {}
        if (text, toggle_label, targets) == getattr(self, 'text', None):
            return
        self.text = (text, toggle_label, targets)
        self.targets = {}
        lines = text.splitlines()
        labels = lines[:1] + [''] + lines[1:]
        labels.extend(['', 'Ouvrir le tableau de bord'])
        self.items = []
        for label in labels[:-1]:
            # An id is never used twice: the shell keeps the properties it knew of an id, so a line that takes the place of a
            # separator (the list going from "no pull request" to ten) would stay a greyed separator.
            self._last_id = getattr(self, '_last_id', 0) + 1
            item_id = self._last_id
            if label:
                self.items.append((item_id, self._properties(label, enabled=label in targets)))
                if label in targets:
                    self.targets[item_id] = targets[label]
            else:
                self.items.append((item_id, self._properties(enabled=False, item_type='separator')))
        self.items.append((self.DASHBOARD_ITEM, self._properties('Ouvrir le tableau de bord')))
        self.items.append((self.TOGGLE_ITEM, self._properties(toggle_label)))
        self.items.append((self.QUIT_ITEM, self._properties('Quitter PR Watch')))
        self.revision += 1
        self.connection.emit_signal(None, '/Menu', 'com.canonical.dbusmenu', 'LayoutUpdated',
                                     GLib.Variant('(ui)', (self.revision, 0)))

    def _on_get_property(self, _connection, _sender, _path, _interface, name):
        values = {
            'Version': GLib.Variant('u', 3),
            'TextDirection': GLib.Variant('s', 'ltr'),
            'Status': GLib.Variant('s', 'normal'),
            'IconThemePath': GLib.Variant('as', []),
        }
        return values.get(name)

    def _layout(self, item_id, props, children=()):
        return item_id, props, [GLib.Variant('(ia{sv}av)', child) for child in children]

    def _on_method(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        if method == 'GetLayout':
            parent_id, _depth, _names = args
            children = [(item_id, props, []) for item_id, props in self.items] if parent_id == 0 else []
            invocation.return_value(GLib.Variant('(u(ia{sv}av))', (self.revision, self._layout(parent_id, {}, children))))
        elif method == 'GetGroupProperties':
            ids, names = args
            properties = []
            for item_id, props in self.items:
                if item_id in ids:
                    selected = props if not names else {key: value for key, value in props.items() if key in names}
                    properties.append((item_id, selected))
            invocation.return_value(GLib.Variant('(a(ia{sv}))', (properties,)))
        elif method == 'GetProperty':
            item_id, name = args
            props = next((props for candidate, props in self.items if candidate == item_id), {})
            value = props.get(name, GLib.Variant('s', ''))
            invocation.return_value(GLib.Variant('(v)', (value,)))
        elif method == 'Event':
            item_id, event_id, _data, _timestamp = args
            if event_id == 'clicked':
                self._clicked(item_id)
            invocation.return_value(None)
        elif method == 'EventGroup':
            failures = []
            for item_id, event_id, _data, _timestamp in args[0]:
                if event_id == 'clicked' and (item_id in (self.DASHBOARD_ITEM, self.TOGGLE_ITEM, self.QUIT_ITEM) or item_id in getattr(self, 'targets', {})):
                    self._clicked(item_id)
                elif item_id not in {candidate for candidate, _props in self.items}:
                    failures.append(item_id)
            invocation.return_value(GLib.Variant('(ai)', (failures,)))
        elif method == 'AboutToShow':
            invocation.return_value(GLib.Variant('(b)', (False,)))
        elif method == 'AboutToShowGroup':
            invocation.return_value(GLib.Variant('(aiai)', ([], [])))

    def _clicked(self, item_id):
        target = getattr(self, 'targets', {}).get(item_id)
        if target and getattr(self, 'handlers', {}).get('open'):
            GLib.idle_add(self._run, lambda: self.handlers['open'](target))
        elif item_id == self.DASHBOARD_ITEM:
            GLib.idle_add(self._activate_dashboard)
        else:
            handler = getattr(self, 'handlers', {}).get({self.TOGGLE_ITEM: 'toggle', self.QUIT_ITEM: 'quit'}.get(item_id))
            if handler:
                GLib.idle_add(self._run, handler)

    @staticmethod
    def _run(handler):
        handler()
        return False  # once

    def _activate_dashboard(self):
        self.on_activate(token=None)
        return False




def open_dashboard(select=None, token=None, pulse=False):
    """Opens the web dashboard in its own process (it may have to start the server, which the icon must not wait for).

    `token` is the activation token of the click on the icon: handed to the process, it lets the window come to the front.
    """
    command = pwhost.module_command('ui', *(['--select', select] if select else []), *(['--pulse'] if select and pulse else []))
    env = {**pwhost.child_env(), **({'XDG_ACTIVATION_TOKEN': token} if token else {})}
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env)


class Application(Gtk.Application):
    """Holds the status icon. It has no window: a click on the icon, or a second `pr-watch tray`, opens the dashboard."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.indicator = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name('PR Watch')
        self.hold()
        self.indicator = Indicator(lambda **kw: open_dashboard(**kw), handlers={'open': self.open_target, 'toggle': self.toggle_daemon, 'quit': self.quit_all})
        self.state = (False, False)  # (daemon running, stopped by the schedule), as of the last look
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)
        self._tick()

    def open_target(self, target):
        """A click on a row of the summary: the window opens on what the row is about, which pulses for a moment."""
        open_dashboard(select=target, pulse=True)

    def toggle_daemon(self):
        """Switches the watching on or off, whichever it is now (as the power button of the window does)."""
        running, scheduled_off = self.state
        command = 'force-schedule' if scheduled_off else 'stop' if running else 'ensure'
        # a command starts or stops the daemon and waits for it: off the main loop, which must keep answering the shell
        threading.Thread(target=pwoptions.daemon_command, args=(command,), daemon=True).start()
        GLib.timeout_add(2500, self._tick)

    def quit_all(self):
        """Quits PR Watch: the open window closes itself and the icon goes. The daemon is the watching: it keeps running."""
        pwpr.request_quit()
        GLib.timeout_add(600, self.quit)

    def do_command_line(self, command_line):
        if '--background' not in command_line.get_arguments()[1:]:
            open_dashboard()
        return 0

    def _tick(self):
        running = pwservice.is_running()
        cfg = pwconfig.config()
        schedule = pwconfig.schedule_of(cfg)
        working, resumes, _override = pwconfig.schedule_effective_state(cfg)
        scheduled_off = schedule['enabled'] and not working
        with pwstore.registry_lock():
            entries = pwstore.load_registry()
        watched = [e for e in entries if e.get('state', 'watching') in ('watching', 'paused')]
        stuck = [e for e in entries if e.get('state') == 'gave-up' or e.get('needsAttention') or e.get('pendingQuestions')]
        active_entries = [e for e in entries if e.get('state', 'watching') != 'done']
        session_ids = {e['sessionId'] for e in active_entries if e.get('sessionId')}
        process_table = pwprocesses.process_table() if session_ids else {}
        active_sessions = {
            session_id for session_id in session_ids
            if pwprocesses.find_session_process(session_id, process_table)
        }
        if not running:
            color, title = RED, 'pr-watch : démon arrêté'
        elif scheduled_off:
            title = 'pr-watch : démon arrêté par les horaires' + (f' jusqu’à {resumes}' if resumes else '')
            color = RED
        elif stuck:
            color, title = ORANGE, 'pr-watch : une PR demande votre attention'
        elif any(e.get('ready') for e in watched):
            color, title = BLUE, 'pr-watch : une PR est prête à être mergée'
        else:
            color, title = GREEN, 'pr-watch : démon actif'
        self.state = (running, scheduled_off)
        shown = (running, scheduled_off, resumes, entries, active_sessions)
        options = {'suspension': pwstore.suspension(), 'max_attempts': cfg['maxAttempts'], 'cfg': cfg}
        text = traysummary.tooltip_text(*shown, **options)
        toggle = traysummary.toggle_of(running, scheduled_off)
        self.indicator.update(color, title, text, rows=traysummary.rows(*shown, **options), toggle=toggle)
        return True


def main(argv=None):
    GLib.set_prgname(APP_ID)
    return Application().run(argv or sys.argv)


if __name__ == '__main__':
    sys.exit(main())
