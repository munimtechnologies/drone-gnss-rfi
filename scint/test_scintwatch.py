"""Unit tests for scintwatch (drone-gnss-rfi study S2). Standard library only.

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scintwatch as scw  # noqa: E402


def mk(eph=1.0, loss=0.0, sats=12, rtk=None, n=20, cell=(22, 113), start=43200):
    return {"start_utc": start, "cell": list(cell), "n_after": n, "loss_share": loss,
            "n_eph": n, "eph_med": eph, "n_sats": n, "sats_med": sats,
            "n_rtk": n if rtk is not None else 0, "rtk_fixed_share": rtk}


def rec(band, cls, **kw):
    return {"band": band, "cls": cls, "m": mk(**kw), "lst": 20.0, "date": "2024-10-05"}


class Geometry(unittest.TestCase):
    def test_pole_and_antipode(self):
        self.assertAlmostEqual(scw.mag_lat(80.7, -72.7), 90.0, places=6)
        self.assertAlmostEqual(scw.mag_lat(-80.7, 107.3), -90.0, places=6)

    def test_equator_on_pole_meridian(self):
        self.assertAlmostEqual(scw.mag_lat(-9.3, -72.7), 0.0, places=6)

    def test_shenzhen_and_zurich_cells(self):
        c = scw.classify(mk(cell=(22, 113), start=43200))
        self.assertTrue(13.0 < c["mlat"] < 13.5)
        self.assertEqual(c["band"], "low")
        self.assertAlmostEqual(c["lst"], 12.0 + 113.5 / 15.0, places=9)
        self.assertEqual(c["cls"], "night")
        z = scw.classify(mk(cell=(47, 8), start=43200))
        self.assertTrue(47.5 < z["mlat"] < 48.7)
        self.assertEqual(z["band"], "mid")
        self.assertEqual(z["cls"], "day")

    def test_bands(self):
        self.assertEqual(scw.band(19.99), "low")
        self.assertEqual(scw.band(-19.99), "low")
        self.assertEqual(scw.band(20.0), "other")
        self.assertEqual(scw.band(30.0), "mid")
        self.assertEqual(scw.band(-54.9), "mid")
        self.assertEqual(scw.band(55.0), "other")

    def test_local_solar_hour(self):
        self.assertAlmostEqual(scw.local_solar_hour(0, 0.0), 0.0)
        self.assertAlmostEqual(scw.local_solar_hour(0, 90.0), 6.0)
        self.assertAlmostEqual(scw.local_solar_hour(0, -90.0), 18.0)
        self.assertAlmostEqual(scw.local_solar_hour(3 * 86400 + 22 * 3600, 45.0), 1.0)
        self.assertAlmostEqual(scw.local_solar_hour(0, -180.0), 12.0)

    def test_time_classes(self):
        for lst, want in ((19.5, "night"), (19.49, "dusk"), (0.99, "night"), (1.0, "late"), (8.0, "day"),
                          (16.99, "day"), (17.0, "dusk"), (7.99, "late"), (23.9, "night")):
            self.assertEqual(scw.time_class(lst), want, lst)

    def test_classify_rejects(self):
        self.assertIsNone(scw.classify(None))
        self.assertIsNone(scw.classify(mk(n=9)))
        m = mk()
        m["cell"] = None
        self.assertIsNone(scw.classify(m))
        m = mk()
        m["start_utc"] = None
        self.assertIsNone(scw.classify(m))

    def test_public_class(self):
        p = scw.public_class({"mlat": 13.2549, "band": "low", "lst": 19.9, "cls": "night"})
        self.assertEqual(p, {"mlat": 13.3, "band": "low", "lst_half_hour": 19.5, "cls": "night"})
        self.assertIsNone(scw.public_class(None))

    def test_sector(self):
        self.assertEqual(scw.sector(-47.5), "americas")
        self.assertEqual(scw.sector(8.5), "africa-europe")
        self.assertEqual(scw.sector(113.5), "asia-pacific")


class Sample(unittest.TestCase):
    def test_survey_order(self):
        def e(v, lid, date):
            return {"v": v, "log_id": lid, "date": date}
        entries = [e("A", "a1", "2024-03-01"), e("A", "a2", "2024-10-11"),
                   e("B", "b1", "2024-03-02"), e("B", "b2", "2024-06-02"),
                   e("C", "c1", "2025-09-01"), e("C", "c2", "2025-11-30"), e("C", "c3", "2023-02-14"),
                   e("D", "d1", "2024-12-01"), e("D", "d2", "2024-01-01")]
        out = scw.survey_order(entries)
        self.assertEqual(sorted(v for v, _ in out), ["A", "C"])

        def h(x):
            return hashlib.sha256(("s2|" + x).encode()).hexdigest()
        firsts = {}
        for v, logs in out:
            ids = [x[1]["log_id"] for x in logs]
            self.assertEqual(ids, sorted(ids, key=h))
            self.assertEqual([x[0] for x in logs], [h(i) for i in ids])
            firsts[v] = h(ids[0])
        self.assertEqual([v for v, _ in out], sorted(firsts, key=firsts.get))

    def test_in_season(self):
        self.assertTrue(scw.in_season("2024-02-29"))
        self.assertTrue(scw.in_season("2024-11-01"))
        self.assertFalse(scw.in_season("2024-05-01"))
        self.assertFalse(scw.in_season("2024-12-31"))
        self.assertFalse(scw.in_season("bad"))


class Stats(unittest.TestCase):
    def test_sign_p(self):
        self.assertAlmostEqual(scw.sign_p(3, 3), 0.125)
        self.assertAlmostEqual(scw.sign_p(2, 3), 0.5)
        self.assertAlmostEqual(scw.sign_p(0, 3), 1.0)
        self.assertEqual(scw.sign_p(0, 0), 1.0)

    def test_fisher(self):
        self.assertAlmostEqual(scw.fisher_greater(3, 0, 0, 3), 0.05)
        self.assertAlmostEqual(scw.fisher_greater(0, 3, 3, 0), 1.0)

    def test_median(self):
        self.assertEqual(scw.median([3, 1, 2]), 2)
        self.assertEqual(scw.median([4, 1, 2, 3]), 2.5)
        self.assertIsNone(scw.median([]))

    def test_value(self):
        self.assertEqual(scw.value(mk(eph=1.5), "eph"), 1.5)
        self.assertIsNone(scw.value(mk(n=9), "eph"))
        self.assertTrue(scw.value(mk(loss=0.1), "loss"))
        self.assertFalse(scw.value(mk(loss=0.0), "loss"))
        self.assertIsNone(scw.value(mk(), "rtk"))
        self.assertEqual(scw.value(mk(rtk=0.5), "rtk"), 0.5)


def pairs_of(band, specs):
    """specs: list of (night eph, day eph, night sats, day sats, night rtk, day rtk)."""
    out = []
    for ne, de, ns, ds, nr, dr in specs:
        out.append({"band": band, "night": mk(eph=ne, sats=ns, rtk=nr), "day": mk(eph=de, sats=ds, rtk=dr)})
    return out


class Grade(unittest.TestCase):
    def test_eph_held_and_too_few(self):
        low = pairs_of("low", [(2.0, 1.0, 10, 12, None, None)] * 16)
        r = scw.eph_test(low)
        self.assertEqual((r["worse"], r["better"], r["ties"]), (16, 0, 0))
        self.assertEqual(r["median_ratio"], 2.0)
        self.assertEqual(r["result"], "held")
        self.assertEqual(scw.eph_test(low[:14])["result"], "too few")

    def test_eph_failed_on_small_ratio(self):
        low = pairs_of("low", [(1.05, 1.0, 12, 12, None, None)] * 16)
        r = scw.eph_test(low)
        self.assertEqual(r["worse"], 16)
        self.assertEqual(r["median_ratio"], 1.05)
        self.assertEqual(r["result"], "failed")

    def test_sats_and_rtk(self):
        low = pairs_of("low", [(2.0, 1.0, 10, 12, 0.2, 0.9)] * 16)
        s = scw.sats_test(low)
        self.assertEqual((s["worse"], s["median_drop"], s["result"]), (16, 2, "held"))
        r = scw.rtk_test(low)
        self.assertEqual((r["worse"], r["result"]), (16, "held"))
        self.assertEqual(scw.rtk_test(low[:7])["result"], "too few")

    def test_grade_headlines(self):
        low = pairs_of("low", [(2.0, 1.0, 10, 12, None, None)] * 16)
        mid_mixed = (pairs_of("mid", [(1.2, 1.0, 12, 12, None, None)] * 8)
                     + pairs_of("mid", [(1.0, 1.2, 12, 12, None, None)] * 8))
        g = scw.grade(low + mid_mixed, [])
        self.assertEqual(g["N0_eph_mid_pairs"]["worse"], 8)
        self.assertAlmostEqual(g["N0_eph_mid_pairs"]["p"], round(39203 / 65536, 6))
        self.assertEqual(g["N0_eph_mid_pairs"]["result"], "clean")
        self.assertEqual(g["headline"],
                         "night accuracy was worse at low latitudes and the middle-latitude control was clean")
        g = scw.grade(low + pairs_of("mid", [(2.0, 1.0, 12, 12, None, None)] * 16), [])
        self.assertEqual(g["N0_eph_mid_pairs"]["result"], "moved")
        self.assertIn("not attributed", g["headline"])
        g = scw.grade(low, [])
        self.assertEqual(g["N0_eph_mid_pairs"]["result"], "too few")
        self.assertIn("too few pairs to check", g["headline"])
        g = scw.grade([], [])
        self.assertEqual(g["headline"], "too few low-latitude night/day pairs to test")
        self.assertEqual(g["N2_fix_loss_did"]["result"], "too few")

    def survey(self, mid_night=20):
        s = []
        s += [rec("low", "night", loss=0.1, eph=3.0)] * 10 + [rec("low", "night")] * 10
        s += [rec("low", "day")] * 100
        s += [rec("mid", "night")] * mid_night + [rec("mid", "day")] * 100
        s += [rec("low", "dusk")] * 5 + [rec("other", "night")] * 5
        return s

    def test_cross_did(self):
        g = scw.grade([], self.survey())
        n2 = g["N2_fix_loss_did"]
        self.assertEqual(n2["low_night"], [10, 20])
        self.assertEqual(n2["low_day"], [0, 100])
        self.assertEqual(n2["mid_night"], [0, 20])
        self.assertEqual((n2["low_diff"], n2["mid_diff"], n2["excess"]), (0.5, 0.0, 0.5))
        self.assertLess(n2["p_low"], 0.05)
        self.assertEqual(n2["result"], "held")
        n3 = g["N3_degraded_eph_did"]
        self.assertEqual(n3["day_median_eph"], {"low": 1.0, "mid": 1.0})
        self.assertEqual(n3["low_night"], [10, 20])
        self.assertEqual(n3["result"], "held")
        self.assertEqual(scw.grade([], self.survey(19))["N2_fix_loss_did"]["result"], "too few")

    def test_cross_failed_when_mid_moves_too(self):
        s = self.survey()
        s = [r for r in s if not (r["band"] == "mid" and r["cls"] == "night")]
        s += [rec("mid", "night", loss=0.2)] * 10 + [rec("mid", "night")] * 10
        n2 = scw.grade([], s)["N2_fix_loss_did"]
        self.assertEqual((n2["low_diff"], n2["mid_diff"], n2["excess"]), (0.5, 0.5, 0.0))
        self.assertEqual(n2["result"], "failed")

    def test_describe(self):
        s = self.survey()
        d = scw.describe(s, pairs_of("low", [(2.0, 1.0, 10, 12, None, None)] * 3))
        self.assertEqual(d["survey_by_band_class"]["low/night"], 20)
        self.assertEqual(d["survey_by_band_class"]["other/night"], 5)
        self.assertEqual(d["pairs_by_band"], {"low": 3})
        self.assertEqual(d["rates_by_band_class"]["low/night"]["fix_loss"], [10, 20])
        self.assertEqual(d["rates_by_band_class"]["low/dusk"]["logs"], 5)
        self.assertEqual(d["low_night_by_local_time"]["19:30-21:00"]["logs"], 20)
        self.assertEqual(d["low_night_by_sector"]["asia-pacific"]["degraded_eph"], [10, 20])
        self.assertEqual(d["low_night_by_upload_month"]["10"]["logs"], 20)
        self.assertEqual(d["low_night_by_upload_year"]["2024"]["logs"], 20)


if __name__ == "__main__":
    unittest.main()
