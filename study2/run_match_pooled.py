"""run_match_pooled.py: drone-gnss-rfi study 2 (same-day GPSJam match) on the pooled study-1 logs.

Pre-registered in batch2/PLAN-batch2.md ("Study 2 on the pooled logs"); mechanics in study2/PLAN-matched-pooled.md.
1. checks that PLAN-matched-pooled.md and this file on the repo's main branch (study2/) are the copies it runs;
2. reads study1/rows.jsonl and batch2/rows.jsonl from main (stops if either is missing) and pools them
   (batch 1 + the batch-2 rows whose log id is not in batch 1);
3. downloads study 2's frozen code from main and runs run_match.py unchanged on the pooled rows (--rows);
4. grades M1-M4 on the batch-1 logs, the batch-2 logs alone and all pooled logs from the same matched records, and
   applies the headline rule of PLAN-batch2.
Writes pooled/ (run_match.py's own outputs), summary-pooled-matched.json and job-fetched-pooled.json.

    python run_match_pooled.py --out-dir /out

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

__version__ = "0.1.0"
REPO = "munimtechnologies/drone-gnss-rfi"
RAW_MAIN = "https://raw.githubusercontent.com/%s/main/%s"
HERE = os.path.dirname(os.path.abspath(__file__))
OWN = {"PLAN-matched-pooled.md": "study2/PLAN-matched-pooled.md", "run_match_pooled.py": "study2/run_match_pooled.py"}
FROZEN = ("study2/PLAN-matched.md", "study2/gpsjam_match.py", "study2/run_match.py")
ROWS1 = "study1/rows.jsonl"
ROWS2 = "batch2/rows.jsonl"
STUDY2_BATCH1 = "study2/summary-matched.json"
PREDICTIONS = ("M1_spoof_exposed_vs_not", "M2_spoof_dose", "M3_jam_disarmed_exposed_vs_not",
               "M4_manipulated_exposed_vs_not")
UA = "drone-gnss-rfi-pooled/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research matching drone logs to GPSJam)" % (
    __version__)


def norm(data):
    return data.replace(b"\r\n", b"\n")


def sha_hex(data):
    """sha256 hex of bytes, or of str(data) encoded as UTF-8."""
    if not isinstance(data, bytes):
        data = str(data).encode()
    return hashlib.sha256(data).hexdigest()


def parse_rows(raw):
    """JSON lines (bytes) -> list of dicts; blank lines skipped."""
    rows = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def pooled(rows1, rows2):
    """Batch 1 rows plus the batch 2 rows whose log id is not in batch 1, and how many were dropped
    (the rule of batch2/run_batch2.py)."""
    ids1 = {r.get("log_id") for r in rows1}
    extra = [r for r in rows2 if r.get("log_id") not in ids1]
    return rows1 + extra, len(rows2) - len(extra)


def split_records(recs, ids1, ids2):
    """Matched records -> (batch-1 records, batch-2 records) by log id; records in neither set are left out."""
    return ([r for r in recs if r.get("log_id") in ids1],
            [r for r in recs if r.get("log_id") in ids2 and r.get("log_id") not in ids1])


def headline(r1, r2, rp):
    """PLAN-batch2's rule for one prediction: r1 = batch-1 result, r2 = batch 2 alone, rp = pooled."""
    out = {"batch1": r1, "batch2_alone": r2, "pooled": rp}
    if r1 in ("held", "failed"):
        out.update(headline=r1, source="batch 1", replicated=(r2 == r1))
    elif r1 == "too few":
        out.update(headline=rp if rp else "pending", source="pooled", replicated=None)
    else:
        out.update(headline="pending", source=None, replicated=None)
    return out


def result_of(grade, key):
    g = (grade or {}).get(key)
    return g.get("result") if isinstance(g, dict) else None


def headlines(g1, g2, gp):
    return {k: headline(result_of(g1, k), result_of(g2, k), result_of(gp, k)) for k in PREDICTIONS}


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def get_main(path, timeout=120):
    """A file from the repo's main branch, or None if it is not there (404)."""
    try:
        return get(RAW_MAIN % (REPO, path), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def own_published(res, wait_min):
    want = {}
    for local, remote in OWN.items():
        with open(os.path.join(HERE, local), "rb") as f:
            want[remote] = sha_hex(norm(f.read()))
    t0 = time.time()
    while True:
        got = {}
        for remote in want:
            try:
                got[remote] = sha_hex(norm(get(RAW_MAIN % (REPO, remote))))
            except Exception as exc:                       # noqa: BLE001
                got[remote] = "error: %s" % type(exc).__name__
        if got == want:
            res["published_check"] = {"ok": True, "waited_min": round((time.time() - t0) / 60, 1), "sha256": want}
            return
        if time.time() - t0 > wait_min * 60:
            res["published_check"] = {"ok": False, "want": want, "got": got}
            raise RuntimeError("published copies do not match the ones this job runs after %d min" % wait_min)
        time.sleep(300)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/out")
    ap.add_argument("--wait-min", type=float, default=150)
    ap.add_argument("--budget-min", type=float, default=300)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_match_pooled": __version__, "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    summary = {"run_match_pooled": __version__}
    try:
        own_published(res, a.wait_min)
        raw1, raw2 = get_main(ROWS1, timeout=300), get_main(ROWS2, timeout=300)
        missing = [p for p, r in ((ROWS1, raw1), (ROWS2, raw2)) if r is None]
        if missing:
            raise RuntimeError("not on main yet: %s" % ", ".join(missing))
        rows1, rows2 = parse_rows(raw1), parse_rows(raw2)
        pool, dropped = pooled(rows1, rows2)
        ids1 = {r.get("log_id") for r in rows1}
        ids2 = {r.get("log_id") for r in pool[len(rows1):]}
        res["inputs"] = {ROWS1: {"sha256": sha_hex(norm(raw1)), "rows": len(rows1)},
                         ROWS2: {"sha256": sha_hex(norm(raw2)), "rows": len(rows2)},
                         "overlap_dropped": dropped, "pooled_rows": len(pool)}
        tmp = tempfile.mkdtemp(prefix="study2-frozen-")
        frozen = {}
        for path in FROZEN:
            data = get_main(path)
            if data is None:
                raise RuntimeError("frozen study-2 file missing on main: %s" % path)
            data = norm(data)
            frozen[path] = sha_hex(data)
            with open(os.path.join(tmp, os.path.basename(path)), "wb") as f:
                f.write(data)
        res["frozen_study2"] = frozen
        rows_path = os.path.join(tmp, "pooled-rows.jsonl")
        with open(rows_path, "w", encoding="utf-8", newline="\n") as f:
            for r in pool:
                f.write(json.dumps(r) + "\n")
        sys.path.insert(0, tmp)
        gm = importlib.import_module("gpsjam_match")
        rm = importlib.import_module("run_match")
        res["versions"] = {"gpsjam_match": gm.__version__, "run_match": rm.__version__}
        pooled_out = os.path.join(out, "pooled")
        rc = rm.main(["--out-dir", pooled_out, "--rows", rows_path, "--wait-min", "30",
                      "--budget-min", str(a.budget_min)])
        res["run_match_exit"] = rc
        with open(os.path.join(pooled_out, "matched.jsonl"), "rb") as f:
            recs = parse_rows(f.read())
        b1, b2 = split_records(recs, ids1, ids2)
        g1, g2, gp = gm.grade(b1), gm.grade(b2), gm.grade(recs)
        raw_s1 = get_main(STUDY2_BATCH1)
        if raw_s1 is not None:
            g1_used = json.loads(raw_s1.decode("utf-8")).get("grade")
            source = {"path": STUDY2_BATCH1, "sha256": sha_hex(norm(raw_s1))}
        else:
            g1_used = g1
            source = {"path": None, "note": "study 2's batch-1 summary was not on main; the batch-1 subset of this job "
                                            "stands in"}
        summary.update(inputs=res["inputs"], frozen_study2=frozen, matched_records=len(recs),
                       batch_records={"batch1": len(b1), "batch2": len(b2)}, batch1_source=source,
                       grade_batch1_subset=g1, grade_batch1_used=g1_used, grade_batch2_alone=g2, grade_pooled=gp,
                       headline=headlines(g1_used, g2, gp))
        try:
            summary["described_batch2"] = gm.describe(b2)
        except Exception:                                     # noqa: BLE001
            summary["describe_error"] = traceback.format_exc()[-2000:]
        summary["attribution"] = getattr(rm, "ATTRIBUTION", "")
        res["ok"] = rc == 0
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)
    write_json(os.path.join(out, "summary-pooled-matched.json"), summary)
    write_json(os.path.join(out, "job-fetched-pooled.json"), res)
    print(json.dumps({"ok": res["ok"], "headline": summary.get("headline")}, indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
