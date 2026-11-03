"""IR to GPX 1.1 (with Garmin's TrackPointExtension), TCX, JSON, CSV in the layout of the official FIT CSV Tool, and JSONL."""
import csv
import io
import json
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from . import ir
from .profile import Profile
from .semantics import Semantics

GPX_NS = 'http://www.topografix.com/GPX/1/1'
TPX_NS = 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1'
PWR_NS = 'http://www.garmin.com/xmlschemas/PowerExtension/v1'
from .protocol import BASE_TYPES

TCX_NS = 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'
ACT_NS = 'http://www.garmin.com/xmlschemas/ActivityExtension/v2'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'
SPORTS = {'running': 'Running', 'cycling': 'Biking'}


def named_messages(fit_file, degrees=True, **flags):
    """(message name or number, named values) for every message, in file order."""
    profile = Profile()
    semantics = Semantics(profile)
    out = []
    for message in fit_file.messages:
        wire = {n: base for n, _, base in fit_file.definitions[message.def_id].fields}
        values = semantics.named(message.global_num, message.fields, degrees=degrees, wire=wire, **flags)
        out.append((profile.message_name(message.global_num) or message.global_num, values))
    return out


def _iso(value):
    return value.strftime('%Y-%m-%dT%H:%M:%SZ') if value.tzinfo else value.isoformat()


def _jsonable(value):
    if isinstance(value, datetime):
        return _iso(value)
    return value


def to_json(fit_file):
    return json.dumps([{'message': name, 'fields': {str(k): _jsonable(v) for k, v in values.items()}}
                       for name, values in named_messages(fit_file)], indent=1) + '\n'


def trackpoints(messages):
    """Record messages as dicts with the fields the GPX and TCX writers use."""
    points = []
    for name, v in messages:
        if name != 'record' or 'timestamp' not in v:
            continue
        points.append({
            'time': v['timestamp'], 'lat': v.get('position_lat'), 'lon': v.get('position_long'),
            'ele': v.get('enhanced_altitude', v.get('altitude')), 'hr': v.get('heart_rate'), 'cad': v.get('cadence'),
            'temp': v.get('temperature'), 'power': v.get('power'), 'distance': v.get('distance'),
            'speed': v.get('enhanced_speed', v.get('speed'))})
    return points


def _text(parent, tag, value):
    element = ET.SubElement(parent, tag)
    element.text = value if isinstance(value, str) else repr(round(value, 6)) if isinstance(value, float) else str(value)
    return element


def _valid(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def to_gpx(fit_file):
    messages = named_messages(fit_file)
    ET.register_namespace('', GPX_NS)
    ET.register_namespace('gpxtpx', TPX_NS)
    ET.register_namespace('gpxpx', PWR_NS)
    ET.register_namespace('xsi', XSI)
    root = ET.Element(f'{{{GPX_NS}}}gpx', {'version': '1.1', 'creator': 'garmin-fit-store',
                                           f'{{{XSI}}}schemaLocation': f'{GPX_NS} http://www.topografix.com/GPX/1/1/gpx.xsd'})
    session = next((v for n, v in messages if n == 'session'), {})
    first = next((p['time'] for p in trackpoints(messages)), None)
    if first is not None:
        _text(ET.SubElement(root, f'{{{GPX_NS}}}metadata'), f'{{{GPX_NS}}}time', _iso(first))
    track = ET.SubElement(root, f'{{{GPX_NS}}}trk')
    _text(track, f'{{{GPX_NS}}}name', str(session.get('sport', 'activity')))
    segment = ET.SubElement(track, f'{{{GPX_NS}}}trkseg')
    for p in trackpoints(messages):
        if not (_valid(p['lat']) and _valid(p['lon'])):
            continue
        point = ET.SubElement(segment, f'{{{GPX_NS}}}trkpt', {'lat': repr(round(p['lat'], 8)), 'lon': repr(round(p['lon'], 8))})
        if _valid(p['ele']):
            _text(point, f'{{{GPX_NS}}}ele', p['ele'])
        _text(point, f'{{{GPX_NS}}}time', _iso(p['time']))
        extension = ET.Element(f'{{{GPX_NS}}}extensions')
        tpx = ET.SubElement(extension, f'{{{TPX_NS}}}TrackPointExtension')
        for tag, key in (('atemp', 'temp'), ('hr', 'hr'), ('cad', 'cad')):
            if _valid(p[key]):
                _text(tpx, f'{{{TPX_NS}}}{tag}', p[key])
        if _valid(p['power']):
            _text(extension, f'{{{PWR_NS}}}PowerInWatts', int(p['power']))
        if len(extension):
            point.append(extension)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def to_tcx(fit_file):
    messages = named_messages(fit_file)
    ET.register_namespace('', TCX_NS)
    ET.register_namespace('ae', ACT_NS)
    ET.register_namespace('xsi', XSI)
    root = ET.Element(f'{{{TCX_NS}}}TrainingCenterDatabase', {
        f'{{{XSI}}}schemaLocation': f'{TCX_NS} http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd'})
    activities = ET.SubElement(root, f'{{{TCX_NS}}}Activities')
    session = next((v for n, v in messages if n == 'session'), {})
    points = trackpoints(messages)
    laps = [v for n, v in messages if n == 'lap' and 'start_time' in v]
    start = session.get('start_time') or (points[0]['time'] if points else None)
    if start is None:
        raise ValueError('the file has neither a session start time nor records')
    activity = ET.SubElement(activities, f'{{{TCX_NS}}}Activity', {'Sport': SPORTS.get(session.get('sport'), 'Other')})
    _text(activity, f'{{{TCX_NS}}}Id', _iso(start))
    if not laps:
        end = points[-1]['time'] if points else start
        laps = [{'start_time': start, 'total_elapsed_time': (end - start).total_seconds(),
                 'total_distance': session.get('total_distance', 0.0)}]
    for index, lap in enumerate(laps):
        lap_start = lap['start_time']
        lap_end = laps[index + 1]['start_time'] if index + 1 < len(laps) else None
        element = ET.SubElement(activity, f'{{{TCX_NS}}}Lap', {'StartTime': _iso(lap_start)})
        _text(element, f'{{{TCX_NS}}}TotalTimeSeconds', float(lap.get('total_elapsed_time', 0.0)))
        _text(element, f'{{{TCX_NS}}}DistanceMeters', float(lap.get('total_distance', 0.0)))
        _text(element, f'{{{TCX_NS}}}Calories', int(lap.get('total_calories', 0)))
        _text(element, f'{{{TCX_NS}}}Intensity', 'Active')
        _text(element, f'{{{TCX_NS}}}TriggerMethod', 'Manual')
        track = ET.SubElement(element, f'{{{TCX_NS}}}Track')
        for p in points:
            if p['time'] < lap_start or (lap_end is not None and p['time'] >= lap_end):
                continue
            point = ET.SubElement(track, f'{{{TCX_NS}}}Trackpoint')
            _text(point, f'{{{TCX_NS}}}Time', _iso(p['time']))
            if _valid(p['lat']) and _valid(p['lon']):
                position = ET.SubElement(point, f'{{{TCX_NS}}}Position')
                _text(position, f'{{{TCX_NS}}}LatitudeDegrees', p['lat'])
                _text(position, f'{{{TCX_NS}}}LongitudeDegrees', p['lon'])
            if _valid(p['ele']):
                _text(point, f'{{{TCX_NS}}}AltitudeMeters', p['ele'])
            if _valid(p['distance']):
                _text(point, f'{{{TCX_NS}}}DistanceMeters', p['distance'])
            if _valid(p['hr']):
                _text(ET.SubElement(point, f'{{{TCX_NS}}}HeartRateBpm'), f'{{{TCX_NS}}}Value', int(p['hr']))
            if _valid(p['cad']):
                _text(point, f'{{{TCX_NS}}}Cadence', int(p['cad']))
            if _valid(p['speed']) or _valid(p['power']):
                tpx = ET.SubElement(ET.SubElement(point, f'{{{TCX_NS}}}Extensions'), f'{{{ACT_NS}}}TPX')
                if _valid(p['speed']):
                    _text(tpx, f'{{{ACT_NS}}}Speed', p['speed'])
                if _valid(p['power']):
                    _text(tpx, f'{{{ACT_NS}}}Watts', int(p['power']))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def to_csv(fit_file):
    """The layout of the official FIT CSV Tool: Type, Local Number, Message, then Field / Value / Units triples.
    Definition rows give the element count as the value; data rows give scaled values (enum fields their number)."""
    profile = Profile()
    semantics = Semantics(profile)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    rows = []
    announced = set()
    descriptions = {(m.fields.get(0), m.fields.get(1)): m.fields for m in fit_file.messages if m.global_num == 206}
    for message in fit_file.messages:
        definition = fit_file.definitions[message.def_id]
        name = profile.message_name(message.global_num) or f'unknown'
        fields = [(number, profile.field(message.global_num, number)) for number, _, _ in definition.fields]
        if definition.id not in announced:
            announced.add(definition.id)
            cells = []
            for (number, size, base), (_, field) in zip(definition.fields, fields):
                cells += [field['name'] if field else f'unknown', _count(size, base), '']
            for number, size, index in definition.dev_fields:
                described = descriptions.get((index, number), {})
                base = BASE_TYPES[described[2] & 0x1F][2] if described.get(2) is not None else 1
                cells += [described.get(3) or 'unknown', size // base, '']
            rows.append(['Definition', definition.id % 16, name, *cells])
        wire = {n: base for n, _, base in definition.fields}
        values = semantics.named(message.global_num, message.fields, wire=wire)
        shown = set()
        cells = []
        for number, field in fields:
            fname, shown_field, raw = field['name'] if field else 'unknown', field, message.fields.get(number)
            if raw is None:
                shown.add(fname)
                continue  # the tool leaves out a field whose value is invalid
            shown.add(fname)
            # A sub-field the reference selects replaces the field's name, not its units (product -> garmin_product).
            for sub in (field['sub_fields'] if field else ()):
                if sub['name'] in values:
                    fname, shown_field = sub['name'], dict(field, **{k: sub[k] for k in ('scale', 'offset', 'type') if k in sub})
                    break
            text = _csv_value(raw, shown_field)
            if isinstance(raw, list) and number in wire and BASE_TYPES[wire[number] & 0x1F][3] is not None:
                # the tool prints an invalid array element as the type's raw invalid value, unscaled
                invalid = BASE_TYPES[wire[number] & 0x1F][3]
                text = '|'.join(str(invalid) if v is None or v == invalid else str(_csv_value(v, shown_field)) for v in raw)
            if field is None and wire.get(number, 0) & 0x1F == 8 and isinstance(raw, float):
                text = _float32(raw)  # an unknown float32 field: the tool prints the 32-bit shortest form too
            cells += [fname, text, _units(shown_field)]
            shown.add(fname)
        # Like the tool, components expanded from a field follow the message's own fields (enhanced_speed after speed).
        by_name = {f['name']: f for f in (profile.message(message.global_num) or {'fields': {}})['fields'].values()}
        by_number = {f['number']: f['name'] for f in by_name.values()}
        order = [by_number[c] for _, field in fields if field for c in field['components'] if c in by_number]
        order += [key for key in values if key not in order]
        for key in order:
            if key in values and key not in shown and key in by_name and not any(key == sub['name'] for f in by_name.values() for sub in f['sub_fields']):
                target = by_name[key]
                value = values[key]
                if isinstance(value, int) and target['scale'][0] != 1:
                    value = float(value)
                cells += [key, _csv_value(value, None), _units(target)]
        for number, size, index in definition.dev_fields:
            if (index, number) in message.dev:
                described = descriptions.get((index, number), {})
                value = message.dev[(index, number)]
                text = _float32(value) if described.get(2) == 136 and isinstance(value, float) else _csv_value(value, None)
                cells += [described.get(3) or 'unknown', text, described.get(8) or 'null']
        rows.append(['Data', definition.id % 16, name, *cells])
    widest = max((len(r) - 3) // 3 for r in rows) if rows else 0
    writer.writerow(['Type', 'Local Number', 'Message'] + [x for i in range(1, widest + 1) for x in (f'Field {i}', f'Value {i}', f'Units {i}')])
    writer.writerows(rows)
    return out.getvalue()


def _count(size, base):
    return size // BASE_TYPES[base & 0x1F][2]


def _units(field):
    if not field:
        return ''
    units = field['units']
    return units if isinstance(units, str) else ','.join(units)


def _csv_value(value, field):
    if value is None:
        return ''
    if isinstance(value, list):
        return '|'.join(str(_csv_value(v, field)) for v in value)
    if isinstance(value, dict):
        return value['hex']
    if field and isinstance(value, (int, float)) and len(field['scale']) == 1 and field['type'] not in ('date_time',):
        scale, offset = field['scale'][0], field['offset'][0]
        if scale != 1 or offset:
            return repr(value / scale - offset)
        if field['base_type'] == 'float32':
            return _float32(value)
    return value


def _float32(value):
    """The shortest decimal that reads back as the same 32-bit float (the tool prints -0.2857143, not the double's digits)."""
    import struct
    packed = struct.pack('<f', value)
    for digits in range(1, 10):
        text = f'{value:.{digits}g}'
        if struct.pack('<f', float(text)) == packed:
            return repr(float(text))
    return repr(value)  # pragma: no cover - every 32-bit float has a decimal of at most 9 digits


def convert(fit_file, target):
    return {'gpx': to_gpx, 'tcx': to_tcx, 'json': to_json, 'csv': to_csv, 'jsonl': ir.dumps}[target](fit_file)
