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


def _quote(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def script():
    """The same rows and the typed views as one SQL script, for databases with no Python at hand (the browser's PGlite).
    Generated on demand from the installed SDK and never committed."""
    from . import views
    from .profile import dumps
    from .profile import Profile
    document = build_profile()
    version = document['sdk_version']
    lines = [f"INSERT INTO fit.profile_versions(sdk_version, profile_version) VALUES ({_quote(version)}, {_quote(document['version'])}) ON CONFLICT DO NOTHING;"]
    for name, values in document['types'].items():
        lines += [f'INSERT INTO fit.profile_types VALUES ({_quote(version)}, {_quote(name)}, {raw}, {_quote(value)});' for raw, value in values.items()]
    for num, message in document['messages'].items():
        lines.append(f"INSERT INTO fit.profile_messages VALUES ({_quote(version)}, {num}, {_quote(message['name'])});")
        for fnum, field in message['fields'].items():
            units = field['units'] if isinstance(field['units'], str) else ','.join(field['units'])
            lines.append(f"INSERT INTO fit.profile_fields VALUES ({_quote(version)}, {num}, {fnum}, {_quote(field['name'])}, {_quote(field['type'])}, "
                         f"{_quote(field['base_type'])}, {_quote(field['array'])}, {field['scale'][0]!r}, {field['offset'][0]!r}, {_quote(units)}, "
                         f"{_quote(dumps(field))}::jsonb);")
    profile = Profile(document)
    lines += [views.view_sql(name, number, profile) + ';' for name, number in views.VIEWS.items()]
    lines.append(views.DEV_VIEW + ';')
    return '\n'.join(lines) + '\n'
