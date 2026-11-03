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
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    widest = 0
    rows = []
    local, announced = {}, {}
    for message in fit_file.messages:
        definition = fit_file.definitions[message.def_id]
        name = profile.message_name(message.global_num) or f'unknown'
        if announced.get(definition.id) is None or local.get(definition.global_num) != definition.id:
            local[definition.global_num] = definition.id
            announced[definition.id] = True
            cells = []
            for number, size, base in definition.fields:
                field = profile.field(message.global_num, number)
                cells += [field['name'] if field else f'unknown', _count(size, base), _units(field)]
            rows.append(['Definition', definition.id % 16, name, *cells])
        cells = []
        for number, size, base in definition.fields:
            field = profile.field(message.global_num, number)
            cells += [field['name'] if field else 'unknown', _csv_value(message.fields.get(number), field), _units(field)]
        rows.append(['Data', definition.id % 16, name, *cells])
    widest = max((len(r) - 3) // 3 for r in rows) if rows else 0
    writer.writerow(['Type', 'Local Number', 'Message'] + [x for i in range(1, widest + 1) for x in (f'Field {i}', f'Value {i}', f'Units {i}')])
    writer.writerows(rows)
    return out.getvalue()


def _count(size, base):
    from .protocol import BASE_TYPES
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
    return value


def convert(fit_file, target):
    return {'gpx': to_gpx, 'tcx': to_tcx, 'json': to_json, 'csv': to_csv, 'jsonl': ir.dumps}[target](fit_file)
