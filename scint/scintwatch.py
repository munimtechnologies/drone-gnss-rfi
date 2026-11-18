"""scintwatch: do equatorial plasma bubbles after sunset degrade GNSS on real drones at low magnetic latitudes?

Part of drone-gnss-rfi, study S2 (pre-registered in scint/PLAN-scint.md). Standard library only. It holds
- a centred-dipole geomagnetic latitude and the latitude bands of the study (low: |mlat| < 20; middle: 30 to 55);
- local solar time at a flight's start and the time classes (night 19:30-01:00, day 08:00-17:00, dusk, late);
- the sample rule (equinox-season upload months, vehicles with at least two such logs, salted sha256 order);
- the pre-registered statistics and grading: paired night-vs-day tests within vehicles (low band, with the middle
  band as the negative control) and cross-sectional difference-in-differences on the survey logs.

Per-log measures come from study S1's published stormwatch.measure (eph, satellites, fix losses, RTK fixed share).
No exact position or time leaves this module in published output: positions are 1-degree cells, local solar time is
kept in half hours (public_class()).

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import hashlib
import math

__version__ = "0.1.0"

# ---------------------------------------------------------------- geometry and time

POLE_LAT, POLE_LON = 80.7, -72.7        # centred-dipole north geomagnetic pole, close to IGRF for 2020-2025
LOW_MAX = 20.0                          # |mlat| below this: low band (equatorial anomaly and plasma-bubble zone)
MID_MIN, MID_MAX = 30.0, 55.0           # |mlat| from 30 to below 55: middle band (negative control)
DAY_S = 86400
EPS = 1e-9


def mag_lat(lat, lon):
    """Centred-dipole geomagnetic latitude (degrees) of a point (degrees)."""
    p, la = math.radians(POLE_LAT), math.radians(lat)
    s = math.sin(la) * math.sin(p) + math.cos(la) * math.cos(p) * math.cos(math.radians(lon - POLE_LON))
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def band(mlat):
    a = abs(mlat)
    if a < LOW_MAX:
        return "low"
    if MID_MIN <= a < MID_MAX:
        return "mid"
    return "other"


def local_solar_hour(start_utc, lon):
    """Mean local solar time (hours, 0 to < 24) at UTC epoch second start_utc and longitude lon (degrees)."""
    return ((start_utc % DAY_S) / 3600.0 + lon / 15.0) % 24.0


def time_class(lst):
    """night: 19:30 to 01:00; day: 08:00 to 17:00; dusk: 17:00 to 19:30; late: 01:00 to 08:00."""
    if lst >= 19.5 or lst < 1.0:
        return "night"
    if 8.0 <= lst < 17.0:
        return "day"
    if 17.0 <= lst < 19.5:
        return "dusk"
    return "late"


def classify(m):
    """{"mlat", "band", "lst", "cls"} of a measured log (stormwatch.measure output), or None if it has no start
    time, no start cell or fewer than 10 GNSS samples from the start on. The 1-degree cell's centre stands for the
    position."""
    if not m or m.get("start_utc") is None or not m.get("cell") or (m.get("n_after") or 0) < MIN_SAMPLES:
        return None
    lat, lon = m["cell"][0] + 0.5, m["cell"][1] + 0.5
    ml = mag_lat(lat, lon)
    lst = local_solar_hour(m["start_utc"], lon)
    return {"mlat": ml, "band": band(ml), "lst": lst, "cls": time_class(lst)}


def public_class(c):
    """A class dict fit for publication: mlat to 0.1 degree, local solar time floored to the half hour."""
    if c is None:
        return None
    return {"mlat": round(c["mlat"], 1), "band": c["band"], "lst_half_hour": math.floor(c["lst"] * 2) / 2.0,
            "cls": c["cls"]}


def sector(lon):
    """Longitude sector of a cell's centre, for description only."""
    if lon < -30.0:
        return "americas"
    if lon < 60.0:
        return "africa-europe"
    return "asia-pacific"


# ---------------------------------------------------------------- sample

MONTHS = (2, 3, 4, 9, 10, 11)            # upload months around the equinoxes, the plasma-bubble seasons
MIN_LOGS = 2                             # vehicles need at least this many in-season logs (one to pair with)
MAX_PAIR_READS = 3                       # other logs read per night survey log, looking for a day flight
MID_SLACK = 5                            # middle-band pairings tried at most low-band ones + this
SALT = "s2|"


def sha(x):
    return hashlib.sha256(x if isinstance(x, bytes) else str(x).encode()).hexdigest()


def salted(log_id):
    return sha(SALT + str(log_id))


def in_season(date):
    try:
        return int(str(date)[5:7]) in MONTHS
    except ValueError:
        return False


def survey_order(entries):
    """[(vehicle, [(salted hash, entry), ...] ascending)] for vehicles with at least MIN_LOGS in-season logs, in the
    ascending order of the salted hash of each vehicle's first log (its survey log)."""
    by = {}
    for e in entries:
        if in_season(e["date"]):
            by.setdefault(e["v"], []).append((salted(e["log_id"]), e))
    out = []
    for v, logs in by.items():
        if len(logs) >= MIN_LOGS:
            logs.sort(key=lambda x: x[0])
            out.append((v, logs))
    out.sort(key=lambda x: x[1][0][0])
    return out


# ---------------------------------------------------------------- statistics

MIN_SAMPLES = 10
RATIO = 1.10
SATS_DROP = 0.5
MIN_PAIRS = 15
MIN_RTK_PAIRS = 8
MIN_NIGHT, MIN_DAY = 20, 100
MARGIN = 0.05
DEGRADED_FACTOR = 2.0


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2


def sign_p(k, n):
    """One-sided exact sign-test p: P(X >= k) for X ~ Binomial(n, 1/2)."""
    if n <= 0:
        return 1.0
    return sum(math.comb(n, x) for x in range(max(k, 0), n + 1)) / 2 ** n


def fisher_greater(a, b, c, d):
    """One-sided Fisher exact p for the table [[a, b], [c, d]]: P(top-left >= a) with the margins fixed."""
    n1, k, total = a + b, a + c, a + b + c + d
    top = sum(math.comb(k, x) * math.comb(total - k, n1 - x) for x in range(a, min(n1, k) + 1))
    return top / math.comb(total, n1)


def value(m, metric):
    if not m:
        return None
    if metric == "eph":
        return m.get("eph_med") if (m.get("n_eph") or 0) >= MIN_SAMPLES else None
    if metric == "sats":
        return m.get("sats_med") if (m.get("n_sats") or 0) >= MIN_SAMPLES else None
    if metric == "rtk":
        return m.get("rtk_fixed_share") if (m.get("n_rtk") or 0) >= MIN_SAMPLES else None
    if metric == "loss":
        return (m["loss_share"] > 0) if (m.get("n_after") or 0) >= MIN_SAMPLES and m.get("loss_share") is not None \
            else None
    raise ValueError(metric)


def paired(pairs, metric, higher_is_worse):
    """Night-worse / night-better / tied counts over pairs ({"night": m, "day": m}) and the one-sided sign-test p
    for 'worse at night'; also the (night, day) values used."""
    vals = []
    for p in pairs:
        a, b = value(p["night"], metric), value(p["day"], metric)
        if a is not None and b is not None:
            vals.append((a, b))
    worse = better = ties = 0
    for a, b in vals:
        if a == b:
            ties += 1
        elif (a > b) == higher_is_worse:
            worse += 1
        else:
            better += 1
    return {"pairs": len(vals), "worse": worse, "better": better, "ties": ties,
            "p": round(sign_p(worse, worse + better), 6)}, vals


def eph_test(pairs, min_n=MIN_PAIRS):
    r, vals = paired(pairs, "eph", True)
    ratios = [a / b for a, b in vals if b > 0]
    r["median_ratio"] = round(median(ratios), 4) if ratios else None
    if r["worse"] + r["better"] < min_n:
        r["result"] = "too few"
    else:
        r["result"] = "held" if r["p"] < 0.05 and r["median_ratio"] >= RATIO - EPS else "failed"
    return r


def sats_test(pairs, min_n=MIN_PAIRS):
    r, vals = paired(pairs, "sats", False)
    r["median_drop"] = median([b - a for a, b in vals])
    if r["worse"] + r["better"] < min_n:
        r["result"] = "too few"
    else:
        r["result"] = "held" if r["p"] < 0.05 and r["median_drop"] >= SATS_DROP - EPS else "failed"
    return r


def rtk_test(pairs, min_n=MIN_RTK_PAIRS):
    r, _vals = paired(pairs, "rtk", False)
    if r["worse"] + r["better"] < min_n:
        r["result"] = "too few"
    else:
        r["result"] = "held" if r["p"] < 0.05 else "failed"
    return r


def day_medians(survey):
    """{band: median eph_med of the band's day survey logs with at least 10 eph samples}."""
    out = {}
    for b in ("low", "mid"):
        xs = [value(r["m"], "eph") for r in survey if r["band"] == b and r["cls"] == "day"]
        out[b] = median([x for x in xs if x is not None])
    return out


def degraded(r, medians):
    x, ref = value(r["m"], "eph"), medians.get(r["band"])
    if x is None or ref is None:
        return None
    return x >= DEGRADED_FACTOR * ref - EPS


def cross(survey, flag):
    """Difference in differences on survey logs: (night share - day share) in the low band against the same in the
    middle band, for a per-record flag (True/False/None; None is left out)."""
    cnt = {}
    for r in survey:
        if r["band"] not in ("low", "mid") or r["cls"] not in ("night", "day"):
            continue
        f = flag(r)
        if f is None:
            continue
        k = cnt.setdefault((r["band"], r["cls"]), [0, 0])
        k[0] += 1 if f else 0
        k[1] += 1
    g = {"%s_%s" % key: cnt.get(key, [0, 0]) for key in
         (("low", "night"), ("low", "day"), ("mid", "night"), ("mid", "day"))}
    out = dict(g)
    if (g["low_night"][1] < MIN_NIGHT or g["low_day"][1] < MIN_DAY or g["mid_night"][1] < MIN_NIGHT
            or g["mid_day"][1] < MIN_DAY):
        out["result"] = "too few"
        return out
    share = {k: v[0] / v[1] for k, v in g.items()}
    low_diff = share["low_night"] - share["low_day"]
    mid_diff = share["mid_night"] - share["mid_day"]
    kn, nn = g["low_night"]
    kd, nd = g["low_day"]
    p = fisher_greater(kn, nn - kn, kd, nd - kd)
    out.update(low_diff=round(low_diff, 4), mid_diff=round(mid_diff, 4), excess=round(low_diff - mid_diff, 4),
               p_low=round(p, 6),
               result="held" if p < 0.05 and low_diff >= MARGIN - EPS and low_diff - mid_diff >= MARGIN - EPS
               else "failed")
    return out


def headline(n1, n0):
    if n1 == "too few":
        return "too few low-latitude night/day pairs to test"
    if n1 == "failed":
        return "no post-sunset effect on estimated horizontal accuracy at low latitudes at the pre-registered thresholds"
    if n0 == "moved":
        return "night accuracy was worse at low latitudes, but also at middle latitudes: not attributed to plasma bubbles"
    if n0 == "too few":
        return "night accuracy was worse at low latitudes; the middle-latitude control had too few pairs to check"
    return "night accuracy was worse at low latitudes and the middle-latitude control was clean"


def grade(pairs, survey):
    """The pre-registered predictions of PLAN-scint.md. pairs: [{"band", "night": m, "day": m}];
    survey: [{"band", "cls", "m"}] (one per vehicle, usable and classified)."""
    low = [p for p in pairs if p["band"] == "low"]
    mid = [p for p in pairs if p["band"] == "mid"]
    out = {"N1_eph_low_pairs": eph_test(low)}
    n0 = eph_test(mid)
    n0["result"] = {"held": "moved", "failed": "clean", "too few": "too few"}[n0["result"]]
    out["N0_eph_mid_pairs"] = n0
    out["N2_fix_loss_did"] = cross(survey, lambda r: value(r["m"], "loss"))
    med = day_medians(survey)
    n3 = cross(survey, lambda r: degraded(r, med))
    n3["day_median_eph"] = med
    out["N3_degraded_eph_did"] = n3
    out["N4_sats_low_pairs"] = sats_test(low)
    out["N5_rtk_low_pairs"] = rtk_test(low)
    out["headline"] = headline(out["N1_eph_low_pairs"]["result"], n0["result"])
    return out


# ---------------------------------------------------------------- description (not graded)

def _rate(rs, med):
    loss = [value(r["m"], "loss") for r in rs]
    deg = [degraded(r, med) for r in rs]
    loss = [x for x in loss if x is not None]
    deg = [x for x in deg if x is not None]
    return {"logs": len(rs), "fix_loss": [sum(loss), len(loss)], "degraded_eph": [sum(deg), len(deg)]}


def lst_bin(lst):
    if 19.5 <= lst < 21.0:
        return "19:30-21:00"
    if 21.0 <= lst < 23.0:
        return "21:00-23:00"
    return "23:00-01:00"


def describe(survey, pairs):
    """Counts and shares by band and time class, and for low-band night logs by local time, sector, month and year."""
    med = day_medians(survey)
    out = {"survey_by_band_class": {}, "pairs_by_band": {}}
    for r in survey:
        k = "%s/%s" % (r["band"], r["cls"])
        out["survey_by_band_class"][k] = out["survey_by_band_class"].get(k, 0) + 1
    out["survey_by_band_class"] = dict(sorted(out["survey_by_band_class"].items()))
    for p in pairs:
        out["pairs_by_band"][p["band"]] = out["pairs_by_band"].get(p["band"], 0) + 1
    groups = {}
    for r in survey:
        if r["band"] in ("low", "mid") and r["cls"] in ("night", "day", "dusk"):
            groups.setdefault("%s/%s" % (r["band"], r["cls"]), []).append(r)
    out["rates_by_band_class"] = {k: _rate(v, med) for k, v in sorted(groups.items())}
    low_night = [r for r in survey if r["band"] == "low" and r["cls"] == "night"]
    for name, key in (("local_time", lambda r: lst_bin(r["lst"])),
                      ("sector", lambda r: sector(r["m"]["cell"][1] + 0.5)),
                      ("upload_month", lambda r: str(r.get("date") or "")[5:7]),
                      ("upload_year", lambda r: str(r.get("date") or "")[:4])):
        d = {}
        for r in low_night:
            d.setdefault(key(r), []).append(r)
        out["low_night_by_" + name] = {k: _rate(v, med) for k, v in sorted(d.items())}
    return out
