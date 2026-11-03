import csv
import io
import json
import os
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from garmin_fit_store import convert, decode, ir
from garmin_fit_store.builder import build_fit
from garmin_fit_store.__main__ import main

from conftest import CORPUS


def activity():
    return decode((CORPUS / 'activity.fit').read_bytes())


def validate(xml, schema, tmp_path):
    directory = os.getenv('FIT_XSD_DIR')
    if not directory or not shutil.which('xmllint'):
        pytest.skip('Set FIT_XSD_DIR (gpx.xsd, TrainingCenterDatabasev2.xsd) and install xmllint')
    path = tmp_path / 'file.xml'
    path.write_text(xml)
    done = subprocess.run(['xmllint', '--noout', '--schema', str(Path(directory) / schema), str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_gpx_has_a_point_per_record_with_extensions_and_validates(tmp_path):
    xml = convert.to_gpx(activity())
    root = ET.fromstring(xml)
    ns = {'g': convert.GPX_NS, 'tp': convert.TPX_NS, 'pw': convert.PWR_NS}
    points = root.findall('.//g:trkpt', ns)
    assert len(points) == 30
    assert points[0].find('g:time', ns).text == '2026-01-01T10:00:00Z'
    assert points[1].find('.//tp:hr', ns).text == '121' and points[1].find('.//pw:PowerInWatts', ns).text == '201'
    assert points[0].find('.//pw:PowerInWatts', ns) is None  # the first record has no power
    validate(xml, 'gpx.xsd', tmp_path)


def test_tcx_has_laps_and_trackpoints_and_validates(tmp_path):
    xml = convert.to_tcx(activity())
    root = ET.fromstring(xml)
    ns = {'t': convert.TCX_NS}
    assert root.find('.//t:Activity', ns).get('Sport') == 'Running'
    assert len(root.findall('.//t:Lap', ns)) == 1 and len(root.findall('.//t:Trackpoint', ns)) == 30
    assert root.find('.//t:Lap/t:DistanceMeters', ns).text == '93.0'
    validate(xml, 'TrainingCenterDatabasev2.xsd', tmp_path)


def test_tcx_without_laps_and_without_a_start_time():
    from garmin_fit_store.builder import build_ir
    from datetime import datetime
    only_records = build_ir([('record', {'timestamp': datetime(2026, 1, 1), 'heart_rate': 100}),
                             ('record', {'timestamp': datetime(2026, 1, 1, 0, 0, 5), 'heart_rate': 101})])
    root = ET.fromstring(convert.to_tcx(only_records))
    assert len(root.findall('.//{%s}Trackpoint' % convert.TCX_NS)) == 2
    with pytest.raises(ValueError):
        convert.to_tcx(build_ir([('file_id', {'type': 'activity'})]))
    assert ET.fromstring(convert.to_gpx(only_records)).findall('.//{%s}trkpt' % convert.GPX_NS) == []  # no positions, no points


def test_json_is_the_named_semantics():
    messages = json.loads(convert.to_json(activity()))
    assert messages[0] == {'message': 'file_id', 'fields': {'type': 'activity', 'manufacturer': 'garmin', 'serial_number': 123,
                                                              'time_created': '2026-01-01T10:00:00Z', 'product_name': 'Test Watch'}}
    assert sum(m['message'] == 'record' for m in messages) == 30


def test_csv_layout():
    rows = list(csv.reader(io.StringIO(convert.to_csv(activity()))))
    assert rows[0][:6] == ['Type', 'Local Number', 'Message', 'Field 1', 'Value 1', 'Units 1']
    assert rows[1][:5] == ['Definition', '0', 'file_id', 'type', '1']
    assert rows[2][:7] == ['Data', '0', 'file_id', 'type', '4', '', 'manufacturer']
    record = next(r for r in rows if r[0] == 'Data' and r[2] == 'record')
    assert 'heart_rate' in record and record[record.index('speed') + 1] == '3.2'
    assert sum(r[0] == 'Definition' for r in rows) < sum(r[0] == 'Data' for r in rows)


def csv_rows(data):
    return list(csv.reader(io.StringIO(convert.to_csv(decode(data)))))


def test_csv_follows_the_tool_for_sub_fields_and_invalid_values():
    rows = csv_rows(build_fit([
        ('file_id', {'type': 'activity', 'manufacturer': 'garmin', 'product': 2697, 'serial_number': 1}),
        ('device_info', {'timestamp': 1100000000, 'device_index': 0, 'cum_operating_time': None, 'battery_voltage': 3.7}),
        ('record', {'timestamp': 1100000001, 'speed': 3.2, 'altitude': 100.0}),
    ]))
    file_id = next(r for r in rows if r[:3] == ['Data', '0', 'file_id'])
    assert 'garmin_product' in file_id and 'product' not in file_id
    info = next(r for r in rows if r[2] == 'device_info' and r[0] == 'Data')
    assert 'cum_operating_time' not in info  # an invalid value is left out of the data row
    record = next(r for r in rows if r[2] == 'record' and r[0] == 'Data')
    assert record[record.index('enhanced_speed') + 1] == '3.2' and record[record.index('enhanced_altitude') + 1] == '100.0'
    assert all(cell == '' for row in rows if row[0] == 'Definition' for cell in row[5::3])  # definition units are blank


def test_csv_value_forms():
    assert convert._csv_value(None, None) == ''
    assert convert._csv_value([1, None], None) == '1|'
    field = {'scale': [1], 'offset': [0], 'type': 'float32', 'base_type': 'float32'}
    assert convert._csv_value(-0.2857142984867096, field) == '-0.2857143'


def test_csv_float32_prints_the_shortest_form():
    assert convert._float32(-0.2857142984867096) == '-0.2857143'
    assert convert._float32(0.5) == '0.5'


def test_csv_equals_the_official_tool(tmp_path):
    """Set FIT_CSV_TOOL_JAR to the jar from Maven Central (com.garmin:fit; it holds com.garmin.fit.csv.CSVTool; not committed).
    Compares every row after ignoring units, trailing empty cells and the tool's repeated Definition rows and local numbers."""
    jar = os.getenv('FIT_CSV_TOOL_JAR')
    if not jar or not shutil.which('java'):
        pytest.skip('Set FIT_CSV_TOOL_JAR and install java')
    path = CORPUS / 'activity.fit'
    subprocess.run(['java', '-cp', jar, 'com.garmin.fit.csv.CSVTool', '-b', str(path), str(tmp_path / 'tool.csv')], check=True, capture_output=True)

    def normal(rows):
        out = []
        for r in rows[1:]:
            r = r + [''] * (-len(r) % 3)
            out.append((r[0], r[2], tuple((r[i], r[i + 1]) for i in range(3, len(r), 3) if r[i] and (r[0] == 'Definition' or r[i + 1] != ''))))
        return out

    ours = normal(csv_rows(path.read_bytes()))
    with open(tmp_path / 'tool.csv', encoding='utf-8-sig') as handle:
        theirs = normal(list(csv.reader(handle)))
    assert ours == theirs


def test_csv_of_unknown_and_odd_values():
    fit_file = decode((CORPUS / 'unknown_things.fit').read_bytes())
    text = convert.to_csv(fit_file)
    assert 'fffe0000' in text and 'unknown' in text


def test_jsonl_target_is_the_ir():
    assert convert.convert(activity(), 'jsonl') == ir.dumps(activity())


def test_convert_command(tmp_path, capsys):
    out = tmp_path / 'out.gpx'
    assert main(['convert', '--to', 'gpx', str(CORPUS / 'activity.fit'), str(out)]) == 0
    assert out.read_text().startswith('<?xml')
