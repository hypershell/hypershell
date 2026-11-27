# Research 04 — Zone data model (Z2 planning)

All line references are to `origin/develop` (`d996579`) unless marked otherwise. A prototype
(scratch copy of `origin/develop`, model patched with `Zone`, `task.zone` and one index, SQLite) was
run to check the upgrade step and query plans; results are quoted below.

## 1. #69's upgrade machinery

- **Declarative.** `upgrade_schema` (`data/upgrade.py:104-126`) diffs `Entity.metadata` against
  reflection (`_plan`, `:148-184`). A missing table becomes `CreateTable(if_not_exists=True)` plus
  all of its indexes (`:157-161`); a missing column becomes `ALTER TABLE … ADD COLUMN` (`:164-169`);
  a missing index becomes `CreateIndex(if_not_exists=True)` (`:176-183`). No per-column code: Z2
  declares the model and nothing else. New tables do **not** go through `create_all` (only
  `truncatedb` does, `data/__init__.py:80-88`).
- **When it runs.** `initdb()` → `upgradedb()` (`data/__init__.py:64-77`); `checkdb()` →
  `upgradedb()` (`:91-95`); `ensuredb()` picks one (`:98-109`). So: every SQLite open, `--initdb`,
  `hs initdb`, every PostgreSQL server start (`server.py:1370`), `hs submit` DB mode
  (`submit.py:1368-1376`), and every `hs task …` command (`task.py:102,192,363,430,696,1039`).
  Clients never open the DB.
- **Forward-only** on both dialects: it never drops/renames/retypes (`upgrade.py:7-10`), except the
  retired chain UNIQUEs on PostgreSQL. Concurrency: `BEGIN IMMEDIATE` on SQLite (`:270-308`),
  advisory xact lock on PostgreSQL (`:320-349`), re-plan inside the lock, one retry on a
  duplicate error (`:117-126`).
- **Constraints on an added column** (`_refusal`, `:195-215`): not PK, not UNIQUE, **no FK**, not
  computed/identity, nullable or a constant `server_default`. **An index added to an existing
  table must not be UNIQUE** (`:179-182`). A **new table** may carry anything (PK, UNIQUE, FK),
  since it is created whole.
- **Prototype result** (2.9.0a2-shape DB with 5 tasks): first run returned `['Created table zone',
  'Added column task.zone', 'Created index index_tasks_zone on task']`; second run `[]`; the 5
  pre-existing rows read `zone IS NULL`; `zone.name` UNIQUE was created with the table.

So Z2 needs: a `Zone` entity class, a nullable `zone` UUID column on `Task` with **no
`ForeignKey`**, and (optionally) a non-unique module-level `Index`. Nothing else, no manual step.

## 2. `Zone` table

Mirror `Source` (`model.py:993-1097`): `id: UUID` PK (the shared `UUID` type, `:94`, is `Text` on
SQLite and native `UUID(as_uuid=False)` on PostgreSQL), `name: TEXT NOT NULL UNIQUE` (legal: new
table), `created: DATETIME NOT NULL`, a `columns` dict, nested `NotFound`/`NotDistinct`/
`AlreadyExists`, `from_id`, `names_for_ids` (like `paths_for_ids`, `:1077-1082`). Do not put
`index=True` on `name` (a later reflection-diff would treat a missing UNIQUE *index* as refusable;
a table-level unique constraint is not diffed). The zone table is not a hypertable candidate, so
the TimescaleDB unique-index caveat (`model.py:294-298`) does not bite.

Reserved default zone: `DEFAULT_ZONE_ID: Final[str]` in the Source style (`:58-62`), e.g.
`00000000-0000-7000-8000-000000000010`, name `'default'` (`DEFAULT_ZONE_NAME`), reserved: `--zone
default` and no `--zone` both mean the default zone. **Readers must not depend on the row
existing** (map `None`/`DEFAULT_ZONE_ID` → `'default'` in code); write the row with the same
race-free insert (§4) at server start in DB mode so R7's "names stored in `Zone`" holds literally.

## 3. `task.zone`

`zone: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)` plus `'zone': str` in
`Task.columns` (`:312-346`). Keep it before `tag` (`tag` is "kept last", `:310`). Place it directly
after `fingerprint` (`:304`), with source/fingerprint (provenance/placement), not next to `part`.

**Storage of the default zone: NULL, canonically.** Pre-existing rows are NULL (R11, no rewrite);
`Task.new` normalizes `zone in (None, DEFAULT_ZONE_ID)` → `None`. Then:

- default zone predicate: `Task.zone.is_(None)`; named zone: `Task.zone == zone_id`;
- one helper `_filter_zone(query, zone)` with `zone=None` meaning *no filter* (today's behaviour,
  keeps every existing caller and test unchanged) and `DEFAULT_ZONE_ID` meaning `IS NULL`.

Why not store `DEFAULT_ZONE_ID` and filter `zone IS NULL OR zone = :default`: on SQLite the OR
form ignored the zone index and fell back to `index_tasks_unscheduled` (prototype `EXPLAIN QUERY
PLAN`), and it doubles every default-zone predicate forever for 2.8 rows. NULL-canonical is
index-friendly and needs no rewrite. The fixed id still exists for the wire, the client's declared
zone, staging-pool keys, and display.

**Index.** Add `index_tasks_zone = Index('index_tasks_zone', Task.zone, Task.group,
Task.schedule_time)` beside `index_tasks_unscheduled` (`:908`); the step creates it in place.
Prototype, 200k rows (75% default, 25% one named zone), draining the default zone 100 at a time
with `select_new`'s shape: insert 0.75 s → 0.83 s (+10%); drain 19.2 s → 13.1 s. Both default
(`IS NULL`) and named predicates SEARCH the new index. Note (pre-existing, not Z2's): `select_new`
`ORDER BY submit_time` sorts every matching unscheduled row per call (TEMP B-TREE), with or without
the index. Index on a homogeneous run: one extra index maintained per insert/schedule update — the
R1 benchmark must include it; if it costs >~2%, the index is deferrable (correctness does not
need it).

## 4. Get-or-create (R8)

**The Source race** (`issues/source-reserved-race.md`, on `origin/develop`): `Source.reserved`
(`model.py:1045-1057`) is check-then-act — `from_id` misses in two processes, both `add`, the second
hits the PK and the submit dies with `IntegrityError` (measured 1-2 in 20 runs of 6 concurrent
submits). Zone must have no window between the check and the insert.

**Recommendation: deterministic ids.** `zone_id(name) = DEFAULT_ZONE_ID if name in (None, '',
'default') else str(uuid5(ZONE_NAMESPACE, name))`, `ZONE_NAMESPACE` a fixed UUID literal that never
changes. Persist with one atomic statement: `insert(Zone).values(id, name, created)
.on_conflict_do_nothing()` from `sqlalchemy.dialects.sqlite` / `.postgresql` (dispatch on
`DATABASE_DIALECT`; both support it, SQLite ≥3.24), then `Session.commit()`; no SELECT needed. Two
processes resolving the same new name compute the same id, so whichever insert wins, both hold the
right id; there is no check-then-act. Fallback for any dialect without upsert: insert inside
`Session.begin_nested()` and swallow `IntegrityError` — still correct because the id is a function of
the name.

Why this over random-uuid7 + `ON CONFLICT (name) DO NOTHING` + `SELECT id WHERE name=`: that is
also race-free, but every resolver must touch the DB. Deterministic ids:

- let the **client** and the **request callable** carry/compare zone ids with no DB access (the
  callable runs on a manager thread in the server, Z1; keeping the DB off that path keeps the
  Scheduler the sole `Task.next` caller and avoids a scoped `Session` on a new thread);
- make **in-memory servers** (`--no-db`) and **`hs submit --queue`** zone-aware with no `Zone`
  table at all;
- make the process-local cache (deferrable) unnecessary: computing a uuid5 is the cache;
- allow per-line zone resolution later (an inline `zone:` tag) without a query per task.

R7 says "maps a minted UUID to the name": a uuid5 is minted by HyperShell and stored in `Zone`,
mapped to the name; record the choice in TECH.md as a deliberate reading so review doesn't grade it
as drift. Cost: names are case-/whitespace-exact (strip and reject empty at the CLI); a rename is
impossible (not a goal).

## 5. Zone filter in selection

- `select_new` (`:593-598`): add `zone=None` param → `_filter_zone`.
- `query_retryable` (`:601-611`): add `zone=None`; `select_failed` (`:614-616`) passes it through.
- `Task.next` (`:631-647`), `__next_eager` (`:650-657`), `__next_not_eager` (`:660-667`),
  `__schedule_next_failed_tasks` (`:670-688`): add `zone=None` and pass down. Do not touch the
  stamping loop (`:642-646`).
- **Stay global (R10):** `current_group` (`:515-542`), `increment_group` (`:545-590`),
  `count_retryable` (`:619-628`, calls `query_retryable` with no zone), `count_remaining`
  (`:691-696`), and the Scheduler's stop check (`server.py:219-228`). Because they are global, an
  empty `Task.next(zone=Z)` followed by `increment_group` is still correct: it holds the group
  while any zone has work in it.
- **Retry copy (R9) is explicit, not automatic:** the retry row is built by
  `cls.new(args, attempt, previous_id, tag, group, fingerprint, source)` (`:676-682`). Add
  `zone=task.zone` (reaches `Task(**other)`). Revert/cancel (`:721-777`, `:793-796`) never clear
  `zone`, so a reverted or orphaned task keeps its zone. (Observed, out of scope: the same
  constructor drops the parent's `cores`/`memory`/`timeout`, since `Task.new` pops them out of the
  stored tag, `:417-424`.)

## 6. Submit paths

- DB mode: `SubmitApp.run` (`submit.py:1323-1346`) resolves the zone once (after
  `check_database`, beside `prepare_source`), stores `self.zone_id`; pass `zone=` through
  `submit_from` (`:905`) → `SubmitThread`/`LoaderThread` (`:434`) → `Loader.__init__` (`:289`) →
  both `Task.new` sites (`:351`, `:382`), the way `cores`/`group` travel; plus `submit_one`
  (`:1363`). One resolve per submit; the `DatabaseCommitter` batch insert (`:517-524`) is
  unchanged.
- Queue mode: compute `zone_id(name)` locally (no DB), stamp it the same way; the server routes by it.
- `hs task submit` (`task.py:100-106`) and `hs task run` (`:428-436`): add `--zone`, resolve once.
- `hs cluster`'s embedded submitter (`server.py:825-831`) takes the same parameter.

## 7. Display (R12, deferrable)

Follow `source`: `print_normal`/`print_card` batch-resolve ids (`task.py:790-810`,
`Source.paths_for_ids`), `resolve_source` maps id→name with a raw-id fallback (`:1418-1435`),
`format_task_fields` sets `source`/`source_id` (`:1476-1477`), and the info template prints
`source: {source} ({source_id})` (`:1335`). Add `Zone.names_for_ids`, `resolve_zone` (None →
`'default'`), `data['zone']`/`data['zone_id']`, and a `zone:` line. `ALL_FIELDS = list(Task.columns)`
(`:440`) makes `zone` appear in `hs task search` table/JSON/CSV output automatically as a raw id
(additive; CSV gains a column). `hs task update` accepts any `Task.columns` field (`:1048`), so a
raw-id `zone=` update works and a name does not (filtering/update by name is a non-goal).

## 8. Overlap with `writeback-scoped-conditional`

Its GOAL (branch only, no TECH/PLAN yet) R1 enumerates the result columns; every other column is
server-held. `zone` must be **server-held** and appear in its TECH list and its "returned copy
differing in every non-result column" test. If that cycle implements an allowlist of result
columns (preferred), `zone` is excluded automatically; if a denylist, add `zone`. Ask that its test
iterate `Task.columns - RESULT_COLUMNS` rather than a literal list.

Shared touchpoints in `data/model.py`, and how Z2 keeps hunks apart:

| Function / block | Writeback (likely) | Z2 edit | Keep separate by |
|---|---|---|---|
| `Task` column block / `Task.columns` (`:259-346`) | maybe an assignment column, `RESULT_COLUMNS` | `zone` after `fingerprint` | ≥1 untouched line from `part`/`tag` and any new constant |
| `Entity.update_all` (`:194-199`) | conditional/scoped write | none | do not touch |
| `Task.next` (`:631-647`) | assignment stamp in the loop | signature + two call lines | never edit `:642-646` |
| `revert_all`/`revert_orphaned`/`cancel_all` (`:721-796`) | clear/refuse assignment | none | do not touch |
| `__schedule_next_failed_tasks` (`:670-688`) | possibly none | `zone=task.zone` on `:682` | one-line add |
| `server.py` `Receiver.update_tasks` (`:459-466`) | rewrite | none | Z2 touches `Scheduler` only |

## 9. Serialization

`Entity.to_json` emits every `columns` key (`model.py:128-134`), so `zone` joins the bundle dicts;
`from_dict` is `cls(**data)` (`:137-139`). A missing key is fine (None); an **unknown** key raises
`TypeError: 'zone' is an invalid keyword argument for Task` (prototype). Consequences: 2.8 clients
are disconnected anyway (R5); the returned copy's `zone` is ignored once writeback lands; but a new
`hs submit --queue` into an older in-memory server would break its clients. Recommend
`serialize_tasks` omit `zone` when it is None (default zone), so default-zone bundles keep the
2.8/2.9.0a2 shape; zoned bundles must carry it (queue-mode submit and in-memory routing need it).
Clients do not need `zone` (they asked for their zone), but carrying it is harmless and useful for logs.

## Recommendation

- **Schema:** new `Zone(id UUID PK, name TEXT NOT NULL UNIQUE, created DATETIME)`; `task.zone` UUID
  nullable, no FK, declared after `fingerprint`; `index_tasks_zone (zone, group, schedule_time)`,
  non-unique (benchmark under R1; deferrable).
- **Default zone:** `DEFAULT_ZONE_ID` Source-style literal, name `'default'` reserved; stored as
  NULL on `task`; readers map NULL → default; the row is upserted at server start, never required.
- **Get-or-create:** `zone_id = uuid5(ZONE_NAMESPACE, name)` + `INSERT … ON CONFLICT DO NOTHING`,
  commit; no read-then-insert. Cache unnecessary.
- **Predicate:** `_filter_zone(q, zone)`: None → no filter; default → `zone IS NULL`; else
  `zone = :id`. Applied in `select_new` and `query_retryable` only; gating stays global.
- **Retry:** add `zone=task.zone` to the retry constructor (`:676-682`); test it.
- **Upgrade:** declare only; no upgrade code. Test a 2.9.0a2-shape DB gains table, column and index
  and a second run is a no-op, on SQLite (PostgreSQL if CI has it).
- **Writeback overlap:** `zone` is server-held; Z2 touches neither `update_all`, the revert/cancel
  family, the `Task.next` stamping loop, nor `Receiver`.
- **Risks:** uuid5 vs R7's "minted" wording (record in TECH); index write cost vs R1's 95% bar;
  `TypeError` on the `zone` key for older receivers (mitigated by omit-when-None); a default-zone
  client starving a group whose remaining tasks are in a zone with no client (expected per R10;
  log it); PostgreSQL path untested in this research.
