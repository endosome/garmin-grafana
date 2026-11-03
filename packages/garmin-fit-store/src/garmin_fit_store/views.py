"""Typed, named SQL views over the raw tables, generated from the profile document at migrate time (plan R10, R14).

Columns are the FIT field names in SI units, positions in degrees and times as timestamptz. Nothing here is committed
as data: a profile upgrade rebuilds the views from the new document.
"""
from psycopg import sql

from .profile import Profile
from .protocol import EPOCH_OFFSET

VIEWS = {'v_record': 20, 'v_lap': 19, 'v_session': 18, 'v_event': 21, 'v_file_id': 0, 'v_device_info': 23}
SEMICIRCLES = '(180.0 / 2147483648)'
FLOATS = {'float32', 'float64'}
INTEGERS = {'sint8', 'uint8', 'sint16', 'uint16', 'sint32', 'uint32', 'uint8z', 'uint16z', 'uint32z', 'sint64', 'enum', 'byte'}


def column_expression(field, profile):
    """The SQL expression for one field of a message, over the jsonb column ``fields``; None when it has no column."""
    number, kind, base = field['number'], field['type'], field['base_type']
    raw = f"fit.num(fields->'{number}')"
    if field['array']:
        return f"fields->'{number}'"
    if base == 'string':
        return f"CASE jsonb_typeof(fields->'{number}') WHEN 'array' THEN fields->'{number}'->>0 ELSE fields->>'{number}' END"
    if kind == 'date_time':
        return f'fit.fit_time({raw}::bigint)'
    if kind == 'local_date_time':
        return f"(to_timestamp({raw}::bigint + {EPOCH_OFFSET}) AT TIME ZONE 'UTC')"
    if profile.type_values(kind) is not None and kind != 'message_index' and not kind.endswith('flags'):
        return f'coalesce(fit.type_name({kind!r}, {raw}::bigint), {raw}::bigint::text)'
    scale, offset, units = field['scale'], field['offset'], field['units']
    scaled = len(scale) == 1 and (scale[0] != 1 or offset[0])
    if units == 'semicircles' and not field['components']:
        return f'{raw}::double precision * {SEMICIRCLES}'
    if scaled:
        return f'({raw}::double precision / {scale[0]!r} - {offset[0]!r})'
    if base in FLOATS:
        return f'{raw}::double precision'
    if base in INTEGERS:
        return f'{raw}::bigint'
    return f'{raw}::numeric'


def view_sql(name, message_number, profile):
    message = profile.message(message_number)
    table = 'fit.records' if message_number == 20 else 'fit.messages'
    columns, seen = ['file_id', 'seq', 'time'], {'file_id', 'seq', 'time', 'timestamp'}
    for number in sorted(message['fields'], key=int):
        field = message['fields'][number]
        if field['name'] in seen:
            continue
        seen.add(field['name'])
        columns.append(f'{column_expression(field, profile)} AS {sql.Identifier(field["name"]).as_string(None)}')
    where = '' if message_number == 20 else f' WHERE global_num = {message_number}'
    return f'CREATE VIEW fit.{name} AS SELECT {", ".join(columns)} FROM {table}{where}'


DEV_VIEW = """CREATE VIEW fit.v_record_dev AS
 SELECT r.file_id, r.seq, r.time,
        (SELECT jsonb_object_agg(coalesce(d.name, e.key), e.value) FROM jsonb_each(r.dev) e
         LEFT JOIN fit.v_dev_fields d ON d.file_id = r.file_id AND d.dev_key = e.key) AS dev
 FROM fit.records r"""


def create(connection):
    """(Re)create every typed view from the installed profile."""
    profile = Profile()
    with connection.cursor() as cur:
        for name in [*VIEWS, 'v_record_dev']:
            cur.execute(f'DROP VIEW IF EXISTS fit.{name}')
        for name, number in VIEWS.items():
            cur.execute(view_sql(name, number, profile))
        cur.execute(DEV_VIEW)


def exist(connection):
    names = [*VIEWS, 'v_record_dev']
    row = connection.execute("SELECT count(*) FROM pg_views WHERE schemaname = 'fit' AND viewname = ANY(%s)", (names,)).fetchone()
    return row[0] == len(names)
