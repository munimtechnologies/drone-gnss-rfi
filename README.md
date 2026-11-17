# drone-gnss-rfi

Do civilian drones near the ground see the GNSS spoofing and jamming that airliners report over the Baltic, the eastern
Mediterranean, the Black Sea and the Persian Gulf?

Interference maps today come from airliners at cruise altitude (ADS-B) and from a few fixed ground monitors. This project
measures it from the drones' own GNSS receivers instead, using the spoofing and jamming states, jamming indicator, positions
and receiver clock recorded in public PX4 flight logs on [PX4 Flight Review](https://review.px4.io).

By MV2 of Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name. Code Apache-2.0; text and data CC BY 4.0.

## Study 1 (pre-registered 2026-11-17)

- [`PLAN.md`](PLAN.md): the question, the sample rule, the measures, five predictions with their thresholds, and the limits,
  all fixed before any log was read.
- `gnsswatch.py`: a small ULog reader (Python standard library only) that decodes just the GNSS and vehicle-status topics and
  works on the first few MB of a log; the per-log measures (spoofing and jamming states, jamming indicator split by arming,
  position "teleports", receiver-clock jumps, the 1-degree cell of the first good fix); the sample rule; the grading.
- `run_gnsswatch.py`: the job. It checks that the plan and code here match the copies it runs before reading any log.
- `test_gnsswatch.py`: unit tests on synthetic logs (`python -m unittest -v test_gnsswatch`).
- [`NOTE-2026-11-17-dates.md`](NOTE-2026-11-17-dates.md): Flight Review's log date is the upload date, not the flight
  date (does not affect H1-H5).

Results will be added in `study1/` (`GRADE.md`, `rows.jsonl`, `summary.json`). Positions are published only as 1-degree
cells and region names.

### Batch 2 (pre-registered 2026-11-17, before batch 1 had read any log)

Up to 2,000 more vehicles from the top half of the same hash order (batch 1 walks it from the bottom), read with
study 1's code unchanged, plus a pooled grade, so that the conflict-adjacent regions get enough logs. Which grade answers each prediction was fixed in advance.

- [`batch2/PLAN-batch2.md`](batch2/PLAN-batch2.md): the batch-2 sample, the two grades and the headline rule.
- `batch2/run_batch2.py`, `batch2/test_batch2.py`.

## Study 2 (pre-registered 2026-11-17, before study 1 had results)

Do drones report interference on the days and places where aircraft see it? Each study-1 log is matched to the share of
aircraft reporting degraded navigation on the same UTC day (from the drone's own receiver clock) in the 3 x 3 block of
1-degree cells around it, using [GPSJam](https://gpsjam.org)'s public daily files.

- [`study2/PLAN-matched.md`](study2/PLAN-matched.md): input, exposure measure, four predictions (M1-M4), limits.
- `study2/gpsjam_match.py`, `study2/run_match.py`, `study2/test_gpsjam_match.py`.

Aircraft interference data: GPSJam (gpsjam.org) by John Wiseman, from airplanes.live and ADS-B Exchange. GPSJam's files
are not copied here; only per-log sums derived from them.

## Use the reader on your own log

```python
import gnsswatch as gw
with open("flight.ulg", "rb") as f:
    key, m, info = gw.measure_log(f.read())
print(key, m["spoof"], m["jam"], m["teleports"], m["utc_jumps"], gw.flags(m))
```

`m["spoof"]` counts samples in spoofing states 0-3 (unknown, none, indicated, multiple); `m["jam"]` counts jamming
states 0-3 (unknown, ok, warning, critical).

## Limits

Uploaded logs are not a random sample of flights; the receiver's spoofing flag is a heuristic; a spoofed fix can put a
drone in the wrong place. See `PLAN.md`, `batch2/PLAN-batch2.md` and `study2/PLAN-matched.md`.

PX4 is BSD-3-Clause; pyulog (used only to cross-check the reader) is BSD-3-Clause. No PX4 code is copied here.
