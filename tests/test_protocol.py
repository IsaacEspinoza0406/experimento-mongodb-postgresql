import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from config.matrix import matrix
from kernel_flush import flush
from config.delivery_schedule import schedule
from deadline_policy import admission, policy


class ProtocolTests(unittest.TestCase):
    def test_deadline_exact_boundary_and_observed_overhead(self):
        cell = matrix()[0]
        cutoff = policy()['load_cutoff_epoch']
        self.assertEqual(policy()['deadline_iso'], '2026-11-01T22:00:00-06:00')
        self.assertTrue(admission(cell, cutoff-510)['allowed'])
        self.assertFalse(admission(cell, cutoff-509)['allowed'])
        slower = admission(cell, cutoff-510, [100])
        self.assertFalse(slower['allowed'])
        self.assertEqual(slower['required_seconds'], 570)

    def test_delivery_first_pass_balance_and_optional_only_c50(self):
        cells = schedule()
        self.assertEqual(len(cells), 72)
        self.assertEqual(len({c['key'] for c in cells}), 72)
        self.assertEqual(len({(c['engine'],c['profile']) for c in cells[:6]}), 6)
        self.assertTrue(all(c['run_number'] == 1 for c in cells[:54]))
        self.assertTrue(all(c['run_number'] == 2 and c['concurrency'] == 50 for c in cells[54:]))
        self.assertEqual({(c['engine'],c['profile'],c['query_id'],c['variant'],c['concurrency']) for c in cells[:54]},
                         {(c['engine'],c['profile'],c['query_id'],c['variant'],c['concurrency']) for c in matrix()})
    def test_matrix_coverage_no_duplicate_q1(self):
        cells = matrix()
        self.assertEqual(len(cells), 162)
        self.assertEqual(len({c['key'] for c in cells}), 162)
        self.assertEqual(sum(c['query_id'] == 'Q1' for c in cells), 54)
        self.assertEqual(sum(c['query_id'] == 'Q2' for c in cells), 108)
        for engine in ['mongodb', 'postgresql']:
            self.assertEqual(sum(c['engine'] == engine for c in cells), 81)
        stored = json.loads(Path('config/matrix.json').read_text())['runs']
        self.assertEqual(stored, cells)

    def test_flush_refuses_foreign_container_without_mutation(self):
        with patch('kernel_flush.command', return_value='foreign|another-project|db') as command:
            with self.assertRaises(RuntimeError):
                flush('eva02-jsonbench', 'id')
            self.assertEqual(command.call_count, 1)

    def test_flush_refuses_different_kernel(self):
        with patch('kernel_flush.command', side_effect=['engine|eva02-jsonbench|mongo', 'boot-one', 'boot-two']) as command:
            with self.assertRaises(RuntimeError):
                flush('eva02-jsonbench', 'id')
            self.assertEqual(command.call_count, 3)

    def test_flush_refuses_active_load(self):
        with patch('kernel_flush.command', return_value='load|eva02-jsonbench|runner') as command:
            with self.assertRaises(RuntimeError):
                flush('eva02-jsonbench', 'id')
            self.assertEqual(command.call_count, 1)


if __name__ == '__main__':
    unittest.main()
