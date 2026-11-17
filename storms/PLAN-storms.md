# drone-gnss-rfi, study S1: did the great geomagnetic storms of 2023-2026 degrade GNSS on real drones?

Pre-registered 2026-11-17, before any log in this study was read. Code: `storms/stormwatch.py` 0.1.0 (Kp events,
placebos, pairing rules, measures, grading) and `storms/run_storms.py` 0.1.0 (the job). The job refuses to read any log
until this file and both scripts on the repository's main branch match the copies it runs. It decodes logs with study 1's
published reader (`gnsswatch.py` 0.1.0, unchanged, downloaded from main; its sha256 is recorded).

## Question

On 10-11 May 2024 the strongest geomagnetic storm since 2003 (Kp 9, the "Gannon" or "Mother's Day" storm) hit Earth
during solar maximum, and more severe storms followed (for example October 2024). GNSS-guided tractors in the US Midwest
stopped planting; a Kansas State University group put the farm losses at about $500 million. Ground networks measured
the ionospheric damage: positioning errors at permanent GNSS stations (for example *Remote Sensing* 17:1492, 2025; *Adv.
Space Res.* 2025 on multi-GNSS PPP; the low-cost Centipede-RTK network), and the UK review in *Royal Society Open Science*
(doi:10.1098/rsos.251943). A review of the storm's mechanisms and impacts (PMC11602687) notes that drone users were
affected too, but cites news reports only, and a 2026 *Space Weather* paper models storm risk for UAV power-line inspection from ground data in Hong Kong.
Searches on 2026-11-17 found no measurement of what any storm did to GNSS on real drones in flight.

PX4 flight logs record the drone receiver's own view: fix type (3D, RTK float, RTK fixed), the estimated horizontal and
vertical accuracy (eph, epv) and the satellites used, next to the receiver's UTC time. Hundreds of thousands of these
logs are public on PX4 Flight Review.

**If it works**, this is the first measurement, from real drone flights nobody staged, of how much the strongest space
weather in two decades degraded drone navigation, with the same vehicles' own quiet-day flights as the control and a
placebo to rule out calendar effects. A clear null (no measurable change at drone level) is also worth knowing: it would
bound the "drones were affected" claims.

## Kp and events (fixed now)

- Kp: GFZ Niemegk's 3-hourly planetary index (`Kp_ap_since_1932.txt`, CC BY 4.0; Matzka et al. 2021, *Space Weather*,
  doi:10.1029/2020SW002641), downloaded by the job; its sha256 is recorded.
- Storm event: a run of 3-hour intervals with Kp 8- (7.667) or more, merged when the next such interval starts less than
  24 h after the run ends; events that start on or after 2023-01-01 and end by 2026-11-14. Processed strongest first
  (maximum Kp, then earlier start).
- Storm-time flight: its start (first sample with a 3D or better fix and a receiver UTC time) falls in an interval with
  Kp 7- (6.667) or more, from 24 h before the event to 24 h after it ends.
- Quiet-time flight: its start falls in an interval with Kp 3+ (3.333) or less.
- Placebo for each event: the same span moved back 28 days, or 56 if that fails, such that Kp is known and below 7- in
  every interval from 14 days before the moved span to 3 days after it; no placebo if both fail. The placebo is run
  exactly like the event, except that its "storm-side" flight must be quiet too.

## Sample rule (fixed now)

The job adds only `pip install numpy pyulog ijson` (pyulog only for V1, ijson to stream the index).

- Source: Flight Review's public index (`review.px4.io/dbinfo`, read once) and its public log downloads.
- Eligible log: study 1's rule (`gnsswatch.eligible`): not SITL, not simulation, not CI; a real vehicle UUID; at least
  60 s; uploaded 2023-01-01 to 2026-11-16; PX4 v1.13 or newer.
- Storm side of an event: logs uploaded from the date of the event start to 2 days after the date it ends. Control side:
  logs uploaded 12 to 2 days before the date of the event start. (The index has only the upload date; the flight time
  comes from the log.)
- For each vehicle with logs on both sides (vehicles taken in the sha256 order of their first storm-side log id): read
  up to 3 storm-side logs (sha256 order of the log id) until one is a usable storm-time flight within the event window;
  if one is, read up to 3 control-side logs and keep those that are usable quiet-time flights starting 14 to 2 days
  before the event start (stop early at one within 2 h of the same UTC time of day); the pair's control is the kept one
  closest in UTC time of day to the storm flight. One pair per vehicle per stage.
- Usable: at least 10 GNSS samples from the start sample on.
- Each log: only the first 2,000,000 bytes (an HTTP range request; about the first minute), only the GNSS topics
  (`sensor_gps`, else `vehicle_gps_position`, else `vehicle_gnss`, lowest instance with rows); bytes dropped at once.
- Requests start at least 6 s apart (at most 10 a minute, the rate PX4's `download_logs.py` documents); a log read once
  is never downloaded again. Real events first for up to 180 minutes of reading, then placebos until 240 minutes; also
  stop at 2,400 logs read or if the server refuses (403/429/444).

## Measures per log (`stormwatch.measure`), from the start sample on

- `eph_med`, `epv_med`: medians of eph and epv over samples with a 3D or better fix (fix types 3-6), values in (0, 100) m.
- `sats_med`: median satellites used over those samples (1-99).
- `loss_share`: share of samples without a 3D or better fix (fix types 0-2, 7, 8).
- `rtk_fixed_share`: from the first RTK sample (fix type 5 or 6) on, the share of samples with RTK fixed (6).
- Kept for publication: the start hour (UTC), the 1-degree cell of the start sample, the medians and counts.

## Predictions (`stormwatch.grade`)

Pairs: one storm-time flight and one quiet-time flight of the same vehicle. All tests are one-sided exact sign tests on
the pairs (ties dropped), "worse" meaning worse at storm time.

| | Prediction | Held if | Too few if |
|---|---|---|---|
| P1 (primary) | Estimated horizontal accuracy is worse at storm time | eph worse in more pairs, p < 0.05, and the median storm/quiet eph ratio is at least 1.10 | fewer than 30 untied pairs with at least 10 eph samples on both sides |
| P1b | The same within the same time of day | P1's rule on pairs whose flights started within 3 h of the same UTC time of day | fewer than 20 untied pairs |
| P2 | Fewer satellites are used at storm time | `sats_med` lower in more pairs, p < 0.05, and the median drop is at least 0.5 | fewer than 30 untied pairs |
| P3 | Fixes are lost more often at storm time | McNemar exact on "any loss" (storm only vs quiet only), p < 0.05 | fewer than 10 discordant pairs |
| P4 | RTK holds its fixed solution less at storm time | `rtk_fixed_share` lower in more pairs, p < 0.05 | fewer than 10 untied pairs with at least 10 RTK samples on both sides |
| P0 (placebo) | The placebo shows no such effect | "clean" unless P1's rule holds on the placebo pairs ("moved") | fewer than 30 untied placebo pairs |

Headline rule, fixed now: P1 held and P0 clean -> "storm-time accuracy was worse and the placebo was clean"; P1 held and
P0 too few -> said so; P1 held and P0 moved -> not attributed to the storms; P1 failed -> no effect at these thresholds;
P1 too few -> too few pairs. Described, not graded: eph ratios by event, by the storm flight's Kp (8- or more vs below)
and by latitude band (|lat| < 30, 30-50, 50 or more).

Priors, stated so the grade can surprise us: storm effects on single-frequency receivers at middle latitudes were
largest in the main phase and on the evening and night side, and Kp is a global index, so many storm-time flights will
have been under a quiet local ionosphere; eph is the receiver's own estimate, not the true error, and may react less than
the true error does. We expect P4 (RTK) to be the most sensitive, P1 to show a small effect at most, and "too few" to be
a real risk for P4. We do not know how many public logs were flown during storm hours.

## Checks on the job

- V1: on the first 3 logs read whole, pyulog reads the same number of rows and the same fix types of the measured
  topic as study 1's reader. A V1 failure is reported next to every result.
- `storms/test_stormwatch.py`: unit tests of the Kp reader, events, placebos, windows, flight rules, measures, pairing
  and grading on synthetic data.

## What is published

`storms/rows.jsonl` (every log read: public log id, upload date, release, hardware, event, side, status, measures with
the start hour and 1-degree cell only), `pairs.jsonl`, `placebo-pairs.jsonl`, `summary.json` (event table with reads,
acceptances and pairs per event, grade, descriptions) and `job-fetched.json` (published-code check, study 1 code sha256,
Kp file sha256, index counts). No exact position, time, track or raw log is copied.

## Limits, stated now

- Uploaded logs are not a random sample of flights, and storm-time pairs come from vehicles that happened to fly during
  storm hours; the paired design compares each vehicle with itself, but a vehicle's two flights can still differ (site,
  payload, antenna, time of day; P1b checks time of day).
- Only the first 2 MB of each log is read, often before or just after take-off.
- eph, epv and the RTK state are the receiver's own reports and differ between receiver generations.
- Kp is a planetary index; the local ionosphere at a drone can be quiet during a high-Kp interval and disturbed during a
  moderate one.
- Flight Review's date is the upload date; a flight uploaded late falls outside both windows and is simply not used.

Corrections after the job starts go into dated NOTE files and do not change this plan.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0. Kp data: GFZ Helmholtz Centre for Geosciences (CC BY 4.0).
