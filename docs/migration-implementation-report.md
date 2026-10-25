# PostgreSQL + TimescaleDB migration: implementation and outstanding work

Status: implementation delivered; production acceptance and user-data cutover
remain outstanding. This report covers the repository changes replacing the
InfluxDB runtime. It distinguishes executed checks from operator acceptance and
future engineering work. No real account was authenticated, no user historical
migration was performed, and no existing user database or volume was removed.

## Documentation map

- [README](../README.md): installation, configuration, backups, upgrades, exports.
- [Architecture](architecture.md): audit findings, schema and design trade-offs.
- [Migration runbook](migration-influxdb-to-timescaledb.md): supported sources,
  backup, preview, import, validation, cutover and recovery commands.
- [Developer guide](development.md): local operation, migrations, tests and
  connector guidelines.
- [Verification evidence](verification.md): commands, measured results and limits.
- [Manual imports](manual-import-instructions.md): account exports and FIT files.

## Change inventory

| Area | Files / resources | Delivered behavior |
| --- | --- | --- |
| Domain boundary | `src/garmin_grafana/domain.py` | Record normalization, typed values, timestamp validation, stable identities, device provenance and complete strength snapshot scopes |
| Persistence | `src/garmin_grafana/storage.py` | Lazy reusable psycopg connection, bounded batches, upserts, atomic snapshots/checkpoints, source isolation and legacy archive validation |
| Schema management | `src/garmin_grafana/migrate.py`, `migrations/001`–`005` | Explicit, ordered, checksummed SQL migrations with advisory locking; optional Grafana reader-role bootstrap |
| Garmin orchestration | `src/garmin_grafana/garmin_fetch.py` | PostgreSQL writes; dataset/day synchronization; requested-range tracking; overlapping refresh; safe retries; no database writes during import |
| Garmin correctness | `garmin_fetch.py`, `domain.py` | Atomic strength correction, null preservation, UTC-aware TCX, preserved strength timestamp offsets/precision, physical IDs where available, stable activity start/end identities |
| Authentication | `garmin_fetch.py`, `tests/test_login.py` | Existing token/MFA flow retained; interactive passwords no longer echo |
| Account-export importer | `src/garmin_grafana/garmin_bulk_importer.py` | Separate checkpoint namespace; unavailable strength snapshots cannot erase existing sets; optional cache-write failures do not break read-only imports |
| Individual FIT importer | `src/garmin_grafana/fit_activity_importer.py` | Shared persistence boundary, package/module execution and database-free dry run |
| Historical database importer | `src/garmin_grafana/legacy_import.py` | Read-only Influx 1.x HTTP streaming, bounded windows/batches, resumable progress, preview, account filtering and archived field-level validation |
| Portable export | `src/garmin_grafana/export.py` | Account-scoped streaming JSONL ZIP with native types/provenance and nanoseconds; refuses to overwrite an existing archive |
| Grafana datasource | `Grafana_Datasource/postgres.yaml` | Provisioned PostgreSQL datasource, fixed `garmin_postgres` UID, separate SELECT-only login in the deployment examples |
| Grafana dashboards | Both JSON files in `Grafana_Dashboard/` | SQL panel/variable queries, account selection, strength and GPS views, proper query formats/field names, calendar-aware grouping and null/zero gap filling |
| Query checks | `scripts/validate_dashboard_queries.py`, `scripts/smoke_grafana.py` | Database execution and actual Grafana datasource/provisioning smoke checks |
| Compose / container | `compose-example.yml`, `Dockerfile`, `.dockerignore`, `.env.example` | Pinned database/Grafana/plugin versions, health dependencies, explicit migration service, persistent storage/tokens, non-root app and private database networking |
| Installer | `easy-install.sh` | Random initial secrets, interactive authentication/backfill, no world-writable token directory or automatic teardown of an existing stack |
| Packaging | `pyproject.toml`, `uv.lock` | psycopg replaces Influx clients; explicit runtime dependencies; installable entry point and distribution containing SQL migrations |
| CI / regression tests | `.github/workflows/tests.yml`, `tests/` | Real Timescale test service, unchanged coverage gate, Grafana checks and retained Garmin/FIT regression coverage |
| Supporting material | `.github/CONTRIBUTING.md`, `Extra/garmin-fetch.ipynb`, `.gitignore`, documentation | Current development instructions; notebook delegates to maintained code; secret/build artifact exclusions |

Removed: the Influx datasource, Influx runtime clients/configuration and demo
writes, destructive strength purge logic, the old CSV exporter, obsolete
backend-specific tests, and (2026-10-26) all Kubernetes/Helm artifacts: the fork only supports Compose.
The new migration utility uses HTTP and needs no Influx Python dependency.

## Architecture and data guarantees

The model deliberately avoids a universal scalar-value table and a speculative
enterprise framework. Existing Garmin dictionary transformations remain behind
normalization to retain the tested parsing behavior. `RecordStore.write` is the
persistence boundary; collectors do not issue database queries or mutations.

Psycopg is used directly instead of an ORM. SQL migrations are sufficient for
this schema and run separately from ingestion. Applied migration files must not
be edited. The five migrations introduce:

1. Provenance (`persons`, `sources`, `devices`), structured `records`, the
   seven-day `samples` hypertable, query view, synchronization and import progress.
2. Safe numeric extraction for analytics without coercing text to zero.
3. A separate legacy archive preserving original identities and field documents.
4. Requested synchronization ranges, including failed oldest days.
5. Device references from records and samples to the relational device table.

Identity is scoped by provider/account, dataset and device. Hypertable uniqueness
includes its time partition column. JSONB retains structured, boolean, numeric,
text and null values; original tags and timestamps remain available. UTC
microseconds support PostgreSQL queries while a nanosecond remainder prevents
legacy precision collisions. Calendar date and timezone metadata support local
reporting and DST. Known legacy date-only Garmin encodings recover calendar dates;
unknown source timestamps are not reinterpreted.

Synchronization commits each dataset/day with its data. Failed days retain their
requested range; recent days overlap for late changes. Automatic retries can skip
older completed datasets, while explicit backfills recheck them. Account-export
checks cannot certify online API completeness. A successful empty API response
means an availability check, not proof that historical sensor coverage exists.

Strength snapshots are fetched before persistence and replaced transactionally,
including empty snapshots. Activity boundary corrections update the existing
record. Other records merge fields on upsert; missing source observations do not
imply deletion. Generic source-tag changes can still change identity and require
reconciliation. No retention or automatic raw-data deletion policy is installed.

The Apache-only Timescale image keeps the default deployment independent of
optional columnstore functionality. COPY staging, continuous aggregates and
compression are intentionally deferred pending workload measurements and an
explicit edition/features decision. JSONB flexibility costs storage and query CPU;
frequently queried fields can be promoted to typed tables/indexes later.

## Compatibility changes requiring operator attention

- Normal ingestion requires PostgreSQL + TimescaleDB. Existing Influx settings,
  datasource UIDs and third-party/custom InfluxQL dashboards do not carry over.
- Use the new checkout/image; the old published Influx ingestion image is not a
  PostgreSQL runtime. Publishing a new release/image has not been performed.
- Keep `ACCOUNT_ID`, `PERSON_ID`, timezone and per-account token storage deliberate
  and stable. The dashboard Account selector separates queries, not authorization.
- `AUTO_DATE_RANGE` and `TAG_MEASUREMENTS_WITH_USER_EMAIL` are retired. Use manual
  date ranges and explicit account identity.
- Export output is typed JSONL ZIP instead of flattened CSV. Use pg_dump for a
  complete database backup; the portable export is not a schema restore tool.
- Failed API/FIT acquisition now retains a pending checkpoint instead of silently
  treating a partial activity import as complete. Missing strength metrics remain
  null rather than becoming zero.
- Account-export data is a subset of online API data. Standalone FIT imports can
  have a different activity ID from a later API import of the same workout.
- Existing historical source data stays intact. The new archive adds disk usage
  beyond canonical records and indexes; allow space for both and backups.

## Verification and acceptance status

Prior executed full suite: **426 passed, 99.01% branch-aware coverage**, retaining
the **98%** threshold. The original baseline was 478 tests / 99.89%; obsolete
Influx-version/configuration permutations were replaced with PostgreSQL tests,
not removed to reduce the coverage requirement. Unit-only execution reported
402 passed and 24 integration skips.

All **169** panel/variable queries executed against PostgreSQL and through the
actual Grafana datasource. Database queries were also executed with a non-UTC
session. Fixtures exercise empty/populated responses, null/zero gaps, local-date
markers, DST, strength totals, native field types, account/provider/device
isolation, idempotency, corrections and transaction rollback after a flushed
batch. A real InfluxDB 1.11 HTTP round trip was verified during migration development;
that server-dependent regression was subsequently removed at the user’s request.
Current migration tests use synthetic/mocked sources and real PostgreSQL. See [verification.md](verification.md) for exact commands and scope.

Container and package builds passed. Compose PostgreSQL/migration/Grafana startup,
reader connectivity, five applied schema versions, UID 1000 and writable named
token storage were checked. Compilation, installer shell syntax
and whitespace checks passed. Disposable validation containers were stopped;
their volumes were retained. CI definitions were updated, but a remote CI run was
not triggered or claimed as passed.

These checks do not establish real-account functionality, frontend visual
acceptance, cluster readiness or production-scale operational performance.

## Outstanding work

The following items are open. They are not implied complete by passing tests.

### Required before trusting a production cutover

| ID | Priority | Work / responsible party | Completion evidence |
| --- | --- | --- | --- |
| A1 | Required | Operator: run a real Garmin account through first login/MFA, persisted-token restart, incremental updates and a small manual backfill | Successful source fetches and PostgreSQL records/checkpoints for enabled datasets; restart does not require unnecessary reauthentication; retry behavior checked without deleting data |
| A2 | Required | Operator/reviewer: inspect both dashboards in a browser with real recent and historical data | No Query Inspector errors; correct account/timezone, GPS selection, strength filters/totals, heatmaps, transformations, legends and empty-data behavior |
| A3 | Required | Operator: rehearse backup and restore into separate compatible databases | Restored PostgreSQL schema/data/Timescale extension query correctly; legacy backup recoverable; tokens and Grafana customizations backed up securely |
| A4 | Required for existing installations | Operator: execute the staged migration and final reconciliation from the runbook | Source retained; archive validation passes for the intended accounts/measurements/ranges; canonical counts/trends reviewed; old ingestion stopped before final scan and cutover |
| A5 | Required before publishing a release | Maintainer: review the patch, execute remote CI and choose the breaking-change version/release policy | CI green; release notes identify changed settings, schema, datasource UID, export format and migration limits; new image published and its pull/start path tested |

Do not retire InfluxDB or its backups until A1–A4 apply and pass for the intended
installation. Rollback is a controlled switch back to the preserved old runtime;
records created only in PostgreSQL are not automatically copied back.

### Conditional acceptance and known feature gaps

| ID | Applies when | Outstanding work / limitation | Completion or mitigation |
| --- | --- | --- | --- |
| B2 | Legacy source is InfluxDB 3 | Direct import is unsupported | Implement/test a typed v3 adapter, or follow the documented staging alternative; preserve v3 history until independently validated |
| B3 | Non-default Influx retention policies contain data | Current importer reads the default retention policy | Inventory policies, stage/import each required policy with deliberate provenance and validate all ranges; do not assume measurement enumeration covers them |
| B4 | Old strength rows have conflicting label/value variants | Their authoritative edit order cannot be recovered from Influx timestamps | Keep the archive and re-fetch affected periods from Garmin before trusting reconciled strength statistics |
| B5 | Device labels/source tags changed, activity data was deleted, or both FIT/API imports exist | Full deletion/identity reconciliation is not automatic | Review originals and canonical records; implement explicit source-aware reconciliation where necessary; never deduplicate by timestamp alone |
| B6 | External GarminHomeAssistant battery ingestion is used | The former external Influx writer is outside the Garmin collector | Update that writer/add a PostgreSQL adapter; historical `%` data can migrate, but new readings need a supported writer |
| B7 | Multi-year or high-frequency workloads | Throughput, disk growth and broad-range fixed-interval dashboard queries are not production-benchmarked; individual large FIT files still parse in memory | Measure representative imports/queries, memory and storage before changing chunking, batching or schema |
| B8 | Deployment extends beyond a trusted personal setup | Examples bootstrap ingestion with the database initialization role; source selection is not access control | Provision restricted ingestion credentials separately, configure TLS/access boundaries and review backup/secret ownership; keep Grafana read-only |

### Future expansion, not release blockers

1. Add Oura/weather/sensor connectors only when requested, with provider-specific
   definitions, units, stable identities and appropriate checkpoint granularity.
2. Benchmark typed high-frequency tables and COPY-to-staging upserts when measured
   ingestion or JSONB query overhead justifies them.
3. Add opt-in analytical aggregates or columnstore policies for demonstrated
   workloads, after selecting a compatible Timescale edition and upgrade process.
4. Consider durable error summaries and automated reconciliation reports if
   maintaining larger histories makes logs and the existing sync ledger inadequate.

## Handoff procedure

Start with A1–A3 on a separate test installation, then follow the migration
runbook for A4. Resolve the applicable B items rather than treating them all as
mandatory new features. Update this report and the verification evidence when
an item is completed, including the command/environment or operator observation.
Production acceptance should cite that evidence; code existence alone is not
completion. The implementation and this handoff report are committed together;
`git log -1 -- docs/migration-implementation-report.md` identifies the change.

## Follow-up: remove the legacy test-service requirement

At the user's request, the real InfluxDB HTTP regression and the InfluxDB CI
service were removed. No test endpoint environment variable or separate legacy
server test suite remains. PostgreSQL-backed migration tests and mocked HTTP
response tests still exercise the standalone importer. The importer remains
solely to preserve the documented historical migration path, with no InfluxDB
Python dependency, datasource, deployment service or normal-runtime backend.

Current follow-up verification: **428 passed, zero skips, 99.01% coverage** with
PostgreSQL alone; the new interactive login helper has three tests and stores
tokens outside the repository. Its live authentication remains an operator step.

## Follow-up: stronger coverage

The subsequent coverage expansion passed **439 tests, zero skipped, with 100%
line and branch coverage of the configured Python package** (1,520 statements,
596 branch exits). The gate remains 98%; its scope and exclusions are unchanged.
New tests cover fresh-schema installation, atomic failed DDL upgrades, activity
retry/cache recovery, package execution and the documented module commands. See
the verification report for the detailed scenarios and acceptance boundaries.
Live Garmin and browser acceptance remain open despite complete measured coverage.

## Follow-up: live validation, 2026-10-25

Executed by the operator and assistant against a private, disposable validation
database (TimescaleDB 2.26.4 / PG 17, separate from any existing data):

- **A1 (partly done):** interactive login with MFA via `scripts/garmin_login.py`,
  then `garmin-fetch` with a 4-day manual range using the persisted token (no
  reauthentication). All 13 default datasets produced 25 records, 20,562 samples
  and 52 checkpoints. A repeat run left the counts unchanged (idempotent). The
  repeat run exposed a bug: `get_lifestyle_data` raised `TypeError` when Garmin
  returned `dailyLogsReport: null`. It is fixed with regression tests; the retried
  run succeeded without errors. Still open: a longer historical backfill and a
  long-running (non-manual) incremental loop.
- **A3 (done for PostgreSQL):** `pg_dump -Fc` restored into a second database with
  `timescaledb_pre_restore`/`post_restore`; row counts, five migrations, extension
  version and hypertable matched. Token and Grafana-customization backup are not
  exercised.
- **Grafana:** datasource healthy as the SELECT-only `grafana_reader`; all 169
  queries pass through Grafana against live data. **A2 (browser inspection) is
  still open.**
- Full suite after the fix: 440 passed, 100% coverage.
- A4 and A5 are not performed for production: no cutover, CI run or release.

### Comparison with the running InfluxDB deployment, 2026-10-25

The `main` instance's InfluxDB 1.11 (read-only HTTP) was imported with
`legacy_import` into a scratch database, and its dashboards were compared with
the old Grafana side by side. `legacy_import --validate` passed. Differences found
and fixed, all in row presence rather than values:

- **Null rows.** InfluxDB never returned a point for a field it lacks (for example
  the activity END marker has no `calories`); the JSONB model returned a NULL row.
  Means were diluted, bar charts had an extra bar and lines had gaps. The 38
  single-field series in 17 panels now filter `value IS NOT NULL`. Activity
  calorie means over 7/30/90 days equal InfluxQL exactly.
- **Empty hourly buckets.** InfluxQL `GROUP BY time(1h)` returns empty hours as
  nulls and Sleep Regularity paints them gray. The query now generates every
  bucket in the dashboard range.
- Operator checks against Garmin Connect: activity calories/BMR agree. Sleep
  stages agree. On the 2026-10-25 DST-change night Connect shows the bed time
  with one winter offset (1:16) while the dashboard shows the real wall clock
  (02:16); the stored UTC timestamps are identical to InfluxDB's.
- Known upstream quirk, intentionally unchanged: Sleep Regularity takes the hourly
  median of a 0-3 stage level on a 0-0.6 color scale, so it cannot distinguish
  light, REM and awake sleep. The sleep-movement series is one hour wider than the
  stage series on each side on the DST night (same on the old deployment).
- `DemoPoint` (upstream demo writes, ~58k rows) and the `%` battery measurement were
  imported; exclude `DemoPoint` with `--measurement` in a real cutover.
