"""The provisioned dashboards as data, for the lint and contract tests."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / 'Grafana_Dashboard'
PATHS = sorted(DASHBOARD_DIR.glob('*.json'))
# Grafana's own variables, which need no definition on the dashboard.
BUILT_IN_VARIABLES = {'__interval', '__interval_ms', '__from', '__to', '__timeFrom', '__timeTo', '__timeFilter', '__dashboard', '__org', '__user'}


def load(path):
    return json.loads(path.read_text())


def walk(items):
    """Every panel, rows and the panels nested in collapsed rows included."""
    for panel in items:
        yield panel
        yield from walk(panel.get('panels', []))


def dashboards():
    return {path.name: load(path) for path in PATHS}


def all_panels():
    """(file name, dashboard, panel) for every panel of every dashboard."""
    return [(name, dashboard, panel) for name, dashboard in dashboards().items() for panel in walk(dashboard['panels'])]


def targets(panel):
    return panel.get('targets', [])


def sql_of(panel):
    return '\n'.join(target.get('rawSql', '') for target in targets(panel))


def variables_used(text):
    """Names of the template variables a piece of text refers to."""
    return set(re.findall(r'\$\{(\w+)(?::\w+)?\}', text)) | set(re.findall(r'\$(\w+)', text))


def compose_plugins(service='grafana'):
    """Plugin ids pinned in GF_PLUGINS_PREINSTALL of a compose-example.yml service."""
    compose = (ROOT / 'compose-example.yml').read_text()
    start = compose.index(f'\n  {service}:')
    block = compose[start + 1:]
    nxt = re.search(r'\n  \S', block[3:])
    block = block[:nxt.start() + 3] if nxt else block
    line = re.search(r'GF_PLUGINS_PREINSTALL:\s*(\S+)', block).group(1)
    return {entry.split('@')[0] for entry in line.split(',')}
