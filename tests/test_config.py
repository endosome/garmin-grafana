"""Module-level setup of garmin_fetch.py: environment parsing and the InfluxDB connection check."""
import base64
import logging

import dotenv
import influxdb
import influxdb_client_3
import pytest
from influxdb.exceptions import InfluxDBClientError
from influxdb_client_3 import InfluxDBError

REAL_LOAD_DOTENV = dotenv.load_dotenv


def test_defaults(load_script):
    config = load_script()

    assert config["INFLUXDB_HOST"] == "localhost" and config["INFLUXDB_PORT"] == 8086
    assert config["INFLUXDB_DATABASE"] == "GarminStats"
    assert config["UPDATE_INTERVAL_SECONDS"] == 300
    assert config["FETCH_FAILED_WAIT_SECONDS"] == 1800
    assert config["MAX_CONSECUTIVE_500_ERRORS"] == 10
    assert config["MAX_INCOMPLETE_SYNC_RETRIES"] == 3
    assert config["IGNORE_INTRADAY_DATA_REFRESH_DAYS"] == 30
    assert config["GARMINCONNECT_EMAIL"] is None and config["GARMINCONNECT_PASSWORD"] is None
    assert config["GARMIN_DEVICENAME"] == "Unknown" and config["GARMIN_DEVICENAME_AUTOMATIC"] is True
    assert config["ACTIVITY_TYPE_FILTER"] == []
    assert config["LACTATE_THRESHOLD_SPORTS"] == ["RUNNING"]
    assert config["MANUAL_START_DATE"] is None
    for flag in ["KEEP_FIT_FILES", "ALWAYS_PROCESS_FIT_FILES", "REQUEST_INTRADAY_DATA_REFRESH",
                 "TAG_MEASUREMENTS_WITH_USER_EMAIL", "IGNORE_ERRORS", "GARMINCONNECT_IS_CN"]:
        assert config[flag] is False, flag
    for flag in ["AUTO_DATE_RANGE", "FORCE_REPROCESS_ACTIVITIES"]:
        assert config[flag] is True, flag


def test_endpoint_is_http_by_default(load_script, monkeypatch):
    monkeypatch.delenv("INFLUXDB_ENDPOINT_IS_HTTP")

    assert load_script()["INFLUXDB_ENDPOINT_IS_HTTP"] is True


@pytest.mark.parametrize("value, expected", [
    ("True", True), ("true", True), ("TRUE", True), ("t", True), ("T", True), ("yes", True), ("YES", True), ("1", True),
    ("False", False), ("false", False), ("0", False), ("no", False), ("", False), ("enabled", False),
])
@pytest.mark.parametrize("flag", ["KEEP_FIT_FILES", "ALWAYS_PROCESS_FIT_FILES", "REQUEST_INTRADAY_DATA_REFRESH",
                                  "TAG_MEASUREMENTS_WITH_USER_EMAIL", "IGNORE_ERRORS", "GARMINCONNECT_IS_CN"])
def test_opt_in_flags(load_script, flag, value, expected):
    assert load_script(**{flag: value})[flag] is expected


@pytest.mark.parametrize("value, expected", [
    ("False", False), ("false", False), ("FALSE", False), ("f", False), ("F", False), ("no", False), ("NO", False), ("0", False),
    ("True", True), ("1", True), ("", True), ("disabled", True),
])
@pytest.mark.parametrize("flag", ["AUTO_DATE_RANGE", "FORCE_REPROCESS_ACTIVITIES", "INFLUXDB_ENDPOINT_IS_HTTP"])
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


@pytest.mark.parametrize("name, value", [("INFLUXDB_PORT", "eighty"), ("UPDATE_INTERVAL_SECONDS", "5m"), ("MAX_INCOMPLETE_SYNC_RETRIES", "")])
def test_non_integer_settings_fail_fast(load_script, name, value):
    with pytest.raises(ValueError):
        load_script(**{name: value})


@pytest.mark.parametrize("version", ["2", "v1", ""])
def test_unsupported_influxdb_version(load_script, version):
    with pytest.raises(AssertionError, match="Only InfluxDB version 1 or 3"):
        load_script(INFLUXDB_VERSION=version)


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


# --- InfluxDB connection ------------------------------------------------------------------

def test_influxdb_v1_over_http(load_script):
    config = load_script(INFLUXDB_HOST="db", INFLUXDB_PORT="8087", INFLUXDB_USERNAME="u", INFLUXDB_PASSWORD="p", INFLUXDB_DATABASE="Stats")

    influxdb.InfluxDBClient.assert_called_once_with(host="db", port=8087, username="u", password="p")
    client = config["influxdbclient"]
    client.switch_database.assert_called_once_with("Stats")
    (demo,) = client.write_points.call_args.args[0]
    assert demo["measurement"] == "DemoPoint" and demo["fields"] == {"DemoField": 0}


def test_influxdb_v1_over_https(load_script):
    load_script(INFLUXDB_ENDPOINT_IS_HTTP="False", INFLUXDB_HOST="db.example.com", INFLUXDB_PORT="443")

    kwargs = influxdb.InfluxDBClient.call_args.kwargs
    assert (kwargs["host"], kwargs["port"], kwargs["ssl"], kwargs["verify_ssl"]) == ("db.example.com", 443, True, True)


@pytest.mark.parametrize("is_http, scheme", [("True", "http"), ("False", "https")])
def test_influxdb_v3(load_script, is_http, scheme):
    config = load_script(INFLUXDB_VERSION="3", INFLUXDB_ENDPOINT_IS_HTTP=is_http, INFLUXDB_HOST="db", INFLUXDB_PORT="8181",
                         INFLUXDB_V3_ACCESS_TOKEN="tok", INFLUXDB_ORG="org", INFLUXDB_DATABASE="Stats")

    influxdb_client_3.InfluxDBClient3.assert_called_once_with(host=f"{scheme}://db:8181", token="tok", org="org", database="Stats")
    influxdb.InfluxDBClient.assert_not_called()
    (demo,) = config["influxdbclient"].write.call_args.kwargs["record"]
    assert demo["measurement"] == "DemoPoint"


@pytest.mark.parametrize("version, error", [("1", InfluxDBClientError("authorization failed")), ("3", InfluxDBError(message="unauthorized"))])
def test_influxdb_connection_failure_aborts(load_script, version, error):
    client = (influxdb.InfluxDBClient if version == "1" else influxdb_client_3.InfluxDBClient3).return_value
    client.write_points.side_effect = error
    client.write.side_effect = error

    with pytest.raises(InfluxDBClientError, match="InfluxDB connection failed"):
        load_script(INFLUXDB_VERSION=version)
