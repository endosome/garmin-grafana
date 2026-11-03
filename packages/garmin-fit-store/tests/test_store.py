import pytest
import psycopg

from garmin_fit_store import FitError, canonical, decode, store

import rawfit as r


def counts(connection):
    return connection.execute("""SELECT (SELECT count(*) FROM fit.files), (SELECT count(*) FROM fit.blobs),
        (SELECT count(*) FROM fit.definitions), (SELECT count(*) FROM fit.messages), (SELECT count(*) FROM fit.records)""").fetchone()


def test_every_corpus_file_round_trips_through_the_database(connection, corpus_file):
    name, data = corpus_file
    file_id = store.import_file(connection, data, check_crc=False)
    assert store.verify(connection, file_id) == []
    assert canonical(decode(store.export_file(connection, file_id))) == canonical(decode(data, check_crc=False))
    assert bytes(connection.execute('SELECT data FROM fit.blobs WHERE file_id = %s', (file_id,)).fetchone()[0]) == data


def test_external_files_round_trip_through_the_database(connection, external_file):
    file_id = store.import_file(connection, external_file, check_crc=False)
    assert store.verify(connection, file_id) == []


def test_a_second_import_returns_the_first_row_and_writes_nothing(connection, corpus_file):
    data = corpus_file[1]
    first = store.import_file(connection, data, source_id=7, check_crc=False)
    before = counts(connection)
    assert store.import_file(connection, data, source_id=7, check_crc=False) == first
    assert counts(connection) == before
    assert store.import_file(connection, data, source_id=8, check_crc=False) != first  # dedup is per source


def test_a_failed_import_leaves_no_partial_rows(connection, corpus_file, monkeypatch):
    before = counts(connection)

    def fail(*args, **kwargs):
        raise RuntimeError('boom')
    monkeypatch.setattr(store, '_flush_records', fail)
    monkeypatch.setattr(store, '_flush_messages', fail)
    with pytest.raises(RuntimeError):
        store.import_file(connection, corpus_file[1], source_id=99, check_crc=False)
    assert counts(connection) == before
    with pytest.raises(FitError):
        store.import_file(connection, b'not a fit file')
    assert counts(connection) == before


def test_records_and_messages_are_split_and_timed(connection):
    from conftest import CORPUS
    file_id = store.import_file(connection, (CORPUS / 'activity.fit').read_bytes(), source_id=1234)
    assert connection.execute('SELECT count(*) FROM fit.records WHERE file_id = %s', (file_id,)).fetchone() == (30,)
    assert connection.execute('SELECT count(*) FROM fit.messages WHERE file_id = %s AND global_num = 20', (file_id,)).fetchone() == (0,)
    start, end = connection.execute('SELECT start_time, end_time FROM fit.files WHERE id = %s', (file_id,)).fetchone()
    assert (end - start).total_seconds() == 30
    assert connection.execute("SELECT fields->>'3' FROM fit.records WHERE file_id = %s ORDER BY seq LIMIT 1", (file_id,)).fetchone() == ('120',)
    assert connection.execute("SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name = 'records'").fetchone()


def test_activity_id_is_kept(connection):
    from conftest import CORPUS
    file_id = store.import_file(connection, (CORPUS / 'developer_fields.fit').read_bytes(), source_id=55, garmin_activity_id=987)
    assert connection.execute('SELECT garmin_activity_id FROM fit.files WHERE id = %s', (file_id,)).fetchone() == (987,)


def test_verify_reports_a_changed_row(connection):
    from conftest import CORPUS
    file_id = store.import_file(connection, (CORPUS / 'activity.fit').read_bytes(), source_id=4321)
    connection.execute("UPDATE fit.records SET fields = jsonb_set(fields, '{3}', '99') WHERE file_id = %s AND seq = 5", (file_id,))
    assert any('differs' in d for d in store.verify(connection, file_id))
    connection.execute('DELETE FROM fit.blobs WHERE file_id = %s', (file_id,))
    assert 'no stored bytes' in store.verify(connection, file_id)[0]
    connection.rollback()
    with pytest.raises(KeyError):
        store.load_ir(connection, -1)


def test_profile_rows_are_loaded_once(connection):
    from garmin_fit_store import profile_tables
    assert connection.execute('SELECT count(*) FROM fit.profile_versions').fetchone() == (1,)
    assert profile_tables.load(connection) is False
    assert connection.execute("SELECT message_name FROM fit.profile_messages WHERE message_number = 20").fetchone() == ('record',)
