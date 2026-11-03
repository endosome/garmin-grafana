import json
import struct
from datetime import timezone

import pytest
from garmin_fit_sdk import Decoder, Stream
from garmin_fit_store import FitError, canonical, decode, encode, ir
from garmin_fit_store.profile import Profile
from garmin_fit_store.protocol import crc16

import rawfit as r
from conftest import CORPUS


def sdk_view(data):
    """(message number, {key: raw value}) per message from the official decoder, in file order (it has no compressed
    timestamp support, so those files are compared against fitparse instead)."""
    seen = []
    _, errors = Decoder(Stream.from_byte_array(bytearray(data))).read(
        apply_scale_and_offset=False, convert_types_to_strings=False, convert_datetimes_to_dates=False,
        expand_sub_fields=False, expand_components=False, merge_heart_rates=False,
        mesg_listener=lambda number, message: seen.append((number, message)))
    return seen, errors


def test_corpus_matches_expected_jsonl(corpus_file):
    name, data = corpus_file
    assert ir.dumps(decode(data, check_crc=False)) == (CORPUS / f'{name}.jsonl').read_text()


def test_jsonl_round_trip(corpus_file):
    fit_file = decode(corpus_file[1], check_crc=False)
    again = ir.loads(ir.dumps(fit_file))
    assert canonical(again) == canonical(fit_file) and again.definitions == fit_file.definitions


def test_round_trip_oracle(corpus_file):
    original = decode(corpus_file[1], check_crc=False)
    encoded = encode(original)
    assert canonical(decode(encoded)) == canonical(original)
    assert encode(decode(encoded)) == encoded  # the encoder is stable


def test_external_files_round_trip(external_file):
    original = decode(external_file, check_crc=False)
    assert canonical(decode(encode(original))) == canonical(original)


def test_sdk_decodes_our_output(corpus_file):
    name, data = corpus_file
    original = decode(data, check_crc=False)
    seen, errors = sdk_view(encode(original))
    assert errors == []
    assert [n for n, _ in seen] == [m.global_num for m in original.messages]


def test_raw_values_equal_the_official_decoder(corpus_file):
    name, data = corpus_file
    if name in ('compressed_timestamps', 'chained'):
        pytest.skip('the official decoder has no compressed timestamp support and stops at a chained file')
    theirs, errors = sdk_view(data)
    assert errors == []
    ours = decode(data, check_crc=False).messages
    assert len(theirs) == len(ours)
    profile = Profile()
    for (number, message), mine in zip(theirs, ours):
        assert number == mine.global_num
        names = {profile.field(number, n)['name'] if profile.field(number, n) else n: n for n in mine.fields}
        for key, value in message.items():
            if key == 'developer_fields':
                continue
            if value is None or key not in names:
                continue
            ours_value = mine.fields[names[key]]
            if isinstance(value, list):  # the SDK nulls an invalid element of an array; the IR keeps the sentinel
                ours_value = [None if o != t and t is None else o for o, t in zip(ours_value, value)]
            assert ours_value == value, (name, number, key)
        for key, field in names.items():
            assert key in message or key == field


def test_compressed_timestamps_equal_fitparse():
    from fitparse import FitFile
    data = (CORPUS / 'compressed_timestamps.fit').read_bytes()
    theirs = [m.get_value('timestamp') for m in FitFile(data).get_messages('record')]
    ours = [m.fields[253] for m in decode(data).messages]
    assert [int(t.replace(tzinfo=timezone.utc).timestamp()) - 631065600 for t in theirs] == ours


def test_sdk_agrees_on_named_values():
    data = (CORPUS / 'activity.fit').read_bytes()
    messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read(
        apply_scale_and_offset=False, convert_types_to_strings=False, convert_datetimes_to_dates=False,
        expand_components=False, merge_heart_rates=False)
    assert errors == []
    profile = Profile()
    mine = [m for m in decode(data).messages if m.global_num == 20]
    for theirs, ours in zip(messages['record_mesgs'], mine):
        for number, value in ours.fields.items():
            name = profile.field(20, number)['name']
            assert theirs[name] == value


def test_compressed_timestamp_expansion():
    fit_file = decode((CORPUS / 'compressed_timestamps.fit').read_bytes())
    assert [m.fields[253] for m in fit_file.messages] == [1_100_000_000, 1_100_000_003, 1_100_000_031, 1_100_000_034, 1_100_000_100]


def test_developer_fields_are_typed_by_their_description():
    fit_file = decode((CORPUS / 'developer_fields.fit').read_bytes())
    records = [m for m in fit_file.messages if m.global_num == 20]
    assert records[0].dev == {(0, 0): 1234, (0, 1): 3}
    assert records[1].dev == {}


def test_unknown_message_and_fields_survive():
    fit_file = decode((CORPUS / 'unknown_things.fit').read_bytes())
    first, second, third, fourth = fit_file.messages
    assert first.fields[200] == [1, 2, 3] and first.fields[201] == 10.0 and first.fields[202] == 1.0
    assert first.fields[203] == 0x7FFFFFFFFFFFFFFF and first.fields[204] == ['ab', 'cd']
    assert first.fields[205] == {'hex': 'fffe0000'} and first.fields[206] == [1, 2, 3, 255, 5]
    assert second.fields == {0: 0xDEADBEEF}
    assert third.fields == {1: 9} and fourth.fields == {}
    assert canonical(decode(encode(fit_file))) == canonical(fit_file)


def test_chained_files_decode_in_order():
    fit_file = decode((CORPUS / 'chained.fit').read_bytes())
    assert [m.fields.get(3) for m in fit_file.messages] == [1, 1, 2, 2]


def test_encoder_reuses_and_evicts_local_types():
    fit_file = ir.FitFile()
    defs = [fit_file.add_definition(20, [(3, 1, 2), (n, 1, 2)]) for n in range(100, 120)]
    for _ in range(2):
        for d in defs:
            fit_file.add_message(d, {3: 1})
    assert canonical(decode(encode(fit_file))) == canonical(fit_file)


def test_encoder_grows_a_field_that_no_longer_fits():
    fit_file = ir.FitFile()
    d = fit_file.add_definition(0, [(8, 4, 7)])
    fit_file.add_message(d, {8: 'a much longer product name'})
    assert decode(encode(fit_file)).messages[0].fields[8] == 'a much longer product name'


def test_encoder_rejects_fields_its_definition_lacks():
    fit_file = ir.FitFile()
    d = fit_file.add_definition(20, [(3, 1, 2)])
    fit_file.add_message(d, {3: 1, 4: 1})
    with pytest.raises(FitError):
        encode(fit_file)


def test_float_nan_and_partial_arrays():
    fit_file = ir.FitFile()
    d = fit_file.add_definition(60001, [(0, 8, 0x88), (1, 3, 2), (2, 4, 0x86)])
    fit_file.add_message(d, {0: [1.5, None], 1: [1, 255, 3]})
    again = decode(encode(fit_file)).messages[0]
    assert again.fields == {0: [1.5, None], 1: [1, 255, 3]}


def test_tolerant_header_crc_and_strict_file_crc():
    data = (CORPUS / 'zero_header_crc.fit').read_bytes()
    assert decode(data).messages
    bad = r.fit([r.definition(0, 20, [(253, 4, r.UINT32)]), r.data(0, r.u32(1))], bad_crc=True)
    with pytest.raises(FitError, match='file CRC'):
        decode(bad)
    assert decode(bad, check_crc=False).messages


@pytest.mark.parametrize('data, message', [
    (b'', 'empty input'),
    (b'\x0e' * 14, 'signature'),
    (b'\x0a' + b'\0' * 20, 'header size'),
    (r.fit([r.data(0, b'\1')]), 'undefined local type'),
    (r.fit([r.compressed(1, 3, b'')]), 'undefined local type'),
    (r.fit([r.definition(0, 20, [(3, 1, 2)]), r.data(0, b'')]), 'truncated record'),
    (r.fit([bytes([0x40, 0, 2, 0, 0, 0])]), 'architecture'),
    (r.fit([r.definition(0, 20, [(3, 1, 0x1F)])]), 'base type'),
    (r.fit([r.definition(0, 20, [(3, 0, 2)])]), 'zero size'),
    (r.fit([r.definition(0, 20, [(3, 1, 2)])])[:-1], 'truncated file'),
    (r.fit([r.definition(0, 20, [(3, 1, 2)])]) + b'garbage-after-the-file', 'header'),
])
def test_malformed_input_raises_typed_errors(data, message):
    with pytest.raises(FitError, match=message):
        decode(data)


def test_bad_header_crc_is_an_error_unless_tolerated():
    data = bytearray(r.fit([r.definition(0, 20, [(3, 1, 2)])]))
    data[12] ^= 1
    with pytest.raises(FitError, match='header CRC'):
        decode(bytes(data))
    content = bytes(data[:-2])
    assert decode(content + struct.pack('<H', crc16(content)), check_crc=False)


def test_trailing_zero_padding_after_a_file_is_ignored():
    data = (CORPUS / 'activity.fit').read_bytes()
    assert len(decode(data + bytes(16)).messages) == len(decode(data).messages)


def test_every_prefix_terminates():
    data = (CORPUS / 'developer_fields.fit').read_bytes()
    for end in range(len(data)):
        with pytest.raises(FitError):
            decode(data[:end], check_crc=False)


def test_sdk_reads_the_json_we_wrote(corpus_file):
    json.loads(ir.dumps(decode(corpus_file[1], check_crc=False)).splitlines()[0])
