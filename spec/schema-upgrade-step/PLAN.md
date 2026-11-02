# PLAN — Open 2.8 databases in 2.9: an idempotent schema-upgrade step

> **Status:** Draft for review · **Last updated:** 2026-11-01
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

The plan adds one function, `upgrade_schema(bind, metadata)`, in a new
`src/hypershell/data/upgrade.py`. It diffs the SQLAlchemy model against the live schema and adds
whatever is missing: tables, columns and indexes. It refuses, before running any statement, any
column that cannot be added in place. On PostgreSQL it also drops the two retry-chain `UNIQUE`
constraints. A current schema costs one read-only reflection and takes no lock. Real work runs in
one locked transaction that re-plans under the lock: SQLite `BEGIN IMMEDIATE`, PostgreSQL advisory
lock. `initdb()` and `checkdb()` both call it, so every command reaches it on both dialects. Two
side paths need small fixes: `hs initdb --truncate/--rotate`, and the rotated-partition view.
Tests start from a committed SQLite database made by real hypershell 2.8.3, and are red on the base
first. A new CI job covers PostgreSQL against databases that real 2.8.3 and 2.9.0a1 build there.
The step is not a migration framework: it has no version table and no scripts.

## 2. Design

### 2.1 The step (`src/hypershell/data/upgrade.py`, new)

`upgrade_schema(bind: Engine, metadata: MetaData = Entity.metadata) -> list[str]` returns the
changes it made, which is empty when the schema was current. `metadata` is a parameter so tests
can pass a model with extra columns without touching the real model.

**Plan (pure reflection, no writes).** `_plan(conn, metadata)` builds a fresh `inspect(conn)` on
every call; the inspector caches, so it must never be reused. For each table in
`metadata.sorted_tables`, honouring `table.schema`:

| Model vs. live schema | Planned statement |
|---|---|
| Table missing | `CreateTable(t, if_not_exists=True)`, then `CreateIndex(ix, if_not_exists=True)` for each of `t.indexes` |
| Column missing | `ALTER TABLE {prep.format_table(t)} ADD COLUMN {CreateColumn(c).compile(dialect=conn.dialect)}` |
| Index missing (by name; `get_indexes` omits SQLite autoindexes) | `CreateIndex(ix, if_not_exists=True)` |
| PostgreSQL only: a reflected unique constraint whose `column_names` is exactly one entry of `RETIRED_UNIQUE` | `ALTER TABLE {fmt} DROP CONSTRAINT IF EXISTS {prep.quote(name)}` |

- `RETIRED_UNIQUE = {('task', 'previous_id'), ('task', 'next_id')}` is the only named case, a
  module constant with a declarative comment. The step never drops any other constraint, so the
  removal R2 allows stays a closed list. The constraint name is always reflected
  (`task_previous_id_key` expected), never hard-coded.
- SQLite constraints reflect nameless and cannot be dropped, so the step skips them by dialect
  (R7).
- **Plan order:** tables first, then columns, then constraint drops, then indexes. The R5 chain
  indexes are created after the PostgreSQL constraint drop.

**Refusal (R4), raised while planning, so no statement has run.** Each missing column `c` is
checked in order:

1. `c.primary_key`
2. `c.unique`, or a missing `Index(unique=True)` on an existing table
3. `c.foreign_keys`
4. `c.computed` or `c.identity`
5. `not c.nullable and c.server_default is None`
6. A `server_default` that is not a constant literal: SQLite rejects non-constant defaults on
   `ADD COLUMN`.

`CreateColumn` silently drops PK, UNIQUE and FK, so rules 1–3 prevent silent schema drift as well
as raw driver errors. A hit raises `SchemaUpgradeError`, which names the table, the column, the
reason and the fix. Example: *"Cannot upgrade table 'task': column 'x' is NOT NULL with no
server default, so existing rows have no value for it. Declare a server_default or make it
nullable."* ([03](research/03-ddl-from-model.md#refusal-rules)).

**Apply (serialized).**

1. Lock-free `_plan`. If the plan is empty, log at `trace` ("Database schema is current") and
   return `[]`. No lock is taken and no DDL runs (R3).
2. Otherwise open a dedicated connection. It is never the scoped `Session`, and it never goes
   through `Session` at all.
   - **SQLite / Turso:** `execution_options(isolation_level='AUTOCOMMIT')`, then
     `PRAGMA busy_timeout = 60000`, then `BEGIN IMMEDIATE`.
   - **PostgreSQL:** `SELECT set_config('lock_timeout', '30s', true)`, then
     `SELECT pg_advisory_xact_lock(SCHEMA_UPGRADE_LOCK_KEY)`. The key is the constant
     `-4139901733377976503`, the first 8 bytes of `sha256(b'hypershell:schema-upgrade')`. The
     derivation sits in a comment beside the literal, and the value must never change across
     releases.
3. Re-plan on the locked connection; refusals are re-checked too. Execute each step, logging it
   (below), then `COMMIT`. On error, `ROLLBACK` in a `finally`. SQLite restores its busy timeout
   there as well.
4. **One whole-step retry.** If a statement still fails with a duplicate or already-exists error
   (SQLite `duplicate column name` / `already exists`; PostgreSQL SQLSTATE `42701`, `42P07`,
   `23505`), roll back and rerun steps 2–3 once. This covers peers that don't take the lock:
   Turso, if `BEGIN IMMEDIATE` is unsupported there, or hand-run SQL. The re-plan will be empty.
5. **Lock waits.** A SQLite `database is locked` after 60 s, or a PostgreSQL `55P03
   lock_not_available`, is re-raised as `SchemaUpgradeError`. The schema is unchanged, and the
   message says that another session holds the `task` table (for example, an older server) and
   to stop it or retry. Other DDL `OperationalError`/`ProgrammingError`s (for example, a role
   that does not own `task`) are wrapped the same way, so they print one line and not a
   traceback file.

PostgreSQL DDL is transactional. SQLite DDL is transactional only under the explicit `BEGIN`: the
default pysqlite path autocommits every statement, which is how the half-upgraded state arose. So
an R4 refusal or a mid-step failure leaves the schema unchanged on both dialects
([04](research/04-concurrency.md#sqlite-experiment)).

**Logging (R3).**

- **Existing database** (`task` was present before the step): each executed step logs at INFO
  and names the table. The messages are `Added column task.source`, `Created index
  index_tasks_source on task`, `Created table source` and `Dropped unique constraint
  task_previous_id_key on task`.
- **Empty database:** table and index creation is initialization. It logs at `trace`, as today's
  silent `create_all` does, so the roughly 60 existing exact-stderr assertions on fresh
  `temp_site` databases stay green ([02](research/02-upgrade-seam.md#logging-finding)).
- **No-op path:** `trace` only. A `debug` line there would also break those assertions.

**`SchemaUpgradeError`** goes in `src/hypershell/core/exceptions.py`, next to
`DatabaseUninitialized`. It is mapped in `get_shared_exception_mapping` to
`partial(handle_exception, …, status=exit_status.runtime_error)`, placed before the `Exception`
catch-all because cmdkit takes the first `isinstance` match.

### 2.2 Where it runs (`src/hypershell/data/__init__.py`)

- **`upgradedb()`** is a thin public wrapper: `upgrade_schema(engine)`. It joins `__all__`.
- **`initdb(optimize)`** calls `upgradedb()` in place of `Entity.metadata.create_all(engine)`. On
  an empty database the plan holds every table, so it initializes. `PRAGMA optimize` still runs
  afterwards through `Session`.
- **`checkdb()`** keeps its `has_table('task', schema=schema)` refusal. An empty PostgreSQL
  database still needs `hs initdb` or `--initdb`. After that check it calls `upgradedb()`. That is
  how PostgreSQL reaches the step on first use without `--initdb`: every start runs `checkdb`, and
  it now creates a 2.8 database's missing `source` table too.
- **`ensuredb()`** is unchanged. It already routes to one of the two.
- **`InitDBApp.run`:** the `--truncate` and `--rotate` branches call `checkdb()` first. On a 2.8
  database, `Task.count()` in the truncate prompt and `rotatedb()`'s `UPDATE … part` otherwise
  fail with `no such column`. `--vacuum` and `--backup` issue no ORM query and stay as they are.
  `truncatedb()` keeps `drop_all`/`create_all`, which with the new model gives the R5 fresh
  schema.

The step runs at the command seam, never at import. `hs client`, `hs config`, `--help` and
completion helpers import `data.core` and must stay database-free; an import-time failure exits 3
for every command. No command path issues a `Session` statement before its helper, so the
SQLite self-lock described in [04](research/04-concurrency.md#self-blocking-same-process) cannot
occur. The ordering rule is "the step runs before the process's `Session` issues anything". Only
the first helper call in a process can find a non-empty plan, so only that call takes a lock. The
later calls in `hs wait --info` and `hs task run` come after commits, and they are lock-free
no-ops. `Session.remove()` is deliberately **not** used: it would detach objects those commands
already loaded and expired on commit.

### 2.3 Model changes (`src/hypershell/data/model.py`)

These declarations change only: the chain columns, `part`, and the module-level `Index`es. The
`retried`-filter and group-gating methods that the sibling `group-advance-retry-gate` changes are
not touched.

- `previous_id`, `next_id`: `mapped_column(UUID, nullable=True)`, with no `unique=True`. A
  declarative comment above them says why: TimescaleDB hypertables require every unique index to
  include the partition column, and one schema serves every provider. It also says that one retry
  per failed row is held by the Scheduler's `retried` filter, not by the schema.
- New `index_tasks_previous_id = Index('index_tasks_previous_id', Task.previous_id)` and
  `index_tasks_next_id`, beside the other `index_tasks_*`.
- `part`: add `server_default=text('0')` and keep `default=0`. The DDL renders `INTEGER DEFAULT 0
  NOT NULL` on both dialects. PostgreSQL 11+ treats it as metadata only; SQLite stores the
  default, and existing rows read 0.

### 2.4 The rotated-partition view (`src/hypershell/task.py`, `auto_union_sqlite`)

`select * … union all select * from part_N.task` lines columns up by position. That breaks in two
ways ([02](research/02-upgrade-seam.md#recommendation), Hole B):

- A 2.8 partition has 3 fewer columns, so `hs list` errors.
- `ADD COLUMN` appends, so an upgraded `main.task` ends `…, tag, source, fingerprint, part`. A
  freshly created table orders those columns `…, source, fingerprint, part, tag`. Rows from a
  mix would land silently in the wrong columns.

The view now names its columns explicitly, taken from `Task.__table__.columns` in model order. For
each attached partition it reads `PRAGMA part_N.table_info(task)` and selects
`NULL AS <col>` for a missing column, except `part`, which reads `0 AS part`, per the GOAL
non-goal. Partition files are only read, never upgraded. This change is the one deviation outside
the data-layer seam (§3).

### 2.5 PostgreSQL in CI (`.github/workflows/tests.yml`, `tests/`)

- **New `postgres` job.** It uses a service container with a `matrix.include` of `postgres:13`
  (floor) and `postgres:18` (current), with a `pg_isready` health check and Python 3.14 via
  `uv run --extra postgres`. Actions keep the existing SHA pins. A `timescale` row can be added
  later with no restructuring.
- **`tests/fixtures/make-legacy-postgres.sh`** builds the template databases before pytest:
  - `hs283`: `hypershell[postgres]==2.8.3`, installed in an isolated `uv venv --python 3.12`,
    runs `hs initdb --yes`, the same `hsx … --max-retries 1` task file as the SQLite generator,
    and two `hs submit`s.
  - `hs290a1`: created `TEMPLATE hs283`, then `hypershell[postgres]==2.9.0a1` runs
    `hs initdb --yes`. That reproduces the half-upgraded state: `source` exists, `task` still has
    the 2.8 columns.

  Admin SQL goes through `docker exec … psql`. All processes exit before pytest, so the templates
  have no connections.
- **Gate:** `HS_TEST_POSTGRES` (admin URL) and `HS_TEST_POSTGRES_TEMPLATES`. The conftest scrubs
  every `HYPERSHELL_*` variable, so the gate cannot use that prefix. The tests skip when the
  variables are unset, which keeps local runs and the existing `tests` job SQLite-only. If the
  gate is set but a named template is missing, the test calls `pytest.fail`, not `skip`. A green
  CI job therefore means every PostgreSQL test ran.
- **Tests** carry `@mark.integration` plus a `skipif`, since only those two markers exist.
- **`pg_database` fixture:** an AUTOCOMMIT admin engine runs
  `CREATE DATABASE t_<hex> [TEMPLATE …]` and, at teardown, `DROP DATABASE … WITH (FORCE)`.
- **`pg_site` fixture:** sets `HYPERSHELL_DATABASE_PROVIDER=postgres` with
  `_HOST/_PORT/_USER/_PASSWORD/_DATABASE`. It never sets `_FILE`, and never uses `postgresql`,
  which is broken at import (§5).

### 2.6 Tests on SQLite

**Fixture.**

- `tests/fixtures/hypershell-2.8.3.db` (≈40 KB) is made by `tests/fixtures/make-2.8.3-db.sh`
  running real `hypershell==2.8.3`. The script pins `TZ=UTC` and the hostname, and checks before
  writing that no real hostname leaked into the file.
- `tests/fixtures/README.md` records the provenance: versions, the command and the sha256.
- The file holds 3 done tasks, a failed→retried→failed chain and 2 pending tasks, all with `tag`
  carrying `"part": 0`.
- Tests `shutil.copyfile` it into `temp_site` and never open the committed file read-write.
- **Half-upgraded copy:** apply the two frozen 2.9.0a1 statements (`CREATE TABLE source …`,
  `CREATE INDEX index_source_lookup …`) to a copy with raw `sqlite3`
  ([05](research/05-fixture-and-red-first.md#half-upgraded-state)).
- **Snapshot helper:** reads every pre-existing row's original 30 `task` columns through raw
  `sqlite3`, keyed by `id`, so `tag` is compared as stored text. It also captures `client`.

**`tests/test_upgrade_cli.py`** (`integration`, driving the CLI). It is committed red in P1
(§6).

- **R9 end to end:**
  1. `hs submit -t kind:new -- echo new-0`
  2. `hs list --count` returns 8
  3. `hsx --restart -r 2 -N2 -b1 -w1 --no-tls` exits 0, and stdout is
     `{new-0, pending-0, pending-1}`
  4. `hs list --count` returns 9, and `--remaining --count` returns 0
  5. Exactly one new row has `attempt = 3` and `previous_id =` attempt 2's id
  6. Attempt 2 now has `retried = 1` and its `next_id` set
  7. Every other original row equals the snapshot, apart from the executed pending rows' run
     fields

- **R1:**
  - Each listed command, run first against an untouched copy and against a half-upgraded copy,
    exits 0: `hs list --all`, `hs info <id>`, `hs search`, `hs update`, `hs wait`, `hs initdb
    --yes` and `hs server`. `hs server` is started with a short `--forever` timeout or the
    existing server test pattern.
  - On both copies, `hs initdb --truncate --yes` and `hs initdb --rotate --yes` succeed.
  - A partitions case: an upgraded main plus the 2.8.3 file copied in as `local.1`.
    `hs list --count` returns 14, and a known row's `args`/`tag` read back in the right columns.
    A main/partition column-order mismatch is covered as well.
- **R2:** the snapshot is equal after the first command. New columns read
  `NULL`/`NULL`/`0`.
- **R3:**
  - A second command logs no upgrade line, and `sqlite_master` is unchanged.
  - The first command's stderr holds the INFO lines naming `task`.
  - A real 4-process smoke test (`multiprocessing` spawn plus a `Barrier`, each process running
    `hs list --count` on one fixture copy) runs in rollback-journal and WAL mode. All processes
    exit 0, and exactly one logs changes.
- **R7:** after the upgrade, the SQLite `UNIQUE (previous_id)` and `UNIQUE (next_id)` are still
  present. The R9 retry ran with them in place.

**`tests/test_data/test_upgrade.py`** (`unit`, in-process, `upgrade_schema(bind=<per-test
engine>, metadata=…)`). It arrives with the fix, because it imports the new function.

- **R3 race.** Two engines share one fixture copy. A monkeypatched `_lock` runs engine A's full
  upgrade before engine B takes the real lock. B's first plan is non-empty, its second plan is
  empty, both return, B logs nothing at DEBUG or above, and the schema ends current.
- **R3 mutation check.** With the re-plan patched out, B must raise `duplicate column name`.
- **R3 no-op.** A second call takes no lock (spy) and emits no `ALTER`/`CREATE` (counted with a
  `before_cursor_execute` listener).
- **R4 generic add.** A copied `MetaData` gains a nullable column and a column with a server
  default. Both are added, with no per-column code.
- **R4 refusals.** One case per rule. Each raises `SchemaUpgradeError` naming the table and
  column, and `sqlite_master` stays byte-identical, including columns planned ahead of the
  refused one.
- **R4 CLI path.** The same refusal through `hs list` prints one `CRITICAL … SchemaUpgradeError`
  line, exits `runtime_error`, and writes no traceback file.
- **R5.** A fresh `create_all` and `truncatedb` on SQLite give `get_unique_constraints('task') ==
  []`, and both chain indexes exist.
- **Ordering guard.** After a no-op upgrade, a second `checkdb()` call while the `Session` holds
  an uncommitted write must succeed without waiting. This pins "repeat calls take no lock".
- **Opt-in fixture check (`test_fixture_provenance`).** It is gated by `HS_TEST_REGEN_FIXTURE`
  and needs PyPI and Python 3.12. It reruns the generator into `tmp_path` and compares the
  `sqlite_master` DDL and row shape (ids mapped to ordinals) with the committed file.

**`tests/test_upgrade_postgres.py`** (gated, §2.5). R1 runs on `hs283` and `hs290a1` through
`checkdb` without `--initdb`. R2 checks the snapshot, `part` filled by `NOT NULL DEFAULT 0` on
populated rows, and `source` with `data_type = 'uuid'`. R3 runs two concurrent CLI processes and
the in-process race. For R4, a refusal plus a forced mid-step failure must roll back fully. R5
checks a fresh database. R6 checks that the constraints are gone, the indexes exist and a second
run is a no-op. R9 runs end to end, as on SQLite. A schema-qualified variant sets
`HYPERSHELL_DATABASE_SCHEMA`. A third-session test holds `BEGIN; SELECT … FROM task` and expects
the actionable lock-timeout error and an unchanged schema. The step makes the timeout overridable
by a module constant for this test.

### 2.7 Docs and invariants (R7, R8)

- **`docs/_include/database.rst` §Initialization and `database_alt.rst`.**
  - 2.9 upgrades a 2.8 database in place on first use, on SQLite and PostgreSQL.
  - The upgrade only adds; it never drops or rewrites data. The one removal is the PostgreSQL
    retry-chain constraints.
  - PostgreSQL holds a brief exclusive lock while it adds columns and builds indexes.
  - Pre-2.9 SQLite databases keep the `previous_id`/`next_id` `UNIQUE` constraints. The exact
    wording waits on §5 Q1.
- **Help text.** `INITDB_HELP` and the `InitDBApp` docstring get one line on upgrade on first
  use. The help includes regenerate in the same commit: `docs/_include/initdb_*.rst` and
  `share/man/man1/{hs,hsx,hyper-shell}.1` (§12). No flags change, so the completions are
  untouched.
- **`AGENTS.md`.** The *Retry model* paragraph and the `data/model.py` footgun bullet get the
  wording below. "Data layer specifics" gains one bullet on the upgrade step. Fix the `InitDBApp`
  misattribution at `AGENTS.md:82`: it lives in `data/__init__.py`, not `data/core.py`.
- **`.agents/factory/invariants.md` §3:** the same amendment.

**R8: the replacement guard, as it stands once `group-advance-retry-gate` lands.** That cycle
moves `select_failed`'s filters into `Task.query_retryable` and leaves `__schedule_next_failed_tasks`
and `Task.next` untouched. Verified against its built source
([06](research/06-retry-guard.md#guard-as-of-group-advance-retry-gate)). The *Retry model*
paragraph will read:

> **Retry model:** retries are **new rows** (`attempt+1`; the new row's `previous_id` names the
> old row, which gets `retried=True` and `next_id`) — never mutate a row in place to retry.
> Nothing in the schema prevents a doubled retry. A failed row is retried once because the
> server's single Scheduler thread is the only caller of `Task.next`, and `Task.query_retryable`
> (under `select_failed`) skips rows already marked `retried`. **Unguarded**, each adding a second
> retry row with the same `previous_id`:
> - two servers on one database, which is unsupported: no query takes row locks, so new tasks
>   double-schedule too;
> - a process killed between the retry insert and the `retried` mark, which are separate commits;
> - a reset `retried` flag, from a late full-row writeback by an evicted client or from
>   `hs task update`.
>
> Pre-2.9 SQLite databases keep `UNIQUE` on both columns, because SQLite cannot drop them without a
> table rebuild. Normal operation never trips them; in the three cases above they raise
> `IntegrityError` instead.

The rest of that paragraph (`attempts == max_retries + 1` spans …, the `CANCEL_STATUS` filter) keeps
the sibling's wording.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1 | `upgradedb()` from `initdb` (SQLite every start, `--initdb`, `hs initdb`) and `checkdb` (PostgreSQL every start) (§2.2). Missing tables via `CreateTable`, so the half-upgraded state is covered (§2.1). `hs initdb --truncate/--rotate` call `checkdb` first. The partition view uses explicit columns (§2.4). Tested on SQLite (`test_upgrade_cli.py`) and PostgreSQL (`hs283`, `hs290a1`). |
| R2 | The plan holds only `CREATE TABLE/INDEX` and `ADD COLUMN`, plus the closed `RETIRED_UNIQUE` drop. `part`'s `server_default` fills old rows with 0, and `source`/`fingerprint` read NULL. The raw-`sqlite3` snapshot test checks every original row, `tag` text included. |
| R3 | Lock-free plan, so an empty plan is a no-op logged at `trace`. Locked re-plan under `BEGIN IMMEDIATE` / `pg_advisory_xact_lock`, plus one whole-step retry. Step-only timeouts. INFO per change, naming the table. The deterministic race test, the mutation check, the no-op spy and the 4-process smoke test. |
| R4 | `CreateColumn`-driven `ADD COLUMN` with no per-column code. Six refusal rules checked at plan time, before any statement. `SchemaUpgradeError` → one-line CRITICAL, `runtime_error`. Atomic rollback on both dialects. |
| R5 | `unique=True` removed. `index_tasks_previous_id` / `index_tasks_next_id` added. The model comment records the rationale. Fresh-database tests on both dialects. |
| R6 | PostgreSQL-only reflected drop of `RETIRED_UNIQUE` constraints, then the generic index diff creates the R5 indexes. Idempotent through the no-op path. PostgreSQL CI test. |
| R7 | The dialect skip: SQLite constraints are never touched and no table is rebuilt. The R9 retry runs with them in place. Docs paragraph (§2.7, wording pending §5 Q1). |
| R8 | The amended text in `AGENTS.md` (×2) and `invariants.md` §3, stating the real guard and naming the three unguarded configurations (§2.7). |
| R9 | The committed real-2.8.3 SQLite fixture with provenance and an opt-in regeneration check. The CLI end-to-end test. The PostgreSQL CI job builds `hs283` from real 2.8.3 and reruns it. The existing suite and the docs build are in every phase's verify. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research (gate #1: the GOAL is sane against §1, §3, §4, §10 and §12) and again against this
design (gate #2).

- **§1 Task lifecycle.** No new state and no new predicate. The partition view selects the same
  columns, so every lifecycle query still works on it. `part` stays non-null.
- **§2 `exit_status`.** Untouched.
- **§3 Retry model.** Retries are still new rows, written only by `Task.next`. The chain loses its
  `UNIQUE` (GOAL R5/R6, approved as Q10a), and R8 amends the invariant in the same change. The
  stated guard is the code's real guard, verified, and the unguarded cases are named. No
  retry-gating method is edited. `attempts == max_retries + 1` is untouched.
- **§4 Server modes.** `in_memory` runs no Scheduler and no step: `ensuredb` already refuses
  `:memory:`. The step is not added to any `--no-db` path. The existing `--no-db` behaviour is
  left as it is (§5).
- **§10 Configuration.** The engine and config stay import-time and immutable. The step runs at the
  command seam, never at import, and reads `schema` from the table metadata. No config key is
  added; the timeouts are module constants.
- **§12 Conventions.**
  - The help text change regenerates `docs/_include` and the man pages in the same commit.
    Completions are unchanged.
  - The SQLAlchemy floor (`>=2.0.50`) already has `if_not_exists` and `DropConstraint(if_exists)`.
    No floor is raised.
  - Tests use only `unit`/`integration`; the gates are `skipif`.
  - Exit codes come from `exit_status`.
  - Source comments state the reason in prose, never a spec id.

### Deviation justifications

None of these rows changes what an R-ID SHALL do.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A fix outside the data-layer seam: `auto_union_sqlite` in `task.py` builds the partition view from an explicit column list. | R1/R9: `hs list`/`hs info` must succeed after the upgrade. With a 2.8 partition the `select *` union errors. With a mixed column order it silently misaligns. | Leaving it fails R1 on any site that has rotated. Upgrading the partition files writes outside the main database, which R2's spirit and forward-only rule forbid. |
| `hs initdb --truncate/--rotate` gain a `checkdb()` call. | R1 lists `hs initdb`. Both branches query `Task` before any helper. | Leaving it: `no such column` on a 2.8 database. |
| A new CI job with a service container, plus a PostgreSQL fixture script that installs 2.8.3 and 2.9.0a1 from PyPI. | R1–R6 and R9 "on PostgreSQL where testable". The GOAL's clarification asked for this choice. | Mocked or compiled-only PostgreSQL DDL cannot show transactional DDL, real constraint names, advisory locks or `NOT NULL DEFAULT` on populated rows. |
| A committed 40 KB binary test fixture (it ships in the sdist, not the wheel). | R9 requires a database made by real 2.8.3, deterministically and offline. | Generating at test time puts PyPI on every test run, fails offline and adds about 1 s plus a 48 MB install per run. It stays available as the opt-in honesty check. |
| A lock plus a re-plan, not plain try/except. | R3 on both dialects, and R4's "schema unchanged". | Per-statement catch-and-recheck gives up atomicity. It also misses PostgreSQL `CREATE INDEX`/`CREATE TABLE IF NOT EXISTS` catalog races (`23505` on `pg_class`/`pg_type`) ([04](research/04-concurrency.md#postgresql-reasoning)). |

## 4. Rabbit holes (resolved)

- **How PostgreSQL gets tested** → a service-container job, with templates from real 2.8.3 and
  2.9.0a1, a per-test `CREATE DATABASE … TEMPLATE`, and a non-`HYPERSHELL_` gate. It is reusable
  as a `timescale` matrix row ([01](research/01-postgres-testing.md)).
- **Where the step runs, given `ensuredb` only checks on PostgreSQL** → inside `checkdb()` as well
  as `initdb()`. Every PostgreSQL start runs `checkdb`, so first use upgrades without `--initdb`.
  Not at import ([02](research/02-upgrade-seam.md)).
- **A database-side default for `part`** → `server_default=text('0')` next to `default=0`. It
  renders `DEFAULT 0 NOT NULL` on both dialects, verified on SQLite with populated rows
  ([03](research/03-ddl-from-model.md#compiled-ddl-evidence)).
- **A fixture really created by 2.8.3** → committed, made by the tracked generator running PyPI
  2.8.3, with provenance. It is reproducible: 3 runs, including a cold install, gave identical DDL
  and row shape. An opt-in test regenerates and compares it. On PostgreSQL the same CLI recipe
  runs live in CI ([05](research/05-fixture-and-red-first.md)). This trades a little honesty for
  determinism, and the opt-in check plus `hs-review` re-driving the generator pay that back.
- **Concurrent upgraders** → serialized, with a lock-free fast path. The SQLite experiment
  confirmed it: without the lock the loser failed 10/10 trials; with it, 2 and 4 processes and
  WAL all succeeded ([04](research/04-concurrency.md)).
- **SQLite DDL atomicity** → the default pysqlite path autocommits each DDL statement. The
  explicit `BEGIN IMMEDIATE` makes the whole step roll back together (verified).
- **Exact-stderr tests vs. new logging** → the no-op and initialization paths use `trace`.

## 5. Risks & open questions

- **Q1 — R7's wording.** [NEEDS CLARIFICATION: R7 says retries on a pre-2.9 SQLite database
  work "exactly as on a fresh one", and the docs must say the kept constraints have "no effect on
  behaviour". Both hold in normal single-server operation. In the three unguarded cases of R8 (a
  kill between commits, a reset `retried`, two servers), an old SQLite database raises
  `IntegrityError` and kills the Scheduler, while a fresh one silently adds a second retry
  ([06](research/06-retry-guard.md#unguarded-configurations)). May the docs and R7 read "no effect
  on behaviour in normal operation", with the abnormal-case difference stated in the R8 invariant?
  Until answered, P5 writes the docs sentence that way and flags it. No code depends on the
  answer.]
- **External waits.**
  - PostgreSQL evidence exists only once the `postgres` job runs on a pushed branch (P6).
  - The R8 text names `Task.query_retryable`, which exists only after `group-advance-retry-gate`
    lands. P5 therefore needs this branch rebased onto a `develop` that contains it. P5's verify
    checks for that.
- **Rebase surface with `group-advance-retry-gate`:**
  - `data/model.py`: the chain columns, `part`, and module-level indexes, against their
    retry-gating classmethods. Different hunks.
  - `AGENTS.md`: the same *Retry model* paragraph. Rebase before P5 and merge the wording.
  - `server.py`: not touched by this plan.
- **PostgreSQL lock queueing.** An older server left idle in a transaction makes the first
  upgrade fail after 30 s with the actionable error. The schema is unchanged, and stopping the
  old server fixes it. A peer waiting on the advisory lock behind that winner fails the same way
  after about 60 s. That is caused by a third-party session, not by the concurrent HyperShell
  step, so R3 holds. Index builds inside the transaction briefly block `task` reads, once. The
  docs say so.
- **Turso.** Whether it supports `BEGIN IMMEDIATE` is unknown. If the `BEGIN` raises, the step
  falls back to a plain `BEGIN` plus the whole-step retry, and logs that at DEBUG. R3 is not
  claimed for Turso until it is tested.
- **Python transaction control.** If pysqlite's default moves away from legacy transaction
  control, the AUTOCOMMIT-plus-`BEGIN IMMEDIATE` recipe breaks. The SQLite atomicity unit test
  pins it on 3.11–3.14.
- **Python API callers** (`serve_from`, `submit_from`, …) bypass every helper; this predates the
  cycle. `docs/api` gets one sentence: call `ensuredb()` first.
- **Follow-up candidates (not built, for `issues/` at publish):**
  - Make the retry insert and the `retried` mark one commit, which closes the crash window. About
    5–8 lines in `__schedule_next_failed_tasks` ([06](research/06-retry-guard.md#follow-up-candidate)).
  - Eager mode with `bundlesize > 1` can return the same retry row twice in one `Task.next`
    (probe-confirmed, predates this cycle).
  - `provider = "postgresql"` fails at import with `Cannot provide 'file'`; only `postgres` works.
  - `hs server --initdb` is parsed but ignored.
  - `--no-db` server and cluster runs still touch the configured database.

## 6. Verification strategy

- **Red-first (P1).**
  - The fixture, the generator and `tests/test_upgrade_cli.py` land with source unchanged from
    the base `6c164d9`. Every new CLI test carries `@mark.xfail(strict=True,
    raises=AssertionError)`.
  - P1's verify runs `git diff --quiet 6c164d9 -- src` and then pytest with `-rxX`. Every test
    must report `XFAIL`; an `XPASS` fails the run under strict mode.
  - Precondition failures inside those tests use `pytest.fail`, never `assert`, so a broken setup
    cannot pass as the expected failure.
  - On the base, the very first command fails with exit 5 and `no such column: task.source`
    ([05](research/05-fixture-and-red-first.md#red-first-evidence)).
- **Green.** P2 and P4 remove the markers, and their verify asserts that no `xfail` remains in the
  file.
- **Drive the real CLI in a throwaway site:**
  - `.agents/factory/bin/temp_site.sh sh -c "cp \"\$UV_PROJECT/tests/fixtures/hypershell-2.8.3.db\"
    \"\$HYPERSHELL_DATABASE_FILE\" && uv run hs submit -- echo new && uv run hsx --restart -r 2
    -N2 --no-tls && uv run hs list --count"` prints `9`. `temp_site.sh` changes into the site
    directory, so the fixture path goes through `$UV_PROJECT`.
  - A second `uv run hs list --count` against the same site shows no upgrade line.
- **Every phase:** `uv run pytest` (full suite) green. **Docs phase:**
  `uv run sphinx-build -E -b html docs docs/_build` with no new warnings.
- **PostgreSQL (P6, external wait):** the `postgres` job is green on both matrix rows, with no
  skipped PostgreSQL tests.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
