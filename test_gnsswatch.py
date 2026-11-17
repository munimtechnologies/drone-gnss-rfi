"""Tests for gnsswatch (stdlib unittest). Every expected value was worked out by hand before any run.

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

import hashlib
import math
import struct
import unittest

import gnsswatch as gw

HEADER = gw.MAGIC + b"\x01" + struct.pack("<Q", 0)


def msg(t, payload):
    return struct.pack("<HB", len(payload), ord(t)) + payload


def fmt(s):
    return msg("F", s.encode())


def add(multi, msg_id, name):
    return msg("A", struct.pack("<BH", multi, msg_id) + name.encode())


def data(msg_id, payload):
    return msg("D", struct.pack("<H", msg_id) + payload)


GPS_FMT = "sensor_gps:uint64_t timestamp;uint8_t spoofing_state;double latitude_deg;uint8_t[7] _padding0;"


def small_log():
    return (HEADER + fmt(GPS_FMT) + fmt("other:uint64_t timestamp;float x;")
            + add(0, 5, "sensor_gps") + add(0, 6, "other")
            + data(6, struct.pack("<Qf", 1, 2.0))
            + data(5, struct.pack("<QBd", 100, 2, 47.25))                 # trailing padding not logged
            + data(5, struct.pack("<QBd", 200, 1, 47.5) + b"\0" * 7))     # full 24 bytes


WANT = {"sensor_gps": ["timestamp", "spoofing_state", "latitude_deg", "jamming_state"]}


def row(**kw):
    return tuple(kw.get(f) for f in gw.GPS_FIELDS)


def scenario():
    u0 = 1700000000000000
    return [
        row(timestamp=0, fix_type=1, spoofing_state=1, jamming_state=1, jamming_indicator=10, time_utc_usec=0),
        row(timestamp=1000000, fix_type=3, latitude_deg=54.6, longitude_deg=25.2, eph=3.0, satellites_used=10,
            spoofing_state=1, jamming_state=2, jamming_indicator=20, time_utc_usec=u0),
        row(timestamp=2000000, fix_type=3, latitude_deg=54.6, longitude_deg=25.2, eph=3.0, satellites_used=10,
            spoofing_state=2, jamming_state=2, jamming_indicator=30, time_utc_usec=u0 + 1000000),
        row(timestamp=3000000, fix_type=3, latitude_deg=55.75, longitude_deg=37.6, eph=3.0, satellites_used=10,
            spoofing_state=3, jamming_state=3, jamming_indicator=40, time_utc_usec=u0 + 102000000),
        row(timestamp=4000000, fix_type=3, latitude_deg=55.75, longitude_deg=37.6, eph=3.0, satellites_used=10,
            spoofing_state=2, jamming_state=1, jamming_indicator=50, time_utc_usec=u0 + 103000000),
    ]


class TestFormat(unittest.TestCase):
    def test_parse_format(self):
        self.assertEqual(gw.parse_format("t1:uint64_t timestamp;float[2] v;uint8_t[3] _padding0;"),
                         ("t1", [("uint64_t", None, "timestamp"), ("float", 2, "v"), ("uint8_t", 3, "_padding0")]))

    def test_parse_format_rejects(self):
        with self.assertRaises(ValueError):
            gw.parse_format("no colon here")

    def test_layout_flat(self):
        name, fields = gw.parse_format("t1:uint64_t timestamp;float[2] v;uint8_t[3] _padding0;")
        codes, names = gw.layout(name, {name: fields})
        self.assertEqual(codes, "Qff3x")
        self.assertEqual(names, ["timestamp", "v[0]", "v[1]"])
        self.assertEqual(struct.calcsize("<" + codes), 19)

    def test_layout_nested(self):
        formats = dict([gw.parse_format("inner:uint8_t spoofing_state;int32_t jamming_indicator;uint8_t[3] _padding0;"),
                        gw.parse_format("vehicle_gnss:uint64_t timestamp;inner receiver;inner[2] extra;")])
        codes, names = gw.layout("vehicle_gnss", formats)
        self.assertEqual(codes, "QBi3xBi3xBi3x")
        self.assertEqual(names, ["timestamp", "receiver.spoofing_state", "receiver.jamming_indicator",
                                 "extra[0].spoofing_state", "extra[0].jamming_indicator",
                                 "extra[1].spoofing_state", "extra[1].jamming_indicator"])
        self.assertEqual(struct.calcsize("<" + codes), 32)
        self.assertEqual(gw.resolve(names, "spoofing_state"), "receiver.spoofing_state")
        self.assertEqual(gw.resolve(names, "timestamp"), "timestamp")
        self.assertIsNone(gw.resolve(names, "lat"))


class TestRead(unittest.TestCase):
    def test_read_small_log(self):
        topics, info = gw.read_topics(small_log(), WANT)
        self.assertEqual(list(topics), [("sensor_gps", 0)])
        t = topics[("sensor_gps", 0)]
        self.assertEqual(t["fields"], ["timestamp", "spoofing_state", "latitude_deg", None])
        self.assertEqual(t["rows"], [(100, 2, 47.25, None), (200, 1, 47.5, None)])
        self.assertFalse(info["truncated"])
        self.assertEqual(info["resyncs"], 0)

    def test_truncated(self):
        buf = small_log() + data(5, struct.pack("<QBd", 300, 3, 48.0))[:-4]
        topics, info = gw.read_topics(buf, WANT)
        self.assertEqual(len(topics[("sensor_gps", 0)]["rows"]), 2)
        self.assertTrue(info["truncated"])

    def test_remove_and_resync(self):
        buf = (small_log() + msg("R", struct.pack("<H", 5)) + data(5, struct.pack("<QBd", 300, 3, 48.0))
               + add(0, 5, "sensor_gps") + b"\x02\x00\xffzz" + msg("S", gw.SYNC)
               + data(5, struct.pack("<QBd", 400, 2, 49.0)))
        topics, info = gw.read_topics(buf, WANT)
        self.assertEqual(topics[("sensor_gps", 0)]["rows"],
                         [(100, 2, 47.25, None), (200, 1, 47.5, None), (400, 2, 49.0, None)])
        self.assertEqual(info["resyncs"], 1)
        self.assertFalse(info["truncated"])

    def test_not_ulog(self):
        with self.assertRaises(gw.NotULog):
            gw.read_topics(b"hello world 12345678", WANT)

    def test_measure_log_end_to_end(self):
        fmt_full = ("sensor_gps:uint64_t timestamp;uint8_t fix_type;double latitude_deg;double longitude_deg;"
                    "uint8_t spoofing_state;uint8_t jamming_state;int32_t jamming_indicator;")
        st = "vehicle_status:uint64_t timestamp;uint8_t arming_state;"
        buf = (HEADER + fmt(fmt_full) + fmt(st) + add(0, 1, "sensor_gps") + add(0, 2, "vehicle_status")
               + data(2, struct.pack("<QB", 0, 1))
               + data(1, struct.pack("<QBddBBi", 1000000, 3, 31.5, 35.0, 2, 2, 90))
               + data(1, struct.pack("<QBddBBi", 2000000, 3, 31.5, 35.0, 2, 2, 95)))
        key, m, info = gw.measure_log(buf)
        self.assertEqual(key, ("sensor_gps", 0))
        self.assertEqual(m["spoof"], [0, 0, 2, 0])
        self.assertEqual(m["first_fix"], {"cell": [31, 35], "region": "levant"})
        self.assertEqual(m["armed_split"]["n_disarmed"], 2)
        self.assertEqual(m["armed_split"]["jam_ind_median_disarmed"], 92.5)


class TestMeasure(unittest.TestCase):
    def test_scenario(self):
        m = gw.measure(scenario(), [(0, 1), (2500000, 2)])
        self.assertEqual(m["n_gps"], 5)
        self.assertEqual(m["n_fix"], 4)
        self.assertEqual(m["spoof"], [0, 2, 2, 1])
        self.assertEqual(m["jam"], [0, 2, 2, 1])
        self.assertEqual(m["jam_ind_median"], 30)
        self.assertEqual(m["jam_ind_max"], 50)
        self.assertIsNone(m["noise_median"])
        self.assertEqual(m["first_fix"], {"cell": [54, 25], "region": "baltic"})
        self.assertEqual(m["teleports"], 1)
        self.assertGreater(m["max_jump_km"], 500)
        self.assertEqual(m["teleport_dest_cell"], [55, 37])
        self.assertEqual(m["utc_jumps"], 1)
        self.assertEqual(m["max_utc_jump_s"], 100.0)
        self.assertEqual(m["utc_date"], "2023-11-14")
        a = m["armed_split"]
        self.assertEqual((a["n_disarmed"], a["n_armed"]), (3, 2))
        self.assertEqual(a["jam_ind_median_disarmed"], 20)
        self.assertEqual(a["jam_ind_median_armed"], 45.0)

    def test_flags(self):
        m = gw.measure(scenario())
        f = gw.flags(m, min_reports=3)
        self.assertEqual(f, {"spoof_reporting": True, "spoof_any": True, "spoof_sustained": True, "spoof_multi": True,
                             "jam_reporting": True, "jam_warn": True, "jam_crit": True, "manipulated": True})
        self.assertFalse(gw.flags(m)["spoof_reporting"])      # 5 reports < 10

    def test_old_int_position_and_eph_gate(self):
        rows = [row(timestamp=1, fix_type=3, lat=546000000, lon=252000000, eph=50.0, satellites_used=10),
                row(timestamp=2, fix_type=3, lat=-336000000, lon=1512000000, eph=2.0, satellites_used=5),
                row(timestamp=3, fix_type=3, lat=-336000000, lon=1512000000, eph=2.0, satellites_used=12)]
        m = gw.measure(rows)
        self.assertEqual(m["first_fix"], {"cell": [-34, 151], "region": "rest"})
        self.assertEqual(m["teleports"], 1)       # Lithuania to Sydney in 1 us is a teleport
        self.assertEqual(m["teleport_dest_cell"], [-34, 151])
        self.assertEqual(m["n_fix"], 3)

    def test_zero_position_ignored(self):
        rows = [row(timestamp=1, fix_type=3, latitude_deg=0.0, longitude_deg=0.0)]
        self.assertIsNone(gw.measure(rows)["first_fix"])
        self.assertEqual(gw.measure(rows)["n_fix"], 0)

    def test_choose_topic(self):
        si = gw.GPS_FIELDS.index("spoofing_state")
        r0 = tuple(0 if i == si else None for i in range(len(gw.GPS_FIELDS)))
        r1 = tuple(1 if i == si else None for i in range(len(gw.GPS_FIELDS)))
        topics = {("sensor_gps", 1): {"rows": [r0]}, ("sensor_gps", 0): {"rows": [r0]},
                  ("vehicle_gps_position", 0): {"rows": [r1]}}
        self.assertEqual(gw.choose_topic(topics), ("vehicle_gps_position", 0))
        topics[("vehicle_gps_position", 0)] = {"rows": [r0]}
        self.assertEqual(gw.choose_topic(topics), ("sensor_gps", 0))
        self.assertIsNone(gw.choose_topic({("vehicle_status", 0): {"rows": [(1, 1)]}}))


class TestGeoStats(unittest.TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(gw.haversine_m(0.0, 0.0, 0.0, 1.0), 6371008.8 * math.pi / 180, places=6)
        self.assertEqual(gw.haversine_m(10.0, 20.0, 10.0, 20.0), 0.0)

    def test_regions(self):
        self.assertEqual(gw.region(54.6, 25.2), "baltic")
        self.assertEqual(gw.region(32.08, 34.78), "levant")
        self.assertEqual(gw.region(46.5, 30.7), "black_sea")
        self.assertEqual(gw.region(25.2, 55.3), "gulf")
        self.assertEqual(gw.region(47.4, 8.5), "rest")
        self.assertEqual(gw.region(61.0, 25.0), "rest")       # upper edge is open
        self.assertEqual(gw.region(53.0, 13.0), "baltic")     # lower edge is closed

    def test_cell_and_median(self):
        self.assertEqual(gw.cell(-33.6, 151.2), [-34, 151])
        self.assertEqual(gw.median([3, 1, 2]), 2)
        self.assertEqual(gw.median([4, 1, 2, 3]), 2.5)
        self.assertIsNone(gw.median([]))

    def test_wilson(self):
        lo, hi = gw.wilson(0, 10)
        z2 = gw.Z95 * gw.Z95
        self.assertAlmostEqual(lo, 0.0, places=12)
        self.assertAlmostEqual(hi, (z2 / 10) / (1 + z2 / 10), places=12)
        self.assertIsNone(gw.wilson(0, 0))

    def test_fisher(self):
        self.assertAlmostEqual(gw.fisher_greater(3, 1, 1, 5), 25 / 210, places=12)
        self.assertAlmostEqual(gw.fisher_greater(0, 4, 4, 2), 1.0, places=12)


def entry(**kw):
    e = {"log_id": "abc-1", "vehicle_uuid": "0123456789abcdef", "duration_s": 300, "log_date": "2024-06-01",
         "ver_sw_release": "v1.14.3 255", "sys_hw": "PX4_FMU_V6C", "mav_type": "Quadrotor", "type": "personal",
         "source": "webui"}
    e.update(kw)
    return e


class TestSample(unittest.TestCase):
    def test_eligible(self):
        e = gw.eligible(entry())
        self.assertEqual(e["log_id"], "abc-1")
        self.assertEqual(e["date"], "2024-06-01")
        self.assertEqual(e["v"], hashlib.sha256(b"0123456789abcdef").hexdigest()[:12])
        self.assertIsNone(gw.eligible(entry(sys_hw="PX4_SITL")))
        self.assertIsNone(gw.eligible(entry(type="simulation")))
        self.assertIsNone(gw.eligible(entry(source="CI")))
        self.assertIsNone(gw.eligible(entry(duration_s=59)))
        self.assertIsNone(gw.eligible(entry(log_date="2022-12-31")))
        self.assertIsNone(gw.eligible(entry(log_date="2026-11-17")))
        self.assertIsNone(gw.eligible(entry(ver_sw_release="v1.12.3 255")))
        self.assertIsNone(gw.eligible(entry(vehicle_uuid="000000000000")))
        self.assertIsNotNone(gw.eligible(entry(ver_sw_release="v1.13.0 0", log_date="2026-11-16")))

    def test_pick_sample(self):
        es = [gw.eligible(entry(log_id=i, vehicle_uuid=v)) for i, v in
              (("a1", "vehicle-one-1"), ("a2", "vehicle-one-1"), ("b1", "vehicle-two-2"))]
        got = gw.pick_sample(es)
        h = lambda s: hashlib.sha256(s.encode()).hexdigest()
        keep = min(("a1", "a2"), key=h)
        self.assertEqual(sorted(e["log_id"] for e in got), sorted([keep, "b1"]))
        self.assertEqual([e["log_id"] for e in got], sorted([keep, "b1"], key=h))


def graded_row(region, any_=False, manipulated=False, jam_warn=False):
    return {"m": {"first_fix": {"cell": [0, 0], "region": region}, "jam_ind_max": 0, "armed_split": None},
            "flags": {"spoof_reporting": True, "spoof_any": any_, "manipulated": manipulated,
                      "jam_reporting": True, "jam_warn": jam_warn}}


class TestGrade(unittest.TestCase):
    def test_held(self):
        rows = ([graded_row("levant", any_=(i < 10)) for i in range(20)]
                + [graded_row("rest", any_=(i < 3)) for i in range(60)])
        g = gw.grade(rows)
        h1 = g["H1_spoof_hot_vs_rest"]
        self.assertEqual((h1["hot"], h1["rest"]), ([10, 20], [3, 60]))
        self.assertEqual(h1["diff"], 0.45)
        self.assertEqual(h1["result"], "held")
        self.assertLess(h1["p"], 0.001)
        self.assertEqual(g["H2_background_rate"]["result"], "held")
        self.assertEqual(g["H3_background_not_manipulated"]["result"], "too few")
        self.assertEqual(g["H4_jam_hot_vs_rest"]["result"], "failed")      # 0 of 20 vs 0 of 60
        self.assertEqual(g["H5_self_interference"]["result"], "too few")

    def test_too_few_and_failed(self):
        rows = [graded_row("baltic", any_=True) for _ in range(5)] + [graded_row("rest") for _ in range(60)]
        self.assertEqual(gw.grade(rows)["H1_spoof_hot_vs_rest"]["result"], "too few")
        rows = ([graded_row("gulf", any_=(i < 2)) for i in range(20)]
                + [graded_row("rest", any_=(i < 12)) for i in range(60)])
        g = gw.grade(rows)
        self.assertEqual(g["H1_spoof_hot_vs_rest"]["result"], "failed")   # 10% vs 20%
        self.assertEqual(g["H2_background_rate"]["result"], "failed")     # 20% > 12%
        self.assertEqual(g["H3_background_not_manipulated"]["result"], "held")   # 0 of 12


if __name__ == "__main__":
    unittest.main()
