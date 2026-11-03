"""Convert Garmin FIT files to and from PostgreSQL + TimescaleDB without losing data."""
from pathlib import Path

from .decoder import decode
from .encoder import encode
from .ir import FitFile, canonical
from .protocol import FitError

MIGRATIONS = Path(__file__).with_name('migrations')

__all__ = ['FitError', 'FitFile', 'MIGRATIONS', 'canonical', 'decode', 'encode']
