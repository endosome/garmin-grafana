# Migrate InfluxDB to PostgreSQL + TimescaleDB

This is a staged copy, not an in-place conversion. The importer never deletes
InfluxDB data, containers, or volumes. Keep your existing deployment and backups
until you have checked the new deployment. Do not run volume-removal commands.

## Supported sources and prerequisites

The HTTP importer supports InfluxDB 1.x, tested with the previous default 1.11.
It reads every measurement, including custom/unknown ones, without exclusions.
It preserves tags, JSON-native field types and UTC nanosecond timestamps in a
legacy archive, while also mapping records for normal dashboards. Edited strength
rows may reconcile into fewer canonical sets; the archive keeps every original
legacy identity. The archive consumes additional disk space; budget for both it
and normal records plus indexes and backups.

Requires this checkout, Python 3.13 with uv (or the newly built container), Docker
Compose for the example target, and read access to your old Influx HTTP endpoint.
Use a read-only legacy database user. Do not expose database ports publicly.
No InfluxDB Python client is required; the importer uses the HTTP streaming API.

**InfluxDB 3 is not supported by this importer.** Its query/export APIs and tag
metadata differ; a v1 streaming query endpoint must not be assumed equivalent.
Retain a vendor-supported complete v3 backup/export. If you can restore a typed
line-protocol export into a disposable InfluxDB 1.x instance with original tag
metadata, import from that instance and validate it against the original export.
That conversion is not provided or verified here. Otherwise re-fetch Garmin data
into PostgreSQL for available periods, keep v3 accessible for unavailable history,
and implement a typed v3 export adapter before decommissioning it. CSV alone
cannot reliably distinguish tags from fields or preserve all timestamp/type
semantics. Do not discard v3 data based solely on matching dashboard trends.

## 1. Back up the old installation

Record its Compose project name, volume names, database name, retention policies,
account configuration and oldest/newest dates. Save its Compose file, environment,
Grafana dashboards, and Garmin tokens securely. The commands below use an example
old container named `influxdb`; substitute the actual container name.

```bash
# Non-destructive portable InfluxDB 1.x backup. May need authentication flags
# according to your old server configuration. Keep those secrets out of logs.
docker exec influxdb influxd backup -portable -database GarminStats /tmp/garmin-backup
docker cp influxdb:/tmp/garmin-backup ./garmin-influx-backup
```

Check backup command status and test restoration to a separate legacy instance.
If you use non-default retention policies, this importer currently queries only
the database's default retention policy. Export/restore each other policy into a
separate staging database and use its own migration ID/account mapping; preserving
conflicting records across policies needs separate provenance accounts. Do not
assume SHOW MEASUREMENTS proves all retention policies were copied.

## 2. Start a separate PostgreSQL target

Use this checkout in a **different directory** from the old deployment. The new
Compose project name is `garmin-timescale`; verify it differs from the old stack.
The file defines fresh postgres_data, garmin_tokens, and grafana_data volumes.
It never references an old Influx volume. Initialize `.env` from `.env.example`,
replace all three passwords, and choose a stable ACCOUNT_ID and USER_TIMEZONE.
Do not change the old deployment's environment in place.

```bash
cp .env.example .env
chmod 600 .env
# Edit .env before continuing.
docker compose -f compose-example.yml build
docker compose -f compose-example.yml up -d postgres
docker compose -f compose-example.yml run --rm migrate
```

Migrations install TimescaleDB and the versioned schema. The separate migration
step is required. It also creates the Grafana SELECT-only role using
GRAFANA_DB_PASSWORD. There are no retention/deletion policies. Keep ingestion
stopped while doing the initial copy.

## 3. Configure legacy access and preview

Run the Python importer on the host to reach the old database and publish the
new PostgreSQL port only on loopback with a temporary Compose override:

```yaml
# compose-migration.yml (do not commit secrets)
services:
  postgres:
    ports:
      - '127.0.0.1:55432:5432'
```

```bash
docker compose -f compose-example.yml -f compose-migration.yml up -d postgres
uv sync --locked
export PGHOST=localhost PGPORT=55432 PGDATABASE=garmin PGUSER=garmin
read -rsp 'Target PostgreSQL password: ' PGPASSWORD; echo; export PGPASSWORD
export LEGACY_INFLUX_URL=http://localhost:8086
export LEGACY_INFLUX_DATABASE=GarminStats
export LEGACY_INFLUX_USERNAME=legacy_readonly
read -rsp 'Legacy password: ' LEGACY_INFLUX_PASSWORD; echo; export LEGACY_INFLUX_PASSWORD
uv run python -m garmin_grafana.legacy_import \
  --account default --migration-id old-influx-initial --dry-run
```

Use the same `--account` as new ingestion's ACCOUNT_ID, replacing `default`
consistently. The dry run streams and validates every record without connecting
to PostgreSQL. Errors stop the operation rather than silently dropping data.
For a quick preview use `--measurement HeartRateIntraday --start <unix_ns>
--end <unix_ns>` to restrict the interval (start inclusive, end exclusive).

Multi-user databases: if multiple `User_ID` tags exist, the importer refuses to
combine them. Repeat with `--legacy-user exact-tag --account corresponding-id`
for each user and a distinct migration ID. Untagged legacy data cannot be
attributed automatically: import it separately from a staging database with a
known owner. Never assign another person's data to your own account to suppress
this guard. Device labels are retained; old data often has no physical device ID.

## 4. Import and resume

```bash
uv run python -m garmin_grafana.legacy_import \
  --account default --migration-id old-influx-initial
```

Rows stream from InfluxDB in chunks of 1,000 and are written in bounded batches.
Progress advances after complete 24-hour windows. `--window-hours 1` reduces
replay after interruption for high-frequency datasets. Restart with the **same**
arguments and migration ID to resume. A partial window is replayed safely;
upserts prevent duplicates. A migration ID identifies a particular source and
query scope: do not reuse it with a different source, account filter or earlier
range. Use a new ID to re-scan old windows after source corrections.

Do not run two importers concurrently with the same migration ID. Normal runtime
health records have their own synchronization ledger; importing legacy history
does not assert that Garmin's online datasets have been fetched successfully.
API ingestion will initially reconcile recent days; use explicit historical
ranges for any old period that needs API correction.

## 5. Validate completeness and fields

```bash
uv run python -m garmin_grafana.legacy_import \
  --account default --migration-id old-influx-initial --validate
```

Validation ignores resume progress and re-reads **all selected source records**,
checking each archived identity, tag document and field document. It fails on a
missing/different record and never advances migration progress. Use the same
range, measurement and user filters as import. This checks source coverage and
values; it does not prove that canonical dashboards have identical semantics.

Inspect archive/canonical counts and ranges in PostgreSQL:

```sql
SELECT s.provider, s.account, a.dataset, count(*), min(a.time), max(a.time)
FROM legacy_archive a JOIN sources s ON s.id=a.source_id
GROUP BY s.provider,s.account,a.dataset ORDER BY 1,2,3;
SELECT source_id,dataset,count(*),min(time),max(time)
FROM all_records GROUP BY source_id,dataset ORDER BY 1,2;
SELECT * FROM legacy_progress ORDER BY source_id,measurement;
```

Compare with source counts over the same ranges. Field-level validation above
is stronger than comparing counts alone. Canonical strength counts can be lower
where the old database contained stale label variants. InfluxDB does not retain
the edit order of those variants, so the importer cannot infer which label or
value is authoritative; re-fetch the affected activity periods from Garmin before
relying on canonical strength statistics. `legacy_archive` remains
unchanged by future Garmin snapshot replacement. Queries use microsecond UTC
`time`, with `nanoseconds` retaining the remaining precision; add both when
comparing exact timestamp identities. Known Garmin date-only datasets (race predictions, fitness age, VO2 max, hill/
endurance scores, lactate threshold, hydration and lifestyle) recover their source
calendar date from the old UTC-midnight encoding. Other legacy datasets lack
fetch-day metadata and use their original absolute timestamp for grouping.
Textual numeric legacy fields stay text in
JSONB; `health_number` returns null for those fields rather than guessing units
or coercing invalid strings to zero.

## 6. Cut over with a final reconciliation

1. Stop **only the old ingestion application**, keeping its InfluxDB running.
   Record the stop time. Do not stop/remove its database or delete volumes.
2. Re-run the importer with a **new migration ID**, e.g. `old-influx-final`, to
   re-scan all windows for late records and corrections. For large databases you
   can restrict this to a known overlap only if you know older data was unchanged.
3. Re-run `--validate` against the final source snapshot. Resolve every failure.
4. Authenticate the new ingestion process interactively as described in the
   README. You can securely copy old compatible token files into the new token
   volume instead. Keep account/token ownership consistent.
5. Start new ingestion and Grafana:
   `docker compose -f compose-example.yml up -d`.
6. Inspect application logs and `sync_days` / `sync_ranges` for pending holes.
   Source/API failures retain their requested range; use the same manual range
   to retry an explicit backfill.
7. Select the proper Account in both dashboards. Check recent and multi-year
   ranges, daily steps, HR/sleep/HRV, activity GPS selection, strength totals,
   corrected exercise labels, HR zones and heatmaps. Set local TimeZone. Run
   `uv run python scripts/validate_dashboard_queries.py` using the target PG*.
8. Keep the legacy database and its verified backup until you are satisfied with
   historical completeness, units, identity reconciliation, and backups/restores.

A new source/API record with a changed identity tag may coexist with its old
legacy version, except for strength snapshots which explicitly replace activity
scopes. Device renaming, standalone FIT IDs and changed GPS lap selectors may
need manual reconciliation. Archive preservation makes those decisions
reviewable. Never delete canonical duplicates using time alone.

## Troubleshooting and recovery

- Connection refused: check health status, loopback port override, and PG* values.
  `PGPASSWORD` is the target password; legacy credentials use separate variables.
- Extension/role permission error: run explicit migration with an administrator,
  then use a restricted ingestion role if desired. Ingestion itself does no DDL.
- Checksum mismatch: restore the original migration file, add a new migration for
  schema changes; never overwrite the stored checksum to silence it.
- Import/validation stops: the incomplete window remains resumable. Inspect the
  failing type/record in a controlled environment, fix the adapter or source, and
  retry. Do not skip errors to obtain a success count. No health values or
  credentials are intentionally printed by importer progress logging.
- Empty panels: verify Account, date range, timezone, datasource UID
  `garmin_postgres`, PostgreSQL reader permissions, and Query Inspector. Custom
  user-created InfluxQL dashboards need manual SQL conversion.
- Old battery panel: external GarminHomeAssistant writers formerly sent `%`
  measurements to InfluxDB. Historical values migrate, but their writers need
  a PostgreSQL connector before new readings will appear. This integration is
  outside Garmin Connect and was never fetched by this application.

Rollback: stop new ingestion, restart the old ingestion application using its
saved configuration and old volumes. PostgreSQL remains intact for analysis and
retry. Health data created only in PostgreSQL during cutover is **not** copied
back automatically; re-fetch the overlap into the old application where available,
or keep both stores until it is reconciled. Restore the old backup only to a
separate instance unless you explicitly choose a destructive recovery yourself.
Do not drop either database as part of rollback.

Back up the new database with the README's pg_dump command and test restoration
before retiring the old deployment. Compression/columnstore and long-range
aggregates are future opt-in workload decisions, not migration prerequisites.
