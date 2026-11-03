"""FIT file framing: base types, CRC, headers and the sentinel that marks an invalid value."""
import struct

CRC_TABLE = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]

EPOCH_OFFSET = 631065600  # seconds from the Unix epoch to the FIT epoch, 1989-12-31T00:00:00Z

ENUM, SINT8, UINT8, SINT16, UINT16, SINT32, UINT32, STRING, FLOAT32, FLOAT64 = range(10)
UINT8Z, UINT16Z, UINT32Z, BYTE, SINT64, UINT64, UINT64Z = range(10, 17)

# id -> (name, struct format, size, invalid value)
BASE_TYPES = {
    ENUM: ('enum', 'B', 1, 0xFF), SINT8: ('sint8', 'b', 1, 0x7F), UINT8: ('uint8', 'B', 1, 0xFF),
    SINT16: ('sint16', 'h', 2, 0x7FFF), UINT16: ('uint16', 'H', 2, 0xFFFF),
    SINT32: ('sint32', 'i', 4, 0x7FFFFFFF), UINT32: ('uint32', 'I', 4, 0xFFFFFFFF),
    STRING: ('string', 's', 1, 0), FLOAT32: ('float32', 'f', 4, None), FLOAT64: ('float64', 'd', 8, None),
    UINT8Z: ('uint8z', 'B', 1, 0), UINT16Z: ('uint16z', 'H', 2, 0), UINT32Z: ('uint32z', 'I', 4, 0),
    BYTE: ('byte', 'B', 1, 0xFF), SINT64: ('sint64', 'q', 8, 0x7FFFFFFFFFFFFFFF),
    UINT64: ('uint64', 'Q', 8, 0xFFFFFFFFFFFFFFFF), UINT64Z: ('uint64z', 'Q', 8, 0),
}


class FitError(Exception):
    """A malformed FIT file."""


def crc16(data, crc=0):
    for byte in data:
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[byte & 0xF]
        tmp = CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def base_type_id(byte):
    """The base type id of a definition's base type byte (the high bit is the endian flag, bits 5 to 6 reserved)."""
    ident = byte & 0x1F
    if ident not in BASE_TYPES:
        raise FitError(f'unknown base type 0x{byte:02x}')
    return ident


def element_size(byte):
    return BASE_TYPES[base_type_id(byte)][2]


def unpack_header(data, offset=0):
    """(header size, protocol version, profile version, data size, header crc ok) of the header at offset."""
    if len(data) - offset < 12:
        raise FitError('truncated header')
    size = data[offset]
    if size not in (12, 14):
        raise FitError(f'bad header size {size}')
    if len(data) - offset < size:
        raise FitError('truncated header')
    if data[offset + 8:offset + 12] != b'.FIT':
        raise FitError('missing .FIT signature')
    protocol, profile, data_size = struct.unpack_from('<BHI', data, offset + 1)
    crc_ok = True
    if size == 14:
        stored = struct.unpack_from('<H', data, offset + 12)[0]
        crc_ok = stored == 0 or stored == crc16(data[offset:offset + 12])
    return size, protocol, profile, data_size, crc_ok


def pack_header(protocol, profile, data_size, size=14):
    header = struct.pack('<BBHI4s', size, protocol, profile, data_size, b'.FIT')
    if size == 14:
        header += struct.pack('<H', crc16(header))
    return header
