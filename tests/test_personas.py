"""Dashboard panels against realistic synthetic athletes (tests/personas.py). Needs TEST_DATABASE=1."""
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo
import pytest
from garmin_grafana.storage import PostgresStore
from personas import NOW, PERSONAS, TODAY, TZ, gym_goer, healthy_lifestyle, legacy_cyclist, newcomer, public_fitbit_user, ski_tourer
from test_storage import db  # noqa: F401  (a fixture)

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
    for panel in (p for p in walk(json.loads((ROOT / 'Grafana_Dashboard' / name).read_text())['panels']) if p['type'] != 'row'):
        # Tiles have no title, only a display name; they come first, so a chart with the same title does not replace them.
        found.setdefault(panel.get('title') or panel.get('fieldConfig', {}).get('defaults', {}).get('displayName'), panel['id'])
    return found


def iso(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def local_day(value, zone=TZ):
    return value.astimezone(zone).date()


@pytest.fixture
def load(db):  # noqa: F811
    """Writes a persona under its own new account and returns a runner for dashboard queries on it."""
    def load(persona):
        store = PostgresStore(account=f'{persona.account}-{uuid.uuid4().hex}', connection=db.connection)
        store.write(persona.records)
        with db.connection.transaction(), db.connection.cursor() as cur:
            sid = store.source_id(cur)

        def run(dashboard, panel, start, end=NOW, ref='A', activity='__all', category='__all', sql=None, interval='5 minutes'):
            if sql is None:
                ids = panels(dashboard)
                sql = STATEMENTS[f'{dashboard}:{ids.get(panel, panel)}:{ref}']
            sql = queries.expand_query(sql, sid, start=iso(start), end=iso(end), timezone=persona.timezone, activity=activity, category=category, interval=interval)
            with db.connection.transaction(), db.connection.cursor() as cur:
                cur.execute(sql)
                return [dict(zip([c.name for c in cur.description], row)) for row in cur.fetchall()]
        run.sid = sid
        return run
    return load


@pytest.mark.parametrize('factory', PERSONAS, ids=lambda f: f.__name__)
def test_every_query_runs_on_each_persona(load, factory):
    run = load(factory())
    for sql in STATEMENTS.values():  # a year and a bit: covers every persona's data; short ranges are checked below
        run(None, None, NOW - timedelta(days=400), sql=sql, interval='12 hours')  # what Grafana picks for this range


def test_accounts_never_see_each_others_data(load):
    ski, wellness = load(ski_tourer()), load(healthy_lifestyle())
    winter = (datetime(2025, 12, 1, tzinfo=TZ), datetime(2026, 5, 1, tzinfo=TZ))
    assert ski('Garmin-Ski-Season.json', 'Season on snow', *winter)
    assert wellness('Garmin-Ski-Season.json', 'Season on snow', *winter) == []
    assert any(r['Steps'] for r in wellness('Garmin-Grafana-Dashboard.json', 'Daily Steps', NOW - timedelta(days=7)))
    assert not any(r['Steps'] for r in ski('Garmin-Grafana-Dashboard.json', 'Daily Steps', NOW - timedelta(days=7)))


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
    # Stress leaves out Garmin's -1/-2 codes in SQL, so smoothing never averages them and Body Battery keeps every sample.
    day = (NOW - timedelta(days=1), NOW)
    stress = run(dash, 'Stress', *day)
    assert stress and min(r['Stress Level'] for r in stress) >= 0
    assert len(run(dash, 'Stress', *day, ref='B')) == sum(1 for r in persona.records if r.dataset == 'BodyBatteryIntraday' and day[0] <= r.time <= day[1])
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


@pytest.mark.parametrize('end, shown', [
    (datetime(2026, 10, 28, 12, tzinfo=TZ), '34 days to go'),
    (datetime(2026, 12, 1, 9, tzinfo=TZ), 'Day 1'),           # opening day
    (datetime(2026, 12, 20, 9, tzinfo=TZ), 'Day 20'),
    (datetime(2027, 4, 29, 9, tzinfo=TZ), 'Day 150'),         # last day counted as the season
    (datetime(2027, 4, 30, 9, tzinfo=TZ), '215 days to go'),  # then the countdown to next winter
])
def test_ski_season_countdown_and_season_day(load, end, shown):
    run = load(ski_tourer())
    assert run('Garmin-Ski-Season.json', 'Ski season', end - timedelta(days=7), end) == [{'Ski season': shown}]


def test_hourly_steps_add_up_on_the_25_hour_dst_day(load):
    persona = healthy_lifestyle()
    run, dst = load(persona), date(2026, 10, 25)
    start, end = datetime(2026, 10, 25, tzinfo=TZ), datetime(2026, 10, 26, tzinfo=TZ)
    hours = run('Garmin-Grafana-Dashboard.json', 'Steps Hourly (24h)', start, end)
    assert sum(r['Hourly Steps'] for r in hours if local_day(r['time']) == dst) == persona.expected['steps'][dst]
    assert len({r['time'] for r in hours if local_day(r['time']) == dst}) == 25


def test_strength_dashboard_matches_the_gym_goer(load):
    persona = gym_goer()
    run, want = load(persona), persona.expected
    dash = 'Garmin-Strength-Training-Dashboard.json'
    start = datetime.combine(want['first'], datetime.min.time(), TZ) + timedelta(hours=12)  # mid-day: the first workout still counts
    one = lambda panel, **kw: list(run(dash, panel, start, **kw)[0].values())[0]  # noqa: E731
    assert one('Total Volume') == pytest.approx(want['total_volume'])
    assert one('Weighted Sets') == want['weighted_sets']
    assert one('Total Reps') == want['reps']
    assert one('Number of Workouts') == want['workouts']
    assert one('Avg Volume / Workout') == pytest.approx(want['avg_volume'])
    assert one('Sets Needing Classification (UNKNOWN)') == want['unknown']
    prs = {r['Exercise']: (r['Max Weight (kg)'], r['Best Est. 1RM (kg)']) for r in run(dash, 'Personal Records (Top Set & Est. 1RM)', start)}
    assert {k: v[0] for k, v in prs.items()} == pytest.approx({k: v[0] for k, v in want['records'].items()})
    assert {k: v[1] for k, v in prs.items()} == pytest.approx({k: v[1] for k, v in want['records'].items()})
    assert {r['ExerciseCategory']: r['Sets'] for r in run(dash, 'Sets by Category', start)} == want['sets_by_category']
    summary = {local_day(r['Time']): (r['Sets'], r['Reps'], r['Volume (kg)'], r['Time Under Tension (s)'])
               for r in run(dash, 'Workout Summary (per session)', start)}
    assert summary == pytest.approx(want['per_day'])
    weeks = {local_day(r['Time']): r['Workouts'] for r in run(dash, 'Workouts per Week', start) if r['Workouts']}
    assert weeks == want['per_week']
    squat = run(dash, 'Estimated 1RM Progression (Epley) by Exercise', start, category='SQUAT')
    assert {r['metric'] for r in squat} == {'SQUAT/BARBELL_BACK_SQUAT'}
    assert {local_day(r['time']): r['e1rm'] for r in squat} == pytest.approx(want['squat_e1rm'])
    zones = run(dash, 'HR Zone Time Distribution (Strength Sessions)', start)
    assert zones == [{'Zone 1': want['zones'][0]}]
    assert [one('HR Zone Time Distribution (Strength Sessions)', ref=r) for r in 'BCDE'] == list(want['zones'][1:])


def test_records_without_local_dates_use_the_dashboard_time_zone(load):
    persona = legacy_cyclist()
    run, want = load(persona), persona.expected
    zone = ZoneInfo(persona.timezone)
    end = datetime(2026, 10, 28, 12, tzinfo=zone)
    steps = {local_day(r['time'], zone): r['Steps'] for r in run('Garmin-Grafana-Dashboard.json', 'Daily Steps', end - timedelta(days=14), end)}
    assert {d: s for d, s in steps.items() if s} == {d: s for d, s in want['steps'].items() if d >= date(2026, 10, 14)}
    rhr = run('Garmin-Health-Overview.json', 'Resting heart rate', end - timedelta(days=14), end)
    assert {local_day(r['time'], zone): r['Resting HR'] for r in rhr if r['Resting HR']} == {d: v for d, v in want['rhr'].items() if d > date(2026, 10, 13)}
    # The Sunday 22:30 ride is Monday in UTC but belongs to the week of Monday 2026-10-19 locally.
    weekly = run('Garmin-Health-Overview.json', 'Training time per week by sport', end - timedelta(days=21), end)
    assert {local_day(r['time'], zone): r['value'] for r in weekly} == pytest.approx({date(2026, 10, 19): sum(h for _, h in want['rides'])})
    vo2 = run('Garmin-Health-Overview.json', 'VO2 max', end - timedelta(days=14), end)  # the tile: cycling when there is no running estimate
    assert [r['VO2 max'] for r in vo2] == [want['vo2']]
    battery = run('Garmin-Grafana-Dashboard.json', 'Battery Level (Requires GarminHomeAssistant from ConnectIQ integration)', end - timedelta(days=2), end)
    assert [r['Device Battery Level'] for r in battery] == want['battery']


def test_new_users_get_no_usual_range_or_load_yet(load):
    persona = newcomer()
    run, want = load(persona), persona.expected
    hrv = {local_day(r['time']): r for r in run('Garmin-Health-Overview.json', 'Overnight HRV', NOW - timedelta(days=10))}
    assert [hrv[d]['Overnight HRV'] for d in want['days']] == want['hrv']
    assert all(r['Usual range'] is None and r['Usual low'] is None for r in hrv.values())  # needs 14 nights
    assert [hrv[d]['7-day average'] is not None for d in want['days']] == [False, False, True, True, True]  # needs 3
    recovery = run('Garmin-Health-Overview.json', 'Recovery compared with your usual', NOW - timedelta(days=10))
    assert recovery and all(not r.get('Usual range') for r in recovery)
    ski = 'Garmin-Ski-Season.json'
    assert run(ski, 'Load ramp', NOW - timedelta(days=7)) == [{'value': None}]
    assert run(ski, 'Vertical, 7 days', NOW - timedelta(days=7)) == [{'value': 0}]
    assert run(ski, 'Longest uphill, 4 weeks', NOW - timedelta(days=7)) == [{'value': None}]
    assert run('Garmin-Health-Overview.json', 'VO2 max', NOW - timedelta(days=7)) == []


def test_dashboards_show_a_real_fitbit_month(load):
    """Public data (Zenodo 53894, CC BY 4.0): non-wear days, gaps in heart rate, naps and missing fields."""
    persona = public_fitbit_user()
    run, want = load(persona), persona.expected
    end = datetime.combine(want['last_day'], datetime.min.time(), TZ) + timedelta(hours=23)
    month = end - timedelta(days=31)
    stats = {local_day(r['time']): r['Steps'] for r in run('Garmin-Grafana-Dashboard.json', 'Daily Steps', month, end)}
    assert {d: stats[d] for d in want['steps']} == want['steps']
    health = {local_day(r['time']): r['Steps'] for r in run('Garmin-Health-Overview.json', 'Steps', month, end)}
    assert {d: health[d] for d in want['steps']} == want['steps']
    sleep = {local_day(r['time']): r['Light'] for r in run('Garmin-Health-Overview.json', 'Sleep per night', month, end) if r['Light'] is not None}
    assert sleep == pytest.approx(want['asleep_hours'])
    day = want['last_day'] - timedelta(days=3)
    start = datetime.combine(day, datetime.min.time(), TZ)
    heart = run('Garmin-Grafana-Dashboard.json', 'Heart Rate', start, start + timedelta(days=1, minutes=-1), interval='2 minutes')
    assert {r['time']: r['Heart Rate'] for r in heart if r['Heart Rate'] is not None} == pytest.approx(want['heart'][day])
    intensity = run('Garmin-Health-Overview.json', 'Intensity, 7 days', end - timedelta(days=14), end)
    assert intensity[-1]['Intensity minutes (7 days)'] == sum(want['intensity'][want['last_day'] - timedelta(days=n)] for n in range(7))
    weight = {local_day(r['time']): r['Weight'] for r in run('Garmin-Health-Overview.json', 'Weight', month, end) if r['Weight'] is not None}
    assert weight == pytest.approx(want['weight'])
    for tile in ('Resting HR', 'HRV', 'Battery at wake'):  # Fitbit never sends these: empty, not zero
        assert all(list(r.values())[-1] is None for r in run('Garmin-Health-Overview.json', tile, end - timedelta(days=14), end)), tile
