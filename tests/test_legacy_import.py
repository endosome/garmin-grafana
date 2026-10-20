"""Bounded legacy conversion, resume safety, validation and HTTP query behavior."""
from datetime import datetime, timezone
import json
import runpy
import sys
from unittest import mock
import pytest
from garmin_grafana import legacy_import as legacy
from garmin_grafana import storage, migrate
from garmin_grafana.domain import Record
from test_storage import db, point, query, source


class Reader:
    def records(self, measurement, start, end):
        for index in range(start,end):
            yield Record.from_mapping(point(measurement,time=f'2026-01-01T00:00:00.{index:09d}Z',value=index))


def test_bounded_import_resume_validation_and_dry_run(db):
    reader=Reader()
    assert legacy.import_measurement(reader,db,'Test',0,2001,migration_id='initial',window_ns=1001)==2001
    assert query(db,'SELECT imported FROM legacy_progress WHERE source_id=%s',(source(db),)) == [(2001,)]
    assert legacy.import_measurement(reader,db,'Test',0,2001,migration_id='initial',window_ns=1001)==0
    assert legacy.import_measurement(reader,db,'Test',0,2001,migration_id='initial',window_ns=1001,validate=True)==2001
    assert legacy.import_measurement(reader,None,'Test',0,2001,migration_id='preview',window_ns=1001,dry_run=True)==2001


def test_interrupted_window_replays_without_duplicates(db):
    class Broken(Reader):
        def records(self,*args):
            for index,record in enumerate(super().records(*args)):
                if index==1001:
                    raise RuntimeError('interrupted')
                yield record
    with pytest.raises(RuntimeError):
        legacy.import_measurement(Broken(),db,'Test',0,2002,migration_id='retry',window_ns=2002)
    assert query(db,'SELECT count(*) FROM legacy_progress WHERE source_id=%s',(source(db),))==[(0,)]
    assert legacy.import_measurement(Reader(),db,'Test',0,2002,migration_id='retry',window_ns=2002)==2002
    assert query(db,'SELECT count(*) FROM legacy_archive WHERE source_id=%s',(source(db),))==[(2002,)]
    assert query(db,'SELECT count(*) FROM records WHERE source_id=%s',(source(db),))==[(2002,)]


def response(*items):
    result=mock.MagicMock()
    result.__enter__.return_value=result
    result.iter_lines.return_value=[b'', *[json.dumps(item).encode() for item in items]]
    return result


def test_http_reader_chunks_tags_and_nanoseconds():
    reader=legacy.LegacyReader('http://legacy/','GarminStats','reader','secret')
    assert reader.session.auth==('reader','secret')
    data={'results':[{'series':[{'name':'HeartRateIntraday','tags':{'Device':'watch'},'columns':['time','Device','HeartRate','Note'],
            'values':[[1767225600123456789,'watch',70.5,None]]}]}]}
    with mock.patch.object(reader.session,'get',return_value=response(data)) as get:
        records=list(reader.records('HeartRateIntraday',0,2000000000000000000))
        assert get.call_args.kwargs['stream'] is True
        assert get.call_args.kwargs['params']['chunk_size']==1000
        assert records[0].nanoseconds==789
        assert records[0].tags=={'Device':'watch'}
        assert records[0].values=={'HeartRate':70.5,'Note':None}
        assert records[0].original_time=='2026-01-01T00:00:00.123456789Z'
    for data in ({'error':'failed'}, {'results':[{'error':'failed'}]}):
        with mock.patch.object(reader.session,'get',return_value=response(data)):
            with pytest.raises(RuntimeError,match='Legacy query failed'):
                list(reader.query('invalid'))
    reader.session.close()


def test_measurements_bounds_empty_series_and_identifier_escaping():
    reader=legacy.LegacyReader('http://legacy','db')
    with mock.patch.object(reader,'query',return_value=iter([{'columns':['name'],'values':[['HeartRateIntraday'],['unknown']]}])):
        assert list(reader.measurements())==['HeartRateIntraday','unknown']
    with mock.patch.object(reader,'query',side_effect=[iter([{'values':[[5,1]]}]),iter([{'values':[[9,1]]}])]):
        assert reader.bounds('series')==(5,10)
    with mock.patch.object(reader,'query',return_value=[]):
        assert reader.bounds('empty') is None
    assert legacy.quote_identifier('a"b\\c')=='"a\\"b\\\\c"'
    reader.session.close()


def run_cli(monkeypatch,*args):
    monkeypatch.setattr(sys,'argv',['legacy_import','--account','test','--migration-id','source-v1',*args])
    legacy.main()


def test_cli_dry_run_and_range(monkeypatch):
    reader=mock.MagicMock()
    reader.user_tags.return_value=set()
    reader.measurements.return_value=['HeartRateIntraday','empty']
    reader.bounds.side_effect=[(0,3),None]
    with mock.patch.object(legacy,'LegacyReader',return_value=reader), mock.patch.object(legacy,'PostgresStore') as store, mock.patch.object(legacy,'import_measurement') as ingest:
        run_cli(monkeypatch,'--dry-run','--start','1','--end','2')
        ingest.assert_called_once()
        assert ingest.call_args.args[3:]==(1,2)
        assert ingest.call_args.kwargs['dry_run'] is True
        reader.session.close.assert_called_once()
        store.return_value.close.assert_called_once()
    with pytest.raises(SystemExit):
        run_cli(monkeypatch,'--window-hours','0')


def test_cli_measurement_and_failure_closes_connections(monkeypatch):
    reader=mock.MagicMock()
    reader.user_tags.return_value=set()
    reader.bounds.return_value=(0,3)
    with mock.patch.object(legacy,'LegacyReader',return_value=reader),mock.patch.object(legacy,'PostgresStore') as store,mock.patch.object(legacy,'import_measurement',side_effect=ValueError('failed')):
        with pytest.raises(ValueError):
            run_cli(monkeypatch,'--measurement','Test','--validate')
        reader.session.close.assert_called_once()
        store.return_value.close.assert_called_once()


def test_migrate_cli_and_checksum_match(db,monkeypatch):
    connection = db.connection
    with mock.patch.object(migrate,'connect',return_value=connection),mock.patch.object(migrate,'configure_reader') as configure:
        migrate.main()
        configure.assert_called_once_with(connection)



def test_legacy_user_filter_and_multiuser_guard(monkeypatch):
    reader=legacy.LegacyReader('http://legacy','db',user_tag="runner'one")
    with mock.patch.object(reader.session,'get',return_value=response({'results':[]})) as get:
        assert list(reader.records('Test',0,1))==[]
        assert 'User_ID' in get.call_args.kwargs['params']['q']
    with mock.patch.object(reader,'query',return_value=[{'values':[['User_ID','runner']]}]):
        assert reader.user_tags()=={'runner'}
    reader.session.close()
    backend=mock.MagicMock()
    backend.user_tags.return_value={'one','two'}
    with mock.patch.object(legacy,'LegacyReader',return_value=backend),mock.patch.object(legacy,'PostgresStore'):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch)
        with pytest.raises(SystemExit):
            run_cli(monkeypatch,'--dry-run','--validate')


def test_cli_user_filter_requires_existing_tag(monkeypatch):
    reader=mock.MagicMock()
    reader.user_tags.return_value={'runner'}
    reader.measurements.return_value=[]
    with mock.patch.object(legacy,'LegacyReader',return_value=reader),mock.patch.object(legacy,'PostgresStore'):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch,'--legacy-user','typo')
        run_cli(monkeypatch,'--legacy-user','runner')
        assert reader.user_tag=='runner'


def test_legacy_date_only_dataset_recovers_calendar_date():
    reader=legacy.LegacyReader('http://legacy','db')
    data={'results':[{'series':[{'columns':['time','VO2_max_value'], 'values':[[1767225600000000000,50]]}]}]}
    with mock.patch.object(reader.session,'get',return_value=response(data)):
        record=list(reader.records('VO2_Max',0,1767225600000000001))[0]
        assert str(record.local_date)=='2026-01-01'
    reader.session.close()


def test_module_preview_reads_source_without_postgresql(monkeypatch, caplog):
    import logging
    from helpers import run_module_command
    session = mock.MagicMock()
    timestamp = 1767225600123456789
    def http_response(url, *, params, **kwargs):
        if params['q'].startswith('SHOW TAG VALUES'):
            return response({'results': []})
        return response({'results': [{'series': [{
            'name': 'HeartRateIntraday', 'tags': {'Device': 'synthetic-watch'},
            'columns': ['time', 'HeartRate'], 'values': [[timestamp, 72]],
        }]}]})
    session.get.side_effect = http_response
    with mock.patch.object(legacy.requests, 'Session', return_value=session), \
            mock.patch.object(storage, 'connect', side_effect=AssertionError('preview must not connect')) as connect, \
            caplog.at_level(logging.INFO):
        run_module_command(
            monkeypatch, 'garmin_grafana.legacy_import', '--account', 'synthetic',
            '--migration-id', 'module-preview', '--measurement', 'HeartRateIntraday', '--dry-run',
        )
    connect.assert_not_called()
    assert session.get.call_count == 4  # account tags, two bounds, one streamed window
    session.close.assert_called_once()
    assert 'Previewed 1 records' in caplog.text
