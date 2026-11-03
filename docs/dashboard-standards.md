# Dashboard standards

How we build and change the provisioned dashboards in `Grafana_Dashboard/`. These
are the conventions the existing dashboards already follow, plus the rules we
settled on when fixing them. When a rule and an old panel disagree, fix the panel.

## Files and provisioning

- One JSON file per dashboard, provisioned read-only from `Grafana_Dashboard/`
  (compose mounts it `:ro`). Edit the file, not the dashboard in the Grafana UI;
  a UI edit is lost on the next provisioning load.
- Give each dashboard a stable `uid`, a `description`, `tags` (`garmin` plus a
  topic), `weekStart: monday`, `graphTooltip: 1` (shared crosshair) and
  `timezone: browser`. Link related dashboards with `links` that keep the time
  range and variables (`keepTime`, `includeVars`).
- Write the JSON with two-space indentation and ASCII escapes (`–`, not `–`),
  as Grafana exports it, so diffs only show real changes. Load and dump with
  Python's `json.dumps(d, indent=2)` rather than editing by regex when a change
  touches many panels, and check `git diff` for stray reformatting.
- After a change, the running Grafana must reload the provisioned file (restart
  or wait for the provisioning interval) before the change is visible.

## Variables

- `Source` (query over `sources`, multi-value) selects the account. Every query
  filters `source_id IN (${Source:csv})`, so accounts never see each other's data.
- `TimeZone` is the viewer's time zone. Convert with
  `AT TIME ZONE ${TimeZone:sqlstring}` and use `local_date` when a row has one;
  fall back to the converted UTC date only for rows without it.
- Use `$__timeFilter(time)`, `$__timeFrom()` and `$__timeTo()` for the range, and
  `${__interval}` to bucket intraday series so a long range returns about one
  point per pixel. Do not return every sample.

## Queries

- Raw SQL, `rawQuery: true`, `editorMode: code`. Format `time_series` for charts
  and tiles, `table` for tables. A time series needs a `time` column and returns
  one numeric column per series; use `AS metric` for long format.
- Read through `all_records` and `health_number(fields, '...')`, or through the
  rollups (`samples_hourly`, `hourly_medians`, ...) where one exists. Prefer a
  rollup for anything that scans more than a few days (see
  [architecture](architecture.md)). Exclude the `END` activity marker
  (`fields->>'activityName' IS DISTINCT FROM 'END'`).
- One query per panel where possible. Do joins, filters and moving averages in SQL,
  not in browser transformations; the browser is the slow part.
- Every query must also run on an empty database: tiles return no row or `NULL`,
  never an error.
- Do not leave inactive or stale queries in a panel.

## Units and number formatting

- **Return the raw unit and let Grafana format it.** Return durations in seconds,
  distances in metres (or kilometres where the panel says so), and do not divide
  in SQL to make a display friendlier. Thresholds are written in the same unit as
  the returned value (6 h is `21600`, not `6`).
- **Durations are shown as hours and minutes, never decimal hours.** "11.2 h" is
  11 h 12 min and reads wrongly. Single values (stat tiles) use `clocks`; table
  columns use `dthms`; the column is named `Duration`, not `Hours`. Bar gauges
  may use `s`, which Grafana scales to minutes or hours on its own.
- Time-series and bar charts that stack or sum durations (per night, per week)
  stay in decimal hours (`suffix: h`, one decimal, value divided by 3600 in SQL),
  because axes and stacks need a linear scale. Say "Hours" in the description.
- Other units: `suffix: bpm`, `suffix: ms`, `suffix: brpm`, `suffix: km`,
  `celsius`, `percent`, `velocitykmh`, `lengthkm`. Set `decimals` explicitly.
- Set `noValue` to an en dash (`–`) so a missing value is visibly missing.

## Panels

- Every panel has a `description`: what it shows, the period it covers, which
  direction is better, and when it is empty (for example "Empty until Garmin
  Connect has last night's sleep"). Health panels that compare with a personal
  baseline say it is not medical advice.
- Stat tiles for "latest" values use `timeFrom` with `hideTimeOverride` so they
  show the latest value regardless of the dashboard range, and `lastNotNull` or
  `last` as the reducer.
- Use Garmin's own colour bands where Garmin defines them (sleep score: 90+
  excellent, 80-89 good, 60-79 fair, below 60 poor). Otherwise use red/orange/green
  thresholds for "needs attention / fair / good", and `text` for neutral values.
- Personal-baseline panels show the daily dots, a 7-day average, and a band for the
  mean plus or minus one standard deviation of the previous 28 days, shown only
  once enough days have data.
- A column or series that is renamed must have its override matcher renamed too
  (`byName`), or the unit and decimals silently stop applying.

## Testing a dashboard change

Layers are described in the [dashboard testing plan](dashboard-testing-plan.md).

1. `uv run --locked pytest tests/test_dashboard_lint.py` needs no database. It enforces
   this document where it can be checked statically: canonical JSON, uids, tags and
   settings, layout, installed panel types, `source_id IN (${Source:csv})`, variables,
   units, `noValue`, links, and a registry entry for every panel
   (`tests/panel_registry.py`). Rules some old panels still break are ratcheted in
   `tests/data/lint_ratchet.json`; fixing a panel means removing it from there
   (`LINT_UPDATE_RATCHET=1` rewrites the file).
2. Run the query checks against a disposable TimescaleDB, never the real
   database (setup is in [development](development.md)):
   `uv run --locked python scripts/validate_dashboard_queries.py`, and the suite with
   `TEST_DATABASE=1`. `tests/test_dashboard_contracts.py` snapshots every query's
   columns (`pytest --update-schemas` after a deliberate change, reviewed in the same
   diff as the override that depends on it) and checks isolation between accounts,
   two accounts at once, and zones and ranges. `tests/test_personas.py` checks panel
   output against values computed independently from the persona data. When a panel's
   unit changes, change the expected value and the column name in
   `tests/personas.py` and `tests/test_personas.py` in the same commit. Without
   `TEST_DATABASE=1` these tests are skipped and prove nothing.
3. Run the browser tests against the Compose stack with the personas loaded
   (`scripts/load_personas.py`), inside the memory limit:
   `systemd-run --user --scope -p MemoryMax=6G -p MemoryHigh=5G -- e2e/run.sh`.
   They check that every dashboard loads without a panel error, console error or missing
   plugin, the text of the tiles (`clocks`, `dthms`, decimals, `noValue`), dropdowns, links,
   collapsed rows, Fetch now, the kiosk, and screenshots of the chart panels. A deliberate
   change to a chart updates its baseline with `e2e/update-baselines.sh`; look at every
   changed image and commit the baselines with the change that explains them.
4. Run heavy commands inside a memory limit and with low parallelism, as the
   project and machine rules require.
