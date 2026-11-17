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

Results will be added as `GRADE.md` with the job's outputs. Positions are published only as 1-degree cells and region names.

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
drone in the wrong place. See `PLAN.md`.

PX4 is BSD-3-Clause; pyulog (used only to cross-check the reader) is BSD-3-Clause. No PX4 code is copied here.
