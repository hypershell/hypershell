# 03 — Scale & indexing strategy (GOAL R17)

> **PARTIALLY SUPERSEDED (2026-07-11)** by maintainer corrections + [`09`](09-pruning-adversarial-check.md):
> `Task.source`/`Source.id` are **native `UUID`** (not TEXT); de-dup is a **single small-`IN` query**
> (strategies (c) batched anti-join / (d) temp-table and the 150 MB note are dropped); the pruning
> lever is **TimescaleDB compressed per-batch min/max** enabled by an orderable **UUIDv7** `source`
> (no BRIN, no TSDB-specific code). See [`00-digest.md`](00-digest.md).

**Question:** make source-detection, count-checks, and de-dup cheap even at billions–trillions of
`task` rows, without making a bare `hs submit <file>` materially slower than today.

## Existing index patterns (ground truth)

Indices are plain module-level `Index(...)` objects declared after the model, no FKs anywhere in the
schema:

- `index_tasks_unscheduled = Index('index_tasks_unscheduled', Task.group, Task.schedule_time)` — `data/model.py:759`
- `index_tasks_retries = Index('index_tasks_retries', Task.exit_status, Task.retried)` — `data/model.py:760`
- `index_client_disconnect` — `data/model.py:831`

They are created by `Entity.metadata.create_all(engine)` in `initdb()` (`data/__init__.py:63-68`) —
a new `Source` model + new `Task` columns are picked up automatically on a fresh `hs initdb` (the
GOAL's no-backfill non-goal). `previous_id`/`next_id` show the house style for "relationship without
FK": a bare `mapped_column(UUID, unique=True, nullable=True)` (`data/model.py:276-277`).

Dialect-variant types are pre-defined at `data/model.py:74-82`: `UUID` = `Text` on sqlite / native
`POSTGRES_UUID` on pg; `JSON` = `JSON_TEXT` / `JSONB`. Providers at `data/core.py:173-178`
(sqlite/turso/postgres). `schema` is configurable (`data/core.py:189`).

## Key structural insight — detection touches `Source`, not `Task`

The gate's existence check (R5/R6/R11) is a lookup on the **new `Source` table**, whose cardinality
is *one row per named-file ingestion* — thousands, maybe low millions at a huge site, i.e. many
orders of magnitude smaller than a trillion-row `task`. So the expensive predicate never scans
`task`. That is the whole R17 argument.

Bare `hs submit <file>` today already touches `task`: `check_database()` then
`Task.current_group()` (`submit.py:1077-1080`), an indexed order-by on `schedule_time`
(`data/model.py:426-431`). The new work adds only: (1) one B-tree descent on a `Source` index
(~2–4 page reads over a tiny table), and (2) the R7 count, which is an **index range aggregate
bounded by one source's rows** (one file's worth), never the whole table. Net: no new
linear-in-total-tasks cost; the added latency is dominated by reading+md5'ing the file (R4), not the DB.

## Column typing that constrains the indices

- **Reserved sources `<direct>`/`<stdin>` (R3) are sentinel strings, not UUIDs.** If `Task.source`
  used the `UUID` type it would reject `'<direct>'` on postgres (native `uuid` cast fails). Declare
  **`Task.source` as `TEXT`** (holds either a uuid-string or a sentinel, mirroring the `'<auto>'`
  sentinel idiom) and **`Source.id` as `TEXT`** too, so joins/`IN` don't need a text↔uuid cast on pg.
- **`identity` is a precomputed scalar hex string (md5/sha), declared `TEXT`** — *not* JSONB. The
  tags that feed identity are JSON/JSONB, but the canonical order-independent hash is computed in
  Python at submit time and stored as a plain scalar. This is what keeps indexing dialect-uniform
  and B-tree-friendly: **no JSONB GIN / expression index is needed**, and no per-query JSON parsing.
- New tasks always have non-null `source` and `identity` (reserved sources included), so the index
  has no NULL-sparsity problem; a partial index for NULLs is unnecessary.

## Recommended indices

```python
# On the new Source table — serves R5 (path+fingerprint exact) AND R6 (path-seen? via leading col)
index_source_lookup = Index('index_source_lookup', Source.path, Source.fingerprint)

# On Task — serves R7 (count-by-source: leading column) AND R9/R12/R14 de-dup (seek lineage, filter identity)
index_tasks_source = Index('index_tasks_source', Task.source, Task.identity)
```

One composite per table. `(path, fingerprint)` covers both path-only prefix scans and exact matches.
`(source, identity)` is the workhorse: leading `source` gives R7's `COUNT(*) WHERE source=:uuid`
directly, and restricts de-dup to one lineage's rows before filtering `identity` (index-only scan on
pg).

*Optional shrink:* a partial index `postgresql_where=Task.source.notin_(('<direct>','<stdin>'))`
(sqlite supports the same via `sqlite_where`) keeps the two high-volume reserved-source ranges out of
the index — those values are never queried (R3 exemption), so excluding them is free. Nice-to-have,
not required.

## De-dup strategy (R9/R12/R14) at scale

Lineage = all prior sources at the same abs path: `SELECT id FROM source WHERE path=:abspath` (uses
`index_source_lookup`, returns a handful of uuids). Then find which of the file's freshly-computed
identities already exist under those sources. The file's full identity set is already in memory
because R4 reads the whole file upfront to fingerprint+count it.

Strategy comparison:

- **(b) per-task existence query — rejected.** Millions of round-trips.
- **(a) load lineage identity set into a Python `set`, diff in memory.** One indexed query
  (`WHERE source IN (lineage)` on `index_tasks_source`), simplest. Memory ≈ *cumulative* lineage
  tasks (grows with every `--update` generation): ~150 MB per 1M identities (32-char md5 + set
  overhead), ~1.5 GB per 10M. Fine for a few file-generations; unbounded across many.
- **(c) batched anti-join driven by the file's own identities — RECOMMEND as default.**
  For each batch of the file's identities: `SELECT identity FROM task WHERE source IN (lineage) AND
  identity IN (:batch)`. Returns only the intersection, so memory is bounded by the file (already
  required) plus one batch — independent of how large the lineage grew. Batch size ~**900** to stay
  under sqlite's `SQLITE_MAX_VARIABLE_NUMBER` (999 on old builds; 32766 modern); postgres tolerates
  much larger but ~1–10k keeps planning cheap. Each query is an index range on `(source, identity)`.
- **(d) temp-table + indexed join — fallback for pathologically huge files** (tens of millions of
  lines): bulk-insert the file's identities into a temp table, `JOIN` on `task`. One bulk load + one
  join beats thousands of `IN` batches. Dialect-specific temp-table DDL and must run on the same
  `scoped_session` connection; only reach for it past a size threshold.

**Recommendation:** default **(c)** (bounded memory, no version-cap, dialect-uniform); optionally use
**(a)** as a fast path when the lineage is small; document **(d)** as the escape hatch for extreme
files. All three ride the single `index_tasks_source`.

## Caveats / open questions

- **TimescaleDB hypertable:** if `task` is partitioned by time (e.g. `submit_time`), a de-dup query
  with no time predicate (`WHERE source IN (lineage)`) cannot use chunk exclusion and touches every
  chunk's `(source, identity)` index. Each chunk scan is still bounded to matching rows, but chunk
  count itself scales with history. Out of scope to fully solve; a future mitigation is storing a
  submit-time window per source and adding a time bound. Flag for the planner.
- **R7 semantics:** the recorded count lives in `Source` (R1), but R7 compares it to the *actual* DB
  landed count, so an index-backed `COUNT(*) WHERE source=:uuid` is genuinely needed (can't just read
  the stored number). Cheap via `index_tasks_source` leading column.
- Confirm the identity hash width (md5=32 vs sha256=64 hex chars) — affects index size at trillion
  scale but not the strategy; md5 is sufficient for non-adversarial content identity and matches the
  fingerprint example in R1.
</content>
</invoke>
