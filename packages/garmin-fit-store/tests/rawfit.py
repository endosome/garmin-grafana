"""Assembles FIT files byte by byte, for shapes the builder does not produce: big-endian, compressed timestamps,
developer fields, odd sizes, chained files and bad checksums."""
import struct

from garmin_fit_store.protocol import crc16, pack_header

ENUM, UINT8, UINT16, UINT32, STRING, FLOAT32, BYTE = 0x00, 0x02, 0x84, 0x86, 0x07, 0x88, 0x0D
SINT32, UINT32Z, SINT16, UINT8Z, FLOAT64, UINT64 = 0x85, 0x8C, 0x83, 0x0A, 0x89, 0x8F


def definition(local, global_num, fields, dev=(), arch=0):
    out = bytes([0x40 | (0x20 if dev else 0) | local, 0, arch]) + struct.pack('>H' if arch else '<H', global_num)
    out += bytes([len(fields)]) + b''.join(bytes(f) for f in fields)
    if dev:
        out += bytes([len(dev)]) + b''.join(bytes(f) for f in dev)
    return out


def data(local, payload):
    return bytes([local]) + payload


def compressed(local, offset, payload):
    return bytes([0x80 | (local << 5) | offset]) + payload


def fit(records, header_size=14, protocol=0x20, profile=2132, header_crc=True, bad_crc=False):
    body = b''.join(records)
    header = pack_header(protocol, profile, len(body), header_size)
    if header_size == 14 and not header_crc:
        header = header[:12] + b'\0\0'
    content = header + body
    crc = crc16(content) ^ (1 if bad_crc else 0)
    return content + struct.pack('<H', crc)


def u16(*v, arch=0):
    return struct.pack(('>' if arch else '<') + 'H' * len(v), *v)


def u32(*v, arch=0):
    return struct.pack(('>' if arch else '<') + 'I' * len(v), *v)


def string(text, size):
    return text.encode().ljust(size, b'\0')
