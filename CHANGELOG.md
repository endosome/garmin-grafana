# Changelog

## 0.11.2

- The "Last Sync" panel is now "Newest data from the watch": it was always the newest data Garmin Connect has, not the time of the last fetch.
- Each "Fetch now" request gets a result when the sync that served it ends: `finished_at`, `new_points` and a short text ("12 new points", "nothing new since 09:40", or "incomplete: …" / "failed (…): …"). Migration `011` adds the columns and the `fetch_status` view.
- A new "Fetch now result" panel beside the button shows the latest request's result through the ordinary read-only datasource; the button's login stays INSERT-only. The Heart Rate chart is two rows shorter to make room (its browser baseline needs `e2e/update-baselines.sh`).
- Also in this release: the `garmin-fit-store` package (`fit` schema, FIT converters, edit primitives; see `docs/fit-converter-plan.md`); `fitparse` is no longer a dependency.
