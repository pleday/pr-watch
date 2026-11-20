import unittest

from prwatch.core import journal

SAMPLE = """2026-11-17 10:00:01 daemon started (pid 12)
2026-11-17 10:00:07 my-repo#2006: session in use, 1 event(s) kept for later
2026-11-17 10:00:08 API 403 on /repositories/x/commit/e418edb16a12/statuses
2026-11-17 10:01:07 my-repo#2006: session in use, 1 event(s) kept for later
2026-11-17 10:01:08 API 403 on /repositories/x/commit/e418edb16a12/statuses
2026-11-17 10:04:00 my-repo#2006: resuming session 65f4 for 2 event(s)
2026-11-17 10:05:00 my-repo#2006: claude exited 0
Le pipeline a échoué.

- **Cause :** `npmAuthToken` absent
  - détail imbriqué
1. première étape

PR-WATCH: ATTENTION le relecteur demande un choix
2026-11-17 10:05:01 NOTIFY PR 2006 : votre attention est nécessaire: le relecteur demande un choix
2026-11-18 09:00:00 NOTIFY PR 2006 prête à être mergée: repo : titre
2026-11-18 09:01:00 my-repo#2006: claude exited 1
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.entries = journal.parse(SAMPLE)

    def test_entries_and_pr_key(self):
        self.assertEqual(len(self.entries), 10)
        self.assertEqual(self.entries[1]['pr'], 'my-repo#2006')
        self.assertTrue(self.entries[1]['message'].startswith('session in use'))

    def test_report_lines_belong_to_their_entry(self):
        self.assertEqual(len(self.entries[6]['details']), 7)

    def test_kinds(self):
        kinds = [e['kind'] for e in self.entries]
        self.assertEqual(kinds, ['system', 'muted', 'error', 'muted', 'error', 'claude', 'ok', 'attention', 'notify', 'error'])


class CollapseTest(unittest.TestCase):
    def test_alternating_noise_is_folded_even_when_not_consecutive(self):
        folded = journal.collapse(journal.parse(SAMPLE))
        waiting = [e for e in folded if e['kind'] == 'muted']
        self.assertEqual([e['count'] for e in waiting], [2])
        self.assertEqual(waiting[0]['last_time'], '10:01:07')
        api = [e for e in folded if e['message'].startswith('API 403')]
        self.assertEqual([e['count'] for e in api], [2])

    def test_a_significant_event_ends_the_group(self):
        text = (
            '2026-11-17 10:00:00 a#1: session in use, 1 event(s) kept for later\n'
            '2026-11-17 10:01:00 a#1: resuming session x for 1 event(s)\n'
            '2026-11-17 10:02:00 a#1: session in use, 1 event(s) kept for later\n'
        )
        self.assertEqual(len(journal.collapse(journal.parse(text))), 3)

    def test_entries_with_a_report_are_never_folded(self):
        self.assertEqual(len([e for e in journal.collapse(journal.parse(SAMPLE)) if e['kind'] == 'ok']), 1)


class FiltersTest(unittest.TestCase):
    def setUp(self):
        self.folded = journal.collapse(journal.parse(SAMPLE))

    def test_important_keeps_notifications_and_errors(self):
        self.assertEqual({e['kind'] for e in self.folded if journal.visible(e, 'important')}, {'error', 'attention', 'notify'})

    def test_claude_mode(self):
        self.assertEqual({e['kind'] for e in self.folded if journal.visible(e, 'claude')}, {'claude', 'ok', 'attention', 'notify'})

    def test_search_also_reads_the_reports(self):
        self.assertTrue(any(journal.visible(e, 'all', 'npmauthtoken') for e in self.folded))


class MarkdownTest(unittest.TestCase):
    def test_runs(self):
        self.assertEqual(journal.runs('a **b** et `c`'), [('a ', None), ('b', 'bold'), (' et ', None), ('c', 'code')])

    def test_bullets(self):
        line = journal.detail_line('- **Cause :** `x`')
        self.assertEqual((line['bullet'], line['runs'][0]), ('•', ('Cause :', 'bold')))
        nested = journal.detail_line('  - sous')
        self.assertEqual((nested['bullet'], nested['indent']), ('◦', 1))
        self.assertEqual(journal.detail_line('1. première')['bullet'], '1.')

    def test_verdict(self):
        self.assertEqual(journal.detail_line('PR-WATCH: ATTENTION le choix')['verdict'], 'ATTENTION')
        self.assertEqual(journal.detail_line('PR-WATCH: OK')['verdict'], 'OK')


class PagingTest(unittest.TestCase):
    def test_latest(self):
        self.assertEqual(journal.latest([1, 2, 3], 2), ([2, 3], 1))
        self.assertEqual(journal.latest([1, 2, 3], 5), ([1, 2, 3], 0))
        self.assertEqual(journal.latest([1, 2, 3], 0), ([1, 2, 3], 0))


if __name__ == '__main__':
    unittest.main()
