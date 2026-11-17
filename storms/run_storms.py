"""run_storms.py: drone-gnss-rfi study S1, GNSS on real drones during the severe geomagnetic storms of 2023-2026.

Pre-registered in storms/PLAN-storms.md. Before it reads any log it
1. checks that storms/PLAN-storms.md, storms/stormwatch.py and this file on the repo's main branch are the copies it
   runs (CRLF normalised; waits up to --wait-min minutes for the publish, else stops);
2. downloads study 1's reader and downloader (gnsswatch.py, run_gnsswatch.py) from main and records their sha256;
3. downloads GFZ Niemegk's Kp file (CC BY 4.0) and builds the storm events (Kp 8- or more) and their placebos;
then
4. streams Flight Review's public log index (review.px4.io/dbinfo) once, keeping the logs that pass study 1's
   eligibility rule (gnsswatch.eligible) and were uploaded inside an event's storm-side or control-side window;
5. real stage (strongest event first, at most --real-min minutes): for each vehicle with logs on both sides, reads
   up to 3 storm-side logs (sha256 order) until one started at storm time, then up to 3 control-side logs, and pairs
   the storm flight with the quiet control flight closest in UTC time of day; one pair per vehicle;
6. placebo stage (the rest of --budget-min): the same on the placebo spans, where both flights must be quiet;
7. grades (stormwatch.grade) and describes.
Each log read is at most the first --read-bytes bytes, decoded only for the GNSS topics; bytes are dropped at once.
Requests start at least 6 s apart (at most 10 a minute, the rate PX4's download_logs.py documents).
Writes rows.jsonl (every log read), pairs.jsonl, placebo-pairs.jsonl, summary.json and job-fetched.json.

    python run_storms.py --out-dir /out

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stormwatch as sw  # noqa: E402

__version__ = "0.1.0"
REPO = "munimtechnologies/drone-gnss-rfi"
RAW_MAIN = "https://raw.githubusercontent.com/%s/main/%s"
OWN = {"PLAN-storms.md": "storms/PLAN-storms.md", "stormwatch.py": "storms/stormwatch.py",
       "run_storms.py": "storms/run_storms.py"}
STUDY1_CODE = ("gnsswatch.py", "run_gnsswatch.py")
KP_URL = "https://kp.gfz.de/app/files/Kp_ap_since_1932.txt"
DBINFO = "https://review.px4.io/dbinfo"
UA = "drone-gnss-rfi-storms/%s (MV2 of Munim, Inc.; mv2@munimtech.com; research on public logs)" % __version__
MIN_GAP_S = 6.0
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
            want[remote] = sw.sha(norm(f.read()))
    t0 = time.time()
    while True:
        got = {}
        for remote in want:
            try:
                got[remote] = sw.sha(norm(get(RAW_MAIN % (REPO, remote))))
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
        got[p] = sw.sha(data)
        with open(os.path.join(code_dir, p), "wb") as f:
            f.write(data)
    sys.path.insert(0, code_dir)
    import run_gnsswatch as rg
    res["study1_code"] = {"sha256": got, "gnsswatch": rg.gw.__version__, "run_gnsswatch": rg.__version__}
    rg.UA = UA
    return rg, rg.gw


def load_kp(res):
    raw = get(KP_URL, timeout=600)
    kp = sw.parse_kp(raw.decode("utf-8", "replace"))
    last = max(kp) if kp else None
    res["kp"] = {"url": KP_URL, "bytes": len(raw), "sha256": sw.sha(raw), "intervals_kept": len(kp),
                 "last_interval": sw.iso_hour(last) if last is not None else None,
                 "source": "GFZ Helmholtz Centre for Geosciences, Geomagnetic Observatory Niemegk (CC BY 4.0); "
                           "Matzka et al. 2021, Space Weather, doi:10.1029/2020SW002641"}
    return kp


def stream_index(res, gw, evs):
    import ijson
    days = set()
    for ev in evs:
        for lo, hi in sw.upload_windows(ev).values():
            days.update(sw.window_days(lo, hi))
    req = urllib.request.Request(DBINFO, headers={"User-Agent": UA})
    kept, seen, eligible = [], 0, 0
    with urllib.request.urlopen(req, timeout=900) as resp:
        for entry in ijson.items(resp, "item"):
            seen += 1
            e = gw.eligible(entry)
            if e:
                eligible += 1
                if e["date"] in days:
                    kept.append(e)
    res["dbinfo"] = {"entries": seen, "eligible_logs": eligible, "in_windows": len(kept), "window_days": len(days)}
    return kept


def check_with_pyulog(data, key, rows):
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

    def __init__(self, rg, gw, read_bytes, max_reads, out_dir):
        self.rg, self.gw, self.read_bytes, self.max_reads, self.out = rg, gw, read_bytes, max_reads, out_dir
        self.cache, self.rows, self.v1 = {}, [], []
        self.last, self.deadline = 0.0, None

    def read(self, e, ev, side):
        lid = e["log_id"]
        if lid in self.cache:
            return self.cache[lid]
        if len(self.rows) >= self.max_reads:
            raise Stop("read cap")
        if self.deadline is not None and time.time() > self.deadline:
            raise Stop("time budget")
        wait = MIN_GAP_S - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        row = {"log_id": lid, "date": e["date"], "rel": e["rel"], "hw": e["hw"], "mav": e["mav"], "dur": e["dur"],
               "event": ev["id"], "side": side}
        try:
            status, data, complete = self.rg.fetch_head(lid, self.read_bytes)
        except self.rg.Blocked as exc:
            raise Stop("blocked: %s" % exc)
        row.update(status=status, complete=complete)
        m = None
        if data is not None:
            row["bytes"] = len(data)
            try:
                topics, info = self.gw.read_topics(data, sw.wanted())
                row.update(truncated=info["truncated"], resyncs=info["resyncs"])
                key = sw.choose(topics)
                if key is None:
                    row["status"] = "no GNSS topic"
                else:
                    rows = topics[key]["rows"]
                    m = sw.measure(rows)
                    row.update(topic="%s/%d" % key, m=sw.public(m))
                    if complete and len(self.v1) < V1_LOGS and len(rows) >= 10:
                        try:
                            self.v1.append(dict(check_with_pyulog(data, key, rows), log_id=lid))
                        except Exception as exc:          # noqa: BLE001
                            self.v1.append({"log_id": lid, "match": False,
                                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
            except self.gw.NotULog:
                row["status"] = "not a ULog"
            except Exception as exc:                      # noqa: BLE001
                row["status"] = "parse error"
                row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        data = None
        self.cache[lid] = m
        self.rows.append(row)
        if len(self.rows) % 25 == 0:
            write_jsonl(os.path.join(self.out, "rows.jsonl"), self.rows)
            gc.collect()
        return m


def run_stage(evs, entries, kp, reader, sink, table):
    """Pairs for each event in order; returns the stop reason."""
    paired = set()
    for ev in evs:
        s_by, c_by, vehicles = sw.candidates(entries, ev)
        w = sw.upload_windows(ev)
        row = {"id": ev["id"], "kind": ev["kind"], "max_kp": ev["max_kp"], "severe_intervals": ev["severe_intervals"],
               "start": sw.iso_hour(ev["start"]), "end": sw.iso_hour(ev["end"]), "storm_upload": list(w["storm"]),
               "control_upload": list(w["control"]), "vehicles_both_sides": len(vehicles), "vehicles_tried": 0,
               "storm_reads": 0, "storm_accepted": 0, "control_reads": 0, "pairs": 0, "cut": False}
        if ev["kind"] == "placebo":
            row.update(of=ev["of"], shift_d=ev["shift_d"])
        table.append(row)
        try:
            for v in vehicles:
                if v in paired:
                    continue
                row["vehicles_tried"] += 1
                storm = None
                for _h, e in s_by[v][:sw.MAX_TRIES]:
                    m = reader.read(e, ev, "storm")
                    row["storm_reads"] += 1
                    if sw.usable(m) and sw.flight_ok("storm", ev, m["start_utc"], kp):
                        storm = (e["log_id"], m)
                        break
                if storm is None:
                    continue
                row["storm_accepted"] += 1
                cands = []
                for _h, e in c_by[v][:sw.MAX_TRIES]:
                    m = reader.read(e, ev, "control")
                    row["control_reads"] += 1
                    if sw.usable(m) and sw.flight_ok("control", ev, m["start_utc"], kp):
                        cands.append((e["log_id"], m))
                        if sw.hour_gap(storm[1]["start_utc"], m["start_utc"]) <= 2.0:
                            break
                pick = sw.pick_control(storm[1]["start_utc"], cands)
                if pick is None:
                    continue
                sink.append(sw.make_pair(ev, v, storm, pick, kp))
                paired.add(v)
                row["pairs"] += 1
        except Stop as exc:
            row["cut"] = True
            return str(exc)
    return "done"


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
    ap.add_argument("--budget-min", type=float, default=240)
    ap.add_argument("--real-min", type=float, default=180)
    ap.add_argument("--max-reads", type=int, default=2400)
    ap.add_argument("--read-bytes", type=int, default=2000000)
    ap.add_argument("--wait-min", type=float, default=150)
    a = ap.parse_args(argv)
    out = a.out_dir
    os.makedirs(out, exist_ok=True)
    res = {"run_storms": __version__, "stormwatch": sw.__version__, "ok": False,
           "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "settings": {"budget_min": a.budget_min, "real_min": a.real_min, "max_reads": a.max_reads,
                        "read_bytes": a.read_bytes, "min_gap_s": MIN_GAP_S}}
    reader, pairs, placebo_pairs, table = None, [], [], []
    try:
        own_published(res, a.wait_min)
        rg, gw = load_study1_code(res)
        kp = load_kp(res)
        real = sw.events(kp)
        plac = [p for p in (sw.placebo(e, kp) for e in real) if p]
        res["events"] = {"real": len(real), "placebo": len(plac),
                         "all": [{"id": e["id"], "kind": e["kind"], "max_kp": e["max_kp"],
                                  "start": sw.iso_hour(e["start"]), "end": sw.iso_hour(e["end"])}
                                 for e in real + plac]}
        entries = stream_index(res, gw, real + plac)
        gc.collect()
        reader = Reader(rg, gw, a.read_bytes, a.max_reads, out)
        t0 = time.time()
        for stage, evs, minutes, sink in (("real", real, min(a.real_min, a.budget_min), pairs),
                                          ("placebo", plac, a.budget_min, placebo_pairs)):
            reader.deadline = t0 + minutes * 60
            stop = run_stage(evs, entries, kp, reader, sink, table)
            res["stop_" + stage] = stop
            if stop.startswith("blocked") or stop == "read cap":
                break
        res.update(reads=len(reader.rows), pairs=len(pairs), placebo_pairs=len(placebo_pairs),
                   minutes=round((time.time() - t0) / 60, 1))
        res["ok"] = True
    except Exception:                                         # noqa: BLE001
        res["error"] = traceback.format_exc()[-3000:]
        print(res["error"], flush=True)

    rows = reader.rows if reader else []
    v1 = reader.v1 if reader else []
    write_jsonl(os.path.join(out, "rows.jsonl"), rows)
    write_jsonl(os.path.join(out, "pairs.jsonl"), pairs)
    write_jsonl(os.path.join(out, "placebo-pairs.jsonl"), placebo_pairs)
    summary = {"stormwatch": sw.__version__, "events": table, "v1_pyulog": v1,
               "v1_result": ("held" if all(x.get("match") for x in v1) else "failed") if len(v1) >= V1_LOGS
               else ("failed" if any(not x.get("match") for x in v1) else "too few")}
    try:
        summary["grade"] = sw.grade(pairs, placebo_pairs)
        summary["described"] = sw.describe(pairs)
        summary["described_placebo"] = sw.describe(placebo_pairs)
    except Exception:                                         # noqa: BLE001
        summary["grade_error"] = traceback.format_exc()[-2000:]
    write_json(os.path.join(out, "summary.json"), summary)
    write_json(os.path.join(out, "job-fetched.json"), res)
    print(json.dumps({"res": {k: v for k, v in res.items() if k != "published_check"},
                      "grade": summary.get("grade")}, indent=1)[:6000], flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
