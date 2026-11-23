# PLAN — On PostgreSQL, a task can declare 2 GiB of memory or more

> **Status:** Approved at the plan gate, with one ruling (§7) · **Last updated:** 2026-11-23
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`Task.memory` becomes `BIGINT` on PostgreSQL through a dialect variant, so SQLite's schema does not
change. The in-place upgrade step gains one enumerated widening, `WIDENED = {('task', 'memory')}`,
next to `RETIRED_UNIQUE`. It runs in the step's existing transaction, under the existing advisory
lock and lock ceiling, and logs an INFO notice before the rewrite. On a TimescaleDB hypertable the
step probes the columnstore. Where the release allows it (2.24.0 and later, no compressed chunk), the
step widens directly. Where TimescaleDB would refuse, it refuses first with an actionable
`SchemaUpgradeError`, before running any statement and with the schema unchanged: on 2.22/2.23 with
the columnstore on, and on any release with a compressed chunk. It never toggles the columnstore
(plan gate, §7). Three phases: tests first, then the PostgreSQL widening, then the TimescaleDB probe
and refusals. The appetite stays small.

## 2. Design

### 2.1 Model (`src/hypershell/data/model.py`)

- Add `BIG_INTEGER = Integer().with_variant(BIGINT, 'postgresql')` beside `INTEGER` and
  `SMALL_INTEGER` (`:240-241`). Import `BIGINT` from `sqlalchemy.dialects.postgresql`.
- `Task.memory` (`:436`) uses `BIG_INTEGER`. Nothing else in the model changes. `retry_columns`
  already carries `memory`.
- SQLite's created DDL stays `memory INTEGER` (R1). A fresh PostgreSQL database gets `memory BIGINT`
  from `create_all`, which also covers the `timescale` provider: its table is created plain and then
  converted.

### 2.2 The widening (`src/hypershell/data/upgrade.py`)

**Constants**, beside `RETIRED_UNIQUE`:

```python
# Columns the models declare wider than earlier releases created them. On PostgreSQL the step
# widens each in place (a rewrite of its table under the table's exclusive lock); SQLite's
# INTEGER already holds 64 bits. These are the only columns the step ever retypes.
WIDENED: Final[FrozenSet[Tuple[str, str]]] = frozenset({('task', 'memory')})

# The first TimescaleDB release that changes a column's type on a hypertable with the
# columnstore enabled and no compressed chunk (timescale/timescaledb#8849, 2.24.0).
COLUMN_TYPE_VERSION: Final[Tuple[int, int, int]] = (2, 24, 0)
```

**`Step`** gains `notice: Optional[str] = None`. `_apply_transactional` logs it at INFO immediately
before `conn.exec_driver_sql(step.sql)` (R4). The step is retried after each lock-wait slice, so the
notice repeats after each "Waiting for …" line. That repetition is accurate, because the rewrite has
not started yet. The after-commit `message` is logged as every step's is.

**Planning.** In `_plan`'s existing `if dialect.name == 'postgresql':` block, after the added
columns and before the retired-constraint drops, for each `(table, column)` in `WIDENED` that the
table holds:

1. Read the reflected type from the `get_columns` result the loop already fetched. Change it only
   when it is a narrower integer (`INTEGER` or `SMALLINT`). Any other type plans nothing: `BIGINT`,
   or a type widened by hand. That is R5: no new query and no lock on a current schema.
2. Probe the hypertable state with `_columnstore(conn, table)`, a new read-only helper. It returns
   `None` unless the `timescaledb` extension is in `pg_extension` and `table` is a hypertable in
   `timescaledb_information.hypertables`. Otherwise it returns `(version, enabled, compressed)`,
   where `compressed` is the number of `timescaledb_information.chunks` rows with `is_compressed`.
   The extension check comes first so the probe works on a plain server.
3. Decide with `_widen(table, column, state)`, a new pure function that the unit tests drive.
   The rule is the same under both providers:

| State | Steps / outcome |
|---|---|
| no hypertable, or columnstore off (Apache-only, or `columnstore = false`) | widen |
| columnstore on, `compressed > 0` | `SchemaUpgradeError` before any statement, schema unchanged |
| columnstore on, none compressed, version ≥ `COLUMN_TYPE_VERSION` | widen (columnstore left on) |
| columnstore on, none compressed, version < `COLUMN_TYPE_VERSION` (2.22.x/2.23.x) | `SchemaUpgradeError` before any statement, schema unchanged |

The steps the table names:

- **widen.** SQL: `ALTER TABLE {qualified} ALTER COLUMN {col} TYPE BIGINT` (the model column's type
  compiled for the dialect). Notice: `Rewriting table task to widen task.memory to BIGINT (this can
  take a while on a large table)`. Message: `Widened column task.memory to BIGINT`.
- **compressed-chunk refusal.** It names the table, the column, the count of compressed chunks, and
  why: TimescaleDB cannot change a column's type while chunks are in the columnstore, and HyperShell
  does not decompress data without the user's say. It gives the manual steps: convert the chunks
  back to the rowstore (`SELECT decompress_chunk(c) FROM show_chunks('task') c`, with the policy
  paused if it could convert them again first), then run any `hs` command again. It ends with "The
  schema is unchanged."
- **2.22/2.23 refusal.** It names the table, the column and the TimescaleDB version, and says why:
  this release cannot change a column's type while the columnstore is enabled. It gives two ways
  out. The first is to upgrade TimescaleDB to 2.24.0 or later (`ALTER EXTENSION timescaledb UPDATE`
  after installing it on the server) and run any `hs` command again. The second is the manual steps
  (`ALTER TABLE task SET (timescaledb.enable_columnstore = false)`, `ALTER TABLE task ALTER COLUMN
  memory TYPE bigint`, then `hs initdb` to restore the columnstore and its settings). It ends with
  "The schema is unchanged."

The probe and the decision run in `_check`, which is lock-free, so a refusal costs no lock. They run
again under the advisory lock in `_apply_transactional`, as every plan does. A chunk compressed by
the policy between the probe and the `ALTER` is refused by TimescaleDB itself inside the
transaction (`0A000`, research 02). The rollback and `_wrap` then raise `SchemaUpgradeError` with
the schema unchanged. `_wrap` maps that one TimescaleDB message to the refusal text above, so the
error is actionable either way.

**No new parameter.** `upgrade_schema(bind, metadata=None)` keeps its signature. The step never
issues a TimescaleDB statement, so `upgradedb()`, `initdb()` and `checkdb()` in
`src/hypershell/data/__init__.py` are unchanged (§2.4).

**Module docstring.** It names the exceptions: the retired uniqueness and the `WIDENED` columns,
which are widened on PostgreSQL by a table rewrite under the table's exclusive lock (R8).

### 2.3 Lock ceiling and concurrent first use (R6)

The lock ceiling stays as it is: `LOCK_WAIT_SLICE` 30 s and `LOCK_WAIT_CEILING` 900 s.

- **Own wait.** The `ALTER` waits for `task`'s ACCESS EXCLUSIVE lock behind other sessions, at most
  one slice per attempt (`lock_timeout`). Then it rolls back, logs `Waiting for another session to
  release its lock on the tables (Ns elapsed)` and retries. After 900 s it raises the existing
  `WAIT_TABLES` error with the schema unchanged.
- **The rewrite.** `lock_timeout` does not bound a statement that already holds its lock, so the
  rewrite runs to completion. A site's own `statement_timeout`, a full disk, or any other failure
  ends it with a `DBAPIError`. The transaction rolls back and `_wrap` raises `SchemaUpgradeError`:
  rows and schema unchanged.
- **Two processes at once.** Both plan the widening without a lock: the uncommitted `ALTER` does not
  block reflection, and reflection still reads `INTEGER` (probed on PostgreSQL 18, research 01). One
  takes the advisory lock and widens. The other waits on the advisory lock in slices, logging
  `Waiting for a concurrent schema upgrade to finish` at INFO each time. After the commit it re-plans
  under the lock, finds `BIGINT` and nothing to do (`trace`), and proceeds. The column is widened
  once, and only that process logs it.
- **Past the ceiling.** If a rewrite takes longer than 900 s, a waiting peer raises the existing
  `WAIT_UPGRADE` error. The widening in the first process still completes, and a rerun of the
  peer's command succeeds. R8's docs say so in one sentence. A separate ceiling for the widening was
  rejected (§3).

### 2.4 The hypertable's shape is never touched (`src/hypershell/data/__init__.py` unchanged)

- The step widens directly or refuses, and the direct `ALTER` leaves the columnstore settings and
  the policy as they were. R7's "keep its columnstore settings and policy" holds by construction.
- `initdb()` still runs `timescale.converge` after `upgradedb()`, as today. `checkdb()` still runs
  `upgradedb()` alone, so the current path adds no converge, no lock and no new query (R5).

### 2.5 Docs (R8)

- `docs/_include/database.rst`, Initialization. Add a `.. versionchanged:: 2.9.0` with one clause:
  on PostgreSQL the upgrade widens `task.memory` so a task can declare more than 2 GiB. Rewrite the
  "only adds what is missing … never drops or rewrites existing tasks" sentence to name the
  exception. On PostgreSQL the step also widens `task.memory` by rewriting the `task` table, keeping
  every row and value, under its exclusive lock. That can take minutes on a large table and is
  announced in the log. Add one sentence on a waiter that gives up after 15 minutes behind a long
  rewrite: rerun it.
- `docs/_include/database.rst`, TimescaleDB section (P3). Add one paragraph covering three cases.
  On TimescaleDB 2.24.0 or later the hypertable is widened in place, with the columnstore left on.
  On 2.22/2.23 with the columnstore enabled the upgrade refuses, naming the update to 2.24.0 or the
  manual steps. With compressed chunks it refuses, naming the manual steps (`decompress_chunk`).
  The schema is unchanged when it refuses.
- `docs/_include/database_alt.rst`: the same exception, one sentence.
- `AGENTS.md` § Data layer specifics, the schema-upgrade bullet: "only adds … the one removal is …"
  becomes "only adds …, with two PostgreSQL-only exceptions, the retired `UNIQUE` constraints
  (`RETIRED_UNIQUE`) and the `WIDENED` columns (`task.memory`, widened by a rewrite, an INFO notice
  first)". Add one clause: on a TimescaleDB hypertable the step widens only where TimescaleDB
  allows it and otherwise refuses, never toggling the columnstore.
- No CLI option changes, so the `*_usage.rst`/`*_help.rst` files and the `share/` completions stay as
  they are. The man pages are `hs-release`'s job. Docs use top-level commands only (`hs submit`,
  `hs list`, `hs info`, `hs initdb`) and never show a key on a command line.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | §2.1 `BIG_INTEGER` variant: `BIGINT` on PostgreSQL (both providers), `INTEGER` on SQLite unchanged |
| R2   | §2.1, so the insert is accepted; the retry copies `memory` through `retry_columns` unchanged |
| R3   | §2.2 `WIDENED` + widen step in `_plan`, applied in the existing transaction |
| R4   | §2.2 `Step.notice` logged at INFO before the statement; after-commit message at INFO |
| R5   | §2.2 widening due only on `INTEGER`/`SMALLINT`; no probe, no lock, nothing above `trace` otherwise; §2.4 `checkdb` unchanged |
| R6   | §2.3: existing transaction, slices, ceiling, `_wrap`; re-plan under the advisory lock |
| R7   | §2.2 `_columnstore` probe + `_widen` decision table (widen directly, or refuse before any statement); §2.4 shape untouched |
| R8   | §2.5 docs, `AGENTS.md`, module docstring |
| R9   | §6 and TECH P1: tests across the default suite, the PostgreSQL suite (templates) and the TimescaleDB suite |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research and again after this design was drafted.

- **High-blast-radius file `data/model.py`.** One column's type changes through a dialect variant.
  No lifecycle predicate (§1), no query, no `Task` classmethod changes. `parallel: false` for the
  phase that edits it.
- **§1 Task lifecycle.** Not touched. The widening rewrites rows with every value kept; no state
  column changes.
- **§3 Retry model.** `Task.retry_columns` still carries `memory`, and the column belongs to
  `retry_columns` only (the pinning test stays green). R2's retry check proves the copy at the new
  width.
- **§12 Project conventions.** The docs change in the same commit as the behaviour (P2 for
  PostgreSQL, P3 for TimescaleDB). Each change is marked `.. versionchanged:: 2.9.0`. Every new test
  is tagged `@mark.unit` or `@mark.integration`. P1's tests also use the builtin strict
  `xfail`, which `--strict-markers` accepts. No dependency floor changes, and no option is added.
- **AGENTS.md § Data layer specifics.** The step "only adds …" except enumerated cases. This design
  adds exactly one enumerated case in the same shape as `RETIRED_UNIQUE` and updates that bullet in
  the same commit.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `Step.notice`, a second log point inside the transaction | R4 asks for a line *before* the rewrite; every other message is logged after commit | Logging before `_apply` would announce a rewrite a concurrent peer may already have done, and would print on refusals |
| The TimescaleDB probe lives in `upgrade.py`, not `timescale.py` | `_plan` calls it; `timescale.py` imports from `upgrade.py` | A lazy import inside `_plan` hides a cycle |
| Lock ceiling unchanged for a rewrite that may outlast it | Keeps one wait policy for the whole step | A per-step ceiling is a new knob for a once-per-database event whose error is already actionable and whose rerun succeeds |

## 4. Rabbit holes (resolved)

- **Does TimescaleDB refuse `ALTER COLUMN TYPE` with the columnstore on but no compressed chunk?**
  It refuses on 2.22.x and 2.23.x and allows it from 2.24.0. Every release refuses once a chunk is
  compressed. The source is `src/process_utility.c`,
  `check_alter_table_allowed_on_ht_with_compression`, at tags 2.23.0, 2.24.0 and 2.31.0, plus
  timescale/timescaledb PR #8849 (issue #8840, CHANGELOG 2.24.0). See
  [`research/02-timescale-alter-type.md`](research/02-timescale-alter-type.md).
- **Where the widening sits, and what a concurrent peer sees.** It sits in `_plan`'s PostgreSQL
  block beside `RETIRED_UNIQUE`. Reflection is not blocked by an uncommitted `ALTER` and reads the
  old type (probed on PostgreSQL 18, 2026-11-23). See
  [`research/01-upgrade-step.md`](research/01-upgrade-step.md).
- **Does converge follow every upgrade?** No, only in `initdb`. That made turning the columnstore off
  and back on fragile, and the plan gate replaced it with a refusal (§7).

## 5. Risks & open questions

- **2.22/2.23 Community users of the alpha `timescale` provider do a manual step**, or update
  TimescaleDB, before this release can use their database. The error says how. This is the ruled
  trade (§7).
- **The 2.22/2.23 refusal has no real-version test.** CI has no image older than 2.24.0. P3 raises
  `COLUMN_TYPE_VERSION` above the server's version on the 2.24.0 and 2.31.0 Community entries, so the
  probe and the refusal run against real servers. That TimescaleDB itself refuses on those releases
  is confirmed from the 2.23.0 source only. Our refusal comes first either way.
- **A rewrite longer than 15 minutes** makes concurrent first uses give up with the existing error.
  The docs say to rerun. Processes of an earlier release that are using the database are blocked for
  the rewrite's duration (ACCESS EXCLUSIVE). This is inherent, and it is announced by R4's notice and
  in the docs.
- **A site `statement_timeout`** on the role can cancel the rewrite on a large table. The result is
  `SchemaUpgradeError` with the schema unchanged, and the driver's message names the timeout.
- **Local evidence is partial.** No Docker and no TimescaleDB are available here, so the
  legacy-template cases and every TimescaleDB case are CI-only. A template-free narrowed database
  (fresh `hs initdb`, then `memory` set back to `integer`) proves R3–R6 locally on PostgreSQL 18.
- **`hypershell-mcp`'s strict expected failure** (`memory_overflows`) will start passing against a
  server on this release. Flipping it is that repository's change (a non-goal).

## 6. Verification strategy

Tests come first (P1), as strict `xfail` where the fix is missing, and P2/P3 remove the markers.
Local runs are serial, with no `-n auto`, and use the sandbox off for the local PostgreSQL socket.

**Default suite: `tests/test_memory_column.py` (new; runs everywhere).**
- `@mark.unit` R1: the `task` DDL compiled for SQLite holds `memory INTEGER` and for PostgreSQL
  `memory BIGINT`.
- `@mark.unit` R3/R5/R7: the `_widen` decision table, every row, through a fake state. That covers
  the steps' SQL, notices and messages, both refusals' text, and the no-op on `BIGINT`, `numeric` and
  absent.
- `@mark.integration` R2 on SQLite (a guard, expected to pass today). `hs submit -m 4GB` and a task
  file line with `# HYPERSHELL: memory:4GB`. `hs list memory` and `hs info ID -x memory` show
  `4294967296`. `hs cluster --restart --no-tls` runs both to completion. A failing 4GB task's retry
  row carries `4294967296`.

**PostgreSQL suite: `tests/test_upgrade_postgres.py`.** Runs locally with `HS_TEST_POSTGRES`; CI
runs it on pg13, pg18 and the three TimescaleDB entries.
- Local and CI (template-free): fresh database R1/R2, the same drive as SQLite. Narrowed database
  R3/R4/R5: notice before message, each once, rows kept, the second run silent, then a 4GB submit.
  R6 failure mid-step (a broken step appended after the widening): still `integer`, signature
  unchanged. R6 lock ceiling (a session holding `task`, slice 0.2 s, ceiling 1 s): the `WAIT_TABLES`
  error, still `integer`. R6 concurrent: 4 workers all succeed and one logs the widening.
- **CI only** (`hs283`, `hs290a1` templates; Docker builds them): first use widens a 2.8.3 and a
  2.9.0a1 database with rows kept, then R2. `test_concurrent_first_use` also asserts the widening is
  logged exactly once. `UPGRADE_LINE` learns `Widened`/`Rewriting`.

**TimescaleDB suite: `tests/test_timescale.py`. CI only** (no TimescaleDB here; CI runs 2.31.0
Community, 2.31.0 Apache-only and 2.24.0 Community).
- Fresh hypertable R1/R2.
- Narrowed hypertable (rows in chunks), first use through `hs list --count`: widened directly, the
  columnstore still on, `assert_converged`, rows kept.
- Community only, gate forced above the server's version (the 2.22/2.23 refusal), first use
  through `hs list --count`: exit 5, the refusal text naming 2.24.0 and the manual steps, still
  `integer`, `shape()` unchanged. On the Apache-only entry the same forced gate still widens, since
  there is no columnstore.
- Community only, a compressed chunk: exit 5, the refusal text, still `integer`, the chunk still
  compressed. Then the documented manual step (decompress) and a rerun: widened.

**Docs:** the build passes with `-W`, plus greps for the `versionchanged` and the exception wording.

## 7. Plan gate (2026-11-23)

Ruled by the maintainer at the plan gate, answering §5's converge risk. On TimescaleDB 2.22.x/2.23.x
with the columnstore enabled, the step **refuses** with an actionable `SchemaUpgradeError`: it gives
the manual steps or says to upgrade TimescaleDB to 2.24.0 or later, and leaves the schema unchanged.
There is no columnstore off/on toggling anywhere in the upgrade path, no `converges=` flag, and no
converge call from `checkdb`. 2.24.0 and later (columnstore on, nothing compressed) widen directly.
Any release with a compressed chunk is refused, as planned. The `postgres` provider follows the same
rule. This supersedes GOAL Q1 option A for the 2.22/2.23 case only; GOAL.md records it as a dated
Clarification, with R7 reworded and R-IDs unchanged. This PLAN and TECH.md P3 are revised to match.
The plan is otherwise approved by the foreman, gate judge under the maintainer's mandate.
