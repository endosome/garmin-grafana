"""Raw field values to named, scaled, unit-bearing values and back, driven only by the profile document.

``Semantics.named`` follows the official SDK's conventions for sub-fields, components, accumulated fields, enum names,
scale and offset, so the two can be compared; what the document does not name (unknown messages and fields) is returned
raw, keyed by number. Positions can additionally be converted from semicircles to degrees.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .profile import Profile
from .protocol import BASE_TYPES, EPOCH_OFFSET

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
NUMERIC = {'sint8', 'uint8', 'sint16', 'uint16', 'sint32', 'uint32', 'float32', 'float64', 'uint8z', 'uint16z', 'uint32z',
           'byte', 'sint64', 'uint64', 'uint64z'}
BASE_BY_NAME = {spec[0]: spec for spec in BASE_TYPES.values()}
DEGREES = 180 / 2 ** 31


class Accumulator:
    """The running value of accumulated fields, per message and field number."""

    def __init__(self):
        self._fields = {}

    def reset(self, message, field, value):
        self._fields[(message, field)] = [value, value]

    def add(self, message, field, value, bits):
        state = self._fields.setdefault((message, field), [value, value])
        state[0] += (value - state[1]) & ((1 << bits) - 1)
        state[1] = value
        return state[0]


class Semantics:
    def __init__(self, profile=None):
        self.profile = profile or Profile()
        self.accumulator = Accumulator()

    # raw -> named

    def named(self, message_number, fields, expand_sub_fields=True, expand_components=True, degrees=False, wire=None):
        """{name: value} for the raw {field number: value} of one message; call in file order (accumulation is stateful).
        ``wire`` maps a field number to the base type byte its definition used, which decides what an array's invalid element is."""
        message = self.profile.message(message_number)
        result = {}
        entries = {}  # name -> (field profile, raw, is sub field)
        to_expand, with_subfields = [], []
        for number, raw in fields.items():
            field = message['fields'].get(str(number)) if message else None
            if field is None:
                base = BASE_TYPES[wire[number] & 0x1F] if wire and number in wire else None
                if isinstance(raw, list) and base is not None and base[3] is not None and base[0] != 'byte':
                    raw = [None if v == base[3] else v for v in raw]
                result[number] = raw
                continue
            entries[field['name']] = (field, raw, False)
            if field['sub_fields']:
                with_subfields.append(field)
            if field['components']:
                to_expand.append(field['name'])
            if field['accumulate']:
                self._seed(message_number, field, raw)
        if expand_sub_fields:
            for field in with_subfields:
                self._sub_fields(field, entries, to_expand)
        expanded = self._components(message_number, message, entries, to_expand) if expand_components else {}
        for name, (field, raw, is_sub) in entries.items():
            result[name] = self._value(field, raw, is_sub, degrees, (wire or {}).get(field['number']))
        result.update({name: value for name, (field, value) in expanded.items()})
        return result

    def _sub_fields(self, field, entries, to_expand):
        raw = entries[field['name']][1]
        for sub in field['sub_fields']:
            for item in sub['map']:
                reference = entries.get(item['name'])
                if reference is not None and reference[1] == item['raw_value']:
                    entries[sub['name']] = (dict(sub, number=field['number'], base_type=field['base_type']), list(raw) if isinstance(raw, list) else raw, True)
                    if sub['components']:
                        to_expand.append(sub['name'])
                    break

    def _seed(self, number, field, raw):
        """An accumulated field present in a message is the new baseline for its accumulation."""
        value = raw[-1] if isinstance(raw, list) else raw
        self.accumulator.reset(number, field['number'], int(value))

    def _components(self, number, message, entries, to_expand):
        expanded = {}
        pending = {}  # target name -> list of raw values
        shown = defaultdict(list)  # target name -> the component's value, which is what the SDK reports
        queue = list(to_expand)
        while queue:
            name = queue.pop()
            if name in entries:
                field, raw, is_sub = entries[name]
            elif name in pending:
                field, raw = message_field(message, name), pending[name]
                raw, is_sub = (raw[0] if len(raw) == 1 else raw), False
            else:
                continue
            base = BASE_BY_NAME.get(field['type'])
            if not field['components'] or base is None:
                continue
            elements = raw if isinstance(raw, list) else [raw]
            invalid = base[3]
            if all(e == invalid for e in elements):
                continue
            bits = _Bits(elements, base[2] * 8)
            for i, target_number in enumerate(field['components']):
                if bits.available < field['bits'][i]:
                    break
                target = self.profile.field(number, target_number)
                value = bits.read(field['bits'][i])
                if target['accumulate']:
                    value = self.accumulator.add(number, target_number, value, field['bits'][i])
                value = value / field['scale'][i] - field['offset'][i]
                value = int(value) if float(value).is_integer() else value
                raw_value = int((value + target['offset'][0]) * target['scale'][0])
                pending.setdefault(target['name'], []).append(raw_value)
                shown[target['name']].append(None if raw_value == _invalid(target) else self._enum(target, value))
                if target['components']:
                    queue.append(target['name'])
                if not bits.available:
                    break
        for name, values in pending.items():
            field = message_field(message, name)
            expanded[name] = (field, shown[name][0] if len(shown[name]) == 1 else shown[name])
        return expanded

    def _enum(self, field, value):
        values = self.profile.type_values(field['type'])
        return values.get(str(value), value) if values is not None and isinstance(value, int) else value

    def _value(self, field, raw, is_sub, degrees, wire=None):
        kind = field['type']
        base = BASE_TYPES[wire & 0x1F] if wire is not None else BASE_BY_NAME.get(field.get('base_type', kind))
        if isinstance(raw, list) and base is not None and base[3] is not None and base[0] != 'byte':
            raw = [None if v == base[3] else v for v in raw]  # an invalid element of an array
        values = self.profile.type_values(kind)
        if values is not None and kind not in ('date_time', 'local_date_time'):
            convert = lambda v: values.get(str(v), v) if isinstance(v, int) else v  # noqa: E731
            return [convert(v) for v in raw] if isinstance(raw, list) else convert(raw)
        if kind == 'date_time' and isinstance(raw, int):
            return FIT_EPOCH + timedelta(seconds=raw)
        if kind == 'local_date_time' and isinstance(raw, int):
            return (FIT_EPOCH + timedelta(seconds=raw)).replace(tzinfo=None)
        if kind in NUMERIC:
            return _scale(field, raw, degrees)
        return raw

    # named -> raw

    def raw(self, message_number, named):
        """The raw {field number: value} for named values (sub-field names map onto their parent field)."""
        message = self.profile.message(message_number)
        fields = {}
        for name, value in named.items():
            if isinstance(name, int):
                fields[name] = value
                continue
            number, field, is_sub = self._lookup(message, name)
            if is_sub and number in fields:
                continue
            fields[number] = self._invert(field, value)
        return fields

    def _lookup(self, message, name):
        for number, field in message['fields'].items():
            if field['name'] == name:
                return int(number), field, False
        for number, field in message['fields'].items():
            for sub in field['sub_fields']:
                if sub['name'] == name:
                    return int(number), sub, True
        raise KeyError(name)

    def _invert(self, field, value):
        kind = field['type']
        values = self.profile.type_values(kind)
        if isinstance(value, list):
            sentinel = _invalid(field)
            return [sentinel if v is None else self._invert(field, v) for v in value]
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return int((value - FIT_EPOCH.replace(tzinfo=None)).total_seconds())
            return int((value - FIT_EPOCH).total_seconds())
        if isinstance(value, str) and values is not None:
            return int(next(raw for raw, name in values.items() if name == value))
        if kind in NUMERIC and isinstance(value, (int, float)) and not isinstance(value, bool):
            scale, offset = field['scale'], field['offset']
            if len(scale) == 1 and (scale[0] != 1 or offset[0]):
                return int(round((value + offset[0]) * scale[0]))
        return value


def _invalid(field):
    base = BASE_BY_NAME.get(field['type']) or BASE_BY_NAME.get(field['base_type'])
    return base[3] if base else 0xFF


def message_field(message, name):
    return next(f for f in message['fields'].values() if f['name'] == name)


def _scale(field, raw, degrees):
    if raw is None or len(field['scale']) > 1:
        return raw
    scale, offset = field['scale'][0], field['offset'][0]
    units = field['units']
    if degrees and units == 'semicircles' and not isinstance(raw, list):
        return raw * DEGREES
    if isinstance(raw, list):
        return [None if v is None else (v / scale if scale != 1 else v) - offset for v in raw]
    return (raw / scale if scale != 1 else raw) - offset


class _Bits:
    """Little-endian bit reader over the elements of a field, element 0 first."""

    def __init__(self, elements, width):
        self.value, self.available = 0, 0
        for i, element in enumerate(elements):
            self.value |= (element & ((1 << width) - 1)) << (i * width)
        self.available = width * len(elements)

    def read(self, count):
        out = self.value & ((1 << count) - 1)
        self.value >>= count
        self.available -= count
        return out
