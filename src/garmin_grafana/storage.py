"""PostgreSQL persistence; no Garmin API dependencies or implicit schema changes."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import psycopg
from psycopg.types.json import Jsonb


def connect():
    return psycopg.connect(
        host=os.getenv('PGHOST', 'localhost'), port=int(os.getenv('PGPORT', '5432')),
        dbname=os.getenv('PGDATABASE', 'garmin'), user=os.getenv('PGUSER', 'garmin'),
        password=os.getenv('PGPASSWORD'), sslmode=os.getenv('PGSSLMODE', 'prefer'),
        connect_timeout=int(os.getenv('PGCONNECT_TIMEOUT', '10')),
        options='-c timezone=UTC',
    )


class PostgresStore:
    def __init__(self, *, provider='garmin', account=None, person=None, connection=None):
        self.provider = provider
        self.account = account or os.getenv('ACCOUNT_ID') or os.getenv('GARMINCONNECT_EMAIL') or 'default'
        self.person = person or os.getenv('PERSON_ID') or self.account
        self._connection = connection
        self.changed_rows = 0  # rows the writes of this process actually inserted or changed

    @property
    def connection(self):
        if self._connection is None or self._connection.closed:
            self._connection = connect()
        return self._connection

    def close(self):
        if self._connection is not None:
            self._connection.close()

    def source_id(self, cur):
        cur.execute('INSERT INTO persons VALUES (%s) ON CONFLICT DO NOTHING', (self.person,))
        cur.execute('''INSERT INTO sources(provider,account,person_id) VALUES (%s,%s,%s)
            ON CONFLICT(provider,account) DO UPDATE SET account=excluded.account RETURNING id''',
            (self.provider, self.account, self.person))
        return cur.fetchone()[0]

    def write(self, records, *, replacements=(), checkpoint=None):
        """One atomic dataset/snapshot transaction. Input may be a bounded iterator."""
        count = 0
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            # Serialize writes for one account; avoids interleaved snapshot replacement.
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (source,))
            for dataset, activity in replacements:
                cur.execute('DELETE FROM records WHERE source_id=%s AND dataset=%s AND tags->>\'ActivityID\'=%s',
                            (source, dataset, str(activity)))
            batch = []
            for record in records:
                batch.append(record)
                if len(batch) == 1000:
                    self.changed_rows += self._batch(cur, source, batch)
                    count += len(batch)
                    batch = []
            if batch:
                self.changed_rows += self._batch(cur, source, batch)
                count += len(batch)
            if checkpoint:
                dataset, day = checkpoint
                cur.execute('''INSERT INTO sync_ranges VALUES (%s,%s,%s)
                    ON CONFLICT(source_id,dataset) DO UPDATE
                    SET start_day=LEAST(sync_ranges.start_day,excluded.start_day)''', (source, dataset, day))
                cur.execute('''INSERT INTO sync_days(source_id,dataset,day) VALUES (%s,%s,%s)
                    ON CONFLICT(source_id,dataset,day) DO UPDATE SET completed_at=now()''', (source, dataset, day))
        return count

    @staticmethod
    def _batch(cur, source, batch):
        """Upserts a batch and returns how many rows it inserted or changed (unchanged rows are filtered out first)."""
        changed = 0
        cur.executemany('INSERT INTO devices VALUES (%s,%s) ON CONFLICT DO NOTHING',
                        [(source, device) for device in sorted({r.device for r in batch})])
        for table in ('records', 'samples'):
            # One statement per batch. ON CONFLICT cannot update a row twice in one statement, so records with the
            # same key are merged here first, the way consecutive upserts would merge them.
            merged = {}
            for r in batch:
                if r.is_sample != (table == 'samples'):
                    continue
                key = (r.dataset, r.key) + ((r.time, r.nanoseconds) if table == 'samples' else ())
                if key in merged:
                    previous = merged[key]
                    r = replace(r, values={**previous.values, **r.values},
                                local_date=previous.local_date if r.local_date is None else r.local_date,
                                timezone=previous.timezone if r.timezone is None else r.timezone)
                merged[key] = r
            if not merged:
                continue
            rows = merged.values()
            conflict = 'source_id,dataset,record_key' + (',time,nanoseconds' if table == 'samples' else '')
            columns = 'source_id,dataset,record_key,time,nanoseconds,original_time,device_id,local_date,timezone,tags,fields'
            same_key = ' AND '.join(f'e.{c}=v.{c}' for c in conflict.split(','))
            # Without the batch's time range the check below probes every chunk of the hypertable for every row.
            in_range = ' AND e.time BETWEEN %s AND %s' if table == 'samples' else ''
            bounds = (min(r.time for r in rows), max(r.time for r in rows)) if table == 'samples' else ()
            # Rows the update would leave unchanged are dropped before ON CONFLICT: it locks the existing row
            # even when nothing changes, and that lock is written to WAL. The advisory lock in write() keeps
            # the check and the insert consistent.
            cur.execute(f'''INSERT INTO {table} AS t ({columns})
                SELECT * FROM unnest(%s::bigint[],%s::text[],%s::text[],%s::timestamptz[],%s::smallint[],%s::text[],%s::text[],
                    %s::date[],%s::text[],%s::jsonb[],%s::jsonb[]) v({columns})
                WHERE NOT EXISTS (SELECT 1 FROM {table} e WHERE {same_key}{in_range}
                    AND (e.time,e.nanoseconds,e.original_time,e.local_date,e.timezone,e.tags,e.fields) IS NOT DISTINCT FROM
                        (v.time,v.nanoseconds,v.original_time,COALESCE(v.local_date,e.local_date),
                         COALESCE(v.timezone,e.timezone),v.tags,e.fields || v.fields))
                ON CONFLICT ({conflict}) DO UPDATE SET
                time=excluded.time,nanoseconds=excluded.nanoseconds,original_time=excluded.original_time,
                local_date=COALESCE(excluded.local_date,t.local_date),
                timezone=COALESCE(excluded.timezone,t.timezone),
                tags=excluded.tags,fields=t.fields || excluded.fields''',
                [[source] * len(rows), [r.dataset for r in rows], [r.key for r in rows], [r.time for r in rows],
                 [r.nanoseconds for r in rows], [r.original_time for r in rows], [r.device for r in rows],
                 [r.local_date for r in rows], [r.timezone for r in rows], [Jsonb(r.tags) for r in rows],
                 [Jsonb(r.values) for r in rows], *bounds])
            changed += cur.rowcount
        return changed

    def request_range(self, datasets, start):
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            cur.executemany('''INSERT INTO sync_ranges VALUES (%s,%s,%s)
                ON CONFLICT(source_id,dataset) DO UPDATE
                SET start_day=LEAST(sync_ranges.start_day,excluded.start_day)''',
                [(source, dataset, start) for dataset in datasets])

    def record_fetch(self):
        """Remember when a sync last finished, for the dashboard's "Last Fetch" (the watch's own upload is "Last Sync")."""
        with self.connection.transaction(), self.connection.cursor() as cur:
            cur.execute("INSERT INTO app_info(key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                        (f"last_fetch:{self.source_id(cur)}", "ok"))

    def finish_fetch_requests(self, upto, changed_before, timezone='UTC', outcome='ok'):
        """Give every "Fetch now" request up to id `upto` that has no result yet the result of the sync that just ended:
        when it finished, how many points it added, and a short text. `changed_before` is `changed_rows` at the sync's start."""
        new = self.changed_rows - changed_before
        with self.connection.transaction(), self.connection.cursor() as cur:
            cur.execute('SELECT id FROM fetch_requests WHERE id <= %s AND finished_at IS NULL', (upto,))
            if not cur.fetchall():
                return None
            if new:
                text = f"{new} new point{'' if new == 1 else 's'}"
            else:
                cur.execute('''SELECT max(time) AT TIME ZONE %s FROM all_records WHERE source_id=%s''', (timezone, self.source_id(cur)))
                newest = cur.fetchone()[0]
                text = 'nothing new' + (f" since {newest:%H:%M}" if newest and newest.date() == datetime.now(ZoneInfo(timezone)).date()
                                        else f" since {newest:%a %d %b %H:%M}" if newest else '')
            if outcome != 'ok':
                text = f'{outcome}: {text}'
            cur.execute('UPDATE fetch_requests SET finished_at=now(), new_points=%s, result=%s WHERE id <= %s AND finished_at IS NULL',
                        (new, text, upto))
            return text

    def latest_fetch_request(self):
        """Id of the newest "Fetch now" request from Grafana, 0 before the first one."""
        with self.connection.transaction(), self.connection.cursor() as cur:
            cur.execute('SELECT COALESCE(max(id), 0) FROM fetch_requests')
            return cur.fetchone()[0]

    # A completed day s is fetched again when the device uploaded after that fetch, unless the fetch already
    # followed an upload made after the day ended (with time for Garmin to process it): a watch that was not
    # synced for days uploads those days later. A watch keeps only a few weeks of data, so older days are
    # not checked. Parameter: the account's time zone.
    UP_TO_DATE = '''(s.day<CURRENT_DATE-60
        OR NOT EXISTS (SELECT 1 FROM records u WHERE u.source_id=s.source_id AND u.dataset='DeviceSync'
            AND u.time>s.completed_at)
        OR EXISTS (SELECT 1 FROM records u WHERE u.source_id=s.source_id AND u.dataset='DeviceSync'
            AND u.time>=(s.day+1)::timestamp AT TIME ZONE %s AND u.time<=s.completed_at-interval '30 minutes'))'''

    def completed_datasets(self, day, timezone='UTC'):
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            cur.execute('SELECT dataset FROM sync_days s WHERE source_id=%s AND day=%s AND ' + self.UP_TO_DATE,
                        (source, day, timezone))
            return {row[0] for row in cur.fetchall()}

    def pending_start(self, datasets, today, timezone='UTC'):
        """Find holes and days uploaded since they were fetched; always recheck two recent days."""
        starts = [today - timedelta(days=2)]
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            for dataset in datasets:
                cur.execute('SELECT start_day FROM sync_ranges WHERE source_id=%s AND dataset=%s', (source, dataset))
                row = cur.fetchone()
                first = row[0] if row else today - timedelta(days=7)
                cur.execute('''SELECT min(d::date) FROM generate_series(%s::date,%s::date,interval '1 day') d
                    WHERE NOT EXISTS (SELECT 1 FROM sync_days s WHERE s.source_id=%s AND s.dataset=%s AND s.day=d::date
                    AND ''' + self.UP_TO_DATE + ')', (first, today, source, dataset, timezone))
                starts.append(cur.fetchone()[0] or today)
        return min(starts)

    def write_legacy(self, records):
        """Archive original legacy identities before canonical domain reconciliation."""
        from .domain import canonical
        from hashlib import sha256
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            self.write(records)
            cur.executemany('''INSERT INTO legacy_archive VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source_id,dataset,legacy_key) DO UPDATE SET
                fields=legacy_archive.fields || excluded.fields, original_time=excluded.original_time''',
                [(source, r.dataset, sha256(canonical([r.tags, r.time.isoformat(), r.nanoseconds]).encode()).hexdigest(),
                  r.time, r.nanoseconds, r.original_time, Jsonb(r.tags), Jsonb(r.values)) for r in records])

    def validate_legacy(self, records):
        from .domain import canonical
        from hashlib import sha256
        with self.connection.transaction(), self.connection.cursor() as cur:
            source = self.source_id(cur)
            for record in records:
                key = sha256(canonical([record.tags, record.time.isoformat(), record.nanoseconds]).encode()).hexdigest()
                cur.execute('SELECT tags,fields FROM legacy_archive WHERE source_id=%s AND dataset=%s AND legacy_key=%s',
                            (source, record.dataset, key))
                row = cur.fetchone()
                if row is None or row != (record.tags, record.values):
                    raise ValueError('Legacy validation failed: missing or changed source record')
