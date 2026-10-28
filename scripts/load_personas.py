"""Load the synthetic test personas (tests/personas.py) into a development database.

Each persona becomes its own Account in Grafana. Run with PG* settings for a
disposable database, never your real health database; it writes data but never
deletes any. Today in the personas is 2026-10-28, so browse around that date.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from personas import PERSONAS  # noqa: E402
from garmin_grafana.storage import PostgresStore  # noqa: E402


def main():
    for persona in (factory() for factory in PERSONAS):
        store = PostgresStore(account=persona.account)
        try:
            print(f'{persona.account}: {store.write(persona.records)} records')
        finally:
            store.close()


if __name__ == '__main__':
    main()
