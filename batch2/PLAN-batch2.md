# drone-gnss-rfi, study 1 batch 2: the top half of the same order, and a pooled grade

Pre-registered 2026-11-17, before study 1's batch-1 job had read any log and before any result of study 1 or study 2
existed. Code: `batch2/run_batch2.py` 0.1.0. It runs study 1's published code (`PLAN.md`, `gnsswatch.py` 0.1.0,
`run_gnsswatch.py` 0.1.0, downloaded from the main branch) unchanged; only the sample changes. The job refuses to read
any log until this file and `run_batch2.py` on main match the copies it runs, and, once batch 1's
`study1/job-fetched.json` is published, until study 1's code on main is byte-identical to the code the batch-1 job ran.

## Why a second batch, and why now

Study 1 (`PLAN.md`) is graded on batch 1 alone: up to 2,000 public PX4 logs, one per vehicle. Its two main predictions
(H1, H4) need at least 15 located logs from the four "hot" regions (Baltic, Levant, Black Sea, Persian Gulf). Nobody
knows yet how many public logs come from there; if it is a few percent, batch 1 may be "too few". This file fixes,
before any result is seen, exactly which logs batch 2 reads and how batch 2 and the pooled data are graded, so the
second batch cannot be shaped by the first.

## Sample (fixed now)

- Eligibility and the per-vehicle choice are study 1's rule, unchanged (`gnsswatch.eligible`, `gnsswatch.pick_sample`):
  not SITL, simulation or CI; a real vehicle UUID; at least 60 s; upload date 2023-01-01 to 2026-11-16 (see
  `NOTE-2026-11-17-dates.md`); PX4 v1.13 or newer; one log per vehicle, the one whose log id has the smallest sha256;
  vehicles in that hash order. Because the upload-date window closed on 2026-11-16, later uploads cannot enter.
- **Batch 2 is the top half of that order**: the sampled vehicles whose chosen log id has a sha256 (lower-case hex) of
  `8000...` or more, taken in the same ascending order. Batch 1 walks the same order from `0000...` and stops after at
  most 2,000 logs; earlier PX4 index work (compare-2026) counted over 6,000 vehicles, so batch 1 is not expected to
  reach the top half. Batch 2 therefore does not depend on batch 1's output and can run at any time. If the two ever
  share a log id, batch 2's copy is dropped from the pooled grade and batch 2 is also graded without it.
- Reading, measures, flags, regions and stopping rules are study 1's: first 5,000,000 bytes per log, GNSS topics and
  `vehicle_status` only, bytes dropped; stop at 2,000 logs read, 240 minutes of downloading, or a 403/429/444 answer;
  5 s between logs. The V1 check against pyulog runs again on batch 2's first three whole logs.

## Grades (fixed now)

Both grades use study 1's own `gnsswatch.grade` (H1-H5, thresholds and "too few" rules exactly as in `PLAN.md`):

- **Batch 2 alone** (`summary.json`): an independent replication sample.
- **Pooled, batch 1 + batch 2** (`summary-pooled.json`): more power. Written by the batch-2 job if batch 1's results
  are on main when it finishes; otherwise by the same script in pool-only mode (`--pool-only`) after both are published.

Which one answers each prediction is decided now, per prediction:

| Batch-1 result for that prediction | Headline | Also reported |
|---|---|---|
| held or failed | batch 1 (as `PLAN.md` says); called **replicated** only if batch 2 alone gives the same result | pooled |
| too few | **pooled** (confirmatory: same thresholds, the larger sample fixed in advance) | batch 2 alone |

A prediction that is "too few" in the pooled data is reported as too few. There is no batch 3 unless a new dated plan
is published before batch 2's results are read.

## Study 2 on the pooled logs (fixed now)

Once batch 2's rows are published (`batch2/rows.jsonl`), study 2's same-day aircraft match (`study2/PLAN-matched.md`,
M1-M4, `gpsjam_match.py` 0.1.0, thresholds unchanged) is run on the pooled located logs, with the same headline rule:
if study 2 on batch 1 gave "too few" for a prediction, the pooled match is its headline; otherwise batch 2 alone is the
replication and the pooled match is reported next to it.

## What is published

In `batch2/`: `GRADE.md` (both grades, every number, misses included, and the headline per the table above),
`rows.jsonl`, `summary.json`, `summary-pooled.json`, `job-fetched.json` and `batch2-job.json` (which study-1 code and
which batch-1 rows were used, by sha256, and how many vehicles fell below the start of the top half). Positions only as
1-degree cells and region names, as in study 1.

## Load on Flight Review

The same size as batch 1 (at most 2,000 partial downloads, 5 s apart, about four hours), run once, with a User-Agent
that gives MV2's email address. It stops at the first refusal.

## Limits, stated now

Study 1's limits all apply (uploaded logs are not a random sample of flights; first 5 MB only; the spoofing flag is a
receiver heuristic; a spoofed first fix can place a drone in the wrong region). Pooled data share batch 1, so a pooled
"held" after a batch-1 "too few" is one confirmatory test, not two.

Corrections after the job starts go into dated NOTE files and do not change this plan.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0.
