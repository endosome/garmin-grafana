# Garmin Grafana

Fetch Garmin Connect health and fitness data, retain it in PostgreSQL with
TimescaleDB, and explore long-term trends in Grafana. Includes daily statistics,
heart rate, steps, stress and Body Battery, respiration, sleep, HRV, body
composition, activities, FIT/TCX GPS tracks, swimming, cycling dynamics, strength
sets and HR zones, and Garmin account-export / FIT imports.

Originally created by [Arpan Ghosh](https://github.com/arpanghosh8453).
Licensed under [BSD-3-Clause](LICENSE).

**Upgrading an existing installation:** follow the
[InfluxDB migration guide](docs/migration-influxdb-to-timescaledb.md) first.
This version uses PostgreSQL only. Keep your old InfluxDB database and volumes
until you have verified the import and dashboards.

## Fresh installation

Requires Docker Engine with Compose v2, about 2 GB available RAM for a modest
personal deployment, disk space for your health history, and a Garmin account.
The example pins TimescaleDB 2.26.4 Apache-only on PostgreSQL 17 and Grafana 13.0.2.
It builds ingestion from this checkout, rather than pulling the old Influx image.
The optional hourly heatmap plugin needs internet access during installation.

```bash
git clone https://github.com/endosome/garmin-grafana.git
cd garmin-grafana
cp .env.example .env
chmod 600 .env
# Edit .env: replace all three passwords, choose USER_TIMEZONE and ACCOUNT_ID.
docker compose -f compose-example.yml build
docker compose -f compose-example.yml up -d postgres
docker compose -f compose-example.yml run --rm migrate
# First login may ask for Garmin credentials and MFA interactively.
docker compose -f compose-example.yml run --rm garmin-fetch-data
# After a successful fetch, Ctrl+C; the tokens remain in the named volume.
docker compose -f compose-example.yml up -d
docker compose -f compose-example.yml logs -f garmin-fetch-data
```

Alternatively, `./easy-install.sh` generates random passwords in a new `.env`,
builds, initializes, and starts the same stack. It leaves an existing `.env`
untouched. Python 3 is required by this convenience script.

Open [Grafana](http://localhost:3000), username `admin`, password from `.env`.
Both dashboards and the `Garmin PostgreSQL` data source are provisioned. Select
your **Account** on each dashboard. Set **TimeZone** to the local IANA timezone
for daily aggregation. Grafana binds to loopback by default; use an SSH tunnel
or configure an authenticated TLS reverse proxy for remote access.

The default Compose network keeps PostgreSQL private. Database, Garmin tokens,
and Grafana settings use independent persistent named volumes. Restarting or
rebuilding containers keeps them. Never use `docker compose down -v` on a live
installation. Changing the Compose project name selects different volumes.

## Configuration

Compose reads `.env`. Local commands read standard environment variables. The collector also reads the
existing optional `override-default-vars.env` (which overrides environment values).
Do not commit secrets. Base64 is encoding, not encryption.

| Setting | Meaning / default |
| --- | --- |
| `PGHOST`, `PGPORT` | PostgreSQL host and port; local Python: localhost, 5432 |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD` | Database, migration/ingestion user and password; garmin, garmin, no password default |
| `PGSSLMODE` | libpq TLS mode; `prefer` locally, `disable` on Compose's private network; use `verify-full` across untrusted networks |
| `PGCONNECT_TIMEOUT` | Connection timeout in seconds, 10 |
| `PGREADER_USER`, `PGREADER_PASSWORD` | Optional migration bootstrap for a SELECT-only Grafana login; default name grafana_reader |
| `GRAFANA_DB_PASSWORD` | Compose password for the grafana_reader role |
| `GRAFANA_PASSWORD`, `GRAFANA_USER`, `GRAFANA_PORT` | Compose Grafana administrator password, username (admin), local port (3000) |
| `ACCOUNT_ID`, `PERSON_ID` | Stable account identity (default email or default); person defaults to account |
| `USER_TIMEZONE` | IANA zone, e.g. Europe/Budapest; explicit UTC fallback, DST-aware scheduler |
| `GARMINCONNECT_EMAIL`, `GARMINCONNECT_BASE64_PASSWORD` | Optional login credentials; otherwise interactive on first login |
| `TOKEN_DIR` | Token directory, `~/.garminconnect`; isolate per account |
| `GARMINCONNECT_IS_CN` | Use Garmin China, False |
| `GARMIN_DEVICENAME`, `GARMIN_DEVICEID` | Optional device configuration; otherwise detect from latest device |
| `UPDATE_INTERVAL_SECONDS` | Poll interval, 300; checks dataset progress and refreshes recent days even without a new watch upload |
| `MANUAL_START_DATE`, `MANUAL_END_DATE` | Inclusive YYYY-MM-DD historical range; setting start runs once, end defaults to today |
| `FETCH_SELECTION` | Comma-separated enabled datasets; see below |
| `ACTIVITY_TYPE_FILTER` | Comma-separated Garmin activity type keys; empty imports all |
| `KEEP_FIT_FILES`, `FIT_FILE_STORAGE_LOCATION` | Retain original downloaded recordings; False, ~/fit_filestore |
| `ALWAYS_PROCESS_FIT_FILES` | Parse indoor activities without GPS too, True; set False to parse only activities with GPS |
| `FORCE_REPROCESS_ACTIVITIES` | Reprocess activity files for corrections, True; disabling may miss edits |
| `REQUEST_INTRADAY_DATA_REFRESH` | Request older Garmin intraday files (older than `IGNORE_INTRADAY_DATA_REFRESH_DAYS`), False. Garmin allows about 31 requests a day per account; a refused request stops a bulk fetch (`MANUAL_START_DATE`) at that date with an incomplete exit, so rerun the same range the next day |
| `IGNORE_INTRADAY_DATA_REFRESH_DAYS` | Recent days excluded from that request, 30 |
| `LACTATE_THRESHOLD_SPORTS` | Sport list, RUNNING |
| `RATE_LIMIT_CALLS_SECONDS`, `FETCH_FAILED_WAIT_SECONDS` | Delay between days and on rate limits; 5, 1800 |
| `MAX_CONSECUTIVE_500_ERRORS` | Skip a repeatedly failing day in this run after 10 attempts; its pending checkpoint remains |
| `IGNORE_ERRORS` | Continue past unexpected failed days, False; failed days remain pending |
| `LOG_LEVEL` | INFO; DEBUG is useful for local troubleshooting |

Default selection:
`daily_avg,sleep,steps,heartrate,stress,breathing,hrv,fitness_age,vo2,activity,race_prediction,body_composition,lifestyle`.
Additional selections: `lactate_threshold,training_status,training_readiness,hill_score,endurance_score,blood_pressure,hydration,solar_intensity,cycling_dynamics`.
`cycling_dynamics` augments `activity`; it is not a separate API dataset.
When using Compose, pass less common Python settings with `docker compose run -e
SETTING=value ...` or add them to the service environment.

`AUTO_DATE_RANGE` and `TAG_MEASUREMENTS_WITH_USER_EMAIL` have been retired.
Use the explicit manual date range and stable `ACCOUNT_ID`. Multiple accounts
use separate ingestion processes and token volumes. Do not change an established
ACCOUNT_ID casually: it creates a different provenance source, not a rename.

## Backfills and manual imports

```bash
docker compose -f compose-example.yml run --rm \
  -e MANUAL_START_DATE=2020-01-01 -e MANUAL_END_DATE=2020-12-31 garmin-fetch-data
```

Safe to repeat; records are upserted. Failed dataset/day ranges stay pending.
The automatic scheduler refreshes two recent days and retries older holes.
Garmin does not make every historical dataset available for every account;
empty successful responses record an availability check. Use manual backfills
again after requesting historical files. A change to activity filters can require
an explicit manual backfill over the newly included period.

See [manual account-export and FIT imports](docs/manual-import-instructions.md).
Those imports do not require online Garmin login. The account export contains
only a subset of the online API data and cannot replace strength API snapshots.

## Backups and upgrades

```bash
# The -Fc archive includes schema and data; keep it encrypted/off-host.
docker compose -f compose-example.yml exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > garmin.dump
```

Back up Garmin tokens separately and treat them as credentials. Back up Grafana's
volume if it contains custom dashboards. Test restoration on a separate database;
install the compatible Timescale extension before `pg_restore`. Keep the complete
`.env` securely. A volume snapshot must be PostgreSQL-consistent, not a copy of
files from a running database.

For app upgrades: take a backup, build the new checkout, run `migrate`, restart
ingestion/Grafana. SQL migrations are checksummed and transactional; do not edit
an applied migration. PostgreSQL major upgrades need a supported dump/restore or
pg_upgrade procedure; changing the image tag alone is insufficient. No raw-data
retention, compression, or automatic deletion policy is installed.

## Development and other deployments

Python 3.13 and uv are required for development. See [developer instructions](docs/development.md),
and [architecture](docs/architecture.md).
See the [implementation and outstanding-work report](docs/migration-implementation-report.md)
for the change inventory and production acceptance checklist.
See the [executed verification report](docs/verification.md) for test results and
unverified acceptance boundaries.

## Portable data export

The former Influx CSV exporter is replaced by a bounded PostgreSQL JSONL ZIP
export that preserves typed and nested values, provenance and nanoseconds:

```bash
uv run python -m garmin_grafana.export --account default \
  --start-date 2020-01-01 --end-date 2026-12-31 --output health-export.zip
```

Date-only end values include the whole UTC day; `--last-n-days 30` overrides the
date range. This portable export is account-scoped and contains health data;
protect it accordingly. Use pg_dump for a complete restorable database backup.
