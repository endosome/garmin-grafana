# FIT converter plan

As of 2026-11-03. A second package in this repository, `garmin-fit-store`, that converts between Garmin's FIT
format and the PostgreSQL + TimescaleDB database in both directions, and whose schema can hold any FIT file
without losing data. It is the storage and codec layer under a later browser tool (pglite + the TimescaleDB fork,
both ours) that fixes activity files the way Garmin's own tools do: Time Adjuster, File Combiner, Device Changer,
FIT to GPX / TCX / JSON / CSV, Section Remover, Field Merger.

The plan follows [grafana-optimization-plan.md](grafana-optimization-plan.md): a dated header, decisions, a numbered
worklist with a done criterion per item, and status sections prepended as work lands. When the work is done the
decisions move into [architecture.md](architecture.md) and the test layers into [testing.md](testing.md).

## Decided and assumed

The user decided items 1 to 3 and delegated 4 to 23 to a design review. Each decision below is marked **decided**
(by the user), **recommended** (by the review, adopted) or **assumed, verify** (a fact the plan depends on that could
not be confirmed from a primary source). Override any of them by editing this section; the worklist refers back here.

**Assumed, verify.** The user confirmed A1, A2, A4 and A5 on 2026-11-03 and named the forks (A6); A3 was amended
from the forks' sources the same day. They stay listed because they decide scope and record where the facts came from.

- A1. Garmin Connect imports activity files only, as FIT, GPX or TCX, one file at a time through Activities, Import.
  Garmin's support article returned 403 to the fetch; forum and third-party guides agree on this. It does not
  import monitoring, sleep or other wellness files. Confirmed by the user.
- A2. Garmin Connect exports a single activity as the original FIT (zipped), TCX, GPX, KML and CSV splits, and the
  account data export ("Export Your Data") holds the original activity FIT files plus wellness data as **JSON**
  (`DI-Connect-Wellness`: sleep, monitoring, HRV, body composition), not as FIT. Three guides agree; no Garmin page
  was reachable. Under decision 2 this makes the scope **activity FIT files in both directions**; wellness file types
  (monitoring, sleep, HRV, metrics FIT as devices write them) are out until Garmin imports or exports them.
  Confirmed by the user.
- A3. **Amended 2026-11-03 after reading the forks.** The browser build is TimescaleDB 2.31.0 and Toolkit 1.26.0
  ported to PGlite (packages `@electric-sql/pglite-timescaledb` and `@electric-sql/pglite-timescaledb-toolkit` in
  the `timescaledb` branch of endosome/pglite, built by the `timescaledb` branch of endosome/postgres-pglite from the
  `pglite` branches of endosome/timescaledb and endosome/timescaledb-toolkit). Hypertables, continuous aggregates,
  compression, retention and refresh policies and the Toolkit all work; the fork's own tests use this repository's
  migrations as fixtures, including the `samples` upsert, the real-time rollups and the `heartbeat_agg` rollup.
  What differs from the server: there are no background workers, so policies run only when the host calls
  `pg.timescaledb.runDueJobs()`, and never inside an open transaction; and the browser runs PostgreSQL 18.6 while
  the Compose stack runs 17.11. The schema rule (R11) is therefore "whatever the pinned `timescaledb-ha` image
  supports, written to run on PostgreSQL 17 and 18, with nothing in the `fit` schema that depends on a policy having
  run". Item 2 confirms this by applying the FIT migrations under the fork's test harness.
- A4. Expected volume is years of activities per account: thousands of files, millions of `record` rows. Item 9
  benchmarks it. Confirmed by the user.
- A5. **Licence decision, confirmed by the user.** The FIT profile (Profile.xlsx, and the `Profile` module of the official
  SDKs) is "Licensed Technology" under Garmin's FIT Protocol License. Clause 2c forbids distributing it or derivatives
  except as the licence sets out, and clause 4 calls the Licensed Technology Garmin's Confidential Information. This
  plan therefore does **not** commit a profile artifact to this public BSD-3 repository. Instead the package pins the
  official `garmin-fit-sdk` as a runtime dependency (its decoder and encoder unused in production) and generates the
  profile JSON and the `fit.profile_*` rows from the installed package's `Profile` at migrate time, recording the SDK
  version. The browser package (item 16) does the same from `@garmin/fitsdk`. `fitparse` (MIT) has shipped a derived
  profile for years, which is context, not permission. Should that reading of the licence change, R14 becomes a
  committed JSON with Garmin's licence file beside it; nothing else in the plan moves.
- A6. **Confirmed 2026-11-03.** The forks are https://github.com/endosome/pglite (branch `timescaledb`),
  https://github.com/endosome/postgres-pglite (branch `timescaledb`, on `REL_18_6-pglite`),
  https://github.com/endosome/timescaledb (branch `pglite`) and https://github.com/endosome/timescaledb-toolkit
  (branch `pglite`). The two npm packages are not published yet; item 16 either publishes them (npm or the GitHub
  registry) or builds them in CI with the fork's `buildAndTestTimescale.sh`. Item 2 uses a local build.

**Decided**

- D1. Both directions, FIT to database and database to FIT (question 1).
- D2. Garmin Connect parity for file types: what Connect imports or exports is in scope, nothing else (question 2).
  With A1 and A2: activity files.
- D3. The future browser tool performs Garmin's six operations listed above (question 3). The schema and the edit
  primitives are designed for exactly those.

**Recommended**

- R4. Round trip is semantic, not byte-identical. The oracle: decoding the original and decoding
  encode(decode(original)) give the same sequence of (global message number, field number, raw base-type value),
  developer fields keyed by (developer data index, field definition number), in the same message order. Byte identity
  is impossible to promise: compressed timestamp headers, local message type assignment and definition placement are
  encoder choices, and Garmin's own tools do not preserve them either.
- R5. Unknown message numbers, unknown fields, manufacturer-specific fields and Connect IQ developer fields are all
  preserved, as raw base-type values with their definition (size, base type, endianness). Named, scaled, unit-bearing
  values are a derived layer, never the stored truth, so a FIT profile upgrade re-derives names without a re-import.
- R6. Every message carries its position in the file (`seq`), and export writes in that order. Timestamps cannot
  rebuild the order: `hrv`, `field_description` and `developer_data_id` have no timestamp, and the placement of
  same-second `event` messages next to `record` messages matters to Garmin Connect's validator.
- R7. A separate `fit` schema, not the JSONB `records` / `samples` documents. The link to the existing pipeline is a
  nullable Garmin activity id on the file row, which the collector fills when it downloads the file through the API
  and knows the id. The existing dashboards and the `fitparse` path are untouched until item 12.
- R8. No breaking changes to existing tables, new migrations only.
- R9. Storage shape: `fit.files` (metadata, SHA-256, profile version, parent file for derived edits),
  `fit.blobs` (original bytes), `fit.definitions` (one row per definition message), `fit.messages` (every message
  that is not a `record`: nullable timestamp, `seq`, raw fields as JSONB keyed by field number, developer fields as
  JSONB) and `fit.records` as a hypertable keyed by (file id, timestamp, `seq`) in the same raw shape. `record` is
  the only high-volume message and the one the six operations touch per row. Typed, named views over the raw rows are
  generated from the profile tables (R14).
- R10. Raw values are the truth, with a decoded `timestamptz` column on every row that has a timestamp (for chunking,
  indexes and the time-shift operation) and derived SI views (degrees, m/s, metres) on top. Never store only converted
  floats: rounding breaks the round trip. Latitude and longitude stay semicircle integers in raw and become degrees in
  the views.
- R11. Schema rule for the browser (A3): any feature the pinned `timescaledb-ha` image supports, written to run on
  PostgreSQL 17 and 18, and nothing in the `fit` schema may depend on a policy having run, because the browser runs
  jobs only when the host asks. The `fit` schema as planned needs no policy at all: raw rows, indexes, views and SQL
  functions. Item 2 applies the FIT migrations under the fork's test harness and item 16 adds a CI gate that does
  the same on every push.
- R12. No extensions beyond `timescaledb`. Identity columns, no PostGIS, pgcrypto or uuid-ossp.
- R13. Surrogate key per file. Identity is never derived from `file_id` contents: Device Changer edits exactly those
  fields, and that derivation is what the current importer's MD5 scheme does. Duplicates are detected by SHA-256 per
  source. Originals are immutable; an edit produces a new file row with `parent_id`. Multi-user through a nullable
  `source_id bigint` column without a foreign key, so the `fit` migrations apply on a database that has no
  `public.sources` (pglite); a root `public` migration may add the constraint later where that table exists.
- R14. The FIT profile (types, messages, fields, scale, offset, units, subfields, components) is a versioned JSON
  document generated from the installed official SDK's `Profile` (A5), from which the codec's lookup tables, the
  `fit.profile_*` rows and the typed views are generated. It is generated, never committed. Every file row carries
  the `profile_version` it was decoded with. A profile upgrade is a dependency bump, a migrate run that loads the new
  profile rows, and a view rebuild.
- R15. Original bytes live in the database (`fit.blobs`, `bytea`, its own narrow table). The browser has no disk, and
  re-verifying a file after a profile change needs the bytes. The collector's existing on-disk option
  (`KEEP_FIT_FILES`) stays as an optional mirror.
- R16. Python first, in this repository's toolchain, with a language-neutral contract: the schema, the profile tables,
  the SQL edit primitives and a JSONL intermediate representation (definitions plus data messages with raw values)
  backed by a conformance corpus of fixtures and their expected representation. The codec is the thin, replaceable
  part; the TypeScript codec for the browser is the last milestone and is accepted when it passes the same corpus.
  Not TypeScript now because the repository, CI, image and tests are Python and the collector must consume this.
- R17. Our own profile-driven codec for decode and encode. The official SDKs (Python and JavaScript, both 21.218.0 at
  the time of writing) do encode, but their encoders drop unknown fields and messages ("Unknown values in the message
  will be ignored by the Encoder"), which rules them out for R5. `fitparse` 1.2.0 decodes only and is old enough that
  this repository filters its deprecation warning. `tests/fit_builder.py` already holds an encoder core and the CRC.
  The official Python SDK is a pinned runtime dependency for its `Profile` only (A5); its decoder and `fitparse` are
  differential test oracles. Licence: the official SDKs and the profile are under Garmin's FIT Protocol License, not
  Apache. It allows using the FIT protocol in our software; what it does not clearly allow is redistributing the
  profile, hence A5.
- R18. Library API plus `python -m garmin_fit_store` subcommands (`import`, `export`, `verify`, `roundtrip`,
  `convert`), in the style of `fit_activity_importer`. No HTTP service. No Garmin download inside this package: the
  collector already downloads and hands the bytes over.
- R19. One file per transaction, array inserts of up to 1000 rows as in `storage.py`, COPY only after item 9's
  benchmark says so.
- R20. Three fixture sources: files built by `tests/fit_builder.py` (always, no network), the official SDK's test
  files (`tests/fits/*.fit` in the garmin/fit-python-sdk repository; not committed here because of the FIT Protocol
  License, and used only by tests that skip when `FIT_SDK_FIXTURES_DIR` is unset, so unit tests stay offline), and
  an opt-in real corpus through `FIT_CORPUS_DIR`, mirroring the `TEST_DATABASE=1` pattern, never committed. The
  corpus must include non-Garmin files (Wahoo, Zwift, Coros, app generated) for the "any FIT" claim, and at least one
  file with developer fields and one with compressed timestamps.
- R21. A uv workspace: the new member at `packages/garmin-fit-store/` with its own `pyproject.toml`, `src/`, `tests/`
  and migration directory. The root package depends on it. The Dockerfile copies `packages/`. The 98 percent coverage
  gate applies per package. `migrate.py` applies namespaced migration sets, named `fit/001_….sql` in
  `schema_migrations`, so the existing files and checksums are untouched.
- R22. This document is the plan, at `docs/fit-converter-plan.md`, in the format of the other plan.
- R23. MVP: an activity file imports, exports and passes the R4 oracle with unknown and developer fields preserved.
  The later milestones are typed views and profile tables, collector integration, replacing `fitparse` in the
  existing importer, the six edit primitives, the TypeScript codec and the pglite gate.

## Constraints the request implies

- The FIT migrations apply standalone: the `fit` schema carries a nullable `source_id` without a foreign key (R13)
  and references nothing in `public`. The fork can run the collector's whole schema (A3), but the browser tool
  should not need the collector's tables, rollups or refresh policies to open and fix a file.
- The SQL runs on two PostgreSQL majors, 17.11 on the server and 18.6 in the browser. No feature of one that the
  other lacks; the CI gate (item 16) is what catches a slip.
- Invalid values. A FIT field can be in the definition but hold the base type's invalid sentinel (0xFF, 0xFFFF, ...).
  The store keeps rows sparse: a field whose value is invalid is omitted from the row's JSONB, and the encoder writes
  the invalid sentinel for every field the row's definition lists and the row omits. The R4 oracle therefore compares
  decoded values with invalid normalised to absent. Item 3 implements this rule once, in the IR.
- Persistence boundary. `architecture.md` says collectors perform no database writes and the orchestration layer
  owns persistence. The collector therefore returns the FIT bytes next to its `RecordBatch`, and the orchestration
  layer calls the store (item 10).
- Time Adjuster must shift every timestamp carrier: `record.timestamp`, `timestamp_16` in `record`, `event`, `lap`
  and `session` timestamps and start times, `activity.timestamp` and `local_timestamp`, `timestamp_correlation`,
  developer timestamps and `file_id.time_created`. Because every row stores its decoded absolute timestamp next to
  the raw value, the shift is one UPDATE of the raw field plus the derived column per table, then a re-encode.
- Field Merger joins two record streams on timestamp; the hypertable index on (file id, timestamp) covers it.
- File Combiner needs the `session` and `lap` totals recomputed; the profile tables say which fields are totals,
  averages, maxima and timers, so the recomputation is data-driven where it can be and explicit for the rest.
- Garmin Connect's acceptance of a re-exported file has no automated oracle. Item 7 makes it a manual acceptance
  step with a checklist; the automated gate is the R4 oracle plus the official SDK decoding our output without error.
- The panel registry test means no dashboard change in this plan. If a dashboard ever reads the `fit` schema, the
  reader role's default privileges (which are per schema) need a migration step; not in scope here.
- Every test command in this plan runs inside the memory limit from the machine's rules:
  `systemd-run --user --scope -p MemoryMax=6G -p MemoryHigh=5G -- <command>`, and at most two xdist workers.

## Package layout

```
packages/garmin-fit-store/
  pyproject.toml                 garmin-fit-store, Python 3.13, psycopg only at runtime
  LICENSE                        BSD-3-Clause
  src/garmin_fit_store/
    __init__.py, __main__.py     library surface and the CLI subcommands
    profile.py                   builds the profile document from the installed garmin-fit-sdk Profile (A5);
                                 lookup by message and field number; never committed as data
    protocol.py                  header, record headers, definition messages, CRC, base types
    decoder.py                   bytes -> IR (definitions and data messages with raw values)
    encoder.py                   IR -> bytes, in file order, one definition per local type change
    ir.py                        the intermediate representation and its JSONL form
    semantics.py                 raw -> named / scaled / unit values and back, from the profile
    store.py                     IR <-> fit.* tables (import, export, verify)
    operations.py                the six edit primitives as SQL, each returning a new file row
    convert.py                   IR -> GPX / TCX / JSON / CSV
    migrations/001_….sql         applied by garmin_grafana.migrate as fit/001_….sql
  tests/
    conftest.py                  database fixture (TEST_DATABASE=1), corpus fixture (FIT_CORPUS_DIR)
    corpus/                      fit_builder-made files and their expected JSONL (the conformance corpus)
  tools/profile_diff.py          diff report between two SDK versions' profiles, for the bump's commit message
```

The root `pyproject.toml` gains `[tool.uv.workspace] members = ["packages/*"]` and a dependency on
`garmin-fit-store` from the workspace. `tests/fit_builder.py` moves into the package (the root tests import it from
there) once item 3 has an encoder that supersedes it.

## Worklist

Each item ends with its done criterion. Sizes are days of focused work.

1. **Workspace member, licence notes and the profile document.** (1 day) Create `packages/garmin-fit-store/` as a
   uv workspace member with an empty library, the CLI skeleton, its own pytest configuration and the 98 percent
   coverage gate; make the Dockerfile copy `packages/` and `uv sync --locked` install both; pin `garmin-fit-sdk`
   and write `profile.py`, which builds the profile document (types, messages, fields, scale, offset, units,
   subfields, components, the SDK version) from the installed `Profile`; extend `migrate.py` to apply every
   workspace member's `migrations/*.sql` under a namespaced name. Done: `uv run --locked pytest` at the root still
   passes; `uv run --locked --package garmin-fit-store pytest` passes; `docker build .` succeeds and the image
   contains both packages; `migrate` on a persona database applies no FIT migration yet and leaves
   `schema_migrations` unchanged; the profile document is stable (built twice, identical) and no Garmin-derived data
   file is in the tree (a test greps the package for message-number tables).

2. **Schema, migration `fit/001`, and the first run under the fork.** (2 days) The `fit` schema per R9 to R13:
   `files`, `blobs`, `definitions`, `messages`, `records` (hypertable, 30-day chunks, index on (file_id, time)),
   `profile_versions`, and the `profile_types`, `profile_messages`, `profile_fields` tables, which `migrate` fills
   from the profile document after the SQL file (an upsert keyed by SDK version, so a bump adds rows and never
   rewrites a version already loaded). Build the fork locally (`buildAndTestTimescale.sh` in endosome/pglite, branch
   `timescaledb`, inside the machine's memory limit) and add the FIT migrations to its fixture set, the way it
   already applies this repository's migrations. Done: migration applies on the pinned `timescaledb-ha` image and is
   idempotent; `test_schema_migrations.py`-style tests cover the namespaced application; the FIT migrations apply
   and a corpus file's rows insert and read back under the fork on PostgreSQL 18; the result and the fork commit used
   are recorded in a status section here.

3. **Protocol layer and decoder to IR.** (3 days) `protocol.py` and `decoder.py`: 12 and 14 byte headers, normal
   and compressed timestamp record headers, definition messages with architecture and developer field definitions,
   all base types including arrays and strings, `timestamp_16` and the accumulated timestamp rule, chained files
   (several FIT files concatenated), header and file CRC verification with a tolerant mode for files with a zero
   header CRC. The IR (`ir.py`) is a list of definitions and data messages with raw values and `seq`, serialisable
   as JSONL. Done: every `fit_builder` fixture and every official SDK test file decodes; for each, the raw values
   equal what the official Python SDK decodes (differential test); malformed inputs raise typed errors, never hang;
   the conformance corpus is committed as pairs of file and expected JSONL.

4. **Encoder from IR.** (2 days) `encoder.py` writes an IR back to bytes: one definition message whenever a local
   message type's definition changes (up to 16 local types with least-recently-used reuse), normal headers only, the
   same architecture as the source definition, developer fields after their `field_description`, correct CRCs.
   Done: for every corpus file, decode(encode(decode(f))) equals decode(f) under the R4 oracle; the official Python
   and JavaScript SDKs decode our output without error and report the same named values as for the original;
   `tests/fit_builder.py` is reimplemented on top of the encoder and the root test suite still passes.

5. **Semantics layer from the profile.** (2 days) `semantics.py`: raw to named value (enums, scale, offset, units,
   date_time and local_date_time, semicircles, subfields chosen by reference fields, components and accumulated
   fields, bit flags) and back, driven only by the profile document. Unknown fields and messages pass through as
   raw. Done: named values equal the official SDK's for every corpus file, including subfield and component
   expansion; the inverse maps named back to the original raw for every field; the profile document alone, with no
   Python table, decides every conversion (a test swaps in a one-message profile and checks the raw fallback).

6. **Store: import, export, verify.** (3 days) `store.py`: `import_file(bytes, source_id=None, garmin_activity_id=None)`
   writes one transaction (files, blobs, definitions, messages, records in batches of 1000, SHA-256 dedup per source
   returning the existing id), `export_file(file_id)` rebuilds the IR in `seq` order and encodes it, `verify(file_id)`
   runs the R4 oracle against the stored blob. The CLI gets `import`, `export`, `verify` and `roundtrip` (import into
   a temporary schema, export, compare, drop). Done: every corpus file round-trips through a real database; a second
   import of the same bytes returns the first row and writes nothing; a failed import leaves no partial rows;
   `roundtrip` on the opt-in real corpus reports zero differences (the first run on real Garmin, Wahoo, Zwift and
   Coros files is recorded in a status section with the file count).

7. **Garmin Connect acceptance checklist.** (half a day, manual) Export three real activities (run with GPS, indoor
   with developer fields, multisport) from the database and upload them to a Garmin Connect account; compare the
   activity summary, laps, charts and Connect IQ fields with the original. Record the outcome here. Done: the three
   files are accepted and show the same values; any rejection becomes a worklist item with the file's shape.

8. **Typed views and the profile tables.** (2 days) Generate `fit.v_record`, `fit.v_lap`, `fit.v_session`,
   `fit.v_event`, `fit.v_file_id` and `fit.v_device_info` as SQL views over the raw tables and `profile_fields`,
   with named columns in SI units, degrees for positions, timestamptz for times, and a `v_record_dev` view that
   pivots developer fields by name. The view SQL is generated by `tools/generate_views.py` from the profile document
   and committed as migration `fit/002` (the column list is ours, the names are the FIT field names). Done: view
   columns and values equal the semantics layer's named values for the corpus; the views work under the fork's test
   harness from item 2; their cost is measured in item 9, with a target of 50 ms for one activity's record
   view on a year of data.

9. **Volume benchmark.** (1 day) Extend `scripts/bench_dashboards.py`'s pattern: seed a year of a persona's
   activities as FIT files (one per day, 1 Hz records, GPS), import them, and time import per file, export per file,
   `verify`, and the view queries at 30 days and 1 year. Done: the numbers are in a status section; import stays
   under 1 s per typical one-hour activity, export under 0.5 s and the item 8 view query under 50 ms on the
   development machine. If not, two levers in this order, each re-measured: COPY through a staging table for the
   import; promoting the common `record` fields (timestamp, position, distance, speed, altitude, heart rate, cadence,
   power, temperature) to typed columns holding the raw integers, with JSONB only for the rest. The benchmark is
   really a test of JSONB per row at millions of rows, and the second lever is the answer if it fails.

10. **Collector integration.** (2 days) `fetch_activity_GPS` returns the downloaded FIT bytes and the Garmin
    activity id next to its points (collectors write nothing, see the constraints); the orchestration layer in
    `garmin_fetch` passes them to `garmin_fit_store.import_file` with the source id, in its own transaction after the
    activity's `RecordBatch` is written; `FIT_STORE_ENABLED` (default True) turns it off; `KEEP_FIT_FILES` keeps
    working as the disk mirror. The bulk importer does the same for the account export's FIT files. Done: the
    existing collector tests pass unchanged; new tests show the file row linked to the activity id and the `samples`
    rows for the same activity side by side; a failure in the FIT store never fails the activity's existing
    processing (logged, counted, retried on the next run through a `fit.pending_imports` row).

11. **Convert: GPX, TCX, JSON, CSV.** (2 days) `convert.py` from the IR: GPX 1.1 with Garmin's TrackPointExtension
    (heart rate, cadence, temperature, power), TCX with laps and trackpoints, JSON as the named semantics output, CSV
    in the official FIT CSV Tool's layout so files can be diffed against it. Done: GPX and TCX validate against their
    XML schemas; our CSV equals the FIT CSV Tool's for the corpus apart from documented formatting differences;
    the CLI `convert --to gpx|tcx|json|csv` is documented in [manual-import-instructions.md](manual-import-instructions.md).

12. **Replace `fitparse` in the existing importers.** (3 days) `fit_activity_importer` and the GPS, cycling
    dynamics, strength and swimming extraction in `garmin_fetch` read the semantics layer instead of `fitparse`,
    keeping their output dictionaries identical. The FIT-derived activity id for standalone imports stays MD5 of the
    `file_id` fields for compatibility with existing rows (R13 applies to the new schema only). Done: the current
    tests of those modules pass unchanged as the regression oracle; the `fitparse` dependency and its warning filter
    are removed from the root `pyproject.toml`; the personas and dashboard contract tests pass.

13. **Edit primitives, part one: Time Adjuster, Device Changer, Section Remover.** (3 days) `operations.py` and SQL
    functions in migration `fit/003`: each primitive copies the source file's rows into a new file row with
    `parent_id` and an `operation` JSONB describing the edit, then applies it in SQL. Time Adjuster shifts every
    carrier listed under constraints; Device Changer rewrites `file_id` and `device_info` manufacturer, product,
    `garmin_product` and serial number with the profile's enum names; Section Remover deletes `record` rows in a
    time range, shifts later timestamps to close the gap or leaves them (both as Garmin offers), and recomputes
    `lap` and `session` totals and timers over the remaining rows. Done: for each primitive, the exported file decodes
    with the official SDK, the R4 oracle holds between the SQL result and a Python reference implementation on the
    IR, and the original file row is unchanged.

14. **Edit primitives, part two: Field Merger and File Combiner.** (3 days) Field Merger copies chosen field numbers
    (heart rate, position, power, or any field) from a donor file's `record` rows into the target by nearest
    timestamp within a tolerance, adding the field to the target's definition. File Combiner concatenates files
    ordered by time into one with a single `file_id`, renumbered `lap` and `session` `message_index`, one `activity`
    message, and totals recomputed as in item 13. Done: the same criteria as item 13, plus a combined file of two
    real split recordings passes the Garmin Connect checklist (item 7).

15. **Documentation and promotion.** (1 day) Move the decisions into `architecture.md`, the test layers into
    `testing.md`, the CLI into `manual-import-instructions.md`, a `packages/garmin-fit-store/README.md` for the
    library API and the IR format, and the licence notes into the root README. Done: this document is a status
    history and the other documents carry the current description; the README's dependency and licence paragraph
    names the FIT Protocol License for the `garmin-fit-sdk` dependency and the profile built from it.

16. **TypeScript codec and the pglite gate.** (5 days, last) `packages/garmin-fit-store-ts/` (npm, next to `e2e/`'s
    toolchain) implements decoder, encoder and semantics from the same profile document, built at install time from
    `@garmin/fitsdk`'s `Profile` (A5), accepted against the committed conformance corpus; a CI job runs the FIT
    migrations on `@electric-sql/pglite` with `@electric-sql/pglite-timescaledb` under Node (published from the fork
    first, or built in the job from endosome/postgres-pglite, which is slow and so cached), imports a corpus file
    through the TypeScript store, exports it and checks the R4 oracle. Done: the TypeScript codec passes every corpus
    pair; the Node job is green in `tests.yml`; a browser demo page imports a FIT file into pglite and downloads the
    export.

Total about 36 days. Items 1 to 7 are the MVP (R23), about 13 days.

## Status

Prepend a dated section here as items land: what was done, the numbers, and anything that changed a decision above
(with the decision's label, e.g. "A3 confirmed" or "R19 amended").

### 2026-11-03: implemented

Every item is implemented except item 7, which is a manual upload to a Garmin Connect account.
The current description lives in [architecture.md](architecture.md), [testing.md](testing.md) and
[manual-import-instructions.md](manual-import-instructions.md).

- Item 1: workspace member, Dockerfile copies `packages/`, image built and checked. Migrations are namespaced (`fit/001_…`).
- Item 2: the migrations and a corpus file's rows apply and read back under the fork (`tools/pglite_check.mjs`, PostgreSQL
  18.6, endosome/pglite commit c7f5edd67a), including the typed views and the edit primitives. Idempotent on the pinned image.
- Item 3 and 4: decoder and encoder; 7 conformance files; the official Python SDK's three fixtures and one third-party file
  (`Garmin.fit`) round-trip and match the SDK's raw values. **Finding:** the official Python SDK cannot read compressed
  timestamp headers, so those are checked against fitparse. Chained files decode into one IR and encode as one file.
  `timestamp_16` belongs to monitoring files, out of scope under A2, and is not implemented. `tests/fit_builder.py` now
  re-exports the package builder.
- Item 5: named values equal the SDK's, including sub-fields and components; the inverse is exact for primary fields.
- Item 6: import/export/verify/roundtrip. **R9 amended:** `fit.definitions` holds each distinct definition once, not one
  row per definition message. Not done: a roundtrip on a real Wahoo/Zwift/Coros corpus (none available; only the five
  files above were run, zero differences).
- Item 7: done with the caveats under the real-files entry below (upload accepted, summaries equal).
- Item 8: **amended:** the view SQL is generated at migrate time from the profile, not committed as `fit/002`, because it
  would be profile-derived data (A5). Views show NULL, not an error, where a file puts an array where the profile has a number.
- Item 9 (365 one-hour 1 Hz activities, 1.31 M records): the first run missed the view target (41 to 76 ms for one activity),
  so both levers were applied: the import writes with COPY, and the eleven record fields every chart reads are stored
  generated columns (migration `fit/006`; the JSONB stays the truth, the view falls back to it where a column is NULL).
  Final numbers: import 0.31 s per file, export 0.24 s, verify 0.63 s, the one-activity view query 23 ms, a 30-day hourly
  aggregate 64 ms, a full-year daily aggregate 2.0 s. All targets are met.
- Item 10: `fetch_activity_GPS` returns a `RecordBatch` carrying `(activity id, bytes)`; the orchestration stores them after
  the records are written; failures go to `fit.pending_imports` and are retried. The bulk importer uses the same path.
- Item 11: GPX (TrackPointExtension, PowerExtension) and TCX validate against Garmin's and Topografix's XSDs. The CSV follows
  the FIT CSV Tool's layout; amended 2026-11-03 against the real tool (the Maven Central jar `com.garmin:fit` holds
  `com.garmin.fit.csv.CSVTool`): 0 differing rows for the committed activity file and for three real files (a fenix 2 run,
  an indoor-trainer activity and the 24,274-row multisport file), after fixing sub-field names (`garmin_product`), omitted
  invalid fields, blank Definition units, expanded components (`enhanced_speed`) and 32-bit float text. Remaining documented
  differences: local message numbers (the IR renumbers definitions; the tool prints the wire's), the compressed-timestamp
  definition, developer fields (the tool adds their columns), the 65535 sentinels it prints in arrays instead of scaled
  numbers, and unknown byte fields it prints as text. The test `test_csv_equals_the_official_tool` runs the comparison when
  `FIT_CSV_TOOL_JAR` is set.
- Item 12: `fitparse` removed from the root dependencies; the existing tests pass unchanged. It stays a dev dependency of the
  package as a differential oracle.
- Items 13 and 14: SQL functions and Python references agree on the R4 oracle; originals unchanged; SDK decodes the output.
  Totals recomputed: start/end clamping, elapsed and timer time, distance, avg/max/min by name where the scales agree.
  Developer data collisions on combine raise an error. The combined-file Garmin Connect check is manual and open.
- Item 15: documents updated.
- Item 16: `packages/garmin-fit-store-ts` has the decoder, encoder, a store (`importFile`, `exportFile`) and a semantics layer
  (enum names, scale and offset, date_time, components with accumulation; sub-field selection is not ported). Its profile
  document is built from `@garmin/fitsdk`'s `Profile` at test time, which names fields in camelCase, so it is compared
  with the Python layer by field number. The codec passes every corpus pair; the pglite test imports each corpus file
  through the TypeScript store into PGlite with the TimescaleDB fork, exports it and checks the R4 oracle (it passes
  locally). `tests.yml` runs the Node job (`act push -j fit-ts` passes); `pglite.yml` builds the fork and runs the pglite
  test; manual only. It **passed under act** (`act workflow_dispatch --bind -W .github/workflows/pglite.yml`; `--bind`
  because the fork's build script bind-mounts the workspace into a Docker builder; the build took 27 minutes, the test 5 s).
  Not yet run on GitHub itself. `demo/` holds the browser page, its
  build step and `demo/check.mjs`, which opens it in headless Chromium, imports a corpus file, downloads the export and
  compares it (passes locally). That run found and fixed a Node-only `Buffer` in the codec.
- Item 7 checklist (run; outcome below): `python -m garmin_fit_store export <id> out.fit` for a
  run with GPS, an indoor activity with developer fields, and a multisport activity (also try `time_shift` and `combine` results);
  upload each at Activities, Import; compare summary, laps, charts and Connect IQ fields with the original; record the outcome
  and any rejection (with the file's shape) here.
- Real multi-vendor files (downloaded from python-fitparse's `tests/files`, 2026-11-03, not committed): 8 of 9 pass the R4
  oracle through the database (`roundtrip`): Garmin fenix 2 run and multisport (1 MB, 7 sessions), fenix 5 run, Edge 820,
  an indoor-trainer activity, the developer-types sample, a Wahoo ELEMNT file and a COROS Pace 2 file with misaligned
  fields. The ninth (a Strava app export) is rejected by us as "truncated" and by the official SDK too (`Invalid local message
  number` at byte 7472, integrity check false). The GPS run and the multisport file export byte-identical; the developer-field
  file exports 36 bytes shorter (R4 holds, the official SDK's integrity check passes). Exports for the upload are in the session
  scratchpad (`up/gps_run.fit`, `up/indoor_devfields.fit`, `up/multisport.fit`).
- Item 7 (2026-11-03, the user's test account, MFA by OTP): `upload_activity` accepted all three exports with no failures
  and Connect created three activities. Against the originals decoded by the official SDK, the summaries are equal:
  GPS run (fenix 2): running, 9008 m, 2832 s, 4 laps, start 2015-08-15; developer-fields file: running, 6754 m, 3424 s, 1 lap,
  start 2017-01-17; multisport (7 sessions: generic, transition, running, transition, running, transition, cycling):
  `multi_sport`, 69831 m, 24617 s, 7 laps. The run's chart data has heart rate, cadence, power, speed, elevation, ground
  contact time, vertical oscillation and the GPS track. Control for the developer fields: I deleted the activity, uploaded the
  original `developer-types-sample.fit`, and Connect's detail metric keys are identical to those of our export (neither shows
  the sample's four Connect IQ fields, whose app is unknown to Connect), then re-uploaded our export. The charts were compared as
  metric keys, not by eye. No rejection.
  Edits (items 13 and 14), all accepted: `time_shift` +3 days of the run (Connect: 2015-08-18, 9008 m, 2832 s, 4 laps, as the
  original); `remove_section` of minutes 5 to 15 of the run with the gap closed (9007 m, 2258 s, 4 laps); `combine` of two real
  runs (one `multi_sport` activity, 2 sessions, 9165 m, 2933 s). The `remove_section` upload exposed two defects, both fixed (migration `fit/007`, `ref_remove_section`, tests): the later records'
  cumulative `distance` kept the removed stretch (the run showed 9007 m after losing ten minutes), and with `close_gap` a lap
  that straddled the section was clamped after the shift, when a time moved into the section can't be told from one inside it
  (laps 849/2988/2979/2193 m became 845/92/3746/2185 m). Re-exported and re-uploaded (the first removed-section activity
  deleted from the account): Connect shows 6882 m, 2258 s, 4 laps of 845, 861, 2975 and 2185 m, which add up to the session.
- Package coverage 98.2 percent; root 100 percent.

### 2026-11-03: plan accepted

The user confirmed A1, A2, A4 and A5 and named the forks (A6). Reading the forks amended A3: the browser build has
the whole of TimescaleDB 2.31.0 and the Toolkit 1.26.0, with jobs run by the host instead of background workers, on
PostgreSQL 18.6. R11 and items 2 and 16 were rewritten accordingly. No code yet.
