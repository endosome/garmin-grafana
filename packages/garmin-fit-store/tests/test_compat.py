from datetime import datetime

import pytest
from fitparse import FitFile as FitparseFile
from garmin_fit_store.compat import FitFile, FitParseError

from conftest import CORPUS


def test_values_equal_fitparses_for_the_corpus_where_fitparse_names_the_field(corpus_file):
    name, data = corpus_file
    if name in ('unknown_things', 'chained', 'big_endian'):
        pytest.skip('fitparse stops at chained files and normalises odd sizes differently')
    theirs = list(FitparseFile(data, check_crc=False).get_messages())
    ours = list(FitFile(data, check_crc=False).get_messages())
    assert len(theirs) == len(ours)
    for t, o in zip(theirs, ours):
        mine = o.get_values()
        for key, value in t.get_values().items():
            if key in mine and not key.startswith('unknown') and isinstance(value, (int, float, str, datetime)):
                assert mine[key] == pytest.approx(value) if isinstance(value, float) else mine[key] == value, (t.name, key)


def test_unknown_fields_and_developer_fields_are_named_like_fitparse():
    fit = FitFile((CORPUS / 'developer_fields.fit').read_bytes())
    record = next(fit.get_messages('record'))
    assert record.get_values()['Stride'] == 1234 and record.get_value('Flag') == 3 and record.get_value('missing', 'x') == 'x'
    unknown = FitFile((CORPUS / 'unknown_things.fit').read_bytes())
    assert unknown.get_messages('unknown_60000') is not None
    first = list(unknown.messages)[0]
    assert first.get_values()['unknown_200'] == [1, 2, 3]
    assert [f.name for f in first][0] == 'type'
    assert 'type' in repr(next(iter(first)))


def test_timestamps_are_naive_utc_and_a_path_or_bytes_both_work(tmp_path):
    path = tmp_path / 'a.fit'
    path.write_bytes((CORPUS / 'activity.fit').read_bytes())
    for source in (str(path), path, (CORPUS / 'activity.fit').read_bytes(), path.open('rb')):
        record = next(FitFile(source).get_messages('record'))
        assert record.get_values()['timestamp'] == datetime(2026, 1, 1, 10)
        if hasattr(source, 'close'):
            source.close()


def test_garbage_raises_the_parse_error():
    with pytest.raises(FitParseError):
        FitFile(b'garbage that is not a fit file').parse()
    with pytest.raises(FitParseError):
        list(FitFile(b'\x0e\x20\x00\x00\x00\x00\x00\x00.FIT\0\0' + b'\xff' * 6).get_messages())


def test_developer_scale_and_offset_apply():
    from garmin_fit_store import builder, ir, encode
    fit = ir.FitFile()
    d206 = fit.add_definition(206, [(0, 1, 2), (1, 1, 2), (2, 1, 2), (3, 8, 7), (6, 1, 2), (7, 1, 1)])
    fit.add_message(d206, {0: 0, 1: 0, 2: 2, 3: 'gain', 6: 10, 7: 5})
    d20 = fit.add_definition(20, [(253, 4, 0x86)], dev_fields=[(0, 1, 0)])
    fit.add_message(d20, {253: 1_100_000_000}, dev={(0, 0): 100})
    values = next(FitFile(encode(fit)).get_messages('record')).get_values()
    assert values['gain'] == 100 / 10 - 5
