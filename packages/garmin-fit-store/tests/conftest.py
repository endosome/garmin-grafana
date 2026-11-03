import os
from pathlib import Path

import pytest

CORPUS = Path(__file__).parent / 'corpus'


def corpus_names():
    return sorted(p.stem for p in CORPUS.glob('*.fit'))


def external_files():
    """Official SDK fixtures (FIT_SDK_FIXTURES_DIR) and the opt-in real corpus (FIT_CORPUS_DIR); never committed."""
    files = []
    for variable in ('FIT_SDK_FIXTURES_DIR', 'FIT_CORPUS_DIR'):
        directory = os.getenv(variable)
        if directory:
            files += sorted(Path(directory).rglob('*.fit'))
    return files


@pytest.fixture(params=corpus_names())
def corpus_file(request):
    return request.param, (CORPUS / f'{request.param}.fit').read_bytes()


@pytest.fixture(params=external_files() or [None], ids=lambda p: p.name if p else 'none')
def external_file(request):
    if request.param is None:
        pytest.skip('Set FIT_SDK_FIXTURES_DIR or FIT_CORPUS_DIR')
    return request.param.read_bytes()


@pytest.fixture(scope='session')
def database():
    """A disposable database with the fit migrations applied (TEST_DATABASE=1 and PG*, as in the root package)."""
    if os.getenv('TEST_DATABASE') != '1':
        pytest.skip('Set TEST_DATABASE=1 with PG* pointing at a disposable TimescaleDB server')
    import uuid
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    import garmin_fit_store
    admin = psycopg.connect(autocommit=True)
    name = 'fit_test_' + uuid.uuid4().hex
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    settings = conninfo_to_dict(admin.info.dsn) | {'dbname': name}
    try:
        with psycopg.connect(**settings) as setup:
            for path in sorted(garmin_fit_store.MIGRATIONS.glob('*.sql')):
                setup.execute(path.read_text())
            garmin_fit_store.after_migrate(setup)
        yield settings
    finally:
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close()


@pytest.fixture
def connection(database):
    import psycopg
    with psycopg.connect(**database) as conn:
        yield conn
