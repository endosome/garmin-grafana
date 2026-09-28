"""Assertions and data builders shared by the test modules."""
import io
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from influxdb.line_protocol import make_lines

SRC = Path(__file__).resolve().parents[1] / "src" / "garmin_grafana"
GARMIN_FETCH = SRC / "garmin_fetch.py"


def assert_valid_points(points):
    """Asserts points would be accepted by InfluxDB as a single write.

    Every point needs a measurement, a time and at least one non-None field (a point
    without fields encodes to an invalid line that fails the whole batch). A field
    must not mix integer and float values within a measurement, which InfluxDB
    rejects as a field type conflict. Finally the points must encode to line protocol.
    """
    field_types = defaultdict(set)
    for point in points:
        assert point.get("measurement"), point
        assert point.get("time") is not None, point
        fields = {k: v for k, v in point["fields"].items() if v is not None}
        assert fields, f"point has no non-None fields: {point}"
        for key, value in fields.items():
            kind = "bool" if isinstance(value, bool) else "int" if isinstance(value, int) else "float" if isinstance(value, float) else "str"
            field_types[(point["measurement"], key)].add(kind)
    conflicts = {key: kinds for key, kinds in field_types.items() if len(kinds) > 1}
    assert not conflicts, f"field type conflicts: {conflicts}"
    lines = make_lines({"points": points}).strip().split("\n") if points else []
    assert len(lines) == len(points)


def only(points, measurement):
    """Returns the points of one measurement."""
    return [p for p in points if p["measurement"] == measurement]


def gmt(value):
    """Formats a datetime like Garmin's '...GMT' string fields."""
    return value.strftime("%Y-%m-%dT%H:%M:%S.0")


def ms(value):
    """Converts a naive UTC datetime to Garmin's millisecond timestamps."""
    return int((value - datetime(1970, 1, 1)).total_seconds() * 1000)


def zip_bytes(files):
    """Returns the bytes of a zip archive holding {name: bytes}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def written_points(client):
    """All points passed to a mocked InfluxDB v1 client's write_points, in order."""
    return [p for call in client.write_points.call_args_list for p in call.args[0]]
