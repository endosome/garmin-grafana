"""Runs garmin_fetch.py as a script to exercise the sync marker handling in its update loop."""
import runpy
import time
from pathlib import Path
from unittest import mock

import garminconnect
import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "garmin_grafana" / "garmin_fetch.py"
UPDATE_INTERVAL = 999  # unique sleep duration marking the end of an update cycle


class StopLoop(BaseException):
    """Ends the infinite update loop; BaseException so no `except Exception` in the script swallows it."""


def run_update_cycles(monkeypatch, cycles, failing_cycles=(), max_retries=2):
    """Runs the update loop for the given number of cycles and returns the dates fetched in each one.

    A new watch sync is reported every cycle. With no data in InfluxDB, the first
    cycle starts from the default of 7 days ago.
    """
    monkeypatch.setenv("FETCH_SELECTION", "daily_avg")
    monkeypatch.setenv("UPDATE_INTERVAL_SECONDS", str(UPDATE_INTERVAL))
    monkeypatch.setenv("MAX_INCOMPLETE_SYNC_RETRIES", str(max_retries))

    cycle = 0
    fetched = [[] for _ in range(cycles)]
    upload_time_ms = int(time.time() * 1000)

    garmin = mock.MagicMock()
    garmin.get_device_last_used.side_effect = lambda: {"lastUsedDeviceUploadTime": upload_time_ms + cycle * 1000}
    garmin.get_last_activity.return_value = {"startTimeLocal": "2026-01-01 10:00:00", "startTimeGMT": "2026-01-01 10:00:00"}

    def get_stats(date):
        fetched[cycle].append(date)
        if cycle in failing_cycles:
            raise requests.exceptions.ConnectionError("Garmin unreachable")
        return {"wellnessStartTimeGmt": None}

    garmin.get_stats.side_effect = get_stats

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


def test_incomplete_sync_is_retried_then_given_up(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=4, failing_cycles={0, 1, 2, 3}, max_retries=2)

    assert fetched[0] == fetched[1] == fetched[2]  # the same range is retried twice
    assert len(fetched[3]) < len(fetched[0])  # then the marker moves on
    out = capsys.readouterr().out
    assert "(retry 1/2)" in out and "(retry 2/2)" in out
    assert "advancing sync marker anyway" in out


def test_retry_count_resets_after_complete_sync(offline, monkeypatch, capsys):
    fetched = run_update_cycles(monkeypatch, cycles=4, failing_cycles={0, 2}, max_retries=2)

    assert fetched[1] == fetched[0]  # retried after the first failure
    out = capsys.readouterr().out
    assert out.count("(retry 1/2)") == 2
    assert "(retry 2/2)" not in out
    assert "advancing sync marker anyway" not in out
