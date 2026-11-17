"""Tests for run_batch2 (drone-gnss-rfi study 1, batch 2). Standard library only: python -m unittest -v test_batch2

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_batch2 as rb  # noqa: E402


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


class TestSelect(unittest.TestCase):
    def setUp(self):
        ids = ["log%d" % i for i in range(40)]
        self.sample = [{"log_id": i, "v": "v" + i[3:]} for i in sorted(ids, key=h)]

    def test_top_half_in_order(self):
        got, info = rb.select_batch2(self.sample)
        self.assertEqual(rb.START, "8")
        top = [e for e in self.sample if h(e["log_id"])[0] in "89abcdef"]
        self.assertEqual(got, top)
        self.assertTrue(all(h(e["log_id"]) < "8" for e in self.sample if e not in got))
        self.assertEqual(info["sample_vehicles"], 40)
        self.assertEqual(info["below_start"] + info["batch2_candidates"], 40)
        self.assertEqual(info["batch2_candidates"], len(top))
        self.assertEqual(info["start"], "8")

    def test_bottom_of_order_never_selected(self):
        got, _ = rb.select_batch2(self.sample)
        bottom = [e for e in self.sample if h(e["log_id"]) < "8"]
        if got and bottom:
            self.assertGreater(self.sample.index(got[0]), self.sample.index(bottom[-1]))

    def test_bounds(self):
        self.assertEqual(rb.select_batch2(self.sample, "")[0], self.sample)
        got, info = rb.select_batch2(self.sample, "g")
        self.assertEqual(got, [])
        self.assertEqual(info["below_start"], 40)

    def test_hex_edges(self):
        self.assertTrue("8" + "0" * 63 >= rb.START)
        self.assertTrue("a" + "0" * 63 >= rb.START)
        self.assertFalse("7" + "f" * 63 >= rb.START)

    def test_empty(self):
        self.assertEqual(rb.select_batch2([]), ([], {"start": "8", "sample_vehicles": 0, "below_start": 0,
                                                     "batch2_candidates": 0}))


class TestHelpers(unittest.TestCase):
    def test_sha_hex(self):
        self.assertEqual(rb.sha_hex("abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(rb.sha_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())

    def test_norm(self):
        self.assertEqual(rb.norm(b"a\r\nb\n"), b"a\nb\n")

    def test_parse_rows(self):
        raw = b'{"log_id": "a"}\n\n{"log_id": "b", "m": null}\r\n  \n'
        self.assertEqual(rb.parse_rows(raw), [{"log_id": "a"}, {"log_id": "b", "m": None}])

    def test_pooled(self):
        rows1 = [{"log_id": "a"}, {"log_id": "b"}]
        rows2 = [{"log_id": "b", "x": 1}, {"log_id": "c"}]
        pool, dropped = rb.pooled(rows1, rows2)
        self.assertEqual([r["log_id"] for r in pool], ["a", "b", "c"])
        self.assertEqual(pool[1], {"log_id": "b"})
        self.assertEqual(dropped, 1)

    def test_pooled_disjoint(self):
        pool, dropped = rb.pooled([{"log_id": "a"}], [{"log_id": "c"}])
        self.assertEqual(len(pool), 2)
        self.assertEqual(dropped, 0)

    def test_own_files_named(self):
        self.assertEqual(sorted(rb.OWN.values()), ["batch2/PLAN-batch2.md", "batch2/run_batch2.py"])


if __name__ == "__main__":
    unittest.main()
