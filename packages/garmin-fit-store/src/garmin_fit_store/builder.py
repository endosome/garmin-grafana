"""Builds CRC-valid FIT files for tests, driven by the profile and written by the package's own encoder.

    build_fit([
        ("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": 123}),
        ("record", {"timestamp": datetime(2026, 1, 1, 10), "heart_rate": 120, "speed": 3.2}),
    ])

Values are given in the units the profile names (scale and offset are applied here), enum fields take their string
names, and date_time fields take naive UTC datetimes. Fields missing from the profile are written by number with an
explicit base type: {140: ("uint16", 3500)}.
"""
from datetime import datetime, timezone

from .encoder import encode
from .ir import FitFile
from .profile import Profile
from .protocol import BASE_TYPES, EPOCH_OFFSET

BASE_BY_NAME = {spec[0]: (ident, spec[2]) for ident, spec in BASE_TYPES.items()}
_profile = None


def _get_profile():
    global _profile
    _profile = _profile or Profile()
    return _profile


def _raw(field, value, profile):
    kind = field['type']
    if isinstance(value, datetime):
        return int(value.replace(tzinfo=timezone.utc).timestamp()) - EPOCH_OFFSET
    if isinstance(value, str) and field['base_type'] != 'string':
        names = profile.type_values(kind)
        return int(next(raw for raw, name in names.items() if name == value))
    if isinstance(value, str) or value is None:
        return value
    scale, offset = field['scale'][0], field['offset'][0]
    if scale != 1 or offset:
        return int(round((value + offset) * scale))
    return value


def _spec(profile, number, key, value):
    """(field number, base type byte, size, raw value) for one field of a message."""
    if isinstance(key, int):
        name, raw = value
        ident, width = BASE_BY_NAME[name]
        count = len(raw) if isinstance(raw, (list, tuple)) else 1
        return key, 0x80 | ident if width > 1 else ident, width * count, raw
    fnum = profile.field_number(number, key)
    field = profile.field(number, fnum)
    ident, width = BASE_BY_NAME[field['base_type']]
    items = value if isinstance(value, (list, tuple)) else [value]
    raws = [_raw(field, v, profile) for v in items]
    raw = raws if isinstance(value, (list, tuple)) else raws[0]
    if ident == 7:
        size = len(raws[0].encode()) + 1
    else:
        size = width * len(raws)
    return fnum, 0x80 | ident if width > 1 else ident, size, raw


def build_ir(messages):
    profile = _get_profile()
    fit_file = FitFile()
    for name, fields in messages:
        number = profile.message_number(name)
        specs = [_spec(profile, number, key, value) for key, value in fields.items() if value is not None]
        definition = fit_file.add_definition(number, [(n, size, base) for n, base, size, _ in specs])
        fit_file.add_message(definition, {n: raw for n, _, _, raw in specs})
    return fit_file


def build_fit(messages):
    """Encodes (message name, fields) pairs into the bytes of a FIT file."""
    return encode(build_ir(messages))
