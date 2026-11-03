import os
from datetime import datetime
from unittest import mock

import psycopg
import pytest
from garmin_fit_store import FitError, builder, decode, decoder, encode, ir, profile_tables, store, views
from garmin_fit_store.__main__ import main
from garmin_fit_store.profile import Profile
from garmin_fit_store.semantics import Accumulator, Semantics

from conftest import CORPUS


# ---- semantics ----

def test_accumulator_rolls_over():
    accumulator = Accumulator()
    assert accumulator.add(20, 5, 250, 8) == 250
    assert accumulator.add(20, 5, 4, 8) == 260  # 8-bit wrap
    accumulator.reset(20, 5, 100)
    assert accumulator.add(20, 5, 110, 8) == 110


def test_components_of_components_and_accumulation():
    semantics = Semantics()
    first = semantics.named(20, {8: [0x34, 0x12, 0x01]})  # compressed_speed_distance: 12 bit speed, 12 bit distance
    assert first['speed'] == 0x234 / 100 and first['enhanced_speed'] == pytest.approx(first['speed'])
    second = semantics.named(20, {8: [0x34, 0x12, 0x02]})
    assert second['distance'] > first['distance']


def test_subfield_components_and_names_of_subfields():
    semantics = Semantics()
    event = semantics.named(21, {0: 42, 3: 0x04030201})
    assert event['event'] == 'front_gear_change' and event['rear_gear_num'] == 1 and event['front_gear'] == 4
    named = semantics.named(21, {0: 42, 3: 0x04030201}, expand_components=False)
    assert 'gear_change_data' in named
    assert semantics.raw(21, {'event': 'front_gear_change', 'gear_change_data': 0x04030201}) == {0: 42, 3: 0x04030201}
    with pytest.raises(KeyError):
        semantics.raw(21, {'no_such_field': 1})


def test_scaled_arrays_and_invalid_array_elements():
    semantics = Semantics()
    named = semantics.named(20, {6: [1000, 2000]}, wire={6: 0x84})
    assert named['speed'] == [1.0, 2.0]
    assert semantics.raw(20, {'speed': [1.0, None]}) == {6: [1000, 65535]}
    assert semantics.named(20, {250: [1, 255]}, wire={250: 2}) == {250: [1, None]}


# ---- views and the builder ----

def test_view_expressions_for_every_field_shape():
    profile = Profile()
    base = {'number': 1, 'name': 'x', 'type': 'uint8', 'base_type': 'uint8', 'array': False, 'scale': [1], 'offset': [0],
            'units': '', 'bits': [], 'components': [], 'accumulate': False, 'sub_fields': []}
    assert 'jsonb_typeof' in views.column_expression(dict(base, base_type='string', type='string'), profile)
    assert 'AT TIME ZONE' in views.column_expression(dict(base, type='local_date_time'), profile)
    assert views.column_expression(dict(base, base_type='uint64', type='uint64'), profile).endswith('::numeric')
    assert views.column_expression(dict(base, base_type='float32', type='float32'), profile).endswith('double precision')
    assert views.column_expression(dict(base, array=True), profile) == "fields->'1'"
    assert '* (180.0' in views.column_expression(dict(base, units='semicircles'), profile)


def test_builder_by_number_and_scale():
    data = builder.build_fit([('record', {'timestamp': datetime(2026, 1, 1), 'speed': 3.2, 140: ('uint16', [1, 2]), 'heart_rate': 80}),
                              ('file_id', {'type': 'activity', 'product_name': 'x', 'manufacturer': 'garmin'})])
    fit = decode(data)
    assert fit.messages[0].fields[140] == [1, 2] and fit.messages[0].fields[6] == 3200 and fit.messages[1].fields[8] == 'x'
    with pytest.raises(StopIteration):
        builder.build_fit([('file_id', {'type': 'nonsense'})])


# ---- decoder odds ----

def test_a_decoder_failure_becomes_a_fit_error():
    with mock.patch.object(decoder.values, 'decode', side_effect=ValueError('bad')):
        with pytest.raises(FitError, match='malformed records'):
            decode((CORPUS / 'activity.fit').read_bytes())


# ---- store ----

def test_batches_flush_while_importing(connection, monkeypatch):
    monkeypatch.setattr(store, 'BATCH', 5)
    data = (CORPUS / 'activity.fit').read_bytes()
    file_id = store.import_file(connection, data, source_id=9100)
    assert store.verify(connection, file_id) == []


def test_pending_imports_in_the_package(connection):
    source = 9200
    good = (CORPUS / 'activity.fit').read_bytes()
    store.record_pending(connection, good, source, 5, 'first')
    store.record_pending(connection, good, source, 5, 'again')
    store.record_pending(connection, b'bad bytes', source, 6, 'x' * 600)
    assert connection.execute('SELECT attempts, length(error) FROM fit.pending_imports WHERE source_id = %s ORDER BY id', (source,)).fetchall() == [(2, 5), (1, 500)]
    assert store.retry_pending(connection, source) == (1, 1)
    assert connection.execute('SELECT garmin_activity_id FROM fit.files WHERE source_id = %s', (source,)).fetchall() == [(5,)]
    assert connection.execute('SELECT attempts FROM fit.pending_imports WHERE source_id = %s', (source,)).fetchall() == [(2,)]


# ---- the command line ----

@pytest.fixture
def cli(database, monkeypatch):
    for key, variable in (('host', 'PGHOST'), ('port', 'PGPORT'), ('user', 'PGUSER'), ('password', 'PGPASSWORD'), ('dbname', 'PGDATABASE')):
        if key in database:
            monkeypatch.setenv(variable, str(database[key]))
    return main


def test_import_export_verify_roundtrip_commands(cli, tmp_path, capsys):
    corpus = tmp_path / 'files'
    corpus.mkdir()
    (corpus / 'a.fit').write_bytes((CORPUS / 'activity.fit').read_bytes())
    (corpus / 'b.fit').write_bytes((CORPUS / 'zero_header_crc.fit').read_bytes())
    assert cli(['roundtrip', str(corpus)]) == 0
    assert '2 files, 0 with differences' in capsys.readouterr().out
    (corpus / 'broken.fit').write_bytes(b'junk')
    assert cli(['roundtrip', str(corpus)]) == 1
    out = capsys.readouterr().out
    assert 'broken.fit: FitError' in out and '3 files, 1 with differences' in out
    (corpus / 'broken.fit').unlink()
    assert cli(['import', '--source-id', '9300', '--garmin-activity-id', '77', str(corpus / 'a.fit')]) == 0
    imported = capsys.readouterr().out.split(': ')[1].strip()
    exported = tmp_path / 'out.fit'
    assert cli(['export', imported, str(exported)]) == 0
    assert ir.canonical(decode(exported.read_bytes())) == ir.canonical(decode((CORPUS / 'activity.fit').read_bytes()))
    assert cli(['verify', imported]) == 0
    assert f'{imported}: ok' in capsys.readouterr().out
    assert cli(['import', '--ignore-crc', str(corpus / 'b.fit')]) == 0
    assert cli(['verify']) == 0


def test_verify_command_reports_a_difference(cli, capsys, database):
    with psycopg.connect(**database) as connection:
        file_id = store.import_file(connection, (CORPUS / 'activity.fit').read_bytes(), source_id=9400)
        connection.execute("UPDATE fit.records SET fields = jsonb_set(fields, '{3}', '1') WHERE file_id = %s AND seq = 4", (file_id,))
    assert cli(['verify', str(file_id)]) == 1
    assert 'differs' in capsys.readouterr().out


def test_profile_sql_command(capsys):
    assert main(['profile-sql']) == 0
    out = capsys.readouterr().out
    assert out.startswith('INSERT INTO fit.profile_versions') and 'CREATE VIEW fit.v_record' in out and 'CREATE VIEW fit.v_record_dev' in out
    assert profile_tables._quote(True) == 'true' and profile_tables._quote("it's") == "'it''s'" and profile_tables._quote(2.5) == '2.5'


def test_semantics_all_invalid_components_and_verify_length_mismatch(connection):
    assert 'speed' not in Semantics().named(20, {8: [255, 255, 255]}, wire={8: 0x0D})
    data = (CORPUS / 'activity.fit').read_bytes()
    file_id = store.import_file(connection, data, source_id=9500)
    connection.execute('DELETE FROM fit.records WHERE file_id = %s AND seq = 6', (file_id,))
    assert any('messages, the original has' in d for d in store.verify(connection, file_id))


def test_developer_description_with_an_unknown_base_type_is_ignored():
    import rawfit as r
    data = r.fit([r.definition(0, 206, [(0, 1, r.UINT8), (1, 1, r.UINT8), (2, 1, r.UINT8)]), r.data(0, bytes([0, 0, 99])),
                  r.definition(1, 20, [(253, 4, r.UINT32)], dev=[(0, 1, 0)]), r.data(1, r.u32(1_100_000_000) + bytes([7]))])
    assert decode(data).messages[1].dev == {(0, 0): [7]} or decode(data).messages[1].dev == {(0, 0): 7}


def test_helpers_of_the_references():
    from garmin_fit_store.operations import fit_seconds
    from datetime import timezone
    assert fit_seconds(datetime(2026, 1, 1, tzinfo=timezone.utc)) == fit_seconds(datetime(2026, 1, 1))


def test_no_garmin_derived_profile_data_is_in_the_package_tree():
    """Plan A5: the FIT profile is built from the installed SDK at run time, never committed."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.fit', '.jsonl', '.pyc') or '.venv' in path.parts or path.name.startswith('.coverage'):
            continue
        assert path.suffix not in ('.json', '.xlsx', '.csv'), f'{path} looks like a profile artifact'
        text = path.read_text(errors='ignore')
        tables = re.findall(r"^\s*\d+:\s*['\"][a-z_]+['\"],?\s*$", text, re.M)
        assert len(tables) < 10, f'{path} holds a table of numbered names'


def test_profile_json_and_named_json_commands(capsys):
    import json
    assert main(['profile-json']) == 0
    assert json.loads(capsys.readouterr().out)['messages']['20']['name'] == 'record'
    assert main(['named-json', str(CORPUS / 'activity.fit')]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]['8'] == 'Test Watch' and rows[2]['253'].startswith('2026-01-01T10:00:00')
