# NOTE 2026-11-17: study 1's `date` is the upload date, not the flight date

Written after study 1 was pre-registered and before its job had read any log. It does not change `PLAN.md`.

Study 1's sample rule takes each log's date from Flight Review's public index (`log_date` in `review.px4.io/dbinfo`).
Reading Flight Review's source shows that this is `Logs.Date`, which the upload handler sets to the server's time at
upload (`datetime.datetime.now()` in `app/tornado_handlers/upload.py`), not the time the flight happened. So:

- The sample window "log date 2023-01-01 to 2026-11-16" is a window on **upload** dates. A flight from 2022 uploaded in
  2023 is in the sample; a flight from late 2022 uploaded the same week is not.
- The half-year trend in `summary.json` (`half_years_any_of_reporting`) is by upload half-year. It will be reported
  under that name in the grade.
- H1-H5 do not use dates, so the grade is unaffected.

Each row also carries the receiver's own UTC date of its first timed GNSS sample (`m.utc_date`). Study 2
(`study2/PLAN-matched.md`) uses only that as the flight day.

MV2, Munim, Inc. (mv2@munimtech.com). Made with AI agents working in MV2's name. CC BY 4.0.
