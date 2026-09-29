"""Strength training exercise sets and HR zones (get_strength_training_data, purge_existing_strength_exercise_sets)."""
from unittest import mock

import pytest
from influxdb.exceptions import InfluxDBClientError

from helpers import assert_valid_points, only

ACTIVITY = {3: {"typeKey": "strength_training", "startTimeGMT": "2026-01-01 10:00:00", "activityName": "Push day"}}


def exercise_set(set_type="ACTIVE", category="BENCH_PRESS", name="BARBELL_BENCH_PRESS", **extra):
    values = {"setType": set_type, "exercises": [{"category": category, "name": name}], "weight": 60000.0,
              "repetitionCount": 8, "duration": 45.5, "startTime": "2026-01-01T10:05:00.0", "setOrder": 1}
    values.update(extra)
    return values


@pytest.fixture
def sets(gf):
    def serve(*exercise_sets):
        gf.garmin_obj.get_activity_exercise_sets.return_value = {"exerciseSets": list(exercise_sets)}
    return serve


@pytest.fixture(autouse=True)
def hr_zones(gf):
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = [
        {"zoneNumber": 1, "secsInZone": 300.0, "zoneLowBoundary": 100},
        {"zoneNumber": 2, "secsInZone": 600.5, "zoneLowBoundary": 120},
    ]


def test_exercise_sets(gf, sets):
    sets(
        exercise_set(),
        exercise_set(set_type="REST", exercises=[]),
        exercise_set(category="SQUAT", name="", weight=None, repetitionCount=10, startTime=None, setOrder=2),
    )

    points = gf.get_strength_training_data(ACTIVITY)

    assert_valid_points(points)
    first, second = only(points, "StrengthExerciseSet")
    assert first["time"] == "2026-01-01T10:05:00+00:00"
    assert first["tags"] == {"Device": gf.GARMIN_DEVICENAME, "Database_Name": gf.INFLUXDB_DATABASE, "ActivityID": 3,
                             "ActivitySelector": "20260101T100000UTC-strength_training",
                             "ExerciseCategory": "BENCH_PRESS", "ExerciseLabel": "BENCH_PRESS/BARBELL_BENCH_PRESS"}
    assert first["fields"] == {"Activity_ID": 3, "ActivityName": "Push day", "SetOrder": 1, "SetType": "ACTIVE",
                               "Reps": 8, "Weight_kg": 60.0, "Duration_s": 45.5}
    assert second["tags"]["ExerciseLabel"] == "SQUAT"  # no exercise name: category only
    assert second["fields"]["Weight_kg"] == 0.0
    assert second["time"] == "2026-01-01T10:00:02+00:00"  # no start time: activity start + set counter (REST not counted)


def test_exercise_without_details_is_unknown(gf, sets):
    sets(exercise_set(exercises=None))

    (point,) = only(gf.get_strength_training_data(ACTIVITY), "StrengthExerciseSet")

    assert point["tags"]["ExerciseCategory"] == point["tags"]["ExerciseLabel"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["repetitionCount", "setOrder"])
def test_timed_set_without_reps_keeps_other_sets(gf, sets, field):
    sets(exercise_set(), exercise_set(category="PLANK", name="PLANK", **{field: None}))

    assert len(only(gf.get_strength_training_data(ACTIVITY), "StrengthExerciseSet")) == 2


def test_hr_zones(gf, sets):
    sets()

    zones = only(gf.get_strength_training_data(ACTIVITY), "StrengthHRZones")

    assert_valid_points(zones)
    assert [(z["time"], z["fields"]["ZoneNumber"], z["fields"]["SecsInZone"]) for z in zones] == [
        ("2026-01-01T10:00:00.001000+00:00", 1, 300.0),  # zones are 1 ms apart so they don't overwrite each other
        ("2026-01-01T10:00:00.002000+00:00", 2, 600.5),
    ]


def test_hr_zone_without_number_is_skipped_and_zone_key_is_accepted(gf, sets):
    sets()
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = [{"secsInZone": 10}, {"zone": 4, "secsInZone": 20}]

    zones = only(gf.get_strength_training_data(ACTIVITY), "StrengthHRZones")

    assert [z["fields"]["ZoneNumber"] for z in zones] == [4]


def test_hr_zone_errors_are_logged(gf, sets, caplog):
    sets(exercise_set())
    gf.garmin_obj.get_activity_hr_in_timezones.return_value = None

    points = gf.get_strength_training_data(ACTIVITY)

    assert only(points, "StrengthExerciseSet") and not only(points, "StrengthHRZones")
    assert "Failed to fetch HR zones for activity 3" in caplog.text


def test_existing_sets_are_purged_before_refresh(gf, sets):
    sets(exercise_set())

    gf.get_strength_training_data(ACTIVITY)

    gf.influxdbclient.delete_series.assert_called_once_with(measurement="StrengthExerciseSet", tags={"ActivityID": "3"})


def test_failed_set_fetch_neither_purges_nor_drops_hr_zones(gf, caplog):
    gf.garmin_obj.get_activity_exercise_sets.side_effect = RuntimeError("API error")

    points = gf.get_strength_training_data(ACTIVITY)

    gf.influxdbclient.delete_series.assert_not_called()
    assert not only(points, "StrengthExerciseSet") and only(points, "StrengthHRZones")
    assert "Failed to fetch exercise sets for activity 3" in caplog.text


def test_failed_purge_skips_sets_to_avoid_duplicates(gf, sets, caplog):
    sets(exercise_set())
    gf.influxdbclient.delete_series.side_effect = InfluxDBClientError("delete failed")

    points = gf.get_strength_training_data(ACTIVITY)

    assert not only(points, "StrengthExerciseSet") and only(points, "StrengthHRZones")
    assert "stale rows could not be purged" in caplog.text


def test_influxdb_v3_writes_sets_without_purging(gf, sets, monkeypatch):
    monkeypatch.setattr(gf, "INFLUXDB_VERSION", "3")
    sets(exercise_set())

    points = gf.get_strength_training_data(ACTIVITY)

    gf.influxdbclient.delete_series.assert_not_called()
    assert only(points, "StrengthExerciseSet")


def test_client_without_delete_series_writes_sets(gf, sets, monkeypatch):
    monkeypatch.setattr(gf, "influxdbclient", mock.MagicMock(spec=["write_points"]))
    sets(exercise_set())

    assert gf.purge_existing_strength_exercise_sets(3) is True
    assert only(gf.get_strength_training_data(ACTIVITY), "StrengthExerciseSet")


def test_multiple_activities(gf, sets):
    sets(exercise_set())
    activities = {**ACTIVITY, 4: {"typeKey": "strength_training", "startTimeGMT": "2026-01-02 18:00:00", "activityName": None}}

    points = gf.get_strength_training_data(activities)

    assert {p["tags"]["ActivityID"] for p in points} == {3, 4}
    assert gf.influxdbclient.delete_series.call_count == 2
