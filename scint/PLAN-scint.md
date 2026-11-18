# drone-gnss-rfi, study S2: do equatorial plasma bubbles degrade GNSS on real drones after sunset?

Pre-registered 2026-11-18, before any log in this study was read. Code: `scint/scintwatch.py` 0.1.0 (geomagnetic
latitude, local solar time, sample rule, grading) and `scint/run_scint.py` 0.1.0 (the job). The job refuses to read any
log until this file and both scripts on the repository's main branch match the copies it runs. It decodes logs with
study 1's published reader (`gnsswatch.py`, `run_gnsswatch.py`) and measures them with study S1's published
`storms/stormwatch.py` (`measure`), all downloaded from main unchanged; their sha256 values are recorded.

## Question

After sunset, the ionosphere over the magnetic equator often breaks up into "plasma bubbles": depleted channels full of
small irregularities that make GNSS signals flicker (scintillation). Receivers then lose lock on satellites, positions
degrade and RTK solutions drop out. Ground receivers see this mostly from about 19:00 to 01:00 local time, most of all
around the equinoxes, within about 20 degrees of the magnetic equator, and most strongly near solar maximum. Solar cycle
25 peaked in 2024-2025. Ground networks have measured it many times, for example Abadi et al. 2024 (*Space Weather*,
doi:10.1029/2024SW003908, Kototabang), the South China multi-GNSS case study in *Remote Sensing* 16:1358 (2024), and Li et
al. 2026 (*Space Weather*, doi:10.1029/2025SW004727, multi-year scintillation and PPP). A 2025-2026 study ("Space Weather
and Low-Altitude Drone Economy") models what scintillation means for drone power-line inspection, but it uses ground
PPP data from Hong Kong and a simulated drone. Searches on 2026-11-18 found no measurement of scintillation effects on
GNSS on drones in real flight.

Hundreds of thousands of public PX4 flight logs on PX4 Flight Review record the drone receiver's own view: fix type
(3D, RTK float, RTK fixed), the estimated horizontal accuracy (eph), the satellites used and the receiver's UTC time.

**If it works**, this is the first measurement, from drone flights nobody staged, of the post-sunset GNSS degradation
that plasma bubbles cause at low latitudes, with each vehicle's own daytime flight as the control and middle latitudes as
the negative control. A clear null is also useful: it would mean that, in the first minute or so of the flights people
upload, the effect is too small or too rare to show at these thresholds.

## Bands and times (fixed now)

- Position: the centre of the 1-degree cell of the flight's start (the first sample with a 3D or better fix and a
  receiver UTC time, as in `stormwatch.measure`).
- Geomagnetic latitude: centred dipole with the north pole at 80.7 N, 72.7 W (close to IGRF for 2020-2025).
  Low band: |mlat| < 20. Middle band: 30 <= |mlat| < 55. Others are not tested.
- Local time: mean local solar time from the start's UTC time and the cell centre's longitude.
  Night: 19:30-01:00. Day: 08:00-17:00. Dusk (17:00-19:30) and late night (01:00-08:00) are not compared.
- Season: logs uploaded in February-April or September-November, the months around the equinoxes. (The index has only
  the upload date; it is a stand-in for the flight month.)

## Sample rule (fixed now)

The job adds only `pip install numpy pyulog ijson` (pyulog only for V1, ijson to stream the index).

- Source: Flight Review's public index (`review.px4.io/dbinfo`, read once) and its public log downloads.
- Eligible log: study 1's rule (`gnsswatch.eligible`): not SITL, not simulation, not CI; a real vehicle UUID; at least
  60 s; uploaded 2023-01-01 to 2026-11-16; PX4 v1.13 or newer; and uploaded in season.
- Vehicles with at least 2 eligible logs. Each vehicle's logs are ordered by sha256("s2|" + log id), and vehicles by the
  same hash of their first log (the "survey log"). The salt keeps this order different from studies 1 and S1.
- Survey: read each vehicle's survey log in that order, measure it and classify it (band, time class). Usable: start
  time, start cell and at least 10 GNSS samples from the start on.
- Pairing: if the survey log is a night flight in the low band, read up to 3 more of that vehicle's logs (in hash order)
  until one is a usable day flight in the same band; that is a night/day pair. The same in the middle band, as long as
  middle-band pairings tried do not exceed low-band ones by more than 5 (so the control does not eat the budget).
- Each log: only the first 2,000,000 bytes (an HTTP range request; roughly the first minute), only the GNSS topics
  (`sensor_gps`, else `vehicle_gps_position`, else `vehicle_gnss`, lowest instance with rows); bytes dropped at once.
- Requests start at least 7.5 s apart (8 a minute, under the 10 a minute that PX4's `download_logs.py` documents);
  each log is downloaded at most once. Stop after 540 minutes of reading, 4,300 logs read, or if the server refuses
  (403/429/444).

## Measures per log (`stormwatch.measure`, unchanged from study S1)

`eph_med` (median eph over samples with fix types 3-6, values in (0, 100) m), `sats_med`, `loss_share` (share of samples
without a 3D or better fix), `rtk_fixed_share` (from the first RTK sample on). A metric counts only with at least 10
samples behind it. "Degraded" (N3): `eph_med` at least 2 times the median `eph_med` of the same band's day survey logs.

## Predictions (`scintwatch.grade`)

Paired tests are one-sided exact sign tests (ties dropped), "worse" meaning worse at night.

| | Prediction | Held if | Too few if |
|---|---|---|---|
| N1 (primary) | Low band: eph is worse on the night flight than on the same vehicle's day flight | worse in more pairs, p < 0.05, and median night/day eph ratio >= 1.10 | fewer than 15 untied low-band pairs |
| N0 (control) | Middle band: no such effect | "clean" unless N1's rule holds on middle-band pairs ("moved") | fewer than 15 untied middle-band pairs |
| N2 | Survey logs: fix losses rise at night more in the low band than in the middle band | low-band night share minus day share >= 0.05, that minus the same middle-band difference >= 0.05, and one-sided Fisher exact p < 0.05 (low night vs low day) | fewer than 20 night or 100 day survey logs in either band |
| N3 | Survey logs: the same for "degraded" eph | N2's rule on the degraded flag | as N2 |
| N4 | Low band: fewer satellites used at night | `sats_med` lower in more pairs, p < 0.05, median drop >= 0.5 | fewer than 15 untied pairs |
| N5 | Low band: RTK holds its fixed solution less at night | `rtk_fixed_share` lower in more pairs, p < 0.05 | fewer than 8 untied pairs |

Headline rule, fixed now: N1 held and N0 clean -> "night accuracy was worse at low latitudes and the middle-latitude
control was clean"; N1 held and N0 too few -> said so; N1 held and N0 moved -> not attributed to plasma bubbles; N1
failed -> no post-sunset effect at these thresholds; N1 too few -> too few pairs. Described, not graded: survey counts
and rates by band and time class (with dusk), and low-band night logs by local time (19:30-21:00, 21:00-23:00,
23:00-01:00), longitude sector, upload month and upload year.

Priors, stated so the grade can surprise us: most public PX4 logs come from Europe and North America, so the low band
(southern China including Shenzhen, India, South-East Asia, Brazil, Africa) may be thin, and night flights rarer still:
"too few" for N1 is the most likely single outcome. Scintillation is patchy, and on most equinox nights at a given site
there is no bubble overhead, so a median effect may be small even where it is real. N2 and N5 (fix losses, RTK) are the
most likely to show it. The daytime ionosphere is denser, which may make day flights look worse on some measures. eph is
the receiver's own estimate, not the true error.

## Checks on the job

- V1: on the first 3 logs read whole, pyulog reads the same number of rows and the same fix types of the measured
  topic as study 1's reader. A V1 failure is reported next to every result.
- `scint/test_scintwatch.py`: unit tests of geometry, time classes, sample order, statistics and grading on synthetic
  data.

## What is published

`scint/rows.jsonl` (every log read: public log id, upload date, release, hardware, role, status, measures with the start
UTC hour and 1-degree cell only, class with mlat to 0.1 degree and local time in half hours), `survey.jsonl`,
`pairs.jsonl`, `summary.json` (grade, descriptions, V1) and `job-fetched.json` (published-code check, frozen code sha256,
index counts, stop reason). No exact position, time, track or raw log is copied.

## Limits, stated now

- Uploaded logs are not a random sample of flights; the paired design compares each vehicle with itself, but its night
  and day flights can still differ (site, purpose, payload).
- A centred dipole is a rough stand-in for the dip equator (off by several degrees in places, most in South America);
  the 20-degree band is wide for that reason.
- Only the first 2 MB of each log is read, often before or just after take-off.
- The upload month stands in for the flight month; no per-night scintillation or bubble data are used, and storm nights
  are not removed (study S1 covers storms).
- If other studies' jobs read Flight Review at the same time, the combined request rate is higher than this job's own.

Corrections after the job starts go into dated NOTE files and do not change this plan.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name; no human has checked this plan.
Text CC BY 4.0; code Apache-2.0.
