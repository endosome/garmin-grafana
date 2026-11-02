"""Apply ordered SQL migrations explicitly; safe for concurrent deployment startups."""
import hashlib
import os
from importlib import metadata
from psycopg import sql
from pathlib import Path
from .storage import connect


def migrate(connection):
    with connection.transaction(), connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(782643901)')
        cur.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in sorted(Path(__file__).with_name('migrations').glob('*.sql')):
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            cur.execute('SELECT checksum FROM schema_migrations WHERE name=%s', (path.name,))
            row = cur.fetchone()
            if row:
                if row[0] != digest:
                    raise RuntimeError(f'Applied migration changed: {path.name}')
                continue
            cur.execute(sql)
            cur.execute('INSERT INTO schema_migrations(name,checksum) VALUES (%s,%s)', (path.name, digest))


def update_extension():
    """Move an existing database to the TimescaleDB and Toolkit versions of a newer image, which does not do it itself."""
    with connect() as connection:
        outdated = [row[0] for row in connection.execute("""SELECT e.extname FROM pg_extension e
            JOIN pg_available_extensions a ON a.name = e.extname
            WHERE e.extname IN ('timescaledb', 'timescaledb_toolkit') AND e.extversion <> a.default_version ORDER BY e.extname""")]
    for name in outdated:
        # TimescaleDB's must be the first statement of a new session, before the old version's library is loaded.
        with connect() as connection:
            connection.autocommit = True
            connection.execute(sql.SQL('ALTER EXTENSION {} UPDATE').format(sql.Identifier(name)))


def rebuild_indexes():
    """Rebuild every index once the C library sorts text differently, as after moving from an Alpine (musl) image."""
    with connect() as connection:
        name, recorded, actual = connection.execute("""SELECT datname, datcollversion, pg_database_collation_actual_version(oid)
            FROM pg_database WHERE datname = current_database()""").fetchone()
    if recorded == actual:
        return
    with connect() as connection:
        connection.autocommit = True
        connection.execute(sql.SQL('REINDEX DATABASE {}').format(sql.Identifier(name)))
        if recorded is None:  # musl reports no version and PostgreSQL refuses to refresh one it never recorded
            connection.execute('UPDATE pg_database SET datcollversion = %s WHERE datname = %s', (actual, name))
        else:
            connection.execute(sql.SQL('ALTER DATABASE {} REFRESH COLLATION VERSION').format(sql.Identifier(name)))


def record_version(connection):
    """Store the installed package version so the Grafana dashboard can show what is running."""
    try:
        version = metadata.version('garmin-grafana')
    except metadata.PackageNotFoundError:
        version = 'unknown'
    with connection.transaction(), connection.cursor() as cur:
        cur.execute("INSERT INTO app_info(key,value) VALUES ('version',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (version,))


def configure_reader(connection):
    """Optional explicit deployment bootstrap for Grafana's SELECT-only login."""
    password = os.getenv('PGREADER_PASSWORD')
    if not password:
        return
    username = os.getenv('PGREADER_USER', 'grafana_reader')
    with connection.transaction(), connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(782643901)')
        cur.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (username,))
        if not cur.fetchone():
            cur.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(username)))
        cur.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(username), sql.Literal(password)))
        cur.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(connection.info.dbname), sql.Identifier(username)))
        cur.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(username)))
        cur.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(username)))
        cur.execute(sql.SQL('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}').format(sql.Identifier(username)))


def main():
    update_extension()
    rebuild_indexes()
    with connect() as connection:
        migrate(connection)
        record_version(connection)
        configure_reader(connection)


if __name__ == '__main__':
    main()
