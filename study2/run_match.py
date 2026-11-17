"""run_match.py: drone-gnss-rfi study 2, drone GNSS flags matched to same-day aircraft interference (GPSJam).

Pre-registered in study2/PLAN-matched.md. Before it reads any data it
1. checks that PLAN-matched.md, gpsjam_match.py and this file on the repo's main branch (folder study2/) are the copies
   it runs (CRLF normalised; waits up to --wait-min minutes for the publish, else stops);
then
2. reads study 1's published per-log rows (study1/rows.jsonl on main, or --rows) and records their sha256;
3. reads GPSJam's manifest, and for each UTC day that has a located study-1 log fetches that day's H3 file
   (gpsjam.org/data/<day>-h3_4.csv), keeping only the 1-degree cells around the logs (1 s pause between files);
4. matches each log to the aircraft "bad" share in the 3 x 3 block of 1-degree cells around its first good fix,
   grades M1-M4 (gpsjam_match.grade) and describes.
Writes matched.jsonl (one line per located log; positions only as 1-degree cells), summary-matched.json and
job-fetched.json. GPSJam's own files are not copied.

    python run_match.py --out-dir /out

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gpsjam_match as gm  # noqa: E402

__version__ = "0.1.0"
REPO = "munimtechnologies/drone-gnss-rfi"
RAW_MAIN = "https://raw.githubusercontent.com/%s/main/%s"
PUBLISHED = {"PLAN-matched.md": "study2/PLAN-matched.md", "gpsjam_match.py": "study2/gpsjam_match.py",
             "run_match.py": "study2/run_match.py"}
ROWS_PATH = "study1/rows.jsonl"
MANIFEST = "https://gpsjam.org/data/manifest.csv"
DAY_FILE = "https://gpsjam.org/data/%s-h3_4.csv"
UA = "drone-gnss-rfi/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research matching drone logs to GPSJam)" % __version__
ATTRIBUTION = ("Aircraft interference data: GPSJam (gpsjam.org) by John Wiseman, from airplanes.live and "
               "ADS-B Exchange aircraft navigation-accuracy reports.")


def norm(data):
    return data.replace(b"\r\n", b"\n")


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if data[:2] == b"\x1f\x8b":
        data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
    return data


def wait_for_published(res, wait_min):
    want = {}
    for local, remote in PUBLISHED.items():
        with open(os.path.join(HERE, local), "rb") as f:
            want[remote] = gm.sha(norm(f.read()))
    t0 = time.time()
    while True:
        got = {}
        for remote in want:
            try:
                got[remote] = gm.sha(norm(get(RAW_MAIN % (REPO, remote))))
            except Exception as exc:                       # noqa: BLE001
                got[remote] = "error: %s" % type(exc).__name__
        if got == want:
            res["published_check"] = {"ok": True, "waited_min": round((time.time() - t0) / 60, 1), "sha256": want}
            return
        if time.time() - t0 > wait_min * 60:
            res["published_check"] = {"ok": False, "want": want, "got": got}
            raise RuntimeError("published copies do not match the ones this job runs after %d min" % wait_min)
        time.sleep(300)


def fetch_day(day):
    """(status, text or None)."""
    for attempt in range(4):
        try:
            return "ok", get(DAY_FILE % day, timeout=180).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return "missing file", None
            if exc.code in (403, 429):
                return "blocked", None
            time.sleep(min(20 * 2 ** attempt, 160))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(15)
    return "fetch failed", None


def h3_centre():
    import h3
    f = getattr(h3, "cell_to_latlng", None) or getattr(h3, "h3_to_geo")
    return lambda hx: tuple(f(hx))


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/out")
    ap.add_argument("--rows", default="", help="local study-1 rows.jsonl instead of the published one")
    ap.add_argument("--wait-min", type=float, default=150)
    ap.add_argument("--budget-min", type=float, default=200)
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_match": __version__, "gpsjam_match": gm.__version__, "ok": False, "attribution": ATTRIBUTION,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    recs = []
    try:
        wait_for_published(res, a.wait_min)
        if a.rows:
            with open(a.rows, "rb") as f:
                raw = f.read()
        else:
            raw = get(RAW_MAIN % (REPO, ROWS_PATH), timeout=300)
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        res["rows_input"] = {"path": a.rows or ROWS_PATH, "sha256": gm.sha(norm(raw)), "rows": len(rows)}
        manifest = gm.parse_manifest(get(MANIFEST).decode("utf-8", "replace"))
        res["manifest"] = {"days": len(manifest), "first": min(manifest) if manifest else None,
                           "last": max(manifest) if manifest else None,
                           "suspect": sum(1 for v in manifest.values() if v)}
        cands, needed, counts = gm.candidates(rows)
        res["candidates"] = dict(counts, logs=len(cands), days=len(needed))
        centre, cache = h3_centre(), {}
        day_cells, day_status = {}, {}
        t0, fetched = time.time(), 0
        for day in sorted(needed):
            if day not in manifest:
                day_status[day] = "no gpsjam day"
                continue
            if manifest[day]:
                day_status[day] = "suspect day"
                continue
            if time.time() - t0 > a.budget_min * 60:
                day_status[day] = "not fetched (time budget)"
                continue
            if fetched:
                time.sleep(a.delay)
            status, text = fetch_day(day)
            fetched += 1
            day_status[day] = status
            if status == "blocked":
                res["stop"] = "blocked by gpsjam.org on %s" % day
                for d in needed:
                    day_status.setdefault(d, "not fetched (blocked)")
                break
            if text is not None:
                try:
                    day_cells[day] = gm.parse_day(text, centre, needed[day], cache)
                except ValueError as exc:
                    day_status[day] = "parse error: %s" % str(exc)[:100]
            text = None
        res["fetch"] = {"files": fetched, "minutes": round((time.time() - t0) / 60, 1), "hex_cache": len(cache)}
        for r, day, cell in cands:
            st = day_status.get(day, "not fetched")
            exp = gm.exposure(day_cells[day], cell) if st == "ok" and day in day_cells else None
            recs.append(gm.record(r, day, cell, exp, st))
        res["ok"] = True
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)

    write_jsonl(os.path.join(out, "matched.jsonl"), recs)
    summary = {"gpsjam_match": gm.__version__, "attribution": ATTRIBUTION,
               "rows_input": res.get("rows_input"), "candidates": res.get("candidates")}
    try:
        summary["grade"] = gm.grade(recs)
    except Exception:                                         # noqa: BLE001
        summary["grade_error"] = traceback.format_exc()[-2000:]
    try:
        summary["described"] = gm.describe(recs)
    except Exception:                                         # noqa: BLE001
        summary["describe_error"] = traceback.format_exc()[-2000:]
    with open(os.path.join(out, "summary-matched.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
        f.write("\n")
    with open(os.path.join(out, "job-fetched.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({"res": {k: v for k, v in res.items() if k != "published_check"},
                      "grade": summary.get("grade")}, indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
