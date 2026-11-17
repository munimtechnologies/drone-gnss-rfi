"""Tests for stormwatch (drone-gnss-rfi study S1). Standard library only: python -m unittest -v test_stormwatch

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import calendar
import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stormwatch as sw  # noqa: E402


def T(y, mo, d, h=0, mi=0, s=0):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


def H(s):
    return hashlib.sha256(s.encode()).hexdigest()


def kp_line(y, mo, d, h, kp):
    return "%04d %02d %02d %04.1f %05.2f %11.5f %11.5f %6.3f %4d %d" % (y, mo, d, h, h + 1.5, 0.0, 0.0, kp, 0, 1)


def fill(start, end, v=2.0):
    return {t: v for t in range(start, end, 10800)}


def storm_kp():
    """Quiet (Kp 2) from 2024-03-01 to 2024-07-01 with event A on 10-11 May 2024 and event B on 20 May 2024."""
    kp = fill(T(2024, 3, 1), T(2024, 7, 1))
    kp[T(2024, 5, 10, 15)] = 7.667
    kp[T(2024, 5, 10, 18)] = 9.0
    kp[T(2024, 5, 11, 6)] = 8.333
    kp[T(2024, 5, 20, 0)] = 7.667
    kp[T(2024, 5, 25, 0)] = 7.333
    return kp


def row(fix, eph=1.0, epv=2.0, sats=12, utc=0, la=52.5, lo=13.4, lat=None, lon=None, t=0):
    return (t, fix, eph, epv, sats, utc, la, lo, lat, lon)


class TestKp(unittest.TestCase):
    def test_parse(self):
        text = "\n".join([
            "# LICENSE: CC BY 4.0",
            "#YYY MM DD hh.h hh._m        days      days_m     Kp   ap D",
            kp_line(2024, 5, 10, 15, 7.667),
            kp_line(2024, 5, 10, 18, 9.0),
            kp_line(2024, 5, 10, 21, -1.0),
            kp_line(2022, 1, 1, 0, 5.0),
            "garbage line",
            "a b c d e f g h i j",
            ""])
        self.assertEqual(sw.parse_kp(text), {T(2024, 5, 10, 15): 7.667, T(2024, 5, 10, 18): 9.0})

    def test_kp_at(self):
        kp = {T(2024, 5, 10, 18): 9.0}
        self.assertEqual(sw.kp_at(kp, T(2024, 5, 10, 18)), 9.0)
        self.assertEqual(sw.kp_at(kp, T(2024, 5, 10, 20, 59, 59)), 9.0)
        self.assertIsNone(sw.kp_at(kp, T(2024, 5, 10, 21)))
        self.assertIsNone(sw.kp_at(kp, T(2024, 5, 10, 17, 59, 59)))


class TestEvents(unittest.TestCase):
    def test_merge_order_and_filter(self):
        kp = storm_kp()
        kp[T(2022, 12, 31, 21)] = 9.0            # starts before 2023-01-01: dropped
        ev = sw.events(kp)
        self.assertEqual([e["id"] for e in ev], ["2024-05-10T15", "2024-05-20T00"])
        a, b = ev
        self.assertEqual((a["start"], a["end"], a["max_kp"], a["severe_intervals"]),
                         (T(2024, 5, 10, 15), T(2024, 5, 11, 9), 9.0, 3))
        self.assertEqual((b["start"], b["end"], b["max_kp"], b["severe_intervals"]),
                         (T(2024, 5, 20, 0), T(2024, 5, 20, 3), 7.667, 1))
        self.assertEqual(a["kind"], "real")

    def test_merge_boundary(self):
        self.assertEqual(len(sw.events({T(2024, 4, 1, 0): 8.0, T(2024, 4, 2, 3): 8.0})), 2)   # exactly 24 h apart
        one = sw.events({T(2024, 4, 1, 0): 8.0, T(2024, 4, 2, 0): 8.0})                     # 21 h apart
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0]["end"], T(2024, 4, 2, 3))

    def test_ties_earlier_first(self):
        ev = sw.events({T(2024, 3, 1, 0): 8.0, T(2024, 2, 1, 0): 8.0})
        self.assertEqual([e["id"] for e in ev], ["2024-02-01T00", "2024-03-01T00"])

    def test_late_event_dropped(self):
        self.assertEqual(sw.events({T(2026, 11, 15, 0): 8.0}), [])
        self.assertEqual(len(sw.events({T(2026, 11, 14, 21): 8.0})), 1)

    def test_placebo(self):
        kp = storm_kp()
        a = sw.events(kp)[0]
        p = sw.placebo(a, kp)
        self.assertEqual((p["id"], p["kind"], p["of"], p["shift_d"]), ("placebo-2024-05-10T15", "placebo",
                                                                        "2024-05-10T15", 28))
        self.assertEqual((p["start"], p["end"], p["max_kp"]), (T(2024, 4, 12, 15), T(2024, 4, 13, 9), 2.0))
        kp[T(2024, 4, 5, 0)] = 7.0                # storm-time Kp inside the 28-day placebo's clear range
        p = sw.placebo(a, kp)
        self.assertEqual((p["shift_d"], p["start"]), (56, T(2024, 3, 15, 15)))
        kp[T(2024, 3, 10, 0)] = 7.0
        self.assertIsNone(sw.placebo(a, kp))

    def test_placebo_needs_known_kp(self):
        kp = fill(T(2024, 4, 1), T(2024, 7, 1))
        kp[T(2024, 5, 10, 15)] = 9.0
        a = sw.events(kp)[0]
        self.assertIsNone(sw.placebo(a, kp))      # both clear ranges start before 2024-04-01

    def test_upload_windows(self):
        a = sw.events(storm_kp())[0]
        self.assertEqual(sw.upload_windows(a), {"storm": ("2024-05-10", "2024-05-13"),
                                                "control": ("2024-04-28", "2024-05-08")})
        ev = {"start": T(2024, 5, 10, 21), "end": T(2024, 5, 11, 0)}
        self.assertEqual(sw.upload_windows(ev)["storm"], ("2024-05-10", "2024-05-12"))

    def test_window_days(self):
        self.assertEqual(sw.window_days("2024-02-28", "2024-03-01"), ["2024-02-28", "2024-02-29", "2024-03-01"])
        self.assertEqual(sw.window_days("2024-03-02", "2024-03-01"), [])


class TestFlightOk(unittest.TestCase):
    def setUp(self):
        self.kp = storm_kp()
        self.kp[T(2024, 5, 11, 21)] = 7.0
        self.kp[T(2024, 5, 2, 0)] = 4.0
        self.a = sw.events(self.kp)[0]

    def test_storm_side(self):
        kp, a = self.kp, self.a
        self.assertTrue(sw.flight_ok("storm", a, T(2024, 5, 10, 19, 30), kp))
        self.assertTrue(sw.flight_ok("storm", a, T(2024, 5, 11, 22), kp))      # Kp 7, inside the 24 h margin
        self.assertFalse(sw.flight_ok("storm", a, T(2024, 5, 10, 12), kp))     # Kp 2
        self.assertFalse(sw.flight_ok("storm", a, T(2024, 5, 12, 10), kp))     # after the margin
        self.assertFalse(sw.flight_ok("storm", a, None, kp))
        self.assertFalse(sw.flight_ok("storm", a, T(2023, 1, 1), kp))          # no Kp known

    def test_control_side(self):
        kp, a = self.kp, self.a
        self.assertTrue(sw.flight_ok("control", a, T(2024, 5, 1, 12), kp))
        self.assertFalse(sw.flight_ok("control", a, T(2024, 5, 9, 12), kp))    # less than 2 days before
        self.assertFalse(sw.flight_ok("control", a, T(2024, 4, 20), kp))       # more than 14 days before
        self.assertFalse(sw.flight_ok("control", a, T(2024, 5, 2, 1), kp))     # Kp 4
        self.assertTrue(sw.flight_ok("control", a, T(2024, 4, 26, 15), kp))    # exactly 14 days before
        self.assertFalse(sw.flight_ok("control", a, T(2024, 5, 8, 15), kp))    # exactly 2 days before

    def test_placebo_storm_side(self):
        kp = self.kp
        p = sw.placebo(self.a, kp)
        self.assertEqual(p["start"], T(2024, 4, 12, 15))
        kp[T(2024, 4, 13, 0)] = 5.0
        self.assertTrue(sw.flight_ok("storm", p, T(2024, 4, 12, 16), kp))
        self.assertFalse(sw.flight_ok("storm", p, T(2024, 4, 13, 1), kp))      # Kp 5 is not quiet


class TestMeasure(unittest.TestCase):
    def test_measure(self):
        u0 = T(2024, 5, 10, 19, 30) * 1000000
        rows = [row(1, eph=50.0),
                row(3, utc=0),                                  # no receiver time yet: not the start
                row(3, eph=1.0, epv=2.0, sats=12, utc=u0),
                row(3, eph=3.0, epv=4.0, sats=10, utc=u0 + 200000),
                row(2, utc=u0 + 400000),
                row(5, eph=0.5, epv=0.8, sats=14),
                row(6, eph=0.02, epv=0.03, sats=15),
                row(6, eph=150.0, epv=0.0, sats=0),
                row(8),
                row(None)]
        m = sw.measure(rows)
        self.assertEqual(m["start_utc"], T(2024, 5, 10, 19, 30))
        self.assertEqual(m["start_hour"], "2024-05-10T19")
        self.assertEqual(m["cell"], [52, 13])
        self.assertEqual(m["n_after"], 7)
        self.assertEqual(m["loss_share"], 0.2857)
        self.assertEqual(m["n_eph"], 4)
        self.assertAlmostEqual(m["eph_med"], 0.75)
        self.assertAlmostEqual(m["epv_med"], 1.4)
        self.assertEqual((m["n_sats"], m["sats_med"]), (4, 13.0))
        self.assertTrue(m["rtk"])
        self.assertEqual((m["n_rtk"], m["rtk_fixed_share"]), (4, 0.5))
        self.assertFalse(sw.usable(m))                          # fewer than 10 samples
        self.assertNotIn("start_utc", sw.public(m))
        self.assertEqual(sw.public(m)["start_hour"], "2024-05-10T19")
        self.assertIsNone(sw.public(None))

    def test_old_int_position_and_south_west(self):
        u0 = T(2024, 5, 10) * 1000000
        m = sw.measure([row(3, utc=u0, la=None, lo=None, lat=525000000, lon=134000000)])
        self.assertEqual(m["cell"], [52, 13])
        m = sw.measure([row(3, utc=u0, la=-33.9, lo=-70.6)])
        self.assertEqual(m["cell"], [-34, -71])
        m = sw.measure([row(3, utc=u0, la=0.0, lo=0.0)])
        self.assertIsNone(m["cell"])
        self.assertEqual(m["n_after"], 1)

    def test_no_time_no_start(self):
        m = sw.measure([row(3, utc=0)] * 20)
        self.assertEqual((m["start_utc"], m["n_after"], m["loss_share"], m["eph_med"], m["rtk"], m["n_rtk"],
                          m["rtk_fixed_share"]), (None, 0, None, None, False, 0, None))
        self.assertFalse(sw.usable(m))
        self.assertFalse(sw.usable(None))

    def test_usable(self):
        u0 = T(2024, 5, 10) * 1000000
        m = sw.measure([row(3, utc=u0 + i * 100000) for i in range(10)])
        self.assertTrue(sw.usable(m))
        self.assertEqual((m["n_eph"], m["eph_med"], m["loss_share"], m["rtk"]), (10, 1.0, 0.0, False))

    def test_choose(self):
        topics = {("vehicle_gps_position", 0): {"rows": [1]}, ("sensor_gps", 1): {"rows": [1]},
                  ("sensor_gps", 0): {"rows": []}}
        self.assertEqual(sw.choose(topics), ("sensor_gps", 1))
        self.assertEqual(sw.choose({("vehicle_gps_position", 0): {"rows": [1]}}), ("vehicle_gps_position", 0))
        self.assertIsNone(sw.choose({("sensor_gps", 0): {"rows": []}}))
        self.assertEqual(set(sw.wanted()), {"sensor_gps", "vehicle_gps_position", "vehicle_gnss"})


class TestPairing(unittest.TestCase):
    def test_hour_gap(self):
        self.assertEqual(sw.hour_gap(T(2024, 5, 10, 23), T(2024, 5, 1, 1)), 2.0)
        self.assertEqual(sw.hour_gap(T(2024, 5, 10, 12), T(2024, 5, 1, 12, 30)), 0.5)

    def test_pick_control(self):
        cands = [("a", {"start_utc": T(2024, 5, 1, 8)}), ("b", {"start_utc": T(2024, 5, 2, 19)}),
                 ("c", {"start_utc": T(2024, 5, 3, 21)})]
        gap, (lid, _m) = sw.pick_control(T(2024, 5, 10, 20), cands)
        self.assertEqual((gap, lid), (1.0, "b"))
        self.assertIsNone(sw.pick_control(T(2024, 5, 10, 20), []))

    def test_candidates(self):
        a = sw.events(storm_kp())[0]
        es = [{"log_id": "L1", "v": "v1", "date": "2024-05-10"}, {"log_id": "L2", "v": "v1", "date": "2024-05-12"},
              {"log_id": "L3", "v": "v1", "date": "2024-05-01"}, {"log_id": "L4", "v": "v2", "date": "2024-05-11"},
              {"log_id": "L5", "v": "v3", "date": "2024-05-05"}, {"log_id": "L6", "v": "v1", "date": "2024-05-09"}]
        s, c, vs = sw.candidates(es, a)
        self.assertEqual(vs, ["v1"])
        self.assertEqual([e["log_id"] for _h, e in s["v1"]], sorted(["L1", "L2"], key=H))
        self.assertEqual([h for h, _e in s["v1"]], sorted([H("L1"), H("L2")]))
        self.assertEqual([e["log_id"] for _h, e in c["v1"]], ["L3"])
        self.assertEqual(set(s), {"v1", "v2"})
        self.assertEqual(set(c), {"v1", "v3"})

    def test_make_pair(self):
        kp = storm_kp()
        a = sw.events(kp)[0]
        sm = {"start_utc": T(2024, 5, 10, 19), "start_hour": "2024-05-10T19", "eph_med": 2.0}
        cm = {"start_utc": T(2024, 5, 1, 18), "start_hour": "2024-05-01T18", "eph_med": 1.0}
        p = sw.make_pair(a, "v1", ("S", sm), (1.0, ("C", cm)), kp)
        self.assertEqual((p["event"], p["kind"], p["v"], p["hour_gap"], p["days_apart"]),
                         ("2024-05-10T15", "real", "v1", 1.0, 9.0))
        self.assertEqual((p["storm"]["log_id"], p["storm"]["kp"], p["control"]["log_id"], p["control"]["kp"]),
                         ("S", 9.0, "C", 2.0))
        self.assertNotIn("start_utc", p["storm"]["m"])
        self.assertNotIn("start_utc", p["control"]["m"])


def mm(eph=1.0, sats=12, loss=0.0, rtk=None, n=50):
    return {"n_after": n, "n_eph": n, "n_sats": n, "eph_med": eph, "sats_med": sats, "loss_share": loss,
            "rtk": rtk is not None, "n_rtk": n if rtk is not None else 0, "rtk_fixed_share": rtk, "cell": [52, 13]}


def pair(sm, cm, kp=8.0, gap=1.0, event="E"):
    return {"event": event, "kind": "real", "v": "x", "storm": {"log_id": "s", "kp": kp, "m": sm},
            "control": {"log_id": "c", "kp": 1.0, "m": cm}, "hour_gap": gap}


def held_set(gap=1.0):
    return [pair(mm(1.5), mm(1.0), gap=gap) for _ in range(35)] + [pair(mm(0.9), mm(1.0), gap=gap) for _ in range(5)]


class TestStats(unittest.TestCase):
    def test_sign_p(self):
        self.assertEqual(sw.sign_p(0, 0), 1.0)
        self.assertEqual(sw.sign_p(10, 10), 1 / 1024)
        self.assertEqual(sw.sign_p(8, 10), 56 / 1024)
        self.assertEqual(sw.sign_p(0, 5), 1.0)
        self.assertEqual(sw.sign_p(3, 4), 5 / 16)

    def test_value(self):
        self.assertIsNone(sw.value(mm(n=9), "eph"))
        self.assertEqual(sw.value(mm(loss=0.1), "loss"), True)
        self.assertEqual(sw.value(mm(), "loss"), False)
        self.assertIsNone(sw.value(mm(), "rtk"))
        self.assertIsNone(sw.value(None, "eph"))
        with self.assertRaises(ValueError):
            sw.value(mm(), "other")

    def test_held_placebo_too_few(self):
        g = sw.grade(held_set(), [])
        p1 = g["P1_eph"]
        self.assertEqual((p1["pairs"], p1["worse"], p1["better"], p1["ties"]), (40, 35, 5, 0))
        self.assertEqual((p1["median_ratio"], p1["result"]), (1.5, "held"))
        self.assertLess(p1["p"], 0.05)
        self.assertEqual(g["P1b_eph_same_time_of_day"]["result"], "held")
        self.assertEqual(g["P2_sats"]["result"], "too few")
        self.assertEqual(g["P2_sats"]["ties"], 40)
        self.assertEqual(g["P3_fix_loss"]["result"], "too few")
        self.assertEqual((g["P4_rtk_fixed"]["pairs"], g["P4_rtk_fixed"]["result"]), (0, "too few"))
        self.assertEqual(g["P0_placebo_eph"]["result"], "too few")
        self.assertEqual(g["headline"], "storm-time accuracy was worse; the placebo had too few pairs to check")

    def test_same_time_of_day_subset(self):
        g = sw.grade(held_set(gap=5.0), [])
        self.assertEqual(g["P1_eph"]["result"], "held")
        self.assertEqual((g["P1b_eph_same_time_of_day"]["pairs"], g["P1b_eph_same_time_of_day"]["result"]),
                         (0, "too few"))

    def test_small_effect_fails(self):
        ps = [pair(mm(1.05), mm(1.0)) for _ in range(35)] + [pair(mm(0.95), mm(1.0)) for _ in range(5)]
        g = sw.grade(ps, [])
        self.assertLess(g["P1_eph"]["p"], 0.05)
        self.assertEqual((g["P1_eph"]["median_ratio"], g["P1_eph"]["result"]), (1.05, "failed"))
        self.assertEqual(g["headline"], "no storm effect on estimated horizontal accuracy at the pre-registered "
                                        "thresholds")

    def test_too_few(self):
        g = sw.grade([pair(mm(2.0), mm(1.0)) for _ in range(29)], [])
        self.assertEqual(g["P1_eph"]["result"], "too few")
        self.assertEqual(g["headline"], "too few pairs to test")

    def test_placebo_moved_and_clean(self):
        g = sw.grade(held_set(), held_set())
        self.assertEqual(g["P0_placebo_eph"]["result"], "moved")
        self.assertEqual(g["headline"], "storm-time accuracy was worse, but the placebo moved too: not attributed "
                                        "to the storms")
        clean = [pair(mm(1.2), mm(1.0)) for _ in range(20)] + [pair(mm(0.8), mm(1.0)) for _ in range(20)]
        g = sw.grade(held_set(), clean)
        self.assertEqual((g["P0_placebo_eph"]["median_ratio"], g["P0_placebo_eph"]["result"]), (1.0, "clean"))
        self.assertEqual(g["headline"], "storm-time accuracy was worse and the placebo was clean")

    def test_sats_loss_rtk(self):
        ps = [pair(mm(sats=9), mm(sats=12)) for _ in range(35)] + [pair(mm(sats=13), mm(sats=12)) for _ in range(5)]
        r = sw.grade(ps, [])["P2_sats"]
        self.assertEqual((r["worse"], r["better"], r["median_drop"], r["result"]), (35, 5, 3.0, "held"))
        ps = [pair(mm(loss=0.1), mm()) for _ in range(11)] + [pair(mm(), mm(loss=0.2))]
        r = sw.grade(ps, [])["P3_fix_loss"]
        self.assertEqual((r["worse"], r["better"], r["result"]), (11, 1, "held"))
        self.assertEqual(r["p"], round(13 / 4096, 6))
        ps = [pair(mm(rtk=0.6), mm(rtk=0.95)) for _ in range(9)] + [pair(mm(rtk=1.0), mm(rtk=0.9))]
        r = sw.grade(ps, [])["P4_rtk_fixed"]
        self.assertEqual((r["worse"], r["better"], r["result"]), (9, 1, "held"))
        self.assertEqual(r["p"], round(11 / 1024, 6))

    def test_describe(self):
        d = sw.describe(held_set())
        self.assertEqual(d["eph_ratio_by_event"], {"E": {"pairs": 40, "worse": 35, "median_ratio": 1.5}})
        self.assertEqual(list(d["eph_ratio_by_storm_kp"]), ["8- or more"])
        self.assertEqual(list(d["eph_ratio_by_latitude"]), ["|lat| 50 or more"])
        self.assertEqual(sw.describe([]), {"eph_ratio_by_event": {}, "eph_ratio_by_storm_kp": {},
                                           "eph_ratio_by_latitude": {}})


if __name__ == "__main__":
    unittest.main()
