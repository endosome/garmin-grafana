import logging
from unittest import mock

import pytest
import requests
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from influxdb.exceptions import InfluxDBClientError

POINT = {"measurement": "Test", "time": "2026-01-01T00:00:00+00:00", "tags": {}, "fields": {"value": 1}}


@pytest.fixture
def no_device_sync(gf, monkeypatch):
    monkeypatch.setattr(gf, "get_last_sync", lambda: [])


def fail_once(error, on_date="2026-01-02"):
    """A daily_fetch_write stand-in that raises `error` the first time `on_date` is fetched."""
    calls = []

    def daily_fetch_write(date):
        calls.append(date)
        if date == on_date and calls.count(date) == 1:
            raise error

    return daily_fetch_write, calls


def test_failed_write_is_counted(gf):
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")
    before = gf.FAILED_WRITE_COUNT

    gf.write_points_to_influxdb([dict(POINT)])

    assert gf.FAILED_WRITE_COUNT == before + 1


@pytest.mark.parametrize("points", [[dict(POINT)], []], ids=["successful-write", "nothing-to-write"])
def test_write_without_error_is_not_counted(gf, points):
    before = gf.FAILED_WRITE_COUNT

    gf.write_points_to_influxdb(points)

    assert gf.FAILED_WRITE_COUNT == before


def test_complete_run_returns_true(gf, no_device_sync, monkeypatch):
    fetched = []
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: fetched.append(date) or gf.write_points_to_influxdb([dict(POINT)]))

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is True
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-01"]


def test_failed_write_makes_run_incomplete(gf, no_device_sync, monkeypatch):
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: gf.write_points_to_influxdb([dict(POINT)]))

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-01") is False


def test_failed_device_sync_write_makes_run_incomplete(gf, monkeypatch):
    gf.garmin_obj.get_device_last_used.return_value = {"lastUsedDeviceUploadTime": 1767261600000}
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: None)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-01") is False


def test_earlier_failed_writes_do_not_affect_later_runs(gf, no_device_sync, monkeypatch):
    monkeypatch.setattr(gf, "FAILED_WRITE_COUNT", gf.FAILED_WRITE_COUNT + 5)
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: None)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-01") is True


@pytest.mark.parametrize("error, settings", [
    (requests.exceptions.ConnectionError("connection refused"), {}),
    (requests.exceptions.Timeout("timed out"), {}),
    (requests.exceptions.HTTPError("404 Client Error: Not Found"), {}),
    (GarminConnectConnectionError("403 Forbidden"), {}),
    (requests.exceptions.HTTPError("500 Server Error"), {"MAX_CONSECUTIVE_500_ERRORS": 1}),
    (GarminConnectConnectionError("500 Server Error"), {"MAX_CONSECUTIVE_500_ERRORS": 1}),
    (ValueError("unexpected payload"), {"IGNORE_ERRORS": True}),
], ids=["connection", "timeout", "http-404", "garmin-403", "http-500-max", "garmin-500-max", "ignore-errors"])
def test_skipped_date_makes_run_incomplete(gf, no_device_sync, monkeypatch, error, settings):
    for name, value in settings.items():
        monkeypatch.setattr(gf, name, value)
    daily_fetch_write, fetched = fail_once(error)
    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is False
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-01"]  # not retried; the remaining dates are still fetched


@pytest.mark.parametrize("error", [
    requests.exceptions.HTTPError("500 Server Error"),
    GarminConnectConnectionError("500 Server Error"),
    GarminConnectTooManyRequestsError("429 Too Many Requests"),
], ids=["http-500", "garmin-500", "rate-limited"])
def test_date_retried_successfully_keeps_run_complete(gf, no_device_sync, monkeypatch, error):
    daily_fetch_write, fetched = fail_once(error)
    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is True
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-02", "2026-01-01"]


def test_relogin_after_auth_error_keeps_run_complete(gf, no_device_sync, monkeypatch):
    new_session = mock.MagicMock()
    monkeypatch.setattr(gf, "garmin_login", lambda: new_session)
    daily_fetch_write, fetched = fail_once(GarminConnectAuthenticationError("session expired"))
    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is True
    assert gf.garmin_obj is new_session
    assert fetched == ["2026-01-03", "2026-01-02", "2026-01-02", "2026-01-01"]


def test_incomplete_run_logs_skipped_dates_and_failed_writes(gf, no_device_sync, monkeypatch, caplog):
    gf.influxdbclient.write_points.side_effect = InfluxDBClientError("database down")

    def daily_fetch_write(date):
        if date == "2026-01-02":
            raise requests.exceptions.ConnectionError("connection refused")
        gf.write_points_to_influxdb([dict(POINT)])

    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    with caplog.at_level(logging.WARNING):
        gf.fetch_write_bulk("2026-01-01", "2026-01-03")

    assert "Incomplete sync : skipped dates ['2026-01-02'], 2 failed InfluxDB write(s)" in caplog.text


def test_ignored_error_is_logged_once_with_traceback(gf, no_device_sync, monkeypatch, caplog):
    monkeypatch.setattr(gf, "IGNORE_ERRORS", True)
    daily_fetch_write, _ = fail_once(ValueError("unexpected payload"), on_date="2026-01-01")
    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    gf.fetch_write_bulk("2026-01-01", "2026-01-01")

    (record,) = [r for r in caplog.records if "Failed to process" in r.getMessage()]
    assert record.levelname == "ERROR" and record.exc_info
    assert record.getMessage() == "IGNORE_ERRORS Enabled >> Failed to process 2026-01-01 : skipping date"
    assert sum("unexpected payload" in r.getMessage() for r in caplog.records) == 0  # only in the traceback


def test_unexpected_error_still_raises_without_ignore_errors(gf, no_device_sync, monkeypatch):
    monkeypatch.setattr(gf, "IGNORE_ERRORS", False)
    daily_fetch_write, _ = fail_once(ValueError("unexpected payload"), on_date="2026-01-01")
    monkeypatch.setattr(gf, "daily_fetch_write", daily_fetch_write)

    with pytest.raises(ValueError):
        gf.fetch_write_bulk("2026-01-01", "2026-01-01")


def test_pauses_between_days_when_rate_limited(gf, no_device_sync, monkeypatch):
    monkeypatch.setattr(gf, "RATE_LIMIT_CALLS_SECONDS", 5)
    monkeypatch.setattr(gf, "daily_fetch_write", lambda date: None)

    gf.fetch_write_bulk("2026-01-01", "2026-01-03")

    assert [c.args[0] for c in gf.time.sleep.call_args_list] == [3, 5, 5, 5]  # startup pause, then one per day
