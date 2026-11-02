---
slug: schema-upgrade-step
title: 'Open 2.8 databases in 2.9: an idempotent schema-upgrade step'
kind: fix
appetite: small
status: in_progress
branch: fix/schema-upgrade-step
base: develop
current_phase: P5
last_updated: '2026-11-02'
phases:
- id: P1
  name: 'Red: real-2.8.3 fixture, generator, and failing CLI tests (tests only)'
  status: done
  satisfies:
  - R9
  - R1
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet 6c164d9 -- src && ! grep -a -q "$(hostname)" tests/fixtures/hypershell-2.8.3.db
    && uv run pytest -rxX tests/test_upgrade_cli.py
- id: P2
  name: 'The upgrade step: model diff, refusal, locked apply, wired into initdb/checkdb'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R7
  - R9
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -rxX && .agents/factory/bin/temp_site.sh sh -c "cp \"\$UV_PROJECT/tests/fixtures/hypershell-2.8.3.db\"
    \"\$HYPERSHELL_DATABASE_FILE\" && uv run hs submit -- echo new && uv run hsx --restart
    -r 2 -N2 --no-tls && test \$(uv run hs list --count) -eq 9 && test \$(uv run hs
    list --remaining --count) -eq 0"
- id: P3
  name: 'PostgreSQL: CI service job, legacy template builder, gated tests (run A:
    authoring)'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -rs tests/test_upgrade_postgres.py && uv run python -c "import
    yaml; yaml.safe_load(open('.github/workflows/tests.yml'))" && bash -n tests/fixtures/make-legacy-postgres.sh
    && uv run pytest
- id: P4
  name: 'Side paths: hs initdb --truncate/--rotate and the rotated-partition view'
  status: done
  satisfies:
  - R1
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: '! grep -q xfail tests/test_upgrade_cli.py && uv run pytest'
- id: P5
  name: Docs and the amended retry-model invariant (after group-advance-retry-gate
    lands)
  status: pending
  satisfies:
  - R7
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: grep -q 'def query_retryable' src/hypershell/data/model.py && ! grep -nE
    'UNIQUE\**( chain|\.)' AGENTS.md .agents/factory/invariants.md && grep -q 'query_retryable'
    .agents/factory/invariants.md && uv run pytest && uv run sphinx-build -E -b html
    docs docs/_build
- id: P6
  name: 'PostgreSQL CI evidence (run B: external wait on a pushed branch)'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R9
  depends_on:
  - P3
  - P4
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: test "$(gh run list --branch fix/schema-upgrade-step --workflow tests.yml
    --limit 1 --json headSha,conclusion --jq '.[0].headSha + " " + .[0].conclusion')"
    = "$(git rev-parse HEAD) success"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Open 2.8 databases in 2.9: an idempotent schema-upgrade step

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground truth: read it with
`uv run python .agents/factory/bin/next_phase.py spec/schema-upgrade-step/TECH.md`. The per-phase
checklists below are the work. `hs-build` executes the next actionable phase and runs its
`verify:` command. It updates state with `uv run python .agents/factory/bin/set_phase.py …` and
makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). R1–R9 are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and briefs 01–06.

## Frontmatter field reference

See [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md). Two points
matter for this cycle:

- `verify` uses YAML folded scalars (`>-`), so each command is one shell line.
- **P3 and P6 are one piece of work split at an external wait.** P3 (run A) authors the
  PostgreSQL job and tests and proves them locally: they skip without a server. P6 (run B) only
  checks the CI evidence once the branch is pushed.
- P4 and P5 depend on P2 only, so they stay actionable while P6 waits.
- P5 has a second external precondition: `group-advance-retry-gate` landed on `develop`, and this
  branch rebased onto it.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution. The invariants this cycle touches are
  [`invariants.md`](../../.agents/factory/invariants.md) §1, §3, §4, §10 and §12 (PLAN §3).
- One phase per `hs-build` invocation. Each phase is one atomic commit with the code and this
  file's state change. The subject reads `[fix] Build schema-upgrade-step P<n>: …`, with **no
  `Co-Authored-By` trailer**.
- Source comments are declarative and carry **no spec ids** (`R5`, `P2`). Log levels:
  - INFO: a change to an existing database.
  - `trace`: the no-op path and initialization of an empty database.
  - Never `debug` on a path the existing exact-stderr tests drive.
- **This repo is public.** The fixture must carry no real hostname: the generator pins it, and
  P1's verify checks for it. Paths in docs and READMEs are generic.
- **Rebase expectation.** `group-advance-retry-gate` edits `data/model.py` (`increment_group`,
  `query_retryable`, `count_retryable`, `select_failed`) and `server.py` (`load_bundle`). This
  cycle touches only the chain-column and `part` declarations and the module-level indexes in
  `data/model.py`, and does not touch `server.py`. The overlap is the *Retry model* paragraph in
  `AGENTS.md`, edited in P5 after the rebase.

---

## Phase P1 — Red: real-2.8.3 fixture, generator, and failing CLI tests

**Satisfies:** R9, R1 (tests only) · **Depends on:** —

**Goal:** a committed SQLite database that real hypershell 2.8.3 made, and CLI tests that fail on
the base `6c164d9` because the build cannot open it. No `src/` change.

- [x] Add `tests/fixtures/make-2.8.3-db.sh`. Start from the recipe in
  [research/05](research/05-fixture-and-red-first.md#generator-recipe) and make these changes:
  - Take the Python interpreter (with `hypershell==2.8.3`) and an output directory as arguments.
  - Isolate every `HYPERSHELL_*` variable and use a free port.
  - Pin `TZ=UTC` and `socket.gethostname`.
  - Use the `run.in` task file: 3 `echo done-N`, plus `sh -c 'exit 3'`.
  - Run `hsx … --max-retries 1 --no-tls`, then the two `hs submit -t kind:pending …`.
  - **Finish by refusing to keep the file** if `hostname` appears in it.
- [x] Generate the database. Use a `uv venv --python 3.12` under `$TMPDIR`, then
  `uv pip install hypershell==2.8.3`. Commit `tests/fixtures/hypershell-2.8.3.db`.
- [x] Add `tests/fixtures/README.md` with the provenance:
  - the hypershell, Python, SQLAlchemy and cmdkit versions;
  - the exact command;
  - the file's sha256;
  - what the file contains (7 task rows: done, a failed chain, pending; 1 client row);
  - "never open read-write; tests copy it".
- [x] Add the shared helpers to `tests/__init__.py`, or a small `tests/fixtures.py` module that
  pytest does not collect:
  - `copy_fixture(temp_site)`
  - `make_half_upgraded(path)`: applies the frozen 2.9.0a1 `CREATE TABLE source …` and
    `CREATE INDEX index_source_lookup …` through raw `sqlite3`
  - `snapshot(path)`: the original 30 `task` columns keyed by `id`, plus `client`, through raw
    `sqlite3`
- [x] Add `tests/test_upgrade_cli.py` (`@mark.integration`). Every test is
  `@mark.xfail(strict=True, raises=AssertionError)`, and its precondition failures use
  `pytest.fail`. The tests (PLAN §2.6):
  - R9 end to end;
  - R1 per listed command, on an untouched copy and a half-upgraded copy;
  - R2 snapshot equality and new-column values;
  - R3: a second command logs no upgrade line and the schema is unchanged; INFO lines name
    `task`; the 4-process smoke test in rollback-journal and WAL mode;
  - R7: the SQLite `UNIQUE` constraints remain and the retry still works;
  - `hs initdb --truncate --yes` and `--rotate --yes` on a 2.8 copy;
  - partitions: the 2.8.3 file as `local.1`, plus a column-order mismatch case.
- [x] Confirm by hand on the base that the first command exits 5 with `no such column:
  task.source`. Record that in the commit body.
- **Verify:** `git diff --quiet 6c164d9 -- src`, no hostname in the fixture, and
  `uv run pytest -rxX tests/test_upgrade_cli.py`. Every test must be XFAIL; an XPASS fails the run.
- **Touches:** `tests/fixtures/*`, `tests/test_upgrade_cli.py`, `tests/__init__.py` (helpers).

## Phase P2 — The upgrade step

**Satisfies:** R1, R2, R3, R4, R5, R7, R9 · **Depends on:** P1

**Goal:** every command upgrades a 2.8 or half-upgraded database in place on first use, on its own
lock-serialized connection. A current schema is a silent, lock-free no-op, and unsafe columns are
refused before any DDL.

- [x] Add `SchemaUpgradeError` to `src/hypershell/core/exceptions.py` and map it in
  `get_shared_exception_mapping` to `exit_status.runtime_error`, placed before the `Exception`
  catch-all.
- [x] Create `src/hypershell/data/upgrade.py` (PLAN §2.1):
  - **`_plan(conn, metadata)`:** a fresh `inspect(conn)` per call, honouring `table.schema`. It
    plans missing tables (`CreateTable(if_not_exists=True)` plus their indexes), missing columns
    (`ALTER TABLE {format_table} ADD COLUMN {CreateColumn}`), PostgreSQL-only
    `RETIRED_UNIQUE` drops (by reflected name), and missing indexes
    (`CreateIndex(if_not_exists=True)`). Order: tables, columns, drops, indexes.
  - **The six refusal rules**, each raising `SchemaUpgradeError` with the table, the column, the
    reason and the fix.
  - **`_lock(conn)`:**
    - SQLite: AUTOCOMMIT, `PRAGMA busy_timeout = 60000`, `BEGIN IMMEDIATE`. If `BEGIN IMMEDIATE`
      is unsupported (Turso), fall back to `BEGIN`.
    - PostgreSQL: `set_config('lock_timeout', '30s', true)`, then
      `pg_advisory_xact_lock(SCHEMA_UPGRADE_LOCK_KEY)`. The key literal is
      `-4139901733377976503`, with a comment deriving it from
      `sha256(b'hypershell:schema-upgrade')`.
    - Both timeouts are module constants.
  - **`upgrade_schema(bind, metadata=Entity.metadata) -> list[str]`:**
    1. Lock-free plan. If it is empty, log at `trace` and return `[]`.
    2. Otherwise lock, re-plan, execute and commit.
    3. Roll back in a `finally`, restoring SQLite's busy timeout.
    4. Retry the whole step once on a duplicate or already-exists error.
    5. Wrap lock timeouts (`database is locked`, `55P03`) and other DDL `OperationalError` /
       `ProgrammingError` in `SchemaUpgradeError`, with actionable text.
  - **Logging:** INFO per executed step, naming the table, when `task` existed before the step.
    `trace` otherwise.
- [x] Change `src/hypershell/data/model.py`. Declarations only; no retry-gating method changes:
  - `previous_id` / `next_id` lose `unique=True`. A declarative comment above them gives the
    hypertable and one-schema rationale, and says that the Scheduler's `retried` filter, not the
    schema, keeps one retry per row.
  - Add `index_tasks_previous_id` and `index_tasks_next_id` beside the other `index_tasks_*`.
  - `part` gains `server_default=text('0')` and keeps `default=0`.
- [x] Change `src/hypershell/data/__init__.py`:
  - `upgradedb()` (exported) calls `upgrade_schema(engine)`.
  - `initdb()` calls `upgradedb()` in place of `create_all`, keeping `PRAGMA optimize` afterwards.
  - `checkdb()` keeps its `has_table('task', schema=schema)` refusal, then calls `upgradedb()`.
- [x] Add `tests/test_data/test_upgrade.py` (`@mark.unit`, per-test engines on fixture copies,
  PLAN §2.6):
  - the R3 race, its mutation check and the no-op spy (no lock, no DDL events);
  - R4: generic add through a copied `MetaData`, each refusal rule with a byte-identical
    `sqlite_master`, and the CLI one-line error with no traceback file;
  - R5: fresh `create_all` and `truncatedb` give no unique constraints and both chain indexes;
  - the ordering guard: a repeat `checkdb()` with a dirty `Session` does not wait;
  - SQLite atomicity: a forced mid-step failure leaves the schema unchanged;
  - the opt-in `HS_TEST_REGEN_FIXTURE` provenance check.
- [x] In `tests/test_upgrade_cli.py`, remove `xfail` from every test **except** the
  `--truncate`/`--rotate` and partition tests. Those stay red until P4.
- **Verify:** the full suite (`uv run pytest -rxX`, where only the P4 tests remain XFAIL), then the
  throwaway-site drive: fixture copy, `hs submit`, `hsx --restart -r 2`, 9 rows, 0 remaining.
- **Touches:** `src/hypershell/data/upgrade.py` (new), `data/__init__.py`, `data/model.py`,
  `core/exceptions.py`, `tests/test_data/test_upgrade.py`, `tests/test_upgrade_cli.py`.

## Phase P3 — PostgreSQL: CI job, template builder, gated tests (run A)

**Satisfies:** R1–R6, R9 on PostgreSQL · **Depends on:** P2

**Goal:** everything PostgreSQL needs is authored and proven to skip cleanly locally. The evidence
comes in P6.

- [x] Add a `postgres` job to `.github/workflows/tests.yml` (PLAN §2.5, the sketch in
  [research/01](research/01-postgres-testing.md#proposed-ci-job)):
  - a service container with `matrix.include` of `postgres:13` and `postgres:18`, and a
    `pg_isready` health check;
  - the same SHA-pinned checkout and setup-uv actions;
  - env `HS_TEST_POSTGRES` / `HS_TEST_POSTGRES_TEMPLATES`;
  - a template-builder step, then
    `uv run --python 3.14 --extra postgres pytest -v -rs tests/test_upgrade_postgres.py`;
  - a commented placeholder row for a future `timescale/timescaledb` image.
- [x] Add `tests/fixtures/make-legacy-postgres.sh`:
  - `uv venv --python 3.12` venvs for `hypershell[postgres]==2.8.3` and `==2.9.0a1`;
  - `hs283` built by 2.8.3 (`hs initdb --yes`, the same task file and `hsx … --max-retries 1`,
    two `hs submit`s);
  - `hs290a1` = `CREATE DATABASE … TEMPLATE hs283`, then 2.9.0a1 `hs initdb --yes`;
  - admin SQL through `docker exec "$PG_CONTAINER" psql`;
  - provider `postgres`, never `postgresql`.
- [x] Add `tests/test_upgrade_postgres.py`:
  - module `pytestmark = [mark.integration, mark.skipif(not os.getenv('HS_TEST_POSTGRES'), …)]`;
  - a missing template under a set gate → `pytest.fail`;
  - `pg_database` (AUTOCOMMIT admin engine, `CREATE DATABASE … TEMPLATE`,
    `DROP … WITH (FORCE)`) and `pg_site` (provider, host, port, user, password and database
    env; never `_FILE`);
  - tests per PLAN §2.6: R1 through `checkdb` without `--initdb` on `hs283` and `hs290a1`; R2
    including `data_type = 'uuid'` and `part = 0`; R3 concurrent CLIs plus the in-process race;
    R4 refusal and rollback; R5 fresh; R6 constraints dropped, indexes present, second run a
    no-op; R9 end to end; the schema-qualified variant; the third-session lock-timeout test.
- **Verify:**
  - the PostgreSQL module runs and skips (`-rs` lists the skip reason);
  - the workflow YAML parses;
  - the builder script passes `bash -n`;
  - the full suite stays green.
- **Touches:** `.github/workflows/tests.yml`, `tests/fixtures/make-legacy-postgres.sh`,
  `tests/test_upgrade_postgres.py`, `tests/conftest.py` (PostgreSQL fixtures, if not
  module-local).
- **External wait:** this phase cannot prove PostgreSQL behaviour. Once P3–P5 are done, the
  foreman pushes the branch and P6 reads the result.

## Phase P4 — Side paths: `hs initdb --truncate/--rotate` and rotated partitions

**Satisfies:** R1, R9 · **Depends on:** P2

**Goal:** the two `hs initdb` branches that query `Task` before any helper, and the partition view,
work on upgraded databases.

- [x] In `src/hypershell/data/__init__.py`, `InitDBApp.run` calls `checkdb()` at the top of the
  `--truncate` and `--rotate` branches. `--vacuum` and `--backup` are unchanged.
- [x] In `src/hypershell/task.py`, `auto_union_sqlite` builds the temp view with an explicit
  column list from `Task.__table__.columns`, in model order:
  - `main.task` selects them by name.
  - For each attached `part_N`, it reads `PRAGMA part_N.table_info(task)` and selects `NULL AS
    <col>` for each missing column, or `0 AS part` for `part`.
  - Identifiers are quoted with the dialect preparer, because `group` is a keyword.
  - Partition files are never written.
- [x] Remove the remaining `xfail` markers in `tests/test_upgrade_cli.py`.
- **Verify:** no `xfail` left in `tests/test_upgrade_cli.py`, and the full suite is green.
- **Touches:** `src/hypershell/data/__init__.py`, `src/hypershell/task.py`,
  `tests/test_upgrade_cli.py`.

## Phase P5 — Docs and the amended retry-model invariant

**Satisfies:** R7, R8 (and R9's "docs build clean") · **Depends on:** P2 · **Precondition:**
`group-advance-retry-gate` has landed on `develop` and this branch is rebased onto it. If
`Task.query_retryable` is absent, STOP and report; don't write the R8 text against the old code.

- [ ] In `AGENTS.md`:
  - Replace the *Retry model* paragraph's first sentence with the PLAN §2.7 text. Keep the
    sibling's `attempts == max_retries + 1` and `CANCEL_STATUS` sentences.
  - Amend the `data/model.py` footgun bullet: drop "`previous_id`/`next_id` are UNIQUE", and state
    that one retry per row rests on the single Scheduler plus the `retried` filter, not the
    schema, and that pre-2.9 SQLite databases are the exception.
  - Add a "Data layer specifics" bullet on `upgradedb()`: it runs from `initdb`/`checkdb`, adds
    only, logs INFO per change, and new columns must be nullable or carry a `server_default`.
  - Fix `InitDBApp`'s location at line 82.
- [ ] `.agents/factory/invariants.md` §3 gets the same amendment, split into two bullets per
  [research/06](research/06-retry-guard.md#draft-invariant-text).
- [ ] `docs/_include/database.rst` §Initialization (and `database_alt.rst`) describe:
  - the upgrade in place on first use, on both providers;
  - additions only, plus the one PostgreSQL constraint removal;
  - a brief exclusive lock on PostgreSQL;
  - pre-2.9 SQLite databases keeping the chain constraints, worded per PLAN §5 Q1. Default:
    "no effect on behaviour in normal operation".
- [ ] `INITDB_HELP` and the `InitDBApp` docstring get one line on the upgrade on first use. In the
  same commit, regenerate `docs/_include/initdb_*.rst` and `share/man/man1/{hs,hsx,hyper-shell}.1`
  (AGENTS.md §Docs, invariants §12). The completions don't change.
- [ ] `docs/api/data.rst` gets `upgradedb` and the sentence "API callers run `ensuredb()` first".
- **Verify:**
  - the sibling's `query_retryable` exists;
  - no "UNIQUE chain" or "are UNIQUE." remains in `AGENTS.md` or `invariants.md`;
  - `invariants.md` names `query_retryable`;
  - the full suite is green;
  - `sphinx-build -E` has no new warnings.
- **Touches:** `AGENTS.md`, `.agents/factory/invariants.md`, `docs/_include/database*.rst`,
  `docs/_include/initdb_*.rst`, `docs/api/data.rst`, `share/man/man1/*.1`,
  `src/hypershell/data/__init__.py` (help text only).

## Phase P6 — PostgreSQL CI evidence (run B)

**Satisfies:** R1–R6, R9 on PostgreSQL · **Depends on:** P3, P4, P5

**Goal:** the pushed branch's `Tests` workflow is green at HEAD, with both `postgres` matrix rows
green. Because a missing template fails rather than skips, green means every PostgreSQL test ran.

- [ ] **External wait.** The foreman pushes `fix/schema-upgrade-step`; `hs-build` never pushes.
- [ ] Read the run (`gh run view <id> --log-failed` on red). If PostgreSQL behaviour differs from
  the plan, fix it in a new commit on this phase and ask for another push. The likely spots are
  `set_config` for `lock_timeout`, the SQLSTATEs under a race, and constraint names under a
  schema. Each such fix is a `TECH.md` amendment noted in the commit body.
- **Verify:** the latest `tests.yml` run on the branch is for `git rev-parse HEAD` and concluded
  `success`.
- **Touches:** whatever the evidence requires. Expect `src/hypershell/data/upgrade.py` and
  `tests/test_upgrade_postgres.py` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase. Statuses are authoritative; the
   `current_phase` pointer is reconciled against them.
2. Pre-flight: clean tree, on `branch`, `base` reachable. For P5, also check the sibling
   precondition.
3. Execute every `[ ]` in the phase, consulting `PLAN.md` and `research/` for detail.
4. Run the phase's `verify:` command. Never advance on a checkbox alone.
5. Amend this file freely if reality diverges: regenerate the frontmatter with `set_phase.py` and
   note the amendment in the commit body. STOP and escalate only on a **`GOAL.md`
   contradiction**.
6. Mark the phase `done`, advance `current_phase` and `--touch`. Make one `[fix]` commit, then
   stop and report.
