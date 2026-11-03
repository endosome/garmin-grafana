"""Dashboard queries as the tests run them: SQL by label, panel ids by title, macro expansion."""
from datetime import timezone
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('queries', ROOT / 'scripts/validate_dashboard_queries.py')
queries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queries)
STATEMENTS = {label: sql for label, sql, fmt in queries.dashboard_queries()}
FORMATS = {label: fmt for label, sql, fmt in queries.dashboard_queries()}


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
