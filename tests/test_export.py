"""Streaming export preserves exact timestamps and native fields in account-scoped ZIPs."""
from datetime import datetime, timezone, timedelta
import json
import sys
from unittest import mock
import zipfile
import pytest
from garmin_grafana import export as exporter
from garmin_grafana.domain import Record
from test_storage import db, point


def test_date_ranges():
    now=datetime(2026,1,10,12,tzinfo=timezone.utc)
    assert exporter.date_range(last_n_days=3,now=now)==(now-timedelta(days=3),now)
    start,end=exporter.date_range(start='2026-01-01',end='2026-01-01')
    assert (end-start).days==1
    start,end=exporter.date_range(now=now)
    assert (end-start).days==31
    with pytest.raises(ValueError):
        exporter.date_range(last_n_days=0)
    with pytest.raises(ValueError):
        exporter.date_range(start='2026-02-01',end='2026-01-01')
    with pytest.raises(ValueError):
        exporter.date_range(start='bad-date')


def test_export_streaming_accounts_and_inclusive_dates(db,tmp_path):
    db.write([Record.from_mapping(point()),Record.from_mapping(point('unknown/dataset',value={'nested':[1]},flag=True))])
    path=tmp_path/'export.zip'
    start,end=exporter.date_range(start='2026-01-01',end='2026-01-01')
    assert exporter.export(db.connection,path,account=db.account,start=start,end=end)==2
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist())=={'manifest.json','HeartRateIntraday.jsonl','unknown%2Fdataset.jsonl'}
        item=json.loads(archive.read('HeartRateIntraday.jsonl'))
        assert item['nanoseconds']==789 and item['values']['HeartRate']==72
        assert item['time'].endswith('123456789Z')
        assert json.loads(archive.read('unknown%2Fdataset.jsonl'))['values']['flag'] is True
    with pytest.raises(ValueError,match='account not found'):
        exporter.export(db.connection,path,account='missing',start=start,end=end)
    empty=tmp_path/'empty.zip'
    start,end=exporter.date_range(start='2026-02-01',end='2026-02-01')
    assert exporter.export(db.connection,empty,account=db.account,start=start,end=end)==0
    with zipfile.ZipFile(empty) as archive:
        assert archive.namelist()==['manifest.json']


def test_export_cli(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['export','--account','test','--output','/tmp/unused.zip','--last-n-days','1'])
    with mock.patch.object(exporter,'connect'),mock.patch.object(exporter,'export',return_value=5):
        exporter.main()
    assert 'Exported 5 records' in capsys.readouterr().out


def test_export_refuses_to_overwrite_existing_backup(db,tmp_path):
    db.write([Record.from_mapping(point())])
    path=tmp_path/'already.zip'
    path.write_bytes(b'original backup')
    start,end=exporter.date_range(start='2026-01-01',end='2026-01-01')
    with pytest.raises(FileExistsError):
        exporter.export(db.connection,path,account=db.account,start=start,end=end)
    assert path.read_bytes()==b'original backup'
