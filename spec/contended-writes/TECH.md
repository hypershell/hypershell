---
slug: contended-writes
title: 'Contended writes: the retried mark and Task.cancel_all land when contended
  on a hypertable; concurrent first submits share the reserved source row'
kind: fix
appetite: small
status: in_progress
branch: fix/contended-writes
base: develop
current_phase: P2
last_updated: '2026-11-14'
phases:
- id: P1
  name: 'Tests first: R5''s fault-injection, real-race and two-session tests and the
    red list (tests only)'
  status: done
  satisfies:
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; git diff
    --quiet 92af9c1 -- src .github && test -z "$(git diff --name-only 92af9c1 -- .
    ':!spec' ':!tests')" && for t in test_contended_retry_mark_lands test_contended_cancel_lands
    test_reserved_source_concurrent_first_use; do grep -q "^def $t(" tests/test_contended_write.py
    || exit 1; done && test "$(grep -cvE '^#|^$' spec/contended-writes/red.txt)" -ge
    13 && RED_BASE_REV=92af9c1 RED_LIST="$PWD/spec/contended-writes/red.txt" HS_TEST_POSTGRES=$pg
    HS_TEST_TIMESCALE= sh spec/relay-hardening/drive/red_check.sh && sh spec/contended-writes/drive/rest_green.sh
    && for ts in community ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/contended-writes/drive/rest_green.sh
    -rs tests/test_contended_write.py tests/test_writeback_postgres.py || exit 1;
    done
- id: P2
  name: CI red at the tests-first commit (external wait on the draft PR)
  status: pending
  satisfies:
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python spec/contended-writes/drive/ci_check.py red spec/contended-writes/evidence/ci-red
    --code-rev "$(git log -1 --format=%H --grep='^\[fix\] Build contended-writes P1:')"
- id: P3
  name: The retried mark commits with its retry rows, on the re-run pattern
  status: done
  satisfies:
  - R1
  - R2
  - R5
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; uv run
    pytest -q -p no:cacheprovider tests/test_contended_recovery.py -k 'not contended_cancel'
    && uv run pytest -q -p no:cacheprovider tests/test_retry_gate.py tests/test_groups.py
    tests/test_group_order.py tests/test_cancel.py tests/test_restart.py tests/test_writeback.py
    tests/test_server_fsm.py tests/test_zone_dispatch.py tests/test_dispatch_staged.py
    tests/test_zone_model.py && for ts in community ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts
    uv run pytest -q -p no:cacheprovider -rs tests/test_contended_write.py tests/test_writeback_postgres.py
    tests/test_zone_postgres.py -k 'not reserved_source' || exit 1; done && RED_BASE_REV=92af9c1
    RED_LIST="$PWD/spec/contended-writes/red.txt" HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=
    sh spec/relay-hardening/drive/red_check.sh && sh spec/contended-writes/drive/retry_drive.sh
    sqlite && HS_TEST_POSTGRES=$pg sh spec/contended-writes/drive/retry_drive.sh timescale
- id: P4
  name: Task.cancel_all/cancel on the re-run pattern, and a conflict-safe reserved
    source row
  status: done
  satisfies:
  - R3
  - R4
  - R5
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; uv run
    pytest -q -p no:cacheprovider tests/test_contended_recovery.py tests/test_source.py
    tests/test_cancel.py tests/test_dispatch_staged.py tests/test_writeback.py tests/test_topology_server.py
    tests/test_submit.py && for ts in community ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts
    uv run pytest -q -p no:cacheprovider -rs tests/test_contended_write.py tests/test_writeback_postgres.py
    || exit 1; done && RED_BASE_REV=92af9c1 RED_LIST="$PWD/spec/contended-writes/red.txt"
    HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE= sh spec/relay-hardening/drive/red_check.sh
    && for b in sqlite sqlite-283; do sh spec/contended-writes/drive/concurrent_submit.sh
    $b 20 || exit 1; done && HS_TEST_POSTGRES=$pg sh spec/contended-writes/drive/concurrent_submit.sh
    postgres 20
- id: P5
  name: AGENTS.md and the invariants drop the two exceptions; full local evidence
  status: pending
  satisfies:
  - R6
  - R5
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; ! grep
    -q 'killed between the retry insert' AGENTS.md && ! grep -q 'kill between the
    retry insert' .agents/factory/invariants.md && ! grep -q 'hypertable-contended-scheduler-and-cli-writes'
    AGENTS.md .agents/factory/invariants.md && grep -q 'issues/hs-update-contended-writes.md'
    AGENTS.md && grep -q 'cancel_all' .agents/factory/invariants.md && grep -q 'retry
    mark' AGENTS.md && grep -q 'retry mark' .agents/factory/invariants.md && uv run
    pytest -q -p no:cacheprovider && for ts in community ''; do HS_TEST_POSTGRES=$pg
    HS_TEST_TIMESCALE=$ts uv run pytest -q -p no:cacheprovider -rs tests/test_contended_write.py
    tests/test_writeback_postgres.py tests/test_zone_postgres.py || exit 1; done &&
    RED_BASE_REV=92af9c1 RED_LIST="$PWD/spec/contended-writes/red.txt" HS_TEST_POSTGRES=$pg
    HS_TEST_TIMESCALE= sh spec/relay-hardening/drive/red_check.sh
- id: P6
  name: CI green on the fix (external wait on the draft PR)
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on:
  - P2
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python spec/contended-writes/drive/ci_check.py green spec/contended-writes/evidence/ci-green
    --code-rev "$(git log -1 --format=%H -- . ':!spec')"
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — Contended writes: the retried mark and Task.cancel_all land when contended; concurrent first submits share the reserved source row

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/contended-writes/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs and probes.

## Frontmatter field reference

As in [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the touched invariants are
  [`invariants.md`](../../.agents/factory/invariants.md) §1, §2, §3 and §12 (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build contended-writes P<n>: …` (P2's verify finds the P1 commit by that subject).
  **No `Co-Authored-By` trailer.**
- **Every verify runs with the sandbox off**: they reach the local PostgreSQL 18 + TimescaleDB 2.32
  (`HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`) and the suite
  starts servers. Set `UV_CACHE_DIR` under `$TMPDIR`. The 29 template failures of
  `tests/test_upgrade_postgres.py` (no Docker-built templates locally) are expected; no verify runs it.
  Nor `tests/test_timescale.py`: the one local server offers TimescaleDB to both `HS_TEST_TIMESCALE`
  settings, which two of its server-identity checks refuse (3 local failures on `92af9c1`); CI runs it.
- **The base is `92af9c1`** (develop `6da633d` + the GOAL). `red_check.sh` (from
  `spec/relay-hardening/drive/`, reused as is) exports its `src` and runs each [`red.txt`](red.txt)
  line against it; every phase's verify re-runs it, so the tests keep proving the defect.
- No CLI or prose-docs change in this cycle, so no `docs/_include`, `share/` or Sphinx build.

### Where each test is expected red

| Test (module) | Base, locally (SQLite / PG 18 / TS 2.32) | Base, CI | After the fix |
|---|---|---|---|
| Fault injection: mark lands, mark exhausted, mark skips cancelled / deleted / marked row (`test_contended_recovery.py`) | red (`StaleDataError`) | red, `tests (3.x)` | green |
| Stop between insert and mark (`test_contended_recovery.py`) | red (assertion) | red, `tests (3.x)` | green |
| Cancel lands, cancel exhausted (`test_contended_recovery.py`) | red (`StaleDataError`) | red, `tests (3.x)` | green |
| Uncontended mark/cancel cost; cancel of a gone row (`test_contended_recovery.py`) | green (guards) | green | green |
| Two sessions, SQLite fresh/upgraded × `<direct>`/`<stdin>` (`test_source.py`) | red (assertion) | red, `tests (3.x)` | green |
| Two sessions, PostgreSQL fresh (`test_contended_write.py`) | red on PG 18 and TS 2.32 | red, every `PostgreSQL (…)` job | green |
| Two sessions, PostgreSQL `hs283` upgraded (`test_contended_write.py`) | skipped (no template) | red, every `PostgreSQL (…)` job | green |
| Real race: mark, cancel (`test_contended_write.py`) | **green** (PG 18, TS 2.32 re-check) | red on `timescale`, `timescale-oss` (2.31.0) only | green |

`red.txt` lists the locally red ones; [`ci-red.txt`](ci-red.txt) the CI-only ones; `ci_check.py`
derives each job's expected failures from both.

---

## Phase P1 — Tests first (tests only)
**Satisfies:** R5 · **Depends on:** —
**Goal:** every R5 test in the tree, red on the base for the defect, with nothing else red; no
`src/` change. This commit is what the foreman pushes to the draft PR for P2.

- [x] `tests/test_contended_recovery.py`: a driver-level injector (PLAN §2.5) on the per-test engine
      (`do_execute`/`do_executemany` dialect events): `lose(task_id, count, meanwhile=None)` makes
      that task's UPDATE of `task` match 0 rows by executing a 0-row UPDATE in its place (an
      executemany drops that task's parameter set and runs the rest); `meanwhile` commits another
      writer's change first on a separate connection; it records `(kind, task id)` per parameter set.
      Identify the mark by its SET of `retried` and the cancel by its SET of `exit_status` with
      `completion_time`, so the dispatch stamp and the result write are never lost by mistake.
- [x] Mark tests (`Task.next(limit=…, attempts=2)` on failed rows set up through dispatch and a
      written-back failure, as the module's helpers do): lands on a re-run (one retry row,
      `retried`/`next_id` set, the retry returned, DEBUG re-run line, no WARNING); exhausted (no retry
      row for that row, `retried` still false, one WARNING naming the task and `retry mark`, the other
      failed row in the batch retried, and the next round retries it once); skips a row cancelled,
      deleted, or marked `retried` (with a `next_id`) meanwhile, with no retry row and no WARNING.
- [x] `test_stop_between_retry_insert_and_mark_leaves_no_retry`: a `before_cursor_execute` hook
      raises a `BaseException` subclass at whichever of {INSERT INTO task, UPDATE of `retried`} comes
      second; `Session.remove()`; then no row names the failed row as `previous_id`, it is not
      `retried`, and the next `Task.next` retries it exactly once.
- [x] Cancel tests: lands on a re-run (`Task.cancel_all(ids) == ids`, rows cancelled); exhausted
      (`cancel_all` returns the other ids, one WARNING naming the task and `cancel`, that row
      unchanged, the rest cancelled and committed; `Task.cancel(id) is False` under the same loss).
      Guards: uncontended mark and cancel each cost one UPDATE per row and no SELECT re-check;
      `cancel_all` with a gone id raises `StaleDataError` and cancels nothing.
- [x] `tests/test_source.py`: `test_reserved_source_concurrent_first_use[fresh|upgraded-direct|stdin]`
      (`@mark.unit`): a per-test SQLite engine bound as `task_db` does (fresh: `create_all`;
      upgraded: a copy of `tests/fixtures/hypershell-2.8.3.db` through `upgrade_schema`); two threads
      call `Source.reserved`, a `before_cursor_execute` barrier (`threading.Barrier(2, timeout=…)`)
      on `INSERT INTO source` holds each until both have missed; each thread returns its row id or
      the exception and calls `Session.remove()`. Both return the reserved id; one row in `source`.
- [x] `tests/test_contended_write.py`: `test_reserved_source_concurrent_first_use[fresh|upgraded]`
      (fresh: `contended_db`; upgraded: the `hs283` template under the `postgres` provider, `hs initdb
      --yes`, skipped unless `HS_TEST_POSTGRES_TEMPLATES` lists `hs283`); `test_contended_retry_mark_lands`
      (holder `UPDATE task SET client_host = …` on the failed row; worker `Task.next(limit=1,
      attempts=2)`; after release: no error, the one retry row returned and stamped, `retried` set,
      exactly one row naming it); `test_contended_cancel_lands` (holder as above on an unscheduled row;
      worker `Task.cancel_all([id])`; after release: no error, the row cancelled). Assert outcomes
      only, never the new return values, so they pass on the base locally.
- [x] Reconcile [`red.txt`](red.txt) and [`ci-red.txt`](ci-red.txt) with the node ids as written
      (renaming is fine; dropping a line needs a note in PLAN §5).
- [x] Record the red: the `red_check.sh` output (every line RED) goes in the commit body.
- **Verify:** see frontmatter (no `src`/`.github` change; the three PostgreSQL tests exist; ≥13
  listed; `red_check.sh` all red on the base; the suite and both contended server modules otherwise
  green on SQLite, PG 18 and TS 2.32).
- **Touches:** `tests/test_contended_recovery.py`, `tests/test_source.py`, `tests/test_contended_write.py`,
  `spec/contended-writes/red.txt`, `spec/contended-writes/ci-red.txt`.

## Phase P2 — CI red at the tests-first commit (external wait)
**Satisfies:** R5 · **Depends on:** P1
**Goal:** CI shows the R5 tests red before the fix, each on the jobs expected and nowhere else.

- [ ] **External wait.** The foreman pushes `fix/contended-writes` **at the P1 commit** and opens the
      draft PR into `develop`; `hs-build` never pushes. Nothing past P1 is pushed until this run
      has completed and is recorded (the workflow cancels a superseded run on the same ref). P3 may
      be built locally meanwhile.
- [ ] The foreman records the run under `spec/contended-writes/evidence/ci-red/`:
      `gh run view <id> --json databaseId,headSha,conclusion,jobs > run.json` and
      `gh run view <id> --log-failed > failed.log`.
- [ ] If a job differs from the table above (e.g. the 2.31 real race does not miss on the base),
      stop and report: the foreman decides (PLAN §5).
- **Verify:** `ci_check.py red` on the recorded evidence, with the P1 commit as `--code-rev`.
- **Touches:** `spec/contended-writes/evidence/ci-red/`.

## Phase P3 — The retried mark commits with its retry rows (R1, R2)
**Satisfies:** R1, R2, R5 · **Depends on:** P1
**Goal:** a contended mark lands or leaves the row retryable with no retry row; mark and retry rows
commit together; the mark tests go green.

- [x] `src/hypershell/data/model.py`: one private helper building the row-state retry filters
      (`retried IS FALSE`, `exit_status` not NULL, not 0, not `CANCEL_STATUS`) from the table's
      columns; `query_retryable` uses it (SQL unchanged; `tests/test_retry_gate.py` and
      `tests/test_groups.py` stay green unedited).
- [x] `__row_statements`: condition `'retryable'` (match and lookup: `id` plus those filters; read
      `id`). `__write_rows`: `retryable: bool = False` (no row found → DEBUG `Ignored {path} for task
      ({id}) - task gone or no longer awaiting retry`, skip; found → re-run), `each=` per-row values
      (same keys for every id, else `ValueError`), `commit: bool = True` (False: no commit; still
      rollback and re-raise on error). Docstrings say all three.
- [x] `__schedule_next_failed_tasks` as PLAN §2.2: mint retries; marks through `__write_rows(...,
      'retry mark', 'task left to retry later', retryable=True, each=…, commit=False, prefix='m_')`;
      `Session.add_all` the landed ones; one commit; rollback and re-raise on error; return the landed
      retries in selection order. `Entity.update_all` stays (used by `hs update`).
- [x] The mark tests and the stop-between test pass; the cancel tests still fail (P4).
- **Verify:** see frontmatter (the recovery module minus the cancel tests; the retry/group/restart/
      writeback/dispatch modules; the server-database modules minus the source test on PG 18 and TS
      2.32; `red_check.sh` still all red on the base; `retry_drive.sh` on SQLite and TimescaleDB:
      every failed row exactly one retry row, eager and not).
- **Touches:** `src/hypershell/data/model.py`.

## Phase P4 — `Task.cancel_all`/`cancel` and the reserved source row (R3, R4)
**Satisfies:** R3, R4, R5 · **Depends on:** P3
**Goal:** a contended cancel lands or is reported; concurrent first use of a reserved source row
never fails.

- [x] `Task.cancel_all(ids) -> List[str]` and `Task.cancel(id) -> bool` as PLAN §2.3 (`__write_rows`,
      `'cancel'`, `'task not cancelled'`, `prefix='c_'`); docstrings name the return and the gone-row
      `StaleDataError`.
- [x] `Source.reserved` as PLAN §2.4: lookup; on a miss `INSERT … ON CONFLICT (id) DO NOTHING` for
      the bind's dialect, commit (rollback and re-raise on error), re-read; docstring says why.
- [x] Every test in `red.txt` and `ci-red.txt` now passes locally (the upgraded PostgreSQL case
      stays skipped without a template).
- **Verify:** see frontmatter (the recovery, source, cancel and submit modules; both server-database
      modules on PG 18 and TS 2.32; `red_check.sh`; `concurrent_submit.sh` 20 rounds each on fresh
      SQLite, 2.8.3-upgraded SQLite and fresh PostgreSQL, all rounds clean; before: 3/20, 2/10, 7/10).
- **Touches:** `src/hypershell/data/model.py`.

## Phase P5 — AGENTS.md and the invariants (R6); full local evidence
**Satisfies:** R6, R5 · **Depends on:** P4
**Goal:** the constitution says what the code now does, in the same change.

- [ ] `AGENTS.md` § Task lifecycle: drop the "process killed between the retry insert and the
      `retried` mark" bullet; the pre-2.9 `UNIQUE` sentence's "three cases above" becomes two.
- [ ] `AGENTS.md` § Data layer specifics: add the retry mark (conditional on still awaiting retry,
      committed with its retry row; skipped when no longer awaiting; at the bound left retryable with
      no retry row) and `Task.cancel_all` (by id; raises on a gone row; returns the ids cancelled) to
      the pattern; the closing sentence names only `hs update`, pointing at
      `issues/hs-update-contended-writes.md`.
- [ ] `AGENTS.md` § High-risk files: the `data/model.py` line adds that the mark commits with its
      retry row.
- [ ] `.agents/factory/invariants.md` §1 (pattern list) and §3 (unguarded list, two cases), in
      lockstep with AGENTS.md.
- **Verify:** see frontmatter (the greps; the full suite on SQLite; the server-database modules on PG 18
      and TS 2.32; `red_check.sh`).
- **Touches:** `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P6 — CI green on the fix (external wait)
**Satisfies:** R1–R6 · **Depends on:** P2, P5
**Goal:** every CI job green at the last code commit, the TimescaleDB 2.31 jobs included.

- [ ] **External wait.** The foreman pushes the branch to the draft PR and records the run under
      `spec/contended-writes/evidence/ci-green/run.json` (`gh run view <id> --json
      databaseId,headSha,conclusion,jobs`).
- **Verify:** `ci_check.py green`, with the last commit outside `spec/` as `--code-rev`.
- **Touches:** `spec/contended-writes/evidence/ci-green/`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `fix/contended-writes`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command, sandbox off — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP and escalate only on a
   **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
