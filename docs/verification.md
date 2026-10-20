# Verification report

See the [implementation and outstanding-work report](migration-implementation-report.md)
for the consolidated change inventory and prioritized acceptance checklist.

Executed in the repository workspace on 2026-10-20 against isolated, synthetic
data. Existing user services/databases and historical volumes were not modified.

## Automated results

| Check | Executed result |
| --- | --- |
| Pre-change `uv run pytest --cov --cov-report=term` | 478 passed; 99.89% coverage |
| Full migrated suite, including real database and legacy HTTP tests | 426 passed; 99.01% coverage; unchanged 98% gate |
| Unit run without integration environment | 402 passed, 24 integration tests skipped |
| `python scripts/validate_dashboard_queries.py` | All 169 panel/variable queries executed successfully in PostgreSQL |
| `python scripts/smoke_grafana.py` | Both dashboards provisioned, datasource healthy; all 169 queries passed through Grafana |
| `docker build -t garmin-grafana:timescale .` | Passed; non-root runtime image built from current source |
| `docker compose ... config --quiet` | Passed |
| Compose PostgreSQL + migration + Grafana startup | Passed; database healthy, migration container exited successfully |
| Application image / named token-volume probe | UID 1000; five applied migrations; persistent token volume writable |
| `uv build` | Wheel and source distribution built; SQL migrations included |
| `python -m compileall -q src scripts` | Passed |
| `bash -n easy-install.sh` | Passed |
| `helm lint k8s` / `helm template garmin k8s` | Passed; standalone manifest regenerated |
| `git diff --check` | Passed |

Full suite command (test-only passwords and endpoints):

```bash
TEST_DATABASE=1 TEST_INFLUX_URL=http://localhost:58089 \
PGHOST=localhost PGPORT=55439 PGDATABASE=garmin PGUSER=postgres \
PGPASSWORD=test-only-password \
uv run --locked pytest -q --cov --cov-report=term-missing
```

Services: TimescaleDB 2.26.4 Apache-only / PostgreSQL 17, InfluxDB 1.11,
Grafana 13.0.2. Grafana registered hourly heatmap plugin 2.0.1 successfully.
Queries were checked with both empty and synthetic populated data. PostgreSQL
and Grafana datasource responses confirmed time-series fields, string activity
states, strength aggregate numeric types, and legacy `Time` table field names.
The SQL fixtures cover local-day markers, DST transitions, null/zero gaps,
strength volume, and original JSON-native types. Real database tests include
rollback after a flushed 1,000-record batch, not only failures before insertion.

Obsolete Influx 1/3 client/configuration permutations were replaced by real
PostgreSQL tests. Garmin parsing, native FIT construction, GPS/TCX fallback,
cycling, strength transformation, daily datasets, login/MFA and account-export
regressions remain. Behavior changes are explicit: failed APIs no longer certify
completion, missing strength metrics remain null, and failed FIT/TCX acquisition
forces a retry rather than silently declaring a complete activity sync.

## Acceptance boundaries

Implemented and exercised: sole PostgreSQL runtime; fresh Timescale schema;
source/device provenance; existing Garmin categories via fixtures; manual
historical ingestion; upserts and correction handling; dataset/day checkpoints
and failed oldest-day coverage; transactional strength snapshots; all bundled
SQL dashboards and variables; persistent deployment artifacts; non-destructive
streamed Influx 1.x migration, resume, archive and field validation; source/code
package builds; tests and operational documentation.

Not independently verified:

- Live Garmin authentication/collection against a real account. Tests use mocked
  Garmin responses and real synthetic FIT files; no credentials were requested.
- Browser rendering, all Grafana frontend transformations, geographic basemap
  availability, or third-party panel visual appearance. Grafana server/API and
  plugin registration checks are not a substitute for those UI checks. Follow
  the developer guide's reproducible manual smoke checklist.
- Actual Kubernetes scheduling, PVC storage-driver behavior or MFA token transfer.
  Helm lint/render and Compose execution do not establish cluster readiness.
- Multi-year production-scale import throughput, storage growth, backups restored
  from a real installation, or upgrades between PostgreSQL major versions.
- InfluxDB 3 import and non-default retention-policy conversion; see the migration
  guide's explicit limitations and staging alternatives.

Operational limits: legacy ambiguous strength variants need online reconciliation;
standalone FIT imports may have a different ID from an API import; device renaming
and deleted GPS samples need explicit reconciliation. The legacy archive retains
original identities even after canonical strength replacement. No source data is
deleted, no raw-data expiry is installed, and no user migration was run.

Future work: implement provider-specific connectors with stable identities and
unit definitions, benchmark typed high-frequency streams before adding COPY
staging, and add opt-in aggregates/columnstore only for measured query workloads
and a deliberately chosen Timescale edition. Large FIT activities still parse in
memory as in the original project; the historical database importer is bounded.

## Integration skip investigation and rerun

The last unit-only check before the implementation commit reported 402 passed
and 24 skips because the required opt-in environment was absent. There are two
explicit skip guards:

- `tests/test_storage.py::db` requires `TEST_DATABASE=1`. It applies schema
  migrations and writes synthetic records/roles, so it must target a disposable
  database. This shared fixture also serves legacy-import and export tests.
- `tests/test_legacy_import.py::test_real_legacy_http_roundtrip` additionally
  requires `TEST_INFLUX_URL`; it creates a synthetic legacy database and imports
  from the actual InfluxDB HTTP service.

These are service-availability configuration gates, not credential requirements
or expected-failure markers. With the flags supplied, failed connections and
assertions fail the tests; they do not fall back to skipping. The CI workflow
already starts both services and supplies both settings.

After the user requested investigation, the existing isolated test containers
were restarted and the full command above was executed again against the
committed implementation. Result: **426 passed, zero skipped, 99.01% coverage**
in 14.88 seconds. All 24 previously skipped tests executed. No Garmin credentials
were used, and no production database was touched. The disposable test services
were stopped again after the checks.

Next acceptance step: live Garmin authentication and ingestion into a separate
validation database, followed by a repeated import, persisted-token restart and
browser inspection of both dashboards. Use a protected local copy of compatible
Garmin tokens or interactive login; never put token contents/passwords in chat,
commits or test fixtures. Supply only the token path, IANA timezone and desired
small date range to coordinate the run. A copied token store may be refreshed by
the client; keep the original store separate. Live health values must remain in
the private validation database rather than being included in the test report.
