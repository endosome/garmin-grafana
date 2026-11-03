"""Typed, named SQL views over the raw tables, generated from the profile document at migrate time (plan R10, R14).

Columns are the FIT field names in SI units, positions in degrees and times as timestamptz. Nothing here is committed
as data: a profile upgrade rebuilds the views from the new document.
"""
import hashlib

from psycopg import sql

from .profile import Profile
from .protocol import EPOCH_OFFSET

VIEWS = {'v_record': 20, 'v_lap': 19, 'v_session': 18, 'v_event': 21, 'v_file_id': 0, 'v_device_info': 23}
SEMICIRCLES = '(180.0 / 2147483648)'
FLOATS = {'float32', 'float64'}
INTEGERS = {'sint8', 'uint8', 'sint16', 'uint16', 'sint32', 'uint32', 'uint8z', 'uint16z', 'uint32z', 'sint64', 'enum', 'byte'}


# record fields stored as generated bigint columns (migration fit/006); the view reads these instead of parsing the JSONB
PROMOTED = {0: 'f0', 1: 'f1', 2: 'f2', 3: 'f3', 4: 'f4', 5: 'f5', 6: 'f6', 7: 'f7', 13: 'f13', 73: 'f73', 78: 'f78'}


def column_expression(field, profile, promoted=None):
    """The SQL expression for one field of a message, over the jsonb column ``fields``; None when it has no column."""
    number, kind, base = field['number'], field['type'], field['base_type']
    raw = f"fit.num(fields->'{number}')"
    if promoted and number in promoted:  # the stored column, or the JSONB when the column could not hold the value
        raw = f"coalesce(CASE WHEN abs({promoted[number]}) < 9e18 THEN {promoted[number]} END, {raw})"
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
        columns.append(f'{column_expression(field, profile, PROMOTED if message_number == 20 else None)} AS {sql.Identifier(field["name"]).as_string(None)}')
    where = '' if message_number == 20 else f' WHERE global_num = {message_number}'
    return f'CREATE VIEW fit.{name} AS SELECT {", ".join(columns)} FROM {table}{where}'


DEV_VIEW = """CREATE VIEW fit.v_record_dev AS
 SELECT r.file_id, r.seq, r.time,
        (SELECT jsonb_object_agg(coalesce(d.name, e.key), e.value) FROM jsonb_each(r.dev) e
         LEFT JOIN fit.v_dev_fields d ON d.file_id = r.file_id AND d.dev_key = e.key) AS dev
 FROM fit.records r"""


def definitions(profile=None):
    profile = profile or Profile()
    return {**{name: view_sql(name, number, profile) for name, number in VIEWS.items()}, 'v_record_dev': DEV_VIEW}


def signature():
    return hashlib.sha256('\n'.join(definitions().values()).encode()).hexdigest()


def create(connection):
    """(Re)create every typed view from the installed profile, remembering what they were generated from."""
    with connection.cursor() as cur:
        for name in definitions():
            cur.execute(f'DROP VIEW IF EXISTS fit.{name}')
        for name, text in definitions().items():
            cur.execute(text)
        cur.execute(sql.SQL('COMMENT ON VIEW fit.v_record IS {}').format(sql.Literal(signature())))


def exist(connection):
    """True when every view exists and was generated from the current profile and view code."""
    names = list(definitions())
    row = connection.execute("SELECT count(*) FROM pg_views WHERE schemaname = 'fit' AND viewname = ANY(%s)", (names,)).fetchone()
    comment = connection.execute("SELECT obj_description('fit.v_record'::regclass)").fetchone()[0] if row[0] == len(names) else None
    return row[0] == len(names) and comment == signature()
