# drone-gnss-rfi, study 2: do drones report GNSS interference on the days and places where aircraft see it?

Pre-registered 2026-11-17, before study 1's job had read any log and before any of its results existed. Code:
`gpsjam_match.py` 0.1.0 (parsing, exposure, statistics, grading) and `run_match.py` 0.1.0 (the job), both in this folder.
The job refuses to read any data until this file and both scripts on the repository's main branch match the copies it runs.

## Question

Study 1 (`../PLAN.md`) asks whether drones whose first fix lies inside four fixed boxes (Baltic, Levant, Black Sea, Gulf)
report spoofing and jamming more often than drones elsewhere. Boxes are blunt: interference switches on and off from day to
day, and it also happens outside them. Study 2 asks a sharper question: **on the UTC day a drone flew, were aircraft over
the same place reporting degraded navigation, and if so, did the drone's own receiver flag spoofing or jamming?**

Aircraft-side data: GPSJam (gpsjam.org, by John Wiseman) publishes, for every UTC day since 2022-02-14, the number of
aircraft per H3 resolution-4 hexagon (about 1,770 km2) that reported good and bad navigation accuracy in ADS-B, from
airplanes.live and ADS-B Exchange receivers (`gpsjam.org/data/<day>-h3_4.csv`, listed in `gpsjam.org/data/manifest.csv`).
Searches on 2026-11-17 found no study that checks drone receivers against these aircraft maps.

**If it works**, it is the first evidence that GNSS interference seen from aircraft at altitude also reaches small
drones near the ground on the same days, measured from flights nobody staged, and a first ground-truth check of the
aircraft-based maps at low altitude. A clear negative (no link between the two) is also worth knowing: either drones
near the ground are shielded from what aircraft see, or their receivers' flags do not detect it.

## Input (fixed now)

- Study 1's per-log rows exactly as its job wrote them, published as `study1/rows.jsonl` in this repository. The job
  records the file's sha256. A row is used if it was measured, has a first good fix (1-degree cell) and has a flight day.
- **Flight day = the drone receiver's own UTC date** (study 1's `m.utc_date`, from the first GNSS sample carrying UTC
  time). Flight Review's `date` is the upload date (its `Logs.Date` is set to the server time at upload), so it is never
  used as the flight day; it only rules out receiver dates more than one day after the upload. Rows without a receiver
  date, or dated before 2022-02-14, are counted and left out.
- GPSJam days marked suspect in the manifest, and days without a file, are counted and left out.

## Exposure (gpsjam_match.exposure)

For a log with first-fix cell (lat, lon) and flight day d: sum GPSJam's good and bad aircraft counts over every hexagon
of day d whose centre lies in the 3 x 3 block of 1-degree cells around (lat, lon) (about 330 x 330 km at the equator,
narrower in longitude further north). Exposure = bad / (good + bad). Fewer than 30 aircraft reports in the block:
exposure unknown, log left out. **Exposed** = exposure at least 0.05.

## Predictions (gpsjam_match.grade)

"Known" = logs with a known exposure. Flags are study 1's (`spoof_reporting`: at least 10 samples in spoofing states
1-3; `spoof_any`: any sample in state 2 or 3; `manipulated`: a position teleport or a receiver-clock jump).

| | Prediction | Set | Held if | Too few if |
|---|---|---|---|---|
| M1 | Exposed drones report spoofing more often | known logs with `spoof_reporting` | exposed share of `spoof_any` minus unexposed share at least 0.10 and one-sided Fisher exact p < 0.05 | fewer than 10 exposed or 50 unexposed |
| M2 | The more aircraft see, the more drones flag (no threshold) | same | AUC of exposure, `spoof_any` logs vs the rest, at least 0.60 and one-sided Mann-Whitney p < 0.05 | fewer than 10 `spoof_any` or 50 other logs |
| M3 | Exposed drones show a higher jamming indicator before arming (motors off, so not self-interference) | known logs with at least 20 disarmed samples and a jamming indicator above 0 at some point | AUC of the disarmed median jamming indicator, exposed vs unexposed, at least 0.60 and p < 0.05 | fewer than 10 exposed or 30 unexposed |
| M4 | Exposed drones more often show position teleports or clock jumps | all known logs | exposed share `manipulated` minus unexposed share at least 0.05 and Fisher p < 0.05 | fewer than 10 exposed or 50 unexposed |

Mann-Whitney p uses the normal approximation with tie and continuity corrections.

Not graded, reported in full: counts by exposure bin (0-1%, 1-2%, 2-5%, 5-10%, 10% and up) with spoofing, sustained
spoofing, jamming warnings, manipulated logs and the disarmed jamming median; M1 at thresholds 0.02 and 0.10; every
exposed 1-degree cell with its log counts and study-1 region; the counts of logs left out and why.

Priors, stated so the grade can surprise us: we expect few exposed logs, because public PX4 logs come mostly from
Europe, North America and East Asia, and inside Europe interference is concentrated in a few places. If fewer than
10 logs are exposed, M1, M3 and M4 are "too few", and that count is itself a result about how rarely public drone logs
overlap aircraft-observed interference. A later batch of logs (study 1 continued in the same hash order) would be
graded with these same rules, pooled, in a dated note.

## What is published

`matched.jsonl` (one line per located study-1 log: public log id, flight day, 1-degree cell, study-1 region, exposure,
aircraft-report count, hexagon count, study-1 flags; no finer position), `summary-matched.json` (grade and description),
`job-fetched.json`. GPSJam's own files are not copied; only the per-log block sums derived from them.

## Limits, stated now

- GPSJam counts aircraft whose navigation-accuracy report dropped; that can be jamming, spoofing, or other causes
  (GPSJam's FAQ notes manoeuvring trainer aircraft), and it does not say which. Aircraft at altitude see
  transmitters over the horizon that a drone near the ground may not.
- A whole UTC day and a 3 x 3 degree block are coarse: interference that lasts an hour or covers one city is diluted.
- GPSJam's sources changed (ADS-B Exchange alone, then merged with airplanes.live from 2026-08-27).
- The drone's place is its first good fix and its day the first receiver UTC time; a spoofed receiver can report both
  wrongly. Uploaded logs are not a random sample of flights. See also study 1's limits.

Corrections after the job starts go into dated NOTE files and do not change this plan.

Aircraft interference data: GPSJam (gpsjam.org) by John Wiseman, from airplanes.live and ADS-B Exchange.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0.
