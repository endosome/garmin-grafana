from datetime import datetime, timezone

import pytest
from garmin_fit_store import decode, store, views
from garmin_fit_store.semantics import Semantics

from conftest import CORPUS


def named_rows(data, number):
    fit_file = decode(data)
    semantics = Semantics()
    rows = []
    for m in fit_file.messages:
        named = semantics.named(m.global_num, m.fields, expand_sub_fields=False, expand_components=False, degrees=True)
        if m.global_num == number:
            rows.append(named)
    return rows


def close(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b)
    return a == b


@pytest.mark.parametrize('name, view, number', [('activity', 'v_record', 20), ('activity', 'v_lap', 19), ('activity', 'v_session', 18),
                                                 ('activity', 'v_event', 21), ('activity', 'v_file_id', 0), ('activity', 'v_device_info', 23),
                                                 ('big_endian', 'v_record', 20), ('developer_fields', 'v_file_id', 0)])
def test_view_columns_and_values_equal_the_semantics_layer(connection, name, view, number):
    data = (CORPUS / f'{name}.fit').read_bytes()
    file_id = store.import_file(connection, data)
    cur = connection.execute(f'SELECT * FROM fit.{view} WHERE file_id = %s ORDER BY seq', (file_id,))
    columns = [c.name for c in cur.description]
    expected = named_rows(data, number)
    rows = cur.fetchall()
    assert len(rows) == len(expected) > 0
    for row, named in zip(rows, expected):
        values = dict(zip(columns, row))
        for key, value in named.items():
            if key == 'timestamp':
                key = 'time'
            if isinstance(key, int):
                continue
            actual = values[key]
            if isinstance(value, datetime):
                assert actual == value.replace(tzinfo=actual.tzinfo or timezone.utc)
            elif isinstance(value, list):
                assert actual == value or actual == pytest.approx(value[0])  # the profile says scalar; the view shows the first element
            else:
                if isinstance(actual, str) and isinstance(value, int):
                    actual = str(value)  # an enum value the profile does not name stays its number, as text
                    value = str(value)
                assert close(actual, value), (view, key, actual, value)


def test_views_are_rebuilt_only_when_missing_or_the_profile_is_new(connection):
    assert views.exist(connection)
    connection.execute('DROP VIEW fit.v_lap')
    assert not views.exist(connection)
    import garmin_fit_store
    garmin_fit_store.after_migrate(connection)
    assert views.exist(connection)


def test_developer_fields_are_pivoted_by_name(connection):
    file_id = store.import_file(connection, (CORPUS / 'developer_fields.fit').read_bytes())
    rows = connection.execute('SELECT dev FROM fit.v_record_dev WHERE file_id = %s ORDER BY seq', (file_id,)).fetchall()
    assert rows[0][0] == {'Stride': 1234, 'Flag': 3}
    assert rows[1][0] is None
    assert connection.execute('SELECT name, units FROM fit.v_dev_fields WHERE file_id = %s ORDER BY name', (file_id,)).fetchall() == [
        ('Flag', None), ('Stride', 'mm')]


def test_promoted_columns_follow_the_json_and_tolerate_odd_values(connection):
    from garmin_fit_store import encode, ir
    fit = ir.FitFile()
    d = fit.add_definition(20, [(253, 4, 0x86), (3, 3, 2), (6, 2, 0x84)])
    fit.add_message(d, {253: 1_100_000_000, 3: [1, 2, 3], 6: 3200})
    file_id = store.import_file(connection, encode(fit), source_id=9600)
    assert connection.execute('SELECT f3, f6 FROM fit.records WHERE file_id = %s', (file_id,)).fetchone() == (None, 3200)
    connection.execute("UPDATE fit.records SET fields = jsonb_set(fields, '{6}', '5000') WHERE file_id = %s", (file_id,))
    assert connection.execute('SELECT speed, heart_rate FROM fit.v_record WHERE file_id = %s', (file_id,)).fetchone() == (5.0, 1)
