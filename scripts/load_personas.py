"""Load the synthetic test personas (tests/personas.py) into a development database.

Each persona becomes its own Account in Grafana. Run with PG* settings for a disposable
database, never your real health database; it writes data but never deletes any. Today in
the personas is 2026-10-28, so browse around that date. The rollups are refreshed and each
account's "Last Fetch" is stamped with that time, so what a dashboard shows does not depend
on when this ran.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from personas import NOW, PERSONAS  # noqa: E402
from garmin_grafana.storage import PostgresStore, connect  # noqa: E402

ROLLUPS = ('samples_hourly', 'gps_cells_daily', 'hourly_medians', 'wear_hourly')


def main():
    for persona in (factory() for factory in PERSONAS):
        store = PostgresStore(account=persona.account)
        try:
            print(f'{persona.account}: {store.write(persona.records)} records')
            store.record_fetch()
            with store.connection.transaction(), store.connection.cursor() as cur:
                cur.execute('UPDATE app_info SET updated_at = %s WHERE key = %s', (NOW, f'last_fetch:{store.source_id(cur)}'))
        finally:
            store.close()
    with connect() as connection:
        connection.autocommit = True
        for view in ROLLUPS:
            connection.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


if __name__ == '__main__':
    main()
