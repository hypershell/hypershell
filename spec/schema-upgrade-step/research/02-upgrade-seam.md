# 02 — Where the upgrade runs

## Recommendation

The `initdb()` + `checkdb()` seam is **sufficient for the CLI commands in R1, with three holes to plug**.

1. Add one function, e.g. `upgradedb(bind: Engine = engine)`, to `data/__init__.py` (or a sibling
   `data/upgrade.py`). It owns the whole sequence: **reflect → plan → refuse if unsafe (R4) →
   `create_all(bind)` → add columns → add indexes → PG constraint drop (R6)**. The validation has to
   run *before* `create_all`. Otherwise a refusal has already created `source`, which is the
   half-upgrade state the GOAL complains about. Note that `create_all` never adds an index to a
   table that already exists, so `index_tasks_source` and the R5 chain indexes need explicit
   handling.
2. `initdb()` (`data/__init__.py:65`) calls `upgradedb(engine)` in place of the bare `create_all`.
   `checkdb()` (`:84`) keeps its `has_table('task', schema=schema)` refusal and then calls
   `upgradedb(engine)`. An empty PostgreSQL database therefore still needs an explicit init, while a
   2.8 PostgreSQL database (with `task` but no `source`) gets the missing table, columns and indexes.
   `ensuredb` then inherits the upgrade with no change.
3. **Hole A — `hs initdb --truncate` / `--rotate`** never call a helper. `--truncate` runs
   `Task.count()` in its confirm prompt (`:244`) and `--rotate` runs `rotatedb()` (`:242`). Both
   fail on a 2.8 database. Verified on a 2.8-shaped SQLite file:
   `no such column: task.source` (the count) and `no such column: part` (the rotate UPDATE).
   Call `checkdb()` (which now upgrades) at the top of those two branches. `--vacuum` and `--backup`
   issue no ORM query and worked unchanged in the same drive.
4. **Hole B — rotated partitions.** `auto_union_sqlite()` (`task.py:870-887`) builds
   `select * from main.task union all select * from part_N.task`. After the upgrade, a partition
   rotated by 2.8 has 3 fewer columns. Verified: `hs list --count` fails with *"SELECTs to the left
   and right of UNION ALL do not have the same number of result columns"*. There is also a second,
   silent problem. `ALTER … ADD COLUMN` appends, so an upgraded `main.task` is ordered
   `…next_id, tag, source, fingerprint, part`, while a fresh 2.9 table (and so any partition rotated
   after a later `--truncate`) is ordered `…next_id, source, fingerprint, part, tag`. `select *`
   lines columns up by position, so the values would land in the wrong columns. Recommended fix,
   read-only: build the view with an **explicit column list from `Task.__table__`**, and for each
   partition substitute `NULL` (or `0` for `part`) for any column that
   `PRAGMA part_N.table_info(task)` lacks. Partition files are not written. R1 and R9 (`hs list`,
   `hs info`) need this fix whenever 2.8 partitions exist. Treat it as in scope.
5. **Hole C — `--no-db` still touches the DB.** `ServerApp.run` (`server.py:1367`) calls
   `ensuredb()` with no arguments and without checking `self.in_memory`. `ClusterApp.__enter__`
   (`cluster/__init__.py:543`) calls `initdb()` on SQLite even under `--no-db`. With the seam in
   place, an R4 refusal or a DDL failure would therefore break `hsx --no-db` and `hs server --no-db`.
   Guard both with `if not self.in_memory`.
6. **Existing bug, fix in passing:** `ServerApp` declares `--initdb` (`server.py:1329,1332`) but
   never reads `self.auto_initdb`. `hs server --initdb` against an empty PostgreSQL database raises
   `DatabaseUninitialized`. The one-line fix is `ensuredb(auto_init=self.auto_initdb)` under the
   `in_memory` guard.

Run it **at the command seam, not at import**. Give it a `bind` parameter: the repo's existing
pattern is a throwaway `create_engine('sqlite://')` passed explicitly (`tests/test_source.py:102`),
with no rebinding of `Session`.

## Entry-point table

| Command | First DB helper | Location | Covered by seam? |
|---|---|---|---|
| `hs submit` (DB mode) | `check_database` → `initdb` (SQLite / `--initdb`) else `checkdb` | `submit.py:1336,1368-1376` | Yes. Runs before `Task.current_group`/`prepare_source`. `-q` queue mode never touches the DB. |
| `hs server` | `ensuredb()` (no args) | `server.py:1367` | Yes on both dialects. `--initdb` is ignored (bug); `--no-db` is not guarded (Hole C). |
| `hs cluster` / `hsx` | `__enter__`: `initdb` (SQLite / `--initdb`) elif not `--no-db`: `checkdb` | `cluster/__init__.py:543-546` | Yes. SQLite `--no-db` still upgrades (Hole C). |
| `hs task submit` | `ensuredb()` | `task.py:102` | Yes |
| `hs info` | `ensuredb()` then `auto_union_sqlite` | `task.py:192` | Seam yes; partitions no (Hole B) |
| `hs wait` | `ensuredb()`, then nested `TaskInfoApp.run` repeats it | `task.py:363,366-369` | Yes (the repeat is a no-op) |
| `hs task run` | `ensuredb()` ×3 (self, Wait, Info) | `task.py:430` | Yes |
| `hs list` / `search` | `ensuredb()` then `auto_union_sqlite` | `task.py:696` | Seam yes; partitions no (Hole B) |
| `hs update` | `ensuredb()` | `task.py:1021` | Yes |
| `hs initdb` (no flag) | SQLite `initdb(optimize=True)`; PG confirm → `initdb()` | `data/__init__.py:247-252` | Yes |
| `hs initdb --truncate` | **none** → `Task.count()` → `truncatedb` | `:243-245` | **No** (Hole A). `drop_all`/`create_all` itself is fine. |
| `hs initdb --rotate` | **none** → `rotatedb` | `:239-242` | **No** (Hole A). The partition engine (`:156`) opens a `VACUUM INTO` copy of the upgraded main, so it is current. |
| `hs initdb --vacuum/--backup` | none | `:233-238` | Not needed (no ORM query) |
| `hs client`, `hs config`, `--help` | none (imports `data.core`; the engine is lazy) | — | N/A; must stay DB-free |
| `LiveSubmitThread` | none: queue only, no DB | `submit.py:783` | N/A |
| Python API (`serve_from`, `submit_from`, `run_local`, …) | none | — | **Bypasses all helpers** (pre-existing; outside R1, which lists commands). Document "call `ensuredb()` first". |

No CLI path queries `Session` before its helper. Verified: `SubmitApp.run` runs `check_arguments`
and `check_source` (no DB) before `check_database`, and `ClusterApp` does its helper in `__enter__`
before `run`.

## Session/import findings

- **Run on its own connection** (`with bind.begin()`), never through the scoped `Session`. `Session`
  is thread-local and may hold an open transaction. The upgrade's commit must not be tied to
  unrelated work. PostgreSQL `SET LOCAL lock_timeout` belongs in the upgrade transaction. Running on
  its own connection is also what makes `bind=` testable.
- **SQLite self-lock:** a second pool connection's DDL blocks until busy-timeout only if the same
  process's `Session` holds an *uncommitted write* (RESERVED lock). The pysqlite legacy mode emits no
  BEGIN for SELECTs, so reads hold nothing once fetched. On every path the first helper call precedes
  all `Session` use. The repeat calls (`hs wait --info`, `hs task run`) come after a commit and are
  read-only no-ops. `initdb(optimize=True)` must keep `PRAGMA optimize` (via `Session`) *after* the
  upgrade. Result: no deadlock.
- **Inspector caching:** `Inspector` caches what it reflects. Re-plan with a fresh `inspect(conn)`
  inside the write transaction after taking the lock (SQLite `BEGIN IMMEDIATE`; PostgreSQL
  `pg_advisory_xact_lock` or `IF NOT EXISTS` DDL). That re-plan is what satisfies R3's
  concurrent-process clause.
- **Not at import time.** Every `hs` invocation imports `data.core` through `hypershell/__init__.py`.
  That includes `hs client` on compute nodes that often cannot reach PostgreSQL, `hs config` (the
  tool used to *repair* a bad DB config), `--help`, and shell completion helpers
  (`hs server --available-ports`). Import-time failures `sys.exit(3)` with a traceback file. Running
  the upgrade at import would turn a DB outage or refusal into "every command is broken". It would
  also make `bind=` untestable.

## Logging finding

`core/logging.py:66` defines **`TRACE = DEBUG - 5`** (also `DEVEL = 1`), and `Logger.trace()` exists.
`temp_site` sets `HYPERSHELL_LOGGING_LEVEL=DEBUG`, and the tests run the CLI as a subprocess
(`tests/__init__.py:30`), so every DEBUG line lands in captured stderr. Many tests assert stderr
**exactly**:

- `test_initdb.py::test_vacuum` expects exactly the 2 INFO lines from `hs initdb --yes`.
- `test_list_empty_list` and `test_list_empty_count` expect `hs list` stderr to be empty.
- There are roughly 58 more exact-tuple assertions across `test_submit`, `test_groups`,
  `test_restart`, `test_cluster` and `test_submit_json`.

I drove a temp site with the venv CLI. At DEBUG, `hs initdb --yes` emits only those two INFO lines,
and `hs list --count` emits nothing on stderr. A `log.trace` line (`Added task …`) appears only at
`TRACE`.

So a `log.debug` "schema current" line would break dozens of tests. **Use `log.trace` for the no-op
path.** It satisfies R3 ("nothing above DEBUG"). Change lines stay at `log.info` per R3, for example
`Added column task.source (UUID)`. Those appear only on a real upgrade, so no existing test sees
them. SQLite reflection (`get_columns`/`get_indexes`/`get_unique_constraints`) raised no SAWarning
under `-W error`, so nothing leaks onto stderr there.

## Error-path recommendation

- Add `class SchemaUpgradeError(Exception)` to `src/hypershell/core/exceptions.py`, next to
  `DatabaseUninitialized`. Add one entry to `get_shared_exception_mapping`:
  `SchemaUpgradeError: partial(handle_exception, logger=logger, status=exit_status.runtime_error)`
  (= 5, matching `DatabaseUninitialized`).
- The entry must sit **before** the `Exception` catch-all: cmdkit dispatches on the first
  `isinstance` match in dict order (`cmdkit/app.py:134`).
- Every DB app already spreads the shared mapping, including `InitDBApp`, `ClusterApp`,
  `ServerApp`, `SubmitApp` and all of `task.py`. `ClusterApp.__enter__` exceptions are caught by
  `Application.main` as well.
- The output is a single line, `CRITICAL [hypershell.<mod>] SchemaUpgradeError: cannot add column
  task.X (NOT NULL, no server default) …`, with no traceback file.
- Raise it during the plan step, before any DDL (R4: "schema unchanged").
- Also wrap DDL `ProgrammingError`/`OperationalError` (for example a PostgreSQL role that does not
  own `task`, or a `lock_timeout` hit) in the same type, with guidance. Otherwise they fall through
  to `write_traceback`.

## Docs touch list

- `data/__init__.py:187` `INITDB_HELP` "For SQLite this happens automatically." and `:207`
  `InitDBApp` docstring "(not needed for SQLite/Turso)" (shown in `hs --help`, `__init__.py:91`):
  add "existing databases are upgraded in place on first use".
- Generated outputs to regenerate: `docs/_include/initdb_desc.rst:3`, plus
  `share/man/man1/{hs,hsx,hyper-shell}.1` (`hs.1:1176`).
- `docs/_include/database.rst` §Initialization (`~:101-109`) and `docs/_include/database_alt.rst:9-10`
  (used by `manual.rst:238`): add the upgrade-on-first-use paragraph and the R7 SQLite note.
- `--initdb` help in `server.py`, `cluster/__init__.py` and `submit.py`, plus
  `docs/_include/{server,cluster,submit}_help.rst` ("auto-initialize tables if they don't already
  exist… by default with SQLite"): note that the upgrade runs regardless of `--initdb`.
- `docs/api/data.rst`: add `autofunction` for the new function if it is public, and a note that API
  users call `ensuredb()` first.
- `AGENTS.md:82`: misattributes `InitDBApp` to `data/core.py` (it lives in `data/__init__.py`).
  Correct it alongside the R8 edits, and add the seam to "Data layer specifics".

## Open risks

1. **2.8 rotated partitions (Hole B)** go beyond the "one seam" the GOAL scoped, but R1/R9 fail
   without the fix. The plan must also decide what `part` reads as for 2.8 partition rows. Literal
   `0` follows the GOAL non-goal but makes `hs list --part 0` include them; the partition index
   `N` is the alternative.
2. **PostgreSQL lock queueing.** `ALTER TABLE` needs ACCESS EXCLUSIVE. A still-running 2.8 server
   that is "idle in transaction" makes the first 2.9 command wait forever, and everything queues
   behind it. Set a `lock_timeout` and fail with `SchemaUpgradeError`.
3. **SQLite atomicity.** SQLAlchemy+pysqlite emits no BEGIN for DDL. Multi-statement atomicity needs
   an explicit `BEGIN IMMEDIATE` on the upgrade connection, or validate-then-apply plus idempotent
   steps. Concurrent `ALTER ADD COLUMN` without the lock and re-plan fails with
   "duplicate column name".
4. **Per-start cost.** Reflection now runs on every SQLite command, 2–3× in `hs task run` and
   `hs wait --info`. It is cheap, and an optional per-process memo per engine URL would remove it.
5. **Python API** callers bypass every helper (pre-existing). Document it; don't expand scope.
6. **`schema` namespace** (PostgreSQL `database.schema`): reflection and DDL must pass `schema=`,
   as `checkdb` does.
