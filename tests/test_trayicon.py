import unittest

from prwatch.tray import icon as trayicon
from prwatch.tray import summary as traysummary

GREEN = (46, 160, 67)


def pixel(icon, x, y):
    width, _height, data = icon
    return tuple(data[(y * width + x) * 4:(y * width + x) * 4 + 4])  # alpha, red, green, blue


class TrayIconTest(unittest.TestCase):
    def test_the_picture_has_the_size_asked_for(self):
        for size in (22, 48):
            width, height, data = trayicon.pixmap(size, GREEN)
            self.assertEqual((width, height, len(data)), (size, size, size * size * 4))

    def test_it_is_the_logo_with_a_disc_of_the_state_in_the_bottom_right_corner(self):
        icon = trayicon.pixmap(64, GREEN)
        self.assertEqual(pixel(icon, 0, 0)[0], 0, 'the corners of the rounded square are transparent')
        self.assertEqual(pixel(icon, 5, 32), (255, *trayicon.BLUE), 'the background is the blue of the logo')
        self.assertEqual(pixel(icon, 20, 30), (255, 255, 255, 255), 'the branch line of the logo is white')
        self.assertEqual(pixel(icon, 52, 52), (255, *GREEN), 'the disc has the colour of the state')

    def test_without_a_badge_the_corner_stays_part_of_the_logo(self):
        self.assertEqual(pixel(trayicon.pixmap(64), 56, 56), (255, *trayicon.BLUE))

    def test_each_state_gets_its_own_colour(self):
        red = (207, 34, 46)
        self.assertNotEqual(pixel(trayicon.pixmap(32, red), 26, 26), pixel(trayicon.pixmap(32, GREEN), 26, 26))

    def test_the_menu_lists_only_what_asks_for_something_and_counts_the_rest(self):
        entries = [
            {'repo': 'api', 'pr': 1, 'title': 'quiet one'},  # watched, nothing to do
            {'repo': 'api', 'pr': 2, 'title': 'ready', 'ready': True},
            {'repo': 'api', 'pr': 3, 'title': 'broken', 'needsAttention': 'pipeline'},
            {'repo': 'api', 'pr': 4, 'state': 'paused'},
            {'repo': 'api', 'pr': 5, 'title': 'asks', 'pendingQuestions': [{'id': 1}, {'id': 2}]},
            {'repo': 'api', 'pr': 6, 'state': 'gave-up'},
            {'repo': 'api', 'pr': 7, 'title': 'ready too', 'ready': True},
            {'repo': 'api', 'pr': 8, 'title': 'and again', 'ready': True},
            {'repo': 'api', 'pr': 9, 'title': 'ready 9', 'ready': True},
        ]
        lines = traysummary.tooltip_text(True, False, None, entries).splitlines()
        self.assertEqual(lines[0], '🟢 Démon en route · 🟢 Claude activé', 'one status line, as the two pills of the window, a dot in front of each')
        self.assertEqual(lines[1], 'Pull requests (9) · 7 à traiter :', 'the total, then what asks for something')
        shown = [line.split(' — ')[0] for line in lines[2:7]]
        self.assertEqual([line.split(' ')[2] for line in shown], ['#3', '#5', '#6', '#2', '#7'], 'attention, questions, given up, then ready: most urgent first')
        self.assertIn('Décision attendue (2)', lines[3])
        self.assertEqual(lines[7], '+ 2 autres à traiter · 2 autres en cours', 'what is not listed is counted, not hidden')
        self.assertEqual(len(lines), 8)

    def test_the_rows_that_lead_somewhere_are_the_pull_requests_listed(self):
        entries = [{'repo': 'api', 'pr': 12, 'title': 'fix', 'ready': True}, {'repo': 'web', 'pr': 3, 'state': 'done'}]
        rows = traysummary.rows(True, False, None, entries)
        self.assertEqual([(r['kind'], r['target']) for r in rows], [('status', None), ('heading', None), ('pr', 'api#12'), ('pr', 'web#3')],
                         'the state and the heading are text: nothing to click; every pull request listed leads to its card')
        self.assertEqual(rows[2]['text'], '• api #12 · fix — Prête à merger ✓')
        self.assertEqual(rows[3]['text'], '• web #3 — Terminée')

    def test_the_pull_requests_in_progress_are_listed_after_those_that_ask_something(self):
        entries = [{'repo': 'a', 'pr': 1, 'state': 'done'}, {'repo': 'a', 'pr': 2, 'state': 'watching'}, {'repo': 'a', 'pr': 3, 'title': 'broken', 'needsAttention': 'x'},
                   {'repo': 'a', 'pr': 4, 'state': 'paused'}]
        lines = traysummary.tooltip_text(True, False, None, entries).splitlines()
        self.assertEqual([line.split(' — ')[0] for line in lines[2:]], ['• a #3 · broken', '• a #2', '• a #4', '• a #1'], 'attention first, finished last')
        many = [{'repo': 'a', 'pr': n, 'state': 'watching'} for n in range(1, 9)]
        self.assertEqual(traysummary.tooltip_text(True, False, None, many).splitlines()[-1], '+ 3 autres en cours')

    def test_nothing_to_do_and_no_pull_request(self):
        quiet = traysummary.tooltip_text(True, False, None, [{'repo': 'a', 'pr': 1, 'state': 'watching'}, {'repo': 'a', 'pr': 2, 'state': 'paused'}]).splitlines()
        self.assertEqual(quiet[1:], ['Pull requests (2) · rien à traiter :', '• a #1 — Surveillée', '• a #2 — En pause'])
        self.assertEqual(traysummary.tooltip_text(True, False, None, []).splitlines()[1:], ['Aucune pull request.'])

    def test_a_line_stays_short_so_the_menu_stays_narrow(self):
        long_title = 'fix(composition): suggester no longer drops the searched item when the tree is filtered'
        line = traysummary.rows(True, False, None, [{'repo': 'acme-server', 'pr': 6130, 'title': long_title, 'ready': True}])[2]['text']
        self.assertLessEqual(len(line), traysummary.LINE)
        self.assertTrue(line.startswith('• acme-server #6130 · fix(composition)') and line.endswith('… — Prête à merger ✓'))

    def test_the_toggle_says_what_it_will_do(self):
        self.assertEqual(traysummary.toggle_of(True, False), ('Désactiver la surveillance', 'stop'))
        self.assertEqual(traysummary.toggle_of(False, False), ('Activer la surveillance', 'start'))
        self.assertEqual(traysummary.toggle_of(True, True), ('Activer la surveillance', 'start'), 'stopped by the schedule: it starts anyway')

    def test_the_state_line_follows_the_daemon_the_schedule_and_the_suspension(self):
        first = lambda *args, **kwargs: traysummary.tooltip_text(*args, **kwargs).splitlines()[0]  # noqa: E731
        self.assertEqual(first(False, False, None, []), '🔴 Démon arrêté · 🟢 Claude activé')
        self.assertEqual(first(True, True, '14:30', []), '🟠 Démon en pause (horaires) jusqu’à 14:30 · 🟢 Claude activé')
        self.assertEqual(first(True, False, None, [], suspension={'kind': 'usage'}), '🟢 Démon en route · 🟠 Claude désactivé (forfait presque épuisé)')
        self.assertEqual(first(True, False, None, [], suspension={'kind': 'tokens'}), '🟢 Démon en route · 🔴 Claude désactivé (plus de jetons)')

    def test_the_dots_say_how_the_daemon_and_claude_are_doing(self):
        def dots(*args, **kwargs):
            return traysummary.rows(*args, **kwargs)[0]['dots']
        self.assertEqual(dots(True, False, None, []), ['green', 'green'])
        self.assertEqual(dots(False, False, None, []), ['red', 'green'], 'stopped: it needs the user')
        self.assertEqual(dots(True, True, '14:30', []), ['orange', 'green'], 'waiting for the hours: it starts again by itself')
        self.assertEqual(dots(True, False, None, [], suspension={'kind': 'usage'}), ['green', 'orange'], 'the quota comes back by itself')
        self.assertEqual(dots(True, False, None, [], suspension={'kind': 'tokens'}), ['green', 'red'], 'only the user lifts it')

    def test_the_labels_are_those_of_the_window(self):
        from prwatch.actions import status as pwstatus
        entries = [{'repo': 'a', 'pr': 1, 'state': 'gave-up'}, {'repo': 'a', 'pr': 2, 'needsAttention': 'x'}, {'repo': 'a', 'pr': 3, 'ready': True}]
        text = traysummary.tooltip_text(True, False, None, entries)
        for entry in entries:
            self.assertIn(f"a #{entry['pr']} — {pwstatus.status(entry)['label']}", text)
