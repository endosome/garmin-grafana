"""Static rules for the provisioned dashboards (docs/dashboard-standards.md). No database, no Grafana.

A rule an old panel still breaks is ratcheted: tests/data/lint_ratchet.json lists today's offenders,
which may only shrink. The test also fails when a listed panel now passes, so the entry gets removed.
"""
from collections import Counter
import json
import os
import re
import pytest
from dashboards import BUILT_IN_VARIABLES, ROOT, all_panels, compose_plugins, dashboards, sql_of, targets, variables_used, walk

RATCHET_FILE = ROOT / 'tests/data/lint_ratchet.json'
RATCHET = json.loads(RATCHET_FILE.read_text())
DASHBOARDS = dashboards()
PANELS = all_panels()
UIDS = {
    'Garmin-Grafana-Dashboard.json': 'feiibsx498gsgb',
    'Garmin-Health-Overview.json': 'garmin-health-overview',
    'Garmin-Ski-Season.json': 'garmin-ski-season',
    'Garmin-Strength-Training-Dashboard.json': 'garmin-strength-training',
}
CORE_PANELS = {'row', 'stat', 'gauge', 'bargauge', 'timeseries', 'barchart', 'table', 'text', 'piechart', 'state-timeline', 'status-history',
               'geomap', 'heatmap', 'histogram', 'trend', 'xychart', 'candlestick', 'canvas', 'logs', 'news', 'nodeGraph'}
UNITS = {'none', 'short', 's', 'ms', 'dthms', 'clocks', 'percent', 'celsius', 'kg', 'masskg', 'massg', 'velocitykmh', 'lengthkm', 'lengthm',
         'locale', 'dateTimeFromNow', 'BPM', 'bpm'}
UNIT_PREFIXES = ('suffix:', 'time:')
FORMATS = {'time_series', 'table'}
DATASOURCES = {uid for path in (ROOT / 'Grafana_Datasource').glob('*.yaml') for uid in re.findall(r'^\s+uid:\s*(\S+)', path.read_text(), re.M)}
READ_TABLES = re.compile(r'\b(?:records|samples|all_records|samples_hourly|gps_cells_daily|hourly_medians|wear_hourly)\b')
SOURCE_FILTER = re.compile(r'source_id\s+IN\s*\(\s*\$\{Source:csv\}\s*\)')


def ratchet(rule, found):
    """Offenders must equal the recorded list: new ones fail, fixed ones must leave the list."""
    found, allowed = sorted(set(found)), RATCHET.get(rule, [])
    if os.getenv('LINT_UPDATE_RATCHET') == '1':  # regenerate after fixing panels, then review the diff
        RATCHET[rule] = allowed = found
        RATCHET_FILE.write_text(json.dumps(RATCHET, indent=2, sort_keys=True) + '\n')
    assert found == allowed, (f'{rule}: new offenders {sorted(set(found) - set(allowed))}; '
                              f'fixed, remove from tests/data/lint_ratchet.json: {sorted(set(allowed) - set(found))}')


def label(name, panel):
    return f"{name}:{panel['id']}"


def units_of(panel):
    defaults = panel.get('fieldConfig', {}).get('defaults', {})
    found = [defaults.get('unit')]
    found += [prop['value'] for override in panel.get('fieldConfig', {}).get('overrides', []) for prop in override['properties'] if prop['id'] == 'unit']
    return [unit for unit in found if unit]


# Files

@pytest.mark.parametrize('name', DASHBOARDS)
def test_file_is_canonical_json(name):
    text = (ROOT / 'Grafana_Dashboard' / name).read_text()
    assert text == json.dumps(json.loads(text), indent=2) + '\n'


def test_uids_are_the_pinned_ones():
    # Links and bookmarks use the uid: renaming one is a deliberate change of this map.
    assert {name: d['uid'] for name, d in DASHBOARDS.items()} == UIDS


@pytest.mark.parametrize('name', DASHBOARDS)
def test_dashboard_settings(name):
    d = DASHBOARDS[name]
    assert 'garmin' in d['tags'] and len(d['tags']) >= 2, 'garmin plus a topic'
    assert (d.get('weekStart'), d.get('graphTooltip'), d.get('timezone')) == ('monday', 1, 'browser')
    assert d.get('description')
    assert d['schemaVersion'] == 41  # what Grafana 13.0.2 writes; the drift check in tests/test_grafana_stack.py compares the rest


# Panels

@pytest.mark.parametrize('name', DASHBOARDS)
def test_panel_ids_are_unique_and_the_layout_fits(name):
    d = DASHBOARDS[name]
    ids = [panel['id'] for panel in walk(d['panels'])]
    assert len(ids) == len(set(ids))

    def check(level, where):
        boxes = [(p['id'], p['gridPos']) for p in level]
        for pid, g in boxes:
            assert 0 <= g['x'] and g['x'] + g['w'] <= 24 and g['w'] > 0 and g['h'] > 0, (where, pid)
        for i, (a, ga) in enumerate(boxes):
            for b, gb in boxes[i + 1:]:
                overlap = ga['x'] < gb['x'] + gb['w'] and gb['x'] < ga['x'] + ga['w'] and ga['y'] < gb['y'] + gb['h'] and gb['y'] < ga['y'] + ga['h']
                assert not overlap, (where, a, b)
    check(d['panels'], name)
    for row in d['panels']:
        check(row.get('panels', []), f"{name} row {row['id']}")


def test_every_panel_has_a_description():
    ratchet('description', (label(n, p) for n, _, p in PANELS if p['type'] != 'row' and not p.get('description')))


def test_panel_types_are_installed():
    main, kiosk = compose_plugins('grafana'), compose_plugins('grafana-kiosk')
    hidden = {int(i) for i in re.findall(r'grid-item-(\d+)', (ROOT / 'Grafana_Kiosk/index.html').read_text())}
    for name, _, panel in PANELS:
        assert panel['type'] in CORE_PANELS | main, (name, panel['id'], panel['type'])
    # The kiosk can show any of the dashboards (KIOSK_DASHBOARD): a panel it cannot draw must be hidden by its page.
    for name, _, panel in PANELS:
        assert panel['type'] in CORE_PANELS | kiosk or panel['id'] in hidden, (name, panel['id'], panel['type'])


# Queries

def test_dashboard_sql_takes_now_from_the_time_range():
    # A frozen browser clock then controls every range.
    for name, _, panel in PANELS:
        assert not re.search(r'\bnow\s*\(\)|\bcurrent_(date|timestamp|time)\b|\blocaltimestamp\b|\bclock_timestamp\b', sql_of(panel), re.I), (name, panel['id'])
    for name, d in DASHBOARDS.items():
        for variable in d['templating']['list']:
            assert not re.search(r'\bnow\s*\(\)|\bcurrent_date\b', variable.get('query', ''), re.I), (name, variable['name'])


def test_targets_are_code_queries_on_a_provisioned_datasource():
    for name, _, panel in PANELS:
        for target in targets(panel):
            where = (name, panel['id'], target['refId'])
            assert target['datasource']['uid'] in DATASOURCES, where
            assert target['rawQuery'] is True and target['editorMode'] == 'code', where
            assert target['format'] in FORMATS, where
            assert not target.get('hide'), where
            assert panel.get('datasource', target['datasource'])['uid'] == target['datasource']['uid'], where


def test_every_query_on_health_data_filters_on_the_selected_sources():
    offenders = []
    for name, d, panel in PANELS:
        for target in targets(panel):
            sql = target['rawSql']
            if READ_TABLES.search(sql) and not SOURCE_FILTER.search(sql):
                offenders.append(f"{label(name, panel)}:{target['refId']}")
    for name, d in DASHBOARDS.items():
        for variable in d['templating']['list']:
            if variable['type'] == 'query' and READ_TABLES.search(variable['query']) and not SOURCE_FILTER.search(variable['query']):
                offenders.append(f"{name}:{variable['name']}")
    ratchet('source-filter', offenders)


# Variables

@pytest.mark.parametrize('name', DASHBOARDS)
def test_variables_are_defined_used_and_safely_formatted(name):
    d = DASHBOARDS[name]
    defined = {v['name'] for v in d['templating']['list']}
    text = '\n'.join(str(x) for panel in walk(d['panels']) for x in [sql_of(panel), panel.get('title', ''), panel.get('repeat', '')])
    text += '\n'.join(v.get('query', '') if isinstance(v.get('query'), str) else '' for v in d['templating']['list'])
    used = {v for v in variables_used(text) if v not in BUILT_IN_VARIABLES}
    assert used <= defined, f'undefined: {used - defined}'
    assert defined <= used | {v['name'] for v in d['templating']['list'] if v['type'] in ('custom', 'constant', 'textbox')}, f'unused: {defined - used}'
    formats = Counter(re.findall(r'\$\{(\w+):(\w+)\}', text))
    for (variable, fmt), _ in formats.items():
        if variable in BUILT_IN_VARIABLES:
            continue
        assert fmt in {'csv', 'sqlstring'}, (variable, fmt)
        assert (fmt == 'csv') == (variable == 'Source'), f'{variable} must be {":csv" if variable == "Source" else ":sqlstring"}, not :{fmt}'
    assert not re.findall(r'\$\{(?:Source)\}|\$Source\b', text), 'Source needs :csv'


# Units

def test_units_come_from_the_allow_list():
    for name, _, panel in PANELS:
        for unit in units_of(panel):
            assert unit in UNITS or unit.startswith(UNIT_PREFIXES), (name, panel['id'], unit)


def test_durations_are_shown_as_clocks_dthms_or_seconds():
    offenders = []
    for name, _, panel in PANELS:
        defaults = panel.get('fieldConfig', {}).get('defaults', {})
        if defaults.get('displayName') == 'Duration' and defaults.get('unit') not in ('clocks', 'dthms', 's'):
            offenders.append(label(name, panel))
        for override in panel.get('fieldConfig', {}).get('overrides', []):
            if override['matcher']['id'] == 'byName' and override['matcher']['options'] == 'Duration':
                unit = next((p['value'] for p in override['properties'] if p['id'] == 'unit'), defaults.get('unit'))
                if unit not in ('clocks', 'dthms', 's'):
                    offenders.append(label(name, panel))
    ratchet('duration-unit', offenders)


def test_no_value_is_an_en_dash():
    offenders = [label(n, p) for n, _, p in PANELS
                 if p['type'] in ('stat', 'gauge', 'table') and p.get('fieldConfig', {}).get('defaults', {}).get('noValue') != '–']
    ratchet('no-value', offenders)


def test_tiles_with_a_pinned_range_hide_the_override_text():
    ratchet('hide-time-override', (label(n, p) for n, _, p in PANELS if p['type'] == 'stat' and p.get('timeFrom') and not p.get('hideTimeOverride')))


# Links

@pytest.mark.parametrize('name', DASHBOARDS)
def test_dashboard_links_exist_and_keep_the_range_and_variables(name):
    for link in DASHBOARDS[name]['links']:
        if link['url'].startswith('/d/'):
            assert link['url'][3:] in UIDS.values() and link['url'][3:] != DASHBOARDS[name]['uid'], link['url']
            assert link['keepTime'] is True and link['includeVars'] is True, link['title']
        else:
            assert link['url'].startswith('https://') and link['targetBlank'] is True, link['url']


# Structure (moved from test_storage.py)

def test_dashboard_rows_nest_only_when_collapsed():
    for name, d in DASHBOARDS.items():
        for row in (panel for panel in d['panels'] if panel['type'] == 'row'):
            assert bool(row.get('panels')) == row['collapsed'], (name, row['title'])
            assert all(panel['gridPos']['y'] > row['gridPos']['y'] for panel in row.get('panels', []))


def test_stat_field_regex_matches_the_display_name():
    # Grafana matches reduceOptions.fields against the display name, so a renamed field
    # selected by its column name shows "No data".
    for name, _, panel in PANELS:
        fields = panel.get('options', {}).get('reduceOptions', {}).get('fields', '')
        display = panel.get('fieldConfig', {}).get('defaults', {}).get('displayName')
        if panel['type'] == 'stat' and fields.startswith('/') and display:
            assert re.search(fields.strip('/'), display), (name, panel['id'])


def test_dashboard_style_overrides_match_a_field():
    # An override for a field no query or transformation produces does nothing; they piled up in Garmin Stats.
    for name, _, panel in PANELS:
        sql = sql_of(panel)
        if re.search(r'AS\s+metric\b', sql):
            continue  # series named by the data, e.g. one per sport
        names = {'Time'} | set(re.findall(r'AS\s+"([^"]+)"', sql)) | set(re.findall(r'AS\s+(\w+)', sql))
        for transformation in panel.get('transformations', []):
            options = transformation.get('options', {})
            names |= {options.get('alias')} | set((options.get('renameByName') or {}).values())
        overrides = panel.get('fieldConfig', {}).get('overrides', [])
        names |= {p['value'] for o in overrides for p in o['properties'] if p['id'] == 'displayName'}
        for override in overrides:
            if override['matcher']['id'] == 'byName':
                assert override['matcher']['options'] in names, (name, panel['id'], override['matcher']['options'])


def test_dashboard_all_values_are_sql_literals():
    # Grafana inserts a custom "All" value unformatted, even for ${Name:sqlstring}.
    for name, d in DASHBOARDS.items():
        for variable in d['templating']['list']:
            if variable.get('allValue'):
                assert re.fullmatch(r"'[^']*'", variable['allValue']), (name, variable['name'])


def test_weekly_bars_cover_their_week():
    # Weekly rows are stamped with the Monday the week starts, so the bar must be drawn after it.
    for name, _, panel in PANELS:
        custom = panel.get('fieldConfig', {}).get('defaults', {}).get('custom', {})
        if custom.get('drawStyle') == 'bars' and "date_trunc('week'" in sql_of(panel):
            assert custom.get('barAlignment') == 1, (name, panel['id'])


def test_dashboard_colours_are_grafana_colours():
    # An unknown name such as 'grey' makes Grafana fail to draw the panel.
    hues = 'red|orange|yellow|green|blue|purple'
    valid = re.compile(rf'^((super-light-|light-|semi-dark-|dark-)?({hues})|text|transparent|#[0-9a-fA-F]{{3,8}}|rgba?\(.*\))$')
    for path in (ROOT / 'Grafana_Dashboard').glob('*.json'):
        for colour in re.findall(r'"(?:fixedColor|color)": "([^"]*)"', path.read_text()):
            assert valid.match(colour), (path.name, colour)


# Registry (tests/panel_registry.py)

def test_every_panel_is_in_the_registry():
    import panel_registry as registry
    panels = {label(n, p) for n, _, p in PANELS if p['type'] != 'row'}
    listed = [set(registry.VALUE_TESTS), set(registry.NO_VALUES), set(registry.NOT_YET)]
    assert sum(len(part) for part in listed) == len(set().union(*listed)), 'a panel is listed twice'
    assert set().union(*listed) == panels, (f'not in the registry: {sorted(panels - set().union(*listed))}; '
                                            f'no longer a panel: {sorted(set().union(*listed) - panels)}')
    named = {test for tests in registry.VALUE_TESTS.values() for test in tests}
    for test in named:  # a renamed or removed test must be renamed here too
        path, function = test.split('::')
        assert re.search(rf'^def {function}\b', (ROOT / 'tests' / path).read_text(), re.M), test
