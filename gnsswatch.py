"""gnsswatch: GNSS interference as seen by drones, read from the start of public PX4 flight logs.

Part of drone-gnss-rfi (study 1, pre-registered in PLAN.md). It holds
- a small ULog reader (standard library only) that decodes just the topics it is asked for and
  works on a truncated file, so a study can read the first few MB of each log;
- per-log measures of what the GNSS receiver reported: spoofing and jamming states, the jamming
  indicator (split into disarmed and armed samples), position jumps no aircraft can fly
  ("teleports"), receiver-time jumps, and the 1-degree cell of the first good fix;
- the study's sample rule and its pre-registered grading.

No exact position leaves this module: positions are reduced to 1-degree cells and region names.

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import datetime
import hashlib
import math
import re
import struct

__version__ = "0.1.0"

# ---------------------------------------------------------------- ULog reading

MAGIC = b"ULog\x01\x12\x35"
SYNC = bytes([0x2F, 0x73, 0x13, 0x20, 0x25, 0x0C, 0xBB, 0x12])
KNOWN = set(b"FIAMPQBDLCSOR")
BASIC = {"int8_t": ("b", 1), "uint8_t": ("B", 1), "int16_t": ("h", 2), "uint16_t": ("H", 2),
         "int32_t": ("i", 4), "uint32_t": ("I", 4), "int64_t": ("q", 8), "uint64_t": ("Q", 8),
         "float": ("f", 4), "double": ("d", 8), "bool": ("B", 1), "char": ("B", 1)}
_HDR = struct.Struct("<HB")
_U16 = struct.Struct("<H")


class NotULog(ValueError):
    pass


def parse_format(text):
    """('name', [(type, count or None, field), ...]) from a format string 'name:type field;type[n] field;'."""
    name, sep, rest = text.partition(":")
    if not sep or not name.strip():
        raise ValueError("not a format string")
    fields = []
    for part in rest.split(";"):
        part = part.strip()
        if not part:
            continue
        typ, _, fname = part.rpartition(" ")
        typ, fname = typ.strip(), fname.strip()
        if not typ or not fname:
            raise ValueError("bad field %r" % part)
        count = None
        if typ.endswith("]") and "[" in typ:
            typ, _, n = typ[:-1].partition("[")
            count = int(n)
        fields.append((typ, count, fname))
    return name.strip(), fields


def layout(name, formats, _depth=0):
    """(struct codes without the '<' prefix, [flat field names]) of one format. Nested types become
    'outer.inner', arrays 'field[i]'; _padding fields are skipped bytes with no name."""
    if _depth > 32:
        raise ValueError("nesting too deep")
    codes, names = [], []
    for typ, count, fname in formats[name]:
        reps = 1 if count is None else count
        pad = fname.startswith("_padding")
        if typ in BASIC:
            code, size = BASIC[typ]
            if pad:
                codes.append("%dx" % (size * reps))
                continue
            for i in range(reps):
                codes.append(code)
                names.append(fname if count is None else "%s[%d]" % (fname, i))
        else:
            sub_codes, sub_names = layout(typ, formats, _depth + 1)
            if pad:
                codes.append("%dx" % (struct.calcsize("<" + sub_codes) * reps))
                continue
            for i in range(reps):
                prefix = fname if count is None else "%s[%d]" % (fname, i)
                codes.append(sub_codes)
                names.extend(prefix + "." + n for n in sub_names)
    return "".join(codes), names


def resolve(names, want):
    """The flat name for a wanted field: an exact match, else the first nested name ending in '.want'."""
    if want in names:
        return want
    tail = "." + want
    for n in names:
        if n.endswith(tail):
            return n
    return None


def read_topics(buf, wanted):
    """Decode the topics named in `wanted` ({topic: [field, ...]}) from ULog bytes, which may be cut short.

    Returns (topics, info). topics maps (topic, multi_id) to {"fields": [resolved name or None per wanted
    field], "rows": [tuple of values in the wanted order, None where the log has no such field]}.
    info has "bytes", "truncated" (bytes left over that do not form a whole message) and "resyncs"."""
    if len(buf) < 16 or bytes(buf[:7]) != MAGIC:
        raise NotULog("not a ULog file")
    formats, subs, topics = {}, {}, {}
    info = {"bytes": len(buf), "truncated": False, "resyncs": 0}
    pos, n = 16, len(buf)
    while pos + 3 <= n:
        size, mtype = _HDR.unpack_from(buf, pos)
        if mtype not in KNOWN:
            nxt = buf.find(SYNC, pos + 1)
            if nxt < 0:
                break
            info["resyncs"] += 1
            pos = nxt + len(SYNC)
            continue
        start, end = pos + 3, pos + 3 + size
        if end > n:
            break
        if mtype == 0x44:                                   # 'D' data
            if size >= 2:
                sub = subs.get(_U16.unpack_from(buf, start)[0])
                if sub is not None:
                    st, idx, rows = sub
                    payload = buf[start + 2:end]
                    if len(payload) < st.size:              # a trailing _padding field may not be logged
                        payload = payload + b"\0" * (st.size - len(payload))
                    vals = st.unpack_from(payload, 0)
                    rows.append(tuple(vals[i] if i is not None else None for i in idx))
        elif mtype == 0x46:                                 # 'F' format
            try:
                name, fields = parse_format(buf[start:end].decode("utf-8", "replace").rstrip("\0"))
            except ValueError:
                pass
            else:
                formats[name] = fields
        elif mtype == 0x41:                                 # 'A' subscription
            if size >= 3:
                multi_id = buf[start]
                msg_id = _U16.unpack_from(buf, start + 1)[0]
                name = buf[start + 3:end].decode("utf-8", "replace").rstrip("\0")
                subs.pop(msg_id, None)
                if name in wanted:
                    try:
                        codes, names = layout(name, formats)
                        st = struct.Struct("<" + codes)
                    except (KeyError, ValueError, struct.error):
                        pass
                    else:
                        res = [resolve(names, w) for w in wanted[name]]
                        idx = [names.index(r) if r is not None else None for r in res]
                        topic = topics.setdefault((name, multi_id), {"fields": res, "rows": []})
                        subs[msg_id] = (st, idx, topic["rows"])
        elif mtype == 0x52:                                 # 'R' remove subscription
            if size >= 2:
                subs.pop(_U16.unpack_from(buf, start)[0], None)
        pos = end
    info["truncated"] = pos < n
    return topics, info


# ---------------------------------------------------------------- per-log measures

GPS_TOPICS = ("sensor_gps", "vehicle_gps_position", "vehicle_gnss")
GPS_FIELDS = ("timestamp", "fix_type", "latitude_deg", "longitude_deg", "lat", "lon", "eph", "satellites_used",
              "spoofing_state", "jamming_state", "jamming_indicator", "noise_per_ms", "automatic_gain_control",
              "time_utc_usec")
STATUS_FIELDS = ("timestamp", "arming_state")
WANTED = dict({t: GPS_FIELDS for t in GPS_TOPICS}, vehicle_status=STATUS_FIELDS)
_G = {f: i for i, f in enumerate(GPS_FIELDS)}

EARTH_R_M = 6371008.8
TELEPORT_MIN_M = 1000.0
TELEPORT_MIN_SPEED = 150.0          # m/s; no multicopter or small plane in these logs flies this fast
UTC_JUMP_US = 2000000               # receiver time running 2 s off the log clock between two samples
FIRST_FIX_MAX_EPH = 20.0
FIRST_FIX_MIN_SATS = 6
MIN_REPORTS = 10
SUSTAINED_SHARE = 0.25
ARMED = 2                           # vehicle_status.arming_state ARMED in PX4 v1.13 to main

# Regions with widely reported GNSS interference since 2022, fixed before any log was read (PLAN.md).
# (name, lat_min, lat_max, lon_min, lon_max), half-open boxes on the first good fix.
REGIONS = (("baltic", 53.0, 61.0, 13.0, 30.0),
           ("levant", 29.0, 37.5, 32.0, 40.0),
           ("black_sea", 43.0, 53.0, 30.0, 42.0),
           ("gulf", 23.0, 31.0, 47.0, 57.0))


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def region(lat, lon):
    for name, la0, la1, lo0, lo1 in REGIONS:
        if la0 <= lat < la1 and lo0 <= lon < lo1:
            return name
    return "rest"


def cell(lat, lon):
    return [int(math.floor(lat)), int(math.floor(lon))]


def median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2


def position(row):
    """(lat, lon) in degrees from latitude_deg/longitude_deg, or from the older int32 lat/lon (1e-7 deg)."""
    la, lo = row[_G["latitude_deg"]], row[_G["longitude_deg"]]
    if la is None or lo is None:
        la, lo = row[_G["lat"]], row[_G["lon"]]
        if la is None or lo is None:
            return None
        la, lo = la * 1e-7, lo * 1e-7
    if not (math.isfinite(la) and math.isfinite(lo)) or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    if la == 0 and lo == 0:
        return None
    return la, lo


def utc_date(us):
    try:
        d = datetime.datetime.fromtimestamp(us / 1e6, tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return d.strftime("%Y-%m-%d") if 2015 <= d.year <= 2035 else None


def measure(gps_rows, status_rows=()):
    """Per-log measures from GNSS rows (tuples in GPS_FIELDS order) and vehicle_status rows
    ((timestamp, arming_state) tuples), both in log order."""
    g = _G
    spoof, jam = [0, 0, 0, 0], [0, 0, 0, 0]
    jind, noise, agc = [], [], []
    n_fix, first = 0, None
    teleports, max_jump, dest = 0, None, None
    utc_jumps, max_utc, first_utc = 0, None, None
    prev_fix = prev_utc = None
    status = [s for s in status_rows if s[0] is not None and s[1] is not None]
    si, armed_now = 0, None
    dis, arm = [], []
    for r in gps_rows:
        t = r[g["timestamp"]]
        s = r[g["spoofing_state"]]
        if s is not None and 0 <= s <= 3:
            spoof[s] += 1
        j = r[g["jamming_state"]]
        if j is not None and 0 <= j <= 3:
            jam[j] += 1
        ji = r[g["jamming_indicator"]]
        if ji is not None:
            jind.append(ji)
            if t is not None:
                while si < len(status) and status[si][0] <= t:
                    armed_now = status[si][1] == ARMED
                    si += 1
                if armed_now is True:
                    arm.append(ji)
                elif armed_now is False:
                    dis.append(ji)
        if r[g["noise_per_ms"]] is not None:
            noise.append(r[g["noise_per_ms"]])
        if r[g["automatic_gain_control"]] is not None:
            agc.append(r[g["automatic_gain_control"]])
        u = r[g["time_utc_usec"]]
        if u and t is not None:
            if first_utc is None:
                first_utc = u
            if prev_utc is not None:
                d = abs((u - prev_utc[0]) - (t - prev_utc[1]))
                if d > UTC_JUMP_US:
                    utc_jumps += 1
                    max_utc = d / 1e6 if max_utc is None else max(max_utc, d / 1e6)
            prev_utc = (u, t)
        fix = r[g["fix_type"]]
        pos = position(r) if fix is not None and fix >= 3 else None
        if pos is None or t is None:
            continue
        n_fix += 1
        if first is None:
            eph, sats = r[g["eph"]], r[g["satellites_used"]]
            if (eph is None or eph < FIRST_FIX_MAX_EPH) and (sats is None or sats >= FIRST_FIX_MIN_SATS):
                first = {"cell": cell(*pos), "region": region(*pos)}
        if prev_fix is not None:
            dt = (t - prev_fix[2]) / 1e6
            if dt > 0:
                dist = haversine_m(prev_fix[0], prev_fix[1], pos[0], pos[1])
                if dist > TELEPORT_MIN_M and dist / dt > TELEPORT_MIN_SPEED:
                    teleports += 1
                    km = dist / 1000.0
                    max_jump = km if max_jump is None else max(max_jump, km)
                    if dest is None:
                        dest = cell(*pos)
        prev_fix = (pos[0], pos[1], t)
    return {"n_gps": len(gps_rows), "n_fix": n_fix, "spoof": spoof, "jam": jam,
            "jam_ind_median": median(jind), "jam_ind_max": max(jind) if jind else None,
            "noise_median": median(noise), "agc_median": median(agc),
            "first_fix": first, "teleports": teleports,
            "max_jump_km": round(max_jump, 1) if max_jump is not None else None, "teleport_dest_cell": dest,
            "utc_jumps": utc_jumps, "max_utc_jump_s": round(max_utc, 3) if max_utc is not None else None,
            "utc_date": utc_date(first_utc) if first_utc else None,
            "armed_split": {"n_disarmed": len(dis), "n_armed": len(arm),
                            "jam_ind_median_disarmed": median(dis), "jam_ind_median_armed": median(arm)}}


def flags(m, min_reports=MIN_REPORTS):
    rep = m["spoof"][1] + m["spoof"][2] + m["spoof"][3]
    ind = m["spoof"][2] + m["spoof"][3]
    jrep = m["jam"][1] + m["jam"][2] + m["jam"][3]
    return {"spoof_reporting": rep >= min_reports, "spoof_any": ind > 0,
            "spoof_sustained": rep > 0 and ind / rep >= SUSTAINED_SHARE, "spoof_multi": m["spoof"][3] > 0,
            "jam_reporting": jrep >= min_reports, "jam_warn": m["jam"][2] + m["jam"][3] > 0,
            "jam_crit": m["jam"][3] > 0, "manipulated": m["teleports"] > 0 or m["utc_jumps"] > 0}


def choose_topic(topics):
    """The GNSS topic a log is measured on: the lowest instance of sensor_gps, vehicle_gps_position,
    vehicle_gnss (in that order) whose rows carry a non-zero spoofing state; else the first with rows."""
    cands = []
    for t in GPS_TOPICS:
        keys = sorted(k for k in topics if k[0] == t and topics[k]["rows"])
        if keys:
            cands.append(keys[0])
    if not cands:
        return None
    si = _G["spoofing_state"]
    for k in cands:
        if any(r[si] for r in topics[k]["rows"]):
            return k
    return cands[0]


def measure_log(buf):
    """(topic key or None, measures or None, parse info) for one log's bytes."""
    topics, info = read_topics(buf, WANTED)
    key = choose_topic(topics)
    if key is None:
        return None, None, info
    status = []
    skeys = sorted(k for k in topics if k[0] == "vehicle_status")
    if skeys:
        status = topics[skeys[0]]["rows"]
    return key, measure(topics[key]["rows"], status), info


# ---------------------------------------------------------------- sample rule

DATE_FROM, DATE_TO = "2023-01-01", "2026-11-16"
MIN_DURATION_S = 60.0
MIN_SERIES = (1, 13)


def release_series(value):
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode()).hexdigest()


def placeholder_uuid(uuid):
    s = str(uuid or "").lower()
    return len(s) < 8 or set(s) <= set("0f-")


def eligible(entry):
    """The fields kept of one Flight Review dbinfo entry, or None if the log is outside the study."""
    if "SITL" in str(entry.get("sys_hw") or "").upper():
        return None
    if "simul" in str(entry.get("type") or "").lower():
        return None
    if str(entry.get("source") or "").upper() == "CI":
        return None
    if not entry.get("log_id") or placeholder_uuid(entry.get("vehicle_uuid")):
        return None
    try:
        duration = float(entry.get("duration_s") or 0)
    except (TypeError, ValueError):
        return None
    if duration < MIN_DURATION_S:
        return None
    date = str(entry.get("log_date") or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date) or not DATE_FROM <= date <= DATE_TO:
        return None
    rs = release_series(entry.get("ver_sw_release"))
    if rs is None or rs < MIN_SERIES:
        return None
    return {"log_id": str(entry["log_id"]), "v": sha(entry.get("vehicle_uuid"))[:12], "date": date,
            "rel": str(entry.get("ver_sw_release") or ""), "hw": str(entry.get("sys_hw") or ""),
            "mav": str(entry.get("mav_type") or ""), "dur": int(duration)}


def pick_sample(entries):
    """One log per vehicle (the one whose log id has the smallest sha256), vehicles in that hash order."""
    best = {}
    for e in entries:
        h = sha(e["log_id"])
        cur = best.get(e["v"])
        if cur is None or h < cur[0]:
            best[e["v"]] = (h, e)
    return [e for _h, e in sorted(best.values(), key=lambda x: x[0])]


# ---------------------------------------------------------------- statistics and grading

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


def _ff(r):
    return (r.get("m") or {}).get("first_fix")


def _split(rows, need):
    s = [r for r in rows if r.get("m") and r.get("flags") and r["flags"][need] and _ff(r)]
    return [r for r in s if _ff(r)["region"] != "rest"], [r for r in s if _ff(r)["region"] == "rest"]


def _count(rs, flag):
    return sum(1 for r in rs if r["flags"][flag])


def _compare(hot, rest, flag, min_hot=15, min_rest=50, margin=0.10):
    kh, nh, kr, nr = _count(hot, flag), len(hot), _count(rest, flag), len(rest)
    out = {"hot": [kh, nh], "rest": [kr, nr], "hot_ci": wilson(kh, nh), "rest_ci": wilson(kr, nr)}
    if nh < min_hot or nr < min_rest:
        out["result"] = "too few"
        return out
    diff = kh / nh - kr / nr
    p = fisher_greater(kh, nh - kh, kr, nr - kr)
    out.update(diff=round(diff, 4), p=p, result="held" if diff >= margin - 1e-12 and p < 0.05 else "failed")
    return out


def grade(rows):
    """The pre-registered predictions of PLAN.md on measured rows."""
    out = {}
    hot, rest = _split(rows, "spoof_reporting")
    out["H1_spoof_hot_vs_rest"] = _compare(hot, rest, "spoof_any")
    kr, nr = _count(rest, "spoof_any"), len(rest)
    h2 = {"rest": [kr, nr], "rest_ci": wilson(kr, nr), "limit": 0.12}
    h2["result"] = "too few" if nr < 50 else ("held" if kr / nr <= 0.12 + 1e-12 else "failed")
    out["H2_background_rate"] = h2
    flagged = [r for r in rest if r["flags"]["spoof_any"]]
    km, nm = _count(flagged, "manipulated"), len(flagged)
    h3 = {"manipulated": [km, nm], "limit": 0.20}
    h3["result"] = "too few" if nm < 10 else ("held" if km / nm <= 0.20 + 1e-12 else "failed")
    out["H3_background_not_manipulated"] = h3
    jhot, jrest = _split(rows, "jam_reporting")
    out["H4_jam_hot_vs_rest"] = _compare(jhot, jrest, "jam_warn")
    split = []
    for r in rows:
        m = r.get("m") or {}
        a = m.get("armed_split") or {}
        if (a.get("n_disarmed") or 0) >= 20 and (a.get("n_armed") or 0) >= 20 and (m.get("jam_ind_max") or 0) > 0:
            split.append(a["jam_ind_median_armed"] > a["jam_ind_median_disarmed"])
    k5, n5 = sum(split), len(split)
    h5 = {"armed_higher": [k5, n5], "ci": wilson(k5, n5), "limit": 0.60}
    h5["result"] = "too few" if n5 < 20 else ("held" if k5 / n5 >= 0.60 - 1e-12 else "failed")
    out["H5_self_interference"] = h5
    return out
