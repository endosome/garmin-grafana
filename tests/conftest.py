import logging
import os
import re
import runpy
import uuid
from unittest import mock

import dotenv
from garmin_grafana import migrate, storage
from garmin_grafana.storage import PostgresStore
import pytest

import personas as persona_data
from helpers import GARMIN_FETCH, refresh_rollups, zip_bytes
from panel_queries import STATEMENTS, iso, panels, queries

# Every environment variable garmin_fetch reads is cleared, apart from these pinned
# values, so a developer's shell environment can't change what the tests exercise.
def pytest_addoption(parser):
    parser.addoption('--update-values', action='store_true', help='rewrite tests/data/panel_values.json from the current queries')
    parser.addoption('--update-schemas', action='store_true', help='rewrite tests/data/panel_schemas.json from the current queries')


def pytest_terminal_summary(terminalreporter):
    import panel_registry
    covered = len(panel_registry.VALUE_TESTS)
    total = covered + len(panel_registry.SCREENSHOT) + len(panel_registry.NO_VALUES)
    terminalreporter.write_line(f'dashboard panels: {covered} with value tests, {len(panel_registry.SCREENSHOT)} with a screenshot baseline, {len(panel_registry.NO_VALUES)} with a written reason (of {total})')






def pytest_configure(config):
    """With pytest-xdist (`-n 2`), each worker gets its own empty database next to the configured one: two workers sharing a database
    deadlock on migrations and rollup refreshes. Without xdist nothing changes."""
    if hasattr(config, 'workerinput') and os.getenv('TEST_DATABASE') == '1':
        name = f"{os.environ['PGDATABASE']}_{config.workerinput['workerid']}"
        from psycopg import sql
        with storage.connect() as admin:
            admin.autocommit = True
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        os.environ['PGDATABASE'] = name


def pytest_unconfigure(config):
    """The controller process drops the workers' databases once they have finished."""
    if hasattr(config, 'workerinput') or not getattr(config.option, 'numprocesses', None) or os.getenv('TEST_DATABASE') != '1':
        return
    from psycopg import sql
    with storage.connect() as admin:
        admin.autocommit = True
        for (name,) in admin.execute("SELECT datname FROM pg_database WHERE datname LIKE %s", (os.environ['PGDATABASE'] + r'\_gw%',)).fetchall():
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))


TEST_ENV = {
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
            mock.patch.object(storage, "PostgresStore", return_value=mock.MagicMock()):
        _pin_environment(os.environ.__setitem__, lambda name: os.environ.pop(name, None))
        import garmin_fetch
        return garmin_fetch


_garmin_fetch = _import_garmin_fetch()


@pytest.fixture
def offline(monkeypatch):
    """Pinned environment with mocked persistence for standalone script tests."""
    _pin_environment(monkeypatch.setenv, lambda name: monkeypatch.delenv(name, raising=False))
    monkeypatch.setattr(dotenv, "load_dotenv", mock.MagicMock(return_value=False))
    monkeypatch.setattr(storage, "PostgresStore", mock.MagicMock(return_value=mock.MagicMock()))
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
    """The garmin_fetch module with a fresh Garmin/persistence mock and no sleeping.

    Module globals the code under test mutates are restored after each test.
    """
    monkeypatch.setattr(_garmin_fetch, "garmin_obj", mock.MagicMock())
    persisted = []
    def capture(records, **kwargs):
        rows = list(records)
        persisted.extend(rows)
        return len(rows)
    backend = mock.MagicMock()
    backend.write.side_effect = capture
    backend.persisted = persisted
    monkeypatch.setattr(_garmin_fetch, "store", backend)
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


@pytest.fixture(scope='session')
def personas():
    """Writes every persona once per session, each under its own new account, and returns {account: (sid, persona)}.

    Opens its own connection and doesn't depend on `db`. Tests only read from the result
    (see `load`), so what they see doesn't depend on which test ran first.
    """
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* pointing to a disposable TimescaleDB database')
    suffix = uuid.uuid4().hex
    with storage.connect() as connection:
        migrate.migrate(connection)
        migrate.record_version(connection)  # the CLI does this after migrating; a dashboard stat shows it
        with connection.transaction():
            connection.execute("SELECT alter_job(job_id, scheduled => false) FROM timescaledb_information.jobs WHERE proc_name = 'policy_refresh_continuous_aggregate'")
        loaded = {}
        for factory in persona_data.PERSONAS:
            persona = factory()
            store = PostgresStore(account=f'{persona.account}-{suffix}', connection=connection)
            store.write(persona.records)
            with connection.transaction(), connection.cursor() as cur:
                loaded[persona.account] = store.source_id(cur)
        refresh_rollups()
    return loaded


@pytest.fixture(scope='session')
def reader(personas):
    """A read-only connection: a test that starts writing to the shared personas fails loudly."""
    connection = storage.connect()
    connection.read_only = True
    yield connection
    connection.close()


@pytest.fixture
def load(personas, reader):
    """Returns a function giving a runner for dashboard queries on a persona's shared account."""
    def load(persona):
        sid = personas[persona.account]

        def run(dashboard, panel, start, end=persona_data.NOW, ref='A', activity='__all', category='__all', sql=None, interval='5 minutes'):
            if sql is None:
                ids = panels(dashboard)
                sql = STATEMENTS[f'{dashboard}:{ids.get(panel, panel)}:{ref}']
            sql = queries.expand_query(sql, sid, start=iso(start), end=iso(end), timezone=persona.timezone, activity=activity, category=category, interval=interval)
            with reader.transaction(), reader.cursor() as cur:
                cur.execute(sql)
                return [dict(zip([c.name for c in cur.description], row)) for row in cur.fetchall()]
        run.sid = sid
        return run
    return load


@pytest.fixture(scope='session')
def empty_source(personas):
    """The source id of a new account without any data, next to the personas' accounts."""
    with storage.connect() as connection:
        store = PostgresStore(account='empty-' + uuid.uuid4().hex, connection=connection)
        with connection.transaction(), connection.cursor() as cur:
            return store.source_id(cur)
