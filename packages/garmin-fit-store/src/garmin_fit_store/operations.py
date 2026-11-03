"""The edit primitives as SQL (migration fit/004, fit/005) with a Python reference implementation on the IR.

Each primitive copies the source file into a new ``fit.files`` row (``parent_id``, ``operation``), edits the copy and
returns the new id with its encoded bytes stored; the original row is never changed. The reference implementations
exist to check the SQL: the R4 oracle must hold between the two.
"""
import copy
import re
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from . import store
from .ir import FitFile, Message
from .profile import Profile

ABSOLUTE = 0x10000000
FIT_EPOCH = 631065600


def _derive(connection, query, params):
    file_id = connection.execute(query, params).fetchone()[0]
    store.materialize(connection, file_id)
    return file_id


def time_shift(connection, file_id, seconds):
    """Time Adjuster: every calendar-time field of every message moves by ``seconds``."""
    return _derive(connection, 'SELECT fit.time_shift(%s::bigint, %s::bigint)', (file_id, seconds))


def change_device(connection, file_id, manufacturer=None, product=None, serial_number=None):
    """Device Changer: manufacturer and garmin_product by their profile names, serial number as a number."""
    return _derive(connection, 'SELECT fit.change_device(%s::bigint, %s::text, %s::text, %s::bigint)', (file_id, manufacturer, product, serial_number))


def remove_section(connection, file_id, start, end, close_gap=True):
    """Section Remover: drop the records in [start, end); close the gap or leave it; recompute laps and sessions."""
    return _derive(connection, 'SELECT fit.remove_section(%s::bigint, %s::timestamptz, %s::timestamptz, %s::boolean)', (file_id, start, end, close_gap))


def merge_fields(connection, target_id, donor_id, fields, tolerance_seconds=1.0):
    """Field Merger: copy the named fields from the donor's records to the target's by nearest time within the tolerance."""
    return _derive(connection, 'SELECT fit.merge_fields(%s::bigint, %s::bigint, %s::integer[], %s::numeric)', (target_id, donor_id, [int(f) for f in fields], tolerance_seconds))


def combine(connection, file_ids):
    """File Combiner: one file from several, ordered by time."""
    return _derive(connection, 'SELECT fit.combine(%s::bigint[])', ([int(i) for i in file_ids],))


# ---- reference implementations on the IR ----

def _carriers(profile):
    out = {}
    for number, message in profile.document['messages'].items():
        fields = [int(n) for n, f in message['fields'].items()
                  if f['type'] in ('date_time', 'local_date_time') and f['name'] != 'system_timestamp']
        out[int(number)] = fields
    return out


def _shift_value(value, delta, threshold=None):
    if isinstance(value, list):
        return [_shift_value(v, delta, threshold) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= max(ABSOLUTE, threshold or 0):
        return int(value) + delta
    return value


def _dev_timestamp_keys(fit_file, profile):
    description = profile.message_number('field_description')
    native = profile.field_number('field_description', 'native_field_num')
    return {(m.fields[0], m.fields[1]) for m in fit_file.messages
            if m.global_num == description and m.fields.get(native) == 253 and 0 in m.fields and 1 in m.fields}


def ref_copy(fit_file, delta=0, delta_from=None, drop=None):
    """Mirror of fit.copy_file: times as FIT seconds; drop is a (from, to) pair of FIT seconds."""
    profile = Profile()
    carriers = _carriers(profile)
    dev_keys = _dev_timestamp_keys(fit_file, profile)
    record = profile.message_number('record')
    out = FitFile(fit_file.protocol, fit_file.profile, list(fit_file.definitions), [])
    for m in fit_file.messages:
        stamp = m.fields.get(253)
        time = stamp if isinstance(stamp, int) and stamp >= ABSOLUTE else None
        if drop and m.global_num == record and time is not None and drop[0] <= time < drop[1]:
            continue
        d = delta if delta_from is None or time is None or time >= delta_from else 0
        fields = dict(m.fields)
        if d:
            for number in {*carriers.get(m.global_num, []), 253}:
                if number in fields:
                    fields[number] = _shift_value(fields[number], d, delta_from)
        dev = dict(m.dev)
        if d:
            for key in dev_keys:
                if key in dev:
                    dev[key] = _shift_value(dev[key], d, delta_from)
        out.messages.append(Message(m.seq, m.def_id, m.global_num, fields, dev))
    return out


def ref_time_shift(fit_file, seconds):
    return ref_copy(fit_file, delta=seconds)


def ref_change_device(fit_file, manufacturer=None, product=None, serial_number=None):
    profile = Profile()
    types = profile.document['types']

    def raw(type_name, value):
        return next((int(r) for r, n in types[type_name].items() if n == value), None)
    mfr = raw('manufacturer', manufacturer) if manufacturer is not None else None
    prod = None
    if product is not None:
        prod = raw('garmin_product', product)
        if prod is None and product.isdigit():
            prod = int(product)
    if manufacturer is not None and mfr is None:
        raise ValueError(f'unknown manufacturer {manufacturer}')
    if product is not None and prod is None:
        raise ValueError(f'unknown product {product}')
    out = copy.deepcopy(fit_file)
    file_id, device_info = profile.message_number('file_id'), profile.message_number('device_info')
    index_field = profile.field_number('device_info', 'device_index')
    defined = {d.id: {f[0] for f in d.fields} for d in out.definitions}

    def set_field(m, message, name, value):
        if value is None:
            return
        try:
            number = profile.field_number(message, name)
        except KeyError:
            return
        if number in m.fields or number in defined[m.def_id]:
            m.fields[number] = value
    for m in out.messages:
        if m.global_num == file_id:
            targets = ('file_id', ('manufacturer', mfr), ('product', prod), ('serial_number', serial_number))
        elif m.global_num == device_info and m.fields.get(index_field, 0) == 0:
            targets = ('device_info', ('manufacturer', mfr), ('product', prod), ('serial_number', serial_number), ('garmin_product', prod))
        else:
            continue
        for name, value in targets[1:]:
            set_field(m, 'file_id' if m.global_num == file_id else 'device_info', name, value)
    return out


def _round(value):
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def ref_recompute_totals(fit_file, removed=None, closed=False):
    """Mirror of fit.recompute_totals, on a file whose records are already the remaining ones."""
    profile = Profile()
    record_number = profile.message_number('record')
    records = [(m.fields[253], m.fields) for m in fit_file.messages
               if m.global_num == record_number and isinstance(m.fields.get(253), int) and m.fields[253] >= ABSOLUTE]
    rec_dist = profile.field_number('record', 'distance')
    record_fields = {f['name']: (int(n), f) for n, f in profile.document['messages'][str(record_number)]['fields'].items()}
    for summary in ('lap', 'session'):
        number = profile.message_number(summary)
        names = {f['name']: (int(n), f) for n, f in profile.document['messages'][str(number)]['fields'].items()}
        start_n, end_n = names['start_time'][0], names['timestamp'][0]
        for m in fit_file.messages:
            if m.global_num != number or start_n not in m.fields or end_n not in m.fields:
                continue
            s, t, overlap = m.fields[start_n], m.fields[end_n], 0
            if removed:
                lo, hi = removed
                if lo <= s < hi:
                    s = hi
                if lo < t < hi:
                    t = lo
                if not closed:
                    overlap = max(0, min(t, hi) - max(s, lo))
            t = max(t, s)
            m.fields[start_n], m.fields[end_n] = s, t
            if names['total_elapsed_time'][0] in m.fields:
                m.fields[names['total_elapsed_time'][0]] = (t - s) * 1000
            if names['total_timer_time'][0] in m.fields:
                m.fields[names['total_timer_time'][0]] = (t - s - overlap) * 1000
            inside = [f for time, f in records if s <= time <= t]
            if names['total_distance'][0] in m.fields:
                distances = [(time, f[rec_dist]) for time, f in records if s <= time <= t and rec_dist in f]
                if distances:
                    m.fields[names['total_distance'][0]] = sorted(distances)[-1][1] - sorted(distances)[0][1]
            for name, (target, field) in names.items():
                found = re.match(r'^(enhanced_)?(avg|max|min)_', name)
                if not found or field['array'] or target not in m.fields:
                    continue
                source = (found.group(1) or '') + name[found.end():]
                if source not in record_fields:
                    continue
                source_number, source_field = record_fields[source]
                if source_field['array'] or source_field['scale'] != field['scale'] or source_field['offset'] != field['offset']:  # pragma: no cover
                    continue
                values = [f[source_number] for f in inside if source_number in f]
                if not values:
                    continue
                kind = found.group(2)
                m.fields[target] = _round(Decimal(sum(values)) / len(values)) if kind == 'avg' else (max(values) if kind == 'max' else min(values))
    return fit_file


def ref_remove_section(fit_file, start, end, close_gap=True):
    """Times as FIT seconds."""
    delta = -(end - start) if close_gap else 0
    out = ref_copy(fit_file, delta=delta, delta_from=end, drop=(start, end))
    if not close_gap:
        return ref_recompute_totals(out, removed=(start, end), closed=False)
    _clamp_summaries(fit_file, out, start, end)
    _close_distance(out, start)
    return ref_recompute_totals(out)


def _clamp_summaries(original, out, start, end):
    """Mirror of the lap and session step in fit.remove_section (close_gap): clamp in the original time, then shift."""
    profile = Profile()
    for name in ('lap', 'session'):
        number = profile.message_number(name)
        start_n, end_n = profile.field_number(name, 'start_time'), profile.field_number(name, 'timestamp')
        pairs = zip([m for m in original.messages if m.global_num == number], [m for m in out.messages if m.global_num == number])
        for old, new in pairs:
            if start_n not in old.fields or end_n not in old.fields:
                continue
            s, t = old.fields[start_n], old.fields[end_n]
            if start <= s < end:
                s = start
            if start < t < end:
                t = start
            t = max(t, s)
            new.fields[start_n] = s - (end - start) if s >= end else s
            new.fields[end_n] = t - (end - start) if t >= end else t
            if 253 in new.fields:
                new.fields[253] = new.fields[end_n]


def _close_distance(fit_file, start):
    """Mirror of the distance step in fit.remove_section: records from `start` on continue from the last one before it."""
    profile = Profile()
    record = profile.message_number('record')
    distance = profile.field_number('record', 'distance')
    rows = [(m.fields[253], i, m) for i, m in enumerate(fit_file.messages)
            if m.global_num == record and isinstance(m.fields.get(253), int) and m.fields[253] >= ABSOLUTE
            and isinstance(m.fields.get(distance), (int, float))]
    before = [r for r in rows if r[0] < start]
    after = sorted(r for r in rows if r[0] >= start)
    if not before or not after:
        return
    gap = after[0][2].fields[distance] - max(before, key=lambda r: (r[0], r[1]))[2].fields[distance]
    if gap > 0:
        for _, _, m in after:
            m.fields[distance] -= gap


def fit_seconds(moment):
    """FIT seconds of a datetime (naive means UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) - FIT_EPOCH


def ref_merge_fields(target, donor, field_numbers, tolerance=1):
    """Mirror of fit.merge_fields."""
    profile = Profile()
    record = profile.message_number('record')
    added = []
    for k in field_numbers:
        layout = next((f for d in sorted(donor.definitions, key=lambda d: d.id) if d.global_num == record for f in d.fields if f[0] == k), None)
        if layout is not None:
            added.append(layout)
    out = copy.deepcopy(target)
    used = {m.def_id for m in out.messages if m.global_num == record and isinstance(m.fields.get(253), int) and m.fields[253] >= ABSOLUTE}
    remap = {}
    for d in sorted(out.definitions, key=lambda d: d.id):
        if d.id in used and d.global_num == record or d.id in used:
            fields = tuple(f for f in d.fields if f[0] not in {a[0] for a in added}) + tuple(added)
            remap[d.id] = out.add_definition(d.global_num, fields, d.dev_fields, d.arch).id
    donors = [(m.fields[253], m.seq, m.fields) for m in donor.messages
              if m.global_num == record and isinstance(m.fields.get(253), int) and m.fields[253] >= ABSOLUTE]
    for m in out.messages:
        time = m.fields.get(253)
        if m.global_num != record or not isinstance(time, int) or time < ABSOLUTE:
            continue
        near = sorted((abs(t - time), seq, f) for t, seq, f in donors if abs(t - time) <= tolerance)
        m.def_id = remap[m.def_id]
        if near:
            m.fields.update({k: v for k, v in near[0][2].items() if k in set(field_numbers)})
    return out


def ref_combine(files):
    """Mirror of fit.combine, on IRs given in time order of their first timestamp."""
    profile = Profile()
    num = {name: profile.message_number(name) for name in ('record', 'lap', 'session', 'activity', 'file_id', 'developer_data_id', 'field_description')}
    out = FitFile(files[0].protocol, files[0].profile, [], [])
    rows = []
    seen_dev = []
    for rank, source in enumerate(files):
        for m in source.messages:
            if m.global_num == num['activity'] or (m.global_num == num['file_id'] and rank > 0):
                continue
            if rank > 0 and m.global_num in (num['developer_data_id'], num['field_description']) and (m.global_num, m.fields) in seen_dev:
                continue
            if m.global_num in (num['developer_data_id'], num['field_description']):
                seen_dev.append((m.global_num, m.fields))
            d = source.definitions[m.def_id]
            rows.append((m.global_num != num['file_id'], rank, m.seq, m, d))
    rows.sort(key=lambda r: r[:3])
    for _, _, _, m, d in rows:
        new_def = out.add_definition(d.global_num, d.fields, d.dev_fields, d.arch)
        out.messages.append(Message(len(out.messages), new_def.id, m.global_num, dict(m.fields), dict(m.dev)))
    for summary in ('lap', 'session'):
        for i, m in enumerate(x for x in out.messages if x.global_num == num[summary]):
            if 254 in m.fields:
                m.fields[254] = i
    ref_recompute_totals(out)
    sessions = [m for m in out.messages if m.global_num == num['session']]
    timer_n = profile.field_number('session', 'total_timer_time')
    timer = sum(m.fields.get(timer_n, 0) for m in sessions)
    times = [m.fields[253] for m in sessions if 253 in m.fields] or [m.fields[253] for m in out.messages if m.global_num == num['record'] and 253 in m.fields]
    last = max(times)
    template = next(((source, m) for source in reversed(files) for m in reversed(source.messages) if m.global_num == num['activity']), None)
    fields = dict(template[1].fields) if template else {}
    fields.update({253: last, profile.field_number('activity', 'num_sessions'): len(sessions), profile.field_number('activity', 'total_timer_time'): timer})
    if template:
        d = template[0].definitions[template[1].def_id]
        definition = out.add_definition(d.global_num, d.fields, d.dev_fields, d.arch)
    else:
        definition = out.add_definition(num['activity'], [(253, 4, 0x86), (profile.field_number('activity', 'num_sessions'), 2, 0x84),
                                                         (profile.field_number('activity', 'total_timer_time'), 4, 0x86)])
    allowed = {f[0] for f in definition.fields}
    out.messages.append(Message(len(out.messages), definition.id, num['activity'], {k: v for k, v in fields.items() if k in allowed}, {}))
    return out
