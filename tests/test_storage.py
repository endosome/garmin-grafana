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
    monkeypatch.setenv('PGREADER_USER',username)
    monkeypatch.setenv('PGREADER_PASSWORD','test-reader-password')
    migrate.configure_reader(db.connection)
    migrate.configure_reader(db.connection)
    monkeypatch.setenv('PGUSER',username)
    monkeypatch.setenv('PGPASSWORD','test-reader-password')
    with storage.connect() as reader:
        with reader.cursor() as cur:
            cur.execute('SELECT count(*) FROM all_records')
            assert cur.fetchone()[0] >= 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("INSERT INTO persons VALUES ('should-not-be-allowed')")
        reader.rollback()
    monkeypatch.delenv('PGREADER_PASSWORD')
    migrate.configure_reader(db.connection)


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


def test_dashboard_rows_nest_only_when_collapsed():
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        dashboard=json.loads(path.read_text())
        ids=[panel['id'] for panel in dashboard['panels']]
        for row in (panel for panel in dashboard['panels'] if panel['type']=='row'):
            assert bool(row.get('panels'))==row['collapsed'],(path.name,row['title'])
            ids+=[panel['id'] for panel in row.get('panels',[])]
            assert all(panel['gridPos']['y']>row['gridPos']['y'] for panel in row.get('panels',[]))
        assert len(ids)==len(set(ids)),path.name


def test_stat_field_regex_matches_the_display_name():
    # Grafana matches reduceOptions.fields against the display name, so a renamed field
    # selected by its column name shows "No data".
    import re
    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get('panels',[]))
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        for panel in panels(json.loads(path.read_text())['panels']):
            fields=panel.get('options',{}).get('reduceOptions',{}).get('fields','')
            name=panel.get('fieldConfig',{}).get('defaults',{}).get('displayName')
            if panel['type']=='stat' and fields.startswith('/') and name:
                assert re.search(fields.strip('/'),name),(path.name,panel['id'])


def test_dashboard_all_values_are_sql_literals():
    # Grafana inserts a custom "All" value unformatted, even for ${Name:sqlstring}.
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        for variable in json.loads(path.read_text())['templating']['list']:
            if variable.get('allValue'):
                assert re.fullmatch(r"'[^']*'",variable['allValue']),(path.name,variable['name'])


def test_weekly_bars_cover_their_week():
    # Weekly rows are stamped with the Monday the week starts, so the bar must be drawn after it.
    def panels(items):
        for panel in items:
            yield panel
            yield from panels(panel.get('panels',[]))
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        for panel in panels(json.loads(path.read_text())['panels']):
            custom=panel.get('fieldConfig',{}).get('defaults',{}).get('custom',{})
            sql=' '.join(target.get('rawSql','') for target in panel.get('targets',[]))
            if custom.get('drawStyle')=='bars' and "date_trunc('week'" in sql:
                assert custom.get('barAlignment')==1,(path.name,panel['id'])


def test_dashboard_colours_are_grafana_colours():
    # An unknown name such as 'grey' makes Grafana fail to draw the panel.
    import re
    hues='red|orange|yellow|green|blue|purple'
    valid=re.compile(rf'^((super-light-|light-|semi-dark-|dark-)?({hues})|text|transparent|#[0-9a-fA-F]{{3,8}}|rgba?\(.*\))$')
    for path in (Path(__file__).resolve().parents[1]/'Grafana_Dashboard').glob('*.json'):
        for colour in re.findall(r'"(?:fixedColor|color)": "([^"]*)"',path.read_text()):
            assert valid.match(colour),(path.name,colour)


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
    steps=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:68:A'],sid))
    assert len(heart_rate)>1 and all(row[1] is None for row in heart_rate)
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
    assert run(tiles['Uphill time, 7 days'])==[(5.0,)]
    assert run(tiles['Longest uphill, 4 weeks'])==[(4.0,)]
    assert run(tiles['Strength, 14 days'])==[(1,)]
    assert [(sport,value) for _,sport,value in run(titled['Climbing rate on uphill sessions'])]==[('Hiking & trail',400.0),('Ski touring',360.0)]
    season={row[0]:row[1:] for row in run(titled['Season on snow'])}
    assert season=={'Ski touring':(1,1,4.0,1200.0,1150.0,None,None),'Resort skiing':(1,1,5.0,None,4000.0,None,72.0)}
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
    selectors=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:ActivityGPS'],source(db)))
    assert selectors==[('a-ride',),('b-run',)]
    cells=query(db,module.expand_query(statements['Garmin-Grafana-Dashboard.json:69:I'],source(db)))
    assert sorted(cells)==[(51.5,-0.1),(51.6,-0.1)]
