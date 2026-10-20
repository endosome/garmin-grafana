"""Module-level setup of garmin_fetch.py: environment parsing without network activity."""
import base64
import logging

import dotenv
import pytest

REAL_LOAD_DOTENV = dotenv.load_dotenv


def test_defaults(load_script):
    config = load_script()

    assert config["DATABASE_NAME"] == "garmin"
    assert config["UPDATE_INTERVAL_SECONDS"] == 300
    assert config["FETCH_FAILED_WAIT_SECONDS"] == 1800
    assert config["MAX_CONSECUTIVE_500_ERRORS"] == 10
    assert config["IGNORE_INTRADAY_DATA_REFRESH_DAYS"] == 30
    assert config["GARMINCONNECT_EMAIL"] is None and config["GARMINCONNECT_PASSWORD"] is None
    assert config["GARMIN_DEVICENAME"] == "Unknown" and config["GARMIN_DEVICENAME_AUTOMATIC"] is True
    assert config["ACTIVITY_TYPE_FILTER"] == []
    assert config["LACTATE_THRESHOLD_SPORTS"] == ["RUNNING"]
    assert config["MANUAL_START_DATE"] is None
    for flag in ["KEEP_FIT_FILES", "ALWAYS_PROCESS_FIT_FILES", "REQUEST_INTRADAY_DATA_REFRESH",
                 "IGNORE_ERRORS", "GARMINCONNECT_IS_CN"]:
        assert config[flag] is False, flag
    for flag in ["FORCE_REPROCESS_ACTIVITIES"]:
        assert config[flag] is True, flag


@pytest.mark.parametrize("value, expected", [
    ("True", True), ("true", True), ("TRUE", True), ("t", True), ("T", True), ("yes", True), ("YES", True), ("1", True),
    ("False", False), ("false", False), ("0", False), ("no", False), ("", False), ("enabled", False),
])
@pytest.mark.parametrize("flag", ["KEEP_FIT_FILES", "ALWAYS_PROCESS_FIT_FILES", "REQUEST_INTRADAY_DATA_REFRESH",
                                  "IGNORE_ERRORS", "GARMINCONNECT_IS_CN"])
def test_opt_in_flags(load_script, flag, value, expected):
    assert load_script(**{flag: value})[flag] is expected


@pytest.mark.parametrize("value, expected", [
    ("False", False), ("false", False), ("FALSE", False), ("f", False), ("F", False), ("no", False), ("NO", False), ("0", False),
    ("True", True), ("1", True), ("", True), ("disabled", True),
])
@pytest.mark.parametrize("flag", ["FORCE_REPROCESS_ACTIVITIES"])
def test_opt_out_flags(load_script, flag, value, expected):
    assert load_script(**{flag: value})[flag] is expected


def test_credentials(load_script):
    encoded = base64.b64encode(b"  p@ss w0rd\n").decode()

    config = load_script(GARMINCONNECT_EMAIL="  me@example.com ", GARMINCONNECT_BASE64_PASSWORD=encoded)

    assert config["GARMINCONNECT_EMAIL"] == "me@example.com"
    assert config["GARMINCONNECT_PASSWORD"] == "p@ss w0rd"


def test_blank_email_is_unset(load_script):
    assert load_script(GARMINCONNECT_EMAIL="   ")["GARMINCONNECT_EMAIL"] is None


def test_invalid_base64_password_fails_fast(load_script):
    with pytest.raises(ValueError):
        load_script(GARMINCONNECT_BASE64_PASSWORD="not base64!")


def test_list_settings(load_script):
    config = load_script(ACTIVITY_TYPE_FILTER=" Running, strength_training ,,", LACTATE_THRESHOLD_SPORTS="running,Cycling")

    assert config["ACTIVITY_TYPE_FILTER"] == ["running", "strength_training"]
    assert config["LACTATE_THRESHOLD_SPORTS"] == ["RUNNING", "CYCLING"]


def test_configured_device_name_disables_detection(load_script):
    config = load_script(GARMIN_DEVICENAME="Edge 1050", GARMIN_DEVICEID="42")

    assert (config["GARMIN_DEVICENAME"], config["GARMIN_DEVICEID"], config["GARMIN_DEVICENAME_AUTOMATIC"]) == ("Edge 1050", "42", False)


@pytest.mark.parametrize("name, value", [("UPDATE_INTERVAL_SECONDS", "5m")])
def test_non_integer_settings_fail_fast(load_script, name, value):
    with pytest.raises(ValueError):
        load_script(**{name: value})


def test_log_level(load_script):
    load_script(LOG_LEVEL="DEBUG")

    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("value, expected", [("debug", logging.DEBUG), (" Warning ", logging.WARNING), ("warn", logging.WARNING)])
def test_log_level_is_case_insensitive(load_script, value, expected):
    load_script(LOG_LEVEL=value)

    assert logging.getLogger().level == expected


def test_unknown_log_level_falls_back_to_info(load_script, capsys):
    load_script(LOG_LEVEL="verbose")

    assert logging.getLogger().level == logging.INFO
    assert "Unknown LOG_LEVEL 'verbose'" in capsys.readouterr().out


def test_override_file_takes_precedence(load_script, monkeypatch, tmp_path):
    (tmp_path / "override-default-vars.env").write_text("FETCH_SELECTION=sleep\nUPDATE_INTERVAL_SECONDS=60\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(dotenv, "load_dotenv", REAL_LOAD_DOTENV)

    config = load_script(FETCH_SELECTION="heartrate")

    assert config["FETCH_SELECTION"] == "sleep"
    assert config["UPDATE_INTERVAL_SECONDS"] == 60




def test_import_does_not_connect_to_database(load_script,monkeypatch):
    from garmin_grafana import storage
    from unittest import mock
    connection=mock.Mock(side_effect=AssertionError('import should not connect'))
    monkeypatch.setattr(storage,'connect',connection)
    config=load_script()
    connection.assert_not_called()
    assert 'store' in config
