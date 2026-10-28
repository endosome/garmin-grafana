"""Execute every provisioned dashboard query against PostgreSQL with smoke-test macros.

Run with PG* connection settings. Does not mutate data. Empty and fixture tests
also use expand_query() to assert query result schemas and numerical semantics.
"""
import json
from pathlib import Path
import re
from garmin_grafana.storage import connect


def expand_query(query, source=1):
    query = query.replace('${Source:csv}', str(source))
    query = query.replace('${TimeZone:sqlstring}', "'UTC'")
    query = query.replace('${__interval}', '5 minutes')
    for variable in ('ActivityGPS', 'ExerciseCategory', 'ExerciseLabel'):
        query = query.replace('${' + variable + ':sqlstring}', "'__all'")
    query = query.replace('${SeasonStart:sqlstring}', "'12-01'").replace('${VerticalGoal:sqlstring}', "'2000'")
    query = re.sub(r'\$__timeFilter\(([^)]+)\)', r"\1 BETWEEN '2026-01-01T00:00:00Z'::timestamptz AND '2026-01-10T00:00:00Z'::timestamptz", query)
    query = query.replace('$__timeFrom()', "'2026-01-01T00:00:00Z'").replace('$__timeTo()', "'2026-01-10T00:00:00Z'")
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
