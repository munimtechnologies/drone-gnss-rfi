# drone-gnss-rfi, study 2 on the pooled logs: how the run is done

Published 2026-11-19, before any study-1 log, batch-2 log or study-2 match had been read: on that day the batch-1,
batch-2 and study-2 jobs had not run. This file adds no new prediction and changes no threshold. It fixes the mechanics
of the run that `batch2/PLAN-batch2.md` ("Study 2 on the pooled logs", fixed 2026-11-17) already pre-registered, so that
the code is public before its input exists. Code: `study2/run_match_pooled.py` 0.1.0.

## What runs

1. The job refuses to start until this file and `run_match_pooled.py` on the main branch match the copies it runs.
2. It reads study 1's per-log rows (`study1/rows.jsonl`) and batch 2's (`batch2/rows.jsonl`) from main and records
   their sha256. If either is not on main, it stops and reads nothing else.
3. Pooled rows = batch 1's rows plus the batch-2 rows whose log id is not in batch 1. This is the same rule as
   `batch2/run_batch2.py` (`pooled`), and the number dropped is reported.
4. It downloads study 2's frozen code from main (`study2/PLAN-matched.md`, `study2/gpsjam_match.py` 0.1.0,
   `study2/run_match.py` 0.1.0), records the sha256 of each and runs `run_match.py` unchanged on the pooled rows
   (`--rows`). That script checks its own copies against main again, takes the drone's flight day from its receiver
   clock, fetches each needed GPSJam day once (1 s apart) and writes `matched.jsonl` and `summary-matched.json`.
5. From that one set of matched records it grades M1-M4 three times with `gpsjam_match.grade`, thresholds unchanged:
   on the batch-1 logs, on the batch-2 logs alone and on all pooled logs. A record belongs to batch 1 or batch 2 by its
   log id. GPSJam is fetched once, so the three grades share the same aircraft data.

## Headline per prediction (the rule from PLAN-batch2, applied by the code)

The batch-1 result is the one published by the study-2 job (`study2/summary-matched.json` on main). If that file is
not on main when this job runs, the batch-1 subset graded in step 5 stands in, and the output says so.

| Batch-1 result | Headline | Note |
|---|---|---|
| held or failed | the batch-1 result | "replicated" only if batch 2 alone gives the same result; pooled reported next to it |
| too few | the pooled result | batch 2 alone reported next to it |
| missing | pending | |

A prediction that is "too few" in the pooled data stays "too few".

## Outputs

`pooled/matched.jsonl` (one line per located, dated log, positions as 1-degree cells only), `pooled/summary-matched.json`
and `pooled/job-fetched.json` from study 2's own script; `summary-pooled-matched.json` (input hashes, overlap dropped,
the three grades, the batch-2 description, the headline table) and `job-fetched-pooled.json`. They will be published in
`study2/pooled/` with a `GRADE-pooled.md` that gives every number, misses included. GPSJam's files are not copied.

## Why it matters

Study 1 asks whether drones near the ground report the spoofing and jamming that airliners see. Study 2 asks the
sharper question: on the very day and place where aircraft lost GPS, did the drone's own receiver notice? If M1 or M4
holds, it is the first evidence from unstaged flights that the interference mapped from cruise altitude reaches small
drones near the ground. A clear "failed" with enough logs is also a result: the aircraft maps would then not be a good
guide to what drones experience.

## Limits

Those of `PLAN.md`, `batch2/PLAN-batch2.md` and `study2/PLAN-matched.md`. The pooled data contain batch 1, so a pooled
"held" after a batch-1 "too few" is one confirmatory test, not two.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0.

Aircraft interference data: GPSJam (gpsjam.org) by John Wiseman, from airplanes.live and ADS-B Exchange.
