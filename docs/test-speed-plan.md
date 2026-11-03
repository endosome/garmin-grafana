# Test, build and CI speed plan

Proposal, 2026-11-03. Measured first, then ranked by time saved for the effort.
Companion to the [dashboard testing plan](dashboard-testing-plan.md), whose
browser job is placed in the CI layout below.

## Baseline (measured 2026-11-03)

Local numbers come from genesis, which is shared and busy, so they run about twice
CI's. Compare local with local and CI with CI. Every local run used
`systemd-run --user --scope -p MemoryMax=6G` against a disposable
`timescaledb-ha:pg17.11-ts2.31.0` container.

### Local

| Run | Time |
| --- | --- |
| Unit only (`uv run pytest`, 428 passed, 66 DB skips) | 3.7 s |
| Unit only with `--cov` | 6.2 s |
| Full (`TEST_DATABASE=1`, 494 tests), no coverage | 81–83 s |
| Full with `--cov` (what CI runs) | 121–124 s, on a database already holding one or two earlier runs' data, so it somewhat overstates coverage's share |
| `tests/test_personas.py` alone (inside the full run) | ~70 s |
| `docker build --no-cache .` | 2 m 35 s, of which `apt-get` is most |
| Same Dockerfile without the `apt-get` layer | 50 s |

Slowest tests in the full run:

| Test | Time |
| --- | --- |
| `test_rollup_panels_return_what_their_raw_sample_queries_returned` | 20.9 s |
| `test_every_query_runs_on_each_persona[public_fitbit_user]` | 8.9 s |
| `test_every_query_runs_on_each_persona[healthy_lifestyle]` | 6.1 s |
| `test_dashboards_show_a_real_fitbit_month` | 5.6 s |
| `test_hourly_steps_add_up_on_the_25_hour_dst_day` | 4.2 s |
| `test_garmin_health_panels_match_the_healthy_lifestyle_person` | 3.5 s |
| `test_garmin_stats_daily_panels_match_the_healthy_lifestyle_person` | 3.2 s |
| `test_accounts_never_see_each_others_data` | 3.0 s |
| `test_legacy_import.py::test_bounded_import_resume_validation_and_dry_run` | 2.5 s |

Everything else is under 2 s; outside `test_personas.py` the slowest is 2.5 s.

### CI (`Tests` run 41542001040, v0.11.1)

| Step | Time |
| --- | --- |
| Initialize containers (pull the 4.2 GB `timescaledb-ha` image, wait for health) | 31 s |
| pytest with coverage (491 passed in 43.6 s) | 47 s |
| `validate_dashboard_queries.py` | <1 s |
| Compose config + `docker build` (`apt-get` alone 12.3 s) | 24 s |
| Compose up + Grafana smoke + kiosk smoke | 26 s |
| Upgrade rehearsal from the Alpine image | 26 s |
| **Job total** | **2 m 46 s** |

The steps run one after another in a single job.

### Release (tag `v0.11.1`)

- The tests ran **three times**: for the commit push (22:57), for the tag push,
  because `tests.yml` has `on: push` with no filter (23:00), and again inside the
  release workflow through `workflow_call` (23:00).
- Multi-arch build and push: 2 m 15 s under QEMU for `linux/arm64`.
- Tag to image: about 5 minutes.

## Where the time goes

1. **Persona loading, repeated.** `test_personas.py` calls its `load` fixture about
   22 times for 6 distinct personas: `healthy_lifestyle` about 6 times,
   `ski_tourer` 8 times (5 of them from the countdown parametrization),
   `public_fitbit_user` 3 times. Each load writes the persona again under a new
   account, then refreshes the rollups. Profiled on the healthy-lifestyle and
   Fitbit personas:

   | Step | healthy_lifestyle (16 k records) | public_fitbit_user (24 k records) |
   | --- | --- | --- |
   | Build the persona in Python (cached by `functools.cache`) | 3.2 s for both | |
   | `PostgresStore.write` | 2.4 s | 3.4 s |
   | `refresh_rollups()` | 0.8 s | 1.7 s |
   | All 171 dashboard queries over 400 days | 2.2 s | 1.1 s |

   `refresh_rollups()` refreshes every continuous aggregate over its whole range
   (`NULL, NULL`). Nothing deletes test data, so each refresh scans every persona
   written so far in the session, and each load costs more than the last.
2. **Coverage tracing adds about 50 %** (81 s to 122 s locally). Most of it falls on
   the same Python write path the repeated persona loads run.
3. **Postgres I/O is not the bottleneck.** `fsync=off`, `synchronous_commit=off`,
   `full_page_writes=off` with the data directory on tmpfs ran in 81 s against 83 s
   (see "Tested and rejected").
4. **CI runs everything one after another.** The Docker, Compose and upgrade steps
   (about 76 s) don't need pytest's results but wait for it.
5. **The build stage installs a compiler and git it never uses.** `uv.lock` has no
   git sources, and the only package without a wheel is `fitparse`, which is pure
   Python. Without the `apt-get` layer the image builds, `garmin_grafana`,
   `fitparse`, `garminconnect` and `psycopg` (binary implementation) import, and
   the image stays at 268 MB.

## Prerequisite: a test that fails on a reused database

`tests/test_storage.py::test_record_fetch_stamps_the_source_for_the_last_fetch_panel`
(added in 1dcaeb7) reads every `last_fetch:%` row in `app_info`, not just its own
source's row. It passes in CI because each run gets a fresh database, and fails on
a second local run against the same database:

```
AssertionError: assert ['last_fetch:...t_fetch:1092'] == ['last_fetch:1092']
```

Fixed on 2026-11-03: the test now compares the stamps before and after its own
calls, and the full suite passed twice in a row on one database. Rule for the whole suite: **every DB test
must pass against a database that already holds earlier runs' data.** Shared
persona data (item 1) and per-worker databases (item 7) both depend on that rule.

## Changes, ranked

Savings are estimates from the profile above. Measure each one after it lands.

| # | Change | Saves (CI) | Effort | Risk |
| --- | --- | --- | --- | --- |
| 1 | Load each persona once per session | ~20–25 s of 44 s pytest (local ~45 s of 82 s) | S | Low |
| 2 | Split the CI job in two parallel jobs | ~55 s wall clock | S | Low |
| 3 | Drop the `apt-get build-essential git` build layer | 12 s per CI build, more under QEMU | XS | Low |
| 4 | Don't run the tests three times per release | one whole run per release | XS | None |
| 5 | `concurrency` and `paths-ignore` on `tests.yml` | whole runs on docs-only pushes and pushes made in quick succession | XS | Low |
| 6 | Buildx with the GitHub Actions cache in both workflows | most of the build when only `src/` changed | S | Low |
| 7 | Native arm64 runner for the release build | most of the 2 m 15 s | M | Low |
| 8 | Coverage only where it is needed | up to ~40 % of pytest time | S | Medium |
| 9 | Honour `timeFrom` in the run-every-query test | ~1 s per persona | XS | None |
| 10 | pytest-xdist with one database per worker | ~half of what remains | M | Medium |

### 1. Load each persona once per session

- Add a session-scoped fixture that opens its own connection, migrates, pauses
  the rollup refresh policies (the `alter_job(..., scheduled => false)` that `db`
  does; no `db` test is guaranteed to have run first), writes all six personas
  under one random account suffix per session, calls `refresh_rollups()` once,
  and returns `{persona name: run}`. It must not depend on `db`, which is
  function-scoped and owns its connection.
- `load(persona)` becomes a lookup in that dict. The tests keep their signatures.
- Run every panel query in `run` inside a `READ ONLY` transaction, so a test that
  starts writing to shared data fails loudly and doesn't silently change what
  other tests see.
- `test_rollup_panels_return_what_their_raw_sample_queries_returned` reuses the
  shared healthy-lifestyle and Fitbit loads instead of writing its own.
- Optional: limit `refresh_rollups()` to the time range just written, with the
  window arguments of `refresh_continuous_aggregate`, so that refresh time no longer
  grows with everything earlier tests wrote. Once item 1 leaves a single refresh
  per session, this saves less than a second; do it only if that refresh shows up
  in `--durations`.

Expected: 6 writes and 1 refresh instead of about 22 writes and 22 refreshes
that grow each time.

Acceptance: the full suite passes twice in a row against the same local database,
and `test_personas.py` takes under a third of its current time.

### 2. Split the CI job

```
tests.yml
  pytest   postgres service -> pytest --cov -> validate_dashboard_queries
  stack    docker build -> compose config -> compose up -> smoke_grafana -> smoke_kiosk -> smoke_upgrade.sh
  e2e      (later, see the dashboard testing plan) needs: stack's image
```

`pytest` takes about 80 s with container init. `stack` takes about 2 minutes,
because it has no `services:` block: `docker compose up` pulls the 4.2 GB
`timescaledb-ha` image itself, which costs the roughly 30 s the single job now
spends in "Initialize containers". That pull is the floor for this job. Wall
clock becomes the longer of the two instead of their sum: about 1 m 50 s instead
of 2 m 46 s. The cost is a second checkout and uv setup (about
3 s) and more runner minutes, which are free on a public repository.

Keep the upgrade rehearsal on every run. Once it runs in parallel it no longer
delays the result, and path filters on it would let regressions through when a
migration changes.

Replace the `curl` wait loops with `docker compose up --wait`. That needs a
`healthcheck` on the `grafana` service in `compose-example.yml`, which also tells
users when Grafana is ready.

### 3. Drop the unused build tools from the Dockerfile

Delete the `RUN apt-get update && apt-get install ... build-essential git` layer
from the build stage (verified, see "Where the time goes"). While there:

- Add `RUN --mount=type=cache,target=/root/.cache/uv` to both `uv sync` lines,
  with `UV_LINK_MODE=copy`, so local rebuilds reuse downloaded wheels.
- Bump `ghcr.io/astral-sh/uv:0.6.17-...` to a current pinned release. Look up the
  version; don't guess it.
- Guard against a future dependency that needs a compiler: the build then fails
  with a clear error, which is better than carrying a compiler for every
  build.
- Verified on amd64 only. Check the arm64 build in the same change. It should
  pass: `psycopg-binary` ships aarch64 wheels and `fitparse` is pure Python.

### 4. Run the tests once per release

In `tests.yml`, limit `push` to branches so tags don't trigger it:

```yaml
on:
  push:
    branches: ['**']
```

The release workflow keeps `needs: tests` through `workflow_call`, so a tag is
still tested once before its image is built. A later step can drop that call too
and check the `Tests` result for the tagged commit with `gh api` instead. Keep the
call until there is a reason to save the remaining run.

### 5. Skip runs that prove nothing

- `concurrency: { group: tests-${{ github.ref }}, cancel-in-progress: true }` on
  `tests.yml`. Not on the release workflow: a half-pushed image is worse than
  waiting.
- `paths-ignore: ['docs/**', '**/*.md', '**/*.png']` on `push`. `workflow_call`
  from the release workflow is unaffected, so releases are always tested.

### 6. Buildx with the GitHub Actions cache

Use `docker/setup-buildx-action` and `docker/build-push-action` with `load: true`
in the `stack` job, and `cache-from: type=gha`, `cache-to: type=gha,mode=max` in
both workflows. When only `src/` changed, the dependency layer comes from the
cache. Give each workflow its own `scope`, so the arm64 cache doesn't evict the
amd64 one.

### 7. Release build without QEMU

After item 3, measure the release build again; installing packages with
`apt-get` under emulation was the slow part. If the build still takes over a
minute:

- Build each platform on its own runner, amd64 on `ubuntu-26.04` and arm64 on
  GitHub's arm64 runner, with `push-by-digest`, then merge with
  `docker buildx imagetools create`. Check which arm64 label exists for 26.04
  (`ubuntu-26.04-arm` or similar); otherwise use `ubuntu-24.04-arm`, since the build
  happens inside the image and doesn't depend on the host OS.
- Later, test the image that gets shipped: push the image the `stack` job tested
  (by digest) instead of rebuilding it after the tests.

### 8. Coverage only where it is needed

`COVERAGE_CORE=sysmon` is not an option on Python 3.13 (see "Tested and
rejected"). After item 1, measure coverage overhead again. If it is still above
10 s in CI:

- Check whether the gate (`fail_under = 98`, currently at 100 %) still holds
  without `test_personas.py`. These tests check dashboard SQL, not Python
  branches. If the gate holds, mark them `@pytest.mark.no_cover` (pytest-cov) and
  keep them in the same run.
- Revisit `COVERAGE_CORE=sysmon` when the project moves to Python 3.14, which
  can measure branches with it.

### 9. Honour `timeFrom` in `test_every_query_runs_on_each_persona`

The test runs every query over 400 days. Panels with a pinned `timeFrom` never
see that range in Grafana. Stress (Garmin Stats panel 29, pinned to 24 h) takes
0.98 s over 400 days because it smooths every raw sample. Use the panel's
`timeFrom` when it has one, the way `bench_dashboards.py` already does. The test
gets faster and closer to what Grafana actually runs. Fixed ranges stay covered by
the other persona tests.

### 10. pytest-xdist, only if still needed

After items 1 and 8, CI pytest should take about 20 s. If it doesn't:

- A session fixture migrates a `garmin_test_template` database once, and each
  worker gets `CREATE DATABASE garmin_test_<worker> TEMPLATE garmin_test_template`.
  The template must have no open connections at that moment, and each worker
  pauses its own refresh policies, the way `db` does now. Check that TimescaleDB
  background workers start for databases cloned from the template.
- Each worker builds the personas itself (3.2 s). Use `--dist loadgroup` so the
  persona tests share one worker.
- Locally `-n 2`, within the machine rules. In CI, `-n auto` (4 vCPUs).

Don't use xdist for the unit tests: they finish in 3.7 s.

## Tested and rejected

| Idea | Result |
| --- | --- |
| `fsync=off`, `synchronous_commit=off`, `full_page_writes=off`, data on tmpfs | 81 s against 83 s. The suite is limited by CPU and round trips, not by I/O. Don't try it again. |
| `COVERAGE_CORE=sysmon` | coverage 7.16.2 on Python 3.13: "sys.monitoring can't measure branches in this version". With `filterwarnings = error` that warning stops the run. Possible once on Python 3.14. |
| Lighter Postgres service image (`timescale/timescaledb`, 2.3 GB) | Migration `009_toolkit_rollups` needs `timescaledb_toolkit`, which only `-ha` has. The production image is the one to test against. |
| xdist for unit tests | The unit tests take 3.7 s in total, so there is almost nothing to save. |
| Rewriting `PostgresStore.write` for speed | Already batches 1000 records into one `unnest` statement and skips unchanged rows. Test time goes to how often it is called, not to how fast it is. |

## Developer loop

- Day to day: `uv run --locked pytest` (unit tests, 3.7 s), plus `--lf` after a
  failure.
- Before pushing a dashboard or storage change, run the DB suite against a
  long-lived disposable container from `docs/development.md` under
  `systemd-run --user --scope -p MemoryMax=6G -p MemoryHigh=5G`. With the
  prerequisite fixed, reusing that database between runs is fine.
- Add `--durations=15` to CI's pytest command, so a slow new test shows up in the
  log of the commit that added it.

## Targets

| Measure | Now | Target |
| --- | --- | --- |
| `Tests` wall clock on a push | 2 m 46 s | ≤ 1 m 50 s (the `stack` job's image pull sets the floor) |
| CI pytest step | 47 s | ≤ 25 s |
| Local full DB suite (no coverage) | 82 s | ≤ 40 s |
| Test runs per release | 3 | 1 |
| Release multi-arch build | 2 m 15 s | ≤ 1 m 15 s |
| Full suite on a reused database | fixed 2026-11-03 | keeps passing |

## Order

1. Items 3, 4 and 5 (the prerequisite is done): one small commit each.
2. Item 1 (persona sharing), then re-measure, then item 9.
3. Item 2 (job split) and item 6 (build cache).
4. Re-measure, then decide on items 7, 8 and 10.

## Out of scope

- Changing what the tests check. The rollup equivalence matrix stays as it is:
  its three range ends, four spans and two time zones each guard a separate edge
  case (partial hour at the end, ranges shorter than a rollup bucket, a time zone
  whose local midnight falls in the middle of a UTC hour). Revisit it only if it is
  still over a quarter of the suite after item 1.
- Query performance for users. That is the
  [Grafana optimization plan](grafana-optimization-plan.md).

## Status (2026-11-03)

Done: items 1, 2, 3, 4, 5, 6, 8, 9. Measured locally after item 1: full DB suite 54–61 s
(was 82 s), `test_personas.py` 35 s (was ~70 s). Without `test_personas.py` the coverage
gate holds at 100 %, so CI runs those tests in a second pytest command without coverage
(item 8). Container image: uv bumped to 0.9.30, the newest `python3.13-bookworm-slim`
tag published; the arm64 build is verified by the next release run only.

Not done, on purpose: item 7 (native arm64 runner) waits for a measured release build with
the apt layer gone and the layer cache warm; item 10 (xdist) waits for a CI timing of the
new layout. What is left in the suite is the rollup equivalence matrix (12–14 s) and the
first persona load (about 10 s), both inside the "out of scope" rule.
