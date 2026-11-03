# Dashboard testing plan

Proposal, 2026-11-03. How we prove that the provisioned dashboards show the right
thing, not only that their SQL runs. Companion to the
[test speed plan](test-speed-plan.md), whose CI layout has a slot for the browser
job described here. Conventions under test are in
[dashboard standards](dashboard-standards.md).

## What is under test

| Dashboard | uid | Panels (incl. rows) | Notes |
| --- | --- | --- | --- |
| Garmin Stats (`Garmin-Grafana-Dashboard.json`) | `feiibsx498gsgb` | 64 | 3 geomaps, 2 hourly-heatmap plugin panels, Business Forms "Fetch now" button, 17 panels with browser transformations |
| Garmin Health Overview | `garmin-health-overview` | 43 | default home and kiosk dashboard, baseline bands |
| Garmin Ski Season | `garmin-ski-season` | 24 | `SeasonStart` and `VerticalGoal` variables |
| Garmin Strength Training | `garmin-strength-training` | 22 | category and label variables, 1 transformation |

In total: 171 panel and variable queries, two Grafana instances (the main one and
the anonymous read-only kiosk behind nginx), two pinned third-party plugins
(`marcusolsson-hourly-heatmap-panel@2.0.1`, `volkovlabs-form-panel@6.3.5`),
Grafana 13.0.2 and `schemaVersion` 41.

## What can go wrong

Each kind of failure needs a test layer that can see it. The right-hand column is
the layer that should catch it (layers are defined further down).

| # | Failure | Example | Caught by |
| --- | --- | --- | --- |
| F1 | Invalid or non-canonical JSON, provisioning rejects the file | the tags array in 361089c is not in the format the standards doc requires | L0, L3 |
| F2 | Query error | SQL typo, wrong macro, unknown variable | L1, L3 |
| F3 | Wrong number | local day boundary, DST, sum against average, unit in SQL | L2 |
| F4 | One account sees another's data | a query without `source_id IN (${Source:csv})` | L0, L1 |
| F5 | Data shape no longer matches the panel | column renamed, so an override, unit or threshold silently stops applying; `time_series` without a time column | L1, L3 |
| F6 | Presentation | `clocks`/`dthms` render wrong, `noValue`, threshold colours, value mappings, transformations, plugin panels | L4 |
| F7 | Layout | overlapping `gridPos`, a collapsed row's panels missing, a panel shown as "plugin not found" | L0, L4 |
| F8 | Variables | empty dropdown, multi-value `Source`, `All` value | L1, L4 |
| F9 | Navigation | dashboard links lose the time range or variables | L0, L4 |
| F10 | Too slow | a panel scans raw samples over a year | L5 |
| F11 | Security | kiosk viewers can write, or reach the Fetch now datasource | L3, L4 |
| F12 | Upgrade drift | a Grafana or plugin bump changes how a panel renders or migrates the schema | L3, L4, L6 |

## What exists today

| Layer | Existing check | Where | Runs in CI |
| --- | --- | --- | --- |
| Static | rows nest only when collapsed, stat regex matches display name, overrides match a field, `__all` values are SQL literals, weekly bars cover their week, colours are Grafana colours, Fetch now only outside the kiosk | `tests/test_storage.py` | yes |
| SQL runs | every query on an empty database and on each persona | `validate_dashboard_queries.py`, `test_dashboard_queries_empty_and_populated`, `test_every_query_runs_on_each_persona` | yes |
| Semantics | panel values against independent Python on six personas, DST day, local days, null against zero, rollups against raw SQL, isolation for a few panels | `tests/test_personas.py`, `tests/test_storage.py` | yes |
| Grafana server | datasource health, dashboards listed, every query through `/api/ds/query` | `scripts/smoke_grafana.py` | yes |
| Kiosk security | sandboxed frame, one dashboard, anonymous viewers can't save, can't insert fetch requests, no password login | `scripts/smoke_kiosk.py` | yes |
| Browser | nothing; a manual checklist in `development.md` and step 3 of the standards doc | none | no |
| Performance | per-query timing on a year of data, no threshold | `scripts/bench_dashboards.py` | no |

The semantic layer works. Run 41526115467 failed when a ski season panel moved
from hours to seconds without its test being updated.

### Gaps

1. **Nothing checks what Grafana does after the query.** That covers units,
   decimals, `noValue`, thresholds, mappings, the transformations on 18 panels, the plugin
   panels and layout. The standards doc says so itself: "Unit strings such as
   `clocks` and `dthms` are only verified by looking at the rendered panel." The
   main goal of this plan is to remove that sentence.
2. `smoke_grafana.py` requires at least 2 dashboards; 4 are provisioned. Losing
   two of them would pass.
3. `smoke_grafana.py` sends SQL in which `expand_query` has already replaced
   Grafana's macros (`$__timeFilter`, `$__timeFrom()`, `${__interval}`). Grafana's
   own macro expansion and its conversion of rows to data frames are never
   exercised. Only errors are checked, not the shape of the result.
4. `expand_query` puts a single id into `${Source:csv}`. Nothing tests a
   multi-value `Source`, even though the variable allows it.
5. 58 of the 61 non-row panels on Garmin Stats have no `description`, which the
   standards doc requires of every panel. Nothing enforces the standards doc.
6. Nothing guarantees that every panel has a semantic test, and nothing makes a
   new panel get one.
7. `KIOSK_DASHBOARD` may be any of the four files (README), but the kiosk installs
   only the heatmap plugin. The kiosk page hides the Business Forms panel (70) with
   CSS. Nothing checks that this still works, or that the other three dashboards
   render in the kiosk.
8. No performance budget. A slow query is noticed only when someone runs the
   benchmark by hand.

## Test layers

### L0: static lint (no database, milliseconds)

A new `tests/test_dashboard_lint.py`, one test per rule, each rule pointing to its
section of the standards doc. The existing structural tests in `test_storage.py`
move here.

| Rule | Standards section | Today |
| --- | --- | --- |
| The file equals `json.dumps(d, indent=2) + "\n"` (ASCII escapes, two spaces) | Files | fails: Garmin Stats `tags` |
| `uid` present, unique across files, and equal to a pinned map (renaming one is a deliberate change: links and bookmarks use it) | Files | new |
| `tags` include `garmin`; `weekStart: monday`, `graphTooltip: 1`, `timezone: browser`, `description` set | Files | new |
| `schemaVersion` equals what Grafana 13.0.2 writes (41); see the drift check in L3 | Files | new |
| Panel ids unique per dashboard; `gridPos` within 24 columns; no two panels overlap on the same level | Panels | new |
| Every non-row panel has a `description` | Panels | 58 failures: see "Ratchet" below |
| Every panel `type` is core Grafana or in `GF_PLUGINS_PREINSTALL` (parsed from `compose-example.yml`). Main instance: every type is installed. Kiosk: every type is installed or the panel is hidden by `Grafana_Kiosk/index.html` (today panel 70, the Fetch now button) | Files | new |
| Dashboard SQL takes "now" from `$__timeTo()`, never `now()` or `CURRENT_DATE` (none do today), so a frozen browser clock controls every range | Queries | new |
| Every target uses a datasource uid that is provisioned, `rawQuery: true`, `editorMode: code`, a valid `format`, and no `hide: true` | Queries | new |
| Every query on `records`, `samples`, `all_records` or a rollup filters `source_id IN (${Source:csv})` | Variables | new |
| Every `$Var` or `${Var...}` names a variable defined on the dashboard and uses a safe format (`:csv` for `Source`, `:sqlstring` for text); every variable is used | Variables | new |
| `unit` comes from an allow-list; a column or override named `Duration` uses `clocks`, `dthms` or `s` | Units | new |
| `noValue` is the en dash on stat, gauge and table defaults | Units | new |
| Stat tiles with `timeFrom` also have `hideTimeOverride` | Panels | new |
| Dashboard links point to existing uids or tags and set `keepTime` and `includeVars` | Files | new |

**Ratchet.** Write the description rule with an allow-list of today's 58
offenders, which may only shrink: the test also fails if a listed panel now has a
description, so the entry gets removed. Fill the descriptions in later commits.

**Not used:** `grafana/dashboard-linter`. Its rules are written for Prometheus
dashboards. Our conventions are specific enough that our own rules take less code
than configuring someone else's.

### L1: query contracts (PostgreSQL)

Keep every existing execution test. Add:

1. **Result schema snapshot.** For each `dashboard:panel:ref`, the column names
   and PostgreSQL types on the healthy-lifestyle persona, written to
   `tests/data/panel_schemas.json`. A change fails until the file is regenerated
   (`--update-schemas`) in the same commit, so a renamed column shows up in review
   next to the override that depends on it. L3 compares Grafana's frame field
   names with the same file.
2. **Isolation for every query.** Load all personas, then run each of the 171
   queries for a new, empty account. Every result must be empty or entirely
   `NULL`. Today isolation is checked for a few panels; this makes it a property of
   every query, including ones added later.
3. **Multi-value `Source`.** Run every query with two persona ids in
   `${Source:csv}`. It must not fail, and for tables, the rows must be the union
   of the two single-account results where the panel lists rows rather than
   aggregating them.
4. **A named time zone and range matrix.** Zones: UTC, `Asia/Kolkata` (local
   midnight falls in the middle of a UTC hour), `America/Los_Angeles` (DST, and the
   legacy cyclist's zone). Ranges: 24 h, 7 d, 30 d, 1 y, 5 y, each with the interval
   Grafana would choose. Move `interval()` and its step table from
   `bench_dashboards.py` into a module both use, so the tests and the benchmark
   agree.
5. **Panel registry.** `tests/panel_registry.py` maps every panel id to the tests
   that assert its values, or to an explicit reason it has none (for example "text
   panel" or "button"). A test fails when a dashboard has a panel the registry
   doesn't list. A new panel then can't land without either a value test or a
   written reason. Print the share of panels with value assertions in the test
   summary, and raise it over time.

### L2: semantics (personas)

The existing approach stays: compute each expected value independently in plain
Python from the persona's records, never from the SQL under test. Use the registry
to choose which panels to cover next:

1. Health Overview baselines: the 7-day average and the band of the mean plus or
   minus one standard deviation of the previous 28 days, including the "shown only
   once enough days have data" edge (newcomer persona).
2. Garmin Stats panels that only have an "executes" test today.
3. Strength dashboard filters over each `ExerciseCategory` and `ExerciseLabel`.

### L3: Grafana server (Compose stack, HTTP only)

Turn `smoke_grafana.py` and `smoke_kiosk.py` into a pytest module (marker
`grafana`, skipped unless `GRAFANA_URL` is set) that runs against the Compose
stack with the personas loaded (`scripts/load_personas.py`).

- **Provisioning:** `/api/search` returns exactly the four uids, each with
  `meta.provisioned`. The kiosk returns exactly the `KIOSK_DASHBOARD` uid. The
  Grafana container log has no `level=error` lines from provisioning or plugin
  loading.
- **Plugins:** `/api/plugins/<id>/settings` reports the pinned version on the main
  instance. On the kiosk, Business Forms is absent.
- **Queries as the frontend sends them:** substitute what the frontend
  substitutes, which is the template variables and the built-in `${__interval}`
  (derived from `intervalMs`). Leave `$__timeFilter`, `$__timeFrom()` and
  `$__timeTo()` to the Postgres datasource. Check which tokens the backend
  actually expands before relying on this split. Send `from`, `to`, `intervalMs`
  and `maxDataPoints` as a full-width panel would. Assert:
  - no `error` on the result;
  - no frame `meta.notices` of severity warning or error, for example a missing
    time field or a failed long-to-wide conversion;
  - field names equal the L1 schema snapshot;
  - `time_series` frames have a time field.
- **Schema drift:** fetch each dashboard with `/api/dashboards/uid/<uid>` and
  compare it with the file. Grafana 13 migrates some of the schema in the backend
  and some in the frontend. Find out which applies to `schemaVersion` 41 before
  relying on this check. If the API returns the file unchanged, run the
  migration check in L4 instead: open the dashboard and compare the JSON model
  the frontend holds.
- **Security:** keep every kiosk check. Add: the main instance's read-only
  datasource rejects `INSERT`, `UPDATE` and `DELETE`, and only the Fetch now
  datasource can insert, and only into `fetch_requests`.

### L4: browser (Playwright): the missing layer

**Tooling.** Playwright Test (Node) in `e2e/`, pinned, run in the official
`mcr.microsoft.com/playwright` image of the same version. Node rather than
pytest-playwright because screenshot comparison (`toHaveScreenshot`), the trace
viewer, retries and the HTML report are part of Playwright Test and missing from
the Python package. The image pins the browser and fonts, so screenshots match
between genesis and CI. Chromium only. Look at `@grafana/plugin-e2e` for its
Grafana page models, and otherwise select elements by Grafana's `data-testid`
attributes (from `@grafana/e2e-selectors`), never by CSS classes.

**Fixture.** The CI `stack` job's image, Postgres with `load_personas.py`, both
Grafana instances and nginx. Each test opens an absolute URL:

```
/d/<uid>?var-Source=<persona id>&var-TimeZone=<zone>&from=<abs ms>&to=<abs ms>&timezone=<zone>
```

**Deterministic by construction:**

- Persona data is generated from fixed seeds. "Today" is 2026-10-28.
- The browser clock is frozen (`page.clock.setFixedTime`) at the persona's `NOW`.
  Grafana's frontend computes `now`, pinned `timeFrom` ranges and "today" from the
  browser clock and sends absolute times to the server, so freezing the browser is
  enough for the queries. It is not enough for timestamps the database writes with
  its own `now()`. Last Fetch (`dateTimeFromNow`, Garmin Stats) shows
  `app_info.updated_at` from whenever `load_personas.py` ran. Either seed that row
  at persona time or leave those tiles out of tiers 2 and 5.
- Fixed viewport (1920×1080, device scale 1) and fixed zone. Animations and
  transitions are disabled by an injected stylesheet.
- Every request outside the stack is blocked: map tiles, grafana.com news,
  avatars. Geomap panels get a blank basemap (or their map area is masked) or
  they will never be stable.
- A test waits until every panel has loaded. Grafana loads panels lazily, so the
  test scrolls each one into view and expands collapsed rows, then waits for the
  panel loading indicators to disappear and for `/api/ds/query` to go quiet.

**Tiers**, cheapest first:

1. **Health, per dashboard and persona.** Every panel loads. No panel shows an
   error indicator or "Panel plugin not found". No browser console errors. No
   failed `/api/ds/query`. No "No data" on a panel the registry says this persona
   fills. In the kiosk, run it for every allowed `KIOSK_DASHBOARD` and also assert
   that panel 70 is not visible. Hidden by the kiosk's CSS, it may still be in the
   DOM as "plugin not found".
2. **Rendered values (DOM text, not pixels).** Read the displayed text of stat
   tiles, gauges and table cells and compare it with the value L2 computes,
   formatted the way the panel's unit should show it. Write each expected string
   once from what Grafana renders, check it by eye, then pin it. This tier checks
   `clocks`, `dthms`, decimals, suffixes, `noValue` and value mappings, and it is
   not brittle the way screenshots are.
3. **Interaction.**
   - Variable dropdowns list the persona accounts, GPS activities and exercise
     categories, and selecting one updates the panels.
   - Dashboard links keep the time range and variables.
   - Collapsed rows open.
   - Fetch now inserts one row into `fetch_requests` in the disposable database
     and shows its toast. It is absent from the kiosk.
4. **Kiosk touch flows**, which the README says are checked by hand today. Use
   `hasTouch` and the 1280×800 viewport, and advance the clock with
   `page.clock.fastForward` instead of waiting.
   - Back returns to the home dashboard.
   - The idle timeout (`idle=` parameter) returns home.
   - Long-press menus are suppressed.
   - Links that would leave the page don't.
   - Run axe-core on `Grafana_Kiosk/index.html` (our page; Grafana's own UI is out
     of scope).
5. **Screenshots** of chart panels, where thresholds, bands, stacking, heatmaps
   and plugin rendering matter: element screenshots per panel with
   `toHaveScreenshot` and a small pixel-ratio tolerance. Mask text that changes
   over time. Cover every chart panel for one persona (healthy lifestyle) over
   one range, plus a hand-picked set for the others (ski season, gym-goer). Not
   every persona × range: that multiplies baselines and flakiness for little gain.

**Baseline updates** need a fixed procedure, or the suite turns into "update the
snapshots and ship":

- Regenerate only inside the pinned container, with one script
  (`e2e/update-baselines.sh`), never with a local browser.
- Commit baselines separately, or in the same commit as the dashboard change
  that explains them, and say in the commit message which panels changed and why.
- CI uploads the Playwright HTML report (expected, actual and diff images) and
  traces on failure. Whoever updates a baseline looks at that diff first.
- A Grafana or plugin version bump always comes with a full baseline review, never
  a blind update.

**Resources on genesis.** On 2026-10-21 a parallel browser run used all 32 GB and
hung the machine for an hour. Run the browser tests only like this, and never
next to another test suite:

```bash
systemd-run --user --scope -p MemoryMax=6G -p MemoryHigh=5G -- \
  npx playwright test --workers=2
```

The stack (Postgres, two Grafana instances, nginx) needs about 1.5 GB, and two
Chromium workers about 1 GB. If a run times out, stop it, then check
`pgrep -c -f chrom` and `free -h` before starting anything else. A run killed by
systemd-oomd needs to use less memory, not a higher limit.

### L5: performance budget

- **Database:** run `bench_dashboards.py` nightly on the one-year synthetic
  dataset with `--json`. Gate on a committed `tests/data/dashboard_budgets.json`:
  - per-query, the shared buffers read (`EXPLAIN (ANALYZE, BUFFERS)`), which is
    stable across runners, with a 1.5× margin;
  - per-dashboard median milliseconds at 30 and 365 days with a 2× margin, because
    milliseconds on shared runners are noisy.

  A budget change is a reviewed diff, like a baseline.
- **Browser:** time from navigation until the last panel finishes loading, per
  dashboard, on the year dataset. Record the trend in the nightly report, without
  a gate until there is enough history to choose a threshold.

### L6: upgrades

- Let Renovate (or Dependabot) propose bumps to the Grafana image and plugin
  versions. The bump's CI run includes L3 and all of L4, so screenshot diffs show
  what changed.
- Nightly, run L3 and L4 tiers 1–3 (no screenshots) against the newest Grafana
  minor release, to learn about breaking changes before a bump is merged.

### Beyond tests: watching the running deployment

Optional, but it closes the loop. genesis already runs Gatus. A Gatus endpoint
for the deployed Grafana can check `/api/health`, the datasource health with a
read-only service account token, and that the last fetch is less than 26 hours
old. That catches "the dashboards are fine but no data has arrived", which no
pre-release test can.

## Where each layer runs

| Layer | Every push (blocking) | Nightly | Release |
| --- | --- | --- | --- |
| L0 lint | `pytest` job | | yes |
| L1 contracts (the 3-zone × 5-range matrix runs nightly; push runs one zone and two ranges) | `pytest` job | full matrix | yes |
| L2 semantics | `pytest` job | | yes |
| L3 Grafana server | `stack` job | against the newest Grafana | yes |
| L4 browser, tiers 1–4 | `e2e` job (needs the `stack` image) | against the newest Grafana | yes |
| L4 browser, tier 5 screenshots | `e2e` job, pinned Grafana only | | yes |
| L5 performance | | yes | |

The `e2e` job runs in parallel with `pytest` and after `stack`. It gets the same
image by rebuilding it from the GitHub Actions build cache, which takes seconds
once item 6 of the [test speed plan](test-speed-plan.md) is done, rather than by
uploading a 268 MB artifact. Target:
`e2e` under 3 minutes, so the total wall clock stays under about 4 minutes.

## Phases

| Phase | Work | Done when | Size |
| --- | --- | --- | --- |
| 0 | Fix the weak spots: exact uid set in the smoke test, leave macros to Grafana, multi-value `Source` test, canonical JSON for Garmin Stats | the smoke tests fail when a dashboard is removed or a macro breaks | ½ day |
| 1 | L0 lint with the description allow-list | every rule in the standards doc that can be checked statically is a test | 1 day |
| 2 | L1 schema snapshot, isolation for every query, panel registry | a new panel or a renamed column fails CI until it is handled deliberately | 1–2 days |
| 3 | L3 as pytest with frame and notice checks; L4 tiers 1–4; CI `e2e` job | no panel error, console error or empty expected panel gets past CI; step 3 of the standards doc becomes "run the e2e suite" | 3 days |
| 4 | L4 screenshots and the baseline procedure | chart panels are compared with reviewed baselines | 1–2 days |
| 5 | L5 budgets, nightly workflow, compatibility run against the newest Grafana | a slower query or a breaking Grafana release shows up the next morning | 1 day |

Phase 0 needs nothing new. Phases 1 and 2 run in the existing pytest job. Phase 3
is the one that closes the main gap, and depends on the job split from the speed
plan.

## Done means

- The standards doc no longer says anything is "only verified by looking".
- Every panel is in the registry with value tests or a written reason.
- A dashboard edit that breaks a query, a unit, an override, a layout, a link,
  isolation or the kiosk fails CI before a release image is built from it.
- Screenshot and budget changes are reviewed diffs, never silent updates.
- The browser suite runs on genesis inside its memory limit with two workers.

## Out of scope

- **Rewriting the dashboards as code** (Grafonnet, the Grafana Foundation SDK).
  The standards doc commits to editing the exported JSON, and the tests above work
  on that JSON.
- Testing Grafana or the third-party plugins themselves beyond how our dashboards
  use them.
- Browsers other than Chromium.
- Accessibility audits of Grafana's own UI.

## Status (2026-11-03)

Phases 0 to 5 are implemented. What exists, and what is deliberately not done:

| Layer | Where | Notes |
| --- | --- | --- |
| L0 lint | `tests/test_dashboard_lint.py`, `tests/dashboards.py` | Ratchet file `tests/data/lint_ratchet.json`: 58 missing descriptions and 21 `noValue` that are not an en dash. The fixes made on the way: canonical JSON, `weekStart`, `graphTooltip` and `description` for Garmin Stats and Strength, a dead datasource variable, the Ski to Strength link keeps variables. |
| L1 contracts | `tests/test_dashboard_contracts.py`, `tests/panel_registry.py`, `tests/data/panel_schemas.json` | Push runs one zone and two ranges, the nightly workflow all fifteen. Panel registry: 61 of 130 panels have value assertions, 69 are in `NOT_YET`. |
| L2 semantics | `tests/test_personas.py` | Unchanged; the registry now says what is still missing. Baseline bands, the remaining Garmin Stats panels and the strength filters are the next ones. |
| L3 Grafana server | `tests/test_grafana_stack.py` | Replaces `smoke_grafana.py` and `smoke_kiosk.py`. The datasource expands the time macros itself (checked against Grafana 13.0.2); the frontend substitutes variables and `${__interval}`. The API returns each dashboard as its file (minus `id` and `version`), so the drift check compares them directly. |
| L4 browser | `e2e/` | Tiers 1 to 5. Run with `e2e/run.sh` (pinned Playwright 1.64.1 image, 3 GB, two workers); baselines with `e2e/update-baselines.sh`. 1.7 MB of screenshots for 93 chart panels. Geomaps are left out of the screenshots: their basemap needs the internet. |
| L5 budgets | `scripts/check_dashboard_budgets.py`, `tests/data/dashboard_budgets.json` | Buffers per query (1.5x) and median milliseconds per dashboard (2x), run by the nightly workflow. The browser load time is attached to each test as an annotation, without a gate. |
| L6 upgrades | `renovate.json`, `.github/workflows/nightly.yml` | Renovate needs its GitHub app installed on the repository. The nightly run starts the stack with `GRAFANA_IMAGE=grafana/grafana:latest`. |

Found by the new tests: a state timeline with no rows says "Data does not have a time field" instead
of "No data" (Health panels 10 and 36; changing the query format did not help, so the browser tests
list them); Health panel 10 and others only showed up once the browser tests expanded every collapsed
row and scrolled through every panel.

Not done: the clock is frozen with `setFixedTime`, but the kiosk idle test waits for its real
20 seconds instead of fast-forwarding, because a fake clock stalls Grafana's own timers; the accessibility
check covers the kiosk page only, as planned; Gatus monitoring of the deployment is outside this
repository.

Run end to end on 2026-11-03 on one machine, against the Compose stack built as in `tests.yml`: the L3 tests
11 of 11, the whole `e2e/` suite 29 of 29 (7.0 min with two workers; the one kiosk retry that run needed was
the "Data does not have a time field" wording above, now allowed there too, then 18 of 18 over three
repeats), the upgrade rehearsal, the nightly matrix (`DASHBOARD_MATRIX=full`, 40 passed) and the nightly
budget job (330 measurements within budget). Two things only the nightly commands showed: the persona fixture
now records the application version as `migrate` does, and `bench_dashboards.py seed` refreshes the rollups,
because without it every query read the raw samples and the budgets failed 10 to 50 times over. On GitHub (run 41661821531, green): `stack` 114 s and `e2e` all 29 browser tests in 266 s, with the screenshot baselines generated here matching on the runner. Still to be seen on a release: `cancel-in-progress` (the called `Tests` run's concurrency group contains the caller's workflow name and the tag ref, so it does not share a group with a branch push) and the native arm64 build.
