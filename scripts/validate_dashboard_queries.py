"""Execute every provisioned dashboard query against PostgreSQL with smoke-test macros.

Run with PG* connection settings. Does not mutate data. Empty and fixture tests
also use expand_query() to assert query result schemas and numerical semantics.
"""
import json
from pathlib import Path
import re
from garmin_grafana.storage import connect


# Grafana rounds range / max data points to these steps; 1000 points approximates a full-width panel.
STEPS = [(s, f'{s} seconds') for s in (1, 2, 5, 10, 15, 20, 30)] + [(m * 60, f'{m} minutes') for m in (1, 2, 5, 10, 15, 20, 30)] \
    + [(h * 3600, f'{h} hours') for h in (1, 2, 3, 6, 12)] + [(86400, '1 day'), (7 * 86400, '7 days')]
UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def seconds(text):
    """Length of a Grafana relative range such as 24h or 14d."""
    return int(text[:-1]) * UNITS[text[-1]]


def interval(range_seconds, minimum=None):
    floor = max(range_seconds / 1000, seconds(minimum) if minimum else 0)
    return next((label for step, label in STEPS if step >= floor), STEPS[-1][1])


def pinned_ranges():
    """{'File.json:panel id': seconds} for panels whose own `timeFrom` overrides the dashboard's time range."""
    def walk(items):
        for panel in items:
            yield panel
            yield from walk(panel.get('panels', []))
    root = Path(__file__).resolve().parents[1]
    return {path.name + ':' + str(panel['id']): seconds(panel['timeFrom'])
            for path in (root / 'Grafana_Dashboard').glob('*.json')
            for panel in walk(json.loads(path.read_text())['panels']) if panel.get('timeFrom')}


def expand_query(query, source=1, *, start='2026-01-01T00:00:00Z', end='2026-01-10T00:00:00Z', timezone='UTC', activity='__all', category='__all', interval='5 minutes'):
    query = query.replace('${Source:csv}', str(source))
    query = query.replace('${TimeZone:sqlstring}', f"'{timezone}'")
    query = query.replace('${__interval}', interval)
    query = query.replace('${ActivityGPS:sqlstring}', f"'{activity}'")
    query = query.replace('${ExerciseCategory:sqlstring}', f"'{category}'").replace('${ExerciseLabel:sqlstring}', "'__all'")
    query = query.replace('${SeasonStart:sqlstring}', "'12-01'").replace('${VerticalGoal:sqlstring}', "'2000'")
    query = re.sub(r'\$__timeFilter\(([^)]+)\)', rf"\1 BETWEEN '{start}'::timestamptz AND '{end}'::timestamptz", query)
    query = query.replace('$__timeFrom()', f"'{start}'").replace('$__timeTo()', f"'{end}'")
    return query


def dashboard_queries():
    def panels(items):
        for panel in items:
            for target in panel.get('targets', []):
                yield str(panel['id']) + ':' + target['refId'], target['rawSql'], target['format']
            yield from panels(panel.get('panels', []))
    root = Path(__file__).resolve().parents[1]
    for path in (root / 'Grafana_Dashboard').glob('*.json'):
        dashboard = json.loads(path.read_text())
        for label, query, fmt in panels(dashboard['panels']):
            yield path.name + ':' + label, query, fmt
        for variable in dashboard['templating']['list']:
            if variable['type'] == 'query':
                yield path.name + ':' + variable['name'], variable['query'], 'variable'


def main():
    with connect() as connection, connection.cursor() as cur:
        count = 0
        for label, query, fmt in dashboard_queries():
            try:
                cur.execute(expand_query(query))
                cur.fetchall()
            except Exception as error:
                raise RuntimeError('Dashboard SQL failed: ' + label) from error
            count += 1
        print(f'Executed {count} dashboard and variable queries')


if __name__ == '__main__':
    main()
