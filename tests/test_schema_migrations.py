"""Exercise clean database installation and failed upgrades against real PostgreSQL."""
from pathlib import Path
from unittest import mock
import os
import uuid
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest
import garmin_fit_store
from garmin_grafana import migrate, storage


@pytest.fixture
def fresh_database():
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* for a disposable database administrator')
    administrator = storage.connect()
    administrator.autocommit = True
    name = 'garmin_test_schema_' + uuid.uuid4().hex
    connection = None
    created = False
    try:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        settings = conninfo_to_dict(administrator.info.dsn)
        settings['dbname'] = name
        connection = psycopg.connect(**settings)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        if created:
            # This exact database was created above; never drop the configured test database.
            administrator.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        administrator.close()


def test_empty_database_installation_is_complete_and_repeatable(fresh_database):
    connection = fresh_database
    migrate.migrate(connection)
    migrate.migrate(connection)
    with connection.transaction(), connection.cursor() as cur:
        cur.execute('SELECT name FROM schema_migrations ORDER BY name')
        assert [row[0] for row in cur.fetchall()] == sorted(
            [path.name for path in Path(migrate.__file__).with_name('migrations').glob('*.sql')] +
            ['fit/' + path.name for path in garmin_fit_store.MIGRATIONS.glob('*.sql')]
        )
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1")
        assert cur.fetchall() == [('records',), ('samples',)]
        cur.execute('SELECT count(*) FROM fit.profile_versions')
        assert cur.fetchone() == (1,)
        cur.execute("SELECT conname FROM pg_constraint WHERE conname IN ('samples_device_fk','records_device_fk') ORDER BY conname")
        assert cur.fetchall() == [('records_device_fk',), ('samples_device_fk',)]


def test_failed_upgrade_rolls_back_ddl_and_migration_history(fresh_database, tmp_path):
    connection = fresh_database
    migrate.migrate(connection)
    valid = tmp_path / '006_test_upgrade.sql'
    invalid = tmp_path / '007_test_broken.sql'
    valid.write_text('CREATE TABLE upgrade_probe (id integer PRIMARY KEY);')
    invalid.write_text('SELECT * FROM deliberately_missing_upgrade_table;')
    with mock.patch.object(migrate, 'Path') as migration_paths:
        migration_paths.return_value.with_name.return_value.glob.return_value = [invalid, valid]
        with pytest.raises(psycopg.errors.UndefinedTable):
            migrate.migrate(connection)
    with connection.transaction(), connection.cursor() as cur:
        cur.execute("SELECT to_regclass('upgrade_probe')")
        assert cur.fetchone() == (None,)
        cur.execute('SELECT count(*) FROM schema_migrations WHERE name IN (%s,%s)', (valid.name, invalid.name))
        assert cur.fetchone() == (0,)
    # A failed upgrade leaves the prior schema usable and the connection recoverable.
    migrate.migrate(connection)


def test_module_command_initializes_a_fresh_database(fresh_database, monkeypatch):
    from helpers import run_module_command
    connection = fresh_database
    settings = conninfo_to_dict(connection.info.dsn)
    monkeypatch.delenv('PGREADER_PASSWORD', raising=False)
    opened = []

    def connect():  # the command opens more than one connection; all go to the fresh database
        opened.append(psycopg.connect(**settings) if opened else connection)
        return opened[-1]
    monkeypatch.setattr(storage, 'connect', connect)
    run_module_command(monkeypatch, 'garmin_grafana.migrate')
    assert connection.closed and all(c.closed for c in opened)
    with psycopg.connect(**settings) as verification, verification.cursor() as cur:
        cur.execute('SELECT count(*) FROM schema_migrations')
        assert cur.fetchone()[0] == len(list(Path(migrate.__file__).with_name('migrations').glob('*.sql'))) + len(list(garmin_fit_store.MIGRATIONS.glob('*.sql')))
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY 1")
        assert cur.fetchall() == [('records',), ('samples',)]


def test_record_version_stores_installed_package_version(fresh_database):
    connection = fresh_database
    migrate.migrate(connection)
    with mock.patch.object(migrate.metadata, 'version', return_value='1.2.3'):
        migrate.record_version(connection)
    with mock.patch.object(migrate.metadata, 'version', return_value='1.2.4'):
        migrate.record_version(connection)
    with connection.transaction(), connection.cursor() as cur:
        cur.execute("SELECT key, value FROM app_info")
        assert cur.fetchall() == [('version', '1.2.4')]
    with mock.patch.object(migrate.metadata, 'version', side_effect=migrate.metadata.PackageNotFoundError):
        migrate.record_version(connection)
    with connection.transaction(), connection.cursor() as cur:
        cur.execute("SELECT value FROM app_info WHERE key = 'version'")
        assert cur.fetchone() == ('unknown',)


def test_command_updates_timescaledb_from_an_older_image(fresh_database, monkeypatch):
    connection = fresh_database
    settings = conninfo_to_dict(connection.info.dsn)
    with connection.transaction(), connection.cursor() as cur:
        latest, previous = {}, {}
        for name in ('timescaledb', 'timescaledb_toolkit'):
            cur.execute('SELECT default_version FROM pg_available_extensions WHERE name = %s', (name,))
            latest[name] = cur.fetchone()[0]
            cur.execute(r"""SELECT version FROM pg_available_extension_versions WHERE name = %s AND version ~ '^[0-9.]+$'
                AND version <> %s ORDER BY string_to_array(version, '.')::int[] DESC LIMIT 1""", (name, latest[name]))
            previous[name] = cur.fetchone()[0]
            cur.execute(sql.SQL('DROP EXTENSION IF EXISTS {}').format(sql.Identifier(name)))
    connection.close()
    with psycopg.connect(**settings, autocommit=True) as older:  # a new session, before any version is loaded
        for name, version in previous.items():
            older.execute(sql.SQL('CREATE EXTENSION {} VERSION {}').format(sql.Identifier(name), sql.Literal(version)))
    monkeypatch.setattr(migrate, 'connect', lambda: psycopg.connect(**settings))
    migrate.update_extension()
    migrate.update_extension()  # already current: nothing to do
    with psycopg.connect(**settings) as upgraded:
        assert dict(upgraded.execute("SELECT extname, extversion FROM pg_extension WHERE extname LIKE 'timescaledb%'").fetchall()) == latest
        migrate.migrate(upgraded)


@pytest.mark.parametrize('recorded', [None, 'stale'], ids=['alpine-musl', 'older-glibc'])
def test_command_rebuilds_indexes_when_the_c_library_sorts_differently(recorded, monkeypatch):
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* for a disposable database administrator')
    name = 'garmin_test_collation_' + uuid.uuid4().hex
    with storage.connect() as administrator:
        administrator.autocommit = True
        # A linguistic locale, unlike the image's C.UTF-8 default, has a version that changes with the C library.
        administrator.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 LOCALE 'en_US.utf8'").format(sql.Identifier(name)))
        try:
            settings = conninfo_to_dict(administrator.info.dsn)
            settings['dbname'] = name
            with psycopg.connect(**settings, autocommit=True) as connection:
                connection.execute('CREATE TABLE words (word text PRIMARY KEY)')
                index = connection.execute("SELECT pg_relation_filenode('words_pkey')").fetchone()
                actual = connection.execute('SELECT datcollversion FROM pg_database WHERE datname = %s', (name,)).fetchone()[0]
                connection.execute('UPDATE pg_database SET datcollversion = %s WHERE datname = %s', (recorded, name))
                monkeypatch.setattr(migrate, 'connect', lambda: psycopg.connect(**settings))
                migrate.rebuild_indexes()
                rebuilt = connection.execute("SELECT pg_relation_filenode('words_pkey')").fetchone()
                assert rebuilt != index and connection.execute('SELECT datcollversion FROM pg_database WHERE datname = %s', (name,)).fetchone() == (actual,)
                migrate.rebuild_indexes()  # recorded now: nothing to do
                assert connection.execute("SELECT pg_relation_filenode('words_pkey')").fetchone() == rebuilt
        finally:
            administrator.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
