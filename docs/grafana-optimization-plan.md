# Grafana dashboard optimization plan

As of 2026-10-26. At the default 30-day range the database is probably not where
the seconds go, so the next round of work targets Grafana and the browser, after
measuring them first.

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

The main dashboard today: 59 panels, 142 queries, 3 rows all expanded,
auto-refresh every 5 minutes, and 44 browser-side transformations
(13 `calculateField`, 12 `filterByValue`, 7 `filterFieldsByName`,
6 `joinByField`, 4 `merge`, 2 `groupBy`).

## Optimizations, ranked by expected payoff

1. **Reduce refresh cost.** `refresh: "5m"` re-runs every loaded panel while the
   tab is open. Data syncs a few times an hour, so 15m or off is enough. Also
   trim `timepicker.refresh_intervals`. Trivial change, no semantic risk.
2. **Collapse rows.** Grafana already lazy-loads off-screen panels, so first
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
| Month at a Glance (Daily Average) (66) | 9 | join | One query over the shared daily gap-fill CTE, one column per metric | No |
| Week at a Glance (table) | 8 | merge / join | One table query | No |
| Recent Activity (table) | 6 | merge / filter | One table query; keep `AS "Time"` column | No |
| Month at a Glance (Intraday) (67) | 5 | – | One query with hourly CTE; `Band Wearing` stays 2m or moves to 1h with new mappings | Only if D moves to 1h |
| Stress overview, Activity HR Zones (mean) | 5 each | – | One row / one query each | No |
| Heart Rate Distribution (21) | 4 identical scans | – | One scan, four `CASE` columns | No |
| GPS Track by Velocity / by Heart Rate (50, 49) | 4 each | join | One `table` query with lat, lon, HR, speed; check geomap `color.field` | No |
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
- Check `graphTooltip` at the dashboard root: shared crosshair or tooltip (1 or
  2) redraws every visible panel on hover, a common "feels slow" cause.

Acceptance is relative, since absolute numbers depend on the user's hardware:
halve `ds/query` requests on first paint and on refresh, and halve
scripting + rendering time in the Performance recording.

## Constraints

- `tests/test_storage.py::test_dashboard_table_and_heatmap_field_contracts`
  iterates top-level `dashboard['panels']` only; collapsing rows would silently
  drop nested panels from it. Make it recurse like
  `scripts/validate_dashboard_queries.py` does.
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
