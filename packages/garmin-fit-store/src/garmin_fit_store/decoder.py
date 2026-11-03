"""FIT bytes to the IR: 12 and 14 byte headers, normal and compressed timestamp records, developer fields, chained files."""
import struct

from . import values
from .ir import TIMESTAMP_FIELD, UINT32, FitFile, Message
from .protocol import FitError, base_type_id, crc16, element_size, unpack_header

FIELD_DESCRIPTION = 206
UINT8_BYTE = 0x02


class _Reader:
    def __init__(self, data, end):
        self.data, self.pos, self.end = data, 0, end

    def take(self, count):
        if self.pos + count > self.end:
            raise FitError('truncated record')
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk


def decode(data, check_crc=True):
    """Decode every file of a (possibly chained) byte string into one IR."""
    data = bytes(data)
    fit_file = FitFile()
    offset, first = 0, True
    while offset < len(data):
        if not first and not any(data[offset:]):
            break  # zero padding after the last file
        size, protocol, profile, data_size, header_ok = unpack_header(data, offset)
        if not header_ok and check_crc:
            raise FitError('header CRC mismatch')
        end = offset + size + data_size
        if end + 2 > len(data):
            raise FitError('truncated file')
        if check_crc and crc16(data[offset:end]) != struct.unpack_from('<H', data, end)[0]:
            raise FitError('file CRC mismatch')
        if first:
            fit_file.protocol, fit_file.profile = protocol, profile
        try:
            _decode_records(data[offset + size:end], fit_file)
        except (struct.error, IndexError, ValueError, KeyError, OverflowError) as error:
            raise FitError(f'malformed records: {error}') from error
        offset, first = end + 2, False
    if first:
        raise FitError('empty input')
    return fit_file


def _decode_records(body, fit_file):
    reader = _Reader(body, len(body))
    local = {}  # local message type -> Definition
    dev_types = {}  # (developer data index, field number) -> base type byte
    last_timestamp = 0
    with_timestamp = {}
    while reader.pos < reader.end:
        header = reader.take(1)[0]
        if header & 0x80:  # compressed timestamp header
            slot = (header >> 5) & 3
            definition = local.get(slot)
            if definition is None:
                raise FitError(f'data message for undefined local type {slot}')
            offset = header & 0x1F
            base = last_timestamp & ~0x1F
            last_timestamp = base + offset + (0x20 if offset < (last_timestamp & 0x1F) else 0)
            if definition.id not in with_timestamp:
                fields = tuple(f for f in definition.fields if f[0] != TIMESTAMP_FIELD) + ((TIMESTAMP_FIELD, 4, UINT32),)
                with_timestamp[definition.id] = fit_file.add_definition(definition.global_num, fields, definition.dev_fields, definition.arch)
            message = _data(reader, with_timestamp[definition.id], fit_file, dev_types, skip_timestamp=True)
            message.fields[TIMESTAMP_FIELD] = last_timestamp
        elif header & 0x40:
            local[header & 0x0F] = _definition(reader, header, fit_file)
        else:
            definition = local.get(header & 0x0F)
            if definition is None:
                raise FitError(f'data message for undefined local type {header & 0x0F}')
            message = _data(reader, definition, fit_file, dev_types)
            stamp = message.fields.get(TIMESTAMP_FIELD)
            if isinstance(stamp, int):
                last_timestamp = stamp
        if not (header & 0x40) or header & 0x80:
            if message.global_num == FIELD_DESCRIPTION:
                _register_developer_type(message, dev_types)
            stamp = message.fields.get(TIMESTAMP_FIELD)
            if isinstance(stamp, int):
                last_timestamp = stamp


def _definition(reader, header, fit_file):
    reader.take(1)
    arch = reader.take(1)[0]
    if arch > 1:
        raise FitError(f'bad architecture {arch}')
    global_num = struct.unpack('<H' if arch == 0 else '>H', reader.take(2))[0]
    fields = []
    for _ in range(reader.take(1)[0]):
        number, size, base = reader.take(3)
        base_type_id(base)
        if size == 0:
            raise FitError('zero size field')
        fields.append((number, size, base))
    dev_fields = []
    if header & 0x20:
        for _ in range(reader.take(1)[0]):
            number, size, index = reader.take(3)
            dev_fields.append((number, size, index))
    return fit_file.add_definition(global_num, fields, dev_fields, arch)


def _data(reader, definition, fit_file, dev_types, skip_timestamp=False):
    message = Message(len(fit_file.messages), definition.id, definition.global_num)
    for number, size, base in definition.fields:
        if skip_timestamp and number == TIMESTAMP_FIELD:
            continue
        value = values.decode(reader.take(size), base, definition.arch)
        if value is not None:
            message.fields[number] = value
    for number, size, index in definition.dev_fields:
        base = dev_types.get((index, number), UINT8_BYTE)
        if (index, number) not in dev_types:
            base = 0x0D  # byte: the description has not been seen, so the value stays raw bytes
        value = values.decode(reader.take(size), base, definition.arch)
        if value is not None:
            message.dev[(index, number)] = value
    fit_file.messages.append(message)
    return message


def _register_developer_type(message, dev_types):
    index, number, base = message.fields.get(0), message.fields.get(1), message.fields.get(2)
    if None not in (index, number, base):
        try:
            element_size(base)
        except FitError:
            return
        dev_types[(index, number)] = base
