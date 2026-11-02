#!/usr/bin/env bash
# Rehearse the move from the Alpine timescale/timescaledb image (v0.10.x) to compose-example.yml's
# timescaledb-ha service on the same volume: ownership hand-off, data kept, every index rebuilt by migrate.
# Needs PGUSER, PGDATABASE, PGPASSWORD and GRAFANA_DB_PASSWORD, and the garmin-grafana:timescale image built.
# Uses its own Compose project and volume, removed at the end; never point it at a real deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
project=garmin-upgrade-smoke
compose=(docker compose -p "$project" -f compose-example.yml)
old="$project-alpine"
cleanup() { docker rm -f "$old" >/dev/null 2>&1 || true; "${compose[@]}" down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
sql() { "${compose[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$PGDATABASE" -Atc "$1"; }
# Fails, with the indexes it names, if any B-tree index is out of order for the running C library.
INDEX_CHECK="DO \$\$ DECLARE r record; bad int := 0; BEGIN
 FOR r IN SELECT i.indexrelid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_am a ON a.oid = c.relam
  WHERE a.amname = 'btree' AND c.relnamespace IN ('public'::regnamespace, '_timescaledb_internal'::regnamespace) LOOP
  BEGIN PERFORM bt_index_check(r.indexrelid, true);
  EXCEPTION WHEN index_corrupted THEN bad := bad + 1; RAISE WARNING '%: %', r.indexrelid::regclass, SQLERRM; END;
 END LOOP;
 IF bad > 0 THEN RAISE EXCEPTION '% indexes out of order', bad; END IF;
END \$\$"

docker volume create "${project}_postgres_data" >/dev/null
docker run -d --name "$old" -e POSTGRES_USER="$PGUSER" -e POSTGRES_DB="$PGDATABASE" -e POSTGRES_PASSWORD="$PGPASSWORD" \
  -v "${project}_postgres_data:/var/lib/postgresql/data" timescale/timescaledb:2.31.0-pg17 >/dev/null
timeout 120 sh -c "until docker exec $old pg_isready -q -h 127.0.0.1 -U $PGUSER; do sleep 1; done"
# Mixed case and underscores sort differently under musl (code points) and glibc (en_US rules).
docker exec "$old" psql -v ON_ERROR_STOP=1 -q -U "$PGUSER" -d "$PGDATABASE" -c "
 CREATE EXTENSION IF NOT EXISTS timescaledb;
 CREATE TABLE upgrade_words (word text PRIMARY KEY);
 INSERT INTO upgrade_words SELECT prefix || n FROM unnest(ARRAY['HRV_', 'HeartRate', 'a', 'B', '_x']) prefix, generate_series(1, 400) n;
 CREATE TABLE upgrade_ticks (time timestamptz NOT NULL, dataset text NOT NULL, PRIMARY KEY (dataset, time));
 SELECT create_hypertable('upgrade_ticks', 'time', chunk_time_interval => INTERVAL '7 days');
 INSERT INTO upgrade_ticks SELECT TIMESTAMPTZ '2026-01-01' + n * INTERVAL '1 hour', word FROM upgrade_words, generate_series(0, 3) n;"
docker rm -f "$old" >/dev/null

"${compose[@]}" up -d --no-build --wait postgres
test "$("${compose[@]}" exec -T postgres stat -c %u /var/lib/postgresql/data)" = 1000
sql 'CREATE EXTENSION IF NOT EXISTS amcheck' >/dev/null
if sql "$INDEX_CHECK" >/dev/null 2>&1; then
  echo 'The seed data did not reproduce out-of-order indexes after the switch; this rehearsal would prove nothing' >&2
  exit 1
fi
"${compose[@]}" run --rm migrate
sql "$INDEX_CHECK"
test "$(sql 'SELECT count(*) FROM upgrade_words'):$(sql 'SELECT count(*) FROM upgrade_ticks')" = 2000:8000
test "$(sql "SELECT datcollversion IS NOT DISTINCT FROM pg_database_collation_actual_version(oid) FROM pg_database WHERE datname = current_database()")" = t
test "$(sql "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb_toolkit'")" != ''
echo 'Upgrade from the Alpine image: volume handed to UID 1000, data kept, indexes rebuilt and in order, migrations applied'
