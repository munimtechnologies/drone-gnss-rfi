"""stormwatch: did the severe geomagnetic storms of 2023-2026 degrade GNSS on real drones?

Part of drone-gnss-rfi, study S1 (pre-registered in storms/PLAN-storms.md). Standard library only. It holds
- a reader for GFZ Niemegk's 3-hourly Kp index file, and the storm events built from it (Kp 8- or more);
- the placebo for each event (the same span moved back 28 or 56 quiet days);
- the upload-date windows and flight-time rules that pair one storm-time flight of a vehicle with one quiet-time
  flight of the same vehicle in the two weeks before;
- per-log GNSS quality measures (receiver-estimated horizontal and vertical accuracy, satellites used, fix losses,
  RTK fixed share) from rows decoded by study 1's reader (gnsswatch.read_topics);
- the pre-registered statistics and grading.

No exact position or time leaves this module in published output: positions become 1-degree cells and flight start
times whole UTC hours (public()).

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import calendar
import datetime
import hashlib
import math
import time

__version__ = "0.1.0"

INTERVAL_S = 10800                     # Kp is given for 3-hour UTC intervals starting 00, 03, ..., 21 UT
DAY_S = 86400
EPS = 1e-6
SEVERE = 7.667                         # Kp 8- or more in an interval: part of a storm event
STORM_TIME = 6.667                     # Kp 7- or more at a flight's start: a storm-time flight
QUIET = 3.333                          # Kp 3+ or less at a flight's start: a quiet-time flight
MERGE_S = DAY_S                        # severe intervals less than 24 h apart belong to one event
EVENTS_FROM, EVENTS_TO = "2023-01-01", "2026-11-14"
STORM_UPLOAD_AFTER_D = 2               # storm side: logs uploaded from date(event start) to date(event end) + 2 days
CONTROL_UPLOAD_D = (-12, -2)           # control side: logs uploaded 12 to 2 days before date(event start)
STORM_FLIGHT_MARGIN_S = DAY_S          # storm-side flight: from 24 h before the event to 24 h after it ends
CONTROL_FLIGHT_S = (-14 * DAY_S, -2 * DAY_S)   # control-side flight: 14 to 2 days before the event starts
PLACEBO_SHIFTS_D = (28, 56)
PLACEBO_CLEAR_S = (-14 * DAY_S, 3 * DAY_S)     # Kp known and below STORM_TIME from 14 d before to 3 d after the span
MAX_TRIES = 3                          # logs read per vehicle and side, in sha256 order of the log id
MIN_SAMPLES = 10
EPH_MAX_M = 100.0
GOOD_FIX = (3, 4, 5, 6)                # 3D, differential, RTK float, RTK fixed
RTK_FIX = (5, 6)
RATIO = 1.10                           # P1: median storm/quiet ratio of eph at least this
SATS_DROP = 0.5                        # P2: median drop in satellites used at least this
SAME_HOUR_GAP = 3.0                    # P1b: pairs whose flights started within 3 h of the same UTC time of day
MIN_PAIRS = 30
MIN_PAIRS_SAME_HOUR = 20
MIN_DISCORDANT = 10

FIELDS = ("timestamp", "fix_type", "eph", "epv", "satellites_used", "time_utc_usec",
          "latitude_deg", "longitude_deg", "lat", "lon")
_I = {f: i for i, f in enumerate(FIELDS)}
GPS_TOPICS = ("sensor_gps", "vehicle_gps_position", "vehicle_gnss")


# ---------------------------------------------------------------- time helpers

def day_start(datestr):
    return calendar.timegm(time.strptime(datestr, "%Y-%m-%d"))


def iso_day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def iso_hour(t):
    return time.strftime("%Y-%m-%dT%H", time.gmtime(t))


def add_days(datestr, n):
    return (datetime.date.fromisoformat(datestr) + datetime.timedelta(days=n)).isoformat()


def window_days(lo, hi):
    """Every date from lo to hi inclusive (YYYY-MM-DD strings)."""
    out, d = [], lo
    while d <= hi:
        out.append(d)
        d = add_days(d, 1)
    return out


UTC_MIN, UTC_MAX = day_start("2020-01-01"), day_start("2030-01-01")


# ---------------------------------------------------------------- Kp and events

def parse_kp(text, since="2022-09-01"):
    """{start of a 3-hour interval (UTC epoch s): Kp} from the text of GFZ's Kp_ap file. Comment lines, missing
    values (Kp -1) and intervals before `since` are skipped."""
    t_since = day_start(since)
    out = {}
    for line in text.splitlines():
        p = line.split()
        if len(p) < 8 or line.lstrip().startswith("#"):
            continue
        try:
            y, mo, d, hh, kp = int(p[0]), int(p[1]), int(p[2]), float(p[3]), float(p[7])
        except ValueError:
            continue
        if kp < 0:
            continue
        t = calendar.timegm((y, mo, d, int(hh), 0, 0, 0, 0, 0))
        if t >= t_since:
            out[t] = kp
    return out


def kp_at(kp, t):
    """Kp of the interval holding instant t (UTC epoch s), or None."""
    return kp.get(int(t) // INTERVAL_S * INTERVAL_S)


def events(kp, severe=SEVERE, merge_s=MERGE_S, first=EVENTS_FROM, last=EVENTS_TO):
    """Storm events: runs of intervals with Kp >= severe, merged when the next one starts less than merge_s after
    the run ends; kept if they start on or after `first` and end by the end of `last`. Strongest first (max Kp,
    then earlier start)."""
    ev = []
    for t in sorted(t for t, v in kp.items() if v >= severe - EPS):
        if ev and t - ev[-1]["end"] < merge_s:
            ev[-1]["end"] = t + INTERVAL_S
            ev[-1]["max_kp"] = max(ev[-1]["max_kp"], kp[t])
            ev[-1]["severe_intervals"] += 1
        else:
            ev.append({"start": t, "end": t + INTERVAL_S, "max_kp": kp[t], "severe_intervals": 1})
    lo, hi = day_start(first), day_start(last) + DAY_S
    out = [e for e in ev if e["start"] >= lo and e["end"] <= hi]
    for e in out:
        e["id"] = iso_hour(e["start"])
        e["kind"] = "real"
    out.sort(key=lambda e: (-e["max_kp"], e["start"]))
    return out


def placebo(ev, kp, shifts=PLACEBO_SHIFTS_D):
    """The quiet stand-in for a real event: the same span moved back 28 days (else 56) such that Kp is known and
    below the storm-time level in every interval from 14 days before the moved span to 3 days after it; else None."""
    span = ev["end"] - ev["start"]
    for d in shifts:
        s = ev["start"] - d * DAY_S
        lo, hi = s + PLACEBO_CLEAR_S[0], s + span + PLACEBO_CLEAR_S[1]
        vals = [kp.get(t) for t in range(lo // INTERVAL_S * INTERVAL_S, hi, INTERVAL_S)]
        if vals and all(v is not None and v < STORM_TIME - EPS for v in vals):
            return {"id": "placebo-" + ev["id"], "kind": "placebo", "of": ev["id"], "shift_d": d,
                    "start": s, "end": s + span, "max_kp": max(vals), "severe_intervals": 0}
    return None


def upload_windows(ev):
    """Upload-date windows (inclusive YYYY-MM-DD pairs) of the storm side and the control side."""
    d0, d1 = iso_day(ev["start"]), iso_day(ev["end"] - 1)
    return {"storm": (d0, add_days(d1, STORM_UPLOAD_AFTER_D)),
            "control": (add_days(d0, CONTROL_UPLOAD_D[0]), add_days(d0, CONTROL_UPLOAD_D[1]))}


def flight_ok(side, ev, t, kp):
    """Whether a flight that started at t (UTC epoch s, from the drone's receiver) can be this side of the event's
    pair. Storm side: from 24 h before the event to 24 h after it, at Kp 7- or more (real events) or at Kp 3+ or
    less (placebos). Control side: 14 to 2 days before the event start, at Kp 3+ or less."""
    if t is None:
        return False
    k = kp_at(kp, t)
    if k is None:
        return False
    if side == "storm":
        if not ev["start"] - STORM_FLIGHT_MARGIN_S <= t < ev["end"] + STORM_FLIGHT_MARGIN_S:
            return False
        return k >= STORM_TIME - EPS if ev["kind"] == "real" else k <= QUIET + EPS
    lo, hi = CONTROL_FLIGHT_S
    if not ev["start"] + lo <= t < ev["start"] + hi:
        return False
    return k <= QUIET + EPS


# ---------------------------------------------------------------- sample

def sha(x):
    return hashlib.sha256(x if isinstance(x, bytes) else str(x).encode()).hexdigest()


def group(entries, lo, hi):
    """{vehicle: [(sha256 of log id, entry), ...] in ascending hash order} for entries uploaded from lo to hi."""
    out = {}
    for e in entries:
        if lo <= e["date"] <= hi:
            out.setdefault(e["v"], []).append((sha(e["log_id"]), e))
    for v in out:
        out[v].sort(key=lambda x: x[0])
    return out


def candidates(entries, ev):
    """(storm side by vehicle, control side by vehicle, vehicles with logs on both sides ordered by the hash of their
    first storm-side log)."""
    w = upload_windows(ev)
    s, c = group(entries, *w["storm"]), group(entries, *w["control"])
    vs = sorted((v for v in s if v in c), key=lambda v: s[v][0][0])
    return s, c, vs


# ---------------------------------------------------------------- per-log measures

def wanted():
    return {t: FIELDS for t in GPS_TOPICS}


def choose(topics):
    """The topic measured: the lowest instance of sensor_gps (the receiver itself), else of vehicle_gps_position,
    else of vehicle_gnss, that has rows. topics as returned by gnsswatch.read_topics."""
    for name in GPS_TOPICS:
        keys = sorted(k for k in topics if k[0] == name and topics[k]["rows"])
        if keys:
            return keys[0]
    return None


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2


def _pos(r):
    la, lo = r[_I["latitude_deg"]], r[_I["longitude_deg"]]
    if la is None or lo is None:
        la, lo = r[_I["lat"]], r[_I["lon"]]
        if la is None or lo is None:
            return None
        la, lo = la * 1e-7, lo * 1e-7
    if not (math.isfinite(la) and math.isfinite(lo)) or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    if la == 0 and lo == 0:
        return None
    return la, lo


def _ok_acc(x):
    return x is not None and math.isfinite(x) and 0 < x < EPH_MAX_M


def measure(rows):
    """GNSS quality of one log from rows (tuples in FIELDS order, log order). Counting starts at the first sample
    with a 3D (or better) fix and a plausible receiver UTC time, which is also the flight's start time."""
    start = cell_ = None
    n_after = lost = rtk_after = rtk_fixed = 0
    rtk_on = False
    eph, epv, sats = [], [], []
    for r in rows:
        fix = r[_I["fix_type"]]
        if fix is None:
            continue
        if start is None:
            if fix not in GOOD_FIX:
                continue
            u = r[_I["time_utc_usec"]]
            if not u or not UTC_MIN <= u / 1e6 < UTC_MAX:
                continue
            start = int(u // 1000000)
            p = _pos(r)
            cell_ = [int(math.floor(p[0])), int(math.floor(p[1]))] if p else None
        n_after += 1
        if fix in GOOD_FIX:
            e, v, s = r[_I["eph"]], r[_I["epv"]], r[_I["satellites_used"]]
            if _ok_acc(e):
                eph.append(e)
            if _ok_acc(v):
                epv.append(v)
            if s is not None and 0 < s < 100:
                sats.append(s)
        else:
            lost += 1
        if fix in RTK_FIX:
            rtk_on = True
        if rtk_on:
            rtk_after += 1
            if fix == 6:
                rtk_fixed += 1
    return {"start_utc": start, "start_hour": iso_hour(start) if start is not None else None, "cell": cell_,
            "n_after": n_after, "loss_share": round(lost / n_after, 4) if n_after else None,
            "n_eph": len(eph), "eph_med": median(eph), "epv_med": median(epv),
            "n_sats": len(sats), "sats_med": median(sats),
            "rtk": rtk_on, "n_rtk": rtk_after, "rtk_fixed_share": round(rtk_fixed / rtk_after, 4) if rtk_after else None}


def usable(m):
    return bool(m) and m.get("start_utc") is not None and (m.get("n_after") or 0) >= MIN_SAMPLES


def public(m):
    """A measure dict without the exact start time (the start hour stays)."""
    return None if m is None else {k: v for k, v in m.items() if k != "start_utc"}


# ---------------------------------------------------------------- pairing

def hour_gap(t1, t2):
    """Circular distance in hours between the UTC times of day of two instants."""
    d = abs((t1 % DAY_S) - (t2 % DAY_S)) / 3600.0
    return min(d, 24.0 - d)


def pick_control(storm_t, cands):
    """(hour gap, (log_id, m)) of the accepted control flight closest in UTC time of day to the storm flight
    (ties: the earlier candidate), or None."""
    best = None
    for c in cands:
        g = hour_gap(storm_t, c[1]["start_utc"])
        if best is None or g < best[0]:
            best = (g, c)
    return best


def make_pair(ev, v, storm, pick, kp):
    """The published record of one pair. storm = (log_id, m); pick = pick_control(...)."""
    gap, (cid, cm) = pick
    sid, sm = storm
    return {"event": ev["id"], "kind": ev["kind"], "v": v,
            "storm": {"log_id": sid, "kp": kp_at(kp, sm["start_utc"]), "m": public(sm)},
            "control": {"log_id": cid, "kp": kp_at(kp, cm["start_utc"]), "m": public(cm)},
            "hour_gap": round(gap, 2), "days_apart": round((sm["start_utc"] - cm["start_utc"]) / DAY_S, 1)}


# ---------------------------------------------------------------- statistics and grading

def sign_p(k, n):
    """One-sided exact sign-test p: P(X >= k) for X ~ Binomial(n, 1/2)."""
    if n <= 0:
        return 1.0
    return sum(math.comb(n, x) for x in range(max(k, 0), n + 1)) / 2 ** n


def value(m, metric):
    if not m:
        return None
    if metric == "eph":
        return m["eph_med"] if m["n_eph"] >= MIN_SAMPLES else None
    if metric == "sats":
        return m["sats_med"] if m["n_sats"] >= MIN_SAMPLES else None
    if metric == "rtk":
        return m["rtk_fixed_share"] if m["n_rtk"] >= MIN_SAMPLES else None
    if metric == "loss":
        return (m["loss_share"] > 0) if m["n_after"] >= MIN_SAMPLES else None
    raise ValueError(metric)


def paired_values(pairs, metric):
    out = []
    for p in pairs:
        a, b = value(p["storm"]["m"], metric), value(p["control"]["m"], metric)
        if a is not None and b is not None:
            out.append((a, b, p))
    return out


def sign_compare(pairs, metric, higher_is_worse):
    """Pairs where the storm side is worse / better / tied, and the one-sided sign-test p for 'worse'."""
    vals = paired_values(pairs, metric)
    worse = better = ties = 0
    for a, b, _p in vals:
        if a == b:
            ties += 1
        elif (a > b) == higher_is_worse:
            worse += 1
        else:
            better += 1
    return {"pairs": len(vals), "worse": worse, "better": better, "ties": ties,
            "p": round(sign_p(worse, worse + better), 6)}, vals


def eph_test(pairs, min_n=MIN_PAIRS):
    r, vals = sign_compare(pairs, "eph", True)
    ratios = [a / b for a, b, _p in vals]
    r["median_ratio"] = round(median(ratios), 4) if ratios else None
    r["min_untied"] = min_n
    if r["worse"] + r["better"] < min_n:
        r["result"] = "too few"
    else:
        r["result"] = "held" if r["p"] < 0.05 and r["median_ratio"] >= RATIO - EPS else "failed"
    return r


def headline(p1, p0):
    if p1 == "too few":
        return "too few pairs to test"
    if p1 == "failed":
        return "no storm effect on estimated horizontal accuracy at the pre-registered thresholds"
    if p0 == "moved":
        return "storm-time accuracy was worse, but the placebo moved too: not attributed to the storms"
    if p0 == "too few":
        return "storm-time accuracy was worse; the placebo had too few pairs to check"
    return "storm-time accuracy was worse and the placebo was clean"


def grade(pairs, placebo_pairs):
    """The pre-registered predictions of PLAN-storms.md."""
    out = {"P1_eph": eph_test(pairs),
           "P1b_eph_same_time_of_day": eph_test([p for p in pairs if p["hour_gap"] <= SAME_HOUR_GAP + EPS],
                                                MIN_PAIRS_SAME_HOUR)}
    r, vals = sign_compare(pairs, "sats", False)
    r["median_drop"] = median([b - a for a, b, _p in vals])
    if r["worse"] + r["better"] < MIN_PAIRS:
        r["result"] = "too few"
    else:
        r["result"] = "held" if r["p"] < 0.05 and r["median_drop"] >= SATS_DROP - EPS else "failed"
    out["P2_sats"] = r
    for key, metric, hiw in (("P3_fix_loss", "loss", True), ("P4_rtk_fixed", "rtk", False)):
        r, _vals = sign_compare(pairs, metric, hiw)
        if r["worse"] + r["better"] < MIN_DISCORDANT:
            r["result"] = "too few"
        else:
            r["result"] = "held" if r["p"] < 0.05 else "failed"
        out[key] = r
    p0 = eph_test(placebo_pairs)
    p0["result"] = {"held": "moved", "failed": "clean", "too few": "too few"}[p0["result"]]
    out["P0_placebo_eph"] = p0
    out["headline"] = headline(out["P1_eph"]["result"], p0["result"])
    return out


def _band(cell_):
    if not cell_:
        return "unknown"
    a = abs(cell_[0] + 0.5)
    return "|lat| < 30" if a < 30 else ("|lat| 30 to 50" if a < 50 else "|lat| 50 or more")


def describe(pairs):
    """eph storm/quiet ratios by event, by the storm flight's Kp class and by latitude band (not graded)."""
    by = {"event": {}, "storm_kp": {}, "latitude": {}}
    for a, b, p in paired_values(pairs, "eph"):
        r = a / b
        k = p["storm"]["kp"]
        by["event"].setdefault(p["event"], []).append(r)
        by["storm_kp"].setdefault("8- or more" if k is not None and k >= SEVERE - EPS else "below 8-", []).append(r)
        by["latitude"].setdefault(_band(p["storm"]["m"].get("cell")), []).append(r)
    out = {}
    for name, d in by.items():
        out["eph_ratio_by_" + name] = {k: {"pairs": len(v), "worse": sum(1 for x in v if x > 1),
                                           "median_ratio": round(median(v), 4)} for k, v in sorted(d.items())}
    return out
