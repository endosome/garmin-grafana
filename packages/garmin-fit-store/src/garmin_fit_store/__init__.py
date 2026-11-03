"""Convert Garmin FIT files to and from PostgreSQL + TimescaleDB without losing data."""
from pathlib import Path

from .decoder import decode
from .encoder import encode
from .ir import FitFile, canonical
from .protocol import FitError

MIGRATIONS = Path(__file__).with_name('migrations')

__all__ = ['after_migrate', 'FitError', 'FitFile', 'MIGRATIONS', 'canonical', 'decode', 'encode']


def after_migrate(connection):
    """Runs after the fit/*.sql files: loads the profile rows for the installed SDK version and builds the typed views."""
    from . import profile_tables, views
    loaded = profile_tables.load(connection)
    if loaded or not views.exist(connection):
        views.create(connection)
