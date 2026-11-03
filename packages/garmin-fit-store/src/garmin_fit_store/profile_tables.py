"""Load the profile document into the fit.profile_* tables, once per SDK version (a bump adds rows, never rewrites)."""
from psycopg.types.json import Jsonb

from .profile import build_profile


def load(connection):
    document = build_profile()
    version = document['sdk_version']
    with connection.cursor() as cur:
        cur.execute('INSERT INTO fit.profile_versions(sdk_version, profile_version) VALUES (%s, %s) ON CONFLICT DO NOTHING',
                    (version, document['version']))
        if cur.rowcount == 0:
            return False
        cur.executemany('INSERT INTO fit.profile_types VALUES (%s,%s,%s,%s)', [
            (version, name, int(raw), value) for name, values in document['types'].items() for raw, value in values.items()])
        cur.executemany('INSERT INTO fit.profile_messages VALUES (%s,%s,%s)', [
            (version, int(num), message['name']) for num, message in document['messages'].items()])
        rows = []
        for num, message in document['messages'].items():
            for fnum, field in message['fields'].items():
                units = field['units']
                rows.append((version, int(num), int(fnum), field['name'], field['type'], field['base_type'], field['array'],
                             field['scale'][0], field['offset'][0], units if isinstance(units, str) else ','.join(units),
                             Jsonb(field)))
        cur.executemany('INSERT INTO fit.profile_fields VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)
    return True
