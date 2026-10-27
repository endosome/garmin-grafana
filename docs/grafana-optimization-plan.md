# Grafana dashboard optimization plan

As of 2026-10-26, updated 2026-10-27. At the default 30-day range the database is
probably not where the seconds go, so the next round of work targets Grafana and
the browser, after measuring them first.

## Status (2026-10-27)

Done: auto-refresh 5m → 15m with a 15m–1d picker; "Recent Activities" and "Long
term visualization" rows collapsed; GPS Track by Velocity / by Heart Rate
(50, 49) and Month at a Glance (Daily Average) (66) consolidated to one query
each (17 → 3 queries). Old and new SQL return identical rows on seeded data, and
Grafana renders the three panels identically.

Measured in headless Chromium against Grafana 13.0.2 with a small seeded
database, 30-day range, counting `/api/ds/query` POSTs:

| Step | Before | After |
| --- | --- | --- |
| First paint | 10 requests, 14 queries | 10 requests, 14 queries |
| Scroll to bottom | 46 requests, 121 queries | 14 requests, 31 queries |
| One refresh tick at the bottom | 4 requests, 4 queries | 9 requests, 26 queries |
| Open both rows, scroll to bottom | – | 36 requests, 85 queries |

Grafana 13 only queries panels that are on screen, both on first paint and on a
refresh tick, so the refresh interval matters less than assumed below. The
refresh row is not a regression: after collapsing, the bottom of the page shows
different (intraday) panels. Collapsing is the lever that matters. Each panel
is already one HTTP request, so consolidation lowers database scans and
connections, not the request count. Most "Intraday Health Stats" panels have a
fixed `timeFrom` of 24h, and 38, 59 and 65 are pinned to 1w / 30d, so their cost
does not grow with the selected range: consolidating 21 or 59 is cleanup, not a
speedup.

Not done, and why: the remaining worklist rows below are cleanups with little
measured payoff; rendering tweaks and dropping moving averages change what
users see; splitting the dashboard and precomputation are not needed now.
Reusing a panel's results through the "-- Dashboard --" datasource does not fit
either: those targets have no `rawSql`, so the query validator would reject
them.

## Summary

- **Measured** (synthetic 1–3 year TimescaleDB at real Garmin sample rates): at
  30 days all 144 queries together take about 4 s of database time, and Grafana
  runs them in parallel. At 1 year the total is about 27 s, dominated by raw GPS
  and intraday scans.
- **Not measured:** Grafana and browser time (rendering, transformations, joins,
  refresh). Every Grafana-side gain below is an expectation, not a result.
- **Already done:** migration `006` GPS selector index, activity dropdown rewrite
  (2.6 s → 0.26 s at 1 year), GPS heatmap merged into one grid query (panel 69),
  adaptive `${__interval}` buckets on Heart Rate and Breathing (panels 15, 62).

The main dashboard before this work: 59 panels, 142 queries, 3 rows all
expanded, auto-refresh every 5 minutes, and 44 browser-side transformations
(13 `calculateField`, 12 `filterByValue`, 7 `filterFieldsByName`,
6 `joinByField`, 4 `merge`, 2 `groupBy`). Now: 128 queries, 2 of 3 rows
collapsed, 15-minute refresh, 42 transformations (4 `joinByField`).

## Optimizations, ranked by expected payoff

1. **Reduce refresh cost.** *(done: 15m)* The fetcher polls every 5 minutes, but
   watch uploads arrive a few times an hour, so 15m is enough. "Off" would break
   wall displays. Refresh ticks only re-query panels on screen (measured).
2. **Collapse rows.** *(done)* Grafana already lazy-loads off-screen panels, so first
   paint is not all 142 queries. Collapsed rows ("Recent Activities",
   "Long term visualization") stop the query burst when scrolling and keep
   those panels out of every refresh until opened. In JSON this moves child
   panels into `row.panels` with `collapsed: true`, which is a structural change
   (see Constraints). Tradeoff: one click to open a section.
3. **Consolidate queries per panel.** One wide query per panel replaces separate
   scans plus browser-side `joinByField` / `merge`. Column aliases must equal
   today's series names, because `fieldConfig.overrides` match by name.
4. **Move transformations into SQL.** `filterByValue` → `WHERE` / `HAVING`.
   The `calculateField` moving averages on panels 15 and 62 are now largely
   redundant with bucket averaging: drop them rather than port them (panel 15
   uses `replaceFields: true`, so the displayed field is the smoothed one).
5. **Cheaper rendering on dense series.** `lineInterpolation: smooth`,
   `gradientMode: scheme` and threshold areas are the expensive draws on dense
   time series. Try `linear` / `none` where the visual difference is small.
6. **Split the dashboard** into 2–3 (Today / Activities / Long term) only if
   collapsing is not enough. Variables must then be duplicated, and dashboard
   links need `keepTime` and `includeVars`.

Grafana cannot downsample SQL results; the only payload lever is `$__interval`
in the SQL. Remaining raw panels (Stress, Sleep intraday and stages, Heart Rate
Distribution, Month at a Glance (Intraday)) stay raw because averaging would
change their meaning.

## Per-panel worklist

| Panel (id) | Queries now | Browser transformations | Proposed change | Meaning changes |
| --- | --- | --- | --- | --- |
| Month at a Glance (Daily Average) (66) | ~~9~~ 1 | – | Done: one scan, `avg(...) FILTER (WHERE dataset=...)` per metric | No |
| Week at a Glance (59, table, pinned 1w) | 8 | join | One table query | No |
| Recent Activity (38, table, pinned 1w) | 6 | join | One table query; keep `AS "Time"` column | No |
| Month at a Glance (Intraday) (67) | 5 | – | One query with hourly CTE; `Band Wearing` stays 2m or moves to 1h with new mappings | Only if D moves to 1h |
| Stress overview, Activity HR Zones (mean) | 5 each | – | One row / one query each | No |
| Heart Rate Distribution (21) | 4 identical scans | – | One scan, four `CASE` columns | No |
| GPS Track by Velocity / by Heart Rate (50, 49) | ~~4~~ 1 each | – | Done: one `table` query; marker layers' `filterData` now `byRefId: I` | No |
| Distance vs HR (57), Heart Rate vs Pace (55), GPS Track pace (47) | 4 / 2 / 2 | join | One query each | No |
| Piecharts (Today Activity, Last Sleep, Average Sleep trends, Avg Activity) | 4 each | – | One row with one column per slice | No |
| Heart Rate (15), Breathing Rates (62) | 1 | calculateField, filterByValue | Drop moving average; filter in SQL | Slightly smoother line removed |
| Stress (29) | 2 | calculateField, filterByValue | Filter in SQL; keep raw | No |

## Measurement and acceptance

Measure before changing anything, then after each phase, at 30 days and 1 year:

- Browser DevTools → Network, filter `ds/query`: request count and total time on
  first paint, after scrolling to the bottom, and after one refresh tick. This
  confirms lazy loading empirically.
- DevTools → Performance recording of a full load: split scripting
  (transformations) from rendering.
- Panel menu → Inspect → Query / Stats: rows returned and request time per panel.
- PostgreSQL: `log_min_duration_statement=200` in the compose `command` to log
  slow queries; check logs for `too many clients` (Grafana's connection pool vs
  `max_connections`).
- `graphTooltip` is 0 (no shared crosshair), so hover does not redraw every
  panel.

Acceptance is relative, since absolute numbers depend on the user's hardware.
First paint was already lazy, so the request target applies to scrolling
through the dashboard (met: 46 → 14). Scripting + rendering time with real data
has not been measured yet.

## Constraints

- `tests/test_storage.py::test_dashboard_table_and_heatmap_field_contracts`
  recurses into collapsed rows and checks that it visits every query label;
  `test_dashboard_rows_nest_only_when_collapsed` checks row nesting and unique
  panel ids.
- Tests pin panel ids and refIds (`15:A`, `68:A`, `1:A`, `64:*`, `69:I`,
  `ActivityGPS`, strength `1002:A`). Hourly heatmap panels need `valueFieldName`
  in {Hourly Steps, Sleep Regularity} and `timeFieldName == 'Time'`. Table
  queries with `AS "Time"` must keep that column. Consolidation must keep these.
- The dashboard JSON is ~355 KB: edit by script, `indent=2` with a trailing
  newline, panel keys in alphabetical order.
- Provisioning uses `allowUiUpdates: true` with a fixed uid, so shipping a new
  file overwrites any edits made in the Grafana UI.
- Grafana query caching is an Enterprise / Cloud feature and is not available.

## Phasing

1. Measure (section above) at 30 days and 1 year.
2. Refresh interval and collapsed rows; fix the top-level-only test.
3. Consolidate the 9-, 8- and 6-query panels, then the rest of the worklist.
4. Move transformations into SQL; drop redundant moving averages.
5. Re-measure against the acceptance targets.
6. Only then decide on precomputation for 6–12 month views. The pinned
   `timescaledb-oss` image has no continuous aggregates; the options are the
   Community-licensed image or app-maintained rollup tables written by the
   fetcher.
