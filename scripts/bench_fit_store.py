"""Volume benchmark for the FIT store: a year of one-hour 1 Hz GPS activities, imported, exported, verified and read
through the typed view. Creates and drops its own database (PG* variables, a role with CREATEDB; a disposable server).

    uv run python scripts/bench_fit_store.py [--days 365] [--json out.json]
"""
import argparse
import json
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

import garmin_fit_store
from garmin_fit_store import builder, encode, store

END = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)


def activity(day, seconds=3600):
    start = (END - timedelta(days=day)).replace(tzinfo=None)
    messages = [('file_id', {'type': 'activity', 'manufacturer': 'garmin', 'serial_number': 1000 + day, 'time_created': start})]
    for i in range(seconds):
        messages.append(('record', {'timestamp': start + timedelta(seconds=i), 'position_lat': 480_000_000 + day * 1000 + i * 3,
                                    'position_long': 90_000_000 + i * 2, 'distance': i * 3.0, 'speed': 3.0 + (i % 50) / 100,
                                    'altitude': 400 + (i % 100) / 10, 'heart_rate': 120 + i % 30, 'cadence': 80 + i % 8,
                                    'power': 180 + i % 60, 'temperature': 18}))
    messages.append(('session', {'timestamp': start + timedelta(seconds=seconds), 'start_time': start, 'sport': 'running',
                                 'total_elapsed_time': float(seconds), 'total_distance': seconds * 3.0}))
    return encode(builder.build_ir(messages))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=365)
    parser.add_argument('--json')
    args = parser.parse_args()
    admin = psycopg.connect(autocommit=True)
    name = 'fit_bench_' + uuid.uuid4().hex
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    result = {'days': args.days}
    try:
        with psycopg.connect(**(conninfo_to_dict(admin.info.dsn) | {'dbname': name})) as connection:
            for path in sorted(garmin_fit_store.MIGRATIONS.glob('*.sql')):
                connection.execute(path.read_text())
            garmin_fit_store.after_migrate(connection)
            connection.commit()
            imports, ids = [], []
            for day in range(args.days):
                data = activity(day)
                started = time.perf_counter()
                ids.append(store.import_file(connection, data))
                imports.append(time.perf_counter() - started)
            exports = []
            for file_id in ids[:20]:
                started = time.perf_counter()
                store.export_file(connection, file_id)
                exports.append(time.perf_counter() - started)
            started = time.perf_counter()
            assert store.verify(connection, ids[0]) == []
            result['verify_s'] = time.perf_counter() - started
            connection.execute('ANALYZE fit.records')
            connection.commit()

            def timed(query, params=()):
                runs = []
                for _ in range(5):
                    started = time.perf_counter()
                    connection.execute(query, params).fetchall()
                    runs.append((time.perf_counter() - started) * 1000)
                return statistics.median(runs)
            result['import_median_s'] = statistics.median(imports)
            result['export_median_s'] = statistics.median(exports)
            result['view_one_activity_ms'] = timed('SELECT time, heart_rate, speed, position_lat, position_long, altitude, power FROM fit.v_record WHERE file_id = %s ORDER BY seq', (ids[len(ids) // 2],))
            result['view_30_days_ms'] = timed("SELECT time_bucket('1 hour', time), avg(heart_rate) FROM fit.v_record WHERE time > %s GROUP BY 1", (END - timedelta(days=30),))
            result['view_year_ms'] = timed("SELECT date_trunc('day', time), avg(heart_rate), avg(speed) FROM fit.v_record GROUP BY 1")
            result['records'] = connection.execute('SELECT count(*) FROM fit.records').fetchone()[0]
            result['size_mb'] = connection.execute("SELECT pg_total_relation_size('fit.records') / 1048576").fetchone()[0]
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    print(json.dumps(result, indent=1))
    if args.json:
        open(args.json, 'w').write(json.dumps(result, indent=1))


if __name__ == '__main__':
    main()
