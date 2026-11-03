"""The collector keeps the FIT files it downloads in the fit schema, next to the samples it writes from them."""
import logging
import os
import uuid

import pytest
from garmin_fit_store import store as fit_store

from garmin_grafana import storage
from garmin_grafana.storage import PostgresStore
from helpers import only, zip_bytes
from test_fit_parsing import running_fit


@pytest.fixture
def real_store(gf):
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* pointing to a disposable TimescaleDB database')
    from garmin_grafana import migrate
    with storage.connect() as setup:
        migrate.migrate(setup)
    connection = storage.connect()
    backend = PostgresStore(account='fit-' + uuid.uuid4().hex, connection=connection)
    gf.store = backend
    yield backend
    connection.close()


def serve_fit(gf, fit_bytes):
    gf.garmin_obj.download_activity.side_effect = lambda activity_id, dl_fmt=None: zip_bytes({'a.fit': fit_bytes})


def source_of(backend):
    with backend.connection.transaction(), backend.connection.cursor() as cur:
        return backend.source_id(cur)


def test_the_file_is_linked_to_the_activity_next_to_its_samples(gf, real_store):
    fit_bytes = running_fit()
    serve_fit(gf, fit_bytes)
    points = gf.fetch_activity_GPS({424242: 'running'})
    assert points.fit_files == [(424242, fit_bytes)]
    gf.write_records(points)
    assert gf.store_fit_files(points.fit_files) == 0

    connection, source = real_store.connection, source_of(real_store)
    (file_id, activity_id, sha) = connection.execute(
        'SELECT id, garmin_activity_id, sha256 FROM fit.files WHERE source_id = %s', (source,)).fetchone()
    assert activity_id == 424242
    samples = connection.execute("SELECT count(*) FROM samples WHERE source_id = %s AND tags->>'ActivityID' = '424242'", (source,)).fetchone()[0]
    assert samples == len(only(points, 'ActivityGPS')) == connection.execute('SELECT count(*) FROM fit.records WHERE file_id = %s', (file_id,)).fetchone()[0]
    assert fit_store.verify(connection, file_id) == []
    assert gf.store_fit_files(points.fit_files) == 0  # a repeat stores nothing new
    assert connection.execute('SELECT count(*) FROM fit.files WHERE source_id = %s', (source,)).fetchone() == (1,)


def test_the_fit_store_can_be_turned_off(gf, real_store, monkeypatch):
    monkeypatch.setattr(gf, 'FIT_STORE_ENABLED', False)
    assert gf.store_fit_files([(1, running_fit())]) == 0
    gf.retry_pending_fit_files()
    assert real_store.connection.execute('SELECT count(*) FROM fit.files WHERE source_id = %s', (source_of(real_store),)).fetchone() == (0,)


def test_a_failure_is_logged_remembered_and_retried(gf, real_store, monkeypatch, caplog):
    data = running_fit()
    real = fit_store.import_file

    def broken(*args, **kwargs):
        raise RuntimeError('disk full')
    monkeypatch.setattr(fit_store, 'import_file', broken)
    with caplog.at_level(logging.ERROR):
        assert gf.store_fit_files([(9, data)]) == 1
    assert 'disk full' in caplog.text
    connection, source = real_store.connection, source_of(real_store)
    assert connection.execute('SELECT attempts, error FROM fit.pending_imports WHERE source_id = %s', (source,)).fetchone() == (1, 'RuntimeError: disk full')
    assert gf.store_fit_files([(9, data)]) == 1  # the same bytes are one pending row
    assert connection.execute('SELECT attempts FROM fit.pending_imports WHERE source_id = %s', (source,)).fetchone() == (2,)

    monkeypatch.setattr(fit_store, 'import_file', real)
    gf.retry_pending_fit_files()
    assert connection.execute('SELECT count(*) FROM fit.pending_imports WHERE source_id = %s', (source,)).fetchone() == (0,)
    assert connection.execute('SELECT garmin_activity_id FROM fit.files WHERE source_id = %s', (source,)).fetchone() == (9,)


def test_retry_keeps_files_that_still_fail(gf, real_store, caplog):
    connection, source = real_store.connection, source_of(real_store)
    fit_store.record_pending(connection, b'not a fit file', source, 3, 'first failure')
    with caplog.at_level(logging.INFO):
        gf.retry_pending_fit_files()
    assert 'retried pending files, 0 stored, 1 still failing' in caplog.text
    assert connection.execute('SELECT attempts FROM fit.pending_imports WHERE source_id = %s', (source,)).fetchone()[0] == 2


def test_retry_failures_never_escape(gf, monkeypatch, caplog):
    monkeypatch.setattr(gf.store, 'source_id', lambda cur: (_ for _ in ()).throw(RuntimeError('db down')))
    with caplog.at_level(logging.ERROR):
        gf.retry_pending_fit_files()
    assert 'retrying pending files failed' in caplog.text


def test_pending_record_failure_is_logged(gf, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError('db down')
    monkeypatch.setattr(fit_store, 'import_file', broken)
    monkeypatch.setattr(fit_store, 'record_pending', broken)
    with caplog.at_level(logging.ERROR):
        assert gf.store_fit_files([(1, b'x')]) == 1
    assert 'could not record the pending file either' in caplog.text
