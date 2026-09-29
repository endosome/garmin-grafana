"""garmin_bulk_importer: reading a Garmin account data export and importing it through garmin_fetch."""
import io
import json
import runpy
import socket
import sys
import zipfile
from datetime import datetime, timedelta

import pytest

import garmin_bulk_importer as bulk
from fit_builder import build_fit
from helpers import SRC, assert_valid_points, ms, written_points, zip_bytes

RUN_START = datetime(2026, 1, 2, 7, 0, 0)


def fit_for(start, sport="running"):
    return build_fit([
        ("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": 1}),
        ("record", {"timestamp": start, "heart_rate": 130, "position_lat": 626349397, "position_long": 159932488}),
        ("record", {"timestamp": start + timedelta(seconds=60), "heart_rate": 140}),
        ("session", {"timestamp": start + timedelta(seconds=60), "start_time": start, "sport": sport, "num_laps": 1, "message_index": 0}),
    ])


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def export_dir(tmp_path):
    """A minimal Garmin data export: one run, two days of wellness data, one uploaded FIT file."""
    root = tmp_path / "export" / "DI_CONNECT"
    write_json(root / "DI-Connect-Fitness" / "me_0_summarizedActivities.json", [{"summarizedActivitiesExport": [
        {"activityId": 22, "activityType": "running", "name": "Morning Run", "startTimeGmt": ms(RUN_START) + 0.0,
         "duration": 60000.0, "avgSpeed": 3.1, "maxHr": 150, "avgHr": 135},
        {"activityId": 11, "activityType": "walking", "startTimeGmt": ms(datetime(2026, 1, 1, 18, 0)) + 0.0, "duration": 1000.0},
    ]}])
    write_json(root / "DI-Connect-Wellness" / "12345_sleepData.json", [
        {"calendarDate": "2026-01-02", "sleepEndTimestampGMT": "2026-01-02T06:30:00.0", "deepSleepSeconds": 3600,
         "lightSleepSeconds": 14400, "awakeSleepSeconds": 600, "remSleepSeconds": 5400},
        {"sleepEndTimestampGMT": "2026-01-01T06:30:00.0"},  # no calendarDate: ignored
    ])
    write_json(root / "DI-Connect-Aggregator" / "UDSFile_2026-01-01_2026-03-31.json", [
        {"calendarDate": "2026-01-01", "wellnessStartTimeGmt": "2026-01-01T00:00:00.0", "totalSteps": 8000},
        {"calendarDate": "2026-01-02", "wellnessStartTimeGmt": "2026-01-02T00:00:00.0", "totalSteps": 12000},
        {"calendarDate": "2026-01-03", "includesWellnessData": False, "wellnessStartTimeGmt": "2026-01-03T00:00:00.0"},
        {"hydration": {"calendarDate": "2026-01-02", "valueInML": 1500.0, "goalInML": 2500.0}},
    ])
    uploads = root / "DI-Connect-Uploaded-Files"
    uploads.mkdir(parents=True)
    (uploads / "UploadedFiles_0-_Part1.zip").write_bytes(zip_bytes({
        "me_22.fit": fit_for(RUN_START + timedelta(seconds=40)),  # within the 5 minute matching window
        "me_other.fit": fit_for(RUN_START + timedelta(hours=3), sport="cycling"),
        "notes.txt": b"not a fit file",
    }))
    return tmp_path / "export"


def test_activities_are_converted_to_api_format(export_dir):
    export = bulk.GarminBulkExport(export_dir)

    first, second = export.activities  # sorted by start time
    assert first["activityId"] == 11 and first["activityName"] == "walking"  # falls back to the type without a name
    assert second == {**second, "startTimeGMT": "2026-01-02 07:00:00", "activityName": "Morning Run",
                      "activityType": {"typeKey": "running"}, "averageSpeed": 3.1, "maxHR": 150, "averageHR": 135}
    assert export.get_last_activity()["activityId"] == 22
    assert second["duration"] == 60.0


def test_iso_timestamp_preserves_explicit_timezone():
    assert bulk.iso_to_timestamp_ms("2026-01-02T08:30:00+02:00") == ms(datetime(2026, 1, 2, 6, 30))


def test_wellness_data(export_dir):
    export = bulk.GarminBulkExport(export_dir)

    assert export.get_sleep_data("2026-01-02")["dailySleepDTO"]["sleepEndTimestampGMT"] == ms(datetime(2026, 1, 2, 6, 30))
    assert export.get_sleep_data("2026-01-05") == {"dailySleepDTO": {"sleepEndTimestampGMT": None}}
    assert export.get_stats("2026-01-02")["totalSteps"] == 12000
    assert export.get_stats("2026-01-02")["sleepingSeconds"] == 3600 + 14400 + 600  # derived from the sleep data
    assert export.get_stats("2026-01-01")["sleepingSeconds"] is None
    assert export.get_stats("2026-01-03") == {"wellnessStartTimeGmt": None}  # includesWellnessData false
    assert export.get_stats("2026-01-09") == {"wellnessStartTimeGmt": None}
    assert export.get_hydration_data("2026-01-02")["valueInML"] == 1500.0
    assert export.get_hydration_data("2026-01-01") == {}


def test_activities_by_date_cover_the_whole_day(export_dir):
    export = bulk.GarminBulkExport(export_dir)

    assert [a["activityId"] for a in export.get_activities_by_date("2026-01-01", "2026-01-01")] == [11]
    assert [a["activityId"] for a in export.get_activities_by_date("2026-01-01", "2026-01-02")] == [11, 22]
    assert export.get_activities_by_date("2026-01-03", "2026-01-03") == []


def test_device_is_named_after_the_host(export_dir):
    device = bulk.GarminBulkExport(export_dir).get_device_last_used()

    assert device == {"lastUsedDeviceName": socket.gethostname(), "userDeviceId": None, "imageUrl": None, "lastUsedDeviceUploadTime": 0}


def test_download_matches_nearest_fit_file(export_dir):
    export = bulk.GarminBulkExport(export_dir)

    archive = export.download_activity(22)

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.namelist() == ["me_22.fit"]
        assert zf.read("me_22.fit") == fit_for(RUN_START + timedelta(seconds=40))
    assert export.download_activity(22, dl_fmt=bulk.ActivityDownloadFormatEnum.TCX) == b""


def test_nearest_of_several_candidates_wins(export_dir):
    # One archive, so the index order is fixed: the closer file is seen first and must not be replaced.
    (export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files" / "UploadedFiles_0-_Part1.zip").write_bytes(zip_bytes({
        "me_22.fit": fit_for(RUN_START + timedelta(seconds=40)),
        "farther.fit": fit_for(RUN_START + timedelta(seconds=200)),
        "me_other.fit": fit_for(RUN_START + timedelta(hours=3), sport="cycling"),
        "no_session.fit": build_fit([("record", {"timestamp": RUN_START, "heart_rate": 99})]),  # not an activity: not indexed
    }))
    export = bulk.GarminBulkExport(export_dir)

    assert len(export.fit_file_index) == 3
    with zipfile.ZipFile(io.BytesIO(export.download_activity(22))) as zf:
        assert zf.namelist() == ["me_22.fit"]


def test_download_ignores_closer_fit_from_another_sport(export_dir):
    archive = export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files" / "UploadedFiles_0-_Part1.zip"
    archive.write_bytes(zip_bytes({
        "cycling.fit": fit_for(RUN_START, sport="cycling"),
        "running.fit": fit_for(RUN_START + timedelta(seconds=40)),
    }))

    with zipfile.ZipFile(io.BytesIO(bulk.GarminBulkExport(export_dir).download_activity(22))) as zf:
        assert zf.namelist() == ["running.fit"]


@pytest.mark.parametrize("sport, type_key", [("swimming", "lap_swimming"), ("running", "trail_running"), ("training", "strength_training")])
def test_download_matches_fine_grained_type_key(export_dir, sport, type_key):
    archive = export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files" / "UploadedFiles_0-_Part1.zip"
    archive.write_bytes(zip_bytes({"other.fit": fit_for(RUN_START + timedelta(seconds=10), sport="cycling"),
                                   "match.fit": fit_for(RUN_START + timedelta(seconds=40), sport=sport)}))
    export = bulk.GarminBulkExport(export_dir)
    next(a for a in export.activities if a["activityId"] == 22)["activityType"]["typeKey"] = type_key

    with zipfile.ZipFile(io.BytesIO(export.download_activity(22))) as zf:
        assert zf.namelist() == ["match.fit"]


def test_download_falls_back_to_nearest_when_no_sport_matches(export_dir):
    archive = export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files" / "UploadedFiles_0-_Part1.zip"
    archive.write_bytes(zip_bytes({"cycling.fit": fit_for(RUN_START, sport="cycling")}))

    with zipfile.ZipFile(io.BytesIO(bulk.GarminBulkExport(export_dir).download_activity(22))) as zf:
        assert zf.namelist() == ["cycling.fit"]


def test_download_errors(export_dir):
    export = bulk.GarminBulkExport(export_dir)

    with pytest.raises(bulk.GarminBulkImporterError, match="Activity ID not found"):
        export.download_activity(999)
    with pytest.raises(bulk.GarminBulkImporterError, match="No matching FIT file"):
        export.download_activity(11)  # no FIT file within 5 minutes of the walk


def test_fit_index_is_cached(export_dir):
    bulk.GarminBulkExport(export_dir)
    cache = export_dir / bulk.CACHED_FIT_FILE_INDEX_FILENAME
    assert [e["activity"] for e in json.loads(cache.read_text())] == ["running", "cycling"]

    for archive in export_dir.rglob("*.zip"):  # the cached index is used instead of rescanning
        archive.rename(archive.with_suffix(".bak"))
    assert len(bulk.GarminBulkExport(export_dir).fit_file_index) == 2


def test_corrupt_fit_file_fails_indexing(export_dir):
    (export_dir / "DI_CONNECT" / "DI-Connect-Uploaded-Files" / "UploadedFiles_0-_Part2.zip").write_bytes(
        zip_bytes({"broken.fit": fit_for(RUN_START)[:-2] + b"\x00\x00"}))

    with pytest.raises(RuntimeError, match="Failed to parse FIT file"):
        bulk.GarminBulkExport(export_dir)


@pytest.mark.parametrize("remove, message", [
    ("DI-Connect-Wellness", "sleep stats"),
    ("DI-Connect-Aggregator", "aggregated stats"),
])
def test_required_files_missing(export_dir, remove, message):
    for path in (export_dir / "DI_CONNECT" / remove).iterdir():
        path.unlink()

    with pytest.raises(bulk.GarminBulkImporterError, match=message):
        bulk.GarminBulkExport(export_dir)


def test_export_without_activities(export_dir):
    (export_dir / "DI_CONNECT" / "DI-Connect-Fitness" / "me_0_summarizedActivities.json").unlink()

    assert bulk.GarminBulkExport(export_dir).get_activities_by_date("2026-01-01", "2026-01-02") == []


def test_missing_export_directory(tmp_path):
    with pytest.raises(bulk.GarminBulkImporterError, match="does not exist"):
        bulk.GarminBulkExport(tmp_path / "nope")


@pytest.mark.parametrize("folder, filename, entry", [
    ("DI-Connect-Wellness", "extra_sleepData.json", {"calendarDate": "2026-01-02", "sleepEndTimestampGMT": "2026-01-02T06:30:00.0"}),
    ("DI-Connect-Aggregator", "UDSFile_extra.json", {"calendarDate": "2026-01-01"}),
])
def test_duplicate_dates_are_rejected(export_dir, folder, filename, entry):
    write_json(export_dir / "DI_CONNECT" / folder / filename, [entry])

    with pytest.raises(bulk.GarminBulkImporterError, match="Duplicate entries"):
        bulk.GarminBulkExport(export_dir)


# --- importing through garmin_fetch -------------------------------------------------------------

@pytest.fixture
def importer_globals(gf, monkeypatch):
    """The importer overrides garmin_fetch settings; make sure they are restored afterwards."""
    for name in ["FETCH_SELECTION", "UPDATE_INTERVAL_SECONDS", "RATE_LIMIT_CALLS_SECONDS", "ALWAYS_PROCESS_FIT_FILES", "IGNORE_ERRORS"]:
        monkeypatch.setattr(gf, name, getattr(gf, name))
    return gf


def test_import_wellness_data(importer_globals, export_dir, monkeypatch):
    gf = importer_globals
    monkeypatch.setattr(gf, "garmin_obj", bulk.GarminBulkExport(export_dir))
    monkeypatch.setattr(gf, "FETCH_SELECTION", "daily_avg,sleep,hydration")

    assert gf.fetch_write_bulk("2026-01-01", "2026-01-03") is True

    points = written_points(gf.influxdbclient)
    assert_valid_points(points)
    assert sorted((p["measurement"], p["time"]) for p in points if p["measurement"] != "DeviceSync") == [
        ("DailyStats", "2026-01-01T00:00:00+00:00"),
        ("DailyStats", "2026-01-02T00:00:00+00:00"),
        ("Hydration", "2026-01-02T00:00:00+00:00"),
        ("SleepSummary", "2026-01-02T06:30:00+00:00"),
    ]


def test_import_activities(importer_globals, export_dir, monkeypatch):
    gf = importer_globals
    monkeypatch.setattr(gf, "garmin_obj", bulk.GarminBulkExport(export_dir))
    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity")
    monkeypatch.setattr(gf, "ALWAYS_PROCESS_FIT_FILES", True)

    assert gf.fetch_write_bulk("2026-01-02", "2026-01-02") is True

    measurements = {p["measurement"] for p in written_points(gf.influxdbclient)}
    assert {"ActivitySummary", "ActivityGPS", "ActivitySession"} <= measurements
    summary = next(p for p in written_points(gf.influxdbclient)
                   if p["measurement"] == "ActivitySummary" and p["fields"].get("activityName") == "Morning Run")
    assert summary["fields"]["elapsedDuration"] == 60.0


def test_bulk_import_keeps_existing_strength_sets(importer_globals, export_dir, monkeypatch):
    gf = importer_globals
    monkeypatch.setattr(gf, "garmin_obj", bulk.GarminBulkExport(export_dir))

    points = gf.get_strength_training_data({22: {
        "typeKey": "strength_training", "startTimeGMT": "2026-01-02 07:00:00", "activityName": "Workout"
    }})

    assert points == []
    gf.influxdbclient.delete_series.assert_not_called()


def run_importer(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["garmin_bulk_importer.py", *args])
    runpy.run_path(str(SRC / "garmin_bulk_importer.py"), run_name="__main__")


def test_command_line_import(importer_globals, export_dir, monkeypatch):
    gf = importer_globals
    (export_dir / "DI_CONNECT" / "DI-Connect-Fitness" / "me_0_summarizedActivities.json").unlink()  # see test_import_activities

    run_importer(monkeypatch, "--bulk_data_path", str(export_dir), "--start_date", "2026-01-01", "--end_date", "2026-01-02")

    assert type(gf.garmin_obj).__name__ == "GarminBulkExport"  # the script's own copy of the class
    assert gf.FETCH_SELECTION == "daily_avg,sleep,activity,hydration"
    assert (gf.RATE_LIMIT_CALLS_SECONDS, gf.ALWAYS_PROCESS_FIT_FILES, gf.IGNORE_ERRORS) == (0, True, False)
    measurements = {p["measurement"] for p in written_points(gf.influxdbclient)}
    assert {"DailyStats", "SleepSummary", "Hydration"} <= measurements


def test_command_line_start_date_from_environment(importer_globals, export_dir, monkeypatch):
    (export_dir / "DI_CONNECT" / "DI-Connect-Fitness" / "me_0_summarizedActivities.json").unlink()
    monkeypatch.setenv("MANUAL_START_DATE", "2026-01-02")

    run_importer(monkeypatch, "--bulk_data_path", str(export_dir), "--end_date", "2026-01-02", "--ignore_errors")

    assert importer_globals.IGNORE_ERRORS is True
    dates = {p["time"][:10] for p in written_points(importer_globals.influxdbclient) if p["measurement"] == "DailyStats"}
    assert dates == {"2026-01-02"}


def test_command_line_requires_start_date(importer_globals, export_dir, monkeypatch):
    monkeypatch.delenv("MANUAL_START_DATE", raising=False)

    with pytest.raises(RuntimeError, match="start_date must be set"):
        run_importer(monkeypatch, "--bulk_data_path", str(export_dir))
