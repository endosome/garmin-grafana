# Manual Garmin imports

Initialize PostgreSQL with `python -m garmin_grafana.migrate` first. Imports use
the same PG* settings and ACCOUNT_ID as normal ingestion, with no Garmin login.
Run commands from this checkout after `uv sync --locked`, or use the built
Compose application image. Keep original export files as your backup.

## Individual FIT file

```bash
uv run python -m garmin_grafana.fit_activity_importer --fit_file /path/run.fit --dry_run
uv run python -m garmin_grafana.fit_activity_importer --fit_file /path/run.fit
```

Or, with Compose:

```bash
docker compose -f compose-example.yml run --rm \
  -v /absolute/path/run.fit:/fit_file.fit:ro garmin-fetch-data \
  python -m garmin_grafana.fit_activity_importer --fit_file /fit_file.fit
```

The dry run parses the file without database writes. FIT summary identity is
derived from the file, so a standalone FIT import can have a different activity
ID from the later Garmin Connect API record; importing both may represent the
same workout twice. Prefer account export/API imports when Garmin IDs are known.

## Garmin account export

Request your export from Garmin's account data management page, extract it, and
pass the directory containing the exported Garmin files:

```bash
uv run python -m garmin_grafana.garmin_bulk_importer \
  --bulk_data_path /path/export --start_date 2020-01-01 --end_date 2025-12-31
```

Compose equivalent:

```bash
docker compose -f compose-example.yml run --rm \
  -v /absolute/path/export:/bulk_export:ro garmin-fetch-data \
  python -m garmin_grafana.garmin_bulk_importer \
  --bulk_data_path /bulk_export --start_date 2020-01-01 --end_date 2025-12-31
```

The adapter imports daily statistics, sleep, activities/FIT and hydration.
Exports omit online strength exercise snapshots; existing sets remain intact.
`--ignore_errors` continues after failures but returns a failing status if days
were skipped. Rerun the same range after fixing the error. File indexing uses a
cache alongside the export directory, so a read-only mount can cause cache-write
warnings; indexing still works without that cache. No source files are deleted.
