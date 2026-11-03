"""Regenerate tests/corpus/*.fit and the expected *.jsonl: `uv run python tests/generate_corpus.py` in this package."""
from pathlib import Path

from corpus_files import CORPUS
from garmin_fit_store import decode, ir

DIRECTORY = Path(__file__).parent / 'corpus'

if __name__ == '__main__':
    DIRECTORY.mkdir(exist_ok=True)
    for name, build in CORPUS.items():
        data = build()
        (DIRECTORY / f'{name}.fit').write_bytes(data)
        (DIRECTORY / f'{name}.jsonl').write_text(ir.dumps(decode(data, check_crc=False)))
