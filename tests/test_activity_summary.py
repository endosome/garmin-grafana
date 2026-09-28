import pytest

from helpers import assert_valid_points


@pytest.mark.parametrize("durations, expected_end", [
    ({"elapsedDuration": 30.0, "duration": 60.0}, "2026-01-01T10:00:30+00:00"),
    ({"elapsedDuration": 30.9, "duration": 60.0}, "2026-01-01T10:00:30+00:00"),  # truncated to whole seconds
    ({"elapsedDuration": None, "duration": 60.0}, "2026-01-01T10:01:00+00:00"),
    ({"elapsedDuration": 0, "duration": 60.0}, "2026-01-01T10:01:00+00:00"),  # same fallback as the elapsedDuration field
    ({"duration": 60.0}, "2026-01-01T10:01:00+00:00"),
    ({"elapsedDuration": None, "duration": None}, "2026-01-01T10:00:00+00:00"),
    ({}, "2026-01-01T10:00:00+00:00"),
], ids=["elapsed", "elapsed-fractional", "elapsed-none", "elapsed-zero", "elapsed-missing", "both-none", "both-missing"])
def test_activity_end_point_handles_missing_durations(gf, durations, expected_end):
    gf.garmin_obj.get_activities_by_date.return_value = [{
        "activityId": 9,
        "startTimeGMT": "2026-01-01 10:00:00",
        "activityType": {"typeKey": "running"},
        **durations,
    }]
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = []

    points, _, _ = gf.get_activity_summary("2026-01-01")

    start_point, end_point = points
    assert start_point["time"] == "2026-01-01T10:00:00+00:00"
    assert end_point["fields"]["activityName"] == "END"
    assert end_point["time"] == expected_end


def activity(activity_id=1, type_key="running", **extra):
    return {"activityId": activity_id, "startTimeGMT": "2026-01-01 10:00:00", "activityType": {"typeKey": type_key},
            "elapsedDuration": 1800.0, **extra}


def test_summary_fields_and_tags(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICENAME", "Forerunner 965")
    gf.garmin_obj.get_activities_by_date.return_value = [activity(
        activityName="Morning Run", distance=5000.0, averageHR=150.0, deviceId=99, hrTimeInZone_1=120.7, hrTimeInZone_3=None,
        aerobicTrainingEffect=3.2, description=None)]
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = [
        {"zoneNumber": 1, "zoneLowBoundary": 100}, {"zoneNumber": 3, "zoneLowBoundary": 140}]

    points, gps_ids, strength_ids = gf.get_activity_summary("2026-01-01")

    assert_valid_points(points)
    start_point, end_point = points
    assert start_point["tags"] == {"Device": "Forerunner 965", "Database_Name": gf.INFLUXDB_DATABASE, "ActivityID": 1,
                                   "ActivitySelector": "20260101T100000UTC-running"}
    fields = start_point["fields"]
    assert (fields["activityName"], fields["activityType"], fields["distance"], fields["Device_ID"]) == ("Morning Run", "running", 5000.0, 99)
    assert (fields["hrTimeInZone_1"], fields["hrTimeInZone_3"]) == (120, None)
    assert [fields[f"hrZoneLowBoundary_{i}"] for i in range(1, 6)] == [100, None, 140, None, None]
    assert end_point["tags"] == start_point["tags"]
    assert end_point["fields"] == {"Activity_ID": 1, "Device_ID": 99, "activityName": "END", "activityType": "No Activity"}
    assert (gps_ids, strength_ids) == ({}, {})


def test_activities_with_gps_are_queued_for_detail_fetch(gf, monkeypatch):
    monkeypatch.setattr(gf, "ALWAYS_PROCESS_FIT_FILES", False)
    gf.garmin_obj.get_activities_by_date.return_value = [
        activity(1, "running", hasPolyline=True), activity(2, "indoor_cycling", hasPolyline=False)]

    _, gps_ids, _ = gf.get_activity_summary("2026-01-01")

    assert gps_ids == {1: "running"}


def test_always_process_fit_files_queues_activities_without_gps(gf, monkeypatch):
    monkeypatch.setattr(gf, "ALWAYS_PROCESS_FIT_FILES", True)
    gf.garmin_obj.get_activities_by_date.return_value = [activity(2, "indoor_cycling", hasPolyline=False)]

    _, gps_ids, _ = gf.get_activity_summary("2026-01-01")

    assert gps_ids == {2: "indoor_cycling"}


def test_strength_activities_are_collected(gf):
    gf.garmin_obj.get_activities_by_date.return_value = [
        activity(3, "strength_training", activityName="Push day"), activity(4, "running")]

    _, _, strength_ids = gf.get_activity_summary("2026-01-01")

    assert strength_ids == {3: {"typeKey": "strength_training", "startTimeGMT": "2026-01-01 10:00:00", "activityName": "Push day"}}


@pytest.mark.parametrize("type_filter, expected_ids", [
    ([], [1, 2, 3]),
    (["running"], [1]),
    (["running", "strength_training"], [1, 3]),
    (["hiking"], []),
])
def test_activity_type_filter(gf, monkeypatch, type_filter, expected_ids):
    monkeypatch.setattr(gf, "ACTIVITY_TYPE_FILTER", type_filter)
    gf.garmin_obj.get_activities_by_date.return_value = [
        activity(1, "Running"), activity(2, "cycling"), activity(3, "strength_training")]

    points, _, _ = gf.get_activity_summary("2026-01-01")

    assert sorted({p["tags"]["ActivityID"] for p in points}) == expected_ids


def test_activity_without_start_time_is_skipped(gf):
    gf.garmin_obj.get_activities_by_date.return_value = [{"activityId": 5, "activityType": {"typeKey": "running"}}]

    points, _, _ = gf.get_activity_summary("2026-01-01")

    assert points == []
    gf.garmin_obj.get_activity_hr_in_timezones.assert_not_called()


def test_activity_without_type_is_unknown(gf):
    gf.garmin_obj.get_activities_by_date.return_value = [{"activityId": 6, "startTimeGMT": "2026-01-01 10:00:00", "activityType": None}]
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = None

    points, _, _ = gf.get_activity_summary("2026-01-01")

    assert points[0]["tags"]["ActivitySelector"] == "20260101T100000UTC-Unknown"
    assert points[0]["fields"]["activityType"] is None
