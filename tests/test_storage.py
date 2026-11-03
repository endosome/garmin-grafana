"""Real PostgreSQL/TimescaleDB tests. Use TEST_DATABASE=1 and PG* for disposable DB."""
from datetime import date, datetime, timezone, timedelta
from dataclasses import replace
import importlib.util
import json
import os
import re
from pathlib import Path
from unittest import mock
import uuid
import pytest
from garmin_grafana.domain import Record, parse_timestamp, RecordBatch
from garmin_grafana.storage import PostgresStore
from garmin_grafana import storage, migrate
from helpers import refresh_rollups


def point(dataset='HeartRateIntraday', time='2026-01-01T00:00:00.123456789Z', tags=None, **values):
    return {'measurement': dataset, 'time': time, 'tags': tags or {'Device': 'watch'}, 'fields': values or {'HeartRate': 72}}


def test_timestamp_precision_and_offset():
    dt, ns, original = parse_timestamp('2026-10-25T02:30:00.123456789+02:00')
    assert dt == datetime(2026, 10, 25, 0, 30, 0, 123456, timezone.utc)
    assert ns == 789 and original.endswith('+02:00')
    assert parse_timestamp(datetime(2026,1,1,tzinfo=timezone.utc))[1] == 0
    with pytest.raises(ValueError):
        parse_timestamp('2026-01-01T00:00:00')
    with pytest.raises(ValueError):
        parse_timestamp('2026-01-01T00:00:00.1234567891Z')


def test_values_are_lossless_and_invalid_values_rejected():
    record = Record.from_mapping(point(n=None, text='ok', boolean=True, nested={'a': [1, 2]}, zero=0))
    assert record.values == {'n': None, 'text': 'ok', 'boolean': True, 'nested': {'a': [1,2]}, 'zero': 0}
    for fields in ({}, {'v': float('nan')}, {'v': float('inf')}):
        with pytest.raises(ValueError):
            Record.from_mapping({**point(), 'fields': fields})
    with pytest.raises(ValueError):
        Record.from_mapping({**point(), 'measurement': ''})


def test_identity_distinguishes_devices_providers_source_ids_and_nanoseconds():
    r = Record.from_mapping(point())
    assert r.key != Record.from_mapping(point(tags={'Device': 'other'})).key
    assert r.key != Record.from_mapping({**point(), 'source_id': 'second'}).key
    assert r.key != Record.from_mapping(point(time='2026-01-01T00:00:00.123456788Z')).key
    assert r.key == Record.from_mapping(point(HeartRate=73)).key
    assert Record.from_mapping(point('Summary')).is_sample is False
    assert Record.from_mapping(point('ActivityGPS')).is_sample is True
    assert Record.from_mapping(point('HRV_Intraday')).is_sample is True
    assert r.key != Record.from_mapping(point(), device_id='physical').key
    assert Record.from_mapping(point(Device_ID=42)).device == '42'
    assert Record.from_mapping(point(Device_ID=42)).key != Record.from_mapping(point(Device_ID=43)).key


def test_strength_identity_ignores_mutable_labels_and_time():
    a = Record.from_mapping(point('StrengthExerciseSet', tags={'ActivityID':'42', 'ExerciseLabel':'old'}, SetOrder=1, Reps=10))
    b = Record.from_mapping(point('StrengthExerciseSet', time='2026-01-02T00:00:00Z', tags={'ActivityID':'42', 'ExerciseLabel':'new'}, SetOrder=1, Reps=12))
    assert a.key == b.key
    assert Record.from_mapping(point('StrengthHRZones', Activity_ID=42, ZoneNumber=1)).key != Record.from_mapping(point('StrengthHRZones', Activity_ID=42, ZoneNumber=2)).key


@pytest.fixture
def db():
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* pointing to a disposable TimescaleDB database')
    connection = storage.connect()
    migrate.migrate(connection)
    # Tests refresh the rollups themselves (helpers.refresh_rollups); a scheduled refresh running at the same time would fail it.
    with connection.transaction():
        connection.execute("SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs WHERE proc_name = 'policy_refresh_continuous_aggregate'")
    store = PostgresStore(account='test-' + uuid.uuid4().hex, connection=connection)
    yield store
    store.close()


def query(store, sql, args=()):
    with store.connection.transaction(), store.connection.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def source(store):
    with store.connection.transaction(), store.connection.cursor() as cur:
        return store.source_id(cur)


def test_schema_idempotency_and_hypertable(db):
    migrate.migrate(db.connection)
    assert query(db, "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name='samples'") == [('samples',)]
    assert query(db, 'SELECT count(*) FROM schema_migrations')[0][0] >= 3
    with mock.patch.object(migrate.hashlib, 'sha256') as digest:
        digest.return_value.hexdigest.return_value = 'changed'
        with pytest.raises(RuntimeError, match='Applied migration changed'):
            migrate.migrate(db.connection)


def test_bulk_upsert_and_types(db):
    records = [Record.from_mapping(point(time=f'2026-01-01T00:{i//60:02d}:{i%60:02d}Z', HeartRate=70+i)) for i in range(1002)]
    assert db.write(iter(records)) == 1002
    assert db.write(records) == 1002
    sid = source(db)
    assert query(db, 'SELECT count(*) FROM samples WHERE source_id=%s', (sid,)) == [(1002,)]
    correction = replace(records[0], values={'HeartRate': 99, 'note': 'corrected', 'valid': True, 'n': None, 'meta': {'nested': [1]}})
    db.write([correction])
    fields = query(db, 'SELECT fields FROM samples WHERE source_id=%s AND record_key=%s', (sid, correction.key))[0][0]
    assert fields == correction.values
    assert query(db, "SELECT health_number(%s::jsonb,'v')", (json.dumps({'v':'unknown'}),)) == [(None,)]
    db.write([Record.from_mapping(point('NewProviderDocument', temperature=20.5, unit='celsius'))])
    assert query(db, 'SELECT count(*) FROM records WHERE source_id=%s', (sid,)) == [(1,)]


def test_rows_with_one_key_in_a_batch_merge_like_consecutive_upserts(db):
    # A sleep stage and a movement reading in the same minute share a key; so can a corrected document.
    stage = Record.from_mapping(point('SleepIntraday', SleepStageLevel=1), local_date=date(2026, 1, 1), timezone='UTC')
    movement = Record.from_mapping(point('SleepIntraday', SleepMovementActivityLevel=0.5))
    first = Record.from_mapping(point('Sleep', time='2026-01-01T00:00:00Z', score=80), local_date=date(2026, 1, 1))
    corrected = Record.from_mapping(point('Sleep', time='2026-01-01T00:00:00Z', score=81, awake=60))
    assert stage.key == movement.key and first.key == corrected.key
    db.write([stage, first, movement, corrected])
    sid = source(db)
    assert query(db, 'SELECT fields, local_date, timezone FROM samples WHERE source_id=%s', (sid,)) == [
        ({'SleepStageLevel': 1, 'SleepMovementActivityLevel': 0.5}, date(2026, 1, 1), 'UTC')]
    assert query(db, 'SELECT fields, local_date FROM records WHERE source_id=%s', (sid,)) == [({'score': 81, 'awake': 60}, date(2026, 1, 1))]


def test_unchanged_rows_are_not_rewritten(db):
    sample = Record.from_mapping(point(HeartRate=70))
    summary = Record.from_mapping(point('Sleep', time='2026-01-01T00:00:00Z', score=80))
    db.write([sample, summary])
    sid = source(db)

    def versions():
        # xmax also changes when ON CONFLICT only locks the row, which is written to WAL too.
        return {t: query(db, f'SELECT xmin::text,xmax::text FROM {t} WHERE source_id=%s', (sid,))
                for t in ('samples', 'records')}

    before = versions()
    db.write([sample, summary])
    assert versions() == before
    db.write([replace(sample, values={'HeartRate': 70}), replace(summary, values={'score': 80})])
    assert versions() == before
    db.write([replace(sample, values={'HeartRate': 71}), replace(summary, values={'score': 81})])
    after = versions()
    assert after['samples'] != before['samples'] and after['records'] != before['records']
    assert query(db, 'SELECT fields FROM samples WHERE source_id=%s', (sid,)) == [({'HeartRate': 71},)]
    db.write([replace(sample, values={'Extra': 1})])
    assert query(db, 'SELECT fields FROM samples WHERE source_id=%s', (sid,)) == [({'HeartRate': 71, 'Extra': 1},)]


def test_precision_coincidences_and_accounts(db):
    first = Record.from_mapping(point())
    second = Record.from_mapping(point(time='2026-01-01T00:00:00.123456788Z'))
    db.write([first, second])
    other = PostgresStore(account='other-' + uuid.uuid4().hex, connection=db.connection)
    other.write([first])
    assert query(db, 'SELECT count(*) FROM samples WHERE source_id=%s', (source(db),)) == [(2,)]
    assert source(other) != source(db)
    third = PostgresStore(provider='synthetic', account=db.account, connection=db.connection)
    third.write([first])
    assert source(third) != source(db)


def test_atomic_snapshot_replacement_and_empty_snapshot(db):
    tags = {'ActivityID': '42', 'Device':'watch', 'ExerciseLabel':'old'}
    original = Record.from_mapping(point('StrengthExerciseSet', tags=tags, SetOrder=1, Reps=8, Weight_kg=60))
    db.write([original], checkpoint=('activity', '2026-01-01'))
    changed = Record.from_mapping(point('StrengthExerciseSet', time='2026-01-01T10:00:00Z', tags={**tags, 'ExerciseLabel':'new'}, SetOrder=1, Reps=10, Weight_kg=65))
    db.write([changed], replacements=[('StrengthExerciseSet','42')])
    assert query(db, 'SELECT tags,fields FROM records WHERE source_id=%s', (source(db),)) == [(changed.tags, changed.values)]
    def failing_iterator():
        yield original
        raise ValueError('invalid payload')
    with pytest.raises(ValueError):
        db.write(failing_iterator(), replacements=[('StrengthExerciseSet','42')], checkpoint=('activity','2026-01-02'))
    assert query(db, 'SELECT fields FROM records WHERE source_id=%s', (source(db),)) == [(changed.values,)]
    assert query(db, 'SELECT day FROM sync_days WHERE source_id=%s', (source(db),)) == [(date(2026,1,1),)]
    db.write([], replacements=[('StrengthExerciseSet','42')])
    assert query(db, 'SELECT count(*) FROM records WHERE source_id=%s', (source(db),)) == [(0,)]


def test_checkpoint_holes_independent_datasets_and_overlap(db):
    today = date(2026, 1, 10)
    assert db.pending_start(['heartrate','sleep'], today) == today - timedelta(days=7)
    for day in range(1, 11):
        if day != 4:
            db.write([], checkpoint=('sleep', date(2026,1,day)))
        db.write([], checkpoint=('heartrate',date(2026,1,day)))
    assert db.pending_start(['sleep','heartrate'],today) == date(2026,1,4)
    db.write([],checkpoint=('sleep',date(2026,1,4)))
    assert db.pending_start(['sleep','heartrate'],today) == date(2026,1,8)
    assert db.pending_start(['new-dataset'],today) == date(2026,1,3)


def test_atomic_dataset_sync_from_garmin(gf, db, monkeypatch):
    monkeypatch.setattr(gf, 'store', db)
    monkeypatch.setattr(gf, 'FETCH_SELECTION','heartrate,sleep')
    monkeypatch.setattr(gf, 'get_intraday_hr', lambda day: [point()])
    monkeypatch.setattr(gf, 'get_sleep_data', mock.Mock(side_effect=RuntimeError('sleep unavailable')))
    with pytest.raises(RuntimeError):
        gf.daily_fetch_write('2026-01-01')
    assert query(db, 'SELECT dataset FROM sync_days WHERE source_id=%s',(source(db),)) == [('heartrate',)]
    monkeypatch.setattr(gf,'get_sleep_data',lambda day: [point('SleepSummary', sleepTimeSeconds=28000)])
    gf.daily_fetch_write('2026-01-01')
    assert query(db, 'SELECT count(*) FROM all_records WHERE source_id=%s',(source(db),)) == [(2,)]
    assert len(query(db, 'SELECT dataset FROM sync_days WHERE source_id=%s',(source(db),))) == 2


def test_legacy_archive_preserves_original_strength_duplicates(db):
    a = Record.from_mapping(point('StrengthExerciseSet', tags={'ActivityID':'42','ExerciseLabel':'old'}, SetOrder=1, Reps=8))
    b = Record.from_mapping(point('StrengthExerciseSet', tags={'ActivityID':'42','ExerciseLabel':'new'}, SetOrder=1, Reps=10))
    db.write_legacy([a,b])
    db.validate_legacy([a,b])
    assert query(db,'SELECT count(*) FROM legacy_archive WHERE source_id=%s',(source(db),)) == [(2,)]
    db.write([],replacements=[('StrengthExerciseSet','42')])
    db.validate_legacy([a,b])
    with pytest.raises(ValueError,match='Legacy validation failed'):
        db.validate_legacy([replace(a,values={'Reps':9})])
    with pytest.raises(ValueError):
        db.validate_legacy([replace(a,nanoseconds=500)])


def test_dashboard_queries_empty_and_populated(db):
    spec = importlib.util.spec_from_file_location('dashboard_queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sid=source(db)
    def execute_all():
        results={}
        for label,sql,fmt in module.dashboard_queries():
            with db.connection.transaction(),db.connection.cursor() as cur:
                cur.execute(module.expand_query(sql,sid))
                results[label]=(cur.fetchall(), [c.name for c in cur.description])
        return results
    empty=execute_all()
    # Grafana's SQL datasource fails every panel when a variable query returns no rows.
    for name in ('ExerciseCategory','ExerciseLabel'):
        assert empty['Garmin-Strength-Training-Dashboard.json:'+name][0]==[('None recorded','__all')]
    db.write([Record.from_mapping(point()), Record.from_mapping(point('DailyStats',totalSteps=5000)),
        Record.from_mapping(point('SleepSummary',sleepTimeSeconds=28800)),
        Record.from_mapping(point('ActivityGPS',tags={'ActivitySelector':'__all'},Latitude=51.5,Longitude=-0.1,Speed=3,HeartRate=140)),
        Record.from_mapping(point('StrengthExerciseSet', tags={'ActivityID':'42','ExerciseLabel':'BENCH','ExerciseCategory':'PRESS'},SetOrder=1,Reps=10,Weight_kg=60,Duration_s=40)),
        Record.from_mapping(point('StrengthHRZones',Activity_ID=42,ZoneNumber=1,SecsInZone=120))])
    results=execute_all()
    volume=next(value for label,value in results.items() if 'Strength' in label and '1002:A' in label)
    assert volume[0] == [(600.0,)]
    hr=results['Garmin-Grafana-Dashboard.json:15:A']
    assert hr[1] == ['time','Heart Rate'] and any(row[1] == 72 for row in hr[0])


def test_connect_configuration_and_lazy_reconnect(monkeypatch):
    monkeypatch.setenv('PGPORT','5439')
    monkeypatch.setenv('PGSSLMODE','verify-full')
    with mock.patch.object(storage.psycopg,'connect') as connect:
        store = PostgresStore()
        connect.assert_not_called()
        connection = store.connection
        assert connect.call_args.kwargs['port']==5439
        assert connect.call_args.kwargs['sslmode']=='verify-full'
        connection.closed = True
        store.connection
        assert connect.call_count == 2
        store.close()
        connection.close.assert_called_once()
    PostgresStore().close()


def test_days_fetched_before_the_watch_uploaded_them_are_fetched_again(db):
    # Relative to the database date: only the last 60 days are checked.
    today=query(db,'SELECT CURRENT_DATE')[0][0]
    def day(n):
        return today-timedelta(days=10-n)
    def at(n,hour,minute=0):
        return datetime(*day(n).timetuple()[:3],hour,minute,tzinfo=timezone.utc)
    def fetched(n,when):
        db.write([],checkpoint=('sleep',day(n)))
        with db.connection.transaction(),db.connection.cursor() as cur:
            cur.execute('UPDATE sync_days SET completed_at=%s WHERE source_id=%s AND dataset=%s AND day=%s',
                        (when,source(db),'sleep',day(n)))
    def uploaded(when):
        db.write([Record.from_mapping(point('DeviceSync',when.isoformat(),Device_Name='watch'))])
    # The watch uploaded on day 4 at 03:00 UTC and then stayed disconnected; the fetcher kept
    # completing days with whatever Garmin Connect had.
    uploaded(at(4,3))
    for n in range(1,4):
        fetched(n,at(4,4))
    for n in range(4,10):
        fetched(n,at(n+2,0))
    assert db.pending_start(['sleep'],today)==day(8)
    assert db.completed_datasets(day(5))=={'sleep'}
    # The phone reconnects: the upload brings day 4 onwards, uploaded before only up to 03:00.
    uploaded(at(10,9))
    assert db.pending_start(['sleep'],today)==day(4)
    assert db.completed_datasets(day(5))==set()
    assert db.completed_datasets(day(3))=={'sleep'}
    # In New York day 3 ended at 05:00 UTC, after the earlier upload, so it was incomplete too.
    assert db.pending_start(['sleep'],today,'America/New_York')==day(3)
    assert db.completed_datasets(day(3),'America/New_York')==set()
    # Fetched again shortly after the upload: up to date until the next upload, in case Garmin
    # was still processing; fetched again after that, up to date for good.
    for n in range(4,10):
        fetched(n,at(10,9,10))
    assert db.pending_start(['sleep'],today)==day(8)
    uploaded(at(10,10))
    assert db.pending_start(['sleep'],today)==day(4)
    for n in range(4,10):
        fetched(n,at(10,10,40))
    uploaded(at(10,11))
    assert db.pending_start(['sleep'],today)==day(8)
    # A day older than 60 days is no longer on the watch: an upload cannot complete it.
    fetched(-55,at(-55,12))
    fetched(-49,at(-49,12))
    uploaded(at(10,12))
    assert db.completed_datasets(day(-55))=={'sleep'}
    assert db.completed_datasets(day(-49))==set()


def test_failed_oldest_requested_day_cannot_disappear(db):
    db.request_range(['sleep'], date(2026,1,1))
    for day in range(2,11):
        db.write([], checkpoint=('sleep',date(2026,1,day)))
    assert db.pending_start(['sleep'],date(2026,1,10)) == date(2026,1,1)
    db.request_range(['sleep'],date(2026,1,5))
    assert db.pending_start(['sleep'],date(2026,1,10)) == date(2026,1,1)


def test_legacy_tag_type_identity_is_compatible():
    assert Record.from_mapping(point(tags={'ActivityID':42,'Device':'watch'})).key == Record.from_mapping(point(tags={'ActivityID':'42','Device':'watch'})).key


def test_grafana_reader_bootstrap_and_permissions(db, monkeypatch):
    import psycopg
    username='reader_'+uuid.uuid4().hex
    requester='requester_'+uuid.uuid4().hex
    monkeypatch.setenv('PGREADER_USER',username)
    monkeypatch.setenv('PGFETCH_REQUEST_USER',requester)
    monkeypatch.setenv('PGREADER_PASSWORD','test-reader-password')
    migrate.configure_reader(db.connection)
    migrate.configure_reader(db.connection)
    before=db.latest_fetch_request()
    monkeypatch.setenv('PGUSER',username)
    monkeypatch.setenv('PGPASSWORD','test-reader-password')
    with storage.connect() as reader:
        with reader.cursor() as cur:
            cur.execute('SELECT count(*) FROM all_records')
            assert cur.fetchone()[0] >= 0
            cur.execute('SELECT id, result FROM fetch_status')  # the result panel's read path
            assert cur.fetchall() is not None
            for statement in ("INSERT INTO persons VALUES ('should-not-be-allowed')", 'INSERT INTO fetch_requests DEFAULT VALUES'):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    cur.execute(statement)
                reader.rollback()
    # The "Fetch now" button's login may add a request and do nothing else, not even read.
    monkeypatch.setenv('PGUSER',requester)
    with storage.connect() as button:
        button.autocommit=True
        button.execute('INSERT INTO fetch_requests DEFAULT VALUES')
        for statement in ('SELECT max(id) FROM fetch_requests','SELECT count(*) FROM samples',"INSERT INTO persons VALUES ('should-not-be-allowed')",
                          'DELETE FROM fetch_requests','UPDATE fetch_requests SET requested_at=now()','SELECT result FROM fetch_status',
                          "UPDATE fetch_requests SET result='fake'"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                button.execute(statement)
    assert db.latest_fetch_request()>before
    monkeypatch.delenv('PGREADER_PASSWORD')
    migrate.configure_reader(db.connection)


class GrantSpy:
    """Stands in for a connection: records the SQL that configure_reader runs and can fail its GRANTs like a catalog collision."""
    def __init__(self, connection, failures=()):
        self.connection, self.failures, self.statements = connection, list(failures), []
        self.info = connection.info

    def transaction(self):
        return self.connection.transaction()

    def cursor(self):
        spy, real = self, self.connection.cursor()
        class Cursor:
            def __enter__(self):
                real.__enter__()
                return self
            def __exit__(self, *exc):
                return real.__exit__(*exc)
            def execute(self, query, params=None):
                text = query if isinstance(query, str) else query.as_string(spy.connection)
                spy.statements.append(text)
                if text.startswith('GRANT SELECT ON') and spy.failures:
                    raise spy.failures.pop(0)
                return real.execute(query, params)
            def fetchone(self):
                return real.fetchone()
            def fetchall(self):
                return real.fetchall()
        return Cursor()

    def grants(self):
        return [text for text in self.statements if text.startswith('GRANT')]


def reader_env(monkeypatch):
    username = 'reader_' + uuid.uuid4().hex
    monkeypatch.setenv('PGREADER_USER', username)
    monkeypatch.setenv('PGFETCH_REQUEST_USER', 'requester_' + uuid.uuid4().hex)
    monkeypatch.setenv('PGREADER_PASSWORD', 'test-reader-password')
    return username


def can_select(db, username, relation):
    return db.connection.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (username, relation)).fetchone()[0]


def test_reader_grants_cover_the_rollups_and_a_second_run_changes_nothing(db, monkeypatch):
    username = reader_env(monkeypatch)
    migrate.configure_reader(db.connection)
    for view in ('samples_hourly', 'gps_cells_daily', 'hourly_medians', 'wear_hourly', 'all_records', 'fetch_requests'):
        assert can_select(db, username, view), view
    # Already granted: no GRANT ... ON ALL TABLES rewriting every table's ACL while TimescaleDB's refresh jobs update the catalog.
    again = GrantSpy(db.connection)
    migrate.configure_reader(again)
    assert [g for g in again.grants() if g.startswith(('GRANT SELECT ON', 'GRANT INSERT', 'GRANT USAGE ON SEQUENCE'))] == []
    # A table dropped from the reader's access is granted again, and only that one.
    db.connection.execute(f'REVOKE SELECT ON sync_days FROM "{username}"')
    repair = GrantSpy(db.connection)
    migrate.configure_reader(repair)
    assert [g for g in repair.grants() if g.startswith('GRANT SELECT ON')] == [f'GRANT SELECT ON "public"."sync_days" TO "{username}"']
    assert can_select(db, username, 'sync_days')


def test_tables_created_after_prepare_reader_are_readable_without_a_grant(db, monkeypatch):
    username = reader_env(monkeypatch)
    migrate.prepare_reader(db.connection)
    with db.connection.transaction(force_rollback=True):
        db.connection.execute('CREATE TABLE created_by_a_migration (id int)')
        assert can_select(db, username, 'created_by_a_migration')


def test_reader_grants_retry_a_catalog_collision_with_a_background_job(db, monkeypatch, caplog):
    import psycopg
    username = reader_env(monkeypatch)
    db.connection.execute('SELECT 1')
    collision = psycopg.errors.InternalError_('tuple concurrently updated')
    spy = GrantSpy(db.connection, failures=[collision, collision])
    with mock.patch.object(migrate.time, 'sleep') as sleep, caplog.at_level('WARNING', logger='garmin_grafana.migrate'):
        migrate.configure_reader(spy)
    assert can_select(db, username, 'samples_hourly')
    assert sleep.call_args_list == [mock.call(0.5), mock.call(1.0)]
    assert [r.getMessage().count('retrying') for r in caplog.records] == [1, 1]
    assert 'tuple concurrently updated' in caplog.records[0].getMessage()


def test_reader_grants_retry_lock_conflicts_but_not_other_errors(db, monkeypatch):
    import psycopg
    username = reader_env(monkeypatch)
    migrate.configure_reader(db.connection)

    def spy(*failures):  # something is missing again, so there is a GRANT for the failure to hit
        db.connection.execute(f'REVOKE SELECT ON sync_days FROM "{username}"')
        return GrantSpy(db.connection, failures=failures)
    for transient in (psycopg.errors.SerializationFailure('x'), psycopg.errors.DeadlockDetected('x'), psycopg.errors.LockNotAvailable('x')):
        with mock.patch.object(migrate.time, 'sleep'):
            migrate.configure_reader(spy(transient))
        assert can_select(db, username, 'sync_days')
    with mock.patch.object(migrate.time, 'sleep') as sleep:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # not a collision: fail at once
            migrate.configure_reader(spy(psycopg.errors.InsufficientPrivilege('denied')))
        with pytest.raises(psycopg.errors.InternalError_):  # an internal error that is not the collision
            migrate.configure_reader(spy(psycopg.errors.InternalError_('something else')))
        assert sleep.call_count == 0
        with pytest.raises(psycopg.errors.InternalError_):  # gives up after a few attempts instead of looping
            migrate.configure_reader(spy(*[psycopg.errors.InternalError_('tuple concurrently updated')] * 6))
        assert sleep.call_count == 5


def test_corrected_activity_boundary_updates_instead_of_duplicating(db):
    tags={'Device':'watch','ActivityID':'42','ActivitySelector':'old-type'}
    original=Record.from_mapping(point('ActivitySummary',tags=tags,activityName='END',activityType='No Activity'))
    changed=Record.from_mapping(point('ActivitySummary',time='2026-01-01T03:00:00Z',tags={**tags,'ActivitySelector':'corrected-type'},activityName='END',activityType='No Activity'))
    assert original.key==changed.key
    db.write([original])
    db.write([changed])
    assert query(db,'SELECT time FROM records WHERE source_id=%s',(source(db),))==[(changed.time,)]


def test_skip_old_completed_datasets_but_force_manual_reconciliation(gf,db,monkeypatch):
    monkeypatch.setattr(gf,'store',db)
    monkeypatch.setattr(gf,'FETCH_SELECTION','heartrate')
    fetch=mock.Mock(return_value=[point()])
    monkeypatch.setattr(gf,'get_intraday_hr',fetch)
    db.write([],checkpoint=('heartrate','2026-01-01'))
    gf.daily_fetch_write('2026-01-01',skip_completed=True)
    fetch.assert_not_called()
    gf.daily_fetch_write('2026-01-01')
    fetch.assert_called_once()


def test_calendar_day_grouping_uses_source_date_and_dst(db):
    from importlib.util import spec_from_file_location,module_from_spec
    spec=spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    daily=Record.from_mapping(point('DailyStats',time='2026-11-01T00:00:00Z',totalSteps=5000),local_date=date(2026,11,1),timezone='America/New_York')
    db.write([daily])
    sql=next(q for label,q,fmt in module.dashboard_queries() if label=='Garmin-Grafana-Dashboard.json:1:A')
    sql=module.expand_query(sql.replace('${TimeZone:sqlstring}',"'America/New_York'"),source(db))
    rows=query(db,sql)
    # Use a window around the DST transition rather than the smoke-test January window.
    sql=sql.replace('2026-01-01T00:00:00Z','2026-11-01T00:00:00Z').replace('2026-01-10T00:00:00Z','2026-11-03T00:00:00Z')
    rows=query(db,sql)
    assert (datetime(2026,11,1,4,tzinfo=timezone.utc),5000) in rows
    times=[row[0] for row in rows]
    assert datetime(2026,11,2,5,tzinfo=timezone.utc) in times


def test_account_export_does_not_complete_online_api_checkpoint(gf,db,monkeypatch):
    monkeypatch.setattr(gf,'store',db)
    monkeypatch.setattr(gf,'FETCH_SELECTION','heartrate')
    monkeypatch.setattr(gf,'SYNC_NAMESPACE','account-export:')
    monkeypatch.setattr(gf,'get_intraday_hr',lambda day: [point()])
    gf.daily_fetch_write('2026-01-01')
    assert db.completed_datasets('2026-01-01')=={'account-export:heartrate'}
    assert 'heartrate' not in db.completed_datasets('2026-01-01')


def test_daily_query_local_window_includes_utc_midnight_markers(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    record=Record.from_mapping(point('VO2_Max',time='2026-01-01T00:00:00Z',VO2_max_value=50),local_date=date(2026,1,1),timezone='America/New_York')
    db.write([record])
    sql=next(q for label,q,fmt in module.dashboard_queries() if label.startswith('Garmin-Grafana-Dashboard.json:64:'))
    sql=module.expand_query(sql.replace('${TimeZone:sqlstring}',"'America/New_York'"),source(db))
    sql=sql.replace('2026-01-01T00:00:00Z','2026-01-01T05:00:00Z').replace('2026-01-10T00:00:00Z','2026-01-02T04:59:59Z')
    assert (datetime(2026,1,1,5,tzinfo=timezone.utc),50) in query(db,sql)


def test_database_failure_after_insert_rolls_back_entire_dataset(db):
    record=Record.from_mapping(point())
    def interrupted():
        for index in range(1001):
            yield replace(record,key=f'temporary-{index}')
        raise RuntimeError('interrupted after first batch')
    with pytest.raises(RuntimeError):
        db.write(interrupted(),checkpoint=('heartrate','2026-01-01'))
    assert query(db,'SELECT count(*) FROM samples WHERE source_id=%s',(source(db),))==[(0,)]
    assert db.completed_datasets('2026-01-01')==set()


def test_fetch_now_button_inserts_through_its_own_datasource_outside_the_kiosk():
    root=Path(__file__).resolve().parents[1]
    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get('panels',[]))
    owners={path.name for path in (root/'Grafana_Dashboard').glob('*.json') if any(p['id']==70 for p in panels(json.loads(path.read_text())['panels']))}
    assert owners=={'Garmin-Grafana-Dashboard.json'}  # the kiosk page hides grid-item-70 on every dashboard
    button=next(p for p in panels(json.loads((root/'Grafana_Dashboard/Garmin-Grafana-Dashboard.json').read_text())['panels']) if p['id']==70)
    update=button['options']['update']
    assert button['type']=='volkovlabs-form-panel' and update['method']=='datasource' and update['confirm']
    assert (update['datasource'],update['payload']['rawSql'])==('Garmin fetch request','INSERT INTO fetch_requests DEFAULT VALUES')
    request=(root/'Grafana_Datasource/fetch-request.yaml').read_text()
    assert 'name: Garmin fetch request' in request and 'user: grafana_fetch_request' in request
    compose=(root/'compose-example.yml').read_text()
    kiosk=compose[compose.index('  grafana-kiosk:'):compose.index('  kiosk:')]
    assert 'Grafana_Datasource:' not in kiosk and 'Grafana_Datasource/postgres.yaml:' in kiosk
    assert 'grid-item-70' in (root/'Grafana_Kiosk/index.html').read_text()


def test_dashboard_table_and_heatmap_field_contracts(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    queries={label:(sql,fmt) for label,sql,fmt in module.dashboard_queries()}
    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get('panels',[]))
    visited=set()
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        dashboard=json.loads(path.read_text())
        for panel in panels(dashboard['panels']):
            for target in panel.get('targets',[]):
                label=f'{path.name}:{panel["id"]}:{target["refId"]}'
                sql,fmt=queries[label];visited.add(label)
                if fmt=='table' and ('AS "Time"' in sql):
                    with db.connection.transaction(),db.connection.cursor() as cur:
                        cur.execute(module.expand_query(sql,source(db)))
                        assert 'Time' in [column.name for column in cur.description]
            if panel['type']=='marcusolsson-hourly-heatmap-panel':
                assert panel['options']['valueFieldName'] in ('Hourly Steps','Sleep Regularity')
                assert panel['options']['timeFieldName']=='Time'
    assert visited=={label for label,(sql,fmt) in queries.items() if fmt!='variable'}


def test_dashboard_missing_buckets_preserve_null_and_zero_semantics(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    statements={label:sql for label,sql,fmt in module.dashboard_queries()}
    sid=source(db)
    heart_rate=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:15:A'],sid))
    breathing=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:62:B'],sid))
    hrv=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:11:A'],sid))
    steps=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:68:A'],sid))
    # The smoothed lines leave out empty buckets, as Grafana's value filters did; averages never turn them into 0.
    assert heart_rate==[] and breathing==[] and len(hrv)>1 and all(row[1] is None for row in hrv)
    assert len(steps)>1 and all(row[1]==0 for row in steps)


def test_daily_panels_include_the_first_day_of_a_range_starting_mid_day(db):
    # "Last 7 days" starts mid-day: the first day's bucket must still get that day's record.
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    statements={label:sql for label,sql,fmt in module.dashboard_queries()}
    sid=source(db)
    db.write([Record.from_mapping(point('DailyStats','2026-01-01T00:00:00Z',totalSteps=5000),local_date=date(2026,1,1),timezone='UTC'),
              Record.from_mapping(point('DailyStats','2026-01-02T00:00:00Z',totalSteps=7000),local_date=date(2026,1,2),timezone='UTC')])
    sql=module.expand_query(statements['Garmin-Grafana-Dashboard.json:1:A'],sid).replace('2026-01-01T00:00:00Z','2026-01-01T12:00:00Z')
    assert query(db,sql)[:2]==[(datetime(2026,1,1,tzinfo=timezone.utc),5000.0),(datetime(2026,1,2,tzinfo=timezone.utc),7000.0)]


def test_health_today_tiles_stay_empty_until_today_is_synced(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    statements={label:sql for label,sql,fmt in module.dashboard_queries()}
    health=json.loads((Path(__file__).resolve().parents[1]/'Grafana_Dashboard/Garmin-Health-Overview.json').read_text())
    tiles={p['fieldConfig']['defaults']['displayName']:p for p in health['panels'] if p['gridPos']['y']==0}
    sid=source(db)
    def tile(name):
        return query(db,module.expand_query(statements[f"Garmin-Health-Overview.json:{tiles[name]['id']}:A"],sid))
    # The smoke-test range ends on 2026-01-10 (UTC): the watch last synced on the 9th, and Garmin
    # Connect returns a daily summary without steps for the 10th.
    db.write([Record.from_mapping(point('DailyStats','2026-01-08T00:00:00Z',totalSteps=9000)),
        Record.from_mapping(point('DailyStats','2026-01-09T00:00:00Z',totalSteps=4000)),
        Record.from_mapping(point('DailyStats','2026-01-10T00:00:00Z',totalSteps=None,bmrKilocalories=1700)),
        Record.from_mapping(point('SleepSummary','2026-01-09T06:00:00Z',sleepScore=81)),
        Record.from_mapping(point('StressIntraday','2026-01-09T12:00:00Z',stressLevel=30)),
        Record.from_mapping(point('VO2_Max','2026-01-02T00:00:00Z',VO2_max_value=48.4,VO2_max_value_cycling=None)),
        Record.from_mapping(point('VO2_Max','2026-01-05T00:00:00Z',VO2_max_value=None,VO2_max_value_cycling=51.0))])
    for name in ('Sleep score','Sleep','HRV','Resting HR','Battery at wake','Stress today','Steps today'):
        assert tiles[name]['options']['reduceOptions']['calcs']==['last'],name
        rows=tile(name)
        assert rows[-1]==(datetime(2026,1,10,tzinfo=timezone.utc),None),name
        assert all(value is not None for _,value in rows[:-1]),name
    assert [value for _,value in tile('Steps today')]==[9000,4000,None]
    assert [value for _,value in tile('Sleep score')]==[81,None]
    db.write([Record.from_mapping(point('DailyStats','2026-01-10T00:00:00Z',totalSteps=1200)),
        Record.from_mapping(point('SleepSummary','2026-01-10T06:00:00Z',sleepScore=74))])
    assert [value for _,value in tile('Steps today')]==[9000,4000,1200]
    assert [value for _,value in tile('Sleep score')]==[81,74]
    # Sleep comes in seconds and shows as hours and minutes (11h:12m), not decimal hours (11.2 h).
    db.write([Record.from_mapping(point('SleepSummary','2026-01-09T06:00:00Z',sleepScore=81,sleepTimeSeconds=40320))])
    assert [value for _,value in tile('Sleep')]==[40320,None]
    assert (tiles['Sleep']['fieldConfig']['defaults']['unit'],tiles['Sleep']['fieldConfig']['defaults']['decimals'])==('clocks',1)
    # VO2 max is the latest estimate, not a today value: running is preferred over cycling.
    assert tiles['VO2 max']['options']['reduceOptions']['calcs']==['lastNotNull']
    # Whether a VO2 max is good depends on age and sex, so the tile carries no rating colour.
    assert tiles['VO2 max']['fieldConfig']['defaults']['color']=={'fixedColor':'text','mode':'fixed'}
    assert [value for _,value in tile('VO2 max')]==[48.4]
    order=[p['fieldConfig']['defaults']['displayName'] for p in sorted(tiles.values(),key=lambda p:p['gridPos']['x'])]
    assert order[-2:]==['VO2 max','Intensity, 7 days']


def test_ski_season_groups_sports_and_counts_vertical_on_foot(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    statements={label:sql for label,sql,fmt in module.dashboard_queries()}
    ski=json.loads((Path(__file__).resolve().parents[1]/'Grafana_Dashboard/Garmin-Ski-Season.json').read_text())
    tiles={p['fieldConfig']['defaults']['displayName']:p for p in ski['panels'] if p['gridPos']['y']==0}
    titled={p['title']:p for p in ski['panels']}
    sid=source(db)
    def run(panel,ref='A'):
        return query(db,module.expand_query(statements[f"Garmin-Ski-Season.json:{panel['id']}:{ref}"],sid))
    def activity(n,day,kind,**values):
        start={'Activity_ID':n,'activityName':f'Session {n}','activityType':kind,**values}
        end={'Activity_ID':n,'activityName':'END','activityType':'No Activity'}
        return [Record.from_mapping(point('ActivitySummary',f'2026-01-{day:02d}T09:00:00Z',tags={'ActivityID':str(n)},**start)),
                Record.from_mapping(point('ActivitySummary',f'2026-01-{day:02d}T12:00:00Z',tags={'ActivityID':str(n)},**end))]
    # The smoke-test range ends on 2026-01-10 (UTC).
    db.write(activity(1,8,'backcountry_skiing_snowboarding_ws',elevationGain=1200,elevationLoss=1150,elapsedDuration=14400,movingDuration=12000,activityTrainingLoad=200)
             +activity(2,9,'resort_skiing_snowboarding_ws',elevationGain=50,elevationLoss=4000,elapsedDuration=18000,maxSpeed=20,activityTrainingLoad=100)
             +activity(3,9,'cycling',elevationGain=900,elapsedDuration=7200)
             +activity(4,7,'trail_running',elevationGain=400,elapsedDuration=3600,movingDuration=3600)
             +activity(5,6,'strength_training',elapsedDuration=3000))
    assert run(tiles['Ski season'])==[('Day 41',)]
    assert run(tiles['Vertical, 7 days'])==[(1600.0,)]
    assert run(tiles['Uphill time, 7 days'])==[(18000.0,)]
    assert run(tiles['Longest uphill, 4 weeks'])==[(14400.0,)]
    assert run(tiles['Strength, 14 days'])==[(1,)]
    assert [(sport,value) for _,sport,value in run(titled['Climbing rate on uphill sessions'])]==[('Hiking & trail',400.0),('Ski touring',360.0)]
    season={row[0]:row[1:] for row in run(titled['Season on snow'])}
    assert season=={'Ski touring':(1,1,14400.0,1200.0,1150.0,None,None),'Resort skiing':(1,1,18000.0,None,4000.0,None,72.0)}
    assert len(run(titled['Snow sessions']))==2
    goal=run(titled['Vertical on foot per week'],'B')
    assert goal and all(value==2000 for _,value in goal)


def test_gps_activity_dropdown_and_heatmap_queries(db):
    spec=importlib.util.spec_from_file_location('queries',Path(__file__).resolve().parents[1]/'scripts/validate_dashboard_queries.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    statements={label:sql for label,sql,fmt in module.dashboard_queries()}
    # An empty list would make every Garmin Stats panel fail.
    assert query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:ActivityGPS'],source(db)))==[('No GPS activity in this period',)]
    def gps(selector,time,lat):
        return Record.from_mapping(point('ActivityGPS',time=time,tags={'Device':'watch','ActivitySelector':selector},Latitude=lat,Longitude=-0.1))
    db.write([gps('b-run','2026-01-02T00:00:00Z',51.50001),gps('b-run','2026-01-02T00:00:01Z',51.50002),
        gps('a-ride','2026-01-03T00:00:00Z',51.6),gps('c-outside','2026-02-01T00:00:00Z',51.7)])
    refresh_rollups()
    selectors=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:ActivityGPS'],source(db)))
    assert selectors==[('a-ride',),('b-run',)]
    cells=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:69:I'],source(db)))
    assert sorted(cells)==[(51.5,-0.1),(51.6,-0.1)]


def test_rollups_are_real_time_and_pick_up_backfills_on_refresh(db):
    assert query(db, 'SELECT view_name, materialized_only FROM timescaledb_information.continuous_aggregates ORDER BY 1') == [
        ('gps_cells_daily', False), ('hourly_medians', False), ('samples_hourly', False), ('wear_hourly', False)]
    # The whole history stays in the refresh window, so a backfill of any age is materialized on the next run.
    assert query(db, """SELECT config->'start_offset', schedule_interval FROM timescaledb_information.jobs
        WHERE proc_name = 'policy_refresh_continuous_aggregate'""") == [(None, timedelta(minutes=5))] * 4
    watermark = query(db, """SELECT _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(mat_hypertable_id))
        FROM _timescaledb_catalog.continuous_agg WHERE user_view_name = 'samples_hourly'""")[0][0]
    hour = max(watermark, datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
    def stress(minute, level):
        return Record.from_mapping(point('StressIntraday', (hour + timedelta(minutes=minute)).isoformat(), stressLevel=level), local_date=hour.date(), timezone='UTC')
    def rollup():
        return query(db, "SELECT samples, total, lowest, highest FROM samples_hourly WHERE source_id = %s AND dataset = 'StressIntraday'", (source(db),))
    db.write([stress(0, 20), stress(3, -1)])
    assert rollup() == [(1, 20, 20, 20)]  # newer than the last refresh: aggregated at query time, without Garmin's -1
    refresh_rollups()
    db.write([stress(6, 40)])
    assert rollup() == [(1, 20, 20, 20)]  # an hour that is already materialized waits for the next refresh
    refresh_rollups()
    assert rollup() == [(2, 60, 20, 40)]


def test_hourly_medians_and_band_wearing_combine_rollups_with_raw_edge_hours(db):
    spec = importlib.util.spec_from_file_location('queries', Path(__file__).resolve().parents[1] / 'scripts/validate_dashboard_queries.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = {label: sql for label, sql, fmt in module.dashboard_queries()}
    watermark = query(db, """SELECT max(_timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(mat_hypertable_id)))
        FROM _timescaledb_catalog.continuous_agg WHERE user_view_name IN ('hourly_medians', 'wear_hourly')""")[0][0]
    day = max(watermark, datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)
    def at(hour, minute):
        return day + timedelta(hours=hour, minutes=minute)
    def heart(hour, minute, **values):
        return Record.from_mapping(point('HeartRateIntraday', at(hour, minute).isoformat(), **values))
    db.write([heart(10, 0, HeartRate=60), heart(10, 2, HeartRate=70), heart(10, 4, HeartRate=90), heart(10, 40, Other=1),
              heart(11, 0, HeartRate=80), heart(11, 2, HeartRate=100), heart(12, 10, HeartRate=50)])
    def panel(ref):  # 10:01 to 12:11: hour 11 from the rollups, the partial hours 10 and 12 from raw samples
        return query(db, module.expand_query(statements[f'Garmin-Grafana-Dashboard.json:67:{ref}'], source(db),
                                             start=at(10, 1).isoformat(), end=at(12, 11).isoformat()))
    medians = [(at(10, 0), 80), (at(11, 0), 90), (at(12, 0), 50)]  # 10:00 is before the range; 10:40 has no heart rate
    # Only the changes: worn in the 2-minute slots with a heart rate reading, off before, between and after them.
    worn = [(at(10, 0), 0), (at(10, 2), 1), (at(10, 6), 0), (at(11, 0), 1), (at(11, 4), 0), (at(12, 10), 1), (at(12, 12), 0)]
    assert (panel('E'), panel('D')) == (medians, worn)  # aggregated at query time
    refresh_rollups()
    assert (panel('E'), panel('D')) == (medians, worn)  # materialized


def test_record_fetch_stamps_the_source_for_the_last_fetch_panel(db):
    # Other accounts' stamps from earlier tests or runs may already be there; only the difference counts.
    def stamps():
        return dict(query(db, 'SELECT key, updated_at FROM app_info WHERE key LIKE %s', ('last_fetch:%',)))
    before = stamps()
    db.record_fetch()
    first = stamps()
    assert set(first) - set(before) == {f'last_fetch:{source(db)}'}
    db.record_fetch()
    second = stamps()
    assert set(second) == set(first)
    assert second[f'last_fetch:{source(db)}'] >= first[f'last_fetch:{source(db)}']


def test_fetch_now_results_are_recorded_and_readable_without_widening_the_button(db):
    def request():
        with db.connection.transaction(), db.connection.cursor() as cur:
            cur.execute('INSERT INTO fetch_requests DEFAULT VALUES RETURNING id')
            return cur.fetchone()[0]

    def latest():
        return query(db, 'SELECT id, finished_at IS NOT NULL, new_points, result FROM fetch_status')[0]
    first, second = request(), request()
    assert latest() == (second, False, None, None)  # waiting for the fetcher
    # a sync that wrote new rows: both requests it started with get its result
    start = db.changed_rows
    db.write([Record.from_mapping({'measurement': 'HeartRateIntraday', 'time': '2026-01-01T10:00:00+00:00', 'tags': {'Device': 'd'},
                                   'fields': {'HeartRate': 61}}, timezone='UTC')])
    assert db.changed_rows == start + 1
    assert db.finish_fetch_requests(second, start, 'UTC') == '1 new point'
    assert query(db, 'SELECT count(*) FROM fetch_requests WHERE id IN (%s,%s) AND result=%s AND new_points=1 AND finished_at IS NOT NULL', (first, second, '1 new point')) == [(2,)]
    # nothing is overwritten, and a request made later is not served by an earlier sync
    third = request()
    assert db.finish_fetch_requests(second, db.changed_rows, 'UTC') is None
    assert latest() == (third, False, None, None)
    # a sync that found nothing says since when, in the account's time zone; an unfinished one says so
    text = db.finish_fetch_requests(third, db.changed_rows, 'Europe/Berlin', 'incomplete')
    assert text == 'incomplete: nothing new since Thu 01 Jan 11:00' or text.startswith('incomplete: nothing new since ')
    fourth = request()
    assert db.finish_fetch_requests(fourth, db.changed_rows, 'UTC', 'failed (OSError)').startswith('failed (OSError): nothing new since')
    # an unchanged rewrite adds no points
    before = db.changed_rows
    db.write([Record.from_mapping({'measurement': 'HeartRateIntraday', 'time': '2026-01-01T10:00:00+00:00', 'tags': {'Device': 'd'},
                                   'fields': {'HeartRate': 61}}, timezone='UTC')])
    assert db.changed_rows == before
