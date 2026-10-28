"""Rebuild tests/data/fitbit_6962181067.csv.gz from the public Fitbit export.

Source: Furberg, Brinton, Keating and Ortiz, "Crowd-sourced Fitbit datasets
03.12.2016-05.12.2016", Zenodo, https://doi.org/10.5281/zenodo.53894, CC BY 4.0.
Download mturkfitbit_export_4.12.16-5.12.16.zip, unzip it, and run
    python -I tests/data/extract_fitbit_persona.py "<unzipped>/Fitabase Data 4.12.16-5.12.16"
The excerpt keeps one participant at Garmin's cadence: daily totals, steps per
15 minutes, mean heart rate per 2 minutes, sleep as runs of one Fitbit level
(1 asleep, 2 restless, 3 awake) and weight logs. Times are Fitbit local times.
"""
import csv
from collections import defaultdict
from datetime import datetime
import gzip
from pathlib import Path
import sys

USER = '6962181067'


def rows(folder, name):
    with open(Path(folder) / name, newline='') as fh:
        yield from (r for r in csv.DictReader(fh) if r['Id'] == USER)


def stamp(value):
    return datetime.strptime(value, '%m/%d/%Y %I:%M:%S %p')


def main(folder):
    out = []
    for r in rows(folder, 'dailyActivity_merged.csv'):
        day = datetime.strptime(r['ActivityDate'], '%m/%d/%Y').date()
        out.append(['day', day.isoformat(), r['TotalSteps'], r['TotalDistance'], r['VeryActiveMinutes'], r['FairlyActiveMinutes'],
                    r['SedentaryMinutes'], r['Calories']])
    steps = defaultdict(int)
    for r in rows(folder, 'minuteStepsNarrow_merged.csv'):
        t = stamp(r['ActivityMinute'])
        steps[t.replace(minute=t.minute - t.minute % 15)] += int(r['Steps'])
    out += [['steps15', t.isoformat(timespec='minutes'), n] for t, n in sorted(steps.items())]
    beats = defaultdict(list)
    for r in rows(folder, 'heartrate_seconds_merged.csv'):
        t = stamp(r['Time'])
        beats[t.replace(minute=t.minute - t.minute % 2, second=0)].append(int(r['Value']))
    out += [['hr2', t.isoformat(timespec='minutes'), round(sum(v) / len(v))] for t, v in sorted(beats.items())]
    segment = None
    for r in sorted(rows(folder, 'minuteSleep_merged.csv'), key=lambda r: stamp(r['date'])):
        t, level = stamp(r['date']).replace(second=0), int(r['value'])
        if segment and segment[1] == level and (t - segment[0]).total_seconds() == segment[2] * 60:
            segment[2] += 1
        else:
            if segment:
                out.append(['sleep', segment[0].isoformat(timespec='minutes'), segment[1], segment[2]])
            segment = [t, level, 1]
    out.append(['sleep', segment[0].isoformat(timespec='minutes'), segment[1], segment[2]])
    for r in rows(folder, 'weightLogInfo_merged.csv'):
        out.append(['weight', stamp(r['Date']).isoformat(timespec='minutes'), round(float(r['WeightKg']), 2)])
    target = Path(__file__).with_name(f'fitbit_{USER}.csv.gz')
    with gzip.open(target, 'wt', newline='') as fh:
        csv.writer(fh).writerows(out)
    print(target, len(out), 'rows')


if __name__ == '__main__':
    main(sys.argv[1])
