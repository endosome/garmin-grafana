"""Runs garmin_fetch.py as a script: startup (resume point, timezone, manual range) and the sync marker handling in its update loop."""
import runpy
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import garminconnect
import influxdb
import influxdb_client_3
import pytest
import requests
from influxdb.exceptions import InfluxDBClientError

from helpers import GARMIN_FETCH as SCRIPT

UPDATE_INTERVAL = 999  # unique sleep duration marking the end of an update cycle


class StopLoop(BaseException):
    """Ends the infinite update loop; BaseException so no `except Exception` in the script swallows it."""


def run_update_cycles(monkeypatch, cycles, failing_cycles=(), failure="fetch", max_retries="2", configure=None):
    """Runs the update loop for the given number of cycles and returns the dates fetched in each one.

    A new watch sync is reported every cycle. With no data in InfluxDB, the first
    cycle starts from the default of 7 days ago. In failing cycles either every
    Garmin fetch raises (failure="fetch") or every InfluxDB write fails (failure="write").
    Pass max_retries=None to leave MAX_INCOMPLETE_SYNC_RETRIES unset. configure(garmin) can
    adjust the Garmin mock before the script starts.
    """
    monkeypatch.setenv("FETCH_SELECTION", "daily_avg")
    monkeypatch.setenv("UPDATE_INTERVAL_SECONDS", str(UPDATE_INTERVAL))
    if max_retries is not None:
        monkeypatch.setenv("MAX_INCOMPLETE_SYNC_RETRIES", max_retries)

    cycle = 0
    fetched = [[] for _ in range(cycles)]
    upload_time_ms = int(time.time() * 1000)

    garmin = mock.MagicMock()
    garmin.get_device_last_used.side_effect = lambda: {"lastUsedDeviceUploadTime": upload_time_ms + cycle * 1000}
    garmin.get_last_activity.return_value = {"startTimeLocal": "2026-01-01 10:00:00", "startTimeGMT": "2026-01-01 10:00:00"}

    def get_stats(date):
        fetched[cycle].append(date)
        if failure == "fetch" and cycle in failing_cycles:
            raise requests.exceptions.ConnectionError("Garmin unreachable")
        return {"wellnessStartTimeGmt": None}

    garmin.get_stats.side_effect = get_stats

    def write_points(points):
        # The DeviceSync point is written at the start of every sync run; the startup DemoPoint must succeed.
        if failure == "write" and cycle in failing_cycles and points[0]["measurement"] != "DemoPoint":
            raise InfluxDBClientError("database down")

    # influxdb.InfluxDBClient is the mock installed by the `offline` fixture, discarded after the test.
    influxdb.InfluxDBClient.return_value.write_points.side_effect = write_points

    def sleep(seconds):
        nonlocal cycle
        if seconds == UPDATE_INTERVAL:
            cycle += 1
            if cycle == cycles:
                raise StopLoop

    if configure:
        configure(garmin)
    monkeypatch.setattr(garminconnect, "Garmin", mock.MagicMock(return_value=garmin))
    monkeypatch.setattr(time, "sleep", sleep)

    with pytest.raises(StopLoop):
        runpy.run_path(str(SCRIPT), run_name="__main__")
    return fetched


def test_complete_sync_advances_marker(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=2)

    assert len(fetched[0]) == 8  # default initial range: 7 days ago up to today
    assert len(fetched[1]) < len(fetched[0])
    assert "Sync marker not advanced" not in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["fetch", "write"])
def test_incomplete_sync_is_retried_then_given_up(offline, monkeypatch, capsys, failure):
    fetched = run_update_cycles(monkeypatch, cycles=4, failing_cycles={0, 1, 2, 3}, failure=failure)

    assert len(fetched[0]) == 8
    assert fetched[0] == fetched[1] == fetched[2]  # the same range is retried twice
    assert len(fetched[3]) < len(fetched[0])  # then the marker moves on
    out = capsys.readouterr().out
    assert "(retry 1/2)" in out and "(retry 2/2)" in out
    assert "(retry 3/2)" not in out
    assert "advancing sync marker anyway" in out


@pytest.mark.parametrize("failure", ["fetch", "write"])
def test_recovered_sync_advances_marker(offline, monkeypatch, capsys, failure):
    fetched = run_update_cycles(monkeypatch, cycles=3, failing_cycles={0}, failure=failure)

    assert fetched[1] == fetched[0]  # retried after the failure, and completes
    assert len(fetched[2]) < len(fetched[0])  # so the next cycle starts from the new marker
    out = capsys.readouterr().out
    assert out.count("Sync marker not advanced") == 1
    assert "advancing sync marker anyway" not in out


def test_retry_count_resets_after_complete_sync(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=4, failing_cycles={0, 2})

    assert fetched[1] == fetched[0]
    out = capsys.readouterr().out
    assert out.count("(retry 1/2)") == 2
    assert "(retry 2/2)" not in out
    assert "advancing sync marker anyway" not in out


def test_zero_retries_advances_marker_immediately(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=2, failing_cycles={0, 1}, max_retries="0")

    assert len(fetched[1]) < len(fetched[0])
    out = capsys.readouterr().out
    assert "Sync marker not advanced" not in out
    assert out.count("advancing sync marker anyway") == 2


def test_retries_default_to_three(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=5, failing_cycles={0, 1, 2, 3, 4}, max_retries=None)

    assert fetched[0] == fetched[1] == fetched[2] == fetched[3]
    assert len(fetched[4]) < len(fetched[0])
    out = capsys.readouterr().out
    assert "(retry 3/3)" in out
    assert "Sync still incomplete after 3 retries" in out


# --- startup: where the first sync starts, and in which timezone ---------------------------

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_date(value, hours):
    return (value + timedelta(hours=hours)).strftime("%Y-%m-%d")


@pytest.mark.parametrize("version", ["1", "3"])
def test_resumes_from_latest_heart_rate_point(offline, monkeypatch, version):
    monkeypatch.setenv("INFLUXDB_VERSION", version)
    last_point = (utc_now() - timedelta(days=2)).replace(microsecond=0)
    if version == "1":
        client = influxdb.InfluxDBClient.return_value
        client.query.return_value.get_points.return_value = [{"time": last_point.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    else:
        client = influxdb_client_3.InfluxDBClient3.return_value
        client.query.return_value.to_pylist.return_value = [{"time": last_point}]

    fetched = run_update_cycles(monkeypatch, cycles=1)

    assert fetched[0][-1] == last_point.strftime("%Y-%m-%d")  # oldest date fetched first run
    assert fetched[0][0] == utc_now().strftime("%Y-%m-%d")
    query = client.query.call_args
    assert "SELECT * FROM HeartRateIntraday ORDER BY time DESC LIMIT 1" in (query.args + tuple(query.kwargs.values()))


def set_last_point(value):
    influxdb.InfluxDBClient.return_value.query.return_value.get_points.return_value = [{"time": value.strftime("%Y-%m-%dT%H:%M:%SZ")}]


def test_user_timezone_decides_local_dates(offline, monkeypatch):
    last_point = (utc_now() - timedelta(days=3)).replace(hour=23, minute=30, second=0, microsecond=0)
    set_last_point(last_point)
    monkeypatch.setenv("USER_TIMEZONE", "Asia/Tokyo")  # UTC+9, no DST

    garmin_calls = []
    fetched = run_update_cycles(monkeypatch, cycles=1, configure=lambda garmin: garmin_calls.append(garmin))

    assert fetched[0][-1] == local_date(last_point, 9)  # 23:30 UTC is already the next day in Tokyo
    assert fetched[0][0] == local_date(utc_now(), 9)
    garmin_calls[0].get_last_activity.assert_not_called()


def test_timezone_detected_from_last_activity(offline, monkeypatch, capsys):
    last_point = (utc_now() - timedelta(days=3)).replace(hour=2, minute=0, second=0, microsecond=0)
    set_last_point(last_point)

    def configure(garmin):
        garmin.get_last_activity.return_value = {"startTimeLocal": "2026-01-01 05:00:00", "startTimeGMT": "2026-01-01 10:00:00"}

    fetched = run_update_cycles(monkeypatch, cycles=1, configure=configure)

    assert fetched[0][-1] == local_date(last_point, -5)  # 02:00 UTC is still the previous day at UTC-5
    assert "local timezone as UTC-5:00:00" in capsys.readouterr().out


@pytest.mark.parametrize("last_activity", [{}, None])
def test_unknown_timezone_falls_back_to_utc(offline, monkeypatch, capsys, last_activity):
    last_point = (utc_now() - timedelta(days=3)).replace(hour=23, minute=30, second=0, microsecond=0)
    set_last_point(last_point)

    def configure(garmin):
        garmin.get_last_activity.return_value = last_activity

    fetched = run_update_cycles(monkeypatch, cycles=1, configure=configure)

    assert fetched[0][-1] == last_point.strftime("%Y-%m-%d")
    assert "Unable to determine user's timezone" in capsys.readouterr().out


def test_no_activities_falls_back_to_utc(offline, monkeypatch, capsys):
    def configure(garmin):
        garmin.get_last_activity.side_effect = IndexError("no activities")

    run_update_cycles(monkeypatch, cycles=1, configure=configure)

    assert "Unable to determine user's timezone" in capsys.readouterr().out


def test_no_new_watch_sync_fetches_nothing(offline, monkeypatch, capsys):
    set_last_point(utc_now() + timedelta(hours=1))  # InfluxDB already has data newer than the watch's last upload

    fetched = run_update_cycles(monkeypatch, cycles=2)

    assert fetched == [[], []]
    assert capsys.readouterr().out.count("No new data found") == 2


@pytest.mark.parametrize("failure", [
    {"lastUsedDeviceUploadTime": None},
    requests.exceptions.ConnectionError("network blip"),
], ids=["no-upload-time", "network-error"])
def test_update_loop_survives_device_sync_errors(offline, monkeypatch, failure):
    def configure(garmin):
        responses = iter([failure])
        normal = garmin.get_device_last_used.side_effect

        def get_device_last_used():
            response = next(responses, None)
            if isinstance(response, Exception):
                raise response
            return response or normal()

        garmin.get_device_last_used.side_effect = get_device_last_used

    run_update_cycles(monkeypatch, cycles=2, configure=configure)


def test_manual_date_range_runs_once_and_exits(offline, monkeypatch):
    monkeypatch.setenv("MANUAL_START_DATE", "2025-06-01")
    monkeypatch.setenv("MANUAL_END_DATE", "2025-06-03")
    monkeypatch.setenv("FETCH_SELECTION", "daily_avg")
    garmin = mock.MagicMock()
    garmin.get_device_last_used.return_value = {"lastUsedDeviceUploadTime": 1767261600000}
    garmin.get_stats.return_value = {"wellnessStartTimeGmt": None}
    monkeypatch.setattr(garminconnect, "Garmin", mock.MagicMock(return_value=garmin))
    monkeypatch.setattr(time, "sleep", mock.MagicMock())

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exit_info.value.code == 0
    assert [c.args[0] for c in garmin.get_stats.call_args_list] == ["2025-06-03", "2025-06-02", "2025-06-01"]
    time.sleep.assert_called()  # only fetch_write_bulk's short pauses...
    assert UPDATE_INTERVAL not in [c.args[0] for c in time.sleep.call_args_list]  # ...never the update loop
