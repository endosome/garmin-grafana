"""Runs garmin_fetch.py as a script to exercise the sync marker handling in its update loop."""
import runpy
import time
from pathlib import Path
from unittest import mock

import garminconnect
import influxdb
import pytest
import requests
from influxdb.exceptions import InfluxDBClientError

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "garmin_grafana" / "garmin_fetch.py"
UPDATE_INTERVAL = 999  # unique sleep duration marking the end of an update cycle


class StopLoop(BaseException):
    """Ends the infinite update loop; BaseException so no `except Exception` in the script swallows it."""


def run_update_cycles(monkeypatch, cycles, failing_cycles=(), failure="fetch", max_retries="2"):
    """Runs the update loop for the given number of cycles and returns the dates fetched in each one.

    A new watch sync is reported every cycle. With no data in InfluxDB, the first
    cycle starts from the default of 7 days ago. In failing cycles either every
    Garmin fetch raises (failure="fetch") or every InfluxDB write fails (failure="write").
    Pass max_retries=None to leave MAX_INCOMPLETE_SYNC_RETRIES unset.
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
