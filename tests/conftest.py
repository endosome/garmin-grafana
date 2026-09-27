import io
import os
import zipfile
from contextlib import ExitStack
from unittest import mock

import dotenv
import influxdb
import influxdb_client_3
import pytest

# Settings read by garmin_fetch at import time. Pinned so a developer's shell
# environment can't change which code paths the tests exercise.
TEST_ENV = {
    "INFLUXDB_VERSION": "1",
    "INFLUXDB_ENDPOINT_IS_HTTP": "True",
    "RATE_LIMIT_CALLS_SECONDS": "0",
}
UNSET_ENV = [
    "MANUAL_START_DATE", "MANUAL_END_DATE", "USER_TIMEZONE", "FETCH_SELECTION",
    "ACTIVITY_TYPE_FILTER", "IGNORE_ERRORS", "FORCE_REPROCESS_ACTIVITIES",
    "TAG_MEASUREMENTS_WITH_USER_EMAIL", "REQUEST_INTRADAY_DATA_REFRESH",
    "KEEP_FIT_FILES", "ALWAYS_PROCESS_FIT_FILES", "MAX_CONSECUTIVE_500_ERRORS",
    "MAX_INCOMPLETE_SYNC_RETRIES", "UPDATE_INTERVAL_SECONDS",
]


def offline_patches():
    """Patches that let garmin_fetch run its module-level setup without InfluxDB or a local .env file."""
    return [
        mock.patch.object(dotenv, "load_dotenv", return_value=False),
        mock.patch.object(influxdb, "InfluxDBClient", return_value=mock.MagicMock()),
        mock.patch.object(influxdb_client_3, "InfluxDBClient3", return_value=mock.MagicMock()),
    ]


def _import_garmin_fetch():
    patches = offline_patches() + [mock.patch.dict(os.environ, TEST_ENV)]
    saved = {k: os.environ.pop(k) for k in UNSET_ENV if k in os.environ}
    try:
        for p in patches:
            p.start()
        import garmin_fetch
        return garmin_fetch
    finally:
        for p in reversed(patches):
            p.stop()
        os.environ.update(saved)


_garmin_fetch = _import_garmin_fetch()


@pytest.fixture
def offline(monkeypatch):
    """Offline patches and pinned environment for tests that re-run garmin_fetch as a script."""
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    for name in UNSET_ENV:
        monkeypatch.delenv(name, raising=False)
    with ExitStack() as stack:
        for p in offline_patches():
            stack.enter_context(p)
        yield


@pytest.fixture
def gf(monkeypatch):
    """The garmin_fetch module with a fresh Garmin/InfluxDB mock and no sleeping."""
    monkeypatch.setattr(_garmin_fetch, "garmin_obj", mock.MagicMock())
    monkeypatch.setattr(_garmin_fetch, "influxdbclient", mock.MagicMock())
    monkeypatch.setattr(_garmin_fetch, "PARSED_ACTIVITY_ID_LIST", [])
    monkeypatch.setattr(_garmin_fetch.time, "sleep", lambda seconds: None)
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
    """Returns a function that registers FIT messages and gives back the zip bytes Garmin would serve for them."""
    monkeypatch.setattr(FakeFitFile, "messages", {})
    monkeypatch.setattr(gf, "FitFile", FakeFitFile)

    def make_fit_zip(key, messages):
        FakeFitFile.messages[key] = messages
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(f"{key}.fit", key.encode())
        return buffer.getvalue()

    return make_fit_zip
