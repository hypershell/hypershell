# 03 — Where the pass hangs, and how it is tested (codebase)

Read against `feature/timescale` at `caf7007` (base `origin/develop` `d996579`).

## The seam

- `data/__init__.py`: `initdb()` = `upgradedb()` (+ SQLite optimize); `truncatedb()` = `drop_all` +
  `create_all`; `checkdb()` = `has_table('task')` check + `upgradedb()`; `ensuredb(auto_init)` picks
  `initdb` for SQLite or `auto_init`, else `checkdb`. Every `--initdb` path (`hs initdb`,
  `hs cluster --initdb`, `hs server --initdb`, `hs submit --initdb`) reaches `initdb()`
  (`submit.py` L1374, `cluster/__init__.py` L544, `server.py` L1370 via `ensuredb`). So hanging the
  pass inside `initdb()` and `truncatedb()` covers R2/R4 without touching any app module.
- `DATABASE_PROVIDER` (module constant) is the configured alias, so `DATABASE_PROVIDER in
  TIMESCALE_ALIASES` is the guard; for every other provider the two functions stay byte-identical in
  behavior (R2's last sentence).
- `data/core.py` pops `schema`, `echo`, `connect_args`, `pragmas` from `config.database`; **every
  other key is forwarded to the driver as a URL parameter** (`DatabaseURL.parameters`, documented in
  `docs/_include/database.rst`). An R6 `[database.timescale]` table must therefore be popped there,
  exactly like `pragmas`, or psycopg receives `timescale=...` as a conninfo keyword and fails to connect.
- The placeholder warning lives at `data/core.py` L232–233, after the `uuid_utils` import guard.
  The guard (R8) stays; only the `log.warning` goes.
- `schema` (popped in `core.py`, exported) must qualify the table everywhere in the pass:
  `regclass` text via `engine.dialect.identifier_preparer.format_table(Task.__table__)` with the
  schema applied, and `hypertable_schema = :schema` in information-view probes
  (`tests/test_upgrade_postgres.py::test_schema_qualified` is the precedent).

## Reusable machinery in `data/upgrade.py`

- `SCHEMA_UPGRADE_LOCK_KEY` + `_lock_postgres(conn)` (transaction-scoped advisory lock),
  `_set_lock_timeout(conn, s)`, `_waiting(bind, attempt)` (30 s slices up to a 900 s ceiling,
  logging each wait) and `_waiting_for(what)`. Running the pass's probe-then-act in one transaction
  under the same advisory key serializes it with peer upgrades and peer passes, and bounds its waits
  on a live server's row locks with the same contract the docs already describe. These are
  module-private names; importing them from a sibling module in `data/` is acceptable, or promote
  them to `__all__` (no behavior change).
- `_sqlstate(error)` / `_message(error)` read the driver's SQLSTATE/message.

## Errors and exit status

- `SchemaUpgradeError` is in `get_shared_exception_mapping()` → `handle_exception` (critical line, no
  traceback) → `exit_status.runtime_error`. cmdkit dispatches by `isinstance`
  (`cmdkit/app.py`), and all 16 apps use the shared mapping. A `TimescaleError(SchemaUpgradeError)`
  defined in `data/timescale.py` is therefore handled everywhere with **no edit to `core/`**.
- `InitDBApp.exceptions` maps `OperationalError` first (runtime_error, no traceback). A dropped
  connection from `CREATE EXTENSION` without preload surfaces as `OperationalError`; the pass should
  catch it on its own connection and re-raise as `TimescaleError` with the preload guidance.
- Import-time config failures follow `display_critical(...)` + `sys.exit(exit_status.bad_config)`
  (`core.py`; the `uuid7`/`turso` guards use `runtime_error` for missing dependencies).

## Model facts the pass relies on

- `Task.id` is `UUID` on PostgreSQL (`POSTGRES_UUID(as_uuid=False)`), the lone primary key; no other
  unique index or constraint remains on `task` after #69 (`RETIRED_UNIQUE` dropped on upgrade).
  `client_id` is a nullable UUID; `tag` is `JSON`. Indexes: `index_tasks_unscheduled`,
  `index_tasks_retries`, `index_tasks_previous_id`, `index_tasks_next_id`, `index_tasks_source` —
  all non-unique, created by `create_all`/the schema step before conversion.
- The pass needs **no** model change (GOAL: no schema change). `data/model.py` is not touched.

## Tests

- `tests/conftest.py` sets SQLite via `temp_site`; the general suite stays SQLite (R9).
- `tests/test_upgrade_postgres.py`: gated on `HS_TEST_POSTGRES`; fixtures `admin` (autocommit
  engine), `pg_database(template=None)` (throwaway `CREATE DATABASE … [TEMPLATE x]`, dropped
  `WITH (FORCE)`), `pg_site(name, schema=None)` (sets `HYPERSHELL_DATABASE_*`, provider `postgres`).
  The new scenario module should reuse these by import (or a small shared helper) and set
  `HYPERSHELL_DATABASE_PROVIDER=timescale`.
- CLI tests shell out (`tests.main`/`main_lines`), so each `hs` invocation is a fresh process that
  re-reads config; in-process tests of the pass must call a function taking an explicit engine and
  schema, not the import-time globals.
- `uuid-utils` is in the `dev` dependency group, so CI already mints UUIDv7 (the timescale guard
  passes; no workflow change for the extra). The `hs283` template holds **UUIDv4** ids (2.8.3
  without the extra), which makes it the natural non-empty, non-v7 plain-table case for R5.
- Only `@mark.unit` / `@mark.integration` are valid markers.

## Local verification

No container runtime or PostgreSQL client exists on the planning host (`docker`, `podman`,
`pg_isready`, `psql` all absent, 2026-11-02). Local gates are the SQLite suite, unit tests of the
pass's pure logic, workflow YAML parsing and the Sphinx build; the TimescaleDB gate is CI on the
draft PR.
