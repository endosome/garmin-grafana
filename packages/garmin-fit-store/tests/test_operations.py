from datetime import datetime, timedelta, timezone

import pytest
from garmin_fit_sdk import Decoder, Stream
from garmin_fit_store import canonical, decode, operations, store
from garmin_fit_store.operations import fit_seconds

from conftest import CORPUS

T0 = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)


@pytest.fixture
def activity(connection):
    data = (CORPUS / 'activity.fit').read_bytes()
    return store.import_file(connection, data, source_id=8100), decode(data)


def sdk_ok(data):
    return Decoder(Stream.from_byte_array(bytearray(data))).read()[1] == []


def snapshot(connection, file_id):
    return (connection.execute('SELECT sha256, message_count FROM fit.files WHERE id = %s', (file_id,)).fetchone(),
            connection.execute("SELECT count(*), md5(string_agg(fields::text || dev::text, '' ORDER BY seq)) FROM (SELECT seq, fields, dev FROM fit.messages WHERE file_id = %s UNION ALL SELECT seq, fields, dev FROM fit.records WHERE file_id = %s) x", (file_id, file_id)).fetchone())


def check(connection, original_id, new_id, reference):
    data = bytes(connection.execute('SELECT data FROM fit.blobs WHERE file_id = %s', (new_id,)).fetchone()[0])
    assert sdk_ok(data)
    assert canonical(decode(data)) == canonical(reference)
    assert store.verify(connection, new_id) == []
    parent, operation = connection.execute('SELECT parent_id, operation FROM fit.files WHERE id = %s', (new_id,)).fetchone()
    assert parent == original_id and operation['operation']


def test_time_shift_moves_every_carrier_and_leaves_the_original(connection, activity):
    original_id, ir = activity
    before = snapshot(connection, original_id)
    new_id = operations.time_shift(connection, original_id, 3600)
    check(connection, original_id, new_id, operations.ref_time_shift(ir, 3600))
    assert snapshot(connection, original_id) == before
    shifted = decode(store.export_file(connection, new_id))
    originals = {n: v for m, o in zip(shifted.messages, ir.messages) for n, v in o.fields.items() if n == 253}
    for new, old in zip(shifted.messages, ir.messages):
        if 253 in old.fields:
            assert new.fields[253] == old.fields[253] + 3600
    session = next(m for m in shifted.messages if m.global_num == 18)
    assert session.fields[2] == next(m for m in ir.messages if m.global_num == 18).fields[2] + 3600  # start_time
    file_id_message = shifted.messages[0]
    assert file_id_message.fields[4] == ir.messages[0].fields[4] + 3600  # time_created
    start, = connection.execute('SELECT start_time FROM fit.files WHERE id = %s', (new_id,)).fetchone()
    assert start == T0 + timedelta(hours=1)
    assert connection.execute('SELECT min(time) FROM fit.records WHERE file_id = %s', (new_id,)).fetchone()[0] == T0 + timedelta(hours=1)


def test_time_shift_backwards_and_developer_timestamps(connection):
    from garmin_fit_store import ir
    fit = ir.FitFile()
    description = fit.add_definition(206, [(0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 8, 7), (15, 1, 2)])
    fit.add_message(description, {0: 0, 1: 0, 2: 6, 3: 'time', 15: 253})
    record = fit.add_definition(20, [(253, 4, 0x86)], dev_fields=[(0, 4, 0)])
    fit.add_message(record, {253: 1_100_000_000}, dev={(0, 0): 1_100_000_005})
    file_id = store.import_file(connection, __import__('garmin_fit_store').encode(fit), source_id=8101)
    new_id = operations.time_shift(connection, file_id, -100)
    check(connection, file_id, new_id, operations.ref_time_shift(fit, -100))
    assert decode(store.export_file(connection, new_id)).messages[1].dev == {(0, 0): 1_099_999_905}


def test_device_changer_rewrites_file_id_and_the_creator(connection, activity):
    original_id, ir = activity
    new_id = operations.change_device(connection, original_id, manufacturer='wahoo_fitness', product='7', serial_number=999)
    check(connection, original_id, new_id, operations.ref_change_device(ir, 'wahoo_fitness', '7', 999))
    changed = decode(store.export_file(connection, new_id))
    assert changed.messages[0].fields[1] == 32 and changed.messages[0].fields[3] == 999  # wahoo_fitness is manufacturer 32
    device_info = next(m for m in changed.messages if m.global_num == 23)
    assert device_info.fields[2] == 32 and device_info.fields[3] == 999
    assert 'product' not in changed.messages[0].fields or changed.messages[0].fields.get(2) in (None, 7)


def test_device_changer_by_name_and_errors(connection, activity):
    original_id, ir = activity
    new_id = operations.change_device(connection, original_id, manufacturer='garmin', product='fr955')
    check(connection, original_id, new_id, operations.ref_change_device(ir, 'garmin', 'fr955'))
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match='unknown manufacturer'):
        operations.change_device(connection, original_id, manufacturer='nobody')
    connection.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match='unknown product'):
        operations.change_device(connection, original_id, product='no_such_watch')
    connection.rollback()
    with pytest.raises(ValueError):
        operations.ref_change_device(ir, manufacturer='nobody')
    with pytest.raises(ValueError):
        operations.ref_change_device(ir, product='no_such_watch')


@pytest.mark.parametrize('close_gap', [True, False])
def test_section_remover(connection, activity, close_gap):
    original_id, ir = activity
    start, end = T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)
    before = snapshot(connection, original_id)
    new_id = operations.remove_section(connection, original_id, start, end, close_gap)
    reference = operations.ref_remove_section(ir, fit_seconds(start), fit_seconds(end), close_gap)
    check(connection, original_id, new_id, reference)
    assert snapshot(connection, original_id) == before
    result = decode(store.export_file(connection, new_id))
    records = [m for m in result.messages if m.global_num == 20]
    assert len(records) == 20
    lap = next(m for m in result.messages if m.global_num == 19)
    assert lap.fields[7] == (20_000 if close_gap else 30_000)  # total_elapsed_time
    assert lap.fields[8] == (20_000 if close_gap else 20_000) or lap.fields[8] == 29_500 - 10_000
    if close_gap:
        assert records[10].fields[253] == records[9].fields[253] + 1
    else:
        assert records[10].fields[253] == records[9].fields[253] + 11
    session = next(m for m in result.messages if m.global_num == 18)
    assert session.fields[16] == pytest.approx(sum(m.fields[3] for m in records) / 20, abs=1)  # avg_heart_rate


def test_section_remover_edge_cases(connection, activity):
    original_id, ir = activity
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):
        operations.remove_section(connection, original_id, T0 + timedelta(seconds=5), T0 + timedelta(seconds=5))
    connection.rollback()
    # a section that starts before the activity and ends inside the first lap; one entirely after it
    for start, end in ((-5, 7), (40, 50), (0, 30)):
        s, e = T0 + timedelta(seconds=start), T0 + timedelta(seconds=end)
        new_id = operations.remove_section(connection, original_id, s, e, True)
        check(connection, original_id, new_id, operations.ref_remove_section(ir, fit_seconds(s), fit_seconds(e), True))


def test_a_derived_file_can_be_derived_again_and_unknown_source_fails(connection, activity):
    original_id, ir = activity
    first = operations.time_shift(connection, original_id, 60)
    second = operations.time_shift(connection, first, 60)
    check(connection, first, second, operations.ref_time_shift(operations.ref_time_shift(ir, 60), 60))
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match='no fit.files row'):
        operations.time_shift(connection, -1, 1)
    connection.rollback()


# ---- Field Merger and File Combiner ----

def recording(start, seconds, heart_rate=True, offset=0, serial=123):
    from garmin_fit_store import builder
    messages = [("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": serial, "time_created": start})]
    for i in range(seconds):
        fields = {"timestamp": start + timedelta(seconds=i), "distance": float(i * 3), "speed": 3.0, "power": 150 + i}
        if heart_rate:
            fields["heart_rate"] = 100 + i + offset
        messages.append(("record", fields))
    end = start + timedelta(seconds=seconds - 1)
    messages += [("lap", {"timestamp": end, "start_time": start, "total_elapsed_time": float(seconds - 1), "total_timer_time": float(seconds - 1),
                          "total_distance": float((seconds - 1) * 3), "avg_power": 1, "message_index": 0}),
                 ("session", {"timestamp": end, "start_time": start, "sport": "running", "total_elapsed_time": float(seconds - 1),
                              "total_timer_time": float(seconds - 1), "total_distance": float((seconds - 1) * 3), "avg_power": 1, "message_index": 0}),
                 ("activity", {"timestamp": end, "num_sessions": 1, "type": "manual", "total_timer_time": float(seconds - 1)})]
    return builder.build_ir(messages)


def stored(connection, fit_file, source):
    from garmin_fit_store import encode
    return store.import_file(connection, encode(fit_file), source_id=source)


def test_field_merger_adds_the_donor_field_to_the_definition(connection):
    target, donor = recording(T0.replace(tzinfo=None), 20, heart_rate=False), recording(T0.replace(tzinfo=None) + timedelta(seconds=2), 30, offset=100)
    target_id, donor_id = stored(connection, target, 8200), stored(connection, donor, 8201)
    new_id = operations.merge_fields(connection, target_id, donor_id, [3], tolerance_seconds=0.5)
    check(connection, target_id, new_id, operations.ref_merge_fields(target, donor, [3], 0.5))
    merged = decode(store.export_file(connection, new_id))
    records = [m for m in merged.messages if m.global_num == 20]
    assert [m.fields.get(3) for m in records[:4]] == [None, None, 200, 201]  # the donor starts two seconds in
    assert records[19].fields[3] == 217
    assert connection.execute('SELECT count(*) FROM fit.definitions WHERE file_id = %s', (new_id,)).fetchone()[0] > connection.execute(
        'SELECT count(*) FROM fit.definitions WHERE file_id = %s', (target_id,)).fetchone()[0]


def test_field_merger_tolerance_and_unknown_donor_field(connection):
    target, donor = recording(T0.replace(tzinfo=None), 10, heart_rate=False), recording(T0.replace(tzinfo=None), 10, offset=50)
    target_id, donor_id = stored(connection, target, 8210), stored(connection, donor, 8211)
    new_id = operations.merge_fields(connection, target_id, donor_id, [3, 200], tolerance_seconds=0)
    check(connection, target_id, new_id, operations.ref_merge_fields(target, donor, [3, 200], 0))
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match='no fit.files row'):
        operations.merge_fields(connection, target_id, -1, [3])
    connection.rollback()


def test_file_combiner_joins_split_recordings(connection):
    first, second = recording(T0.replace(tzinfo=None), 20), recording(T0.replace(tzinfo=None) + timedelta(seconds=600), 25, offset=7, serial=124)
    first_id, second_id = stored(connection, first, 8300), stored(connection, second, 8301)
    new_id = operations.combine(connection, [second_id, first_id])  # given out of order: the file orders them by time
    check(connection, first_id, new_id, operations.ref_combine([first, second]))
    combined = decode(store.export_file(connection, new_id))
    kinds = [m.global_num for m in combined.messages]
    assert kinds.count(0) == 1 and kinds.count(34) == 1 and kinds.count(20) == 45 and kinds[-1] == 34
    assert [m.fields[254] for m in combined.messages if m.global_num == 18] == [0, 1]
    assert [m.fields[254] for m in combined.messages if m.global_num == 19] == [0, 1]
    activity = combined.messages[-1]
    assert activity.fields[1] == 2 and activity.fields[253] == max(m.fields[253] for m in combined.messages if m.global_num == 18)
    times = [m.fields[253] for m in combined.messages if m.global_num == 20]
    assert times == sorted(times)
    assert connection.execute('SELECT parent_id FROM fit.files WHERE id = %s', (new_id,)).fetchone() == (first_id,)


def test_file_combiner_without_an_activity_message_and_edge_cases(connection):
    import psycopg
    from garmin_fit_store import ir
    only = ir.FitFile()
    record = only.add_definition(20, [(253, 4, 0x86), (3, 1, 2)])
    only.add_message(record, {253: 1_100_000_000, 3: 90})
    only_id = stored(connection, only, 8310)
    new_id = operations.combine(connection, [only_id])
    check(connection, only_id, new_id, operations.ref_combine([only]))
    assert decode(store.export_file(connection, new_id)).messages[-1].global_num == 34
    with pytest.raises(psycopg.errors.RaiseException, match='nothing to combine'):
        operations.combine(connection, [])
    connection.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match='unknown file'):
        operations.combine(connection, [only_id, -5])
    connection.rollback()


def test_file_combiner_refuses_colliding_developer_data(connection):
    import psycopg
    from garmin_fit_store import ir
    files = []
    for application in (1, 2):
        fit = ir.FitFile()
        d = fit.add_definition(207, [(1, 16, 13), (3, 1, 2)])
        fit.add_message(d, {1: [application] * 16, 3: 0})
        record = fit.add_definition(20, [(253, 4, 0x86)])
        fit.add_message(record, {253: 1_100_000_000 + application})
        files.append(stored(connection, fit, 8320 + application))
    with pytest.raises(psycopg.errors.RaiseException, match='developer data index'):
        operations.combine(connection, files)
    connection.rollback()
