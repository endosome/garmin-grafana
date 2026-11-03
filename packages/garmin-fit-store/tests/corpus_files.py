"""The conformance corpus: named files built from rawfit/builder, committed next to their expected JSONL."""
from datetime import datetime, timedelta

from garmin_fit_store.builder import build_fit
import rawfit as r

T0 = datetime(2026, 1, 1, 10)


def activity():
    messages = [("file_id", {"type": "activity", "manufacturer": "garmin", "garmin_product": None, "serial_number": 123,
                             "time_created": T0, "product_name": "Test Watch"}),
                ("device_info", {"timestamp": T0, "device_index": 0, "manufacturer": "garmin", "serial_number": 123})]
    for i in range(30):
        messages.append(("record", {"timestamp": T0 + timedelta(seconds=i), "position_lat": 119_000_000 + i * 11, "position_long": -50_000_000 - i * 7,
                                    "heart_rate": 120 + i % 5, "speed": 3.2 + i / 100, "altitude": 100.4,
                                    "distance": i * 3.1, "power": None if i % 7 == 0 else 200 + i}))
        if i == 10:
            messages.append(("event", {"timestamp": T0 + timedelta(seconds=i), "event": "timer", "event_type": "stop_all"}))
    messages += [("lap", {"timestamp": T0 + timedelta(seconds=30), "start_time": T0, "total_elapsed_time": 30.0,
                          "total_timer_time": 29.5, "total_distance": 93.0, "message_index": 0}),
                 ("session", {"timestamp": T0 + timedelta(seconds=30), "start_time": T0, "sport": "running",
                              "total_elapsed_time": 30.0, "total_timer_time": 29.5, "total_distance": 93.0,
                              "avg_heart_rate": 122, "message_index": 0}),
                 ("activity", {"timestamp": T0 + timedelta(seconds=30), "num_sessions": 1, "type": "manual"})]
    return build_fit(messages)


def developer_fields():
    return r.fit([
        r.definition(0, 0, [(0, 1, r.ENUM), (1, 2, r.UINT16), (3, 4, r.UINT32Z), (4, 4, r.UINT32)]),
        r.data(0, bytes([4]) + r.u16(1) + r.u32(42) + r.u32(1_100_000_000)),
        r.definition(1, 207, [(1, 16, r.BYTE), (3, 1, r.UINT8), (4, 4, r.UINT32)]),
        r.data(1, bytes(range(16)) + bytes([0]) + r.u32(7)),
        r.definition(2, 206, [(0, 1, r.UINT8), (1, 1, r.UINT8), (2, 1, r.UINT8), (3, 8, r.STRING), (8, 4, r.STRING)]),
        r.data(2, bytes([0, 0, 0x84]) + r.string('Stride', 8) + r.string('mm', 4)),
        r.data(2, bytes([0, 1, 0x02]) + r.string('Flag', 8) + r.string('', 4)),
        r.definition(3, 20, [(253, 4, r.UINT32), (3, 1, r.UINT8)], dev=[(0, 2, 0), (1, 1, 0)]),
        r.data(3, r.u32(1_100_000_000) + bytes([100]) + r.u16(1234) + bytes([3])),
        r.data(3, r.u32(1_100_000_001) + bytes([0xFF]) + r.u16(0xFFFF) + bytes([0xFF])),
    ])


def compressed_timestamps():
    return r.fit([
        r.definition(0, 20, [(253, 4, r.UINT32), (3, 1, r.UINT8)]),
        r.data(0, r.u32(1_100_000_000) + bytes([90])),
        r.definition(1, 20, [(3, 1, r.UINT8), (5, 4, r.UINT32)]),
        r.compressed(1, (1_100_000_000 + 3) & 0x1F, bytes([91]) + r.u32(500)),
        r.compressed(1, (1_100_000_000 + 31) & 0x1F, bytes([92]) + r.u32(600)),  # rolls the 5-bit offset over
        r.compressed(1, (1_100_000_000 + 34) & 0x1F, bytes([93]) + r.u32(700)),
        r.data(0, r.u32(1_100_000_100) + bytes([94])),
    ])


def big_endian():
    return r.fit([
        r.definition(0, 20, [(253, 4, r.UINT32), (2, 2, r.UINT16), (3, 1, r.UINT8), (0, 4, r.SINT32), (6, 8, r.UINT16)], arch=1),
        r.data(0, r.u32(1_100_000_000, arch=1) + r.u16(2500, arch=1) + bytes([77]) + b'\x80\x00\x00\x01' +
               r.u16(1, 2, 3, 0xFFFF, arch=1)),
    ])


def unknown_things():
    """Unknown message, unknown fields, an odd-sized field, floats, a 64-bit value, multi-string, non-UTF-8, 12-byte header."""
    return r.fit([
        r.definition(0, 0, [(0, 1, r.ENUM), (200, 3, r.UINT16), (201, 4, r.FLOAT32), (202, 8, r.FLOAT64), (203, 8, r.UINT64),
                            (204, 12, r.STRING), (205, 4, r.STRING), (206, 5, r.BYTE)]),
        r.data(0, bytes([4]) + b'\x01\x02\x03' + b'\x00\x00\x20\x41' + b'\x00' * 6 + b'\xf0\x3f' +
               bytes([0xFF] * 7 + [0x7F]) + b'ab\0cd\0\0\0\0\0\0\0' + b'\xff\xfe\0\0' + bytes([1, 2, 3, 0xFF, 5])),
        r.definition(1, 60000, [(0, 4, r.UINT32), (1, 2, r.UINT16)]),
        r.data(1, r.u32(0xDEADBEEF) + r.u16(0xFFFF)),
        r.data(1, r.u32(0xFFFFFFFF) + r.u16(9)),
        r.data(1, r.u32(0xFFFFFFFF) + r.u16(0xFFFF)),
    ], header_size=12)


def chained():
    return activity_small(1) + activity_small(2)


def activity_small(n):
    return r.fit([r.definition(0, 0, [(0, 1, r.ENUM), (3, 4, r.UINT32Z)]), r.data(0, bytes([4]) + r.u32(n)),
                  r.definition(1, 20, [(253, 4, r.UINT32), (3, 1, r.UINT8)]), r.data(1, r.u32(1_100_000_000 + n) + bytes([n]))])


def zero_header_crc():
    return r.fit([r.definition(0, 20, [(253, 4, r.UINT32)]), r.data(0, r.u32(1_100_000_000))], header_crc=False)


CORPUS = {
    'activity': activity, 'developer_fields': developer_fields, 'compressed_timestamps': compressed_timestamps,
    'big_endian': big_endian, 'unknown_things': unknown_things, 'chained': chained, 'zero_header_crc': zero_header_crc,
}
