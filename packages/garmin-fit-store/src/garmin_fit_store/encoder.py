"""IR to FIT bytes: normal headers only, one definition whenever a local message type's layout changes."""
import struct
from collections import OrderedDict

from . import values
from .decoder import FIELD_DESCRIPTION
from .ir import FitFile
from .protocol import FitError, crc16, pack_header

LOCAL_TYPES = 16


def _fit_size(chunk, size):
    return max(size, len(chunk))


def encode(fit_file):
    dev_types = {}
    out = bytearray()
    slots = OrderedDict()  # layout key -> local type, least recently used first
    definitions = {d.id: d for d in fit_file.definitions}
    for message in fit_file.messages:
        definition = definitions[message.def_id]
        unknown = set(message.fields) - {f[0] for f in definition.fields}
        unknown_dev = set(message.dev) - {(f[2], f[0]) for f in definition.dev_fields}
        if unknown or unknown_dev:
            raise FitError(f'message {message.seq} has fields its definition lacks: {sorted(unknown)} {sorted(unknown_dev)}')
        body, layout, dev_layout = bytearray(), [], []
        for number, size, base in definition.fields:
            chunk = values.encode(message.fields.get(number), base, size, definition.arch)
            layout.append((number, _fit_size(chunk, size), base))
            body += chunk
        for number, size, index in definition.dev_fields:
            base = dev_types.get((index, number), 0x0D)
            chunk = values.encode(message.dev.get((index, number)), base, size, definition.arch)
            dev_layout.append((number, _fit_size(chunk, size), index))
            body += chunk
        key = (definition.global_num, definition.arch, tuple(layout), tuple(dev_layout))
        if key in slots:
            slots.move_to_end(key)
        else:
            if len(slots) < LOCAL_TYPES:
                local = len(slots)
                used = set(slots.values())
                local = next(i for i in range(LOCAL_TYPES) if i not in used)
            else:
                _, local = slots.popitem(last=False)
            slots[key] = local
            out += _definition(local, *key)
        out.append(slots[key])
        out += body
        if definition.global_num == FIELD_DESCRIPTION:
            index, number, base = (message.fields.get(n) for n in (0, 1, 2))
            if None not in (index, number, base):
                dev_types[(index, number)] = base
    header = pack_header(fit_file.protocol, fit_file.profile, len(out))
    content = header + bytes(out)
    return content + struct.pack('<H', crc16(content))


def _definition(local, global_num, arch, fields, dev_fields):
    record = bytearray([0x40 | (0x20 if dev_fields else 0) | local, 0, arch])
    record += struct.pack('>H' if arch else '<H', global_num)
    record.append(len(fields))
    for number, size, base in fields:
        record += bytes([number, size, base])
    if dev_fields:
        record.append(len(dev_fields))
        for number, size, index in dev_fields:
            record += bytes([number, size, index])
    return bytes(record)
