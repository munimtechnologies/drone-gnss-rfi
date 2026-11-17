"""Tests for gpsjam_match (drone-gnss-rfi study 2). Standard library only: python -m unittest -v test_gpsjam_match

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpsjam_match as gm  # noqa: E402

CENTRES = {"a": (54.5, 20.5), "b": (54.2, 20.9), "c": (10.0, 10.0), "d": (55.1, 21.3)}


def centre(hx):
    return CENTRES[hx]


def rec(share, spoof_reporting=True, spoof_any=False, manipulated=False, jam_dis=None, n_dis=0, jam_max=None):
    return {"share": share, "spoof_reporting": spoof_reporting, "spoof_any": spoof_any, "spoof_sustained": False,
            "jam_reporting": False, "jam_warn": False, "manipulated": manipulated, "jam_dis": jam_dis,
            "n_dis": n_dis, "jam_max": jam_max, "cell": [54, 20], "region": "baltic", "day_status": "ok"}


class TestFiles(unittest.TestCase):
    def test_manifest(self):
        text = ("date,suspect,num_bad_aircraft_hexes,source\n2022-02-14,true,89,adsbexchange\n"
                "2026-11-16,false,483,merged\nbad line\n")
        self.assertEqual(gm.parse_manifest(text), {"2022-02-14": True, "2026-11-16": False})

    def test_manifest_header(self):
        with self.assertRaises(ValueError):
            gm.parse_manifest("day,flag\n2022-02-14,true\n")

    def test_parse_day(self):
        text = ("hex,count_good_aircraft,count_bad_aircraft\n"
                "a,10,2\nb,5,1\nc,100,0\nd,7,3\nzz,1,1\nb,x,1\n")
        cache = {}
        got = gm.parse_day(text, centre, {(54, 20), (55, 21)}, cache)
        # a and b both fall in cell (54, 20); d in (55, 21); c is not wanted; zz has no centre; 'b,x,1' does not parse
        self.assertEqual(got, {(54, 20): [15, 3, 2], (55, 21): [7, 3, 1]})
        self.assertIsNone(cache["zz"])
        self.assertEqual(cache["c"], (10, 10))

    def test_parse_day_column_order(self):
        text = "count_bad_aircraft,hex,count_good_aircraft\n4,a,6\n"
        self.assertEqual(gm.parse_day(text, centre, {(54, 20)}), {(54, 20): [6, 4, 1]})

    def test_parse_day_header(self):
        with self.assertRaises(ValueError):
            gm.parse_day("h,g,b\na,1,1\n", centre, {(54, 20)})


class TestExposure(unittest.TestCase):
    def test_block(self):
        self.assertEqual(len(gm.block_cells((0, 0))), 9)
        b = gm.block_cells((89, 179))
        self.assertEqual(len(b), 6)
        self.assertIn((89, -180), b)
        self.assertIn((88, 178), b)
        self.assertNotIn((90, 179), b)

    def test_exposure(self):
        day = {(54, 20): [80, 20, 5], (55, 21): [10, 0, 1], (57, 20): [0, 50, 2]}
        e = gm.exposure(day, (54, 20))
        self.assertEqual((e["visits"], e["hexes"]), (110, 6))
        self.assertAlmostEqual(e["share"], 20 / 110)
        self.assertEqual(gm.exposure(day, (60, 60)), {"share": None, "visits": 0, "hexes": 0})
        low = gm.exposure({(0, 0): [20, 5, 1]}, (0, 0))
        self.assertIsNone(low["share"])
        self.assertEqual(low["visits"], 25)

    def test_candidates_and_record(self):
        located = {"first_fix": {"cell": [1, 1], "region": "rest"}}
        rows = [
            # flown 2024-03-10 by the receiver clock, uploaded 2024-03-12: dated by the receiver
            {"log_id": "x1", "date": "2024-03-12", "m": {"first_fix": {"cell": [54, 20], "region": "baltic"},
                                                        "utc_date": "2024-03-10",
                                                        "teleports": 1, "jam_ind_max": 90,
                                                        "armed_split": {"n_disarmed": 30,
                                                                        "jam_ind_median_disarmed": 55}},
             "flags": {"spoof_reporting": True, "spoof_any": True, "spoof_sustained": False,
                       "jam_reporting": True, "jam_warn": False, "manipulated": True}},
            # measured, not located
            {"log_id": "x2", "date": "2024-03-10", "m": {"first_fix": None, "utc_date": "2024-03-10"},
             "flags": {"spoof_reporting": False}},
            # receiver date before GPSJam's first day
            {"log_id": "x3", "date": "2023-05-01", "m": dict(located, utc_date="2021-05-01"),
             "flags": {"spoof_reporting": False}},
            {"log_id": "x4", "status": "missing"},
            # receiver date more than a day after the upload: not dated
            {"log_id": "x5", "date": "2024-03-12", "m": dict(located, utc_date="2024-05-01"),
             "flags": {"spoof_reporting": False}},
            # receiver date one day after the upload date (time zones): kept
            {"log_id": "x6", "date": "2024-03-12", "m": {"first_fix": {"cell": [10, 10], "region": "rest"},
                                                        "utc_date": "2024-03-13"},
             "flags": {"spoof_reporting": False}},
            # no receiver date: not dated (the upload date is never used)
            {"log_id": "x7", "date": "2024-03-12", "m": dict(located), "flags": {"spoof_reporting": False}},
        ]
        cands, needed, counts = gm.candidates(rows)
        self.assertEqual([c[0]["log_id"] for c in cands], ["x1", "x6"])
        self.assertEqual(cands[0][1:], ("2024-03-10", (54, 20)))
        self.assertEqual(cands[1][1:], ("2024-03-13", (10, 10)))
        self.assertEqual(counts, {"measured": 6, "located": 5, "dated": 2})
        self.assertEqual(set(needed), {"2024-03-10", "2024-03-13"})
        self.assertEqual(len(needed["2024-03-10"]), 9)
        r = gm.record(cands[0][0], "2024-03-10", (54, 20), {"share": 0.2, "visits": 100, "hexes": 7}, "ok")
        self.assertEqual((r["share"], r["spoof_any"], r["manipulated"], r["jam_dis"], r["n_dis"], r["teleports"]),
                         (0.2, True, True, 55, 30, 1))
        self.assertEqual(r["cell"], [54, 20])
        n = gm.record(cands[0][0], "2024-03-10", (54, 20), None, "missing file")
        self.assertIsNone(n["share"])


class TestStats(unittest.TestCase):
    def test_auc_separated(self):
        auc, p = gm.auc_mw([3, 4], [1, 2])
        self.assertEqual(auc, 1.0)
        self.assertTrue(0.10 < p < 0.15)          # z = 1.5 / sqrt(5/3) = 1.162, p = 0.1226

    def test_auc_all_tied(self):
        self.assertEqual(gm.auc_mw([1, 1], [1, 1]), (0.5, 1.0))

    def test_auc_empty(self):
        self.assertEqual(gm.auc_mw([], [1]), (None, None))

    def test_fisher(self):
        self.assertAlmostEqual(gm.fisher_greater(1, 0, 0, 1), 0.5)
        self.assertAlmostEqual(gm.fisher_greater(0, 1, 1, 0), 1.0)


class TestGrade(unittest.TestCase):
    def sample(self, n_exposed=12):
        # exposed (share 0.2): 8 of 12 flag spoofing, 3 manipulated; unexposed (0.005): 6 of 60 flag, 2 manipulated
        recs = []
        for i in range(n_exposed):
            recs.append(rec(0.2, spoof_any=i < 8, manipulated=i < 3))
        for i in range(60):
            recs.append(rec(0.005, spoof_any=i < 6, manipulated=i < 2))
        recs.append(rec(None, spoof_any=True))   # unknown exposure: left out
        return recs

    def test_m1_m2_m4_held(self):
        g = gm.grade(self.sample())
        self.assertEqual((g["known"], g["exposed"]), (72, 12))
        m1 = g["M1_spoof_exposed_vs_not"]
        self.assertEqual((m1["exposed"], m1["unexposed"], m1["result"]), ([8, 12], [6, 60], "held"))
        self.assertAlmostEqual(m1["diff"], round(8 / 12 - 6 / 60, 4))
        m2 = g["M2_spoof_dose"]
        # cases: 8 at 0.2 and 6 at 0.005; others: 4 at 0.2 and 54 at 0.005 -> AUC = (432 + 16 + 162) / 812
        self.assertEqual(m2["n"], [14, 58])
        self.assertEqual(m2["auc"], round(610 / 812, 4))
        self.assertEqual(m2["result"], "held")
        m4 = g["M4_manipulated_exposed_vs_not"]
        self.assertEqual((m4["exposed"], m4["unexposed"], m4["result"]), ([3, 12], [2, 60], "held"))
        self.assertEqual(g["M3_jam_disarmed_exposed_vs_not"]["result"], "too few")

    def test_too_few_exposed(self):
        g = gm.grade(self.sample(n_exposed=9))
        self.assertEqual(g["M1_spoof_exposed_vs_not"]["result"], "too few")
        self.assertEqual(g["M4_manipulated_exposed_vs_not"]["result"], "too few")

    def test_m1_failed_when_no_difference(self):
        recs = [rec(0.2, spoof_any=i < 1) for i in range(10)] + [rec(0.001, spoof_any=i < 10) for i in range(100)]
        m1 = gm.grade(recs)["M1_spoof_exposed_vs_not"]
        self.assertEqual(m1["result"], "failed")
        self.assertAlmostEqual(m1["diff"], 0.0)

    def test_m3(self):
        recs = [rec(0.3, jam_dis=120, n_dis=25, jam_max=200) for _ in range(10)]
        recs += [rec(0.0, jam_dis=40, n_dis=25, jam_max=90) for _ in range(30)]
        recs += [rec(0.3, jam_dis=500, n_dis=19, jam_max=600)]          # too few disarmed samples: left out
        recs += [rec(0.3, jam_dis=0, n_dis=40, jam_max=0)]              # indicator never non-zero: left out
        m3 = gm.grade(recs)["M3_jam_disarmed_exposed_vs_not"]
        self.assertEqual(m3["n"], [10, 30])
        self.assertEqual(m3["auc"], 1.0)
        self.assertEqual(m3["result"], "held")
        recs = recs[1:]
        self.assertEqual(gm.grade(recs)["M3_jam_disarmed_exposed_vs_not"]["result"], "too few")

    def test_threshold_edge(self):
        g = gm.grade([rec(0.05)] + [rec(0.0499)])
        self.assertEqual(g["exposed"], 1)

    def test_describe(self):
        recs = self.sample()
        recs.append(dict(rec(None), day_status="missing file"))
        d = gm.describe(recs)
        self.assertEqual(d["known"], 72)
        self.assertEqual(d["day_status"], {"ok": 72, "low coverage": 1, "missing file": 1})
        self.assertEqual(d["bins"][0]["logs"], 60)
        self.assertEqual(d["bins"][4]["logs"], 12)
        self.assertEqual(d["bins"][4]["spoof_any"], 8)
        self.assertEqual(d["exposed_cells_1deg"]["54,20"]["logs"], 12)
        self.assertEqual(d["m1_other_thresholds"]["0.1"]["result"], "held")


if __name__ == "__main__":
    unittest.main()
