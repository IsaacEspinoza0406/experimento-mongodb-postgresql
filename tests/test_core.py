import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from synthetic_data_generator import generate, write_dataset
from benchmark_runner.metrics import HEADER, counter_delta, percentile, summarize, write_csv
from benchmark_runner.semantics import q1_reference, q1_compare, q2_compare


class CoreTests(unittest.TestCase):
    def test_generator_reproducibility_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = write_dataset(Path(tmp) / 'a.jsonl', 2000, 243751)
            b = write_dataset(Path(tmp) / 'b.jsonl', 2000, 243751)
            c = write_dataset(Path(tmp) / 'c.jsonl', 2000, 243752)
            self.assertEqual(a['sha256'], b['sha256'])
            self.assertNotEqual(a['sha256'], c['sha256'])
            with self.assertRaises(FileExistsError):
                write_dataset(Path(tmp) / 'a.jsonl', 1, 1)
        orders = list(generate(2000))
        self.assertEqual(len({o['order_id'] for o in orders}), 2000)
        self.assertTrue(2.8 < sum(len(o['items']) for o in orders) / len(orders) < 3.2)
        for o in orders:
            self.assertEqual(o['payment']['final_total'], o['payment']['totals']['final_total'])

    def test_boundaries_and_topk_ties(self):
        order = next(generate(1))
        order['items'] = [{'price': 50, 'qty': 9, 'category': 'x'}, {'price': 51, 'qty': 2, 'category': 'x'}]
        ref = q1_reference([order])
        self.assertEqual(list(ref.values())[0], (102, 102, 1))
        q1_compare(ref, ref)
        orders = []
        for i, (status, date, total) in enumerate([('completed', '2026-01-01T00:00:00Z', 100),
                                                  ('completed', '2026-02-01T00:00:00Z', 90),
                                                  ('completed', '2026-02-01T00:00:00Z', 90),
                                                  ('pending', '2026-02-01T00:00:00Z', 999),
                                                  ('completed', '2025-12-31T23:59:59Z', 999)]):
            o = copy.deepcopy(order)
            o.update(order_id=str(i), timestamp=date)
            o['payment'].update(status=status, final_total=total)
            orders.append(o)
        customer = order['customer']['id']
        q2_compare(orders, [('0', customer, 100), ('2', customer, 90)], k=2)
        with self.assertRaises(AssertionError):
            q2_compare(orders, [('1', customer, 90), ('2', customer, 90)], k=2)

    def test_metrics_boundaries_and_missing(self):
        self.assertEqual(percentile([10, 20, 30, 40], .95), 38.5)
        self.assertIsNone(percentile([], .99))
        self.assertIsNone(counter_delta(10, 1))
        self.assertIsNone(counter_delta(None, 1))
        rows = [{'start': s, 'end': e, 'ok': ok, 'duration_ms': (e-s)*1000}
                for s, e, ok in [(9, 11, True), (11, 12, True), (12, 13, False), (19, 20, True)]]
        summary = summarize(rows, 10, 20)
        self.assertEqual(summary['throughput_qps'], .2)
        self.assertEqual(summary['latency_samples'], 1)
        self.assertEqual(summary['warmup_crossings_completed'], 1)
        self.assertEqual(summary['errors_in_window'], 1)
        self.assertEqual(summary['crossings_end'], 1)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'out.csv'
            row = dict.fromkeys(HEADER)
            row['status'] = 'error'
            write_csv(p, row)
            self.assertEqual(p.read_text().splitlines()[0], ','.join(HEADER))
            with p.open() as stream:
                self.assertEqual(list(csv.DictReader(stream))[0]['max_rss_mb'], '')
            with self.assertRaises(FileExistsError):
                write_csv(p, row)


if __name__ == '__main__':
    unittest.main()
