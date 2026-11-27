# Research 03 — Schema creation, submit/ingest path, config, and the forward-only boundary

**Scope:** how a fresh DB gets its schema, how `part` enters on submit today, what config knobs
touch partitioning, and precisely what breaks when new (`part`-column) code meets an old
(`part`-in-tag) SQLite DB. Read-only; no migration is designed here (out of scope per GOAL).

## Summary

Adding a `Column` to the `Task` model is **all that is required** for a fresh DB to get the
`part` column — `initdb()` just calls `Entity.metadata.create_all(engine)`, which reflects the
model. `part` does **not** enter through `submit.py` at all: the submit/loader path only carries
user `tag`/`cores`/`memory`/`group`; the `{'part': 0}` injection happens solely inside
`Task.new` (`data/model.py:387`). There are **no config knobs** for `part`/DB-partitioning
(the `rotate` key in config is for *log* files). Forward-only means: run new code against an old
SQLite DB and the first `INSERT`/`SELECT` touching `part` raises `OperationalError` — and the
auto-union will break with a column-count mismatch when a new-schema `main` is unioned with
old-schema partition files.

## Schema creation / `InitDBApp` (`data/core.py`, `data/__init__.py`)

Engine + session are built **at import** in `data/core.py:266-269`:

```python
engine = get_engine()
factory = sessionmaker(bind=engine)
Session = scoped_session(factory)
```

SQLite `check_same_thread=False` is injected at `data/core.py:213-214`:

```python
if 'check_same_thread' not in connect_args:
    connect_args['check_same_thread'] = False
```

Table creation is entirely reflective — `data/__init__.py:63-65`:

```python
def initdb(optimize: bool = False) -> None:
    """Initialize database tables."""
    Entity.metadata.create_all(engine)
```

`truncatedb()` (`:76-78`) does `drop_all` then `create_all`. **Adding a `Column` to `Task`
appears automatically in any fresh DB with no extra work** — `create_all` emits `CREATE TABLE`
from the declarative metadata. Crucially, `create_all` defaults to `checkfirst=True`: it creates
only *absent* tables and **never `ALTER`s an existing table to add a column**. So re-running
`hs initdb` on a DB that already has a `task` table is a no-op for that table — this is the
mechanical root of "forward-only." `checkdb()` (`:82-85`) only asserts the `task` table exists,
not its columns. `InitDBApp.run` (`:246-252`) auto-inits for SQLite and prompts otherwise.

Column type note (R3): the model already uses dialect-neutral `.with_variant` types (e.g.
`SMALL_INTEGER`, `data/model.py:97`); a `part` column should follow that pattern, e.g.
`mapped_column(SMALL_INTEGER, nullable=False, default=0)`, matching `group` at
`data/model.py:260`.

## Submit / ingest path — `part` enters only in `Task.new`

The loader builds every task via `Task.new` (`submit.py:351-353` for line tasks, `382-384` for
JSON records) passing only `args/raw_args/source/cores/memory/timeout/group/tag`. `SubmitApp`
single-task path is the same (`submit.py:1363-1364`). **No submit-path code writes `part`.**

`part` is injected inside the model, `data/model.py:387`:

```python
tag = {**(tag or {}), **inline_tags, **{'part': 0, }}
```

and then excluded from the fingerprint, `data/model.py:423`:

```python
'tags': {key: value for key, value in (tags or {}).items() if key != 'part'}},
```

**Can `part` be user-supplied via `-t/--tag`?** In DB mode tags are parsed by
`Task.ensure_valid_tag` (`submit.py:1378-1384`) with no `part` reservation — a user *could* pass
`-t part:5`, but line 387's `**{'part': 0}` unconditionally overwrites it, so it is always
clobbered to `0`. After this change (R2/R5) `part` must move to the column and the `{'part': 0}`
merge is removed; a stray user `part:` tag would then survive in `tag` unless separately guarded
(the GOAL keeps `part` internal — worth flagging in TECH whether to strip/reject a user `part`
key). Inline `# HYPERSHELL:` tags are parsed by `Task.split_argline` (`data/model.py:428-444`)
and merged the same way. Per AGENTS.md, **submit queue-mode silently ignores `-t/--tag`** (group
stays `None`); tag/`part` resolution happens entirely in `Task.new`, confirming the column change
lives in the model, not the submit plumbing.

## Config knobs

**None for `part` / DB partitioning.** `default['database']` is just `{'provider': 'sqlite'}`
(`core/config.py:91-93`). The only `rotate` knob is for **logging** file rotation
(`core/config.py:109`: `'rotate': 'never'`), unrelated to `rotatedb()`. DB rotation is a manual
action (`hs initdb --rotate`, `data/__init__.py:239-242`) with no size/threshold config — so no
config defaults need touching for this feature.

## Forward-only failure-mode analysis (informs the non-goal)

Given an **old** SQLite DB whose `task` table has no `part` column, running new code:

1. **`hs initdb` does not fix it.** `create_all(checkfirst=True)` leaves the existing `task`
   table untouched — no column is added. (`data/__init__.py:65`.)
2. **INSERT fails.** `Task.new` will populate the new `part` column, so `Task.add`/`add_all`
   (`data/model.py:173-191`) emits `INSERT INTO task (..., part, ...) VALUES(...)` →
   SQLite `OperationalError: table task has no column named part`. First submit fails.
3. **SELECT fails.** Any query referencing the column — the rewritten partition/rotation logic
   (currently `Task.tag['part']` at `data/__init__.py:147,157`; after R4 a `Task.part` filter),
   or a `SELECT *`/ORM load that maps `part` — raises `OperationalError: no such column:
   task.part` at query time.

This is exactly "may require re-initialization": the DB must be recreated (`initdb` on a fresh
file, or `--truncate`) to gain the column. No auto-add is in scope.

### Auto-union column-count mismatch (the subtle one)

`auto_union_sqlite()` (`task.py:839-860`) attaches every `main.N` partition file and builds a
temp view:

```python
Session.execute(text(f'attach database \'{path}\' as \'part_{i+1}\''))
...
SQLITE_UNION_PART + '\n'.join([f'union all\nselect * from part_{i+1}.task' ...])
```

with `SQLITE_UNION_PART = "create temp view 'task' as \nselect * from main.task\n"`
(`task.py:864-867`). This is `SELECT * FROM main.task UNION ALL SELECT * FROM part_i.task`.
**`UNION ALL` requires identical column counts.** If `main` is re-initialized to the new schema
(has `part`) but pre-existing partition files were produced by `--rotate` under the old schema
(no `part` column), the branches differ by one column →
`OperationalError: SELECTs to the left and right of UNION ALL do not have the same number of
result columns`, and `hs list`/`hs search`/`hs info` (which call `auto_union_sqlite`,
`task.py:191-192,684-685`) break until `--ignore-partitions` is passed. `rotatedb`
(`data/__init__.py:124-160`) clones `main` via `VACUUM INTO`, so *new* partitions inherit the new
schema and are consistent — the mismatch is strictly a legacy-partition-file problem. This is the
concrete edge that makes the forward-only boundary "old partition files may need re-rotation or
`--ignore-partitions`," and it is why `SELECT *` here is fragile to column changes.

## Tests affected (fixtures constructing tasks/DBs)

- `tests/test_source.py:100-134` — `test_fresh_schema_creates_source_table_columns_and_indices`
  asserts `insp.get_columns('task')`; a new `part` column changes the column set (currently only
  checks `{'source','fingerprint'} <= task_columns`, so likely still passes, but a new R7 test
  should assert `part` is present). `:64-66` asserts `part` is fingerprint-neutral (must stay
  green). `:83-86` builds the expected fingerprint with `{'part': 0}` — **will need updating**
  once `part` leaves the tag dict (the fingerprint payload no longer contains `part`).
- `tests/test_initdb.py:67-83` — `test_rotate` (integration) exercises `initdb`→`submit`→rotate;
  covers the partition path end-to-end and must stay green after R4 rewrites the queries.
- `tests/test_submit.py:230-238`, `tests/test_client.py:32-40` — construct via `Task.new`;
  benign but exercise the injection site.
- `tests/__init__.py:59-76` (`create_taskfile_echo`, etc.) and `conftest.py:49 temp_site` are the
  standard fixtures; no `part` assumptions.

## Open questions for TECH

1. With `{'part': 0}` removed, should a user-supplied `-t part:...` / inline `part:` tag be
   stripped or rejected to keep `part` internal (R2/R5)? Today it is silently clobbered.
2. Default form: `SMALL_INTEGER, nullable=False, default=0` (mirroring `group`) vs. nullable —
   `default=0` best preserves current semantics and keeps `part` a non-null bookkeeping value.
3. Confirm whether any `to_json`/serialization consumer relies on `part` living in `tag` (the
   remote-queue task JSON round-trips `columns`, `data/model.py:303-336`; adding `part` to
   `columns` puts it in the wire payload — verify server/client tolerate the extra key).
