"""Time every provisioned dashboard query against a year of synthetic data.

Run with PG* settings for a disposable database, never your real health database.
`seed` writes the healthy-lifestyle persona with a full year of intraday series at
Garmin's cadence and a 5-second GPS track for every run (a few minutes, about
1.3 million rows). `run` executes each query with the panel's pinned range, or the
selected range, ending at the persona's "now", and prints database time per panel (--json also saves the shared buffers each query
touches, which scripts/check_dashboard_budgets.py compares with tests/data/dashboard_budgets.json):

    uv run --locked python scripts/bench_dashboards.py seed
    uv run --locked python scripts/bench_dashboards.py run --days 30 --days 365 [--json out.json]

Grafana and browser time are not measured here.
"""
import argparse
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import personas  # noqa: E402
from garmin_grafana.storage import PostgresStore, connect  # noqa: E402
from validate_dashboard_queries import expand_query, interval, seconds  # noqa: E402

ACCOUNT = 'bench-year'
END = personas.NOW


def seed():
    p = personas.healthy_lifestyle()
    p.account = ACCOUNT
    rng = personas.random.Random(5)
    summaries = [r for r in p.records if r.dataset == 'ActivitySummary' and r.values.get('activityType') == 'running'
                 and r.values.get('activityName') != 'END' and r.local_date < date(2026, 10, 25)]
    day = date(2025, 10, 28)
    while day < date(2026, 10, 25):
        personas.intraday(p, rng, day, personas.at(day, -1), personas.at(day, 6, 50), 55, 46, [], personas.at(day, 24))
        day += timedelta(1)
    for summary in summaries:
        personas.gps(p, summary)
    store = PostgresStore(account=ACCOUNT)
    try:
        for offset in range(0, len(p.records), 100000):
            store.write(p.records[offset:offset + 100000])
            print(f'{min(offset + 100000, len(p.records))}/{len(p.records)} records', flush=True)
    finally:
        store.close()
    with connect() as connection:
        connection.autocommit = True
        connection.execute('VACUUM ANALYZE')


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get('panels', []))


def run(days, output):
    root = Path(__file__).resolve().parents[1] / 'Grafana_Dashboard'
    results = {}
    with connect() as connection, connection.cursor() as cur:
        cur.execute("SELECT s.id FROM sources s WHERE s.account=%s", (ACCOUNT,))
        source = cur.fetchone()[0]
        selector = cur.execute("SELECT tags->>'ActivitySelector' FROM samples WHERE source_id=%s AND dataset='ActivityGPS' ORDER BY time DESC LIMIT 1",
                               (source,)).fetchone()[0]
        for span in days:
            totals, rows, blocks = {}, [], {}
            for path in sorted(root.glob('*.json')):
                dashboard = json.loads(path.read_text())
                for panel in panels(dashboard['panels']):
                    pinned = panel.get('timeFrom')
                    range_seconds = seconds(pinned) if pinned else span * 86400
                    start = END - timedelta(seconds=range_seconds)
                    for target in panel.get('targets', []):
                        query = expand_query(target['rawSql'], source, start=start.isoformat(), end=END.isoformat(),
                                             timezone=personas.TZ_NAME, activity=selector,
                                             interval=interval(range_seconds, panel.get('interval')))
                        timings = []
                        for _ in range(3):
                            began = time.perf_counter()
                            cur.execute(query)
                            count = len(cur.fetchall())
                            timings.append(time.perf_counter() - began)
                        label = f"{path.stem}:{panel['id']}:{target['refId']}"
                        plan = cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query).fetchone()[0][0]['Plan']
                        blocks[label] = plan.get('Shared Hit Blocks', 0) + plan.get('Shared Read Blocks', 0)
                        rows.append((statistics.median(timings), count, label, panel.get('title', '')))
                        totals[path.stem] = totals.get(path.stem, 0) + statistics.median(timings)
            rows.sort(reverse=True)
            print(f'\n== {span} days: {sum(totals.values()):.2f} s total over {len(rows)} queries')
            for name, total in totals.items():
                print(f'  {name}: {total:.2f} s')
            for elapsed, count, label, title in rows[:15]:
                print(f'  {elapsed * 1000:8.1f} ms {count:7d} rows  {label}  {title}')
            results[span] = {label: {'ms': round(elapsed * 1000, 2), 'rows': count, 'buffers': blocks[label]} for elapsed, count, label, _ in rows}
    if output:
        Path(output).write_text(json.dumps(results, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('seed', 'run'))
    parser.add_argument('--days', type=int, action='append')
    parser.add_argument('--json')
    args = parser.parse_args()
    if args.command == 'seed':
        seed()
    else:
        run(args.days or [30, 365], args.json)


if __name__ == '__main__':
    main()
