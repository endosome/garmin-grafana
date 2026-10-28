"""Dashboard panels against realistic synthetic athletes (tests/personas.py). Needs TEST_DATABASE=1."""
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import pytest
from personas import NOW, TODAY, TZ, TZ_NAME, healthy_lifestyle, ski_tourer
from test_storage import db, query, source  # noqa: F401  (db is a fixture)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('queries', ROOT / 'scripts/validate_dashboard_queries.py')
queries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queries)
STATEMENTS = {label: sql for label, sql, fmt in queries.dashboard_queries()}


def panels(name):
    def walk(items):
        for panel in items:
            yield panel
            yield from walk(panel.get('panels', []))
    found = {}
    for panel in walk(json.loads((ROOT / 'Grafana_Dashboard' / name).read_text())['panels']):
        found[panel.get('title') or panel.get('fieldConfig', {}).get('defaults', {}).get('displayName')] = panel['id']
    return found


def iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def local_day(value):
    return value.astimezone(TZ).date()


@pytest.fixture
def load(db):  # noqa: F811
    def load(persona):
        db.write(persona.records)
        sid = source(db)

        def run(dashboard, panel, start, end=NOW, ref='A', activity='__all'):
            ids = panels(dashboard)
            key = f'{dashboard}:{ids.get(panel, panel)}:{ref}'
            sql = queries.expand_query(STATEMENTS[key], sid, start=iso(start), end=iso(end), timezone=TZ_NAME, activity=activity)
            with db.connection.transaction(), db.connection.cursor() as cur:
                cur.execute(sql)
                return [dict(zip([c.name for c in cur.description], row)) for row in cur.fetchall()]
        run.sid = sid
        return run
    return load


def test_every_query_runs_on_both_personas(load, db):  # noqa: F811
    for persona in (ski_tourer(), healthy_lifestyle()):
        run = load(persona)
        for start in (NOW - timedelta(days=7), NOW - timedelta(days=400)):
            for label, sql in STATEMENTS.items():
                expanded = queries.expand_query(sql, run.sid, start=iso(start), end=iso(NOW), timezone=TZ_NAME)
                with db.connection.transaction(), db.connection.cursor() as cur:
                    cur.execute(expanded)
                    cur.fetchall()


def test_ski_season_panels_match_the_ski_tourer(load):
    persona = ski_tourer()
    run, want = load(persona), persona.expected
    build = datetime.combine(want['build_start'], datetime.min.time(), TZ)
    dash = 'Garmin-Ski-Season.json'
    for tile, value in want['tiles'].items():
        got = list(run(dash, tile, build)[0].values())[0]
        assert got == pytest.approx(value) if not isinstance(value, str) else got == value, tile
    weekly = {(local_day(r['time']), r['metric']): r['value'] for r in run(dash, 'Vertical on foot per week', build) if r['value']}
    assert weekly == pytest.approx(want['weekly_vertical'])
    goal = run(dash, 'Vertical on foot per week', build, ref='B')
    assert goal and all(local_day(r['time']).weekday() == 0 and r['Weekly goal'] == 2000 for r in goal)
    sets = {(local_day(r['time']), r['metric']): r['value'] for r in run(dash, 'Strength sets per week by body area', build)}
    assert sets == want['weekly_sets']
    winter = run(dash, 'Season on snow', datetime(2025, 12, 1, tzinfo=TZ), datetime(2026, 5, 1, tzinfo=TZ))
    columns = ('Days', 'Sessions', 'Hours', 'Vertical up', 'Vertical down', 'Distance', 'Max speed')
    got = {(r['Sport'], c): r[c] for r in winter for c in columns}
    assert got == pytest.approx({(sport, c): v for sport, row in want['season'].items() for c, v in zip(columns, row)})
    assert len(run(dash, 'Climbing rate on uphill sessions', build)) == want['climbing_points']
    # Resort skiing is not on foot: its lift-served "gain" never reaches the vertical panels.
    assert all(metric != 'Resort skiing' for _, metric in weekly)


def test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person(load):
    persona = healthy_lifestyle()
    run, want = load(persona), persona.expected
    dash = 'Garmin-Grafana-Dashboard.json'
    week = run(dash, 'Week at a Glance', NOW - timedelta(days=7), ref='C')  # starts mid-day: the first day still counts
    assert {local_day(r['time']): r['Steps'] for r in week} == {d: want['steps'][d] for d in want['steps'] if d >= TODAY - timedelta(days=7)}
    rhr = run(dash, 'Week at a Glance', NOW - timedelta(days=7), ref='A')
    assert {local_day(r['time']): r['Resting Heart Rate'] for r in rhr} == {d: want['rhr'][d] for d in want['rhr'] if d >= TODAY - timedelta(days=7)}
    month = {local_day(r['time']): r for r in run(dash, 'Daily Steps', NOW - timedelta(days=30))}
    for day in want['gap']:
        assert not month[day].get('Steps'), day  # the sync gap stays empty, never a copy of a neighbour
    assert run(dash, "Today's Steps", NOW - timedelta(days=1))[-1]['totalSteps'] == want['steps'][TODAY]
    assert run(dash, 'Today RHR', NOW - timedelta(days=1))[-1]['restingHeartRate'] == want['rhr'][TODAY]
    track = run(dash, 'GPS Track Heart Rate', NOW - timedelta(days=7), activity=want['gps_selector'])
    assert len(track) == want['gps_points']


def test_garmin_health_panels_match_the_healthy_lifestyle_person(load):
    persona = healthy_lifestyle()
    run, want = load(persona), persona.expected
    dash = 'Garmin-Health-Overview.json'
    tiles = {name: run(dash, name, NOW - timedelta(days=14)) for name in ('Steps today', 'Resting HR', 'Sleep score')}
    assert tiles['Steps today'][-1]['Steps today'] == want['steps'][TODAY]
    assert tiles['Resting HR'][-1]['Resting HR'] == want['rhr'][TODAY]
    assert tiles['Sleep score'][-1]['Sleep score'] == want['sleep_score'][TODAY]
    start = datetime(2025, 10, 28, tzinfo=TZ)
    table = {r['Activity']: r for r in run(dash, 'Activities in the selected period', start)}
    assert {k.lower(): r['Sessions'] for k, r in table.items()} == want['sessions']
    assert {k.lower(): r['Distance'] for k, r in table.items() if r['Distance']} == pytest.approx({k: v for k, v in want['distance_km'].items() if v})
    weight = {local_day(r['time']): r['Weight'] for r in run(dash, 'Weight', start) if r['Weight'] is not None}
    assert weight == pytest.approx(want['weight'])
    journal = run(dash, 'Lifestyle journal', start)
    assert {b: sum(1 for r in journal if r['metric'] == b and r['value']) for b in want['journal_yes']} == want['journal_yes']
    steps = {local_day(r['time']): r for r in run(dash, 'Steps', NOW - timedelta(days=30))}
    assert [steps[d]['Steps'] for d in want['gap']] == [None] * len(want['gap'])  # the 7-day average may span the gap
