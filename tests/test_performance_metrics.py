"""Training and performance metrics, blood pressure, hydration, solar intensity and lifestyle logging."""
import os
import time
from datetime import datetime

import pytest

from helpers import assert_valid_points, gmt, ms

DAY = "2026-01-01"
MIDNIGHT = "2026-01-01T00:00:00+00:00"


# --- lactate threshold -------------------------------------------------------------

def test_lactate_threshold_queries_each_sport(gf, monkeypatch):
    monkeypatch.setattr(gf, "LACTATE_THRESHOLD_SPORTS", ["RUNNING", "CYCLING"])
    responses = {
        f"/biometric-service/stats/lactateThresholdSpeed/range/{DAY}/{DAY}?aggregation=daily&sport=RUNNING": [{"value": 3.9}],
        f"/biometric-service/stats/lactateThresholdHeartRate/range/{DAY}/{DAY}?aggregation=daily&sport=RUNNING": [{"value": 168}],
        f"/biometric-service/stats/lactateThresholdSpeed/range/{DAY}/{DAY}?aggregation=daily&sport=CYCLING": [],
        f"/biometric-service/stats/lactateThresholdHeartRate/range/{DAY}/{DAY}?aggregation=daily&sport=CYCLING": [{"value": None}],
    }
    gf.garmin_obj.connectapi.side_effect = lambda endpoint: responses[endpoint]

    points = gf.get_lactate_threshold(DAY)

    assert_valid_points(points)
    assert [p["fields"] for p in points] == [{"SpeedThreshold_RUNNING": 3.9}, {"HeartRateThreshold_RUNNING": 168}]
    assert {p["measurement"] for p in points} == {"LactateThreshold"}
    assert gf.garmin_obj.connectapi.call_count == 4


@pytest.fixture
def local_timezone():
    """Sets the process's local timezone for the test; restored afterwards."""
    original = os.environ.get("TZ")

    def set_timezone(name):
        os.environ["TZ"] = name
        time.tzset()

    yield set_timezone
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


def test_lactate_threshold_timestamp_in_utc_container(gf, monkeypatch, local_timezone):
    local_timezone("UTC")
    gf.garmin_obj.connectapi.return_value = [{"value": 3.9}]
    monkeypatch.setattr(gf, "LACTATE_THRESHOLD_SPORTS", ["RUNNING"])

    assert gf.get_lactate_threshold(DAY)[0]["time"] == MIDNIGHT


def test_lactate_threshold_timestamp_is_utc_midnight_in_any_timezone(gf, monkeypatch, local_timezone):
    local_timezone("America/New_York")
    gf.garmin_obj.connectapi.return_value = [{"value": 3.9}]
    monkeypatch.setattr(gf, "LACTATE_THRESHOLD_SPORTS", ["RUNNING"])

    assert gf.get_lactate_threshold(DAY)[0]["time"] == MIDNIGHT


# --- training status / readiness ---------------------------------------------------

def test_training_status_per_device(gf):
    gf.garmin_obj.get_training_status.return_value = {"mostRecentTrainingStatus": {"latestTrainingStatusData": {
        "111": {"timestamp": ms(datetime(2026, 1, 1, 6)), "trainingStatus": 4, "weeklyTrainingLoad": 520,
                "acuteTrainingLoadDTO": {"acwrPercent": 45, "dailyAcuteChronicWorkloadRatio": 1.1}},
        "222": {"timestamp": None, "trainingStatus": 4},
        "333": {"timestamp": ms(datetime(2026, 1, 1, 6)), "acuteTrainingLoadDTO": None},
    }}}

    points = gf.get_training_status(DAY)

    assert_valid_points(points)
    (point,) = points
    assert point["measurement"] == "TrainingStatus"
    assert point["time"] == "2026-01-01T06:00:00+00:00"
    assert point["fields"]["weeklyTrainingLoad"] == 520
    assert point["fields"]["acwrPercent"] == 45
    assert point["fields"]["dailyAcuteChronicWorkloadRatio"] == 1.1


@pytest.mark.parametrize("payload", [{}, {"mostRecentTrainingStatus": None}, {"mostRecentTrainingStatus": {"latestTrainingStatusData": None}}])
def test_training_status_without_data(gf, payload):
    gf.garmin_obj.get_training_status.return_value = payload

    assert gf.get_training_status(DAY) == []


def test_training_readiness(gf):
    gf.garmin_obj.get_training_readiness.return_value = [
        {"timestamp": gmt(datetime(2026, 1, 1, 7)), "level": "HIGH", "score": 81, "hrvFactorPercent": 90},
        {"timestamp": None, "score": 70},
        {"timestamp": gmt(datetime(2026, 1, 1, 8))},
    ]

    points = gf.get_training_readiness(DAY)

    assert_valid_points(points)
    (point,) = points
    assert (point["measurement"], point["time"]) == ("TrainingReadiness", "2026-01-01T07:00:00+00:00")
    assert point["fields"]["level"] == "HIGH" and point["fields"]["score"] == 81


@pytest.mark.parametrize("payload", [[], None])
def test_training_readiness_without_data(gf, payload):
    gf.garmin_obj.get_training_readiness.return_value = payload

    assert gf.get_training_readiness(DAY) == []


# --- once-a-day scores -----------------------------------------------------------------

@pytest.mark.parametrize("getter, method, payload, measurement, expected_fields", [
    ("get_hillscore", "get_hill_score", {"overallScore": 62, "strengthScore": 50, "vo2MaxPreciseValue": 55.1},
     "HillScore", {"overallScore": 62, "strengthScore": 50, "vo2MaxPreciseValue": 55.1}),
    ("get_race_predictions", "get_race_predictions", [{"time5K": 1260, "timeMarathon": 12600}],
     "RacePredictions", {"time5K": 1260, "timeMarathon": 12600}),
    ("get_fitness_age", "get_fitnessage_data", {"chronologicalAge": 40, "fitnessAge": 33.5, "achievableFitnessAge": 31.0},
     "FitnessAge", {"chronologicalAge": 40.0, "fitnessAge": 33.5, "achievableFitnessAge": 31.0}),
    ("get_vo2_max", "get_max_metrics", [{"generic": {"vo2MaxPreciseValue": 55.1}, "cycling": {"vo2MaxPreciseValue": 58.0}}],
     "VO2_Max", {"VO2_max_value": 55.1, "VO2_max_value_cycling": 58.0}),
    ("get_vo2_max", "get_max_metrics", [{"generic": None, "cycling": {"vo2MaxPreciseValue": 58.0}}],
     "VO2_Max", {"VO2_max_value": None, "VO2_max_value_cycling": 58.0}),
    ("get_endurance_score", "get_endurance_score", {"overallScore": 7100},
     "EnduranceScore", {"EnduranceScore": 7100}),
    ("get_hydration", "get_hydration_data", {"valueInML": 2000.0, "goalInML": 2500.0},
     "Hydration", {"ValueInML": 2000.0, "GoalInML": 2500.0}),
])
def test_daily_score_point(gf, getter, method, payload, measurement, expected_fields):
    getattr(gf.garmin_obj, method).return_value = payload

    points = getattr(gf, getter)(DAY)

    assert_valid_points(points)
    (point,) = points
    assert (point["measurement"], point["time"]) == (measurement, MIDNIGHT)
    assert {k: v for k, v in point["fields"].items() if v is not None} == {k: v for k, v in expected_fields.items() if v is not None}


@pytest.mark.parametrize("getter, method, payload", [
    ("get_hillscore", "get_hill_score", None),
    ("get_hillscore", "get_hill_score", {"overallScore": None}),
    ("get_race_predictions", "get_race_predictions", []),
    ("get_race_predictions", "get_race_predictions", [{"time5K": None}]),
    ("get_fitness_age", "get_fitnessage_data", None),
    ("get_fitness_age", "get_fitnessage_data", {"chronologicalAge": None}),
    ("get_vo2_max", "get_max_metrics", []),
    ("get_vo2_max", "get_max_metrics", [{"generic": None, "cycling": None}]),
    ("get_vo2_max", "get_max_metrics", [None]),  # AttributeError is swallowed
    ("get_endurance_score", "get_endurance_score", None),
    ("get_endurance_score", "get_endurance_score", {"overallScore": None}),
    ("get_hydration", "get_hydration_data", {}),
])
def test_daily_score_without_data(gf, getter, method, payload):
    getattr(gf.garmin_obj, method).return_value = payload

    assert getattr(gf, getter)(DAY) == []


def test_race_predictions_requests_daily_range(gf):
    gf.garmin_obj.get_race_predictions.return_value = []

    gf.get_race_predictions(DAY)

    gf.garmin_obj.get_race_predictions.assert_called_once_with(startdate=DAY, enddate=DAY, _type="daily")


# --- blood pressure / solar -------------------------------------------------------------

def test_blood_pressure_measurements(gf):
    gf.garmin_obj.get_blood_pressure.return_value = {"measurementSummaries": [{"measurements": [
        {"measurementTimestampGMT": gmt(datetime(2026, 1, 1, 8)), "systolic": 118, "diastolic": 76, "pulse": 60, "sourceType": "MANUAL"},
        {"measurementTimestampGMT": gmt(datetime(2026, 1, 1, 9)), "systolic": None, "diastolic": None, "pulse": None},
        {"systolic": 120, "diastolic": 80},
    ]}]}

    points = gf.get_blood_pressure(DAY)

    assert_valid_points(points)
    (point,) = points
    assert point["measurement"] == "BloodPressure"
    assert point["time"].isoformat() == "2026-01-01T08:00:00+00:00"
    assert point["tags"]["Source"] == "MANUAL"
    assert point["fields"] == {"Systolic": 118, "Diastolic": 76, "Pulse": 60}


@pytest.mark.parametrize("payload", [{"measurementSummaries": []}, {}])
def test_blood_pressure_without_data(gf, payload):
    gf.garmin_obj.get_blood_pressure.return_value = payload

    assert gf.get_blood_pressure(DAY) == []


def test_solar_intensity_requires_device_id(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICEID", None)

    assert gf.get_solar_intensity(DAY) == []
    gf.garmin_obj.get_device_solar_data.assert_not_called()


def test_solar_intensity_readings(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICEID", 3456)
    gf.garmin_obj.get_device_solar_data.return_value = {"solarDailyDataDTOs": [{"solarInputReadings": [
        {"readingTimestampGmt": gmt(datetime(2026, 1, 1, 12)), "solarUtilization": 35.5, "activityTimeGainMs": 1200},
        {"readingTimestampGmt": gmt(datetime(2026, 1, 1, 13)), "solarUtilization": None, "activityTimeGainMs": None},
    ]}]}

    points = gf.get_solar_intensity(DAY)

    assert_valid_points(points)
    (point,) = points
    assert point["time"].isoformat() == "2026-01-01T12:00:00+00:00"
    assert point["fields"] == {"solarUtilization": 35.5, "activityTimeGainMs": 1200}
    gf.garmin_obj.get_device_solar_data.assert_called_once_with(3456, DAY)


@pytest.mark.parametrize("payload", [None, {}, {"solarDailyDataDTOs": []}])
def test_solar_intensity_without_data(gf, monkeypatch, payload):
    monkeypatch.setattr(gf, "GARMIN_DEVICEID", 3456)
    gf.garmin_obj.get_device_solar_data.return_value = payload

    assert gf.get_solar_intensity(DAY) == []


# --- lifestyle logging --------------------------------------------------------------------

def test_lifestyle_journal(gf):
    gf.garmin_obj.get_lifestyle_logging_data.return_value = {"dailyLogsReport": [
        {"name": "Alcohol", "category": "SUBSTANCES", "logStatus": "YES", "details": [{"amount": 2}, {"amount": 1.5}, {"amount": None}]},
        {"behavior": "Caffeine", "logStatus": "NO", "details": []},
        {"category": "SUBSTANCES", "logStatus": "YES"},  # no name: skipped
    ]}

    points = gf.get_lifestyle_data(DAY)

    assert_valid_points(points)
    assert [(p["tags"]["behavior"], p["tags"]["category"], p["fields"]) for p in points] == [
        ("Alcohol", "SUBSTANCES", {"status": 1, "value": 3.5}),
        ("Caffeine", "UNKNOWN", {"status": 0, "value": 0.0}),
    ]
    assert all(p["time"] == MIDNIGHT and p["measurement"] == "LifestyleJournal" for p in points)


def test_lifestyle_errors_are_swallowed(gf, caplog):
    gf.garmin_obj.get_lifestyle_logging_data.side_effect = RuntimeError("endpoint unavailable")

    assert gf.get_lifestyle_data(DAY) == []
    assert "Failed to fetch Lifestyle Journaling data" in caplog.text
