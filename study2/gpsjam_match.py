"""gpsjam_match: do drones report GNSS interference on the days and places where aircraft see it?

Part of drone-gnss-rfi (study 2, pre-registered in study2/PLAN-matched.md). It takes study 1's per-log rows
(1-degree cell of the drone's first good fix, the log's UTC date, the receiver's spoofing and jamming flags) and
matches each log to the share of aircraft that reported degraded navigation accuracy on the same UTC day in the
3 x 3 block of 1-degree cells around it, from GPSJam's public daily H3 files (gpsjam.org, by John Wiseman, built from
airplanes.live and ADS-B Exchange reports). It holds the parsing, the exposure measure, the statistics and the
pre-registered grading. Standard library only; the H3 cell-centre function is passed in (the job uses the h3 package).

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import math
import re

__version__ = "0.1.0"

NEIGHBOURHOOD = 1            # cells on each side of the drone's 1-degree cell: a 3 x 3 block
MIN_VISITS = 30              # aircraft-hex reports in the block below which exposure is unknown
EXPOSED_SHARE = 0.05         # bad / (good + bad) at or above which a log counts as exposed
GPSJAM_START = "2022-02-14"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode()).hexdigest()


# ---------------------------------------------------------------- GPSJam files

def parse_manifest(text):
    """{date: suspect (bool)} from GPSJam's manifest.csv (date,suspect,num_bad_aircraft_hexes,source)."""
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None) or []
    idx = {h.strip(): i for i, h in enumerate(header)}
    if "date" not in idx or "suspect" not in idx:
        raise ValueError("unexpected GPSJam manifest header %r" % header)
    out = {}
    for row in rows:
        try:
            d, s = row[idx["date"]].strip(), row[idx["suspect"]].strip().lower()
        except IndexError:
            continue
        if _DAY.match(d):
            out[d] = s == "true"
    return out


def parse_day(text, centre, wanted, cache=None):
    """Sum aircraft reports per 1-degree cell for one GPSJam day file (hex,count_good_aircraft,count_bad_aircraft).

    centre(hex) -> (lat, lon) of the H3 cell centre; wanted: set of (lat, lon) integer cells to keep; cache: an
    optional dict hex -> cell reused across days. Returns {cell: [good, bad, hexes]}; rows that do not parse and
    hexes whose centre cannot be computed are skipped."""
    rows = csv.reader(io.StringIO(text))
    header = next(rows, None) or []
    idx = {h.strip(): i for i, h in enumerate(header)}
    try:
        ih, ig, ib = idx["hex"], idx["count_good_aircraft"], idx["count_bad_aircraft"]
    except KeyError:
        raise ValueError("unexpected GPSJam day header %r" % header)
    out = {}
    for row in rows:
        try:
            hx, good, bad = row[ih].strip(), int(row[ig]), int(row[ib])
        except (IndexError, ValueError):
            continue
        if good < 0 or bad < 0:
            continue
        if cache is not None and hx in cache:
            c = cache[hx]
        else:
            try:
                lat, lon = centre(hx)
                c = (int(math.floor(lat)), int(math.floor(lon)))
            except Exception:                              # noqa: BLE001 (any bad hex id)
                c = None
            if cache is not None:
                cache[hx] = c
        if c is None or c not in wanted:
            continue
        g = out.setdefault(c, [0, 0, 0])
        g[0] += good
        g[1] += bad
        g[2] += 1
    return out


# ---------------------------------------------------------------- exposure

def block_cells(cell, k=NEIGHBOURHOOD):
    """The (2k+1) x (2k+1) block of 1-degree cells around cell, longitudes wrapped, latitudes kept in -90..89."""
    la, lo = int(cell[0]), int(cell[1])
    out = []
    for dla in range(-k, k + 1):
        a = la + dla
        if a < -90 or a > 89:
            continue
        for dlo in range(-k, k + 1):
            out.append((a, (lo + dlo + 180) % 360 - 180))
    return out


def exposure(day_cells, cell, min_visits=MIN_VISITS):
    """{"share": bad / (good + bad) or None when fewer than min_visits reports, "visits", "hexes"} over the block."""
    good = bad = hexes = 0
    for c in block_cells(cell):
        g = day_cells.get(c)
        if g:
            good += g[0]
            bad += g[1]
            hexes += g[2]
    n = good + bad
    return {"share": (bad / n) if n >= min_visits and n > 0 else None, "visits": n, "hexes": hexes}


def row_day(row):
    """The flight's UTC day from the drone's own receiver clock (study 1's m.utc_date, the first receiver UTC time).

    None when it is missing, before GPSJam's first day, or more than one day after the log's Flight Review date
    (which is the upload date, so a flight cannot be later). The upload date is never used as the flight day."""
    m = row.get("m") or {}
    d = str(m.get("utc_date") or "")[:10]
    if not _DAY.match(d) or d < GPSJAM_START:
        return None
    up = str(row.get("date") or "")[:10]
    if _DAY.match(up):
        try:
            limit = (datetime.date.fromisoformat(up) + datetime.timedelta(days=1)).isoformat()
        except ValueError:
            return d
        if d > limit:
            return None
    return d


def candidates(rows):
    """([(row, day, cell)] for study-1 rows that were measured, located and dated, {day: set of block cells needed},
    {"measured": n, "located": n, "dated": n})."""
    out, needed = [], {}
    counts = {"measured": 0, "located": 0, "dated": 0}
    for r in rows:
        m, f = r.get("m"), r.get("flags")
        if not m or not f:
            continue
        counts["measured"] += 1
        if not m.get("first_fix"):
            continue
        counts["located"] += 1
        day = row_day(r)
        if day is None:
            continue
        counts["dated"] += 1
        cell = tuple(int(x) for x in m["first_fix"]["cell"])
        out.append((r, day, cell))
        needed.setdefault(day, set()).update(block_cells(cell))
    return out, needed, counts


def record(row, day, cell, exp, day_status):
    """The matched record of one log (no position finer than the 1-degree cell)."""
    m, f = row["m"], row["flags"]
    a = m.get("armed_split") or {}
    return {"log_id": row.get("log_id"), "day": day, "cell": list(cell), "region": m["first_fix"].get("region"),
            "day_status": day_status,
            "share": exp["share"] if exp else None, "visits": exp["visits"] if exp else 0,
            "hexes": exp["hexes"] if exp else 0,
            "spoof_reporting": bool(f.get("spoof_reporting")), "spoof_any": bool(f.get("spoof_any")),
            "spoof_sustained": bool(f.get("spoof_sustained")), "jam_reporting": bool(f.get("jam_reporting")),
            "jam_warn": bool(f.get("jam_warn")), "manipulated": bool(f.get("manipulated")),
            "teleports": m.get("teleports") or 0,
            "jam_dis": a.get("jam_ind_median_disarmed"), "n_dis": a.get("n_disarmed") or 0,
            "jam_max": m.get("jam_ind_max")}


# ---------------------------------------------------------------- statistics

Z95 = 1.959963984540054


def wilson(k, n):
    if not n:
        return None
    p, z2 = k / n, Z95 * Z95
    den = 1 + z2 / n
    c = (p + z2 / (2 * n)) / den
    h = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def fisher_greater(a, b, c, d):
    """One-sided Fisher exact p for the table [[a, b], [c, d]]: P(top-left >= a) with the margins fixed."""
    n1, k, total = a + b, a + c, a + b + c + d
    top = sum(math.comb(k, x) * math.comb(total - k, n1 - x) for x in range(a, min(n1, k) + 1))
    return top / math.comb(total, n1)


def auc_mw(xs, ys):
    """(AUC, one-sided p) for xs tending to be larger than ys: AUC = P(X > Y) + P(X = Y) / 2 from the Mann-Whitney U,
    p from the normal approximation with tie correction and continuity correction (1.0 when every value ties)."""
    n1, n2 = len(xs), len(ys)
    if not n1 or not n2:
        return None, None
    allv = sorted([(float(v), 0) for v in xs] + [(float(v), 1) for v in ys], key=lambda t: t[0])
    n = len(allv)
    r1, tie_term, i = 0.0, 0.0, 0
    while i < n:
        j = i
        while j + 1 < n and allv[j + 1][0] == allv[i][0]:
            j += 1
        rank = (i + j) / 2 + 1
        t = j - i + 1
        tie_term += t ** 3 - t
        r1 += rank * sum(1 for q in range(i, j + 1) if allv[q][1] == 0)
        i = j + 1
    u1 = r1 - n1 * (n1 + 1) / 2
    auc = u1 / (n1 * n2)
    var = n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return auc, 1.0
    z = (u1 - n1 * n2 / 2.0 - 0.5) / math.sqrt(var)
    return auc, 0.5 * math.erfc(z / math.sqrt(2))


# ---------------------------------------------------------------- grading (PLAN-matched.md)

def _exposed(r, threshold):
    return r["share"] >= threshold - 1e-12


def _compare(a, b, flag, min_a, min_b, margin):
    ka, na, kb, nb = sum(1 for r in a if r[flag]), len(a), sum(1 for r in b if r[flag]), len(b)
    out = {"exposed": [ka, na], "unexposed": [kb, nb], "exposed_ci": wilson(ka, na), "unexposed_ci": wilson(kb, nb)}
    if na < min_a or nb < min_b:
        out["result"] = "too few"
        return out
    diff = ka / na - kb / nb
    p = fisher_greater(ka, na - ka, kb, nb - kb)
    out.update(diff=round(diff, 4), p=p, result="held" if diff >= margin - 1e-12 and p < 0.05 else "failed")
    return out


def _auc_test(xs, ys, min_x, min_y, limit=0.60):
    out = {"n": [len(xs), len(ys)]}
    if len(xs) < min_x or len(ys) < min_y:
        out["result"] = "too few"
        return out
    auc, p = auc_mw(xs, ys)
    out.update(auc=round(auc, 4), p=p, result="held" if auc >= limit - 1e-12 and p < 0.05 else "failed")
    return out


def grade(recs, threshold=EXPOSED_SHARE):
    """The four pre-registered predictions on matched records."""
    known = [r for r in recs if r["share"] is not None]
    out = {"threshold": threshold, "known": len(known), "exposed": sum(1 for r in known if _exposed(r, threshold))}
    rep = [r for r in known if r["spoof_reporting"]]
    out["M1_spoof_exposed_vs_not"] = _compare([r for r in rep if _exposed(r, threshold)],
                                              [r for r in rep if not _exposed(r, threshold)],
                                              "spoof_any", 10, 50, 0.10)
    out["M2_spoof_dose"] = _auc_test([r["share"] for r in rep if r["spoof_any"]],
                                     [r["share"] for r in rep if not r["spoof_any"]], 10, 50)
    js = [r for r in known if r["n_dis"] >= 20 and (r["jam_max"] or 0) > 0 and r["jam_dis"] is not None]
    out["M3_jam_disarmed_exposed_vs_not"] = _auc_test([r["jam_dis"] for r in js if _exposed(r, threshold)],
                                                      [r["jam_dis"] for r in js if not _exposed(r, threshold)], 10, 30)
    out["M4_manipulated_exposed_vs_not"] = _compare([r for r in known if _exposed(r, threshold)],
                                                    [r for r in known if not _exposed(r, threshold)],
                                                    "manipulated", 10, 50, 0.05)
    return out


BINS = ((0.0, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 1.01))


def describe(recs):
    """Not graded: counts by exposure bin, M1 at other thresholds, exposed cells, and day statuses."""
    known = [r for r in recs if r["share"] is not None]
    out = {"records": len(recs), "known": len(known), "day_status": {}, "bins": [], "m1_other_thresholds": {}}
    for r in recs:
        k = r["day_status"] if r["share"] is not None or r["day_status"] != "ok" else "low coverage"
        out["day_status"][k] = out["day_status"].get(k, 0) + 1
    for lo, hi in BINS:
        b = [r for r in known if lo <= r["share"] < hi]
        rep = [r for r in b if r["spoof_reporting"]]
        jrep = [r for r in b if r["jam_reporting"]]
        jd = sorted(r["jam_dis"] for r in b if r["n_dis"] >= 20 and r["jam_dis"] is not None)
        out["bins"].append({"share": [lo, min(hi, 1.0)], "logs": len(b),
                            "spoof_reporting": len(rep), "spoof_any": sum(1 for r in rep if r["spoof_any"]),
                            "spoof_sustained": sum(1 for r in rep if r["spoof_sustained"]),
                            "jam_reporting": len(jrep), "jam_warn": sum(1 for r in jrep if r["jam_warn"]),
                            "manipulated": sum(1 for r in b if r["manipulated"]),
                            "jam_dis_median": (jd[len(jd) // 2] if len(jd) % 2 else (jd[len(jd) // 2 - 1] +
                                               jd[len(jd) // 2]) / 2) if jd else None})
    for t in (0.02, 0.10):
        rep = [r for r in known if r["spoof_reporting"]]
        out["m1_other_thresholds"][str(t)] = _compare([r for r in rep if _exposed(r, t)],
                                                      [r for r in rep if not _exposed(r, t)], "spoof_any", 10, 50, 0.10)
    cells = {}
    for r in known:
        if _exposed(r, EXPOSED_SHARE):
            k = "%d,%d" % tuple(r["cell"])
            g = cells.setdefault(k, {"logs": 0, "spoof_reporting": 0, "spoof_any": 0, "manipulated": 0, "regions": []})
            g["logs"] += 1
            g["spoof_reporting"] += 1 if r["spoof_reporting"] else 0
            g["spoof_any"] += 1 if r["spoof_reporting"] and r["spoof_any"] else 0
            g["manipulated"] += 1 if r["manipulated"] else 0
            if r["region"] not in g["regions"]:
                g["regions"].append(r["region"])
    out["exposed_cells_1deg"] = dict(sorted(cells.items(), key=lambda kv: -kv[1]["logs"]))
    return out
