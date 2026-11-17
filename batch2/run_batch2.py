"""run_batch2.py: drone-gnss-rfi study 1, batch 2 (the top half of the same hash order) and the pooled grade.

Pre-registered in batch2/PLAN-batch2.md on 2026-11-17, before study 1's batch 1 had read any log. It runs study 1's own
published code unchanged (PLAN.md, gnsswatch.py, run_gnsswatch.py, downloaded from main); only the sample changes.

Normal mode (python run_batch2.py --out-dir /out):
1. checks that batch2/PLAN-batch2.md and batch2/run_batch2.py on main are the copies it runs (waits up to --wait-min);
2. downloads study 1's code from main, records its sha256 and, if batch 1's study1/job-fetched.json is already on
   main, checks the code is byte-identical (CRLF normalised) to what the batch-1 job ran; else it stops;
3. runs study 1's job (run_gnsswatch.main) with one change: the sample is the vehicles whose chosen log id hashes at
   or above START (the top half of study 1's order, which batch 1 walks from the bottom), in the same ascending order;
   study 1's rows.jsonl, summary.json (the grade of batch 2 alone) and job-fetched.json are written as usual;
4. if batch 1's results are on main (study1/GRADE.md and study1/rows.jsonl), writes summary-pooled.json (H1-H5 on
   batch 1 + batch 2); otherwise records that pooling is pending.
Pool-only mode (python run_batch2.py --pool-only --out-dir /out) does steps 1, 2 and 4 with batch 2's published rows
(batch2/rows.jsonl on main). Both modes write batch2-job.json.

By MV2 of Munim, Inc. (mv2@munimtech.com); written with AI agents. Apache-2.0.
"""

from __future__ import annotations

import argparse
import hashlib
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
OWN = {"PLAN-batch2.md": "batch2/PLAN-batch2.md", "run_batch2.py": "batch2/run_batch2.py"}
STUDY1_CODE = ("PLAN.md", "gnsswatch.py", "run_gnsswatch.py")
GRADE1 = "study1/GRADE.md"
ROWS1 = "study1/rows.jsonl"
FETCHED1 = "study1/job-fetched.json"
ROWS2 = "batch2/rows.jsonl"
START = "8"            # batch 2 = chosen log ids whose sha256 (lower-case hex) is >= "8", i.e. the top half
UA = "drone-gnss-rfi-batch2/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)" % __version__


def norm(data):
    return data.replace(b"\r\n", b"\n")


def sha_hex(data):
    """sha256 hex of bytes, or of str(data) encoded as UTF-8 (the same as gnsswatch.sha)."""
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


def select_batch2(sample, start=START):
    """The batch-2 sample: entries of study 1's sample (ascending sha256 of the log id) whose log-id hash is >= start,
    order kept; and counts."""
    out, below = [], 0
    for e in sample:
        if sha_hex(e["log_id"]) >= start:
            out.append(e)
        else:
            below += 1
    return out, {"start": start, "sample_vehicles": len(sample), "below_start": below, "batch2_candidates": len(out)}


def pooled(rows1, rows2):
    """Batch 1 rows plus the batch 2 rows whose log id is not in batch 1, and how many were dropped."""
    ids1 = {r.get("log_id") for r in rows1}
    extra = [r for r in rows2 if r.get("log_id") not in ids1]
    return rows1 + extra, len(rows2) - len(extra)


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


def load_study1_code(res):
    code_dir = tempfile.mkdtemp(prefix="study1-code-")
    got = {}
    for p in STUDY1_CODE:
        data = norm(get(RAW_MAIN % (REPO, p)))
        got[p] = sha_hex(data)
        with open(os.path.join(code_dir, p), "wb") as f:
            f.write(data)
    res["study1_code"] = {"sha256": got, "pinned_by_batch1_job": None, "match": None}
    sys.path.insert(0, code_dir)
    import run_gnsswatch as rg
    res["study1_versions"] = {"gnsswatch": rg.gw.__version__, "run_gnsswatch": rg.__version__}
    return rg, rg.gw


def check_against_batch1_job(res):
    """If batch 1's job-fetched.json is on main, the study-1 code used here must be the code that job ran."""
    raw = get_main(FETCHED1)
    if raw is None:
        return False
    pinned = (json.loads(raw.decode("utf-8")).get("published_check") or {}).get("sha256") or {}
    res["study1_code"]["pinned_by_batch1_job"] = pinned
    res["study1_code"]["match"] = pinned == res["study1_code"]["sha256"]
    if not res["study1_code"]["match"]:
        raise RuntimeError("study 1 code on main is not the code the batch-1 job ran")
    return True


def batch2_stream(rg, gw, keep):
    """A replacement for run_gnsswatch.stream_sample: the same index read and sample rule, then select_batch2."""
    def stream(res):
        import ijson
        req = urllib.request.Request(rg.DBINFO, headers={"User-Agent": rg.UA})
        kept, seen = [], 0
        with urllib.request.urlopen(req, timeout=900) as resp:
            for entry in ijson.items(resp, "item"):
                seen += 1
                e = gw.eligible(entry)
                if e:
                    kept.append(e)
        sample = gw.pick_sample(kept)
        chosen, info = select_batch2(sample)
        res["dbinfo"] = {"entries": seen, "eligible_logs": len(kept), "vehicles": len(sample)}
        res["batch2_select"] = info
        keep.update(info)
        return chosen
    return stream


def pool_summary(rg, gw, rows1, rows2):
    pool, dropped = pooled(rows1, rows2)
    s = {"gnsswatch": gw.__version__, "batch1_rows": len(rows1), "batch2_rows": len(rows2),
         "overlap_dropped": dropped, "pooled_rows": len(pool), "grade_pooled": gw.grade(pool)}
    if dropped:
        ids1 = {r.get("log_id") for r in rows1}
        s["grade_batch2_without_overlap"] = gw.grade([r for r in rows2 if r.get("log_id") not in ids1])
    try:
        s["described_pooled"] = rg.describe(pool)
    except Exception:                                         # noqa: BLE001
        s["describe_error"] = traceback.format_exc()[-2000:]
    return s


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/out")
    ap.add_argument("--pool-only", action="store_true")
    ap.add_argument("--max-logs", type=int, default=2000)
    ap.add_argument("--budget-min", type=float, default=240)
    ap.add_argument("--wait-min", type=float, default=150)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_batch2": __version__, "mode": "pool-only" if a.pool_only else "batch2", "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        own_published(res, a.wait_min)
        rg, gw = load_study1_code(res)
        check_against_batch1_job(res)
        if a.pool_only:
            raw2 = get_main(ROWS2, timeout=300)
            if raw2 is None:
                raise RuntimeError("batch 2 rows (%s) are not on main" % ROWS2)
            rows2 = parse_rows(raw2)
            res["batch2_rows"] = {"path": ROWS2, "sha256": sha_hex(norm(raw2)), "rows": len(rows2)}
        else:
            select = {}
            rg.stream_sample = batch2_stream(rg, gw, select)
            code = rg.main(["--out-dir", out, "--max-logs", str(a.max_logs), "--budget-min", str(a.budget_min),
                            "--wait-min", "10", "--read-bytes", "5000000"])
            res["batch2_job_exit"] = code
            res["batch2_select"] = select
            if code != 0:
                raise RuntimeError("the batch-2 run of study 1's job did not finish cleanly (see job-fetched.json)")
            with open(os.path.join(out, "rows.jsonl"), "rb") as f:
                rows2 = parse_rows(f.read())
        has_grade1 = get_main(GRADE1) is not None
        raw1 = get_main(ROWS1, timeout=300) if has_grade1 else None
        if raw1 is None:
            res["pooled"] = "pending: batch 1 results (study1/GRADE.md, study1/rows.jsonl) are not on main yet"
            if a.pool_only:
                raise RuntimeError(res["pooled"])
        else:
            if res["study1_code"]["pinned_by_batch1_job"] is None and not check_against_batch1_job(res):
                raise RuntimeError("study1/job-fetched.json is missing next to batch 1's rows")
            rows1 = parse_rows(raw1)
            res["batch1_rows"] = {"path": ROWS1, "sha256": sha_hex(norm(raw1)), "rows": len(rows1)}
            s = pool_summary(rg, gw, rows1, rows2)
            write_json(os.path.join(out, "summary-pooled.json"), s)
            res["pooled"] = "written"
            print(json.dumps({"grade_pooled": s["grade_pooled"]}, indent=1)[:6000], flush=True)
        res["ok"] = True
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)
    write_json(os.path.join(out, "batch2-job.json"), res)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
