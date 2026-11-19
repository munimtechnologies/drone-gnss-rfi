"""Unit tests for run_match_pooled.py (standard library only; no network).

    python -m unittest -v test_match_pooled

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_match_pooled as rp  # noqa: E402


class TestBasics(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(rp.norm(b"a\r\nb\nc\r\n"), b"a\nb\nc\n")

    def test_sha_hex(self):
        self.assertEqual(rp.sha_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(rp.sha_hex("abc"), hashlib.sha256(b"abc").hexdigest())

    def test_parse_rows_skips_blank_lines(self):
        rows = rp.parse_rows(b'{"log_id": "a"}\n\n  \n{"log_id": "b", "m": null}\n')
        self.assertEqual([r["log_id"] for r in rows], ["a", "b"])
        self.assertIsNone(rows[1]["m"])

    def test_paths(self):
        self.assertEqual(rp.OWN["run_match_pooled.py"], "study2/run_match_pooled.py")
        self.assertEqual(rp.OWN["PLAN-matched-pooled.md"], "study2/PLAN-matched-pooled.md")
        self.assertEqual(rp.FROZEN, ("study2/PLAN-matched.md", "study2/gpsjam_match.py", "study2/run_match.py"))
        self.assertEqual(len(rp.PREDICTIONS), 4)


class TestPooling(unittest.TestCase):
    def test_pooled_drops_batch2_overlap(self):
        r1 = [{"log_id": "a"}, {"log_id": "b"}]
        r2 = [{"log_id": "b", "x": 2}, {"log_id": "c"}]
        pool, dropped = rp.pooled(r1, r2)
        self.assertEqual([r["log_id"] for r in pool], ["a", "b", "c"])
        self.assertEqual(dropped, 1)
        self.assertNotIn("x", pool[1])

    def test_pooled_no_overlap(self):
        pool, dropped = rp.pooled([{"log_id": "a"}], [{"log_id": "c"}, {"log_id": "d"}])
        self.assertEqual(len(pool), 3)
        self.assertEqual(dropped, 0)

    def test_split_records(self):
        recs = [{"log_id": "a"}, {"log_id": "c"}, {"log_id": "x"}, {"log_id": "b"}]
        b1, b2 = rp.split_records(recs, {"a", "b"}, {"c", "b"})
        self.assertEqual([r["log_id"] for r in b1], ["a", "b"])
        self.assertEqual([r["log_id"] for r in b2], ["c"])


class TestHeadline(unittest.TestCase):
    def test_batch1_held_replicated(self):
        h = rp.headline("held", "held", "held")
        self.assertEqual((h["headline"], h["source"], h["replicated"]), ("held", "batch 1", True))

    def test_batch1_held_not_replicated(self):
        h = rp.headline("held", "too few", "held")
        self.assertEqual((h["headline"], h["source"], h["replicated"]), ("held", "batch 1", False))
        self.assertEqual(h["batch2_alone"], "too few")

    def test_batch1_failed_stays_failed_even_if_pooled_holds(self):
        h = rp.headline("failed", "held", "held")
        self.assertEqual((h["headline"], h["source"], h["replicated"]), ("failed", "batch 1", False))
        self.assertEqual(h["pooled"], "held")

    def test_batch1_failed_replicated(self):
        h = rp.headline("failed", "failed", "failed")
        self.assertTrue(h["replicated"])

    def test_batch1_too_few_uses_pooled(self):
        h = rp.headline("too few", "failed", "held")
        self.assertEqual((h["headline"], h["source"], h["replicated"]), ("held", "pooled", None))

    def test_pooled_too_few_stays_too_few(self):
        h = rp.headline("too few", "too few", "too few")
        self.assertEqual(h["headline"], "too few")

    def test_missing_batch1_is_pending(self):
        h = rp.headline(None, "held", "held")
        self.assertEqual((h["headline"], h["source"]), ("pending", None))

    def test_headlines_from_grades(self):
        g1 = {"M1_spoof_exposed_vs_not": {"result": "too few"}, "M2_spoof_dose": {"result": "failed"},
              "known": 12}
        g2 = {"M1_spoof_exposed_vs_not": {"result": "held"}, "M2_spoof_dose": {"result": "failed"}}
        gp = {"M1_spoof_exposed_vs_not": {"result": "held"}, "M2_spoof_dose": {"result": "held"}}
        hs = rp.headlines(g1, g2, gp)
        self.assertEqual(set(hs), set(rp.PREDICTIONS))
        self.assertEqual(hs["M1_spoof_exposed_vs_not"]["headline"], "held")
        self.assertEqual(hs["M1_spoof_exposed_vs_not"]["source"], "pooled")
        self.assertEqual(hs["M2_spoof_dose"]["headline"], "failed")
        self.assertTrue(hs["M2_spoof_dose"]["replicated"])
        self.assertEqual(hs["M3_jam_disarmed_exposed_vs_not"]["headline"], "pending")
        self.assertEqual(rp.result_of(None, "M1_spoof_exposed_vs_not"), None)
        self.assertEqual(rp.result_of(g1, "known"), None)


if __name__ == "__main__":
    unittest.main()
