"""Apply ordered SQL migrations explicitly; safe for concurrent deployment startups."""
import hashlib
import os
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
    with connect() as connection:
        migrate(connection)
        configure_reader(connection)


if __name__ == '__main__':
    main()
