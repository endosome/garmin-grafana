"""What every dashboard query returns, as a contract: column names and types, account isolation, several accounts at once,
and time zones and ranges. Needs TEST_DATABASE=1; the personas are loaded once per session (conftest.py).
"""
from datetime import timedelta
import json
import os
import re
import pytest
from panel_queries import FORMATS, ROOT, STATEMENTS, iso, queries
from personas import NOW, healthy_lifestyle, ski_tourer

SCHEMAS = ROOT / 'tests/data/panel_schemas.json'
RANGES = {'24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400, '1y': 365 * 86400, '5y': 5 * 365 * 86400}
ZONES = ('UTC', 'Asia/Kolkata', 'America/Los_Angeles')  # Kolkata: local midnight falls mid UTC hour; Los Angeles: DST
# Queries whose answer does not come from an account's data, so an account without data still gets a row.
DATA_FREE = {
    'Garmin-Ski-Season.json:1:A': 'the countdown to the next winter',
    'Garmin-Ski-Season.json:10:B': 'the weekly goal line, drawn for weeks without sessions',
    'Garmin-Health-Overview.json:41:A': 'the version of the application',
    'Garmin-Grafana-Dashboard.json:ActivityGPS': 'the dropdown placeholder',
    'Garmin-Strength-Training-Dashboard.json:ExerciseCategory': 'the dropdown placeholder',
    'Garmin-Strength-Training-Dashboard.json:ExerciseLabel': 'the dropdown placeholder',
}


def execute(reader, label, source, *, zone='UTC', seconds=None, interval=None, end=NOW):
    """Runs one dashboard query the way test_personas does; returns ([(column, type name)], rows)."""
    seconds = seconds or queries.pinned_ranges().get(label.rsplit(':', 1)[0]) or 400 * 86400
    sql = queries.expand_query(STATEMENTS[label], source, start=iso(end - timedelta(seconds=seconds)), end=iso(end), timezone=zone,
                               interval=interval or queries.interval(seconds))
    with reader.transaction(), reader.cursor() as cur:
        cur.execute(sql)
        types = reader.adapters.types
        columns = [(c.name, getattr(types.get(c.type_code), 'name', str(c.type_code))) for c in cur.description]
        return columns, cur.fetchall()


def test_result_columns_match_the_snapshot(personas, reader, request):
    """A renamed column fails here, in the same diff as the override that depends on it. Regenerate with --update-schemas."""
    sid = personas[healthy_lifestyle().account]
    found = {label: [list(c) for c in execute(reader, label, sid)[0]] for label in STATEMENTS}
    if request.config.getoption('--update-schemas'):
        SCHEMAS.write_text(json.dumps(found, indent=1, sort_keys=True) + '\n')
    recorded = json.loads(SCHEMAS.read_text())
    assert set(found) == set(recorded), 'queries added or removed: run pytest --update-schemas and review the diff'
    assert {k: v for k, v in found.items() if v != recorded[k]} == {}, 'columns changed: run pytest --update-schemas and review the diff'


def test_time_series_queries_return_a_time_column(personas, reader):
    sid = personas[healthy_lifestyle().account]
    for label, fmt in FORMATS.items():
        if fmt == 'time_series':
            columns = execute(reader, label, sid)[0]
            assert columns[0][1] in ('timestamptz', 'timestamp', 'date') or any(t in ('timestamptz', 'timestamp') for _, t in columns), label


def shows_data(columns, rows):
    """Whether a result holds anything but time spines, NULLs and zeros (which charts draw as nothing or as flat zero)."""
    value_columns = [i for i, (_, kind) in enumerate(columns) if not kind.startswith(('timestamp', 'date'))]
    return any(row[i] not in (None, 0, '', 0.0) for row in rows for i in value_columns)


def test_no_query_shows_an_account_anything_it_does_not_own(personas, empty_source, reader):
    """Every query, for a new account while all the personas' data is in the database: nothing, or only NULLs and zeros."""
    leaks = {}
    for label in STATEMENTS:
        if label.endswith(':Source'):
            continue  # the account dropdown lists accounts by design
        columns, rows = execute(reader, label, empty_source)
        if label not in DATA_FREE and shows_data(columns, rows):
            leaks[label] = rows[:2]
    assert sorted(leaks) == [], {k: str(v)[:80] for k, v in leaks.items()}
    for label in DATA_FREE:  # an entry nobody needs any more has to go
        assert shows_data(*execute(reader, label, empty_source)), label


def test_every_query_accepts_two_accounts_at_once(personas, reader):
    """The Source variable is multi-value. Queries that list rows return the union of the accounts' rows."""
    first, second = personas[healthy_lifestyle().account], personas[ski_tourer().account]
    aggregate = re.compile(r'group\s+by|\b(sum|count|avg|min|max|percentile_cont|array_agg|string_agg|row_number|lag|lead)\s*\(|distinct|limit|over\s*\(', re.I)
    unions = 0
    for label, sql in STATEMENTS.items():
        if label.endswith(':Source'):
            continue
        both = execute(reader, label, f'{first},{second}')[1]
        if FORMATS[label] == 'table' and label not in DATA_FREE and not aggregate.search(sql):
            parts = execute(reader, label, first)[1] + execute(reader, label, second)[1]
            assert sorted(both, key=repr) == sorted(parts, key=repr), label
            unions += 1
    assert unions >= 1


def matrix():
    full = os.getenv('DASHBOARD_MATRIX') == 'full'
    zones = ZONES if full else ('Asia/Kolkata',)
    ranges = RANGES if full else {'24h': RANGES['24h'], '1y': RANGES['1y']}
    return [(zone, name, seconds) for zone in zones for name, seconds in ranges.items()]


@pytest.mark.parametrize('zone, name, seconds', matrix(), ids=lambda v: str(v))
def test_every_query_runs_in_each_zone_and_range(personas, reader, zone, name, seconds):
    """Pushes run one zone and two ranges; the nightly run (DASHBOARD_MATRIX=full) runs three zones and five ranges."""
    sid = personas[healthy_lifestyle().account]
    for label in STATEMENTS:
        execute(reader, label, sid, zone=zone, seconds=seconds)
