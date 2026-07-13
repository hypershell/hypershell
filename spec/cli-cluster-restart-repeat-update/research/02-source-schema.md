# Brief 02 — SOURCE entity + Task association schema (R1, R3)

> **PARTIALLY SUPERSEDED (2026-07-11)** by maintainer corrections + [`07`](07-uuidv7-source-ids.md)
> / [`09`](09-pruning-adversarial-check.md): `Task.source` is a **native `UUID`** (not TEXT); reserved
> `<direct>`/`<stdin>` are **real `Source` rows with fixed constant UUIDs** (not string sentinels in
> `Task.source`); `Source.id` is **UUIDv7**. See [`00-digest.md`](00-digest.md) for the current schema.

Scope: model a `Source` entity and add `source`/`identity` columns to `Task`, following the
existing conventions in `data/model.py`, and confirm `create_all` picks them up on a fresh
`initdb` without altering an existing task table (aligns with the no-backfill non-goal). Grounded
in the current source; every anchor is a real file:line.

## Conventions to follow (verified)

- **Entity base** `data/model.py:85`. Declarative; `__tablename__` = lowercased class name
  (`:91`) → a `Source` class yields table `source`. `__table_args__` injects `{'schema': schema}`
  (`:96`). (`source` is a non-reserved word in both SQLite and PostgreSQL; no quoting needed,
  unlike `group` which is quoted at `:241`.)
- **Pre-defined column types** `data/model.py:74-82`: `UUID` (`Text().with_variant(POSTGRES_UUID(as_uuid=False),'postgresql')`),
  `TEXT`, `INTEGER`, `SMALL_INTEGER`, `FLOAT`, `DATETIME` (=`DateTime(timezone=True)`), `BOOLEAN`,
  `JSON` (JSON_TEXT with JSONB postgres variant). Reuse these — do not hand-roll types.
- **Plain-UUID columns, NOT ForeignKeys** — confirmed: `Task.server_id`/`client_id`/`previous_id`/`next_id`
  are all `mapped_column(UUID, ...)` with no `ForeignKey` (`:254,:258,:276,:277`); `Client.server_id`
  is the same (`:769`). Follow this: `source` is a bare uuid-bearing column, no relationship/FK.
- **Second-entity example** `Client` (`:763-828`): id (UUID pk) + typed columns + a `columns`
  dict (`:776`) + inner `NotFound/NotDistinct/AlreadyExists` + `from_id`/`new` classmethods +
  a module-level `Index` (`:831`). Mirror this shape for `Source`.
- **The `columns` dict is load-bearing wire plumbing** (`:281-312`). `Entity.to_tuple/to_dict/
  to_json` iterate it (`:105-115`); `from_json`→`from_dict`→`cls(**data)` reconstructs from it
  (`:118-125`). `serialize_tasks`/`deserialize_tasks` (`:218-226`) are just `to_json`/`from_json`
  per task. **Any new Task column MUST be added to `Task.columns` or it will not cross the wire**
  and won't round-trip on the remote queue. `Source` needs its own `columns` dict too.

## `create_all` on fresh vs existing DB (verified)

- `initdb()` = `Entity.metadata.create_all(engine)` (`data/__init__.py:65`); `checkdb` only tests
  for the `task` table (`:84`). SQLite auto-inits on submit (`submit.py:1111-1112`).
- SQLAlchemy `create_all` issues `CREATE TABLE IF NOT EXISTS` per table and **never ALTERs an
  existing table**. So on a **fresh** init the new `source` table + new `task` columns are created;
  on an **existing** DB, a new `source` table is created but the existing `task` table is left
  untouched (old rows keep NULL `source`/`identity`). This matches the "require fresh `hs initdb`,
  no migration/backfill" non-goal exactly — no code change needed to get that behavior.
- Dialect variants are already handled by the pre-defined types (postgres UUID/JSONB vs sqlite
  TEXT/JSON); reusing `UUID`/`DATETIME`/etc. inherits them for free.

## Key decision: `Task.source` must be TEXT, not UUID

Reserved sources `<direct>` / `<stdin>` are best represented as **sentinel strings** stored
directly in `Task.source` with **no `Source` row** (mirrors the existing `<auto>`/`<none>`
sentinel idiom and the `<stdin>`/`<direct>` naming already used at `submit.py:437-442` and
`client.py`-level source naming). **But** the shared `UUID` type is `POSTGRES_UUID(as_uuid=False)`
on postgres (`model.py:75`) — inserting `'<direct>'` into a postgres uuid column raises. Therefore
`Task.source` should be typed **`TEXT`**, not `UUID`, so it can hold either a 32-hex uuid or a
`<...>` sentinel. (The sqlite variant is TEXT either way, so this only bites on postgres — but it
bites hard.) `Source.id` itself stays `UUID` (only ever real uuids).

## Retry-propagation gotcha (flag for planner / topic 01)

Retry rows are minted by `Task.new(args=…, attempt+1, previous_id=…, tag=…, group=…)` inside
`__schedule_next_failed_tasks` (`model.py:555-560`) — it does **not** pass `source`. `Task.new`
(`:333-361`) forwards `**other` to the `Task(...)` ctor (`:359`), so `source` will be `NULL` on
retries unless explicitly propagated. `identity` (topic 01) is recomputed from args+group+tags and
would come out identical, but **`source` must be copied from the parent row** in that retry path,
or retried tasks lose their source association (breaking R3 for retries). Loader ingest
(`submit.py:167,195`) is the other `Task.new` call site that must pass the fresh `source`.

## RECOMMENDATION

### `Source` entity (new, after `Task`, before/after `Client`)
| column | type | null | notes |
|--------|------|------|-------|
| `id` | `UUID` | no (pk) | `uuid()` default via `new()` |
| `path` | `TEXT` | no | absolute file path (R1) |
| `fingerprint` | `TEXT` | no | content md5 hex (R1) |
| `task_count` | `INTEGER` | no | ingested count (R1) |
| `created` | `DATETIME` | no | creation timestamp (R1); set in `new()` like `Task.submit_time` |
| `previous_id` | `UUID` | yes | lineage pointer to the prior same-path source superseded by `--update` (R14). Plain uuid, **not** FK, **not** unique (a path may be updated repeatedly). |

Plus: `columns` dict (all six), inner `NotFound/NotDistinct/AlreadyExists`, `from_id` and a
`new(...)` classmethod that fills `id=uuid()` and `created=datetime.now().astimezone()`.

### `Task` new columns (append to model + `columns` dict at `:281`)
| column | type | null | notes |
|--------|------|------|-------|
| `source` | **`TEXT`** | yes | holds a `Source.id` uuid **or** the sentinel strings `<direct>`/`<stdin>`; NULL only for pre-existing/historical rows. TEXT (not UUID) so postgres accepts sentinels. |
| `identity` | `TEXT` | yes | stable identity fingerprint from topic 01 (hex digest); NULL for historical rows. |

Add `'source': str` and `'identity': str` to `Task.columns` (drives wire round-trip + makes them
appear in `hs list/search/update`/`info`, which read `Task.columns` — `task.py:434,548,944,1225`,
additive/no breakage).

### Reserved-source representation
Sentinel STRINGS in `Task.source` (`<direct>`, `<stdin>`), **no `Source` row**. Gating logic
(topics 04+) treats a `Task.source` matching `^<.*>$` (or an explicit set) as exempt (R3/R7).

### Index list (hand to topic 03, R17)
- `Index('index_source_path', Source.path)` — source lookup by path (+ consider composite
  `(path, fingerprint)` for the R5/R12 same-path/same-fingerprint check).
- `Index('index_tasks_identity', Task.identity)` — de-dup "identity already present" (R9/R12/R14).
- Consider composite `Index('index_tasks_source_identity', Task.source, Task.identity)` if de-dup
  is scoped per source/prior-source set; topic 03 to decide single vs composite against the exact
  de-dup query shape.

### Wire/plumbing checklist (must-update)
- `Task.columns` (`model.py:281`) += `source`, `identity` — required for `serialize_tasks`/
  `deserialize_tasks` round-trip (`:218-226`) and `server.py:436` `to_dict()` writeback.
- `Source.columns` dict on the new entity.
- Both `Task.new` call sites must pass `source`: loader ingest (`submit.py:167,195`) and the retry
  minter (`model.py:555`, propagate parent's `source`).

## Open questions
1. Topic 01 owns the exact `identity` digest algorithm/length; this brief assumes a hex TEXT
   column and does not fix the width.
2. Should `Source.previous_id` be UNIQUE (task-style chain) or free (non-unique)? Recommend
   non-unique unless the planner wants a strict single-successor lineage invariant.
3. De-dup scope for the identity index: global-per-DB vs per-source — decides single vs composite
   index (R17). Depends on topics 04/05 gating semantics (R9 "prior submission of the same file").
