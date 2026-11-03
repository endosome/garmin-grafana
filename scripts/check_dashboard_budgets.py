"""Compare a scripts/bench_dashboards.py --json result with tests/data/dashboard_budgets.json.

    uv run --locked python scripts/check_dashboard_budgets.py bench.json            # fail when over budget
    uv run --locked python scripts/check_dashboard_budgets.py bench.json --update   # record this run as the new budget

Per query, the shared buffers read (the same on any runner) may grow by 50 %. Per dashboard, the median
milliseconds at each span may double, because milliseconds on a shared runner are noisy. A budget change is a
reviewed diff, like a screenshot baseline: say in the commit message which panels got dearer and why.
"""
import json
from pathlib import Path
import statistics
import sys

BUDGETS = Path(__file__).resolve().parents[1] / 'tests/data/dashboard_budgets.json'
BUFFER_MARGIN, BUFFER_FLOOR, MS_MARGIN, MS_FLOOR = 1.5, 50, 2.0, 5.0


def measure(result):
    """({span: {query: buffers}}, {span: {dashboard: median ms}}) of a bench result."""
    buffers, medians = {}, {}
    for span, queries in result.items():
        buffers[span] = {label: entry['buffers'] for label, entry in queries.items()}
        per_dashboard = {}
        for label, entry in queries.items():
            per_dashboard.setdefault(label.split(':')[0], []).append(entry['ms'])
        medians[span] = {dashboard: round(statistics.median(values), 2) for dashboard, values in per_dashboard.items()}
    return buffers, medians


def over_budget(budget, buffers, medians):
    problems = []
    for span, queries in buffers.items():
        for label, blocks in queries.items():
            allowed = budget['buffers'].get(span, {}).get(label)
            if allowed is None:
                problems.append(f'{span} days: {label} has no budget: run with --update and review the diff')
            elif blocks > allowed * BUFFER_MARGIN + BUFFER_FLOOR:
                problems.append(f'{span} days: {label} reads {blocks} buffers, budget {allowed}')
        for dashboard, ms in medians[span].items():
            allowed = budget['median_ms'].get(span, {}).get(dashboard)
            if allowed is not None and ms > allowed * MS_MARGIN + MS_FLOOR:
                problems.append(f'{span} days: {dashboard} median {ms} ms, budget {allowed} ms')
    return problems


def main(argv):
    result = json.loads(Path(argv[1]).read_text())
    buffers, medians = measure(result)
    if '--update' in argv:
        BUDGETS.write_text(json.dumps({'buffers': buffers, 'median_ms': medians}, indent=1, sort_keys=True) + '\n')
        print(f'Wrote {BUDGETS}')
        return 0
    problems = over_budget(json.loads(BUDGETS.read_text()), buffers, medians)
    print('\n'.join(problems) or f'{sum(len(q) for q in buffers.values())} query measurements within budget')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
