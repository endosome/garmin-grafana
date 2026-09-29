"""fit_activity_importer: importing a single local FIT file."""
import io
import json
import runpy
import sys
import zipfile
from datetime import datetime, timedelta

import pytest
from fitparse import FitFile

import fit_activity_importer as importer
from fit_builder import build_fit
from helpers import SRC, assert_valid_points, written_points

START = datetime(2026, 1, 1, 10, 0, 0)


def fit_bytes(serial=1234, session=None, product=None):
    session = {"start_time": START, "sport": "running", "total_elapsed_time": 1830.5, "total_timer_time": 1800.0,
               "total_distance": 5000.0, "avg_speed": 2.73, "max_speed": 3.9, "total_calories": 380,
               "avg_heart_rate": 150, "max_heart_rate": 172, "num_laps": 5} if session is None else session
    return build_fit([
        ("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": serial, "product": product}),
        ("record", {"timestamp": START, "heart_rate": 140}),
        ("record", {"timestamp": START + timedelta(seconds=1), "heart_rate": 141}),
        ("session", session),
        ("activity", {"timestamp": START + timedelta(seconds=1), "num_sessions": 1, "type": "manual"}),
    ])


def parsed(data):
    fit = FitFile(io.BytesIO(data))
    fit.parse()
    return fit


def test_summary_points(gf):
    activity_id, activity_type, start, end = importer.get_fit_activity_summary(parsed(fit_bytes()))

    assert activity_type == "running"
    assert_valid_points([start, end])
    assert start["time"] == "2026-01-01T10:00:00+00:00"
    assert end["time"] == "2026-01-01T10:30:30+00:00"  # whole seconds of total_elapsed_time
    assert start["tags"] == {"Device": "Garmin", "Database_Name": gf.INFLUXDB_DATABASE, "ActivityID": activity_id,
                             "ActivitySelector": "20260101T100000UTC-running"}
    assert start["fields"] == {
        "Device_ID": 1234, "activityType": "running", "activityName": "Running 2026-01-01", "distance": 5000.0,
        "elapsedDuration": 1830.5, "movingDuration": 1800.0, "averageSpeed": pytest.approx(2.73), "maxSpeed": pytest.approx(3.9),
        "calories": 380.0, "averageHR": 150.0, "maxHR": 172.0, "lapCount": 5,
    }
    assert end["fields"]["activityName"] == "END" and end["tags"] == start["tags"]


def test_activity_id_is_stable_per_file(gf):
    first = importer.get_fit_activity_summary(parsed(fit_bytes(serial=1)))[0]

    assert importer.get_fit_activity_summary(parsed(fit_bytes(serial=1)))[0] == first
    assert importer.get_fit_activity_summary(parsed(fit_bytes(serial=2)))[0] != first
    assert 0 <= first < 2 ** 32


def test_session_without_start_time_is_rejected(gf):
    with pytest.raises(ValueError, match="does not contain a session start_time"):
        importer.get_fit_activity_summary(parsed(fit_bytes(session={"sport": "running"})))


def test_summary_field_types_match_api_import(gf):
    gf.garmin_obj.get_activities_by_date.return_value = [{
        "activityId": 1, "startTimeGMT": "2026-01-02 10:00:00", "activityType": {"typeKey": "running"},
        "elapsedDuration": 1830.5, "movingDuration": 1800.0, "distance": 5000.0, "averageSpeed": 2.73, "maxSpeed": 3.9,
        "calories": 380.0, "averageHR": 150.0, "maxHR": 172.0, "lapCount": 5, "deviceId": 1234}]
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = []
    api_start = gf.get_activity_summary("2026-01-02")[0][0]

    fit_start = importer.get_fit_activity_summary(parsed(fit_bytes()))[2]

    assert_valid_points([api_start, fit_start])


def test_mock_garmin_serves_the_file_as_zip(tmp_path):
    path = tmp_path / "Morning_RUN.FIT"
    path.write_bytes(fit_bytes())

    with zipfile.ZipFile(io.BytesIO(importer.MockGarminObject(path).download_activity(123))) as zf:
        assert zf.namelist() == ["morning_run.fit"]
        assert zf.read("morning_run.fit") == fit_bytes()

    with pytest.raises(FileNotFoundError):
        importer.MockGarminObject(tmp_path / "missing.fit").download_activity(123)


def run_importer(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["fit_activity_importer.py", *args])
    runpy.run_path(str(SRC / "fit_activity_importer.py"), run_name="__main__")


def test_command_line_dry_run(gf, monkeypatch, tmp_path, capsys):
    path = tmp_path / "run.fit"
    path.write_bytes(fit_bytes())

    run_importer(monkeypatch, "--fit_file", str(path), "--dry_run")

    start, end = json.loads(capsys.readouterr().out)
    assert start["fields"]["activityName"] == "Running 2026-01-01" and end["fields"]["activityName"] == "END"
    gf.influxdbclient.write_points.assert_not_called()


def test_command_line_import_writes_summary_and_records(gf, monkeypatch, tmp_path):
    path = tmp_path / "run.fit"
    path.write_bytes(fit_bytes())

    run_importer(monkeypatch, "--fit_file", str(path))

    points = written_points(gf.influxdbclient)
    assert_valid_points(points)
    assert [p["measurement"] for p in points].count("ActivitySummary") == 2
    assert [p["fields"]["HeartRate"] for p in points if p["measurement"] == "ActivityGPS"] == [140.0, 141.0]
    assert len({p["tags"]["ActivityID"] for p in points}) == 1


def test_command_line_missing_file(gf, monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError, match="FIT file not found"):
        run_importer(monkeypatch, "--fit_file", str(tmp_path / "missing.fit"))


def test_command_line_corrupt_file(gf, monkeypatch, tmp_path):
    path = tmp_path / "corrupt.fit"
    path.write_bytes(fit_bytes()[:-2] + b"\x00\x00")

    with pytest.raises(RuntimeError, match="Failed to parse FIT file"):
        run_importer(monkeypatch, "--fit_file", str(path))
