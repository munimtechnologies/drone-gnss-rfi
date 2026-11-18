"""run_scint.py: drone-gnss-rfi study S2, GNSS on real drones after sunset at low magnetic latitudes.

Pre-registered in scint/PLAN-scint.md. Before it reads any log it
1. checks that scint/PLAN-scint.md, scint/scintwatch.py and this file on the repo's main branch are the copies it
   runs (CRLF normalised; waits up to --wait-min minutes for the publish, else stops);
2. downloads study 1's reader and downloader (gnsswatch.py, run_gnsswatch.py) and study S1's measures
   (storms/stormwatch.py) from main, unchanged, and records their sha256;
then
3. streams Flight Review's public log index (review.px4.io/dbinfo) once, keeping logs that pass study 1's
   eligibility rule (gnsswatch.eligible) and were uploaded in February-April or September-November;
4. survey: for each vehicle with at least 2 such logs (salted sha256 order, scintwatch.survey_order) reads its first
   log, measures it (stormwatch.measure) and classifies it (band by geomagnetic latitude, time class by local solar
   time: scintwatch.classify);
5. pairing: when a survey log is a night flight in the low band (or in the middle band, while middle-band pairings
   tried do not exceed low-band ones by more than 5), reads up to 3 more logs of that vehicle until one is a day
   flight in the same band, and keeps the night/day pair;
6. stops at --budget-min minutes or --max-reads logs read, then grades (scintwatch.grade) and describes.
Each log read is at most the first --read-bytes bytes, decoded only for the GNSS topics; bytes are dropped at once.
Requests start at least 7.5 s apart (8 a minute; PX4's download_logs.py documents 10 a minute).
Writes rows.jsonl (every log read), survey.jsonl, pairs.jsonl, summary.json and job-fetched.json.

    python run_scint.py --out-dir /out

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
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scintwatch as scw  # noqa: E402

__version__ = "0.1.0"
REPO = "munimtechnologies/drone-gnss-rfi"
RAW_MAIN = "https://raw.githubusercontent.com/%s/main/%s"
OWN = {"PLAN-scint.md": "scint/PLAN-scint.md", "scintwatch.py": "scint/scintwatch.py",
       "run_scint.py": "scint/run_scint.py"}
FROZEN = {"gnsswatch.py": "gnsswatch.py", "run_gnsswatch.py": "run_gnsswatch.py",
          "stormwatch.py": "storms/stormwatch.py"}
DBINFO = "https://review.px4.io/dbinfo"
UA = "drone-gnss-rfi-scint/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)" % __version__
MIN_GAP_S = 7.5
V1_LOGS = 3


class Stop(Exception):
    pass


def norm(data):
    return data.replace(b"\r\n", b"\n")


def get(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def own_published(res, wait_min):
    want = {}
    for local, remote in OWN.items():
        with open(os.path.join(HERE, local), "rb") as f:
            want[remote] = scw.sha(norm(f.read()))
    t0 = time.time()
    while True:
        got = {}
        for remote in want:
            try:
                got[remote] = scw.sha(norm(get(RAW_MAIN % (REPO, remote))))
            except Exception as exc:                       # noqa: BLE001
                got[remote] = "error: %s" % type(exc).__name__
        if got == want:
            res["published_check"] = {"ok": True, "waited_min": round((time.time() - t0) / 60, 1), "sha256": want}
            return
        if time.time() - t0 > wait_min * 60:
            res["published_check"] = {"ok": False, "want": want, "got": got}
            raise RuntimeError("published copies do not match the ones this job runs after %d min" % wait_min)
        time.sleep(300)


def load_frozen(res):
    code_dir = tempfile.mkdtemp(prefix="frozen-code-")
    got = {}
    for local, remote in FROZEN.items():
        data = norm(get(RAW_MAIN % (REPO, remote)))
        got[remote] = scw.sha(data)
        with open(os.path.join(code_dir, local), "wb") as f:
            f.write(data)
    sys.path.insert(0, code_dir)
    import run_gnsswatch as rg
    import stormwatch as sw
    res["frozen_code"] = {"sha256": got, "gnsswatch": rg.gw.__version__, "run_gnsswatch": rg.__version__,
                          "stormwatch": sw.__version__}
    rg.UA = UA
    return rg, rg.gw, sw


def stream_index(res, gw):
    import ijson
    req = urllib.request.Request(DBINFO, headers={"User-Agent": UA})
    kept, seen, eligible = [], 0, 0
    with urllib.request.urlopen(req, timeout=900) as resp:
        for entry in ijson.items(resp, "item"):
            seen += 1
            e = gw.eligible(entry)
            if e:
                eligible += 1
                if scw.in_season(e["date"]):
                    kept.append(e)
    res["dbinfo"] = {"entries": seen, "eligible_logs": eligible, "in_season": len(kept)}
    return kept


def check_with_pyulog(data, key, rows, sw):
    """V1: pyulog reads the same number of rows of the measured topic and the same fix types."""
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
                fix = [int(x) for x in d.data["fix_type"]] if "fix_type" in d.data else None
                ours = [r[sw._I["fix_type"]] for r in rows]
                return {"pyulog_rows": n, "rows": len(rows), "fix_compared": fix is not None,
                        "match": n == len(rows) and (fix is None or fix == ours)}
        return {"match": False, "error": "topic not found by pyulog"}
    finally:
        os.remove(path)


class Reader:
    """Reads logs politely (requests at least MIN_GAP_S apart), once each, and keeps one row per log read."""

    def __init__(self, rg, gw, sw, read_bytes, max_reads, deadline, out_dir):
        self.rg, self.gw, self.sw = rg, gw, sw
        self.read_bytes, self.max_reads, self.deadline, self.out = read_bytes, max_reads, deadline, out_dir
        self.rows, self.v1 = [], []
        self.last = 0.0

    def read(self, e, role):
        """(measures or None, class or None) of one log."""
        if len(self.rows) >= self.max_reads:
            raise Stop("read cap")
        if time.time() > self.deadline:
            raise Stop("time budget")
        wait = MIN_GAP_S - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        lid = e["log_id"]
        row = {"log_id": lid, "date": e["date"], "rel": e["rel"], "hw": e["hw"], "mav": e["mav"], "dur": e["dur"],
               "role": role}
        try:
            status, data, complete = self.rg.fetch_head(lid, self.read_bytes)
        except self.rg.Blocked as exc:
            raise Stop("blocked: %s" % exc)
        row.update(status=status, complete=complete)
        m = c = None
        if data is not None:
            row["bytes"] = len(data)
            try:
                topics, info = self.gw.read_topics(data, self.sw.wanted())
                row.update(truncated=info["truncated"], resyncs=info["resyncs"])
                key = self.sw.choose(topics)
                if key is None:
                    row["status"] = "no GNSS topic"
                else:
                    rows = topics[key]["rows"]
                    m = self.sw.measure(rows)
                    c = scw.classify(m)
                    row.update(topic="%s/%d" % key, m=self.sw.public(m), c=scw.public_class(c))
                    if complete and len(self.v1) < V1_LOGS and len(rows) >= 10:
                        try:
                            self.v1.append(dict(check_with_pyulog(data, key, rows, self.sw), log_id=lid))
                        except Exception as exc:          # noqa: BLE001
                            self.v1.append({"log_id": lid, "match": False,
                                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
            except self.gw.NotULog:
                row["status"] = "not a ULog"
            except Exception as exc:                      # noqa: BLE001
                row["status"] = "parse error"
                row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        data = None
        self.rows.append(row)
        if len(self.rows) % 25 == 0:
            write_jsonl(os.path.join(self.out, "rows.jsonl"), self.rows)
            gc.collect()
        return m, c


def run(order, reader, sw, survey, pairs, tally):
    """Survey and pairing in the vehicles' order; returns the stop reason."""
    low_tries = mid_tries = 0
    try:
        for v, logs in order:
            e = logs[0][1]
            m, c = reader.read(e, "survey")
            tally["survey_reads"] += 1
            if c is None:
                continue
            survey.append({"v": v, "log_id": e["log_id"], "date": e["date"], "band": c["band"], "cls": c["cls"],
                           "lst": c["lst"], "m": m})
            if c["cls"] != "night" or c["band"] not in ("low", "mid"):
                continue
            if c["band"] == "mid" and mid_tries >= low_tries + scw.MID_SLACK:
                tally["mid_night_not_paired_slack"] += 1
                continue
            if c["band"] == "low":
                low_tries += 1
            else:
                mid_tries += 1
            for _h, e2 in logs[1:1 + scw.MAX_PAIR_READS]:
                m2, c2 = reader.read(e2, "pair")
                tally["pair_reads"] += 1
                if c2 is not None and c2["cls"] == "day" and c2["band"] == c["band"]:
                    pairs.append({"v": v, "band": c["band"],
                                  "night_log": e["log_id"], "day_log": e2["log_id"],
                                  "night": sw.public(m), "day": sw.public(m2),
                                  "night_c": scw.public_class(c), "day_c": scw.public_class(c2)})
                    break
        return "sample exhausted"
    except Stop as exc:
        return str(exc)
    finally:
        tally["low_pairings_tried"], tally["mid_pairings_tried"] = low_tries, mid_tries


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def write_json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/out")
    ap.add_argument("--budget-min", type=float, default=540)
    ap.add_argument("--max-reads", type=int, default=4300)
    ap.add_argument("--read-bytes", type=int, default=2000000)
    ap.add_argument("--wait-min", type=float, default=150)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_scint": __version__, "scintwatch": scw.__version__, "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "settings": {"budget_min": a.budget_min, "max_reads": a.max_reads, "read_bytes": a.read_bytes,
                        "min_gap_s": MIN_GAP_S}}
    reader, survey, pairs = None, [], []
    tally = {"survey_reads": 0, "pair_reads": 0, "mid_night_not_paired_slack": 0}
    try:
        own_published(res, a.wait_min)
        rg, gw, sw = load_frozen(res)
        entries = stream_index(res, gw)
        order = scw.survey_order(entries)
        res["vehicles_in_order"] = len(order)
        entries = None
        gc.collect()
        t0 = time.time()
        reader = Reader(rg, gw, sw, a.read_bytes, a.max_reads, t0 + a.budget_min * 60, out)
        res["stop"] = run(order, reader, sw, survey, pairs, tally)
        res.update(reads=len(reader.rows), survey_usable=len(survey), pairs=len(pairs), tally=tally,
                   minutes=round((time.time() - t0) / 60, 1))
        res["ok"] = True
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)

    rows = reader.rows if reader else []
    v1 = reader.v1 if reader else []
    write_jsonl(os.path.join(out, "rows.jsonl"), rows)
    pub_survey = [{"v": r["v"], "log_id": r["log_id"], "date": r["date"], "band": r["band"], "cls": r["cls"],
                   "lst_half_hour": scw.math.floor(r["lst"] * 2) / 2.0,
                   "m": {k: x for k, x in r["m"].items() if k != "start_utc"}} for r in survey]
    write_jsonl(os.path.join(out, "survey.jsonl"), pub_survey)
    write_jsonl(os.path.join(out, "pairs.jsonl"), pairs)
    summary = {"scintwatch": scw.__version__, "v1_pyulog": v1,
               "v1_result": ("held" if all(x.get("match") for x in v1) else "failed") if len(v1) >= V1_LOGS
               else ("failed" if any(not x.get("match") for x in v1) else "too few")}
    try:
        summary["grade"] = scw.grade(pairs, survey)
        summary["described"] = scw.describe(survey, pairs)
    except Exception:                                         # noqa: BLE001
        summary["grade_error"] = traceback.format_exc()[-2000:]
    write_json(os.path.join(out, "summary.json"), summary)
    write_json(os.path.join(out, "job-fetched.json"), res)
    print(json.dumps({"res": {k: v for k, v in res.items() if k != "published_check"},
                      "grade": summary.get("grade")}, indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
