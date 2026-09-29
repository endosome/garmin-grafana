"""Daily and intraday health metrics: stats, device sync, sleep, HR, steps, stress, breathing, HRV, weight."""
from datetime import datetime, timedelta

import pytest

from helpers import assert_valid_points, gmt, ms, only

DAY = "2026-01-01"
T0 = datetime(2026, 1, 1, 0, 0)


@pytest.fixture(autouse=True)
def device(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICENAME", "Forerunner 965")
    monkeypatch.setattr(gf, "INFLUXDB_DATABASE", "GarminStats")


def assert_standard_tags(points):
    for point in points:
        assert point["tags"]["Device"] == "Forerunner 965"
        assert point["tags"]["Database_Name"] == "GarminStats"


# --- daily stats -------------------------------------------------------------

def test_daily_stats_point(gf):
    gf.garmin_obj.get_stats.return_value = {
        "wellnessStartTimeGmt": "2025-12-31T23:00:00.0",
        "totalSteps": 12345, "restingHeartRate": 48, "averageSpo2": 96.0,
        "bodyBatteryHighestValue": 90, "stressPercentage": 21.5, "unrelated": "ignored",
    }

    points = gf.get_daily_stats(DAY)

    assert_valid_points(points)
    assert_standard_tags(points)
    (point,) = points
    assert point["measurement"] == "DailyStats"
    assert point["time"] == "2025-12-31T23:00:00+00:00"
    assert point["fields"]["totalSteps"] == 12345
    assert point["fields"]["restingHeartRate"] == 48
    assert point["fields"]["floorsAscended"] is None
    assert "unrelated" not in point["fields"]
    assert len(point["fields"]) == 44


def test_daily_stats_without_wellness_data(gf):
    gf.garmin_obj.get_stats.return_value = {"wellnessStartTimeGmt": None, "totalSteps": 0}

    assert gf.get_daily_stats(DAY) == []


def test_daily_stats_skips_future_dates(gf):
    tomorrow = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    gf.garmin_obj.get_stats.return_value = {"wellnessStartTimeGmt": f"{tomorrow}T00:00:00.0"}

    assert gf.get_daily_stats(tomorrow) == []


def test_daily_stats_tolerates_missing_wellness_key(gf):
    gf.garmin_obj.get_stats.return_value = {}

    assert gf.get_daily_stats(DAY) == []


# --- device sync -------------------------------------------------------------

def test_last_sync_detects_device_automatically(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICENAME_AUTOMATIC", True)
    gf.garmin_obj.get_device_last_used.return_value = {
        "lastUsedDeviceName": "fenix 8", "userDeviceId": 3456, "imageUrl": "https://img", "lastUsedDeviceUploadTime": ms(T0),
    }

    points = gf.get_last_sync()

    assert (gf.GARMIN_DEVICENAME, gf.GARMIN_DEVICEID) == ("fenix 8", 3456)
    assert_valid_points(points)
    (point,) = points
    assert point["measurement"] == "DeviceSync"
    assert point["time"] == "2026-01-01T00:00:00+00:00"
    assert point["tags"]["Device"] == "fenix 8"
    assert point["fields"] == {"imageUrl": "https://img", "Device_Name": "fenix 8"}


def test_last_sync_defaults_when_device_unknown(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICENAME_AUTOMATIC", True)
    gf.garmin_obj.get_device_last_used.return_value = {"lastUsedDeviceName": None, "lastUsedDeviceUploadTime": ms(T0)}

    gf.get_last_sync()

    assert (gf.GARMIN_DEVICENAME, gf.GARMIN_DEVICEID) == ("Unknown", None)


def test_last_sync_without_upload_time_writes_nothing(gf):
    gf.garmin_obj.get_device_last_used.return_value = {"lastUsedDeviceName": "fenix 8", "lastUsedDeviceUploadTime": None}

    assert gf.get_last_sync() == []


def test_last_sync_keeps_configured_device_name(gf, monkeypatch):
    monkeypatch.setattr(gf, "GARMIN_DEVICENAME_AUTOMATIC", False)
    gf.garmin_obj.get_device_last_used.return_value = {"lastUsedDeviceName": "fenix 8", "lastUsedDeviceUploadTime": ms(T0)}

    (point,) = gf.get_last_sync()

    assert gf.GARMIN_DEVICENAME == "Forerunner 965"
    assert point["tags"]["Device"] == "Forerunner 965"


# --- sleep ---------------------------------------------------------------------

def sleep_payload(**overrides):
    start = datetime(2025, 12, 31, 23, 0)
    payload = {
        "dailySleepDTO": {
            "sleepEndTimestampGMT": ms(datetime(2026, 1, 1, 7, 0)),
            "sleepTimeSeconds": 28800, "deepSleepSeconds": 7200, "remSleepSeconds": 5400,
            "sleepScores": {"overall": {"value": 84}},
        },
        "restlessMomentsCount": 12, "avgOvernightHrv": 55.0, "restingHeartRate": 47, "bodyBatteryChange": 60,
        "sleepMovement": [{"startGMT": gmt(start), "endGMT": gmt(start + timedelta(minutes=1)), "activityLevel": 2.5}],
        "sleepLevels": [
            {"startGMT": gmt(start), "endGMT": gmt(start + timedelta(minutes=30)), "activityLevel": 1.0},
            {"startGMT": gmt(start + timedelta(minutes=30)), "endGMT": gmt(start + timedelta(minutes=90)), "activityLevel": 0.0},
        ],
        "sleepRestlessMoments": [{"startGMT": ms(start), "value": 1}, {"startGMT": ms(start), "value": 0}],
        "wellnessEpochSPO2DataDTOList": [{"epochTimestamp": gmt(start), "spo2Reading": 95}, {"epochTimestamp": gmt(start), "spo2Reading": None}],
        "wellnessEpochRespirationDataDTOList": [{"startTimeGMT": ms(start), "respirationValue": 14.0}],
        "sleepHeartRate": [{"startGMT": ms(start), "value": 52}],
        "sleepStress": [{"startGMT": ms(start), "value": 12.0}],
        "sleepBodyBattery": [{"startGMT": ms(start), "value": 40}],
        "hrvData": [{"startGMT": ms(start), "value": 61.0}],
    }
    payload.update(overrides)
    return payload


def test_sleep_summary_and_intraday_series(gf):
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload()

    points = gf.get_sleep_data(DAY)

    assert_valid_points(points)
    assert_standard_tags(points)
    (summary,) = only(points, "SleepSummary")
    assert summary["time"] == "2026-01-01T07:00:00+00:00"
    assert summary["fields"]["sleepScore"] == 84
    assert summary["fields"]["deepSleepSeconds"] == 7200
    assert summary["fields"]["avgOvernightHrv"] == 55.0
    assert summary["fields"]["restlessMomentsCount"] == 12

    intraday = only(points, "SleepIntraday")
    fields = lambda key: [p["fields"][key] for p in intraday if key in p["fields"]]
    assert fields("SleepMovementActivityLevel") == [2.5]
    assert fields("SleepMovementActivitySeconds") == [60]
    assert fields("SleepStageLevel") == [1.0, 0.0, 0.0]  # deep sleep (0) kept, plus the terminal duplicate
    assert fields("SleepStageSeconds") == [1800, 3600]
    assert fields("sleepRestlessValue") == [1]
    assert fields("spo2Reading") == [95]
    assert fields("respirationValue") == [14.0]
    assert fields("heartRate") == [52]
    assert fields("stressValue") == [12.0]
    assert fields("bodyBattery") == [40]
    assert fields("hrvData") == [61.0]


def test_sleep_stage_terminal_point_marks_end_of_last_stage(gf):
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload()

    stages = [p for p in gf.get_sleep_data(DAY) if "SleepStageLevel" in p["fields"]]

    assert stages[-1]["time"] == "2026-01-01T00:30:00+00:00"
    assert stages[-1]["fields"] == {"SleepStageLevel": 0.0}


def test_sleep_stage_with_unknown_level_is_skipped(gf):
    start = datetime(2025, 12, 31, 23, 0)
    levels = [
        {"startGMT": gmt(start), "endGMT": gmt(start + timedelta(minutes=30)), "activityLevel": None},
        {"startGMT": gmt(start + timedelta(minutes=30)), "endGMT": gmt(start + timedelta(minutes=60)), "activityLevel": 2.0},
    ]
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload(sleepLevels=levels)

    stages = [p["fields"]["SleepStageLevel"] for p in gf.get_sleep_data(DAY) if "SleepStageLevel" in p["fields"]]

    assert stages == [2.0, 2.0]


def test_sleep_terminal_point_is_valid_when_last_level_unknown(gf):
    start = datetime(2025, 12, 31, 23, 0)
    levels = [
        {"startGMT": gmt(start), "endGMT": gmt(start + timedelta(minutes=30)), "activityLevel": 2.0},
        {"startGMT": gmt(start + timedelta(minutes=30)), "endGMT": gmt(start + timedelta(minutes=60)), "activityLevel": None},
    ]
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload(sleepLevels=levels)

    assert_valid_points(gf.get_sleep_data(DAY))


def test_no_sleep_recorded(gf):
    gf.garmin_obj.get_sleep_data.return_value = {"dailySleepDTO": {"sleepEndTimestampGMT": None}}

    assert gf.get_sleep_data(DAY) == []


def test_sleep_tolerates_missing_daily_sleep_dto(gf):
    gf.garmin_obj.get_sleep_data.return_value = {}

    assert gf.get_sleep_data(DAY) == []


# --- intraday series -------------------------------------------------------------

def test_intraday_heart_rate_skips_empty_readings(gf):
    gf.garmin_obj.get_heart_rates.return_value = {"heartRateValues": [[ms(T0), 60], [ms(T0) + 120000, None], [ms(T0) + 240000, 72]]}

    points = gf.get_intraday_hr(DAY)

    assert_valid_points(points)
    assert [(p["time"], p["fields"]["HeartRate"]) for p in points] == [
        ("2026-01-01T00:00:00+00:00", 60), ("2026-01-01T00:04:00+00:00", 72)]
    assert {p["measurement"] for p in points} == {"HeartRateIntraday"}


@pytest.mark.parametrize("payload", [{"heartRateValues": None}, {}])
def test_intraday_heart_rate_without_data(gf, payload):
    gf.garmin_obj.get_heart_rates.return_value = payload

    assert gf.get_intraday_hr(DAY) == []


def test_intraday_steps_keep_zero(gf):
    gf.garmin_obj.get_steps_data.return_value = [
        {"startGMT": gmt(T0), "steps": 0}, {"startGMT": gmt(T0 + timedelta(minutes=15)), "steps": 250},
        {"startGMT": gmt(T0 + timedelta(minutes=30)), "steps": None},
    ]

    points = gf.get_intraday_steps(DAY)

    assert_valid_points(points)
    assert [p["fields"]["StepsCount"] for p in points] == [0, 250]
    assert points[1]["time"] == "2026-01-01T00:15:00+00:00"


def test_intraday_stress_and_body_battery(gf):
    gf.garmin_obj.get_stress_data.return_value = {
        "stressValuesArray": [[ms(T0), 25], [ms(T0) + 180000, 0], [ms(T0) + 360000, None]],
        "bodyBatteryValuesArray": [[ms(T0), "MEASURED", 80, 2.0], [ms(T0) + 180000, "MEASURED", 0, 2.0], [ms(T0) + 360000, "MEASURED", None, 2.0]],
    }

    points = gf.get_intraday_stress(DAY)

    assert_valid_points(points)
    assert [p["fields"]["stressLevel"] for p in only(points, "StressIntraday")] == [25, 0]
    assert [p["fields"]["BodyBatteryLevel"] for p in only(points, "BodyBatteryIntraday")] == [80, 0]


def test_intraday_stress_without_data(gf):
    gf.garmin_obj.get_stress_data.return_value = {"stressValuesArray": None, "bodyBatteryValuesArray": None}

    assert gf.get_intraday_stress(DAY) == []


def test_intraday_breathing_rate(gf):
    gf.garmin_obj.get_respiration_data.return_value = {"respirationValuesArray": [[ms(T0), 13.5], [ms(T0) + 120000, None]]}

    points = gf.get_intraday_br(DAY)

    assert_valid_points(points)
    assert [(p["measurement"], p["fields"]["BreathingRate"]) for p in points] == [("BreathingRateIntraday", 13.5)]


@pytest.mark.parametrize("payload", [None, {}, {"hrvReadings": None}])
def test_intraday_hrv_without_data(gf, payload):
    gf.garmin_obj.get_hrv_data.return_value = payload

    assert gf.get_intraday_hrv(DAY) == []


def test_intraday_hrv(gf):
    gf.garmin_obj.get_hrv_data.return_value = {"hrvReadings": [
        {"readingTimeGMT": gmt(T0), "hrvValue": 58}, {"readingTimeGMT": gmt(T0), "hrvValue": None}]}

    points = gf.get_intraday_hrv(DAY)

    assert_valid_points(points)
    assert [(p["measurement"], p["time"], p["fields"]["hrvValue"]) for p in points] == [
        ("HRV_Intraday", "2026-01-01T00:00:00+00:00", 58)]


# --- body composition ------------------------------------------------------------

def test_body_composition_weigh_ins(gf):
    gf.garmin_obj.get_weigh_ins.return_value = {"dailyWeightSummaries": [{"allWeightMetrics": [
        {"timestampGMT": ms(datetime(2026, 1, 1, 7, 30)), "weight": 72500.0, "bmi": 22.1, "bodyFat": 15.2, "sourceType": "INDEX_SCALE"},
        {"timestampGMT": None, "weight": 72000.0},
        {"timestampGMT": ms(T0), "weight": None},
    ]}]}

    points = gf.get_body_composition(DAY)

    assert_valid_points(points)
    assert [(p["time"], p["fields"]["weight"], p["tags"]["SourceType"]) for p in points] == [
        ("2026-01-01T07:30:00+00:00", 72500.0, "INDEX_SCALE"),
        ("2026-01-01T00:00:00+00:00", 72000.0, "Unknown"),  # no timestamp: midnight UTC of that date
    ]
    assert all(p["measurement"] == "BodyComposition" and p["tags"]["Frequency"] == "Intraday" for p in points)
    gf.garmin_obj.get_weigh_ins.assert_called_once_with(DAY, DAY)


@pytest.mark.parametrize("payload", [{"dailyWeightSummaries": []}, {}])
def test_body_composition_without_weigh_ins(gf, payload):
    gf.garmin_obj.get_weigh_ins.return_value = payload

    assert gf.get_body_composition(DAY) == []


def test_sleep_empty_readings_are_skipped(gf):
    start = datetime(2025, 12, 31, 23, 0)
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload(
        wellnessEpochRespirationDataDTOList=[{"startTimeGMT": ms(start), "respirationValue": None}],
        sleepHeartRate=[{"startGMT": ms(start), "value": 0}],
        sleepStress=[{"startGMT": ms(start), "value": None}],
        sleepBodyBattery=[{"startGMT": ms(start), "value": None}],
        hrvData=[{"startGMT": ms(start), "value": None}],
    )

    keys = {key for p in only(gf.get_sleep_data(DAY), "SleepIntraday") for key in p["fields"]}

    assert not keys & {"respirationValue", "heartRate", "stressValue", "bodyBattery", "hrvData"}


def test_sleep_stage_without_end_has_no_terminal_point(gf):
    start = datetime(2025, 12, 31, 23, 0)
    levels = [{"startGMT": gmt(start), "endGMT": gmt(start + timedelta(minutes=30)), "activityLevel": 1.0},
              {"startGMT": gmt(start + timedelta(minutes=30)), "endGMT": None, "activityLevel": None}]
    gf.garmin_obj.get_sleep_data.return_value = sleep_payload(sleepLevels=levels)

    stages = [p["fields"]["SleepStageLevel"] for p in gf.get_sleep_data(DAY) if "SleepStageLevel" in p["fields"]]

    assert stages == [1.0]


@pytest.mark.parametrize("getter, method, payload", [
    ("get_intraday_steps", "get_steps_data", []),
    ("get_intraday_br", "get_respiration_data", {}),
    ("get_intraday_br", "get_respiration_data", {"respirationValuesArray": None}),
])
def test_intraday_series_without_data(gf, getter, method, payload):
    getattr(gf.garmin_obj, method).return_value = payload

    assert getattr(gf, getter)(DAY) == []
