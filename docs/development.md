# Development

Use Python 3.13 and uv. `uv sync --locked` installs the editable package and dev
tools. Importing collectors performs no network request. Unit tests pin the
collector environment and mock persistence; no Garmin credentials are required.

```bash
uv run --locked pytest
```

Integration tests skip unless `TEST_DATABASE=1`. The shared database fixture
applies migrations and writes synthetic data/roles, so this is an explicit opt-in
to using a disposable database. Neither database tests nor unit tests require Garmin credentials. Enabled tests
fail on connection or assertion errors rather than silently skipping. CI provides
PostgreSQL and its settings. No InfluxDB server is required or started by tests.
An ordinary `uv run pytest` is a unit-only check unless the PostgreSQL integration
settings are supplied.

To run the full coverage gate
(unchanged at 98%), use a disposable database, never your real health database:

```bash
docker run -d --name garmin-test-db -e POSTGRES_PASSWORD=test-only-password \
  -e POSTGRES_DB=garmin -p 127.0.0.1:55439:5432 timescale/timescaledb:2.26.4-pg17-oss
# Wait for pg_isready.
export PGHOST=localhost PGPORT=55439 PGUSER=postgres PGDATABASE=garmin
export PGPASSWORD=test-only-password TEST_DATABASE=1
uv run --locked pytest --cov --cov-report=term-missing
uv run --locked python scripts/validate_dashboard_queries.py
```

Tests insert synthetic data under randomized source identities. Fresh-schema
tests also create and drop randomly named temporary databases, so the disposable
test role needs CREATEDB as well as the role/extension privileges used by the
existing integration tests. Only databases created by those tests are dropped. They exercise
real hypertables, idempotent migrations, upserts, chunked import, archive
validation, snapshot rollback, source isolation, progress holes, and all
provisioned SQL queries with empty and representative data. CI starts PostgreSQL. Unit-only coverage is lower because it omits real persistence tests;
the coverage gate applies to the complete suite.

For local operation, export PG* credentials and ACCOUNT_ID/USER_TIMEZONE, then:

```bash
uv run python -m garmin_grafana.migrate
uv run garmin-fetch
```

Apply schema changes by adding the next numbered SQL file in
`src/garmin_grafana/migrations/`. The migration command checks applied file hashes,
locks concurrent migrators, and commits all pending changes atomically. Ingestion
never creates or changes tables. There are no automatic downgrade migrations;
restore a tested backup into a separate database to recover from a bad upgrade.
`PGREADER_PASSWORD` optionally creates/updates Grafana's SELECT-only login during
explicit migration bootstrap. The migration role needs extension/DDL/role-grant
permissions; it can be provisioned separately by the database administrator.

The domain boundary is `Record` / `RecordBatch` and `RecordStore.write`. Existing
Garmin transformations remain dictionary builders to preserve the extensively
tested parsing behavior; normalization converts them at the ingestion boundary.
`values` preserves native JSON values, and `tags` preserves source attributes.
Unknown numbers must remain null/absent, never converted to zero. Timestamps
require an offset. Daily orchestration attaches source calendar dates and zone.

For a future connector, use its provider name and an explicit account identity,
return Records with stable source IDs and device provenance, document each
metric's unit/definition, and synchronize with a provider-specific dataset/day
checkpoint. Do not reuse Garmin metric names for different algorithms. A
high-frequency connector may need a finer checkpoint interval and a typed sample
table: add these when implementing that connector. Keep extraction outside
persistence, with atomic data/checkpoint writes. Synthetic provider isolation is
already covered in integration tests.

`docker compose -f compose-example.yml config --quiet`, `docker build .`, and
`uv build` validate configuration, runtime image and package distribution.

`tests/personas.py` builds deterministic synthetic athletes in the record shapes
the collectors write: a ski tourer (pre-season build-up, last winter on snow), a
healthy-lifestyle runner (a year of daily data, intraday series, a GPS run, a sync
gap, the DST day and a partly synced today), a gym-goer (progressive overload and
every set the Strength dashboard special-cases), a cyclist imported from InfluxDB
(no local dates, Los Angeles time, Home Assistant battery data) and a newcomer with
five days of data. `tests/test_personas.py` checks dashboard panels against values
the personas compute in plain Python, and that accounts never see each other's
data. To browse them in Grafana, load them into a disposable database with
`uv run --locked python scripts/load_personas.py`; each persona is its own Account
and "today" is 2026-10-28.

For Grafana smoke testing, start the fresh Compose stack, select an Account, and
inspect both dashboards over recent and multi-year windows. Verify GPS activity
selection, local-day boundaries, heart-rate distribution, strength category/label
filters, totals, latest sets, and heatmaps. Inspect Query Inspector for errors.
The SQL validator substitutes deterministic Grafana macro values; browser layout,
third-party plugin rendering and every Grafana transformation need UI inspection.

To exercise the actual Grafana datasource and provisioning after startup:

```bash
GRAFANA_URL=http://localhost:3000 GRAFANA_PASSWORD=your-local-admin-password \
uv run python scripts/smoke_grafana.py
```

Use a protected environment or prompt rather than putting real passwords in
shell history. This command makes read-only queries using the provisioned reader
role; it does not authenticate to Garmin or alter health data.

## Interactive Garmin acceptance login

Run this in your own terminal on the workspace machine; passwords use a hidden
prompt, and MFA is entered locally. Do not put secrets in chat or shell arguments.

```bash
cd /home/debian/garmin-grafana
uv run --locked python scripts/garmin_login.py
```

The default token directory is
`~/.local/share/garmin-grafana/live-validation/tokens`, outside the repository.
The directory uses mode 700 and new files use a private umask. `--token-dir`
selects another directory. This command only authenticates and persists tokens;
it does not fetch measurements or connect to PostgreSQL. Tokens are credentials
and may be refreshed in place. After success, provide only the token directory
path and your IANA timezone to coordinate a bounded live ingestion check into a
separate validation database. Never add this token directory to version control.
