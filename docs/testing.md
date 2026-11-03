# Testing and CI

How the code and the dashboards are tested, how CI is laid out and what each part costs, and why. The commands for a
disposable database are in [development](development.md); the rules a dashboard has to follow are in
[dashboard standards](dashboard-standards.md).

## Layers

| Layer | What it checks | Where | Needs |
| --- | --- | --- | --- |
| Unit | collectors, storage logic, migrations (mocked persistence) | `tests/` | nothing (3.7 s) |
| L0 lint | dashboard JSON against the standards: canonical JSON, uids, tags, layout, panel types, `source_id` filters, variables, units, `noValue`, links, a registry entry per panel | `tests/test_dashboard_lint.py`, `tests/dashboards.py` | nothing |
| L1 contracts | every query's result columns (snapshot), isolation between accounts, two accounts at once, time zone × range matrix | `tests/test_dashboard_contracts.py`, `tests/data/panel_schemas.json` | `TEST_DATABASE=1` |
| L2 semantics | panel output against values computed in plain Python from the persona data, never from the SQL under test | `tests/test_personas.py`, `tests/personas.py` | `TEST_DATABASE=1` |
| L3 Grafana server | provisioning, plugins, drift against the files, queries as the frontend sends them (`/api/ds/query`), read-only datasource, kiosk security | `tests/test_grafana_stack.py` (marker `grafana`) | Compose stack with the personas, `GRAFANA_URL` |
| L4 browser | every dashboard loads without a panel error, console error or missing plugin; tile text; dropdowns, links, rows, Fetch now; kiosk touch flows and accessibility; screenshots of chart panels | `e2e/` (Playwright 1.64.1) | the same stack |
| L5 budgets | buffers read per query and median milliseconds per dashboard on a year of data | `scripts/bench_dashboards.py`, `scripts/check_dashboard_budgets.py`, `tests/data/dashboard_budgets.json` | disposable database |
| FIT store | codec round trip (R4 oracle), differential tests against the official SDK and fitparse, semantics, converters validated against the GPX and TCX XSDs, views, edit primitives against Python references; coverage gate 98 percent per package | `packages/garmin-fit-store/tests/` (`uv run --locked --package garmin-fit-store --directory packages/garmin-fit-store pytest --cov`) | `TEST_DATABASE=1` for the database tests; optional `FIT_SDK_FIXTURES_DIR`, `FIT_CORPUS_DIR`, `FIT_XSD_DIR` |
| FIT store, TypeScript | the TypeScript codec against the committed conformance corpus (`tests/corpus/*.fit` and `*.jsonl`) | `node --test packages/garmin-fit-store-ts/test/*.test.ts` | Node 24 |
| L6 upgrades | the newest Grafana against the dashboards; Renovate proposes bumps of the Grafana image | nightly workflow, `renovate.json` | |

Not used: `grafana/dashboard-linter` (its rules are written for Prometheus dashboards; our conventions take less code
than configuring them) and `smoke_grafana.py`/`smoke_kiosk.py` (replaced by L3 and L4).

## Personas

`tests/personas.py` builds deterministic synthetic athletes (see [development](development.md)); "today" is 2026-10-28.
They are written once per pytest session by the `personas` fixture in `tests/conftest.py`, each under a random account
suffix, with the rollups refreshed once. `reader` is a read-only connection, so a test that starts writing to the shared
data fails loudly. `scripts/load_personas.py` loads the same personas into a disposable database for the browser
tests and stamps Last Fetch at "now" there, because the database's own `now()` is not covered by the frozen browser
clock.

**Rule for every database test:** it must pass against a database that already holds earlier runs' data (and with the
other tests' accounts in it). Shared persona data and per-worker databases both depend on it.

## The panel registry

`tests/panel_registry.py` lists every panel as `File.json:id`, and a test fails for a panel in none of the lists, or in
two, or for a panel that no longer exists. A new panel therefore cannot land without one of:

- `VALUE_TESTS`: the tests that assert its values, computed independently from the persona data (65 panels);
- `SCREENSHOT`: chart panels with no hand-computed expectation. `tests/data/panel_values.json` records what each of their
  queries returns for the persona and range the screenshots use (row count, and sum, min and max per column), and a
  reviewed screenshot baseline records what they draw (63 panels). It is a golden master, not an independent expectation:
  a wrong value recorded today passes, any change fails. `pytest --update-values` rewrites it, as a reviewed diff;
- `NO_VALUES`: a written reason (text panel, the Fetch now button, the version stat, the geomaps: their basemap needs
  the internet, which the browser tests block) (6 panels).

The test summary prints how many panels fall in each group. The registry has 134 panels.

## Running things

Always inside a memory limit and at low parallelism (a parallel browser run once used all 32 GB of genesis):

```bash
systemd-run --user --scope -p MemoryMax=6G -p MemoryHigh=5G -- <command>
```

| What | Command |
| --- | --- |
| Unit tests | `uv run --locked pytest` |
| Database suite, as in CI | see [development](development.md) (coverage run, then the dashboard tests) |
| Database suite, fast, locally | `uv run --locked pytest -n 2 --dist loadgroup` (39–42 s; about 60 s serial). Each xdist worker gets its own database, `<PGDATABASE>_gw0`, `_gw1`, created empty and dropped afterwards by `tests/conftest.py`; two workers on one database deadlock on migrations and rollup refreshes. The rollup test has its own `xdist_group`. |
| Full zone × range matrix | `DASHBOARD_MATRIX=full uv run --locked pytest tests/test_dashboard_contracts.py` (push runs one zone and two ranges) |
| Rewrite the schema snapshot or the golden values | `pytest --update-schemas`, `pytest --update-values` (review the diff) |
| Lint ratchet | `LINT_UPDATE_RATCHET=1` rewrites `tests/data/lint_ratchet.json` (58 missing descriptions and 21 `noValue` that are not an en dash are still listed; the list may only shrink) |
| Stack tests (L3) | start the Compose stack with the personas loaded, then `GRAFANA_URL=… KIOSK_URL=… GRAFANA_ALLOW_WRITES=1 uv run --locked pytest -m grafana tests/test_grafana_stack.py` (`GRAFANA_ALLOW_WRITES=1` lets the Fetch now test insert one row) |
| Browser tests (L4) | `e2e/run.sh [playwright args]`: the pinned Playwright image, 3 GB, two workers (`E2E_WORKERS`), `--shard=1/8` works |
| Baselines | `e2e/update-baselines.sh` |
| Budgets | `bench_dashboards.py seed`, `run --days 30 --days 365 --json out.json`, `check_dashboard_budgets.py out.json` (`--update` records a new budget) |
| The CI `pytest` job from the workflow file | `act push -j pytest -P ubuntu-26.04=catthehacker/ubuntu:act-24.04 --pull=false` (act does not do the `stack` and `browser` jobs: they use the host Docker daemon and ports, the `gha` cache and `upload-artifact`) |

If a run times out, stop it and its browsers, then check `pgrep -c -f chrom` and `free -h` before starting anything else. A
run killed by systemd-oomd needs less memory, not a higher limit.

## L3 and L4 in detail

**L3.** The datasource expands the time macros itself (`$__timeFilter`, `$__timeFrom()`, `$__timeTo()`; checked against
Grafana 13.0.2); the frontend substitutes variables and `${__interval}`, and the test does the same. Per query it asserts
no `error`, no warning or error notice on a frame, field names equal to the schema snapshot, and a time field on
`time_series` frames. `/api/dashboards/uid/<uid>` returns each dashboard as its file minus `id` and `version`, so the drift
check compares them directly. The main instance's reader role rejects writes; only the Fetch now datasource inserts, and only
into `fetch_requests`.

**L4 is deterministic by construction:**

- persona data from fixed seeds, "today" 2026-10-28, a frozen browser clock (`page.clock.setFixedTime`): Grafana's
  frontend computes `now`, pinned `timeFrom` ranges and "today" from it and sends absolute times to the server;
- fixed viewport (1920×1080, scale 1), fixed zone, animations and transitions disabled by an injected stylesheet;
- every request outside the stack is blocked (an `auto` fixture), including map tiles and grafana.com;
- Basic auth in `extraHTTPHeaders` instead of the login page;
- a dashboard is settled when every panel has loaded: the test scrolls the dashboard's own scroll container (not the docked
  navigation menu), expands collapsed rows by `aria-label^="Expand row"`, and waits for loading indicators and
  `/api/ds/query` to go quiet.

A state timeline with no rows says "Data does not have a time field" instead of "No data" (Health panels 10 and 36;
changing the query format does not help). The tests list them (`KNOWN` in `e2e/tests/fixtures.ts`) and the kiosk test
allows the wording.

**Tiers:** health per dashboard and persona; rendered values (tile text and table cells, `clocks`, `dthms`, decimals,
`noValue`); interaction (dropdowns, links keep time range and variables, rows open, Fetch now inserts one `fetch_requests` row
and is absent from the kiosk); the kiosk on `1280×800` with touch (Back, the idle timeout, which waits its real 20 s because
a fake clock stalls Grafana's own timers, long-press menus, links that would leave, axe-core on `Grafana_Kiosk/index.html`
with `meta-viewport` disabled on purpose); screenshots of 93 chart panels in chunks of 12 per test, so they spread over
workers and CI shards. Geomaps are left out.

**Baselines** change only through `e2e/update-baselines.sh`, inside the pinned container. Commit them with the dashboard
change that explains them and say in the message which panels changed and why. CI uploads the Playwright report and traces
on failure; look at the diff images before updating. A Grafana or plugin bump always comes with a full baseline review. The
baselines made on genesis matched on the GitHub runner without changes.

## CI

Workflows in `.github/workflows/`:

| Workflow | Runs | Jobs |
| --- | --- | --- |
| `tests.yml` (Tests) | every push to a branch (not for `docs/**`, `*.md`, `*.png`), pull requests, `workflow_dispatch`, `workflow_call`; a newer push cancels the older run | `pytest` (coverage run, gate 98 %, at 100 %), `dashboards` (persona and contract tests without coverage, then `validate_dashboard_queries.py`), `stack` (image, `up --wait`, personas, `pytest -m grafana`), `upgrade` (`scripts/smoke_upgrade.sh`), in parallel |
| `browser.yml` (Browser tests) | the same triggers | `e2e` in eight shards, each starting its own stack; the image comes from the layer cache the `stack` job leaves |
| `nightly.yml` | 03:17 UTC and manually | full zone × range matrix; the budget job (a year of data, compared with `dashboard_budgets.json`); the stack tests and the browser tests without screenshots on `GRAFANA_IMAGE=grafana/grafana:latest` |
| `version.release.yml` | a `v*` tag; `workflow_dispatch` is a dry run that builds and pushes nothing | build, test, tag (below) |

The coverage run and the persona tests are separate on purpose: the persona and contract tests check dashboard SQL, not
Python branches; tracing them costs about half again as much time and the 98 % gate holds without them.

**Release.** Each platform builds natively on its own runner (amd64 on `ubuntu-26.04`, arm64 on `ubuntu-26.04-arm`, no QEMU)
and pushes by digest, with no tag. The amd64 image is then the one `Tests` and `Browser tests` run against
(`.github/actions/runtime-image` pulls it instead of building; the `image` input of both workflows), so what is tested is what
ships. A merge job (`docker buildx imagetools create`) tags both platforms only if those pass. Untagged digests of a failed
release stay in GHCR. The called workflows' concurrency groups contain the caller's name and the tag ref, so a branch push does
not cancel a release's test run. Rehearsed on three throwaway tags (deleted from git and GHCR afterwards).

### Timings

Measured on GitHub on 2026-11-03.

| Measure | Before | Now |
| --- | --- | --- |
| `Tests` on a push | 2 m 46 s (one job, every step in turn) | 1 m 28 s to 1 m 46 s: `pytest` 56 s, `dashboards` 63 s, `stack` 78–93 s, `upgrade` 94 s, in parallel |
| Coverage step | 47 s | 11–15 s |
| Persona and contract step | about 70 s locally, repeated loads | 16–20 s |
| `Browser tests` | n/a | 3 m 04 s to 3 m 41 s (29 browser tests in 266 s in one two-worker job before sharding) |
| Test runs per release | 3 | 1 |
| Release multi-arch build | 2 m 15 s under QEMU | about 50 s (per platform, in parallel); tag to image about 4 m 17 s including the tests |
| Local full suite | 82 s | 39–42 s with `-n 2`, about 60 s serial |

The floor of the `Tests` workflow is the `stack` job: `docker compose up` pulls the 4.2 GB `timescaledb-ha` image, about 30 s.
The persona load (10 s) and the rollup equivalence test (11 s) are what is left in the suite.

### What made it fast

- **Persona loading once per session** instead of about 22 times (each load wrote the persona again under a new account and
  refreshed every rollup over its whole range, more each time): 6 writes and 1 refresh.
- **Job split and `docker compose up --wait`** (health checks on `grafana` and `grafana-kiosk` in `compose-example.yml`).
- **Dockerfile:** the `apt-get install build-essential git` layer is gone (nothing needs a compiler: `uv.lock` has no git
  sources and `fitparse` is pure Python; the image stays 268 MB), uv 0.9.30 is pinned, `uv sync` uses a cache mount, and a
  new dependency that needs a compiler fails the build with a clear error.
- **Tests once per release**, `concurrency` and `paths-ignore`; buildx with the GitHub Actions cache (own scope per workflow
  and platform).
- The run-every-query test honours a panel's pinned `timeFrom`.

### Tried and rejected

| Idea | Result |
| --- | --- |
| `fsync=off`, `synchronous_commit=off`, `full_page_writes=off`, data on tmpfs | 81 s against 83 s. The suite is limited by CPU and round trips, not by I/O. |
| `COVERAGE_CORE=sysmon` | coverage 7.16.2 on Python 3.13 cannot measure branches with it, and `filterwarnings = error` turns the warning into a failure. Possible on Python 3.14. |
| Lighter Postgres image (`timescale/timescaledb`) | migration `009_toolkit_rollups` needs `timescaledb_toolkit`, which only `-ha` has; the production image is the one to test. |
| xdist for the unit tests | they take 3.7 s. |
| xdist in CI on a shared database | `DeadlockDetected` between one worker's migration or write and the other's rollup refresh on the runner, and a saving of only 5–10 s. Locally it works with a database per worker (above); CI does not need it, as the two halves are separate jobs. |
| Rewriting `PostgresStore.write` | it already batches 1000 records per `unnest` and skips unchanged rows; the cost is how often it is called. |

## Performance budgets

`bench_dashboards.py seed` writes a year of intraday data and a GPS track per run (about 1.3 million rows), refreshes the rollups
(without that every query reads the raw samples and costs 10 to 50 times more) and runs `VACUUM ANALYZE`. `run --json` records,
per query, database time and the shared buffers read. The check allows a query 1.5× its recorded buffers (stable across
runners) and a dashboard 2× its recorded median milliseconds (noisy on shared runners). A budget change is a reviewed diff;
say in the commit which panels got dearer and why. The browser load time of each dashboard is attached to its test as an
annotation, without a gate until there is history.

## Upgrades

Renovate has a regex manager for the `GRAFANA_IMAGE` default in `compose-example.yml` (`renovate.json`); its GitHub app has to
be installed on the repository for it to open pull requests. The nightly workflow runs the stack tests and the browser tests
(no screenshots) on the newest Grafana, to learn about breaking changes before a bump is merged.

## Not done

- 58 panels without a description and 21 `noValue` that are not an en dash (the lint ratchet); the Health Overview baselines
  (7-day average and the band of the previous 28 days), the remaining Garmin Stats panels and the strength filters could get
  hand-computed expectations in place of the golden values.
- Gatus (already on genesis) could watch the deployment: Grafana's `/api/health`, the datasource health with a read-only token,
  and a last fetch under 26 hours old. That catches "the dashboards are fine but no data has arrived", which no test before a
  release can. Outside this repository.
- The accessibility check covers the kiosk page only; Grafana's own UI is out of scope.
