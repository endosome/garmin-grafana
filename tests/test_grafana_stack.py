"""The Compose stack over HTTP: provisioning, plugins, every query as the Grafana frontend sends it, schema drift and security.

Skipped unless GRAFANA_URL is set (the main instance; GRAFANA_USER defaults to admin, GRAFANA_PASSWORD is required).
KIOSK_URL (the kiosk behind nginx, without a path) enables the kiosk checks. The personas must be loaded
(scripts/load_personas.py). Everything is read-only except one insert into fetch_requests, which needs GRAFANA_ALLOW_WRITES=1:
never set it on a stack that runs garmin-fetch-data. Run the stack and these tests as in .github/workflows/tests.yml.
"""
from datetime import timedelta
import json
import os
import re
import shutil
import subprocess
import pytest
import requests
from dashboards import ROOT, compose_plugins, dashboards
from panel_queries import FORMATS, STATEMENTS, queries
from personas import NOW, healthy_lifestyle

pytestmark = pytest.mark.grafana
BASE = os.getenv('GRAFANA_URL', '').rstrip('/')
KIOSK = os.getenv('KIOSK_URL', '').rstrip('/')
KIOSK_DASHBOARD = os.getenv('KIOSK_DASHBOARD', 'Garmin-Health-Overview.json')
PINNED = {line.split('@')[0]: line.split('@')[1] for line in re.search(r'GF_PLUGINS_PREINSTALL: (\S+)', (ROOT / 'compose-example.yml').read_text()).group(1).split(',')}
READER = {'uid': 'garmin_postgres', 'type': 'grafana-postgresql-datasource'}
FETCH = {'uid': 'garmin_fetch_request', 'type': 'grafana-postgresql-datasource'}
SCHEMAS = json.loads((ROOT / 'tests/data/panel_schemas.json').read_text())
needs_grafana = pytest.mark.skipif(not BASE, reason='Set GRAFANA_URL to a running Compose stack with the personas loaded')
needs_kiosk = pytest.mark.skipif(not KIOSK, reason='Set KIOSK_URL to the kiosk behind nginx')


@pytest.fixture(scope='module')
def grafana():
    session = requests.Session()
    session.auth = (os.getenv('GRAFANA_USER', 'admin'), os.environ['GRAFANA_PASSWORD'])
    session.timeout = 60
    yield session
    session.close()


def get(session, path, **kwargs):
    return session.get(BASE + path, timeout=60, **kwargs)


def query(session, rows, base=BASE, **window):
    body = {'queries': rows, **window}
    return session.post(base + '/api/ds/query', json=body, timeout=120)


def frontend_interval(seconds):
    """The ${__interval} text the frontend substitutes: Grafana's duration syntax of the step it picks for a full-width panel."""
    number, unit = queries.interval(seconds).split()
    return number + ('d' if unit.startswith('day') else unit[0])


# Provisioning

@needs_grafana
def test_exactly_the_provisioned_dashboards_are_listed_and_come_from_their_files(grafana):
    listed = {d['uid'] for d in get(grafana, '/api/search', params={'type': 'dash-db'}).json()}
    assert listed == {d['uid'] for d in dashboards().values()}
    for name, dashboard in dashboards().items():
        meta = get(grafana, f"/api/dashboards/uid/{dashboard['uid']}").json()['meta']
        assert meta['provisionedExternalId'] == name


@needs_grafana
def test_health_of_the_datasource(grafana):
    assert get(grafana, '/api/datasources/uid/garmin_postgres/health').json()['status'] == 'OK'
    assert get(grafana, '/api/datasources/uid/garmin_fetch_request/health').json()['status'] == 'OK'


@needs_grafana
def test_grafana_returns_each_dashboard_as_its_file(grafana):
    """Drift: a Grafana or schema change that migrates a dashboard on load shows here, not in a user's browser."""
    for name, dashboard in dashboards().items():
        served = get(grafana, f"/api/dashboards/uid/{dashboard['uid']}").json()['dashboard']
        ignored = ('id', 'version')  # Grafana numbers dashboards and counts its own saves
        assert {k: v for k, v in served.items() if k not in ignored} == {k: v for k, v in dashboard.items() if k not in ignored}, name


@needs_grafana
def test_logs_have_no_provisioning_or_plugin_errors():
    if not shutil.which('docker'):
        pytest.skip('no docker CLI to read the container logs')
    logs = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compose-example.yml'), '--profile', 'kiosk', 'logs', '--no-color', 'grafana', 'grafana-kiosk'],
                          capture_output=True, text=True, timeout=60).stdout
    if not logs:
        pytest.skip('no Compose logs for this stack')
    errors = [line for line in logs.splitlines() if 'level=error' in line and (re.search(r'logger=(provisioning|plugin\.loader|plugins)', line) or any(p in line for p in PINNED))]
    assert errors == []


# Plugins

@needs_grafana
def test_the_pinned_plugins_are_installed(grafana):
    for plugin, version in PINNED.items():
        assert get(grafana, f'/api/plugins/{plugin}/settings').json()['info']['version'] == version, plugin


@needs_kiosk
def test_the_kiosk_has_the_heatmap_plugin_and_not_business_forms():
    assert requests.get(KIOSK + '/grafana/api/plugins/marcusolsson-hourly-heatmap-panel/settings', timeout=30).ok
    assert requests.get(KIOSK + '/grafana/api/plugins/volkovlabs-form-panel/settings', timeout=30).status_code == 404
    assert compose_plugins('grafana-kiosk') == {'marcusolsson-hourly-heatmap-panel'}


# Queries

@pytest.fixture(scope='module')
def source(grafana):
    persona = healthy_lifestyle()
    result = query(grafana, [{'refId': 'A', 'datasource': READER, 'format': 'table', 'rawSql': f"SELECT id FROM sources WHERE account LIKE '{persona.account}%' ORDER BY id DESC LIMIT 1"}]).json()
    return result['results']['A']['frames'][0]['data']['values'][0][0]


@needs_grafana
@pytest.mark.parametrize('seconds', [86400, 30 * 86400, 365 * 86400], ids=['24h', '30d', '1y'])
def test_every_query_as_the_frontend_sends_it(grafana, source, seconds):
    """The frontend substitutes the template variables and ${__interval}; the datasource itself expands $__timeFilter,
    $__timeFrom() and $__timeTo(). Nothing may fail, warn, or return other fields than the column snapshot."""
    interval = frontend_interval(seconds)
    problems = {}
    for label, sql in STATEMENTS.items():
        fmt = FORMATS[label]
        row = {'refId': 'A', 'datasource': READER, 'format': 'table' if fmt == 'variable' else fmt, 'intervalMs': seconds, 'maxDataPoints': 1000,
               'rawSql': queries.expand_query(sql, source, timezone='Asia/Kolkata', interval=interval, macros=False)}
        response = query(grafana, [row], **{'from': str(int((NOW - timedelta(seconds=seconds)).timestamp() * 1000)), 'to': str(int(NOW.timestamp() * 1000))})
        result = response.json().get('results', {}).get('A', {}) if response.ok else {'error': response.text}
        if result.get('error'):
            problems[label] = result['error']
            continue
        for frame in result.get('frames', []):
            meta = frame['schema'].get('meta', {})
            fields = frame['schema']['fields']
            if any(n.get('severity') in ('warning', 'error') for n in meta.get('notices', [])):
                problems[label] = meta['notices']
            elif fmt == 'time_series' and fields and fields[0]['type'] != 'time':
                problems[label] = f"first field is {fields[0]['type']}, not time"
            elif fields and fmt != 'variable' and not re.search(r'AS\s+metric\b', sql) and [f['name'].lower() if f['type'] == 'time' else f['name'] for f in fields] != [c[0].lower() if c[1].startswith('timestamp') else c[0] for c in SCHEMAS[label]]:
                problems[label] = [f['name'] for f in fields]
    assert problems == {}


# Security

@needs_grafana
def test_the_main_datasource_cannot_write_and_the_fetch_one_only_inserts_into_fetch_requests(grafana):
    def attempt(datasource, sql):
        result = query(grafana, [{'refId': 'A', 'datasource': datasource, 'format': 'table', 'rawSql': sql}]).json()['results']['A']
        return result.get('error')
    for statement in ("INSERT INTO fetch_requests DEFAULT VALUES", "UPDATE app_info SET value = 'x'", "DELETE FROM fetch_requests", "DROP TABLE app_info"):
        assert attempt(READER, statement), statement
    for statement in ("UPDATE fetch_requests SET id = id", "DELETE FROM fetch_requests", "INSERT INTO app_info(key, value) VALUES ('x', 'y')", "SELECT * FROM sources"):
        assert attempt(FETCH, statement), statement
    if os.getenv('GRAFANA_ALLOW_WRITES') == '1':  # leaves one request behind, which starts a fetch on a stack that runs garmin-fetch-data
        assert attempt(FETCH, 'INSERT INTO fetch_requests DEFAULT VALUES') is None


# The kiosk (ported from scripts/smoke_kiosk.py)

@needs_kiosk
def test_the_kiosk_serves_one_dashboard_read_only_to_anonymous_viewers():
    page = requests.get(KIOSK + '/', timeout=30)
    page.raise_for_status()
    assert 'id="grafana"' in page.text and 'sandbox=' in page.text, 'Grafana must be in a sandboxed frame'
    listed = requests.get(KIOSK + '/grafana/api/search', params={'type': 'dash-db'}, timeout=30).json()
    assert [d['uid'] for d in listed] == [dashboards()[KIOSK_DASHBOARD]['uid']]
    saved = requests.post(KIOSK + '/grafana/api/dashboards/db', json={'dashboard': {'title': 'kiosk smoke'}, 'overwrite': True}, timeout=30)
    assert saved.status_code in (401, 403)
    attempt = query(requests.Session(), [{'refId': 'A', 'format': 'table', 'datasource': FETCH, 'rawSql': 'INSERT INTO fetch_requests DEFAULT VALUES'}], base=KIOSK + '/grafana')
    assert not attempt.ok or attempt.json().get('results', {}).get('A', {}).get('error'), 'anonymous kiosk viewers can start Garmin fetches'
    admin = requests.get(KIOSK + '/grafana/api/admin/settings', auth=('admin', os.environ['GRAFANA_PASSWORD']), timeout=30)
    assert admin.status_code != 200, 'the kiosk accepts password logins'
