# garmin-fit-store

Converts Garmin FIT files to and from PostgreSQL + TimescaleDB without losing data. See
[docs/fit-converter-plan.md](../../docs/fit-converter-plan.md) and the FIT store section of
[docs/architecture.md](../../docs/architecture.md).

```python
from garmin_fit_store import decode, encode, canonical, store
fit = decode(data)                        # IR: definitions + messages with raw values, in file order
assert canonical(decode(encode(fit))) == canonical(fit)
file_id = store.import_file(connection, data, source_id=1, garmin_activity_id=123)
data = store.export_file(connection, file_id)
```

Modules: `decoder`, `encoder`, `ir` (also the JSONL form: a header line, `def` lines, `msg` lines with `f` = raw fields by
number and `d` = developer fields by `index:number`), `semantics` (named, scaled values), `store`, `operations`,
`convert`, `compat` (a fitparse-shaped reader). `packages/garmin-fit-store-ts` is the TypeScript codec.

**Licence.** This package is BSD-3-Clause. It depends on the official `garmin-fit-sdk`, and the FIT profile it builds at run
time from that package, under Garmin's FIT Protocol License; nothing derived from the profile is committed here.
