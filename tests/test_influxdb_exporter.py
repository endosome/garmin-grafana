"""influxdb_exporter: exporting every measurement to CSV files in a zip archive."""
import csv
import io
import runpy
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import influxdb
import influxdb_client_3
import pyarrow as pa
import pytest

from helpers import SRC

MEASUREMENT_DATA = {
    "HeartRateIntraday": [{"time": "2026-01-01T00:00:00Z", "HeartRate": 60, "Device": "W"},
                          {"time": "2026-01-01T00:02:00Z", "HeartRate": 62, "Device": "W"}],
    "SleepSummary": [],
    "StressIntraday": RuntimeError("query timeout"),
}


class Result:
    def __init__(self, points):
        self.points = points

    def get_points(self):
        return iter(self.points)


@pytest.fixture
def exporter(monkeypatch, tmp_path):
    """Runs the exporter script with mocked InfluxDB clients; archives are written to tmp_path instead of /tmp."""
    client = mock.MagicMock()
    queries = []

    def query(q, **kwargs):
        queries.append(q)
        if q == "SHOW MEASUREMENTS":
            return Result([{"name": name} for name in ["DemoPoint", "DeviceSync", *MEASUREMENT_DATA]])
        measurement = q.split('"')[1]
        data = MEASUREMENT_DATA[measurement]
        if isinstance(data, Exception):
            raise data
        return Result(data)

    client.query.side_effect = query
    monkeypatch.setattr(influxdb, "InfluxDBClient", mock.MagicMock(return_value=client))
    monkeypatch.setattr(influxdb_client_3, "InfluxDBClient3", mock.MagicMock(return_value=client))
    for name in ["INFLUXDB_VERSION", "INFLUXDB_ENDPOINT_IS_HTTP"]:
        monkeypatch.delenv(name, raising=False)

    real_zipfile = zipfile.ZipFile

    def zipfile_in_tmp(path, *args, **kwargs):
        return real_zipfile(tmp_path / Path(path).name, *args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", zipfile_in_tmp)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["influxdb_exporter.py", *args])
        result = runpy.run_path(str(SRC / "influxdb_exporter.py"), run_name="__main__")
        archive = tmp_path / Path(result["zip_filename"]).name
        with real_zipfile(archive) as zf:
            files = {name: list(csv.DictReader(io.StringIO(zf.read(name).decode()))) for name in zf.namelist()}
        return result, files, queries

    return run


def test_exports_each_measurement_to_csv(exporter, capsys):
    result, files, queries = exporter("--start-date", "2026-01-01", "--end-date", "2026-01-31")

    assert list(files) == ["HeartRateIntraday.csv"]
    assert files["HeartRateIntraday.csv"] == [
        {"measurement": "HeartRateIntraday", "time": "2026-01-01T00:00:00Z", "HeartRate": "60", "Device": "W"},
        {"measurement": "HeartRateIntraday", "time": "2026-01-01T00:02:00Z", "HeartRate": "62", "Device": "W"},
    ]
    assert result["zip_filename"].startswith("/tmp/GarminStats_Export_") and result["zip_filename"].endswith("_2026-01-01_to_2026-01-31.zip")
    assert 'SELECT * FROM "HeartRateIntraday" WHERE time >= \'2026-01-01T00:00:00+00:00\' AND time < \'2026-02-01T00:00:00+00:00\'' in queries
    out = capsys.readouterr().out
    assert "Skipping: DemoPoint" in out and "Skipping: DeviceSync" in out  # never queried
    assert "No data within given period" in out  # SleepSummary
    assert "Query failed for StressIntraday: query timeout" in out
    assert "Exported 1 measurement CSVs" in out


def test_last_n_days(exporter):
    result, _, _ = exporter("--last-n-days", "7")

    assert result["time_label"] == "Last7Days"
    assert result["end_time"] - result["start_time"] == timedelta(days=7)


def test_defaults_to_last_30_days(exporter):
    result, _, _ = exporter()

    today = datetime.now(timezone.utc).date()
    assert (result["start_time"].date(), result["end_time"].date()) == (today - timedelta(days=30), today)


@pytest.mark.parametrize("args, message", [
    (["--start-date", "2026-02-01", "--end-date", "2026-01-01"], "Start date must be before end date"),
    (["--start-date", "01/02/2026"], "Invalid date format"),
])
def test_invalid_dates(exporter, args, message):
    with pytest.raises(ValueError, match=message):
        exporter(*args)


def test_nothing_to_export(exporter, monkeypatch, capsys):
    monkeypatch.setitem(MEASUREMENT_DATA, "HeartRateIntraday", [])

    _, files, _ = exporter()

    assert files == {}
    assert "No data collected or exported" in capsys.readouterr().out


def test_https_connection(exporter, monkeypatch):
    monkeypatch.setenv("INFLUXDB_ENDPOINT_IS_HTTP", "False")

    exporter()

    kwargs = influxdb.InfluxDBClient.call_args.kwargs
    assert (kwargs["ssl"], kwargs["verify_ssl"]) == (True, True)


@pytest.mark.parametrize("is_http", ["True", "False"])
def test_influxdb_v3_export(exporter, monkeypatch, is_http):
    monkeypatch.setenv("INFLUXDB_VERSION", "3")
    monkeypatch.setenv("INFLUXDB_ENDPOINT_IS_HTTP", is_http)
    client = influxdb_client_3.InfluxDBClient3.return_value
    client.query.side_effect = lambda q, **kwargs: pa.table({"name": ["HeartRateIntraday"]}) if q == "SHOW MEASUREMENTS" \
        else pa.table({"time": ["2026-01-01T00:00:00Z"], "HeartRate": [60]})

    _, files, _ = exporter()

    assert list(files) == ["HeartRateIntraday.csv"]
