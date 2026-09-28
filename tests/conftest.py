import logging
import os
import re
import runpy
from unittest import mock

import dotenv
import influxdb
import influxdb_client_3
import pytest

from helpers import GARMIN_FETCH, zip_bytes

# Every environment variable garmin_fetch reads is cleared, apart from these pinned
# values, so a developer's shell environment can't change what the tests exercise.
TEST_ENV = {
    "INFLUXDB_VERSION": "1",
    "INFLUXDB_ENDPOINT_IS_HTTP": "True",
    "RATE_LIMIT_CALLS_SECONDS": "0",
}
READ_ENV = sorted(set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"', GARMIN_FETCH.read_text())))


def _pin_environment(setenv, delenv):
    for name in READ_ENV:
        delenv(name)
    for name, value in TEST_ENV.items():
        setenv(name, value)


def _import_garmin_fetch():
    with mock.patch.dict(os.environ), \
            mock.patch.object(dotenv, "load_dotenv", return_value=False), \
            mock.patch.object(influxdb, "InfluxDBClient", return_value=mock.MagicMock()), \
            mock.patch.object(influxdb_client_3, "InfluxDBClient3", return_value=mock.MagicMock()):
        _pin_environment(os.environ.__setitem__, lambda name: os.environ.pop(name, None))
        import garmin_fetch
        return garmin_fetch


_garmin_fetch = _import_garmin_fetch()


@pytest.fixture
def offline(monkeypatch):
    """Pinned environment and mocked InfluxDB clients for tests that run garmin_fetch.py as a script.

    influxdb.InfluxDBClient / influxdb_client_3.InfluxDBClient3 are MagicMocks whose
    return_value is the client the script will use. load_dotenv is disabled.
    """
    _pin_environment(monkeypatch.setenv, lambda name: monkeypatch.delenv(name, raising=False))
    monkeypatch.setattr(dotenv, "load_dotenv", mock.MagicMock(return_value=False))
    monkeypatch.setattr(influxdb, "InfluxDBClient", mock.MagicMock(return_value=mock.MagicMock()))
    monkeypatch.setattr(influxdb_client_3, "InfluxDBClient3", mock.MagicMock(return_value=mock.MagicMock()))
    # The script replaces the root logger's handlers and level; restore them for later tests.
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def load_script(offline, monkeypatch):
    """Runs garmin_fetch.py's module-level setup (not its __main__ block) with extra env vars; returns its globals."""

    def load(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return runpy.run_path(str(GARMIN_FETCH), run_name="garmin_fetch_under_test")

    return load


@pytest.fixture
def gf(monkeypatch):
    """The garmin_fetch module with a fresh Garmin/InfluxDB mock and no sleeping.

    Module globals the code under test mutates are restored after each test.
    """
    monkeypatch.setattr(_garmin_fetch, "garmin_obj", mock.MagicMock())
    monkeypatch.setattr(_garmin_fetch, "influxdbclient", mock.MagicMock())
    monkeypatch.setattr(_garmin_fetch, "PARSED_ACTIVITY_ID_LIST", [])
    monkeypatch.setattr(_garmin_fetch, "GARMIN_DEVICENAME", _garmin_fetch.GARMIN_DEVICENAME)
    monkeypatch.setattr(_garmin_fetch, "GARMIN_DEVICEID", _garmin_fetch.GARMIN_DEVICEID)
    monkeypatch.setattr(_garmin_fetch.time, "sleep", mock.MagicMock())
    return _garmin_fetch


class FakeFitFile:
    """Stands in for fitparse.FitFile. The FIT file bytes are a key into FakeFitFile.messages."""

    messages = {}

    def __init__(self, fileish):
        self.key = fileish.read().decode()

    def parse(self):
        pass

    def get_messages(self, name):
        return [mock.Mock(get_values=lambda values=values: values) for values in self.messages[self.key].get(name, [])]


@pytest.fixture
def fake_fit(gf, monkeypatch):
    """Returns a function that registers FIT messages and gives back the zip bytes Garmin would serve for them.

    For edge cases fitparse can't produce from a real file (e.g. a present-but-None
    start_time); tests/fit_builder.py builds real FIT files for everything else.
    """
    monkeypatch.setattr(FakeFitFile, "messages", {})
    monkeypatch.setattr(gf, "FitFile", FakeFitFile)

    def make_fit_zip(key, messages):
        FakeFitFile.messages[key] = messages
        return zip_bytes({f"{key}.fit": key.encode()})

    return make_fit_zip
