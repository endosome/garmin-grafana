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
