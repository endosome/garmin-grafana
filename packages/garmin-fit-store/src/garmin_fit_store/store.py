"""IR <-> the fit.* tables: import, export and verify. One file per transaction."""
import hashlib
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from . import decoder, encoder
from .ir import TIMESTAMP_FIELD, Definition, FitFile, Message, canonical, dev_key, parse_dev_key
from .profile import sdk_version
from .protocol import EPOCH_OFFSET

BATCH = 1000
ABSOLUTE = 0x10000000  # date_time values below this are relative to power on, not a calendar time


def message_time(message):
    value = message.fields.get(TIMESTAMP_FIELD)
    if isinstance(value, int) and value >= ABSOLUTE:
        return datetime.fromtimestamp(value + EPOCH_OFFSET, timezone.utc)
    return None


def find_file(connection, data, source_id=None):
    digest = hashlib.sha256(data).digest()
    row = connection.execute('SELECT id FROM fit.files WHERE COALESCE(source_id, 0) = COALESCE(%s, 0) AND sha256 = %s AND parent_id IS NULL',
                             (source_id, digest)).fetchone()
    return row[0] if row else None


def import_file(connection, data, source_id=None, garmin_activity_id=None, check_crc=True):
    """Store a FIT file and return its fit.files id; bytes already stored for the source return the existing id."""
    data = bytes(data)
    existing = find_file(connection, data, source_id)
    if existing is not None:
        return existing
    fit_file = decoder.decode(data, check_crc=check_crc)
    with connection.transaction():
        file_id = insert_ir(connection, fit_file, data=data, source_id=source_id, garmin_activity_id=garmin_activity_id)
    return file_id


def insert_ir(connection, fit_file, data=None, source_id=None, garmin_activity_id=None, parent_id=None, operation=None):
    """Write an IR as a new file row (original bytes optional: an edited file has none until it is exported)."""
    data = data if data is not None else encoder.encode(fit_file)
    times = [t for t in map(message_time, fit_file.messages) if t is not None]
    with connection.cursor() as cur:
        cur.execute("""INSERT INTO fit.files(source_id, sha256, garmin_activity_id, parent_id, operation, protocol_version,
                       profile_number, sdk_version, message_count, start_time, end_time)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (source_id, hashlib.sha256(data).digest(), garmin_activity_id, parent_id,
                     Jsonb(operation) if operation is not None else None, fit_file.protocol, fit_file.profile,
                     sdk_version(), len(fit_file.messages), min(times, default=None), max(times, default=None)))
        file_id = cur.fetchone()[0]
        cur.execute('INSERT INTO fit.blobs VALUES (%s,%s)', (file_id, data))
        cur.executemany('INSERT INTO fit.definitions VALUES (%s,%s,%s,%s,%s,%s)', [
            (file_id, d.id, d.global_num, d.arch, Jsonb([list(f) for f in d.fields]), Jsonb([list(f) for f in d.dev_fields]))
            for d in fit_file.definitions])
        plain, records = [], []
        for message in fit_file.messages:
            time = message_time(message)
            row = [file_id, message.seq, message.def_id, message.global_num, time,
                   Jsonb({str(k): v for k, v in message.fields.items()}), Jsonb({dev_key(*k): v for k, v in message.dev.items()})]
            if message.global_num == 20 and time is not None:
                records.append(row[:3] + row[4:])
            else:
                plain.append(row)
            if len(plain) >= BATCH:
                _flush_messages(cur, plain)
            if len(records) >= BATCH:
                _flush_records(cur, records)
        _flush_messages(cur, plain)
        _flush_records(cur, records)
    return file_id


def _flush_messages(cur, rows):
    if rows:
        cur.executemany('INSERT INTO fit.messages VALUES (%s,%s,%s,%s,%s,%s,%s)', rows)
        rows.clear()


def _flush_records(cur, rows):
    if rows:
        cur.executemany('INSERT INTO fit.records VALUES (%s,%s,%s,%s,%s,%s)', rows)
        rows.clear()


def load_ir(connection, file_id):
    """The stored file as an IR, in seq order."""
    row = connection.execute('SELECT protocol_version, profile_number FROM fit.files WHERE id = %s', (file_id,)).fetchone()
    if row is None:
        raise KeyError(f'no fit.files row {file_id}')
    fit_file = FitFile(protocol=row[0], profile=row[1])
    for def_id, global_num, arch, fields, dev_fields in connection.execute(
            'SELECT def_id, global_num, arch, fields, dev_fields FROM fit.definitions WHERE file_id = %s ORDER BY def_id', (file_id,)):
        fit_file.definitions.append(Definition(def_id, global_num, arch, tuple(map(tuple, fields)), tuple(map(tuple, dev_fields))))
    for seq, def_id, global_num, fields, dev in connection.execute(
            """SELECT seq, def_id, global_num, fields, dev FROM (
                 SELECT seq, def_id, global_num, fields, dev FROM fit.messages WHERE file_id = %(f)s
                 UNION ALL SELECT seq, def_id, 20, fields, dev FROM fit.records WHERE file_id = %(f)s) m ORDER BY seq""", {'f': file_id}):
        fit_file.messages.append(Message(seq, def_id, global_num, {int(k): v for k, v in fields.items()},
                                         {parse_dev_key(k): v for k, v in dev.items()}))
    return fit_file


def export_file(connection, file_id):
    return encoder.encode(load_ir(connection, file_id))


def verify(connection, file_id):
    """The R4 oracle against the stored blob: a list of differences, empty when the file round-trips."""
    blob = connection.execute('SELECT data FROM fit.blobs WHERE file_id = %s', (file_id,)).fetchone()
    if blob is None:
        return [f'file {file_id} has no stored bytes']
    original = canonical(decoder.decode(bytes(blob[0]), check_crc=False))
    stored = canonical(load_ir(connection, file_id))
    exported = canonical(decoder.decode(export_file(connection, file_id)))
    differences = []
    for label, other in (('stored rows', stored), ('exported file', exported)):
        if len(other) != len(original):
            differences.append(f'{label}: {len(other)} messages, the original has {len(original)}')
            continue
        differences += [f'{label}: message {i} differs' for i, (a, b) in enumerate(zip(original, other)) if a != b]
    return differences


def record_pending(connection, data, source_id=None, garmin_activity_id=None, error=''):
    """Remember a file that could not be stored; the same bytes for the same source are kept once."""
    with connection.transaction():
        connection.execute("""INSERT INTO fit.pending_imports(source_id, garmin_activity_id, data, error) VALUES (%s,%s,%s,%s)
                              ON CONFLICT (COALESCE(source_id, 0), sha256(data)) DO UPDATE
                              SET attempts = fit.pending_imports.attempts + 1, error = excluded.error, last_attempt_at = now()""",
                           (source_id, garmin_activity_id, bytes(data), error[:500]))


def retry_pending(connection, source_id=None):
    """Import the pending files of a source again; returns (stored, still failing)."""
    rows = connection.execute('SELECT id, garmin_activity_id, data FROM fit.pending_imports WHERE COALESCE(source_id, 0) = COALESCE(%s, 0) ORDER BY id',
                              (source_id,)).fetchall()
    stored = failing = 0
    for pending_id, activity_id, data in rows:
        try:
            import_file(connection, bytes(data), source_id, activity_id, check_crc=False)
        except Exception as error:
            failing += 1
            connection.execute('UPDATE fit.pending_imports SET attempts = attempts + 1, error = %s, last_attempt_at = now() WHERE id = %s',
                               (str(error)[:500], pending_id))
        else:
            stored += 1
            connection.execute('DELETE FROM fit.pending_imports WHERE id = %s', (pending_id,))
    return stored, failing


def materialize(connection, file_id):
    """Encode a derived file's rows and keep the bytes (and their hash) as its blob; returns the bytes."""
    data = export_file(connection, file_id)
    connection.execute('UPDATE fit.files SET sha256 = %s WHERE id = %s', (hashlib.sha256(data).digest(), file_id))
    connection.execute('INSERT INTO fit.blobs VALUES (%s, %s) ON CONFLICT (file_id) DO UPDATE SET data = excluded.data', (file_id, data))
    return data
