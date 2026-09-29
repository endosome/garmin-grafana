"""write_points_to_influxdb, iter_days and _is_http_status_error."""
from unittest import mock

import pytest
from influxdb.exceptions import InfluxDBClientError
from influxdb_client_3 import InfluxDBError

from helpers import written_points


def make_points(n):
    return [{"measurement": "M", "time": i, "tags": {"Device": "W"}, "fields": {"v": i}} for i in range(n)]


def test_writes_all_points_with_v1_client(gf):
    points = make_points(3)

    gf.write_points_to_influxdb(points)

    assert written_points(gf.influxdbclient) == points


def test_nothing_is_written_for_empty_list(gf):
    gf.write_points_to_influxdb([])

    gf.influxdbclient.write_points.assert_not_called()


@pytest.mark.parametrize("count, expected_chunks", [(20000, [20000]), (20001, [20000, 1]), (45000, [20000, 20000, 5000])])
def test_large_writes_are_chunked(gf, count, expected_chunks):
    gf.write_points_to_influxdb(make_points(count))

    chunks = [len(call.args[0]) for call in gf.influxdbclient.write_points.call_args_list]
    assert chunks == expected_chunks


def test_v3_client_uses_write_with_records(gf, monkeypatch):
    monkeypatch.setattr(gf, "INFLUXDB_VERSION", "3")
    points = make_points(2)

    gf.write_points_to_influxdb(points)

    gf.influxdbclient.write.assert_called_once_with(record=points)
    gf.influxdbclient.write_points.assert_not_called()


@pytest.mark.parametrize("error", [InfluxDBClientError("bad request"), InfluxDBError(message="v3 write failed")])
def test_client_errors_are_logged_not_raised(gf, monkeypatch, error, caplog):
    monkeypatch.setattr(gf, "INFLUXDB_VERSION", "3" if isinstance(error, InfluxDBError) else "1")
    gf.influxdbclient.write_points.side_effect = error
    gf.influxdbclient.write.side_effect = error
    before = gf.FAILED_WRITE_COUNT

    gf.write_points_to_influxdb(make_points(1))

    assert gf.FAILED_WRITE_COUNT == before + 1
    assert "Write failed" in caplog.text


def test_write_log_names_measurements_and_counts(gf, caplog):
    caplog.set_level("INFO")
    points = make_points(3) + [{"measurement": "Other", "time": 9, "tags": {}, "fields": {"v": 1}}]

    gf.write_points_to_influxdb(points)

    assert "updated influxDB database with 4 new points (M=3, Other=1)" in caplog.text


@pytest.mark.parametrize("display_name, expected", [("runner42", "runner42"), (None, "Unknown")])
def test_user_tag_added_when_enabled(gf, monkeypatch, display_name, expected):
    monkeypatch.setattr(gf, "TAG_MEASUREMENTS_WITH_USER_EMAIL", True)
    gf.garmin_obj.display_name = display_name

    gf.write_points_to_influxdb(make_points(2))

    assert all(p["tags"]["User_ID"] == expected for p in written_points(gf.influxdbclient))


def test_user_tag_tolerates_object_without_display_name(gf, monkeypatch):
    monkeypatch.setattr(gf, "TAG_MEASUREMENTS_WITH_USER_EMAIL", True)
    monkeypatch.setattr(gf, "garmin_obj", object())  # e.g. GarminBulkExport

    gf.write_points_to_influxdb(make_points(1))

    assert written_points(gf.influxdbclient)[0]["tags"]["User_ID"] == "Unknown"


def test_user_tag_not_added_by_default(gf):
    gf.write_points_to_influxdb(make_points(1))

    assert "User_ID" not in written_points(gf.influxdbclient)[0]["tags"]


@pytest.mark.parametrize("start, end, expected", [
    ("2026-01-01", "2026-01-03", ["2026-01-03", "2026-01-02", "2026-01-01"]),
    ("2026-01-01", "2026-01-01", ["2026-01-01"]),
    ("2026-01-02", "2026-01-01", []),
    ("2025-12-31", "2026-01-01", ["2026-01-01", "2025-12-31"]),
    ("2024-02-28", "2024-03-01", ["2024-03-01", "2024-02-29", "2024-02-28"]),
])
def test_iter_days_runs_backwards_inclusive(gf, start, end, expected):
    assert list(gf.iter_days(start, end)) == expected


@pytest.mark.parametrize("error, status, expected", [
    (mock.Mock(response=mock.Mock(status_code=500)), 500, True),
    (mock.Mock(response=mock.Mock(status_code=404), spec=["response"]), 500, False),
    (mock.Mock(status_code=429, spec=["status_code"]), 429, True),
    (Exception("500 Server Error: Internal Server Error"), 500, True),
    (Exception("Error in request: 500"), 500, True),
    (Exception("404 Client Error"), 500, False),
    (Exception("took 15000 ms"), 500, False),
    (Exception("code 5000"), 500, False),
])
def test_is_http_status_error(gf, error, status, expected):
    assert gf._is_http_status_error(error, status) is expected
