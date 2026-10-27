"""Exercise clean database installation and failed upgrades against real PostgreSQL."""
from pathlib import Path
from unittest import mock
import os
import uuid
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest
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
        assert [row[0] for row in cur.fetchall()] == [
            path.name for path in sorted(Path(migrate.__file__).with_name('migrations').glob('*.sql'))
        ]
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")
        assert cur.fetchall() == [('samples',)]
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
    monkeypatch.setattr(storage, 'connect', lambda: connection)
    run_module_command(monkeypatch, 'garmin_grafana.migrate')
    assert connection.closed
    with psycopg.connect(**settings) as verification, verification.cursor() as cur:
        cur.execute('SELECT count(*) FROM schema_migrations')
        assert cur.fetchone()[0] == len(list(Path(migrate.__file__).with_name('migrations').glob('*.sql')))
        cur.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")
        assert cur.fetchall() == [('samples',)]


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
