# Architecture and migration decisions

## Audit and implementation plan

Baseline: 478 tests passed, 99.89% branch-aware coverage. The existing Garmin
transformations cover daily health, sleep, intraday signals, activities, FIT/TCX,
cycling dynamics, strength sets and zones, and manual FIT imports.

Problems found: network writes on import; dictionary transformations tied to
Influx configuration; a heart-rate-based global checkpoint; retries eventually
advancing despite missing days; destructive strength deletion before persistence;
mutable exercise labels in identity; swallowed API failures; dashboard builder
queries accompanied by stale, inactive raw queries; single-account dashboards;
world-writable installer tokens.

Milestones: (1) typed records, versioned schema and persistence; (2) dataset/day
synchronization and collector isolation; (3) SQL dashboards and deployment;
(4) bounded historical migration and documentation; (5) regression, real database,
query and container verification. No user database or volume is modified.

## Components and data flow

Garmin API / FIT -> existing collector transformations -> `Record` normalization
-> `PostgresStore` -> PostgreSQL records / Timescale samples -> SQL Grafana.
`RecordBatch` carries complete strength snapshot scopes, including empty snapshots.
Collectors perform no database queries or writes. The orchestration layer owns
persistence and synchronization. A small protocol describes this boundary.
Psycopg 3 is used directly: no ORM is needed for two document record tables and
explicit transactional SQL. A reusable connection is opened lazily, never at
module import. Failures roll back the dataset transaction and are retried.

## Data model

`persons`, `sources` (provider + account), and `devices` represent provenance.
`records` holds structured summaries, sleep summaries, workouts, sets and zones
as complete typed JSONB documents with original tags. `samples` holds intraday
and GPS documents as a 7-day hypertable. This avoids a row per scalar while
preserving nullable, boolean, text, and nested fields. `all_records` unifies query
access. Dataset names are provider-specific definitions, not universal metrics.
Units remain Garmin's original field semantics (e.g. Weight_kg, Duration_s);
future connectors must declare their own definitions and units.

Identity includes source, device and dataset. Physical IDs from DeviceID tags or
Device_ID fields take priority over the device-label fallback. Strength identities use activity
and set order / zone rather than mutable labels. Activity summaries use activity
and boundary tag; generic samples retain all distinguishing tags and time.
An optional source record ID supports coincident observations. Timestamp is
included in every hypertable uniqueness key. UTC microseconds drive queries;
the remaining 0–999 nanoseconds and original timestamp are retained separately
so legacy observations never collapse due to PostgreSQL precision. Local date
and IANA timezone are stored separately on synchronized records. Never infer
local dates from the server's timezone. Configure USER_TIMEZONE for DST-aware
calendar windows; UTC is the explicit fallback.

`sync_days` records completion independently per source, dataset and local day,
in the same transaction as that dataset's data. Missing days remain pending;
recent two-day overlap refreshes late or corrected data. Empty successful API
responses count as completed availability checks, not proof of sensor coverage.
Manual backfills register their requested range before fetching. Failed oldest
days cannot disappear from hole detection. Changing FETCH_SELECTION creates independent progress. Account-export checks use
a separate checkpoint namespace and cannot certify online API completeness.
Automatic retry skips older completed datasets unless the device uploaded after they
were fetched and no upload covering the whole day (30 minutes before the fetch) preceded
it; days older than 60 days are no longer on the watch and are not checked. Explicit
manual backfills recheck them for source corrections.

Complete strength scopes replace old sets/zones only after both fetches succeed,
in the same transaction as replacement inserts. Other documents merge fields on
upsert, matching legacy partial-field updates; absent observations are not
interpreted as deletion. Deleted/corrected GPS samples need explicit reconciliation.

## Operations and limits

SQL migrations are ordered, checksummed, advisory-locked, transactional, and run
by `python -m garmin_grafana.migrate`, separately from normal ingestion. Before
them it updates the TimescaleDB and Toolkit extensions to the image's versions in
a fresh session, which existing databases otherwise keep, and rebuilds every
index once if the C library's collation version changed (moving from the Alpine
image's musl to glibc reorders text). No retention or compression is enabled.
The pinned `timescale/timescaledb-ha` image (Timescale License; this project stays
BSD-3-Clause) provides continuous aggregates and the Toolkit: `samples_hourly`
(count, sum, min and max per source, dataset, local date and UTC hour of the
intraday series), `gps_cells_daily` (the GPS heatmap's grid cells per UTC day),
`hourly_medians` (exact `percentile_cont` per source, dataset and UTC hour) and
`wear_hourly` (a Toolkit `heartbeat_agg` per source and hour of the 2-minute
slots with a heart rate). They are real-time,
and a policy refreshes changed buckets of the whole history every five minutes,
so a backfill below the last refresh shows up within five minutes. Daily panels
divide sums by counts per local date, which matches the raw averages in every
time zone. Rows without a local date (InfluxDB imports) fall back to the UTC
hour's start: exact for whole-hour offsets, approximate around local midnight in
:30 / :45 zones. Hourly and GPS panels read whole hours or days inside the range from
the rollup and the partial ones at either end from raw samples. Medians cannot
be combined, so `hourly_medians` serves only panels with one-hour buckets and a
single source; the Toolkit's mergeable percentile sketches were measured and not
used, because they moved hourly stress medians by up to 7. Band Wearing merges
the hourly heartbeat aggregates with `rollup()` and returns only the changes
between worn and not worn from `live_ranges()`: a handful of rows instead of one
per 2 minutes (263 k for a year). Compressing
chunks older than 90 days cut the table to a third but made the dashboards
slower at 1 year (5.4 s to 7.9 s of database time): every `all_records` query
paid about 50 ms for the compressed chunks, and the partial ActivitySelector
index no longer applied. See [Timescale unique index rules](https://www.tigerdata.com/docs/learn/hypertables/hypertable-indexes)
and [upstream licensing](https://github.com/timescale/timescaledb/blob/main/LICENSE).

Each batch of up to 1000 rows is one upsert from arrays, its unchanged-row check
limited to the batch's time range so it reads only those chunks. COPY would require a
staging table to preserve upserts; add it only after ingestion benchmarks justify
it. JSONB avoids an enormous speculative domain schema but costs query CPU and
storage: promote heavily queried fields into typed domain tables / indexes when
measurements demonstrate that need. No GIN index on every health field. Time,
source, dataset indexes support dashboard scans; a partial ActivitySelector index
serves per-activity GPS panels and the activity dropdown's loose index scan.
Intraday line panels bucket by Grafana's `${__interval}` (floored at the source
cadence) so long ranges return about one point per pixel, not every sample. At sustained high frequency,
introduce typed stream tables and benchmark chunk size against working-set RAM.

Future connectors produce Records with explicit provider/account, stable source
identity, UTC timestamps, device IDs, and documented units. They must distinguish
unavailable data from failed requests and commit checkpoints with records. Do not
reuse Garmin dataset names for unrelated algorithms. Multi-account ingestion
requires separate processes/token stores and ACCOUNT_ID; dashboards select one
source to avoid mixing people. Database roles remain a deployment responsibility;
Grafana must use a SELECT-only role when exposed beyond a trusted local setup.

Daily SQL grouping prefers the recorded source calendar date over timestamp
conversion, with a UTC-time fallback for legacy records lacking a calendar date.
Activity start/end boundary identities use the Garmin activity ID, so corrected
durations move the end record instead of adding a second boundary.

The external GarminHomeAssistant battery writer is outside this collector; its
historical data is preserved but its writer requires a separate PostgreSQL
adapter. Account exports cannot certify full online dataset availability.

Daily queries use calendar-aware filtering with a bounded two-day timestamp
margin for index pruning. This prevents synthetic UTC-midnight date markers
from falling outside a selected local day. The legacy adapter recovers calendar
dates for known Garmin date-only dataset definitions; unknown timestamps remain
absolute UTC observations. Gap filling uses PostgreSQL generate_series, including
local-day DST boundaries, and requires no Timescale-licensed function.
