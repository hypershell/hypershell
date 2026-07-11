# Research 07 — UUIDv7 for `Source.id` (stdlib-only, 3.11–3.14)

> **SUPERSEDED (2026-07-11, maintainer):** do **not** add a forced/hand-rolled `uuid7()` to
> `core/uuid.py`. The default stays `uuid4` via the existing `uuid()` (which already prefers
> `uuid_utils.uuid7` when the `uuid7` extra is installed). Instead, add a **`timescale`/`timescaledb`
> provider alias** in `data/core.py` that maps to the postgres backend and **gates the `uuid7` extra**
> (bail like missing `psycopg`); in that mode `uuid()` yields v7 automatically. This brief's finding
> that `uuid()` degrades to `uuid4` without the extra, and that `Source.id` change is isolated, still
> holds. See [`00-digest.md`](00-digest.md).

**Revises** PLAN.md per maintainer correction (1): reserved sources are **real rows** with real
UUIDs, and `Task.source` becomes the **native shared UUID type** (not TEXT). Correction (2): no
TimescaleDB-specific code — the only lever is making `Source.id` a **time-ordered UUIDv7** so
per-chunk min/max stats on `task.source` let PostgreSQL/TimescaleDB prune the de-dup query.

This brief resolves how to generate a guaranteed v7 UUID on every supported Python with **no new
hard dependency**.

## Findings

### `uuid()` today is *conditionally* v7 — unsafe for the pruning lever
`src/hypershell/core/uuid.py:8-19`: `uuid()` tries `from uuid_utils import uuid7` and **falls back
to `uuid.uuid4`** on ImportError. But `uuid-utils` is only the optional `uuid7` extra
(`pyproject.toml:59`) and the `dev` group — it is **not** in core `dependencies`
(`pyproject.toml:30-38`). So a plain `pip install hypershell` (no extra) makes `uuid()` return
**uuid4** (random, not time-ordered). Relying on `uuid()` for `Source.id` would silently defeat
chunk pruning on any base install. A dedicated, always-v7 generator is required.

### Changing only `Source.id` is fully isolated
`uuid()` has exactly three call sites, all unaffected:
- `core/logging.py:57` — `INSTANCE` (per-process server/client id).
- `data/model.py:359` — `Task(id=uuid())` in `Task.new`.
- `data/model.py:817` — `Client(id=(id or uuid()))` in `Client.new`.

`previous_id`/`next_id` are **not** freshly generated — the retry minter
`__schedule_next_failed_tasks` (`data/model.py:555-560`) sets `previous_id=task.id` from the parent
and mints the new id via `cls.new(...)` → `uuid()`. So Task/Client ids stay on `uuid()`; `Source`
is a brand-new entity. **Adding a separate `uuid7()` for `Source.new` touches nothing else.**

### `uuid.uuid7()` is stdlib only on 3.14
Confirmed against the official docs (external fact): CPython added `uuid6/7/8` in **Python 3.14**
(`uuid.html` "Changed in version 3.14: Added UUID versions 6, 7 and 8"; RFC 9562 §5.7 — the RFC that
obsoletes RFC 4122 and formally defines v7). The supported floor is **3.11** (`requires-python =
">=3.11"`), so `uuid.uuid7` is **absent on 3.11/3.12/3.13**. A hand-rolled fallback is needed.

### Native UUID column stores v7 cleanly
`UUID = Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')` (`data/model.py:75`). A v7
value is a canonical 36-char UUID string, valid input to the postgres native `uuid` type and
trivially stored as TEXT on sqlite. `as_uuid=False` keeps the Python side as `str` (matching
`Client.id`/`Task.id`). Per correction (1), `Task.source` should use this **shared `UUID` type** (it
now only ever holds a real `Source.id` v7 value, or NULL for historical rows) — the native uuid
ordering is exactly what feeds the planner's per-chunk min/max pruning. `Source.id` uses the same
`UUID` type as PK, mirroring `Client.id` (`data/model.py:766`).

### Reserved rows should also be v7 (uniform column) — and it's harmless
`<direct>`/`<stdin>` become real `Source` rows (PATH holds the sentinel) created **once, lazily**,
reused forever → their v7 ids carry an *ancient* timestamp (low-sorting). This does **not** hurt
pruning: a v7's timestamp lands in the *min* side of a chunk's `task.source` range, and de-dup
queries prune old chunks via `chunk_max < lineage_value`. A low min never raises max, so old chunks
are still pruned. Reserved sources are also gate-exempt, so they never appear in a lineage IN-list.
Keep the column uniform: give them v7 ids like any other source.

## RECOMMENDATION

Add a dedicated, always-v7 `uuid7()` to `core/uuid.py` (keep `uuid()` untouched). Prefer a library
impl when present, else a ~10-line stdlib hand-roll — never degrade to uuid4:

```python
import os, time
from uuid import UUID

try:                                   # 3.14+ stdlib, or the uuid7 extra
    from uuid_utils import uuid7 as _uuid7
except ImportError:
    try:
        from uuid import uuid7 as _uuid7        # CPython >= 3.14
    except ImportError:
        def _uuid7() -> UUID:                    # RFC 9562 §5.7 fallback (3.11–3.13)
            b = bytearray(os.urandom(16))
            b[0:6] = int(time.time() * 1000).to_bytes(6, 'big')  # 48-bit unix-ms
            b[6] = (b[6] & 0x0F) | 0x70                          # version 7
            b[8] = (b[8] & 0x3F) | 0x80                          # variant 10
            return UUID(bytes=bytes(b))

def uuid7() -> str:
    """Generate a time-ordered UUIDv7 (RFC 9562 §5.7)."""
    return str(_uuid7())
```

- Export it (`__all__ = ['uuid', 'uuid7']`).
- `Source.new(...)` (new entity in `data/model.py`) fills `id=uuid7()` (parallel to `Task.new`'s
  `id=uuid()`), and the lazy get-or-create of the `<direct>`/`<stdin>` rows uses the same `uuid7()`.
- `Task.source` column: change from the plan's TEXT to the shared `UUID` type
  (`mapped_column(UUID, nullable=True)`); `Source.id` PK is `UUID` too. The v7 values are valid
  RFC 9562 UUIDs and store natively on postgres / as TEXT on sqlite.
- No dependency change: the hand-roll is pure stdlib, honoring the EPEL/RHEL low-floor philosophy
  (`AGENTS.md` "md5/hashlib is stdlib (no new dep)"). The optional `uuid7` extra remains a
  performance nicety, not a correctness requirement.

## Open questions

- **Monotonicity within a millisecond:** the hand-roll uses fully random `rand_a`/`rand_b` (no
  intra-ms counter, unlike stdlib 3.14 / uuid_utils). Fine for chunk-pruning (ms granularity is
  enough) and de-dup correctness (ids only need to be unique + roughly time-sorted). Only relevant
  if strict per-ms ordering is ever required — it is not here.
- **Timestamp source:** `time.time()` (wall clock) matches v7 semantics; a backward clock step could
  produce a slightly out-of-order id but never a collision (62 random bits). Acceptable.
- Confirm the plan's `index_tasks_source` (`Task.source, Task.identity`) is still wanted now that
  `Task.source` is native UUID — it still serves the R7 count-by-source and de-dup lineage scan.
