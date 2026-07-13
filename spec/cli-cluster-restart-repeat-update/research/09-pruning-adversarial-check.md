# 09 — Adversarial check: UUIDv7 pruning + native-UUID schema + reserved-source rows

Skeptical validation of the two maintainer corrections: (1) reserved `<direct>`/`<stdin>` become
**real Source rows** and `Task.source` becomes a **native UUID** column; (2) **no TimescaleDB-specific
code** — rely on a UUIDv7 `Source.id` so per-chunk/column min/max lets the planner prune the de-dup
query. Verdict up front: **corrections 1 and 2 are the right shape, but the pruning argument as stated
is only partially realizable and rests on preconditions the current code does not guarantee.** Native
UUID is correct; the single small-IN de-dup is correct; the "skip most of the hypertable" claim needs
softening and one dependency fix.

## 1. Does native UUIDv7 + min/max stats prune `WHERE source IN (:lineage)` with no time predicate?

Three candidate mechanisms; only one is clean-PG *and* UUID-capable:

- **TimescaleDB native chunk exclusion** works only on the **partitioning (time) dimension** via each
  chunk's CHECK constraint, and only when the query carries a predicate on that dimension. The de-dup
  query has **no time predicate**, so native chunk exclusion never fires on `source`. (Mechanism does
  not apply.)
- **`enable_chunk_skipping()`** (TimescaleDB per-chunk min/max range stats for a *non*-partitioning
  column) is exactly the mechanism the maintainer describes — **but it does NOT support `uuid`**. Per
  the API docs it tracks min/max only for `smallint, int, bigint, serial, bigserial, date, timestamp,
  timestamptz`; it applies **only to compressed/columnstore chunks**; and it is TimescaleDB-specific
  DDL (forbidden here). So it **cannot** be the mechanism for a UUID `source`. (Verified: TigerData/
  Timescale `enable_chunk_skipping` API reference, fetched 2026-07-11.)
- **PostgreSQL BRIN** *does* have core operator classes for uuid — `uuid_minmax_ops`,
  `uuid_minmax_multi_ops`, `uuid_bloom_ops` (verified: PG16 `brin-builtin-opclasses`, Table 71.1).
  A BRIN `minmax`/`minmax_multi` index on `source` gives per-block-range min/max pruning of
  `source = X` / `IN (...)` on a plain table **and on each TimescaleDB chunk** (a chunk is an ordinary
  table). This is the only clean-PostgreSQL realization of the maintainer's min/max lever for a UUID
  column, and it fires **without a time predicate**. TimescaleDB compressed chunks *also* keep implicit
  per-batch (segment) min/max on ordered columns — same idea, config-only, no code.

**What must be true for the lever to work:** (a) an actual min/max structure exists (BRIN index, or
compressed per-batch stats) — a **plain B-tree does not prune by min/max**; and (b) physical/insert
order is **correlated** with `Source.id` order, i.e. `Source.id` is genuinely time-ordered (UUIDv7)
and tasks are appended in submission order. Under those two conditions the maintainer's "a source from
this week sorts into recent heap ranges → old ranges have `max < X` → skipped" argument is **sound**.

**Precondition gap (critical).** `core/uuid.py:8-11` uses `uuid_utils.uuid7` *only if importable*, else
falls back to **`uuid.uuid4`** (random) — it does **not** even try stdlib `uuid.uuid7` (added in Python
**3.14**; verified py3.14 `uuid` docs). And `uuid-utils` is **not** a runtime dependency — it lives only
in the optional `uuid7` extra and the dev group (`pyproject.toml:59,67`; runtime `dependencies`
`:30-38` omit it). So a default `pip install hypershell` on Python 3.11–3.13 mints **UUIDv4**, and the
entire pruning premise silently degrades to random ids (no correlation → no min/max pruning). The
de-dup query stays **correct** (a B-tree/BRIN still seeks by value) — only the optimization is lost.

## 2. Reserved-source min-pinning concern — evaluated

The concern is **real, but only under a min/max mechanism (BRIN or compressed per-batch stats), and
only for the rare "query an ancient source" case.** Pruning `source = X` skips a range iff `X < min`
OR `X > max`:

- Ranges **older** than X (`max < X`): pruned via `X > max`. Reserved rows lower `min`, never `max`, so
  **old ranges stay pruned regardless.** No harm.
- Ranges **newer** than X (`min > X`): pruned via `X < min`. A range containing any `<direct>`/`<stdin>`
  task has `min` pinned to the ancient reserved id `<< X`, so `X < min` is false → **newer range NOT
  pruned.** This is the maintainer's concern, and it is **correct.**

**When it bites:** querying an **old** source in a DB with much newer data — many newer chunks each
carry a reserved-source task → most aren't pruned → near-full scan. **When harmless:** the actual
use case (`--restart`/`--update`/requeue) queries a **recent** source, so there are **few chunks newer
than X** — the un-pruned set is tiny. Note the reserved rows are **not the only** min-polluter: retries
(new rows with an *old* `source`, `model.py:555`) and interleaved concurrent submissions pollute the
same way. So this is an inherent property of correlation pruning, not a reserved-row-specific bug.

**Mitigation decision: accept + one cheap object-model change; do NOT add TSDB code.** Recommend the
de-dup index be **partial, excluding the two reserved sources** — reserved tasks are de-dup-exempt
(never queried) and high-volume, so excluding them shrinks the index *and* removes their min-pollution
if BRIN is ever used. **But** a static partial predicate can only name the reserved ids if they are
**deterministic constants** — random `uuid7()` reserved ids are unknown at class-definition time.
Therefore: **give the two reserved Source rows fixed, well-known UUID constants** (e.g. hardcoded
`0000…0001`/`0000…0002` or `uuid5` in a hypershell namespace) rather than random uuid7. This also makes
lazy get-or-create idempotent by primary key (`Source.from_id(DIRECT_ID)` else create) with no path
lookup, and takes reserved rows out of the min/max discussion entirely. If BRIN is adopted, prefer
`uuid_minmax_multi_ops` (multi-range — designed to tolerate a handful of outlier values). Otherwise
document an operator note: de-dup is optimized for re-submitting *recent* files; de-dup against an
ancient source is a rare full-history scan.

## 3. Schema / strategy confirmations

- **`Task.source` = native `UUID` (the shared `UUID` type at `model.py:75`), NOT TEXT — CONFIRMED.**
  Now that reserved sources are real rows with real UUIDs, no sentinel string is ever stored, so
  `POSTGRES_UUID(as_uuid=False)` is correct. Nullable for historical rows. `Source.id` likewise `UUID`
  (uuid7 for real files, fixed const for reserved). The shared `uuid()` returns `str`, matching the
  `as_uuid=False` (string-valued) convention of every existing UUID column (`model.py:240,244,254,…`).
  A python `list[str]` binds cleanly into `source IN (...)` on both sqlite (TEXT) and pg (native) — no
  text↔uuid cast issue (this retires brief-03's cast worry).
- **`index_tasks_source (source, identity)` — still right** as the primary de-dup/R7 index (B-tree).
  It already makes de-dup cost scale with **lineage matches + chunk count, not total rows** (one value
  seek per chunk) → **R17 is satisfied by the B-tree alone**, independent of uuid7. UUIDv7 adds
  right-edge insert locality (less page-split/WAL) and *enables* the optional BRIN/compressed pruning
  lever; it does not change the B-tree plan shape. Make this index **partial excluding the reserved
  UUIDs** (now possible with fixed consts).
- **De-dup = single small-IN query (strategy a) — CONFIRMED; drop batching/temp-table.** Lineage =
  the small set of Source rows sharing a path (prior submissions of one file, typically < 100), so the
  IN-list is small; `SELECT identity FROM task WHERE source IN (:lineage)` returns one file-history's
  identities, held in memory. Brief-03 strategies (c) batched anti-join / (d) temp-table and the
  "~150 MB/1M ids, grows every generation" hardening are **no longer needed** — remove them.

## 4. Stale items to edit (corrections 1 + 2)

**PLAN.md:** §2 `Task.source` TEXT→`UUID` native; drop the "sentinel/TEXT" rationale (`:29-31`).
`Source` + reserved: real rows via lazy get-or-create with **fixed const UUIDs** holding
`<direct>`/`<stdin>` in `path` (`:23-27`); R3 map row (`:100`) reserved-row not string-sentinel.
Deviation table: **delete** the "Task.source typed TEXT not UUID" row (`:146`) — it is reversed; drop
the "(c)/(d) deferred" deviation (`:147`). Rabbit hole "Sentinel sources on postgres → must be TEXT"
(`:159`) reversed. Risks (`:171-176`): rewrite TSDB bullet per §1/§2 here (enable_chunk_skipping ≠ uuid;
BRIN/compressed as the real lever; reserved-source pollution accepted+documented) and reframe de-dup
memory (lineage-bounded, no c/d). **Add** a presentation-layer element: `hs list/info/search` must
resolve `Task.source` UUID → `Source.path` (abs/rel) or the reserved sentinel (`task.py`
`ALL_FIELDS`/field rendering `:169,:206-209,:434,:541-548`).

**00-digest.md:** Schema section (`:39-44`) — native UUID; reserved = real rows w/ fixed uuids; gating
tests membership in the two reserved ids (not `^<.*>$` on a string). De-dup (`:59-65`) — single small-IN,
delete c/d + 150 MB note. Residual risks (`:124-130`) — rewrite TSDB + memory bullets; add
presentation-resolution decision.

**02-source-schema.md:** reverse the whole "must be TEXT" decision (`:41-50`,`:80`) and reserved-source
representation (`:87-89`) to native UUID + real rows; `Source.id` uuid7/reserved-const note.

**03-scale-indexing.md:** reverse "declare `Task.source`/`Source.id` as TEXT" (`:41-44`) to native UUID;
partial-index predicate (`:66`) must use the fixed reserved UUIDs, not strings; rewrite the de-dup
recommendation (`:78-98`) to lock strategy (a) single small-IN (drop the "RECOMMEND (c)"); rewrite the
TSDB caveat (`:100-106`) per §1 (enable_chunk_skipping/uuid limits + BRIN uuid_minmax_multi_ops).

**Also flag (new):** the uuid7 precondition. Either (a) improve `core/uuid.py` fallback to prefer
stdlib `uuid.uuid7` on py3.14 before uuid4, and/or (b) promote `uuid-utils` from the `uuid7` extra to a
runtime dep (weigh against EPEL floor policy — uuid-utils is a Rust/maturin wheel, unlikely in EPEL).
Until then, treat time-ordering (hence min/max pruning) as **best-effort optimization**, never a
correctness requirement — the B-tree de-dup is correct under uuid4.

## Open questions

1. Adopt an explicit **BRIN `uuid_minmax_multi_ops`** index on `source` to make the pruning lever real
   in plain PostgreSQL, or lean solely on TimescaleDB's implicit compressed per-batch min/max? (BRIN is
   standard PG, not TSDB code, but adds an index + correlation dependency.)
2. Reserved UUIDs: hardcoded constants vs `uuid5(namespace, '<direct>')`? Either is deterministic; both
   enable the partial index.
3. uuid7 dependency decision (extra vs runtime vs stdlib-preferring fallback) — owner: packaging.
