"""Deterministic synthetic athletes for database and dashboard tests.

Each persona returns the records the collectors would write, in the same shapes as
garmin_fetch.py, and the values the dashboards should show for them, computed here
in plain Python rather than SQL. Both live in Europe/Budapest and "today" is
2026-10-28, so the data crosses the October DST change. scripts/load_personas.py
loads them into a development database to browse in Grafana.
"""
import functools
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from garmin_grafana.domain import Record

TZ_NAME = 'Europe/Budapest'
TZ = ZoneInfo(TZ_NAME)
TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=TZ)


@dataclass
class Persona:
    account: str
    timezone: str = TZ_NAME
    legacy: bool = False  # migrated from InfluxDB: no local date or time zone on any record
    records: list = field(default_factory=list)
    expected: dict = field(default_factory=dict)

    def add(self, dataset, when, fields, day, tags=None):
        tags = {'Device': 'EPIX PRO - 47 mm', 'Database_Name': 'garmin', **(tags or {})}
        self.records.append(Record.from_mapping({'measurement': dataset, 'time': when.astimezone(timezone.utc).isoformat(),
                                                 'tags': tags, 'fields': fields},
                                                local_date=None if self.legacy else day, timezone=None if self.legacy else self.timezone))


def at(day, hour, minute=0):
    """Local wall-clock time; hours past 24 or below 0 roll into the next or previous day."""
    return datetime.combine(day, time(0), TZ) + timedelta(hours=hour, minutes=minute)


def week(day):
    return day - timedelta(day.weekday())


def activity(persona, ids, day, hour, kind, hours, *, gain=None, loss=None, km=None, max_kmh=None, hr=None, load=None,
             zones=None, name=None, minute=0):
    """An ActivitySummary start document and its END marker, as get_activity_summary writes them."""
    start = at(day, hour, minute)
    aid = next(ids)
    secs = round(hours * 3600, 1)
    selector = start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SUTC-') + kind
    tags = {'ActivityID': aid, 'ActivitySelector': selector}
    fields = {'Activity_ID': aid, 'Device_ID': 3468029229, 'activityName': name or kind.replace('_', ' ').title(),
              'activityType': kind, 'distance': km * 1000 if km is not None else None, 'elevationGain': gain,
              'elevationLoss': gain if loss is None else loss, 'elapsedDuration': secs, 'movingDuration': round(secs * .82),
              'maxSpeed': max_kmh / 3.6 if max_kmh else None, 'averageHR': hr, 'maxHR': hr + 25 if hr else None,
              'activityTrainingLoad': load, 'locationName': 'Tatras' if gain else 'Budapest', 'calories': round(hours * 600),
              'bmrCalories': round(hours * 70)}
    for n in range(5):
        fields[f'hrTimeInZone_{n + 1}'] = int(secs * zones[n]) if zones else None
    persona.add('ActivitySummary', start, fields, day, tags)
    persona.add('ActivitySummary', start + timedelta(seconds=secs), {'Activity_ID': aid, 'Device_ID': 3468029229,
                'activityName': 'END', 'activityType': 'No Activity'}, day, tags)
    return dict(id=aid, day=day, kind=kind, secs=secs, moving=fields['movingDuration'], gain=gain,
                loss=fields['elevationLoss'], km=km, max_kmh=max_kmh, hr=hr, load=load, zones=zones,
                start=start, selector=selector)


@functools.cache
def ski_tourer():
    """Sixteen weeks of pre-season training, last winter on snow, and the first tour of the season."""
    p = Persona('persona-ski-tourer')
    ids = iter(range(500000, 600000))
    acts, sets = [], []
    zones = {'hiking': (.25, .45, .22, .07, .01), 'trail_running': (.05, .25, .40, .25, .05), 'stair_climbing': (.05, .30, .45, .18, .02),
             'road_biking': (.20, .50, .25, .05, 0), 'backcountry_skiing_snowboarding_ws': (.15, .45, .30, .09, .01)}

    def act(day, hour, kind, hours, **kw):
        acts.append(activity(p, ids, day, hour, kind, hours, zones=kw.pop('zones', zones.get(kind)), **kw))
        return acts[-1]

    def strength(day, legs):
        a = act(day, 18, 'strength_training', .9, hr=105, load=45, name='Leg day' if legs else 'Gym', minute=30)
        plan = ([('SQUAT', 'SQUAT/BARBELL_BACK_SQUAT', 5, 4, 90), ('LUNGE', 'LUNGE/DUMBBELL_LUNGE', 10, 3, 18),
                 ('CALF_RAISE', 'CALF_RAISE/STANDING_CALF_RAISE', 15, 3, 40), ('PLYO', 'PLYO/BOX_JUMP', 6, 3, 0),
                 ('PLANK', 'PLANK/PLANK', 1, 2, 0)] if legs else
                [('BENCH_PRESS', 'BENCH_PRESS/BARBELL_BENCH_PRESS', 8, 3, 60), ('CORE', 'CORE/DEAD_BUG', 10, 2, 0),
                 ('HIP_RAISE', 'HIP_RAISE/BARBELL_HIP_THRUST', 10, 3, 70)])
        order = 0
        for category, label, reps, count, kg in plan:
            for _ in range(count):
                order += 1
                p.add('StrengthExerciseSet', a['start'] + timedelta(minutes=2 * order),
                      {'Activity_ID': a['id'], 'SetOrder': order, 'Reps': reps, 'Weight_kg': kg, 'Duration_s': 40}, day,
                      {'ActivityID': str(a['id']), 'ExerciseCategory': category, 'ExerciseLabel': label})
                sets.append((day, category))

    start = date(2026, 7, 6)
    for w in range(17):
        days = [start + timedelta(weeks=w, days=d) for d in range(7)]
        f = .6 if w % 4 == 3 else 1 + w * .06  # every fourth week is a recovery week
        for day, fn in ((days[1], lambda d: strength(d, True)),
                        (days[2], lambda d: act(d, 6.5, 'trail_running', .9 * f, gain=round(380 * f), km=9 * f, hr=152, load=round(95 * f))),
                        (days[3], lambda d: act(d, 18, 'stair_climbing', .7, gain=round(450 * f), hr=148, load=60)),
                        (days[4], lambda d: strength(d, w % 2 == 0)),
                        (days[5], lambda d: act(d, 7, 'hiking', 3.5 * f, gain=round(1100 * f), km=14 * f, hr=128, load=round(160 * f))),
                        (days[6], lambda d: act(d, 9, 'road_biking', 2.5, gain=1200, km=70, hr=135, load=140))):
            if day <= TODAY:
                fn(day)
    # Edge cases: a Monday 00:30 local start is still Sunday in UTC; a hilly walk counts as uphill;
    # a treadmill run has no elevation; an accidental zero-length activity; a hike on the DST day.
    act(date(2026, 8, 10), 0.5, 'trail_running', 1.2, gain=520, km=11, hr=150, load=110, name='Midnight trail run')
    act(date(2026, 9, 9), 17, 'walking', 2, gain=350, km=7, hr=100, load=30, name='Hilly walk')
    act(date(2026, 9, 16), 12, 'treadmill_running', .75, km=9, hr=150, load=80)
    act(date(2026, 9, 23), 12, 'other', 0, gain=0, km=0, name='Accidental start')
    act(date(2026, 10, 25), 8, 'hiking', 4.2, gain=1350, km=15, hr=130, load=190, name='DST day hike')
    act(date(2026, 10, 26), 7.5, 'backcountry_skiing_snowboarding_ws', 5, gain=1600, km=16, max_kmh=62, hr=138, load=230, name='First tour')
    for day, kind, hours, gain, loss, km, vmax, load in (
            (date(2025, 12, 20), 'resort_skiing', 5, 3900, 4200, 52, 84, 70), (date(2025, 12, 21), 'resort_skiing', 4, 3100, 3300, 45, 79, 55),
            (date(2026, 1, 10), 'backcountry_skiing_snowboarding_ws', 6, 1450, 1450, 22, 63, 240), (date(2026, 1, 11), 'snowshoeing_ws', 3, 700, 700, 9, 9, 110),
            (date(2026, 1, 24), 'cross_country_skiing_ws', 2, 250, 250, 25, 35, 120), (date(2026, 2, 7), 'backcountry_skiing_snowboarding_ws', 7.5, 1900, 1900, 24, 71, 300),
            (date(2026, 2, 8), 'resort_skiing', 5.5, 4800, 5100, 58, 96, 80), (date(2026, 3, 14), 'backcountry_skiing_snowboarding_ws', 5, 1300, 1300, 18, 58, 210)):
        act(day, 8.5, kind, hours, gain=gain, loss=loss, km=km, max_kmh=vmax, hr=125, load=load)

    sport = {'backcountry_skiing_snowboarding_ws': 'Ski touring', 'cross_country_skiing_ws': 'Nordic skiing', 'snowshoeing_ws': 'Snowshoeing',
             'hiking': 'Hiking & trail', 'trail_running': 'Hiking & trail', 'stair_climbing': 'Stairs & incline', 'treadmill_running': 'Running',
             'walking': 'Walking', 'resort_skiing': 'Resort skiing', 'road_biking': 'Cycling', 'strength_training': 'Strength', 'other': 'Other'}
    on_foot = {'Ski touring', 'Nordic skiing', 'Snowshoeing', 'Hiking & trail', 'Stairs & incline', 'Running', 'Walking'}
    snow = ('Ski touring', 'Resort skiing', 'Nordic skiing', 'Snowshoeing')

    def uphill(a):
        s = sport[a['kind']]
        return s in {'Ski touring', 'Snowshoeing', 'Hiking & trail', 'Stairs & incline'} or (s in on_foot and (a['gain'] or 0) >= 300)

    def last(a, n):
        return TODAY - timedelta(n) < a['day'] <= TODAY

    foot = [a for a in acts if sport[a['kind']] in on_foot]
    weekly_vertical, weekly_sets, season = defaultdict(float), defaultdict(int), {}
    for a in foot:
        if a['day'] >= start and a['gain']:
            weekly_vertical[(week(a['day']), sport[a['kind']])] += a['gain']
    areas = {'SQUAT': 'Legs & hips', 'LUNGE': 'Legs & hips', 'CALF_RAISE': 'Legs & hips', 'PLYO': 'Legs & hips', 'HIP_RAISE': 'Legs & hips',
             'PLANK': 'Core & back', 'CORE': 'Core & back', 'BENCH_PRESS': 'Upper body'}
    for day, category in sets:
        weekly_sets[(week(day), areas[category])] += 1
    for name in snow:
        rows = [a for a in acts if sport[a['kind']] == name and date(2025, 12, 1) <= a['day'] <= date(2026, 4, 30)]
        season[name] = (len({a['day'] for a in rows}), len(rows), sum(a['secs'] for a in rows) / 3600,
                        None if name == 'Resort skiing' else sum(a['gain'] for a in rows), sum(a['loss'] for a in rows),
                        sum(a['km'] for a in rows), max(a['max_kmh'] for a in rows))
    p.expected = {
        'tiles': {'Ski season': f'{(date(2026, 12, 1) - TODAY).days} days to go',
                  'Vertical, 7 days': sum(a['gain'] or 0 for a in foot if last(a, 7)),
                  'Vertical / week, 4 weeks': sum(a['gain'] or 0 for a in foot if last(a, 28)) / 4,
                  'Uphill time, 7 days': sum(a['secs'] for a in acts if uphill(a) and last(a, 7)) / 3600,
                  'Longest uphill, 4 weeks': max(a['secs'] for a in acts if uphill(a) and last(a, 28)) / 3600,
                  'Strength, 14 days': sum(1 for a in acts if a['kind'] == 'strength_training' and last(a, 14)),
                  'Load ramp': sum(a['load'] or 0 for a in acts if last(a, 7)) / (sum(a['load'] or 0 for a in acts if last(a, 28)) / 4)},
        'weekly_vertical': dict(weekly_vertical), 'weekly_sets': dict(weekly_sets), 'season': season,
        'climbing_points': sum(1 for a in acts if uphill(a) and (a['gain'] or 0) >= 200 and start <= a['day'] <= TODAY),
        'build_start': start,
    }
    return p


@functools.cache
def healthy_lifestyle():
    """An active office worker: runs, bike commutes, yoga, daily health data, a sync gap and a partly synced today."""
    p = Persona('persona-healthy-lifestyle')
    rng = random.Random(11)
    ids = iter(range(700000, 800000))
    gap = {date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)}  # the watch was not synced
    intraday_from = date(2026, 10, 25)  # includes the 25-hour DST day
    expected = {'steps': {}, 'rhr': {}, 'sleep_seconds': {}, 'sleep_score': {}, 'weight': {}, 'sessions': defaultdict(int),
                'distance_km': defaultdict(float), 'journal_yes': defaultdict(int), 'gap': sorted(gap), 'gps_points': 0}
    run_zones, ride_zones = (.05, .30, .45, .18, .02), (.30, .50, .18, .02, 0)
    weight = 69.4
    day = date(2025, 10, 28)
    while day <= TODAY:
        if day in gap:
            day += timedelta(1)
            continue
        dow, partial = day.weekday(), day == TODAY
        bed = at(day, -1) + timedelta(minutes=rng.randint(-40, 30))  # the evening before, local time
        wake = at(day, 6, 50) + timedelta(minutes=rng.randint(-30, 30))
        sleep_seconds = int((wake - bed).total_seconds()) - rng.randint(600, 1800)
        rhr, hrv, score = 55 + rng.randint(-2, 3), 46 + rng.randint(-6, 6), rng.randint(65, 90)
        acts = []
        if dow in (1, 3):
            km = round(rng.uniform(5.5, 8), 2)
            acts.append(activity(p, ids, day, 18, 'running', km * 5.6 / 60, km=km, hr=148, gain=35, load=rng.randint(70, 110), zones=run_zones))
        if dow == 5:
            km = round(rng.uniform(12, 16), 2)
            acts.append(activity(p, ids, day, 9, 'running', km * 6 / 60, km=km, hr=142, gain=60, load=rng.randint(150, 210), zones=run_zones))
        if dow in (0, 2, 4):
            acts.append(activity(p, ids, day, 8, 'cycling', 22 / 60, km=7.5, hr=112, gain=20, load=15, zones=ride_zones))
        if dow == 6:
            acts.append(activity(p, ids, day, 10, 'yoga', 1, hr=78, load=8, zones=(.9, .1, 0, 0, 0)))
        for a in acts:
            expected['sessions'][a['kind']] += 1
            expected['distance_km'][a['kind']] += a['km'] or 0
        if day >= intraday_from:
            steps = intraday(p, rng, day, bed, wake, rhr, hrv, acts, NOW if partial else at(day, 24))
        else:
            steps = rng.randint(6500, 12500)
        expected['steps'][day] = steps
        expected['rhr'][day], expected['sleep_seconds'][day], expected['sleep_score'][day] = rhr, sleep_seconds, score
        deep, rem = int(sleep_seconds * .2), int(sleep_seconds * .22)
        p.add('SleepSummary', wake, {'sleepTimeSeconds': sleep_seconds, 'deepSleepSeconds': deep, 'lightSleepSeconds': sleep_seconds - deep - rem,
              'remSleepSeconds': rem, 'awakeSleepSeconds': rng.randint(300, 1500), 'sleepScore': score, 'averageSpO2Value': 95.0,
              'averageRespirationValue': 13.0, 'avgSleepStress': 13.0, 'avgOvernightHrv': float(hrv), 'restingHeartRate': rhr,
              'bodyBatteryChange': 62, 'avgSkinTempDeviationC': round(rng.uniform(-.4, .5), 1)}, day)
        p.add('DailyStats', at(day, 0), {'totalSteps': steps, 'totalDistanceMeters': round(steps * .78), 'restingHeartRate': rhr,
              'sleepingSeconds': sleep_seconds, 'activeKilocalories': 500.0, 'bmrKilocalories': 1610.0, 'activeSeconds': 5400,
              'highlyActiveSeconds': 1800, 'sedentarySeconds': 40000, 'moderateIntensityMinutes': rng.randint(10, 40),
              'vigorousIntensityMinutes': sum(30 for a in acts if a['hr'] > 140), 'minHeartRate': rhr - 3, 'maxHeartRate': 175,
              'highStressDuration': 1000, 'mediumStressDuration': 4000, 'lowStressDuration': 15000, 'restStressDuration': 30000,
              'uncategorizedStressDuration': 2000, 'bodyBatteryHighestValue': 92, 'bodyBatteryLowestValue': 22,
              'bodyBatteryAtWakeTime': 88, 'bodyBatteryChargedValue': 62, 'bodyBatteryDrainedValue': 70, 'averageSpo2': 95.0}, day)
        p.add('DeviceSync', wake + timedelta(minutes=25), {'Device_Name': 'EPIX PRO - 47 mm', 'imageUrl': None}, day)
        p.add('TrainingReadiness', wake + timedelta(minutes=5), {'score': rng.randint(40, 90), 'level': 'MODERATE'}, day)
        midnight_utc = datetime.combine(day, time(0), timezone.utc)
        p.add('RacePredictions', midnight_utc, {'time5K': 1500, 'time10K': 3150, 'timeHalfMarathon': 7000, 'timeMarathon': 14800}, day)
        p.add('FitnessAge', midnight_utc, {'fitnessAge': 29.5, 'chronologicalAge': 34.0, 'achievableFitnessAge': 28.0}, day)
        if not partial:
            p.add('TrainingStatus', wake, {'dailyTrainingLoadAcute': rng.randint(250, 420), 'dailyTrainingLoadChronic': 330,
                  'maxTrainingLoadChronic': 495.0, 'minTrainingLoadChronic': 264.0}, day, {'DeviceID': '3468029229'})
            for behavior, category, yes, amount in (('Alcohol', 'LIFESTYLE', dow in (4, 5), 2.0), ('Caffeine', 'LIFESTYLE', True, 2.0)):
                p.add('LifestyleJournal', midnight_utc, {'status': int(yes), 'value': amount if yes else 0.0}, day,
                      {'behavior': behavior, 'category': category})
                expected['journal_yes'][behavior] += int(yes)
        if dow == 5 and not partial:
            p.add('VO2_Max', midnight_utc, {'VO2_max_value': 50.0 + (day - date(2025, 10, 28)).days / 120, 'VO2_max_value_cycling': None}, day)
        if dow == 0:
            weight = round(weight - rng.uniform(-.1, .25), 1)
            p.add('BodyComposition', at(day, 7, 30), {'weight': weight * 1000, 'bmi': None, 'bodyFat': None}, day,
                  {'Frequency': 'Intraday', 'SourceType': 'MANUAL'})
            expected['weight'][day] = weight
        day += timedelta(1)
    gps_run = next(a for a in reversed(p.records) if a.dataset == 'ActivitySummary' and a.values.get('activityType') == 'running'
                   and a.values.get('activityName') != 'END' and a.local_date >= intraday_from)
    expected['gps_points'] = gps(p, gps_run)
    expected['gps_selector'] = gps_run.tags['ActivitySelector']
    expected.update(sessions=dict(expected['sessions']), distance_km=dict(expected['distance_km']), journal_yes=dict(expected['journal_yes']))
    p.expected = expected
    return p


def intraday(p, rng, day, bed, wake, rhr, hrv, acts, end):
    """Intraday series at Garmin's cadence; returns the day's step total (the sum of the 15-minute counts)."""
    steps, t = 0, at(day, 0)
    while t < end:
        asleep = t < wake or t >= bed + timedelta(days=1)
        act = next((a for a in acts if a['start'] <= t < a['start'] + timedelta(seconds=a['secs'])), None)
        if t.minute % 2 == 0:
            p.add('HeartRateIntraday', t, {'HeartRate': act['hr'] + rng.randint(-6, 8) if act else rhr + rng.randint(-2, 4) if asleep else 66 + rng.randint(-4, 18)}, day)
            p.add('BreathingRateIntraday', t, {'BreathingRate': float(28 if act else 13 + rng.randint(-1, 2))}, day)
        if t.minute % 3 == 0:
            # -1: activity, -2: watch off the wrist (13:00-13:09); Garmin's "not measured" codes.
            stress = -1 if act else -2 if at(day, 13) <= t < at(day, 13, 9) else rng.randint(5, 20) if asleep else rng.randint(15, 55)
            p.add('StressIntraday', t, {'stressLevel': stress}, day)
            p.add('BodyBatteryIntraday', t, {'BodyBatteryLevel': 30 + int((t - at(day, 0)).total_seconds() / 400) if t < wake
                                             else max(5, 92 - int((t - wake).total_seconds() / 800))}, day)
        if t.minute % 15 == 0:
            n = 0 if asleep else rng.randint(1500, 2200) if act and act['hr'] > 140 else rng.choice([0, 0, 40, 120, 300, 650])
            steps += n
            p.add('StepsIntraday', t, {'StepsCount': n}, day)
        t += timedelta(minutes=1)
    stage_start = bed
    for level, minutes in ((1, 25), (0, 45), (1, 30), (2, 20), (1, 35), (0, 30), (2, 30), (1, 40), (3, 3), (2, 35), (1, 999)):
        stage_end = min(wake, stage_start + timedelta(minutes=minutes))
        if stage_end <= stage_start:
            break
        p.add('SleepIntraday', stage_start, {'SleepStageLevel': level, 'SleepStageSeconds': int((stage_end - stage_start).total_seconds())}, day)
        stage_start = stage_end
    p.add('SleepIntraday', wake, {'SleepStageLevel': 1}, day)
    m = bed
    while m < wake:
        p.add('SleepIntraday', m, {'SleepMovementActivityLevel': round(rng.uniform(0, 1.5), 2), 'SleepMovementActivitySeconds': 60}, day)
        if m.minute % 5 == 0:
            p.add('SleepIntraday', m + timedelta(seconds=30), {'heartRate': rhr + rng.randint(-2, 4)}, day)
            p.add('SleepIntraday', m + timedelta(seconds=20), {'spo2Reading': rng.randint(92, 98)}, day)
            p.add('HRV_Intraday', m + timedelta(seconds=10), {'hrvValue': hrv + rng.randint(-8, 8)}, day)
        m += timedelta(minutes=1)
    return steps


def gps(p, summary):
    """A track around Margaret Island, one point every 5 seconds, as the FIT parser writes ActivityGPS."""
    secs, km = summary.values['elapsedDuration'], summary.values['distance'] / 1000
    start = summary.time
    count = 0
    for t in range(0, int(secs), 5):
        a = 2 * math.pi * (t / secs * km * 1000 % 5300) / 5300
        p.add('ActivityGPS', start + timedelta(seconds=t), {'ActivityName': 'running', 'Activity_ID': summary.values['Activity_ID'],
              'Latitude': 47.527 + .0105 * math.sin(a), 'Longitude': 19.047 + .004 * math.cos(a), 'Altitude': 104.0,
              'Distance': t / secs * km * 1000, 'DurationSeconds': float(t), 'HeartRate': 150.0, 'Speed': km * 1000 / secs, 'Cadence': 82},
              summary.local_date, {'ActivityID': summary.tags['ActivityID'], 'ActivitySelector': summary.tags['ActivitySelector']})
        count += 1
    return count


@functools.cache
def gym_goer():
    """Twelve weeks of progressive strength training, with every set the Strength dashboard special-cases."""
    p = Persona('persona-gym-goer')
    ids = iter(range(900000, 1000000))
    sets = []
    first = date(2026, 8, 3)

    def workout(day, hour, plan, minute=0):
        a = activity(p, ids, day, hour, 'strength_training', 1, hr=110, load=50, minute=minute, name='Strength')
        for order, (category, label, reps, kg, seconds) in enumerate(plan, 1):
            p.add('StrengthExerciseSet', a['start'] + timedelta(minutes=3 * order),
                  {'Activity_ID': a['id'], 'SetOrder': order, 'Reps': reps, 'Weight_kg': kg, 'Duration_s': seconds}, day,
                  {'ActivityID': str(a['id']), 'ExerciseCategory': category, 'ExerciseLabel': label})
            sets.append(dict(day=day, activity=a['id'], category=category, label=label, reps=reps, kg=kg, seconds=seconds))
        for zone, seconds in enumerate((600, 1500, 900, 300, 60), 1):
            p.add('StrengthHRZones', a['start'], {'Activity_ID': a['id'], 'ZoneNumber': zone, 'SecsInZone': seconds}, day,
                  {'ActivityID': str(a['id'])})

    for w in range(12):
        for d, kind in ((0, 'legs'), (2, 'push'), (4, 'pull')):
            day = first + timedelta(weeks=w, days=d)
            plan = [('ROW', 'ROW/INDOOR_ROW', 1, 30, 300), ('RUN', 'RUN/TREADMILL', 1, 10, 600)]  # warm-ups the dashboard leaves out
            if kind == 'legs':
                plan += [('SQUAT', 'SQUAT/BARBELL_BACK_SQUAT', 5, 60 + 2.5 * w, 45)] * 4 + [('DEADLIFT', 'DEADLIFT/ROMANIAN_DEADLIFT', 8, 70 + 2.5 * w, 50)] * 3
            elif kind == 'push':
                plan += [('BENCH_PRESS', 'BENCH_PRESS/BARBELL_BENCH_PRESS', 6, 45 + 1.25 * w, 35)] * 4 + [('PUSH_UP', 'PUSH_UP/PUSH_UP', 15, 0, 30)] * 2
            else:
                plan += [('PULL_UP', 'PULL_UP/PULL_UP', 8, 0, 30)] * 3 + [('ROW', 'ROW/BARBELL_ROW', 10, 40 + w, 40)] * 3
            plan += [('UNKNOWN', 'UNKNOWN', 12, 20, 40)]  # Garmin could not classify it
            workout(day, 18, plan)
    workout(date(2026, 10, 21), 7, [('CORE', 'CORE/DEAD_BUG', 10, 5, 45)] * 3)  # a morning workout on a day with an evening one
    weighted = [s for s in sets if s['kg'] > 0 and s['label'] != 'ROW/INDOOR_ROW' and s['category'] != 'RUN']
    volume = defaultdict(float)
    for s in weighted:
        volume[s['activity']] += s['reps'] * s['kg']
    per_day, per_week, records = defaultdict(lambda: [0, 0, 0.0, 0]), defaultdict(set), {}
    for s in weighted:
        row = per_day[s['day']]
        row[0] += 1; row[1] += s['reps']; row[2] += s['reps'] * s['kg']; row[3] += s['seconds']
        per_week[week(s['day'])].add(s['activity'])
        best = records.get(s['label'], (0, 0))
        records[s['label']] = (max(best[0], s['kg']), max(best[1], s['kg'] * (1 + s['reps'] / 30)))
    by_category = defaultdict(int)
    for s in weighted:
        by_category[s['category']] += 1
    squat = defaultdict(float)
    for s in weighted:
        if s['category'] == 'SQUAT':
            squat[s['day']] = max(squat[s['day']], s['kg'] * (1 + s['reps'] / 30))
    p.expected = {'first': first, 'total_volume': sum(volume.values()), 'weighted_sets': len(weighted),
                  'reps': sum(s['reps'] for s in weighted), 'workouts': len(volume), 'avg_volume': sum(volume.values()) / len(volume),
                  'records': records, 'sets_by_category': dict(by_category), 'unknown': sum(1 for s in sets if s['category'] == 'UNKNOWN'),
                  'per_day': {d: tuple(v) for d, v in per_day.items()}, 'per_week': {w: len(a) for w, a in per_week.items()},
                  'squat_e1rm': dict(squat), 'zones': (600 * len(volume), 1500 * len(volume), 900 * len(volume), 300 * len(volume), 60 * len(volume))}
    return p


@functools.cache
def legacy_cyclist():
    """Imported from the old InfluxDB setup: no local dates, and lives in Los Angeles (UTC-7), so evenings are the next UTC day."""
    la = ZoneInfo('America/Los_Angeles')
    p = Persona('persona-legacy-cyclist', timezone='America/Los_Angeles', legacy=True)
    rng = random.Random(5)
    ids = iter(range(1100000, 1200000))
    steps, rhr = {}, {}
    day = date(2026, 10, 8)
    while day <= date(2026, 10, 27):
        midnight = datetime.combine(day, time(0), la)
        steps[day], rhr[day] = rng.randint(4000, 14000), 58 + rng.randint(-3, 3)
        p.add('DailyStats', midnight, {'totalSteps': steps[day], 'restingHeartRate': rhr[day]}, day)
        p.add('SleepSummary', midnight + timedelta(hours=7), {'sleepTimeSeconds': 26000, 'sleepScore': 75, 'restingHeartRate': rhr[day]}, day)
        day += timedelta(1)
    p.add('VO2_Max', datetime(2026, 10, 18, tzinfo=timezone.utc), {'VO2_max_value': None, 'VO2_max_value_cycling': 55.0}, date(2026, 10, 18))
    rides = []
    for day, hour, hours in ((date(2026, 10, 25), 22.5, 1.0),    # Sunday 22:30 local is Monday 05:30 UTC
                             (date(2026, 10, 22), 23.5, 1.25)):  # crosses local midnight
        start = datetime.combine(day, time(0), la) + timedelta(hours=hour)
        aid = next(ids)
        tags = {'ActivityID': aid, 'ActivitySelector': start.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SUTC-') + 'road_biking'}
        p.add('ActivitySummary', start, {'Activity_ID': aid, 'activityName': 'Night ride', 'activityType': 'road_biking',
              'elapsedDuration': hours * 3600, 'distance': 30000.0, 'averageHR': 130, 'activityTrainingLoad': 90}, day, tags)
        p.add('ActivitySummary', start + timedelta(hours=hours), {'Activity_ID': aid, 'activityName': 'END', 'activityType': 'No Activity'}, day, tags)
        rides.append((day, hours))
    battery = []
    for n in range(12):  # Home Assistant watch battery readings, imported under the unit name "%"
        when = datetime(2026, 10, 27, 12, tzinfo=timezone.utc) + timedelta(minutes=10 * n)
        battery.append(80 - n)
        p.records.append(Record.from_mapping({'measurement': '%', 'time': when.isoformat(),
                         'tags': {'domain': 'sensor', 'source': 'hass', 'entity_id': 'garmin_device_battery_level'}, 'fields': {'value': 80.0 - n}}))
    p.expected = {'steps': steps, 'rhr': rhr, 'rides': rides, 'vo2': 55.0, 'battery': battery}
    return p


@functools.cache
def newcomer():
    """Five days with a new watch: not enough history for usual ranges, averages or training load."""
    p = Persona('persona-newcomer')
    for n in range(5):
        day = TODAY - timedelta(days=4 - n)
        p.add('DailyStats', at(day, 0), {'totalSteps': 8000 + 500 * n, 'restingHeartRate': 60, 'bodyBatteryAtWakeTime': 70}, day)
        p.add('SleepSummary', at(day, 7), {'sleepTimeSeconds': 27000, 'sleepScore': 78, 'avgOvernightHrv': 40.0 + n, 'restingHeartRate': 60}, day)
    p.expected = {'days': [TODAY - timedelta(days=4 - n) for n in range(5)], 'hrv': [40.0 + n for n in range(5)]}
    return p


PERSONAS = (ski_tourer, healthy_lifestyle, gym_goer, legacy_cyclist, newcomer)
