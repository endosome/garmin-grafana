"""Read-only, bounded InfluxDB 1.x HTTP import; commits resumable time windows."""
import argparse
from datetime import datetime, timezone
import json
import logging
import os
import requests
from .domain import Record
from .storage import PostgresStore


def quote_identifier(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


class LegacyReader:
    def __init__(self, url, database, username=None, password=None, user_tag=None):
        self.url, self.database = url.rstrip('/') + '/query', database
        self.session = requests.Session()
        self.user_tag = user_tag
        if username:
            self.session.auth = (username, password)

    def query(self, query, *, chunked=False):
        with self.session.get(self.url, params={'db': self.database, 'q': query,
                'epoch': 'ns', 'chunked': str(chunked).lower(), 'chunk_size': 1000},
                stream=True, timeout=(10, 300)) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                result = json.loads(line)
                if 'error' in result:
                    raise RuntimeError('Legacy query failed: ' + result['error'])
                for item in result.get('results', []):
                    if 'error' in item:
                        raise RuntimeError('Legacy query failed: ' + item['error'])
                    yield from item.get('series', [])

    def measurements(self):
        for series in self.query('SHOW MEASUREMENTS'):
            for row in series.get('values', []):
                yield row[series['columns'].index('name')]

    def bounds(self, measurement):
        # FIRST/LAST(*) handle sparse fields; use all returned time columns.
        times = []
        for direction in ('ASC', 'DESC'):
            for series in self.query(f'SELECT * FROM {quote_identifier(measurement)} ORDER BY time {direction} LIMIT 1'):
                times.extend(row[0] for row in series.get('values', []) if row[0] is not None)
        return (min(times), max(times) + 1) if times else None

    def user_tags(self):
        return {row[-1] for series in self.query('SHOW TAG VALUES WITH KEY = "User_ID"') for row in series.get('values', [])}

    def records(self, measurement, start, end):
        query = f'SELECT * FROM {quote_identifier(measurement)} WHERE time >= {start} AND time < {end} '
        if self.user_tag is not None:
            escaped = self.user_tag.replace('\\', '\\\\').replace("'", "\\'")
            query += f" AND \"User_ID\" = '{escaped}'"
        query += ' GROUP BY *'
        for series in self.query(query, chunked=True):
            tags = series.get('tags', {})
            for values in series.get('values', []):
                row = dict(zip(series['columns'], values, strict=True))
                ns = row.pop('time')
                for tag in tags:
                    row.pop(tag, None)
                # SELECT * reports absent fields as null. Preserve them explicitly.
                second, fraction = divmod(ns, 1_000_000_000)
                stamp = datetime.fromtimestamp(second, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{fraction:09d}Z'
                item = {'measurement': measurement, 'time': stamp, 'tags': tags, 'fields': row}
                # These legacy Garmin datasets encoded a calendar date as UTC midnight.
                calendar_datasets = {'LactateThreshold','HillScore','RacePredictions','FitnessAge',
                                     'VO2_Max','EnduranceScore','Hydration','LifestyleJournal'}
                day = datetime.fromtimestamp(second, timezone.utc).date() if measurement in calendar_datasets else None
                record = Record.from_mapping(item, local_date=day)
                yield record


def import_measurement(reader, store, measurement, start, end, *, migration_id, window_ns, dry_run=False, validate=False):
    """Progress is committed only after all chunks in a window succeed.

    A failed window can have committed batches; replay is idempotent. Restarting
    replays that window and never advances past a failed batch.
    """
    if not dry_run and not validate:
        with store.connection.transaction(), store.connection.cursor() as cur:
            source = store.source_id(cur)
            cur.execute('SELECT next_ns FROM legacy_progress WHERE source_id=%s AND migration_id=%s AND measurement=%s',
                        (source, migration_id, measurement))
            row = cur.fetchone()
            if row:
                start = max(start, int(row[0]))
    total = 0
    while start < end:
        stop = min(start + window_ns, end)
        batch = []
        count = 0
        for record in reader.records(measurement, start, stop):
            batch.append(record)
            if len(batch) >= 1000:
                if not dry_run:
                    store.validate_legacy(batch) if validate else store.write_legacy(batch)
                count += len(batch)
                batch = []
        if batch:
            if not dry_run:
                store.validate_legacy(batch) if validate else store.write_legacy(batch)
            count += len(batch)
        if not dry_run and not validate:
            with store.connection.transaction(), store.connection.cursor() as cur:
                source = store.source_id(cur)
                cur.execute('''INSERT INTO legacy_progress VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(source_id,migration_id,measurement) DO UPDATE
                    SET next_ns=excluded.next_ns,imported=legacy_progress.imported+excluded.imported''',
                    (source, migration_id, measurement, stop, count))
        total += count
        action = 'Validated' if validate else 'Previewed' if dry_run else 'Imported'
        logging.info('%s %s records from %s in completed window', action, count, measurement)
        start = stop
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.getenv('LEGACY_INFLUX_URL', 'http://localhost:8086'))
    parser.add_argument('--database', default=os.getenv('LEGACY_INFLUX_DATABASE', 'GarminStats'))
    parser.add_argument('--account', required=True, help='Use the same ACCOUNT_ID as future ingestion')
    parser.add_argument('--migration-id', required=True, help='Stable unique identifier for this source database and import run')
    parser.add_argument('--legacy-user', help='Select an exact legacy User_ID tag; required for multi-account legacy databases')
    parser.add_argument('--measurement', action='append', help='Repeat to select measurements; default imports all')
    parser.add_argument('--start', type=int, help='Inclusive Unix nanoseconds; default source minimum')
    parser.add_argument('--end', type=int, help='Exclusive Unix nanoseconds; default source maximum + 1')
    parser.add_argument('--window-hours', type=int, default=24)
    parser.add_argument('--validate', action='store_true', help='Compare all source records with the preserved legacy archive, ignoring resume progress')
    parser.add_argument('--dry-run', action='store_true', help='Read and normalize all selected records without PostgreSQL access')
    args = parser.parse_args()
    if args.window_hours <= 0:
        parser.error('--window-hours must be positive')
    logging.basicConfig(level=logging.INFO)
    reader = LegacyReader(args.url, args.database, os.getenv('LEGACY_INFLUX_USERNAME'), os.getenv('LEGACY_INFLUX_PASSWORD'))
    store = PostgresStore(account=args.account)
    try:
        if args.dry_run and args.validate:
            parser.error('--dry-run and --validate cannot be combined')
        users = reader.user_tags()
        if args.legacy_user is not None and args.legacy_user not in users:
            parser.error('--legacy-user does not match an existing User_ID tag')
        if args.legacy_user is None and len(users) > 1:
            parser.error('Multiple legacy accounts found; repeat the import with --legacy-user and a separate --account for each')
        reader.user_tag = args.legacy_user
        for measurement in args.measurement or reader.measurements():
            bounds = reader.bounds(measurement)
            if bounds:
                import_measurement(reader, store, measurement,
                    args.start if args.start is not None else bounds[0],
                    args.end if args.end is not None else bounds[1],
                    migration_id=args.migration_id, window_ns=args.window_hours * 3600_000_000_000,
                    dry_run=args.dry_run, validate=args.validate)
    finally:
        reader.session.close()
        store.close()


if __name__ == '__main__':
    main()
