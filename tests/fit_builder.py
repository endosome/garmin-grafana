"""Builds real, CRC-valid FIT files for tests, driven by fitparse's own message profile.

    build_fit([
        ("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": 123}),
        ("record", {"timestamp": datetime(2026, 1, 1, 10), "heart_rate": 120, "speed": 3.2}),
        ("session", {"start_time": datetime(2026, 1, 1, 10), "sport": "running"}),
    ])

Values are given in the units fitparse returns (scale/offset are applied here), enum
fields take their string names, and date_time fields take naive UTC datetimes. Fields
missing from fitparse's profile can be written by number with an explicit base type:
{140: ("uint16", 3500)}.
"""
import struct
from datetime import datetime, timezone

from fitparse import profile
from fitparse.records import BASE_TYPES

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)
BASE_TYPES_BY_NAME = {bt.name: bt for bt in BASE_TYPES.values()}
MESSAGES_BY_NAME = {m.name: (num, m) for num, m in profile.MESSAGE_TYPES.items()}

_CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def fit_crc(data, crc=0):
    for byte in data:
        for nibble in (byte & 0xF, (byte >> 4) & 0xF):
            tmp = _CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ _CRC_TABLE[nibble]
    return crc


def _field_spec(message, key, value):
    """Returns (field number, base type, raw values) for one field of a message."""
    if isinstance(key, int):
        base_type_name, raw = value
        return key, BASE_TYPES_BY_NAME[base_type_name], raw if isinstance(raw, (list, tuple)) else [raw]

    matches = [(num, f) for num, f in message.fields.items() if f.name == key]
    if not matches:
        raise KeyError(f"fitparse profile has no field {key!r} in message {message.name!r}")
    num, field = matches[0]
    field_type = field.type
    base_type = getattr(field_type, "base_type", None) or field_type

    def to_raw(v):
        if isinstance(v, datetime):
            return int((v.replace(tzinfo=timezone.utc) - FIT_EPOCH).total_seconds())
        if isinstance(v, str) and base_type.name != "string":
            reverse = {name: raw for raw, name in (getattr(field_type, "values", None) or {}).items()}
            return reverse[v]
        if field.scale or field.offset:
            return int(round((v + (field.offset or 0)) * (field.scale or 1)))
        return v

    values = value if isinstance(value, (list, tuple)) else [value]
    return num, base_type, [to_raw(v) for v in values]


def _encode_message(name, fields, local_type=0):
    global_num, message = MESSAGES_BY_NAME[name]
    specs = [_field_spec(message, key, value) for key, value in fields.items() if value is not None]
    definition = struct.pack("<BBBHB", 0x40 | local_type, 0, 0, global_num, len(specs))
    data = struct.pack("<B", local_type)
    for num, base_type, raws in specs:
        if base_type.name == "string":
            encoded = raws[0].encode() + b"\x00"
        else:
            encoded = b"".join(struct.pack("<" + base_type.fmt, raw) for raw in raws)
        definition += struct.pack("<BBB", num, len(encoded), base_type.identifier)
        data += encoded
    return definition + data


def build_fit(messages):
    """Encodes (message name, fields) pairs into the bytes of a FIT file."""
    body = b"".join(_encode_message(name, fields) for name, fields in messages)
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", fit_crc(header))
    content = header + body
    return content + struct.pack("<H", fit_crc(content))
