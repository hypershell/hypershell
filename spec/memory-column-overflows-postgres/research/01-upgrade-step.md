# 01 — Where the widening sits in the upgrade step, and how it locks

Codebase read on `fix/memory-column-overflows-postgres` at `bbc55679` (2026-11-23). Sequential
research (small appetite; the fan-out is required because `data/model.py` is on the
high-blast-radius list and GOAL Q1 hands the TimescaleDB mechanism to the plan).

## The step today (`src/hypershell/data/upgrade.py`)

- `upgrade_schema(bind, metadata)` runs `_check` (lock-free plan, one reflection) and, only when
  the plan has steps, `_apply`. `_apply_transactional` (PostgreSQL) opens one transaction, sets
  `lock_timeout` to one slice, takes `pg_advisory_xact_lock(SCHEMA_UPGRADE_LOCK_KEY)`, **re-plans**,
  runs every `Step.sql`, commits, and logs each `Step.message` at INFO after the commit (at `trace`
  for a fresh database).
- `_plan(conn, metadata)` is the only place that diffs models against the live schema. It already
  carries one PostgreSQL-only exception: `RETIRED_UNIQUE`, a module constant of `(table, column)`
  pairs, turned into `DROP CONSTRAINT` steps inside the per-table loop. It refuses unsafe additions
  by raising `SchemaUpgradeError` *before any statement runs* (the UNIQUE-index refusal).
- Lock waits: every wait is bounded by `LOCK_WAIT_SLICE` (30 s); `_waiting` rolls back, logs
  `Waiting for … (Ns elapsed)` at INFO, and retries until `LOCK_WAIT_CEILING` (900 s), then raises
  `SchemaUpgradeError` naming what it waited on (`WAIT_UPGRADE` or `WAIT_TABLES`). Any other
  `DBAPIError` becomes `_wrap(error)`: "Cannot upgrade database schema: … The schema is unchanged."
- `lock_timeout` bounds *waits for a lock*, not a statement's run time. A rewrite that holds its
  lock runs to completion however long it takes (unless the site sets `statement_timeout`).

## Placement

`WIDENED: Final[FrozenSet[Tuple[str, str]]] = frozenset({('task', 'memory')})` beside
`RETIRED_UNIQUE`, consumed in the same `if dialect.name == 'postgresql':` block of `_plan`, after the
added columns and before the drops (so a legacy 2.8 database gets one transaction: add columns,
widen, drop retired constraints, create indexes). A widening is due when the reflected type is a
narrower integer (`INTEGER`, `SMALLINT`); any other reflected type (already `BIGINT`, or a type
changed by hand that holds the range) plans nothing, so R5's current path stays one reflection and
no lock.

`Step` needs one optional field, a notice logged at INFO just before its statement runs inside
`_apply_transactional` (R4: "before the table rewrite begins"). The after-commit message stays as
every other step's.

## Concurrency (R6) — probe on local PostgreSQL 18, 2026-11-23

Session A: `ALTER TABLE task ALTER COLUMN memory TYPE BIGINT` left uncommitted (holds ACCESS
EXCLUSIVE). Session B with `lock_timeout = 2s`: SQLAlchemy `inspect()` `has_table`, `get_columns`,
`get_indexes`, `get_unique_constraints` on `task` all returned in 0.02 s, **not blocked**, and
reported `memory` as `INTEGER` (the committed catalog). So a second process's lock-free `_check`
plans the widening too, goes to `_apply`, waits on the advisory lock in 30 s slices (one INFO line
each), and once the first commits, its re-plan under the lock finds `BIGINT` and nothing to do
(`trace`: "upgraded by another session"). This is exactly the existing path
`test_concurrent_first_use` / `test_race_replans_under_lock` / `test_waits_for_slow_peer_upgrade`
exercise for added columns; those tests pick the widening up automatically once `_plan` emits it.

Ceiling: a waiter gives up after 900 s with the existing `WAIT_UPGRADE` error, its own schema view
unchanged; the widening in the first process still completes. Raising the ceiling for one step was
considered and rejected (one more knob; the error is already actionable and a rerun succeeds).

## Who runs the step, and who runs `converge`

`src/hypershell/data/__init__.py`: `initdb()` = `timescale.prepare` → `upgradedb()` →
`timescale.converge` (timescale provider). `checkdb()` = `upgradedb()` only. `checkdb` is the
first-use path of every PostgreSQL command without `--initdb` (`hs submit`, `hs list`, `hs info`,
`hs wait`, `hs update`, `hs server`, `hs cluster`, the relay). So the GOAL's "the converge step that
already follows the upgrade" holds only for `hs initdb` and `--initdb` launches; on every other
first use nothing turns a columnstore back on unless `checkdb` is taught to.

`timescale.py` imports from `upgrade.py` (`_waiting`, `_lock_postgres`, …), so a TimescaleDB probe
used by `_plan` must live in `upgrade.py` (the reverse import would be circular).

## Model

`src/hypershell/data/model.py:240-241`: `INTEGER = Integer()`,
`SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')`. The same pattern,
`BIG_INTEGER = Integer().with_variant(BIGINT, 'postgresql')`, widens `Task.memory` (`:436`) on
PostgreSQL and leaves SQLite's created DDL `INTEGER` byte for byte (R1). `Task.retry_columns` already
carries `memory` (invariants §3); nothing else in `src/` casts or bounds it.
