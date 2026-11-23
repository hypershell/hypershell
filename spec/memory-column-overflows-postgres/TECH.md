---
slug: memory-column-overflows-postgres
title: On PostgreSQL, a task can declare 2 GiB of memory or more
kind: fix
appetite: small
status: in_progress
branch: fix/memory-column-overflows-postgres
base: develop
current_phase: P1
last_updated: '2026-11-23'
phases:
- id: P1
  name: Tests first, red where the fix is missing (strict xfail)
  status: pending
  satisfies:
  - R9
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/hs-memcol-p1.log"; uv run pytest -q -rxXs tests/test_memory_column.py
    > "$l" 2>&1 && HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE= uv run --extra postgres pytest -q -rxXs tests/test_upgrade_postgres.py
    -k "memory and not legacy" >> "$l" 2>&1 && uv run --extra postgres pytest -q --collect-only
    tests/test_upgrade_postgres.py tests/test_timescale.py -k memory >> "$l" 2>&1;
    r=$?; tail -n 20 "$l"; test $r -eq 0 && grep -q "xfailed" "$l" && ! grep -q "SKIPPED.*test_upgrade_postgres"
    "$l" && grep -q "test_upgrade_postgres.py::test_memory_legacy" "$l" && grep -q
    "test_timescale.py::test_memory_compressed" "$l" && grep -q "test_timescale.py::test_memory_old_release_refused"
    "$l"
- id: P2
  name: PostgreSQL widens task.memory in place; model, upgrade step, docs
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/hs-memcol-p2.log"; uv run pytest -q -rxXs tests/test_memory_column.py
    tests/test_data/test_upgrade.py tests/test_initdb.py tests/test_retry_declarations.py
    > "$l" 2>&1 && HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE= uv run --extra postgres pytest -q -rxXs tests/test_upgrade_postgres.py
    -k "memory and not legacy or fresh_schema" >> "$l" 2>&1; r=$?; tail -n 20 "$l";
    test $r -eq 0 && ! grep -q "SKIPPED.*test_upgrade_postgres" "$l" && ! grep -q
    "XFAIL.*test_upgrade_postgres" "$l" && ! grep -rqE "memory-column P2" tests &&
    grep -q "task.memory" docs/_include/database.rst && grep -q "task.memory" docs/_include/database_alt.rst
    && grep -q "WIDENED" AGENTS.md && grep -q "WIDENED" src/hypershell/data/upgrade.py
    && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv" uv run --python 3.12 --group docs
    sphinx-build -W --keep-going -q -E -b html docs "$TMPDIR/docs-html"
- id: P3
  name: TimescaleDB hypertable widened directly, or refused where TimescaleDB would
    refuse
  status: pending
  satisfies:
  - R7
  - R8
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: l="$TMPDIR/hs-memcol-p3.log"; uv run pytest -q -rxXs tests/test_memory_column.py
    > "$l" 2>&1 && uv run --extra postgres pytest -q -m unit tests/test_timescale.py
    tests/test_data/test_upgrade.py >> "$l" 2>&1 && HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
    HS_TEST_TIMESCALE= uv run --extra postgres pytest -q -rxXs tests/test_upgrade_postgres.py
    -k "memory and not legacy or fresh_schema" >> "$l" 2>&1; r=$?; tail -n 20 "$l";
    test $r -eq 0 && ! grep -q "XFAIL" "$l" && ! grep -rqE "memory-column P[23]" tests
    && grep -q "COLUMN_TYPE_VERSION" src/hypershell/data/upgrade.py && ! grep -q "enable_columnstore"
    src/hypershell/data/upgrade.py && git diff --quiet bbc55679 -- src/hypershell/data/__init__.py
    src/hypershell/data/timescale.py && grep -q "decompress_chunk" docs/_include/database.rst
    && grep -q "2.24.0" docs/_include/database.rst && UV_PROJECT_ENVIRONMENT="$TMPDIR/docs-venv"
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    "$TMPDIR/docs-html"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — On PostgreSQL, a task can declare 2 GiB of memory or more

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/memory-column-overflows-postgres/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md). The R-IDs are the contract. Q1 and Q2
  were ruled at the GOAL gate (2026-11-23). Q1 was amended at the plan gate (2026-11-23):
  TimescaleDB 2.22/2.23 with the columnstore on is refused, never toggled (PLAN §7).
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) and its briefs.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`
- `appetite`: `small | big`. It caps the phase count and the build-iteration budget (the circuit
  breaker).
- phase `status`: `pending | in_progress | done | blocked`
- `satisfies`: the GOAL R-IDs this phase delivers (the traceability anchor for `hs-review`).
- `depends_on`: the phase ids that must be `done` first.
- `parallel`: `false` for all three. P1 shares test modules with P2/P3, P2 edits `data/model.py`
  (coupled core), and P3 edits the same `upgrade.py` code as P2.
- `hammerable`: `false` for all three. They are about schema correctness on users' databases.
- `hill`: `uphill` → `crest` → `downhill`.
- `attempts`: the durable failed-verify counter, bumped by `set_phase.py --phase P<n> --record-attempt`.
- `verify`: the exact command that proves the phase.
- `review.cycle`: completed review passes.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution. The footgun checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) (§1, §3 and §12 here).
- One phase per `hs-build` invocation. Each phase is one atomic commit holding the code and the
  `TECH.md` state change, subject `[fix] Build memory-column-overflows-postgres P<n>: …`, signed.
  **No `Co-Authored-By` trailer.**
- Docs change in the **same commit** as the behaviour: P2 for PostgreSQL, P3 for TimescaleDB. No
  option is added, so the `share/` completions and the `*_usage.rst`/`*_help.rst` files stay as they
  are. The man pages are `hs-release`'s job.
- Docs, tests and notes use top-level commands only (`hs submit`, `hs list`, `hs info`,
  `hs initdb`, `hs cluster`), never `hs task …`. Never show a key on a command line: a test that
  needs one sets `HYPERSHELL_SERVER_AUTH`.
- Absurd memory values are out of scope (`issues/bounded-inputs.md`). Tests use 1 GB and 4 GB.
- **Host.** Python 3.14. Use `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-mem` and
  `UV_CACHE_DIR=/tmp/claude-501/uv-cache-mem` (synced with `--extra postgres`, 2026-11-23). Local
  PostgreSQL 18 is at `HS_TEST_POSTGRES` (in the verify lines) with `HS_TEST_TIMESCALE` empty. The
  verify commands need the sandbox off (local PostgreSQL socket; the docs venv under `$TMPDIR`).
  Run tests serially, never with `-n auto`.
- **CI only** (no Docker for the `hs283`/`hs290a1` templates, no TimescaleDB here): every
  `test_memory_legacy_*` case, the existing template-based `test_upgrade_postgres.py` cases, and
  every TimescaleDB integration case in `test_timescale.py`. CI's PostgreSQL job runs them on pg13,
  pg18, TimescaleDB 2.31.0 Community, 2.31.0 Apache-only and 2.24.0 Community. `hs-publish` reads
  that job before landing. No new test file joins the job's pytest line, because the new
  PostgreSQL/TimescaleDB cases go into files it already lists.
- **Strict-xfail convention (P1).** Each test that needs the fix carries
  `pytest.mark.xfail(strict=True, reason='memory-column P2')` or `'memory-column P3'`. Use
  `pytest.param(..., marks=…)` for parametrized rows. The phase that makes it pass removes the
  marker, and P2's and P3's verify lines grep that none remain.

---

## Phase P1 — Tests first, red where the fix is missing
**Satisfies:** R9 (the tests R1–R8 are graded by) · **Depends on:** —
**Goal:** every test this cycle needs is written and collected. Tests that need the fix are strict
xfail, and the ones that hold today (SQLite R2) pass. No source change.

- [ ] New `tests/test_memory_column.py` (default suite; module docstring names the cycle):
  - [ ] `@mark.unit` R1: compile `CreateTable(Task.__table__)` for the SQLite dialect and assert
        `memory INTEGER`. For the PostgreSQL dialect assert `memory BIGINT` (xfail P2).
  - [ ] `@mark.unit` the `_widen` decision table of PLAN §2.2, one parametrized row per state, each
        asserting the step list (SQL, `notice`, `message`) or the `SchemaUpgradeError` text.
        Plain/no hypertable and columnstore off → widen (xfail P2). Columnstore on with none
        compressed at ≥ 2.24.0 → widen; at < 2.24.0 → the 2.22/2.23 refusal naming 2.24.0 and the
        manual steps; any compressed chunk → the compressed refusal (xfail P3). No row yields a
        TimescaleDB statement (no `enable_columnstore` in any step). A narrower reflected type
        `INTEGER`/`SMALLINT` is due; `BIGINT` and `NUMERIC` are not (xfail P2).
  - [ ] `@mark.integration` R2 on SQLite (`temp_site`), expected to pass today. `hs submit -m 4GB --
        echo big` and a task file line `echo inline # HYPERSHELL: memory:4GB`. `hs list memory` and
        `hs info ID -x memory` print `4294967296`. `hs cluster --restart -N1 --no-tls` completes
        both. A `-m 4GB -- sh -c 'exit 3'` task with `-r 2` gives a retry row whose memory is
        `4294967296`.
- [ ] `tests/test_upgrade_postgres.py` (template-free cases run locally):
  - [ ] Helper `narrow(engine)`: `ALTER TABLE task ALTER COLUMN memory TYPE integer`. Helper
        `memory_type(engine)` from `information_schema.columns`.
  - [ ] `test_memory_fresh_database` R1/R2: `hs initdb --yes`, `bigint`, then the SQLite R2 drive
        against the server (xfail P2).
  - [ ] `test_memory_narrowed_widened` R3/R4/R5. Fresh database, narrow, `hs submit -m 1GB`, then
        `hs list --count`. The notice line comes before the `Widened column task.memory` line, each
        exactly once and at INFO. The column is `bigint` and the snapshot is unchanged. A second
        `hs list --count` is `NO_OUTPUT` and `schema_signature` is unchanged. Then `hs submit -m
        4GB` succeeds (xfail P2).
  - [ ] `test_memory_widening_rolls_back` R6: narrowed database, a broken `Step` appended after the
        plan (as in `test_failure_mid_step_rolls_back`) → `SchemaUpgradeError`, still `integer`,
        signature unchanged (xfail P2).
  - [ ] `test_memory_widening_lock_ceiling` R6: narrowed database, a holder session with an open
        read of `task`, slice 0.2 s, ceiling 1 s → the `WAIT_TABLES` error, still `integer`
        (xfail P2).
  - [ ] `test_memory_concurrent_widening` R6: narrowed database, 4 threads of `hs list --count`.
        All succeed, exactly one stderr holds `Widened column task.memory`, and the column ends
        `bigint` (xfail P2).
  - [ ] **CI only:** `test_memory_legacy_widened[hs283|hs290a1]` R3/R2. First use via `hs list
        --count` widens, the snapshot is unchanged, then `hs submit -m 4GB` and `hs list memory`
        (xfail P2). Extend `test_concurrent_first_use` to assert the widening is logged once (the
        same xfail on the new assertion's test). Teach `UPGRADE_LINE` the `Widened` and `Rewriting`
        prefixes.
- [ ] `tests/test_timescale.py` (**CI only**; `@needs_server @with_timescale`):
  - [ ] `test_memory_fresh_hypertable` R1/R2: `hs initdb --yes`, `bigint`, then the 4GB drive
        (xfail P2).
  - [ ] `test_memory_hypertable_widened` R7 direct. `hs initdb`, narrow, submit and run 3 tasks
        (rows in a chunk), then `hs list --count`: widened, `shape()` unchanged, `assert_converged`,
        `task_rows` unchanged (xfail P3; CI runs it only after P3, so its P2 pass on the
        2.24.0+ images never meets the strict marker).
  - [ ] `test_memory_old_release_refused` R7, the 2.22/2.23 refusal on real servers. Monkeypatch
        `hypershell.data.upgrade.COLUMN_TYPE_VERSION` to `(99, 0, 0)`, narrow, submit and run a
        task, then `hs list --count`. With the columnstore on (Community; `server_gates(engine)`),
        expect exit 5, stderr holding the refusal (the server's version, `2.24.0`, the manual steps,
        "schema is unchanged"), the column still `integer` and `shape()` unchanged. Without it
        (Apache-only), expect the forced gate to change nothing and the column to be widened
        (xfail P3).
  - [ ] `test_memory_compressed_refused` R7, Community only. Narrow, submit and run tasks,
        `compress_chunk` on every chunk, then `hs list --count`: exit 5, stderr holds the refusal
        (compressed chunks, `decompress_chunk`, "schema is unchanged"). The column stays `integer`
        and the chunks stay compressed. Then decompress them and rerun: widened (xfail P3).
- [ ] **Red check:** run the verify. The `xfailed` count is non-zero, and no case fails or
      unexpectedly passes. Record in the commit body which tests are xfail and which pass today.
- **Verify:** see frontmatter (default suite, local PostgreSQL cases not skipped, CI-only cases
  collected).
- **Touches:** `tests/test_memory_column.py`, `tests/test_upgrade_postgres.py`,
  `tests/test_timescale.py`.

## Phase P2 — PostgreSQL widens `task.memory` in place
**Satisfies:** R1, R2, R3, R4, R5, R6, R8 · **Depends on:** P1
**Goal:** a fresh PostgreSQL database holds 64-bit memory, and an old one is widened on first use
with its rows kept and the rewrite announced. Concurrent first uses widen once. The docs say so.

- [ ] `src/hypershell/data/model.py`: add `BIG_INTEGER = Integer().with_variant(BIGINT,
      'postgresql')` (import `BIGINT` with `SMALLINT`), and give `Task.memory` that type. Nothing
      else changes.
- [ ] `src/hypershell/data/upgrade.py` (PLAN §2.2):
  - [ ] `WIDENED` beside `RETIRED_UNIQUE`, with its comment, and added to `__all__`.
  - [ ] `Step.notice: Optional[str] = None`. `_apply_transactional` logs it at INFO immediately
        before the step's statement. `_apply_sqlite` never sees one.
  - [ ] `_widen(table, column, state)`. In P2 it returns the widen step when `state` is
        `None` or the columnstore is off, and raises `NotImplementedError` for the columnstore rows
        (P3 fills them). In P2 `_plan` always passes `None`.
  - [ ] In `_plan`'s PostgreSQL block, after the added columns and before the drops, a `WIDENED`
        pair whose reflected type is `INTEGER`/`SMALLINT` adds `_widen(...)`'s steps. The SQL is
        `ALTER TABLE {qualified} ALTER COLUMN {quoted} TYPE {model type compiled for the dialect}`.
  - [ ] `upgrade_schema`'s signature does not change (no `converges` flag; PLAN §7).
  - [ ] Module docstring: name the `WIDENED` exception (R8).
- [ ] Remove the `memory-column P2` markers. Run the verify. Then run
      `tests/test_upgrade_postgres.py -k "not legacy"` locally once more: the existing
      template-free tests (`test_fresh_schema`) are still green. Note in the commit body that the
      template cases are CI-only.
- [ ] Docs (PLAN §2.5, the PostgreSQL parts): `docs/_include/database.rst` Initialization gets the
      `.. versionchanged:: 2.9.0` clause, the exception sentence and the "rerun after a long
      rewrite" sentence. `docs/_include/database_alt.rst` gets one sentence. `AGENTS.md` § Data
      layer specifics names `WIDENED`. Read each against the behaviour.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/data/upgrade.py`,
  `tests/test_memory_column.py`, `tests/test_upgrade_postgres.py`, `tests/test_timescale.py`
  (markers only), `docs/_include/database.rst`, `docs/_include/database_alt.rst`, `AGENTS.md`.

## Phase P3 — TimescaleDB: widen directly, or refuse where TimescaleDB would refuse
**Satisfies:** R7, R8 · **Depends on:** P2
**Goal:** a hypertable `task` (from the `timescale` provider in 2.9.0a3–a9, or under the `postgres`
provider) is widened directly on first use by any command where TimescaleDB allows it: 2.24.0 or
later, or no columnstore. The columnstore and its settings and policy are left as they are. On
2.22/2.23 with the columnstore on, or with any compressed chunk, the step refuses before any
statement and changes nothing. Nothing toggles the columnstore, and `data/__init__.py` does not
change.

- [ ] `src/hypershell/data/upgrade.py`:
  - [ ] `COLUMN_TYPE_VERSION = (2, 24, 0)` with its source comment (timescale/timescaledb#8849,
        `check_alter_table_allowed_on_ht_with_compression`).
  - [ ] `_columnstore(conn, table)`: `None` without the extension or when `table` is not a
        hypertable, else `(version, enabled, compressed)` from `pg_extension`,
        `timescaledb_information.hypertables` and `timescaledb_information.chunks.is_compressed`,
        schema-aware like `_plan`. It parses the version with a local helper (do not import
        `timescale.py`; that would be circular).
  - [ ] `_widen` fills the columnstore rows of PLAN §2.2's table: widen at ≥
        `COLUMN_TYPE_VERSION`, otherwise the 2.22/2.23 refusal, and the compressed refusal first.
        The refusal texts are as in PLAN §2.2, and `_plan` passes the probe's state.
  - [ ] `_wrap`: TimescaleDB's `operation not supported on hypertables with compressed chunks` (a
        chunk compressed after the probe) maps to the same refusal text.
- [ ] `src/hypershell/data/__init__.py` and `timescale.py` stay unchanged (the verify checks this
      against `bbc55679`).
- [ ] Remove the `memory-column P3` markers. Run the verify. The TimescaleDB integration cases are
      CI-only: say so in the commit body, and `hs-publish` reads CI's `timescale`,
      `timescale-oss` and `timescale-floor` entries before landing.
- [ ] Docs: one paragraph in `docs/_include/database.rst`'s TimescaleDB section (PLAN §2.5), with
      the manual steps written as SQL, not as `hs` commands: `decompress_chunk` for compressed
      chunks, and the 2.24.0 update or the three statements for 2.22/2.23. Add the TimescaleDB
      clause to `AGENTS.md`'s bullet: widen where TimescaleDB allows it, otherwise refuse, never
      toggling the columnstore.
- **Verify:** see frontmatter: the unit decision table, the TimescaleDB unit tests, the local
  PostgreSQL cases still green, no xfail markers left, no `enable_columnstore` in `upgrade.py`,
  `data/__init__.py` and `timescale.py` unchanged, and the docs build.
- **Touches:** `src/hypershell/data/upgrade.py`, `tests/test_memory_column.py`, `tests/test_timescale.py`, `docs/_include/database.rst`,
  `AGENTS.md`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: a clean tree, on `branch`, with `base` reachable.
3. Execute every `[ ]` in the phase, consulting `PLAN.md` and `research/` for detail.
4. Run the phase's `verify:` command. Never advance on a checkbox alone.
5. If reality diverges, amend this file (state via `set_phase.py`; note it in the commit body).
   STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase` and `--touch`. Make one `[fix]` commit, then stop
   and report.
