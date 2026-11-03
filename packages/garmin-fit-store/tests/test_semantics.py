from datetime import datetime, timezone

import pytest
from garmin_fit_sdk import Decoder, Stream
from garmin_fit_store import decode
from garmin_fit_store.profile import Profile, build_profile
from garmin_fit_store.semantics import Semantics

from conftest import CORPUS


def sdk_named(data):
    seen = []
    _, errors = Decoder(Stream.from_byte_array(bytearray(data))).read(
        merge_heart_rates=False, mesg_listener=lambda number, message: seen.append((number, message)))
    return seen, errors


def check_against_sdk(data):
    theirs, errors = sdk_named(data)
    if any('Compressed timestamp' in str(e) for e in errors):
        pytest.skip('the official decoder cannot read compressed timestamps')
    assert errors == []
    semantics, profile = Semantics(), Profile()
    fit_file = decode(data, check_crc=False)
    ours = fit_file.messages
    assert len(ours) == len(theirs)
    for (number, message), mine in zip(theirs, ours):
        wire = {n: base for n, _, base in fit_file.definitions[mine.def_id].fields}
        named = semantics.named(mine.global_num, mine.fields, wire=wire)
        if number in (206, 207):
            continue  # the SDK reshapes the developer data messages; their raw values are compared in test_codec.py
        for key, value in message.items():
            if key == 'developer_fields':
                continue
            field = profile.field(number, key) if isinstance(key, int) else None
            if key in named and isinstance(named[key], datetime) is False and field is None:
                pass
            if (number, key) == (206, 'fit_base_type_id'):
                continue  # we name the base type, the SDK leaves the number
            assert key in named, (number, key)
            if isinstance(value, datetime):
                value = value.replace(tzinfo=timezone.utc)
            if value is not None and named[key] != value and _local_time(profile, number, key):
                continue
            assert named[key] == value, (number, key, named[key], value)


def _local_time(profile, number, key):
    message = profile.message(number)
    return any(f['name'] == key and f['type'] == 'local_date_time' for f in message['fields'].values())


def test_named_values_equal_the_sdks_for_the_corpus(corpus_file):
    name, data = corpus_file
    if name in ('compressed_timestamps', 'chained'):
        pytest.skip('the official decoder cannot read these')
    check_against_sdk(data)


def test_named_values_equal_the_sdks_for_external_files(external_file):
    check_against_sdk(external_file)


def test_the_inverse_maps_named_values_back_to_raw(corpus_file):
    semantics = Semantics()
    for message in decode(corpus_file[1], check_crc=False).messages:
        named = semantics.named(message.global_num, message.fields, expand_sub_fields=False, expand_components=False)
        assert semantics.raw(message.global_num, named) == message.fields, (message.global_num, named)


def test_the_inverse_for_external_files(external_file):
    semantics = Semantics()
    for message in decode(external_file, check_crc=False).messages:
        named = semantics.named(message.global_num, message.fields, expand_sub_fields=False, expand_components=False)
        assert semantics.raw(message.global_num, named) == message.fields


def test_subfields_and_components():
    semantics = Semantics()
    event = semantics.named(21, {0: 12, 1: 0, 3: 3000})  # a virtual partner speed alert carries a speed in its data field
    assert event['event'] == 'virtual_partner_pace' and event['data'] == 3000 and event['virtual_partner_speed'] == 3.0
    record = semantics.named(20, {2: 2502, 6: 3200})
    assert record['enhanced_altitude'] == pytest.approx(0.4) and record['enhanced_speed'] == 3.2


def test_accumulated_fields_roll_over():
    semantics = Semantics()
    first = semantics.named(20, {5: 100, 6: 0})  # distance is accumulated; seeds the baseline
    assert first['distance'] == 1.0


def test_degrees_and_local_times():
    semantics = Semantics()
    assert semantics.named(20, {0: 2 ** 30}, degrees=True)['position_lat'] == 90.0
    assert semantics.named(20, {0: 2 ** 30})['position_lat'] == 2 ** 30
    assert semantics.named(34, {5: 100})['local_timestamp'] == datetime(1989, 12, 31, 0, 1, 40)
    assert semantics.raw(34, {'local_timestamp': datetime(1989, 12, 31, 0, 1, 40)}) == {5: 100}


def test_unknown_messages_and_fields_pass_through_raw():
    semantics = Semantics()
    assert semantics.named(60000, {0: 5, 1: [1, 2]}) == {0: 5, 1: [1, 2]}
    assert semantics.named(20, {3: 100, 250: 7}) == {'heart_rate': 100, 250: 7}
    assert semantics.raw(20, {'heart_rate': 100, 250: 7}) == {3: 100, 250: 7}


def test_a_profile_document_alone_decides_every_conversion():
    document = {'sdk_version': 'test', 'version': '0.0', 'types': {'color': {'1': 'red'}}, 'messages': {'70000': {
        'name': 'thing', 'fields': {'0': {'number': 0, 'name': 'hue', 'type': 'color', 'base_type': 'enum', 'array': False,
                                          'scale': [1], 'offset': [0], 'units': '', 'bits': [], 'components': [],
                                          'accumulate': False, 'sub_fields': []},
                                    '1': {'number': 1, 'name': 'tenths', 'type': 'uint16', 'base_type': 'uint16', 'array': False,
                                          'scale': [10], 'offset': [5], 'units': 'm', 'bits': [], 'components': [],
                                          'accumulate': False, 'sub_fields': []}}}}}
    semantics = Semantics(Profile(document))
    assert semantics.named(70000, {0: 1, 1: 100, 2: 9}) == {'hue': 'red', 'tenths': 5.0, 2: 9}
    assert semantics.named(20, {3: 100}) == {3: 100}  # the real record message is not in this profile: raw fallback
    assert semantics.raw(70000, {'hue': 'red', 'tenths': 5.0}) == {0: 1, 1: 100}


def test_the_profile_document_is_stable():
    from garmin_fit_store import profile
    first = profile.dumps(build_profile())
    build_profile.cache_clear()
    assert profile.dumps(build_profile()) == first
