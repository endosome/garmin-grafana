"""Assertions and data builders shared by the test modules."""
import io
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from garmin_grafana import storage
from garmin_grafana.domain import Record

SRC = Path(__file__).resolve().parents[1] / "src" / "garmin_grafana"
GARMIN_FETCH = SRC / "garmin_fetch.py"


def assert_valid_points(points):
    """Validate connector envelopes, consistent field types and domain normalization."""
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
    for point in points:
        Record.from_mapping(point)


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
    """Expose persisted domain records in the collector shape for regression assertions."""
    return [dict(measurement=r.dataset, time=r.original_time, tags=r.tags, fields=r.values)
            for r in client.persisted]


def run_module_command(monkeypatch, module_name, *arguments):
    """Exercise a documented python -m entry point without spawning an unmeasured child."""
    import runpy
    import sys
    with monkeypatch.context() as context:
        context.setattr(sys, 'argv', [module_name, *arguments])
        # runpy otherwise warns if an earlier test imported the target module.
        context.delitem(sys.modules, module_name, raising=False)
        return runpy.run_module(module_name, run_name='__main__')


def refresh_rollups():
    """Materializes the rollups, so they include samples a test wrote into hours an earlier refresh already covered."""
    with storage.connect() as connection:
        connection.autocommit = True
        connection.execute('SELECT pg_advisory_lock(7003)')  # pytest-xdist workers share the database; a concurrent refresh fails
        for view in ('samples_hourly', 'gps_cells_daily', 'hourly_medians', 'wear_hourly'):
            connection.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
