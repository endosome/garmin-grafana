# Development

Use Python 3.13 and uv. `uv sync --locked` installs the editable package and dev
tools. Importing collectors performs no network request. Unit tests pin the
collector environment and mock persistence; no Garmin credentials are required.

```bash
uv run --locked pytest
```

Integration tests skip unless `TEST_DATABASE=1`. To run the full coverage gate
(unchanged at 98%), use disposable databases, never your real health database:

```bash
docker run -d --name garmin-test-db -e POSTGRES_PASSWORD=test-only-password \
  -e POSTGRES_DB=garmin -p 127.0.0.1:55439:5432 timescale/timescaledb:2.26.4-pg17-oss
docker run -d --name garmin-test-influx -p 127.0.0.1:58089:8086 influxdb:1.11
# Wait for pg_isready and the Influx /ping endpoint.
export PGHOST=localhost PGPORT=55439 PGUSER=postgres PGDATABASE=garmin
export PGPASSWORD=test-only-password TEST_DATABASE=1 TEST_INFLUX_URL=http://localhost:58089
uv run --locked pytest --cov --cov-report=term-missing
uv run --locked python scripts/validate_dashboard_queries.py
```

Tests insert synthetic data under randomized source identities. They exercise
real hypertables, idempotent migrations, upserts, chunked import, archive
validation, snapshot rollback, source isolation, progress holes, and all
provisioned SQL queries with empty and representative data. CI starts both
services. Unit-only coverage is lower because it omits real persistence tests;
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

Run `make -C k8s render lint` (Helm 3 required) after modifying deployment or
Grafana artifacts. Dashboard copies in the chart must match canonical JSON files.
`docker compose -f compose-example.yml config --quiet`, `docker build .`, and
`uv build` validate configuration, runtime image and package distribution.

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
