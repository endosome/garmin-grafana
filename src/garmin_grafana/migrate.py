"""Apply ordered SQL migrations explicitly; safe for concurrent deployment startups."""
import hashlib
import logging
import os
import time
from importlib import metadata
import psycopg
from psycopg import errors, sql
from pathlib import Path
from .storage import connect

logger = logging.getLogger(__name__)


def _migration_sets():
    """(name prefix, directory, hook that runs after the set) for the main package and every workspace member."""
    import garmin_fit_store
    yield '', Path(__file__).with_name('migrations'), None
    yield 'fit/', garmin_fit_store.MIGRATIONS, garmin_fit_store.after_migrate


def migrate(connection):
    with connection.transaction(), connection.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(782643901)')
        cur.execute('CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for prefix, directory, after in _migration_sets():
            for path in sorted(directory.glob('*.sql')):
                sql = path.read_text()
                digest = hashlib.sha256(sql.encode()).hexdigest()
                cur.execute('SELECT checksum FROM schema_migrations WHERE name=%s', (prefix + path.name,))
                row = cur.fetchone()
                if row:
                    if row[0] != digest:
                        raise RuntimeError(f'Applied migration changed: {prefix}{path.name}')
                    continue
                cur.execute(sql)
                cur.execute('INSERT INTO schema_migrations(name,checksum) VALUES (%s,%s)', (prefix + path.name, digest))
            if after:
                after(connection)


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


def _transient(error):
    """A catalog update that collided with a TimescaleDB background job, or another lock conflict that succeeds on a retry."""
    if isinstance(error, (errors.SerializationFailure, errors.DeadlockDetected, errors.LockNotAvailable)):
        return True
    return isinstance(error, errors.InternalError_) and error.sqlstate == 'XX000' and 'concurrently updated' in str(error)


def _retrying(connection, action, attempts=6, delay=0.5):
    """Run action(cursor) in a transaction of its own, again after a short backoff while it hits a transient conflict."""
    attempt = 1
    while True:
        try:
            with connection.transaction(), connection.cursor() as cur:
                return action(cur)
        except psycopg.Error as error:
            if attempt == attempts or not _transient(error):
                raise
            logger.warning('Reader grants collided with a background job (%s); retrying (%d/%d)', str(error).strip(), attempt, attempts - 1)
            time.sleep(delay * 2 ** (attempt - 1))
            attempt += 1


def _reader_settings():
    password = os.getenv('PGREADER_PASSWORD')
    if not password:
        return None
    return password, os.getenv('PGREADER_USER', 'grafana_reader'), os.getenv('PGFETCH_REQUEST_USER', 'grafana_fetch_request')


def prepare_reader(connection):
    """Create the SELECT-only and INSERT-only logins and make tables created from now on readable, so it can run before
    the migrations: the rollups they create are then granted when they are created, not afterwards while the
    refresh jobs that start with them are updating the catalog."""
    settings = _reader_settings()
    if not settings:
        return
    password, username, requester = settings

    def apply(cur):
        cur.execute('SELECT pg_advisory_xact_lock(782643901)')
        for role in (username, requester):
            cur.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,))
            if not cur.fetchone():
                cur.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
            cur.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
            cur.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(connection.info.dbname), sql.Identifier(role)))
            cur.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        cur.execute(sql.SQL('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}').format(sql.Identifier(username)))
    _retrying(connection, apply)


def configure_reader(connection):
    """Optional explicit deployment bootstrap for Grafana's SELECT-only login and its "Fetch now" button's
    INSERT-only login (same password), which can add rows to fetch_requests and nothing else.

    Runs on every start, so it only grants what is missing: a blanket GRANT ... ON ALL TABLES rewrites every table's ACL
    in pg_class and collided with the continuous-aggregate refresh jobs ("tuple concurrently updated")."""
    settings = _reader_settings()
    if not settings:
        return
    _, username, requester = settings
    prepare_reader(connection)

    def apply(cur):
        cur.execute('SELECT pg_advisory_xact_lock(782643901)')
        cur.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT has_table_privilege(%s, c.oid, 'SELECT')
            ORDER BY c.relname""", (username,))
        missing = [row[0] for row in cur.fetchall()]
        if missing:
            cur.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.SQL(', ').join(sql.Identifier('public', name) for name in missing), sql.Identifier(username)))
        cur.execute("SELECT has_table_privilege(%s, 'fetch_requests', 'INSERT'), has_sequence_privilege(%s, 'fetch_requests_id_seq', 'USAGE')", (requester, requester))
        can_insert, can_use = cur.fetchone()
        if not can_insert:
            cur.execute(sql.SQL('GRANT INSERT ON fetch_requests TO {}').format(sql.Identifier(requester)))
        if not can_use:
            cur.execute(sql.SQL('GRANT USAGE ON SEQUENCE fetch_requests_id_seq TO {}').format(sql.Identifier(requester)))
    _retrying(connection, apply)


def main():
    update_extension()
    rebuild_indexes()
    with connect() as connection:
        prepare_reader(connection)
        migrate(connection)
        record_version(connection)
        configure_reader(connection)


if __name__ == '__main__':
    main()
