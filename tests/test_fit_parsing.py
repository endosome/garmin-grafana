"""fetch_activity_GPS on real FIT files (built with tests/fit_builder.py) and on TCX fallbacks."""
from datetime import datetime, timedelta

import pytest

from fit_builder import build_fit
from helpers import assert_valid_points, only, zip_bytes

START = datetime(2026, 1, 1, 10, 0, 0)
SEMICIRCLES = 2 ** 31 / 180


def running_fit():
    records = [
        ("record", {"timestamp": START + timedelta(seconds=i), "position_lat": int(52.52 * SEMICIRCLES),
                    "position_long": int(13.405 * SEMICIRCLES), "enhanced_altitude": 34.4 + i,
                    "heart_rate": 140 + i, "cadence": 88, "distance": 3.1 * i, "enhanced_speed": 3.1, "power": 250,
                    "temperature": 12, "vertical_oscillation": 8.4, "stance_time": 240.0, "step_length": 1150.0,
                    140: ("uint16", 3300)})
        for i in range(3)
    ]
    return build_fit([
        ("file_id", {"type": "activity", "manufacturer": "garmin", "serial_number": 1234}),
        *records,
        ("lap", {"timestamp": START + timedelta(seconds=2), "start_time": START, "message_index": 0, "sport": "running",
                 "total_elapsed_time": 2.0, "total_distance": 6.2, "avg_power": 250}),
        ("session", {"timestamp": START + timedelta(seconds=2), "start_time": START, "sport": "running", "sub_sport": "street",
                     "total_elapsed_time": 2.0, "num_laps": 1, "message_index": 0, "total_training_effect": 3.4}),
    ])


def swim_fit():
    return build_fit([
        ("record", {"timestamp": START, "heart_rate": 120}),
        ("length", {"timestamp": START + timedelta(seconds=30), "start_time": START, "message_index": 0,
                    "total_elapsed_time": 30.0, "total_strokes": 18, "swim_stroke": "freestyle"}),
        ("length", {"timestamp": START + timedelta(seconds=62), "start_time": START + timedelta(seconds=30), "message_index": 1,
                    "total_elapsed_time": 32.0, "total_strokes": 19, "swim_stroke": "freestyle"}),
        ("lap", {"start_time": START, "message_index": 0, "sport": "swimming", "num_lengths": 2}),
        ("session", {"start_time": START, "sport": "swimming", "sub_sport": "lap_swimming", "num_laps": 1,
                     "message_index": 0, "pool_length": 25.0, 33: ("uint16", 2)}),  # field 33 is num_lengths in the FIT SDK
    ])


@pytest.fixture
def serve(gf):
    """Serves the given download for activity FIT (ORIGINAL) requests and optionally a TCX document."""
    tcx_format = gf.garmin_obj.ActivityDownloadFormat.TCX

    def configure(fit_download, tcx=None):
        def download_activity(activity_id, dl_fmt=None):
            if dl_fmt is tcx_format:
                if tcx is None:
                    raise RuntimeError("no TCX")
                return tcx.encode()
            return fit_download

        gf.garmin_obj.download_activity.side_effect = download_activity

    return configure


def test_running_records(gf, serve):
    serve(zip_bytes({"12345_ACTIVITY.fit": running_fit()}))

    points = gf.fetch_activity_GPS({12345: "running"})

    assert_valid_points(points)
    records = only(points, "ActivityGPS")
    assert len(records) == 3
    first, last = records[0], records[-1]
    assert first["time"] == "2026-01-01T10:00:00+00:00"
    assert first["tags"]["ActivitySelector"] == "20260101T100000UTC-running"
    assert first["fields"]["Latitude"] == pytest.approx(52.52, abs=1e-6)
    assert first["fields"]["Longitude"] == pytest.approx(13.405, abs=1e-6)
    assert first["fields"]["Altitude"] == pytest.approx(34.4)
    assert first["fields"]["HeartRate"] == 140.0 and isinstance(first["fields"]["HeartRate"], float)
    assert first["fields"]["Speed"] == pytest.approx(3.1)
    assert first["fields"]["GradeAdjustedSpeed"] == pytest.approx(3.3)
    assert first["fields"]["RunningEfficiency"] == pytest.approx(3.3 / 140)
    assert (first["fields"]["Cadence"], first["fields"]["Power"], first["fields"]["Temperature"]) == (88, 250, 12)
    assert first["fields"]["Stance_Time"] == pytest.approx(240.0)
    assert last["fields"]["DurationSeconds"] == 2.0
    assert last["fields"]["Distance"] == pytest.approx(6.2)


def test_running_session_and_lap(gf, serve):
    serve(zip_bytes({"a.fit": running_fit()}))

    points = gf.fetch_activity_GPS({12345: "running"})

    (session,) = only(points, "ActivitySession")
    assert session["time"] == "2026-01-01T10:00:00+00:00"
    assert session["fields"]["Sport"] == "running" and session["fields"]["Sub_Sport"] == "street"
    assert session["fields"]["Laps"] == 1 and session["fields"]["Index"] == 1
    assert session["fields"]["Aerobic_Training"] == pytest.approx(3.4)
    (lap,) = only(points, "ActivityLap")
    assert (lap["fields"]["Index"], lap["fields"]["Sport"], lap["fields"]["Avg_Power"]) == (1, "running", 250)
    assert lap["fields"]["Distance"] == pytest.approx(6.2)


def test_swim_lengths_and_laps(gf, serve):
    serve(zip_bytes({"swim.fit": swim_fit()}))

    points = gf.fetch_activity_GPS({777: "lap_swimming"})

    assert_valid_points(points)
    lengths = only(points, "ActivityLength")
    assert [(p["fields"]["Index"], p["fields"]["Strokes"], p["fields"]["Swim_Stroke"]) for p in lengths] == [
        (1, 18, "freestyle"), (2, 19, "freestyle")]
    assert lengths[1]["time"] == "2026-01-01T10:00:30+00:00"
    (lap,) = only(points, "ActivityLap")
    assert lap["fields"]["Lengths"] == 2
    (session,) = only(points, "ActivitySession")
    assert session["fields"]["Pool_Length"] == pytest.approx(25.0)
    assert session["fields"]["Sub_Sport"] == "lap_swimming"


@pytest.mark.xfail(strict=True, reason="fitparse 1.2.0's profile lacks the session num_lengths field (FIT SDK field 33, "
                   "returned as unknown_33), so ActivitySession.Lengths is always empty for real FIT files")
def test_session_lengths_from_real_fit_file(gf, serve):
    serve(zip_bytes({"swim.fit": swim_fit()}))

    (session,) = only(gf.fetch_activity_GPS({777: "lap_swimming"}), "ActivitySession")

    assert session["fields"]["Lengths"] == 2


def test_cycling_dynamics_only_when_selected(gf, serve, monkeypatch):
    fit = build_fit([
        ("record", {"timestamp": START, "power": 220, "left_torque_effectiveness": 76.0}),
        ("session", {"start_time": START, "sport": "cycling", "avg_left_torque_effectiveness": 78.0,
                     "normalized_power": 240, "left_right_balance": 0x8000 | 4900}),
    ])
    serve(zip_bytes({"ride.fit": fit}))

    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity")
    assert not only(gf.fetch_activity_GPS({9: "road_biking"}), "CyclingDynamics")

    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity,cycling_dynamics")
    (point,) = only(gf.fetch_activity_GPS({9: "road_biking"}), "CyclingDynamics")
    assert point["fields"]["avg_left_torque_effectiveness"] == 78.0
    assert point["fields"]["normalized_power"] == 240.0
    assert point["fields"]["left_right_balance"] == 51.0


def test_keep_fit_files_saves_original(gf, serve, monkeypatch, tmp_path):
    monkeypatch.setattr(gf, "KEEP_FIT_FILES", True)
    monkeypatch.setattr(gf, "FIT_FILE_STORAGE_LOCATION", str(tmp_path / "fit_store"))
    fit = running_fit()
    serve(zip_bytes({"a.fit": fit}))

    gf.fetch_activity_GPS({12345: "running"})

    assert (tmp_path / "fit_store" / "20260101T100000UTC-running.fit").read_bytes() == fit


def test_fit_files_not_kept_by_default(gf, serve, monkeypatch, tmp_path):
    monkeypatch.setattr(gf, "FIT_FILE_STORAGE_LOCATION", str(tmp_path / "fit_store"))
    serve(zip_bytes({"a.fit": running_fit()}))

    gf.fetch_activity_GPS({12345: "running"})

    assert not (tmp_path / "fit_store").exists()


TCX = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
    xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
  <Activities><Activity Sport="Running">
    <Id>2026-01-01T10:00:00.000Z</Id>
    <Lap StartTime="2026-01-01T10:00:00.000Z"><Track>
      <Trackpoint><Time>2026-01-01T10:00:00.000Z</Time>
        <Position><LatitudeDegrees>52.52</LatitudeDegrees><LongitudeDegrees>13.405</LongitudeDegrees></Position>
        <AltitudeMeters>34.4</AltitudeMeters><DistanceMeters>0.0</DistanceMeters>
        <HeartRateBpm><Value>140</Value></HeartRateBpm>
        <Extensions><ns3:TPX><ns3:Speed>3.1</ns3:Speed></ns3:TPX></Extensions>
      </Trackpoint>
    </Track></Lap>
    <Lap StartTime="2026-01-01T10:05:00.000Z"><Track>
      <Trackpoint><Time>2026-01-01T10:05:00.000Z</Time><DistanceMeters>930.5</DistanceMeters></Trackpoint>
    </Track></Lap>
  </Activity></Activities>
</TrainingCenterDatabase>"""


@pytest.mark.parametrize("fit_download", [
    zip_bytes({"notes.txt": b"no fit file here"}),
    zip_bytes({"empty.fit": build_fit([("file_id", {"type": "activity"})])}),  # no records
    zip_bytes({"corrupt.fit": running_fit()[:-2] + b"\x00\x00"}),  # bad CRC
], ids=["no-fit-in-zip", "fit-without-records", "corrupt-fit"])
def test_tcx_fallback(gf, serve, fit_download):
    serve(fit_download, tcx=TCX)

    points = gf.fetch_activity_GPS({12345: "running"})

    assert_valid_points(points)
    first, second = points
    assert first["time"] == "2026-01-01T10:00:00"  # naive UTC; InfluxDB's client treats it as UTC
    assert first["tags"]["ActivitySelector"] == "20260101T100000UTC-running"
    assert first["fields"] == {"ActivityName": "running", "Activity_ID": 12345, "Latitude": 52.52, "Longitude": 13.405,
                               "Altitude": 34.4, "Distance": 0.0, "DurationSeconds": 0.0, "HeartRate": 140.0, "Speed": 3.1, "lap": 1}
    assert (second["fields"]["lap"], second["fields"]["DurationSeconds"], second["fields"]["Latitude"]) == (2, 300.0, None)
    assert gf.PARSED_ACTIVITY_ID_LIST == [12345]


def test_keep_fit_files_saves_tcx_fallback(gf, serve, monkeypatch, tmp_path):
    monkeypatch.setattr(gf, "KEEP_FIT_FILES", True)
    monkeypatch.setattr(gf, "FIT_FILE_STORAGE_LOCATION", str(tmp_path))
    serve(zip_bytes({"notes.txt": b""}), tcx=TCX)

    gf.fetch_activity_GPS({12345: "running"})

    assert (tmp_path / "20260101T100000UTC-running.tcx").read_text() == TCX


def test_record_without_timestamp_is_skipped(gf, serve):
    fit = build_fit([("record", {"timestamp": START, "heart_rate": 120}), ("record", {"heart_rate": 121})])
    serve(zip_bytes({"a.fit": fit}))

    assert [p["fields"]["HeartRate"] for p in only(gf.fetch_activity_GPS({1: "running"}), "ActivityGPS")] == [120.0]


def test_cycling_selected_without_dynamics_data(gf, serve, monkeypatch):
    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity,cycling_dynamics")
    serve(zip_bytes({"a.fit": running_fit()}))

    assert not only(gf.fetch_activity_GPS({1: "running"}), "CyclingDynamics")


def test_tcx_trackpoint_with_missing_or_invalid_values(gf, serve):
    tcx = TCX.replace("<DistanceMeters>0.0</DistanceMeters>", "").replace("<Value>140</Value>", "<Value>n/a</Value>")
    serve(zip_bytes({"notes.txt": b""}), tcx=tcx)

    first = gf.fetch_activity_GPS({1: "running"})[0]

    assert (first["fields"]["Distance"], first["fields"]["HeartRate"], first["fields"]["Latitude"]) == (None, None, 52.52)
