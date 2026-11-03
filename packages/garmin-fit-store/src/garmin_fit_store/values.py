"""Raw values to and from the bytes of a field, by base type, endianness and size."""
import math
import struct

from .protocol import BASE_TYPES, BYTE, FLOAT32, FLOAT64, STRING, base_type_id


def _format(ident, arch):
    return ('>' if arch else '<') + BASE_TYPES[ident][1]


def invalid_bytes(ident, size, arch):
    """The invalid sentinel repeated over size bytes."""
    name, fmt, width, sentinel = BASE_TYPES[ident]
    if ident == STRING:
        return bytes(size)
    if ident in (FLOAT32, FLOAT64):
        element = b'\xff' * width
    else:
        element = struct.pack(_format(ident, arch), sentinel)
    return element * (size // width) + b'\xff' * (size % width)


def decode(raw, ident_byte, arch):
    """The value of a field's bytes, or None when it is invalid."""
    ident = base_type_id(ident_byte)
    width = BASE_TYPES[ident][2]
    if ident == STRING:
        trimmed = raw.rstrip(b'\0')
        if not trimmed:
            return None
        try:
            parts = [part.decode('utf-8') for part in trimmed.split(b'\0')]
        except UnicodeDecodeError:
            return {'hex': raw.hex()}
        return parts[0] if len(parts) == 1 else parts
    if len(raw) % width:
        return None if all(b == 0xFF for b in raw) else list(raw)
    count = len(raw) // width
    values = struct.unpack(('>' if arch else '<') + BASE_TYPES[ident][1] * count, raw)
    sentinel = BASE_TYPES[ident][3]
    if ident in (FLOAT32, FLOAT64):
        values = [None if math.isnan(v) else v for v in values]
        invalid = [v is None for v in values]
    else:
        invalid = [v == sentinel for v in values]
    if all(invalid):
        return None
    values = [(sentinel if ident not in (FLOAT32, FLOAT64) else None) if bad else v for v, bad in zip(values, invalid)]
    return values[0] if count == 1 else values


def encode(value, ident_byte, size, arch):
    """The bytes of a value; longer than size when the value does not fit, so the caller grows the field."""
    ident = base_type_id(ident_byte)
    width = BASE_TYPES[ident][2]
    if value is None:
        return invalid_bytes(ident, size, arch)
    if ident == STRING:
        if isinstance(value, dict):
            data = bytes.fromhex(value['hex'])
        else:
            parts = value if isinstance(value, list) else [value]
            data = b'\0'.join(part.encode('utf-8') for part in parts) + b'\0'
        return data + bytes(max(0, size - len(data)))
    if size % width:
        data = bytes(value)
        return data + b'\xff' * max(0, size - len(data))
    values = value if isinstance(value, list) else [value]
    count = max(len(values), size // width)
    values = list(values) + [None] * (count - len(values))
    fmt = ('>' if arch else '<') + BASE_TYPES[ident][1]
    out = b''
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out += invalid_bytes(ident, width, arch)
        else:
            out += struct.pack(fmt, v)
    return out
