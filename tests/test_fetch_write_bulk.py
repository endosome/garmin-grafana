import pytest
import requests
from influxdb.exceptions import InfluxDBClientError

POINT = {"measurement": "Test", "time": "2026-01-01T00:00:00+00:00", "tags": {}, "fields": {"value": 1}}


@pytest.fixture
def no_device_sync(gf, monkeypatch):
    monkeypatch.setattr(gf, "get_last_sync", lambda: [])


def test_failed_write_is_counted(gf):
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")
    before = gf.FAILED_WRITE_COUNT

    gf.write_points_to_influxdb([dict(POINT)])

    assert gf.FAILED_WRITE_COUNT == before + 1


def test_complete_run_returns_true(gf, no_device_sync, monkeypatch):
    fetched = []
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: fetched.append(date) or gf.write_points_to_influxdb([dict(POINT)]))

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is True
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-01"]


def test_failed_write_makes_run_incomplete(gf, no_device_sync, monkeypatch):
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: gf.write_points_to_influxdb([dict(POINT)]))

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-01") is False


def test_earlier_failed_writes_do_not_affect_later_runs(gf, no_device_sync, monkeypatch):
    monkeypatch.setattr(gf, "FAILED_WRITE_COUNT", gf.FAILED_WRITE_COUNT + 5)
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: None)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-01") is True


@pytest.mark.parametrize("error, settings", [
    (requests.exceptions.ConnectionError("connection refused"), {}),
    (requests.exceptions.Timeout("timed out"), {}),
    (requests.exceptions.HTTPError("404 Client Error: Not Found"), {}),
    (requests.exceptions.HTTPError("500 Server Error"), {"MAX_CONSECUTIVE_500_ERRORS": 1}),
    (ValueError("unexpected payload"), {"IGNORE_ERRORS": True}),
], ids=["connection", "timeout", "http-404", "http-500-max", "ignore-errors"])
def test_skipped_date_makes_run_incomplete(gf, no_device_sync, monkeypatch, error, settings):
    for name, value in settings.items():
        monkeypatch.setattr(gf, name, value)
    fetched = []

    def daily_fetch_write(date):
        fetched.append(date)
        if date == "2026-01-02":
            raise error

    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is False
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-01"]  # the remaining dates are still fetched


def test_unexpected_error_still_raises_without_ignore_errors(gf, no_device_sync, monkeypatch):
    monkeypatch.setattr(gf, "IGNORE_ERRORS", False)

    def daily_fetch_write(date):
        raise ValueError("unexpected payload")

    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    with pytest.raises(ValueError):
        gf.fetch_write_bulk("2026-01-01", "2026-01-01")
