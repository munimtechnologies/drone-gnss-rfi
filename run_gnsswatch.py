"""run_gnsswatch.py: drone-gnss-rfi study 1, GNSS spoofing and jamming as seen by drones in public PX4 logs.

Pre-registered in PLAN.md. Before it reads any log it
1. checks that PLAN.md, gnsswatch.py and this file on the repo's main branch are the copies it runs
   (CRLF normalised; waits up to --wait-min minutes for the publish, else stops);
then
2. streams Flight Review's public log index (review.px4.io/dbinfo) once and applies the sample rule
   (gnsswatch.eligible, one log per vehicle, sha256 order: gnsswatch.pick_sample);
3. for each sampled log in order, reads at most the first --read-bytes bytes, decodes only the GNSS and
   vehicle_status topics and measures them (gnsswatch.measure_log); the bytes are dropped right after;
4. V1: on the first 3 logs read whole, checks the reader against pyulog (row count and spoofing states);
5. stops at --max-logs logs read or --budget-min minutes, then grades (gnsswatch.grade) and describes.
Writes rows.jsonl (one line per attempted log; positions only as 1-degree cells and region names),
summary.json and job-fetched.json.

    python run_gnsswatch.py --out-dir /out

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gnsswatch as gw  # noqa: E402

__version__ = "0.1.0"
REPO = "munimtechnologies/drone-gnss-rfi"
RAW_MAIN = "https://raw.githubusercontent.com/%s/main/%s"
PUBLISHED = ("PLAN.md", "gnsswatch.py", "run_gnsswatch.py")
DBINFO = "https://review.px4.io/dbinfo"
DOWNLOAD = "https://review.px4.io/download?log=%s"
UA = "drone-gnss-rfi/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)" % __version__
DELAY_S = 5.0
V1_LOGS = 3


class Blocked(Exception):
    pass


def norm(data):
    return data.replace(b"\r\n", b"\n")


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def wait_for_published(res, wait_min):
    want = {}
    for p in PUBLISHED:
        with open(os.path.join(HERE, p), "rb") as f:
            want[p] = gw.sha(norm(f.read()))
    t0 = time.time()
    while True:
        got = {}
        for p in PUBLISHED:
            try:
                got[p] = gw.sha(norm(get(RAW_MAIN % (REPO, p))))
            except Exception as exc:                       # noqa: BLE001
                got[p] = "error: %s" % type(exc).__name__
        if got == want:
            res["published_check"] = {"ok": True, "waited_min": round((time.time() - t0) / 60, 1), "sha256": want}
            return
        if time.time() - t0 > wait_min * 60:
            res["published_check"] = {"ok": False, "want": want, "got": got}
            raise RuntimeError("published copies do not match the ones this job runs after %d min" % wait_min)
        time.sleep(300)


def stream_sample(res):
    import ijson
    req = urllib.request.Request(DBINFO, headers={"User-Agent": UA})
    kept, seen = [], 0
    with urllib.request.urlopen(req, timeout=900) as resp:
        for entry in ijson.items(resp, "item"):
            seen += 1
            e = gw.eligible(entry)
            if e:
                kept.append(e)
    sample = gw.pick_sample(kept)
    res["dbinfo"] = {"entries": seen, "eligible_logs": len(kept), "vehicles": len(sample)}
    return sample


def whole_file(status, headers, n, eof):
    """True when the n bytes read are the whole file: a 206 whose Content-Range total is n, or a 200 read to EOF
    (or whose Content-Length is n)."""
    if status == 206:
        total = str(headers.get("Content-Range") or "").rpartition("/")[2].strip()
        return total.isdigit() and int(total) == n
    length = str(headers.get("Content-Length") or "")
    return eof or (length.isdigit() and int(length) == n)


def fetch_head(log_id, read_bytes):
    """(status, bytes or None, complete). Reads at most read_bytes of the log, gunzipping if needed."""
    req = urllib.request.Request(DOWNLOAD % log_id, headers={"User-Agent": UA,
                                                              "Range": "bytes=0-%d" % (read_bytes - 1)})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                parts, n, eof = [], 0, False
                while n < read_bytes:
                    chunk = resp.read(min(1 << 20, read_bytes - n))
                    if not chunk:
                        eof = True
                        break
                    parts.append(chunk)
                    n += len(chunk)
                data = b"".join(parts)
                complete = whole_file(resp.status, resp.headers, len(data), eof)
            if data[:2] == b"\x1f\x8b":
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = d.decompress(data, read_bytes)
                complete = complete and d.eof and not d.unconsumed_tail
                data = raw
            if data[:7] != gw.MAGIC:
                return "not a ULog", None, False
            return "ok", data, complete
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429, 444):
                raise Blocked("HTTP %d" % exc.code)
            if exc.code == 404:
                return "missing", None, False
            if 400 <= exc.code < 500:
                return "http %d" % exc.code, None, False
            time.sleep(min(30 * 2 ** attempt, 300))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(15)
    return "failed", None, False


def check_with_pyulog(data, key, m):
    """V1: the same topic read by pyulog has the same number of rows and the same spoofing-state counts."""
    from pyulog import ULog
    name, multi = key
    with tempfile.NamedTemporaryFile(suffix=".ulg", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        u = ULog(path, [name], True)
        for d in u.data_list:
            if d.name == name and d.multi_id == multi:
                n = len(d.data["timestamp"])
                spoof = None
                for fname in d.data:
                    if fname == "spoofing_state" or fname.endswith(".spoofing_state"):
                        vals = [int(x) for x in d.data[fname]]
                        spoof = [sum(1 for x in vals if x == s) for s in range(4)]
                        break
                return {"pyulog_rows": n, "rows": m["n_gps"], "pyulog_spoof": spoof, "spoof": m["spoof"],
                        "match": n == m["n_gps"] and (spoof is None or spoof == m["spoof"])}
        return {"match": False, "error": "topic not found by pyulog"}
    finally:
        os.remove(path)


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def describe(rows):
    read = [r for r in rows if r.get("m")]
    out = {"attempted": len(rows), "read": len(read),
           "status": {}, "topics": {}, "complete_reads": sum(1 for r in read if r.get("complete")),
           "spoof_reporting": sum(1 for r in read if r["flags"]["spoof_reporting"]),
           "jam_reporting": sum(1 for r in read if r["flags"]["jam_reporting"]),
           "located": sum(1 for r in read if r["m"]["first_fix"])}
    for r in rows:
        out["status"][r["status"]] = out["status"].get(r["status"], 0) + 1
        if r.get("topic"):
            out["topics"][r["topic"]] = out["topics"].get(r["topic"], 0) + 1
    regions = {}
    for r in read:
        ff = r["m"]["first_fix"]
        if not ff:
            continue
        g = regions.setdefault(ff["region"], {"located": 0, "spoof_reporting": 0, "spoof_any": 0,
                                              "spoof_sustained": 0, "spoof_multi": 0, "jam_reporting": 0,
                                              "jam_warn": 0, "jam_crit": 0, "manipulated": 0, "teleports": 0})
        f = r["flags"]
        g["located"] += 1
        g["teleports"] += 1 if r["m"]["teleports"] else 0
        g["manipulated"] += 1 if f["manipulated"] else 0
        if f["spoof_reporting"]:
            g["spoof_reporting"] += 1
            for k in ("spoof_any", "spoof_sustained", "spoof_multi"):
                g[k] += 1 if f[k] else 0
        if f["jam_reporting"]:
            g["jam_reporting"] += 1
            for k in ("jam_warn", "jam_crit"):
                g[k] += 1 if f[k] else 0
    for g in regions.values():
        g["spoof_any_ci"] = gw.wilson(g["spoof_any"], g["spoof_reporting"])
    out["regions"] = regions
    grid = {}
    for r in read:
        ff = r["m"]["first_fix"]
        if not ff or not r["flags"]["spoof_reporting"]:
            continue
        key = "%d,%d" % (ff["cell"][0] // 5 * 5, ff["cell"][1] // 5 * 5)
        g = grid.setdefault(key, [0, 0, 0])
        g[0] += 1
        g[1] += 1 if r["flags"]["spoof_any"] else 0
        g[2] += 1 if r["flags"]["spoof_sustained"] else 0
    out["grid5_reporting_any_sustained"] = {k: v for k, v in sorted(grid.items()) if v[0] >= 3}
    out["grid5_cells_hidden_below_3"] = sum(1 for v in grid.values() if v[0] < 3)
    halves = {}
    for r in read:
        ff = r["m"]["first_fix"]
        if not ff or not r["flags"]["spoof_reporting"]:
            continue
        d = r.get("date") or ""
        if len(d) < 7:
            continue
        h = "%s-H%d" % (d[:4], 1 if int(d[5:7]) <= 6 else 2)
        side = "rest" if ff["region"] == "rest" else "hot"
        g = halves.setdefault(h, {"hot": [0, 0], "rest": [0, 0]})
        g[side][1] += 1
        g[side][0] += 1 if r["flags"]["spoof_any"] else 0
    out["half_years_any_of_reporting"] = dict(sorted(halves.items()))
    dests = {}
    for r in read:
        c = r["m"]["teleport_dest_cell"]
        if c:
            k = "%d,%d" % tuple(c)
            dests[k] = dests.get(k, 0) + 1
    out["teleport_destinations_1deg"] = dict(sorted(dests.items(), key=lambda kv: -kv[1]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/out")
    ap.add_argument("--max-logs", type=int, default=2000)
    ap.add_argument("--budget-min", type=float, default=240)
    ap.add_argument("--wait-min", type=float, default=150)
    ap.add_argument("--read-bytes", type=int, default=5000000)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_gnsswatch": __version__, "gnsswatch": gw.__version__, "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "settings": {"max_logs": a.max_logs, "budget_min": a.budget_min, "read_bytes": a.read_bytes,
                        "delay_s": DELAY_S}}
    rows, v1 = [], []
    try:
        wait_for_published(res, a.wait_min)
        sample = stream_sample(res)
        gc.collect()
        t0, n_read, stop = time.time(), 0, "sample exhausted"
        for i, e in enumerate(sample):
            if n_read >= a.max_logs:
                stop = "log cap"
                break
            if time.time() - t0 > a.budget_min * 60:
                stop = "time budget"
                break
            if i:
                time.sleep(DELAY_S)
            row = {"log_id": e["log_id"], "date": e["date"], "rel": e["rel"], "hw": e["hw"], "mav": e["mav"],
                   "dur": e["dur"]}
            try:
                status, data, complete = fetch_head(e["log_id"], a.read_bytes)
            except Blocked as exc:
                stop = "blocked: %s" % exc
                break
            row.update(status=status, complete=complete)
            if data is not None:
                row["bytes"] = len(data)
                try:
                    key, m, info = gw.measure_log(data)
                    row.update(truncated=info["truncated"], resyncs=info["resyncs"])
                    if m is None:
                        row["status"] = "no GNSS topic"
                    else:
                        row.update(topic="%s/%d" % key, m=m, flags=gw.flags(m))
                        n_read += 1
                        if complete and len(v1) < V1_LOGS and m["n_gps"] >= 10:
                            try:
                                v1.append(dict(check_with_pyulog(data, key, m), log_id=e["log_id"]))
                            except Exception as exc:          # noqa: BLE001
                                v1.append({"log_id": e["log_id"], "match": False,
                                           "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
                except gw.NotULog:
                    row["status"] = "not a ULog"
                except Exception as exc:                      # noqa: BLE001
                    row["status"] = "parse error"
                    row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            data = None
            rows.append(row)
            if len(rows) % 25 == 0:
                write_jsonl(os.path.join(out, "rows.jsonl"), rows)
                gc.collect()
        res.update(stop=stop, attempted=len(rows), read=n_read, minutes=round((time.time() - t0) / 60, 1))
        res["ok"] = True
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)

    write_jsonl(os.path.join(out, "rows.jsonl"), rows)
    summary = {"gnsswatch": gw.__version__, "v1_pyulog": v1,
               "v1_result": ("held" if all(x.get("match") for x in v1) else "failed") if len(v1) >= V1_LOGS
               else ("failed" if any(not x.get("match") for x in v1) else "too few")}
    try:
        summary["grade"] = gw.grade(rows)
    except Exception:                                         # noqa: BLE001
        summary["grade_error"] = traceback.format_exc()[-2000:]
    try:
        summary["described"] = describe(rows)
    except Exception:                                         # noqa: BLE001
        summary["describe_error"] = traceback.format_exc()[-2000:]
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "job-fetched.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({"res": {k: v for k, v in res.items() if k != "published_check"},
                      "v1": summary["v1_result"], "grade": summary.get("grade")}, indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
