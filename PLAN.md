# drone-gnss-rfi, study 1: do civilian drones near the ground see the GNSS spoofing and jamming that airliners report?

Pre-registered 2026-11-17, before any log in this study was read. Code: `gnsswatch.py` 0.1.0 (reader, measures, sample rule,
grading) and `run_gnsswatch.py` 0.1.0 (the job). The job refuses to read any log until this file and both scripts on the
repository's main branch match the copies it runs.

## Question

Since 2022, GNSS (GPS, Galileo, GLONASS, BeiDou) jamming and spoofing have spread around the Baltic Sea, the eastern
Mediterranean, the Black Sea and the Persian Gulf. Almost everything we know about where it happens comes from airliners at
cruise altitude (ADS-B position-quality reports, e.g. GPSJam, SkAI/GPSWise, and the Stanford work on spoofing over Russia),
or from a few fixed ground monitors (e.g. GPSPATRON with Gdynia Maritime University). Nobody has measured it from the
receivers in civilian drones, which fly within a few hundred metres of the ground.

PX4 flight logs record what the drone's own GNSS receiver says about interference: `spoofing_state` (u-blox NAV-STATUS
spoofDetState: 0 unknown, 1 none, 2 indicated, 3 multiple indications), `jamming_state` (0 unknown, 1 ok, 2 warning,
3 critical) and `jamming_indicator`, next to the position and the receiver's clock. Thousands of these logs are public on
PX4 Flight Review. Searches on 2026-11-17 found no study that measures spoofing or jamming prevalence from public drone logs;
published drone work uses attacks the authors staged themselves.

**If it works**, this is the first map of GNSS spoofing as seen by drones near the ground, built from flights nobody staged:
how often civilian drones in the conflict-adjacent regions report spoofing compared with the rest of the world, and whether
their logs show the position teleports and clock jumps that real spoofing leaves behind. A clear negative result (no
difference, or indications that are mostly receiver false alarms) is also worth knowing.

## Data and sample rule (fixed now)

- Source: Flight Review's public index (`review.px4.io/dbinfo`, read once) and its public log downloads.
- Eligible log: not SITL, not a simulation, not CI; a real vehicle UUID; logged duration at least 60 s; log date
  2023-01-01 to 2026-11-16; PX4 release series v1.13 or newer. Every vehicle type counts (multicopter, fixed wing, VTOL,
  rover): the receiver is the same.
- One log per vehicle: the eligible log whose log id has the smallest sha256. Vehicles are taken in that hash order.
- For each sampled log the job reads only the first 5,000,000 bytes (an HTTP range request), decodes only the GNSS topics
  (`sensor_gps`, `vehicle_gps_position`, `vehicle_gnss`) and `vehicle_status`, and drops the bytes.
- Stop at 2,000 logs read, at 240 minutes of downloading, or if the server refuses (403/429/444). A 5 s pause between logs.
- A later batch (if any) continues in the same hash order; this plan is graded on batch 1 alone.

## Measures per log (gnsswatch.measure)

- Topic: the lowest instance of `sensor_gps`, then `vehicle_gps_position`, then `vehicle_gnss` whose rows carry a non-zero
  spoofing state; else the first of those with rows.
- Counts of each spoofing state and each jamming state over all GNSS samples read; jamming indicator median and maximum,
  and its median over disarmed and over armed samples (arming state 2 = armed in `vehicle_status`).
- First good fix: the first sample with fix type 3 or better, eph below 20 m (where logged) and at least 6 satellites (where
  logged). Only its 1-degree cell and its region (below) are kept.
- Teleport: two consecutive 3D fixes more than 1 km apart at an implied speed above 150 m/s. Count, largest jump, and the
  1-degree cell the first teleport lands in.
- Clock jump: between consecutive samples with a receiver UTC time, the receiver clock and the log clock disagree by more
  than 2 s. Count and largest.
- Flags: `spoof_reporting` = at least 10 samples in states 1-3; `spoof_any` = any sample in state 2 or 3;
  `spoof_sustained` = states 2-3 make up at least 25% of states 1-3; `jam_reporting` = at least 10 samples in jamming
  states 1-3; `jam_warn` = any jamming state 2 or 3; `manipulated` = any teleport or clock jump.

## Regions (fixed now, half-open boxes on the first good fix)

| Region | Latitude | Longitude | Why |
|---|---|---|---|
| baltic | 53 to 61 N | 13 to 30 E | jamming and spoofing around Kaliningrad, Poland, the Baltic states and Finland since 2023 |
| levant | 29 to 37.5 N | 32 to 40 E | spoofing over Israel, Lebanon, Syria, Cyprus and Jordan since October 2023 |
| black_sea | 43 to 53 N | 30 to 42 E | jamming and spoofing around Ukraine, Crimea and southern Russia since 2022 |
| gulf | 23 to 31 N | 47 to 57 E | jamming and spoofing over the Persian Gulf |
| rest | everything else | | |

"Hot" means any of the first four.

## Predictions (gnsswatch.grade)

Analysis set for H1-H3: logs read with `spoof_reporting` and a first good fix. For H4: `jam_reporting` and a first good fix.

| | Prediction | Held if | Too few if |
|---|---|---|---|
| H1 | Drones in hot regions report spoofing more often | hot share of `spoof_any` minus rest share at least 0.10 and one-sided Fisher exact p < 0.05 | fewer than 15 hot or 50 rest logs |
| H2 | The background rate is low | rest share of `spoof_any` at most 0.12 | fewer than 50 rest logs |
| H3 | Background indications are mostly not real attacks | among rest logs with `spoof_any`, share `manipulated` at most 0.20 | fewer than 10 such logs |
| H4 | Drones in hot regions report jamming more often | hot share of `jam_warn` minus rest share at least 0.10 and Fisher p < 0.05 | fewer than 15 hot or 50 rest logs |
| H5 | Much "jamming" on drones is self-interference | among logs with at least 20 disarmed and 20 armed samples and a non-zero jamming indicator, the armed median is higher than the disarmed median in at least 60% | fewer than 20 such logs |

Priors, stated so the grade can surprise us: an earlier study of 208 full public logs (px4-log-health, fleet-health-2026)
found spoofing "indicated" in 8% of logs that report the state, and jamming state 2 in 70% of logs that report it. That 70%
is why H4 may fail even if interference is real: self-interference from the drone's own electronics may swamp it, which H5
tests. We do not know how many public logs come from the hot regions. If fewer than 15 do, H1 and H4 are "too few", and
that count is itself a result about where public drone logs come from.

## Checks on the job

- V1: on the first 3 logs read whole, pyulog reads the same number of rows of the chosen topic and the same spoofing-state
  counts as gnsswatch's reader. A V1 failure is reported next to every result.
- The reader is tested on synthetic logs (`test_gnsswatch.py`: nested types, unlogged trailing padding, truncation,
  removed subscriptions, resync after corrupt bytes).

## What is published

`rows.jsonl` (one line per attempted log: public log id, date, release, hardware, the measures and flags; position only as
the 1-degree cell of the first good fix, the region name and the 1-degree cell where a teleport landed), `summary.json`
(grade, region table, 5-degree grid with cells under 3 logs hidden, half-year trend, teleport landing cells) and
`job-fetched.json`. No exact position, track or raw log is copied.

## Limits, stated now

- Uploaded logs are not a random sample of flights: people upload logs to ask for help, so problems are over-represented.
- Only the first 5 MB of each log is read (roughly the first one to three minutes): spoofing that starts later is missed.
- The receiver's spoofing flag is a heuristic and differs between u-blox generations; other receivers may not report it.
- The region is taken from the drone's own first fix. A spoofed fix can be wrong, though spoofers usually place receivers
  at a fixed point inside the same region (for example an airport).
- One log per vehicle; a vehicle's location is where that one log started.

Corrections after the job starts go into dated NOTE files and do not change this plan.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0.
