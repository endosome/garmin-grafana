"""daily_fetch_write: FETCH_SELECTION dispatch and the intraday data refresh request."""
from datetime import datetime, timedelta
from unittest import mock

import pytest

SIMPLE_GETTERS = {
    "daily_avg": "get_daily_stats",
    "sleep": "get_sleep_data",
    "steps": "get_intraday_steps",
    "heartrate": "get_intraday_hr",
    "stress": "get_intraday_stress",
    "breathing": "get_intraday_br",
    "hrv": "get_intraday_hrv",
    "fitness_age": "get_fitness_age",
    "vo2": "get_vo2_max",
    "race_prediction": "get_race_predictions",
    "body_composition": "get_body_composition",
    "lactate_threshold": "get_lactate_threshold",
    "training_status": "get_training_status",
    "training_readiness": "get_training_readiness",
    "hill_score": "get_hillscore",
    "endurance_score": "get_endurance_score",
    "blood_pressure": "get_blood_pressure",
    "hydration": "get_hydration",
    "solar_intensity": "get_solar_intensity",
    "lifestyle": "get_lifestyle_data",
}
DEFAULT_SELECTION = ["daily_avg", "sleep", "steps", "heartrate", "stress", "breathing", "hrv", "fitness_age", "vo2",
                     "activity", "race_prediction", "body_composition", "lifestyle"]
DAY = "2026-01-01"


@pytest.fixture
def getters(gf, monkeypatch):
    """Replaces every metric getter with a mock returning one marker point; writes are recorded."""
    mocks = {}
    for key, name in SIMPLE_GETTERS.items():
        mocks[key] = mock.MagicMock(return_value=[{"marker": key}])
        monkeypatch.setattr(gf, name, mocks[key])
    mocks["activity"] = mock.MagicMock(return_value=([{"marker": "activity"}], {}, {}))
    monkeypatch.setattr(gf, "get_activity_summary", mocks["activity"])
    mocks["gps"] = mock.MagicMock(return_value=[{"marker": "gps"}])
    monkeypatch.setattr(gf, "fetch_activity_GPS", mocks["gps"])
    mocks["strength"] = mock.MagicMock(return_value=[{"marker": "strength"}])
    monkeypatch.setattr(gf, "get_strength_training_data", mocks["strength"])
    mocks["write"] = mock.MagicMock()
    monkeypatch.setattr(gf, "write_points_to_influxdb", mocks["write"])
    return mocks


def called_keys(getters):
    return sorted(key for key in [*SIMPLE_GETTERS, "activity"] if getters[key].called)


def written_markers(getters):
    return [p["marker"] for call in getters["write"].call_args_list for p in call.args[0]]


def test_default_selection(gf, getters, monkeypatch):
    assert gf.FETCH_SELECTION.split(",") == DEFAULT_SELECTION

    gf.daily_fetch_write(DAY)

    assert called_keys(getters) == sorted(DEFAULT_SELECTION)


@pytest.mark.parametrize("key", sorted(SIMPLE_GETTERS))
def test_each_key_runs_only_its_own_getter(gf, getters, monkeypatch, key):
    monkeypatch.setattr(gf, "FETCH_SELECTION", key)

    gf.daily_fetch_write(DAY)

    assert called_keys(getters) == [key]
    getters[key].assert_called_once_with(DAY)
    assert written_markers(getters) == [key]


def test_all_keys(gf, getters, monkeypatch):
    monkeypatch.setattr(gf, "FETCH_SELECTION", ",".join([*SIMPLE_GETTERS, "activity"]))

    gf.daily_fetch_write(DAY)

    assert called_keys(getters) == sorted([*SIMPLE_GETTERS, "activity"])


def test_activity_details_and_strength_sets(gf, getters, monkeypatch):
    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity")
    strength = {3: {"typeKey": "strength_training"}}
    getters["activity"].return_value = ([{"marker": "activity"}], {1: "running"}, strength)

    gf.daily_fetch_write(DAY)

    getters["gps"].assert_called_once_with({1: "running"})
    getters["strength"].assert_called_once_with(strength)
    assert written_markers(getters) == ["activity", "gps", "strength"]


def test_strength_fetch_skipped_without_strength_activities(gf, getters, monkeypatch):
    monkeypatch.setattr(gf, "FETCH_SELECTION", "activity")

    gf.daily_fetch_write(DAY)

    getters["gps"].assert_called_once_with({})
    getters["strength"].assert_not_called()


# --- intraday data refresh -------------------------------------------------------------

OLD_DAY = (datetime.today() - timedelta(days=200)).strftime("%Y-%m-%d")


@pytest.fixture
def refresh(gf, getters, monkeypatch):
    monkeypatch.setattr(gf, "REQUEST_INTRADAY_DATA_REFRESH", True)
    monkeypatch.setattr(gf, "IGNORE_INTRADAY_DATA_REFRESH_DAYS", 30)
    monkeypatch.setattr(gf, "FETCH_SELECTION", "heartrate")

    def respond(*statuses):
        gf.garmin_obj.connectapi.side_effect = [{"status": s} for s in statuses]

    return respond


def refresh_requests(gf):
    return [c for c in gf.garmin_obj.connectapi.call_args_list if c.kwargs.get("method") == "POST"]


@pytest.mark.parametrize("status, expected_sleeps", [
    ("SUBMITTED", [10]),
    ("COMPLETE", []),
    ("SOMETHING_NEW", [5]),
])
def test_refresh_then_fetch(gf, getters, refresh, status, expected_sleeps):
    refresh(status)

    gf.daily_fetch_write(OLD_DAY)

    (request,) = refresh_requests(gf)
    assert request.args == (f"wellness-service/wellness/epoch/request/{OLD_DAY}",)
    assert [c.args[0] for c in gf.time.sleep.call_args_list] == expected_sleeps
    getters["heartrate"].assert_called_once_with(OLD_DAY)


def test_refresh_no_files_skips_the_day(gf, getters, refresh):
    refresh("NO_FILES_FOUND")

    assert gf.daily_fetch_write(OLD_DAY) is None
    getters["heartrate"].assert_not_called()


def test_refresh_denied_waits_a_day_and_retries(gf, getters, refresh):
    refresh("DENIED", "SUBMITTED")

    gf.daily_fetch_write(OLD_DAY)

    assert len(refresh_requests(gf)) == 2
    assert [c.args[0] for c in gf.time.sleep.call_args_list] == [86500, 10]
    getters["heartrate"].assert_called_once()


def test_missing_status_is_treated_as_unknown(gf, getters, refresh):
    gf.garmin_obj.connectapi.side_effect = [{}]

    gf.daily_fetch_write(OLD_DAY)

    getters["heartrate"].assert_called_once()


def test_recent_days_are_not_refreshed(gf, getters, refresh):
    gf.daily_fetch_write((datetime.today() - timedelta(days=5)).strftime("%Y-%m-%d"))

    assert refresh_requests(gf) == []
    getters["heartrate"].assert_called_once()


def test_refresh_disabled_by_default(gf, getters, monkeypatch):
    assert gf.REQUEST_INTRADAY_DATA_REFRESH is False

    gf.daily_fetch_write(OLD_DAY)

    gf.garmin_obj.connectapi.assert_not_called()
