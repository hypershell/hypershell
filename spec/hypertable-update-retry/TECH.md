---
slug: hypertable-update-retry
title: A task-row write that waited on a row lock still lands on a TimescaleDB hypertable
kind: fix
appetite: small
status: in_progress
branch: fix/hypertable-update-retry
base: develop
current_phase: P3
last_updated: '2026-11-04'
phases:
- id: P1
  name: Contended-write tests and the CI line (tests only)
  status: done
  satisfies:
  - R7
  - R3
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; m=tests/test_contended_write.py; l="${TMPDIR:-/tmp}/hs-hur-p1.log";
    git diff --quiet 0e8275e -- src && test "$(git diff --name-only 0e8275e -- .github)"
    = .github/workflows/tests.yml && grep -q 'tests/test_timescale.py tests/test_contended_write.py'
    .github/workflows/tests.yml && for t in test_rerun_after_contended_update_matches
    test_contended_confirm_records_client test_contended_result_applies test_contended_result_refused_when_reverted
    test_contended_orphan_revert_lands; do grep -q "^def $t(" "$m" || exit 1; done
    && test "$(grep -c '^def test_' "$m")" -eq 5 && ! grep -qE 'confirm_all|DEFERRED|CONTENDED_RERUNS'
    "$m" && { kt 300 env -u HS_TEST_POSTGRES pytest -rs "$m" > "$l" 2>&1; r=$?; tail
    -n 8 "$l"; test $r -eq 0; } && grep -q '5 skipped' "$l" && kt 120 python spec/hypertable-update-retry/contention_run.py
    --label p1 --commit 0e8275e --dry-run
- id: P2
  name: Confirm and the eviction revert land when contended
  status: done
  satisfies:
  - R1
  - R4
  - R5
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; git diff --quiet 0e8275e -- src/hypershell/client.py
    src/hypershell/task.py src/hypershell/core && test "$(git diff --name-only 0e8275e
    -- src | tr '\n' ' ')" = 'src/hypershell/data/model.py src/hypershell/server.py
    ' && ! git diff 0e8275e -- src | grep -qE '^[-+].*(mapped_column|__tablename__|__table_args__|Index\()'
    && grep -q 'Task.confirm_all(' src/hypershell/server.py && ! grep -q 'update_all(self.client_info'
    src/hypershell/server.py && kt 900 env -u HS_TEST_POSTGRES pytest -m unit -q tests/test_contended_recovery.py
    tests/test_writeback.py tests/test_writeback_statement.py tests/test_server.py
    tests/test_cancel.py tests/test_update.py && kt 300 env -u HS_TEST_POSTGRES pytest
    -q -rs tests/test_contended_write.py tests/test_writeback_postgres.py && kt 300
    .agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run --frozen hsx -t 'test
    {} -ne 7' -N4 -b1 --no-tls -f failed.txt 2> err.log; test \"\$(cat failed.txt)\"
    = 7 && test \$(uv run --frozen hs list -c -S) -eq 99 && test \$(uv run --frozen
    hs list -c -F) -eq 1 && test \$(uv run --frozen hs list -c -w 'client_id == null')
    -eq 0 && ! grep -qiE 'stale|duplicate|contended|not written' err.log"
- id: P3
  name: Results land when contended; a deferred result is held
  status: pending
  satisfies:
  - R2
  - R3
  - R5
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; git diff --quiet 0e8275e -- src/hypershell/client.py
    src/hypershell/task.py src/hypershell/core && test "$(git diff --name-only 0e8275e
    -- src | tr '\n' ' ')" = 'src/hypershell/data/model.py src/hypershell/server.py
    ' && ! git diff 0e8275e -- src | grep -qE '^[-+].*(mapped_column|__tablename__|__table_args__|Index\()'
    && grep -q 'Task.confirm_all(' src/hypershell/server.py && ! grep -q 'update_all(self.client_info'
    src/hypershell/server.py && grep -q 'DEFERRED' src/hypershell/data/model.py &&
    grep -q 'DEFERRED' tests/test_contended_recovery.py && grep -q 'self.held' src/hypershell/server.py
    && git diff --quiet 0e8275e -- tests/test_writeback.py tests/test_writeback_statement.py
    && kt 900 env -u HS_TEST_POSTGRES pytest -m unit -q tests/test_contended_recovery.py
    tests/test_writeback.py tests/test_writeback_statement.py tests/test_server.py
    tests/test_cancel.py tests/test_update.py && kt 300 env -u HS_TEST_POSTGRES pytest
    -q -rs tests/test_contended_write.py tests/test_writeback_postgres.py && kt 300
    .agents/factory/bin/temp_site.sh sh -c "seq 100 | uv run --frozen hsx -t 'test
    {} -ne 7' -N4 -b1 --no-tls -f failed.txt 2> err.log; test \"\$(cat failed.txt)\"
    = 7 && test \$(uv run --frozen hs list -c -S) -eq 99 && test \$(uv run --frozen
    hs list -c -F) -eq 1 && test \$(uv run --frozen hs list -c -w 'client_id == null')
    -eq 0 && ! grep -qiE 'stale|duplicate|contended|not written' err.log"
- id: P4
  name: Record the hazard and the rule; seed the deferred paths
  status: pending
  satisfies:
  - R8
  - R4
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'f=$(git log -1 --format=%H -- src); s=issues/hypertable-contended-scheduler-and-cli-writes.md;
    git diff --quiet "$f" HEAD -- src tests && git diff --quiet "$f" -- src tests
    && grep -q CONTENDED_RERUNS AGENTS.md && grep -q CONTENDED_RERUNS .agents/factory/invariants.md
    && grep -qi hypertable .agents/factory/invariants.md && test -f "$s" && grep -q
    ''^status: unshaped'' "$s" && grep -q "$s" ROADMAP.md && for x in stale-confirm-hides-orphan
    late-confirm-overwrites-attribution orphan-revert-loses-result coordinator-resilience-retry-backoff;
    do grep -q "$x" "$s" || exit 1; done'
- id: P5
  name: Final local gate and the foreman hand-back
  status: pending
  satisfies:
  - R6
  - R7
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'kt() { uv run --frozen python -c ''import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)'' "$@"; }; f=$(git log -1 --format=%H -- src); e=spec/hypertable-update-retry/EVIDENCE.md;
    l="${TMPDIR:-/tmp}/hs-hur-p5.log"; git diff --quiet "$f" HEAD -- src tests &&
    git diff --quiet "$f" -- src tests && grep -q "$f" "$e" && grep -q "$(git rev-parse
    0e8275e)" "$e" && grep -q ''evidence:r7'' "$e" && grep -q ''evidence:ci'' "$e"
    && ! grep -qE ''^<!-- evidence:'' "$e" && kt 120 python spec/hypertable-update-retry/contention_run.py
    --label p5 --commit "$f" --dry-run && { kt 2400 env -u HS_TEST_POSTGRES pytest
    -q -l > "$l" 2>&1 && tail -n 3 "$l" || { tail -n 40 "$l"; grep -E ''^(FAILED|ERROR)''
    "$l"; echo ''One rerun of the failed tests (known localhost flakes): record both
    runs''; kt 2400 env -u HS_TEST_POSTGRES pytest -q -l --lf; }; }'
- id: P6
  name: Record the foreman's runs and CI
  status: pending
  satisfies:
  - R7
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run --frozen python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; f=$(git log -1 --format=%H -- src); git diff --quiet
    "$f" HEAD -- src tests && kt 120 python spec/hypertable-update-retry/check_evidence.py
    r7 ci
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/hypertable-update-retry/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Why six phases

- **P1** commits the R7 integration tests and the CI line, with no `src/` change. They cannot run
  here (no PostgreSQL server); P1 proves they collect, skip cleanly and use only `0e8275e`'s symbols.
- **P2** and **P3** are the two coupled-core slices, each verifiable on SQLite by fault injection:
  Confirm and the eviction revert first (the CI crash), then the result write and the Receiver's hold.
- **P4** records the hazard and rule (R8) and seeds the deferred write paths (R4).
- **P5** is the final local gate and writes the foreman's hand-back.
- **P6** is an external wait: it records the foreman's four runs and the PR's CI once delivered.
  `hs-build` never pushes, opens a PR, touches a cluster, or runs a PostgreSQL server.

## R4 — audit of every `task` UPDATE path

From [`research/02-write-path-audit.md`](research/02-write-path-audit.md) (`develop` `0e8275e`).
"Contended" means another transaction holds the row's lock when the write reaches it.

| Path | Form | Contended by | Verdict |
|------|------|--------------|---------|
| `Confirm.update_info` → `Task.update_all` (`server.py`) | bulk by id | Receiver (in-server); eviction revert; `hs task update` | **In scope.** `Task.confirm_all` (P2). |
| `Task.apply_results` / `__apply_result` (Receiver) | conditional core UPDATE | Confirm (in-server); eviction revert; `hs task update` | **In scope.** Re-run behind the classifying SELECT (P3). |
| `Task.revert_orphaned` → `revert_all` (HeartMonitor) | bulk by id | Receiver (in-server); a second Confirm; `hs task update` | **In scope.** `revert_all` on the helper; loop stops on a pass that lands nothing (P2). |
| `Task.revert_interrupted` → `revert_all` (Scheduler start) | bulk by id | none in-server (nothing dispatched yet); `hs task update` | **Fixed with the same lines** via `revert_all` (P2); covered by the revert unit cases, no new harness. |
| `Task.revert` → `revert_all` | bulk by id | no in-tree caller | **Fixed with the same lines** via `revert_all`. |
| `Task.next` stamp (`server_id`, `server_host`, `schedule_time`) | ORM flush | **in-server:** a late Confirm on a row eviction just reverted; `hs task update` | **Seed.** Needs a per-row core UPDATE off the ORM flush and its own "qualifies" choice; not the same few lines. Risk 2 in PLAN §5. |
| `__schedule_next_failed_tasks` `retried`/`next_id` mark | bulk by id | **in-server:** a Confirm lagging a fast failure; `hs task update` | **Seed.** An exhausted mark would leave the row `retried IS FALSE` and double the retry; needs its own exhaustion design. |
| `Task.cancel_all` / `cancel` | bulk by id | no in-tree caller (API only) | **Seed** (with the CLI paths). |
| `hs task update` legacy single row (`task.py`) | `Task.update` | the server's writers | **Seed.** A contended 0 is reported as `Task.NotFound`. |
| `hs task update --limit` batches | `Task.update_all` | the server's writers | **Seed.** Partial commit, then a traceback. |
| `hs task update` set / `--cancel` / `--revert` / tags | `Query.update` | the server's writers | **Seed.** Contended rows silently skipped, "Updated N" still logged; inlines its own columns, so `revert_all` does not reach it. |
| `rotatedb` part tag (`data/__init__.py`) | `Query.update` | — | **Unaffected:** SQLite only. |
| vacuum, `upgradedb`, TimescaleDB DDL/columnstore, `submit` | no `task` row UPDATE | — | **Unaffected.** |
| `Client.update` in `HeartMonitor` | bulk by id on `client` | none: one writer thread | **Unaffected:** `client` is never a hypertable, and has a single writer. |

The seed is `issues/hypertable-contended-scheduler-and-cli-writes.md` with a `ROADMAP.md` entry (P4).

## R5 — bound and exhaustion outcomes

- **Bound:** `CONTENDED_RERUNS = 3` extra attempts per write (4 in all), each taken only after a
  fresh read says the row still qualifies, each logged at DEBUG naming the task id and the path
  (`confirm`, `revert`, `result`). In-server a write has at most two other writers, so the bound is
  reachable only through cross-process writers.
- **Confirm at the bound:** WARNING naming the task and `confirm`; that row's `client_id` and
  `client_host` stay unrecorded; the bundle's other rows are stamped and committed; the Confirm
  thread keeps running.
- **Result at the bound:** `ResultOutcome.DEFERRED`; WARNING naming the task and `result`. **What
  then happens to the task:** the Receiver holds the result and presents it again on each of its
  cycles (every bundle, and every empty-queue timeout), DEBUG on each, until it lands (APPLIED, with
  the normal completion logging and `--failures`), or classifies STALE or DUPLICATE because the row
  changed (e.g. eviction reverted it). The row stays in flight under its own dispatch meanwhile, so
  nothing reschedules it: no double run. The run cannot stop while the row is in flight, and the
  result lands as soon as the row is free: no hang. Only a signalled or forced stop can deliver the
  sentinel with a result held; the Receiver then logs a WARNING per held result, and the row is
  reverted and re-run at the next start, as for any result in flight at a stop.
- **Eviction revert at the bound:** WARNING naming the task and `revert`; the row stays in flight
  (completed by its client's result if the client is alive, else reverted by `revert_interrupted`
  at the next start). The revert loops stop on a pass that lands nothing, so they stay bounded.
- **Never:** a crash, an unbounded loop, or a write that lands without its condition.

## Touched surface

| File | Symbol | Change | Phase |
|------|--------|--------|-------|
| `tests/test_contended_write.py` | new module | R7 integration tests (research 03) | P1 |
| `.github/workflows/tests.yml` | `postgres` job's pytest line | append `tests/test_contended_write.py` | P1 |
| `src/hypershell/data/model.py` | `CONTENDED_RERUNS`, `Task.__row_statements`, `Task.__write_rows`, `Task.confirm_all`, `Task.revert_all` body/return, `revert_orphaned`, `revert_interrupted` loops | PLAN §2.1 | P2 |
| `src/hypershell/server.py` | `Confirm.update_info` | `Task.confirm_all(...)` | P2 |
| `tests/test_contended_recovery.py` | new module | R5/R6 fault injection on SQLite | P2 (Confirm, revert), P3 (result) |
| `src/hypershell/data/model.py` | `ResultOutcome.DEFERRED`, `Task.__result_statements` SELECT, `Task.__apply_result` | PLAN §2.2 | P3 |
| `src/hypershell/server.py` | `Receiver.held`, `update_tasks`, `unload_bundle`, `unpack_bundle` | PLAN §2.3 | P3 |
| `AGENTS.md`, `.agents/factory/invariants.md` | Data layer specifics / §1 | the hazard and the rule | P4 |
| `issues/hypertable-contended-scheduler-and-cli-writes.md`, `ROADMAP.md` | new seed + entry | R4's deferred paths | P4 |
| `spec/hypertable-update-retry/EVIDENCE.md` | new | hand-back (P5), foreman evidence (P6) | P5, P6 |

**Unchanged by design:** `Entity.update_all`/`update`, `Task.next`, the `retried` mark,
`Task.cancel_all`, `task.py`, `client.py`, `core/*`, the schema and `data/upgrade.py`,
`data/timescale.py`, the wire codec, `tests/test_timescale.py`, `tests/test_writeback*.py`, CI images
and matrix, `docs/`, `share/`. `contention_run.py` and `check_evidence.py` were committed with the
plan; change them only by amending PLAN §2.7 and saying why in the commit body.

## Conventions (apply to every phase)

- Commit conventions, style and invariants come from [`AGENTS.md`](../../AGENTS.md); curated
  footguns in [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1, §3, §4, §5, §6, §12.
- One atomic commit per phase containing **both** the change and this file's state change. Subject:
  `[fix] Build hypertable-update-retry P<n>: …`. **No `Co-Authored-By` trailer.** Stage by explicit
  path.
- No CLI change, so **no** `docs/_include/*.rst` or `share/` updates (§12 does not trigger).
- Comments are declarative statements; **no R-IDs or phase ids in source or tests** (AGENTS.md).
- `verify:` commands run from the worktree root. `kt` runs a command in its own session and kills
  its process group at the deadline (exit 124); it runs under `uv run --frozen`, so the commands it
  starts resolve in the project environment. CLI drives isolate the site with
  `.agents/factory/bin/temp_site.sh`. Integration tests and the drive bind a loopback port: a
  sandbox that forbids it is an environment fault, not a red verify. `HS_TEST_POSTGRES` is unset in
  every local verify (`env -u`): the PostgreSQL modules must skip, never reach a server.
- No cluster tools, no network, no PostgreSQL server, no GitHub actions in any phase.

## Pre-build evidence and the build environment (recorded at build)

- **The re-run premise, verified before the build** (the foreman's plan-gate probe, 2026-11-04, on a
  test cluster): on a real TimescaleDB 2.31.0 (Apache), in all 6 two-session cases (with
  `timescaledb.enable_hypertable_expansion_for_dml` on and off), the blocked UPDATE matched 0 rows,
  and an immediate re-run of the same statement on the waiter's connection matched **1**. A plain
  table matched 1 the first time. This settles PLAN §5's first residual risk ahead of P6;
  `test_rerun_after_contended_update_matches` still pins it in the foreman's runs and in CI.
- **Plan-gate rulings (foreman, 2026-11-04):** risk 1, keep `develop`'s behaviour (an uncontended
  write to a missing row still raises); risk 2, the Scheduler's in-server contention (the `Task.next`
  stamp and the `retried` mark) goes into the P4 seed, not fixed here; risk 3, accept the per-row
  cost; risk 4, a new test module plus a CI line.
- **Interpreters.** The build machine has no network and no PostgreSQL server. Each verify ran with
  a frozen, offline venv per Python (3.11–3.14), synced from the lock with `uv sync --frozen
  --offline --extra postgres` against a private copy of a local uv cache. No local cache holds the
  `psycopg-binary` 3.3.4 wheels, so the venvs were synced with `--no-install-package
  psycopg-binary`: `psycopg` is installed but cannot load a libpq, which nothing local needs (every
  PostgreSQL module skips). Where a verify says `uv run --frozen CMD`, CMD ran with that venv first
  on `PATH` (a stand-in `uv` that only executes `run [--frozen] CMD`); nothing was resolved or synced
  by a verify.

---

## Phase P1 — Contended-write tests and the CI line (tests only)
**Satisfies:** R7 (tests; red/green proved at P6), R3 (test) · **Depends on:** —
**Goal:** the five R7 tests exist, run in every CI `postgres` entry, and reference only symbols
present on `0e8275e`, so `0e8275e` fails them by assertion. **No `src/` edit.**

- [x] Create `tests/test_contended_write.py`. Module docstring: what the tests force (a write that
      waits on another transaction's row lock, then the holder commits) and how to run them
      (`HS_TEST_POSTGRES`, `HS_TEST_TIMESCALE`, as `tests/test_timescale.py` documents). Module-level
      `pytestmark = [mark.integration, needs_server]`.
- [x] Import from `tests.test_timescale`: `admin`, `ts_database`, `ts_site` (fixtures),
      `database_engine`, `is_hypertable`, `uuid7`, `needs_server`, `TIMESCALE`; from `tests`:
      `main_lines`. Fixture `contended_db`: `ts_database()`; `ts_site(name, provider='timescale' if
      TIMESCALE else 'postgres')`; `hs initdb --yes` succeeds; with `TIMESCALE` set,
      `is_hypertable(engine)` or `pytest.fail`; bind the in-process `Session` to
      `database_engine(name)` and restore it in `finally`, as `tests/test_writeback_postgres.py`'s
      `pg_tasks` does. Yields the engine and the database name.
- [x] Helpers: `submit()` (`Task.new`, `id = uuid7()`, `Task.add`), `dispatch_one()` (through
      `serialize_tasks`/`deserialize_tasks`), `finish()`, `confirm()` (a `Confirm(queue=None)` with
      `client_info` set, then `update_info()`, returning its next state), `receive()` (a
      `Receiver(queue=None, redirect_failures=…)` with `tasks` set through the codec, then
      `update_tasks()`), `row()` (a raw `text()` select of one row on a private connection).
- [x] `contend(engine, holder_sql, params, write)`: open a holder connection (not autocommit), read
      `pg_backend_pid()`, execute the holder's UPDATE (row lock held, uncommitted); start `write` in a
      `threading.Thread` that records its return value or exception and calls `Session.remove()` in
      `finally`; poll every 20 ms, up to 30 s, from an autocommit observer connection:
      `SELECT wait_event_type, query FROM pg_stat_activity WHERE datname = current_database() AND
      :holder = ANY(pg_blocking_pids(pid))`, until a row has `wait_event_type = 'Lock'` and a query
      starting `UPDATE task`. `pytest.fail` if the thread finished first or the deadline passed
      (commit the holder first so nothing hangs). Then commit the holder, join (30 s; `pytest.fail`
      if alive), close both connections, and return the recorded value or exception. No sleep
      decides an outcome.
- [x] The five tests, names exactly as below (`check_evidence.py` reads them), each `pytest.fail`
      on an unmet precondition and `assert` only on the outcome:
  - `test_rerun_after_contended_update_matches`: raw SQL only. Holder `UPDATE task SET client_host =
    'holder' WHERE id = :id`; the write runs `UPDATE task SET client_host = 'writer' WHERE id = :id`
    on its own connection, records its rowcount, then runs it again and records that. Assert the
    second count is 1 and the row reads `writer`. (The first count is 1 on PostgreSQL and 2.24, 0 on
    2.29+; record it in the assertion message, do not assert it.)
  - `test_contended_confirm_records_client`: dispatch one task; holder writes the result columns
    (`completion_time`, `exit_status = 0`, `duration`) by id; the write is `confirm()` for a bundle
    holding that task. Assert no exception, the returned state is `ConfirmState.UNLOAD`, the row's
    `client_id`/`client_host` are the confirming client's, and the holder's `completion_time` is kept.
  - `test_contended_result_applies`: dispatch, confirm uncontended, finish the copy with exit status
    3; holder is Confirm's stamp (`client_id`, `client_host`) by id; the write is `receive()` with a
    `StringIO` for failures. Assert the row's result columns equal the copy's (completion time,
    exit status 3), the failures buffer holds the task's args, and no "Refused stale result" WARNING
    was logged (`caplog`).
  - `test_contended_result_refused_when_reverted`: as above with exit status 0, but the holder
    reverts the row (every column `revert_all` clears, set NULL). Assert the row is unscheduled with
    no result column written, and the stale-result WARNING was logged once.
  - `test_contended_orphan_revert_lands`: dispatch and confirm to client X; holder is a late Confirm
    re-stamping the same client by id; the write is `Task.revert_orphaned(X)`. Assert no exception
    and the row is unscheduled (`schedule_time`, `client_id` NULL).
- [x] `.github/workflows/tests.yml`: in the `postgres` job's "Run PostgreSQL tests" step, append
      ` tests/test_contended_write.py` after `tests/test_timescale.py`. Nothing else in the file.
- **Verify:** `src/` byte-identical to `0e8275e`; the workflow is the only `.github` change and
  names the module; the module defines exactly the five tests and names no symbol the fix adds;
  without a server all five skip; `contention_run.py --dry-run` accepts `0e8275e`.
- **Touches:** `tests/test_contended_write.py`, `.github/workflows/tests.yml`, this file.

## Phase P2 — Confirm and the eviction revert land when contended
**Satisfies:** R1, R4 (`revert_orphaned`, `revert_interrupted`, `revert`), R5, R6 · **Depends on:** P1
**Goal:** a contended Confirm or revert re-runs and lands; at the bound it warns and the thread keeps
running; uncontended writes issue one UPDATE per row and nothing else.

- [x] `src/hypershell/data/model.py`: `CONTENDED_RERUNS: Final[int] = 3` with a declarative comment
      (a TimescaleDB 2.29+ hypertable can report 0 rows for an UPDATE that waited on a row lock);
      `Task.__row_statements` (cached per column-name tuple), `Task.__write_rows`, `Task.confirm_all`,
      `Task.revert_all` on the helper returning the landed ids, and the two revert loops stopping on
      a pass that lands nothing — all per PLAN §2.1, log texts as given there. Import `StaleDataError`
      from `sqlalchemy.orm.exc`.
- [x] `src/hypershell/server.py`: `Confirm.update_info` calls `Task.confirm_all(info.task_ids,
      info.client_id, info.client_host)` under the existing `in_memory` guard.
- [x] Create `tests/test_contended_recovery.py` (`@mark.unit`, SQLite, a per-test database as
      `tests/test_writeback_statement.py`'s `task_db` does). Fixture `contention`: wraps the scoped
      session's `execute` with `monkeypatch.setitem(vars(Session), 'execute', …)`; `contention.lose(
      task_id, k)` makes the next `k` UPDATEs whose `r_id` is `task_id` return an object with
      `rowcount == 0` without executing; it records every executed statement kind (UPDATE/SELECT).
      Cases: Confirm loses once → stamped, one DEBUG naming the id; Confirm loses 4 → WARNING naming
      the id and `confirm`, that row's client columns NULL, the bundle's other rows stamped,
      `update_info` returns `ConfirmState.UNLOAD`; revert loses once → reverted; revert loses 4 →
      WARNING, `revert_orphaned` returns, row in flight; a Confirm for a deleted row raises
      `StaleDataError` and stamps none of the bundle; uncontended Confirm and revert issue exactly one
      UPDATE per row and no SELECT.
- **Verify:** the `src/` diff is `data/model.py` and `server.py` only, with no schema line;
  `client.py` and `task.py` untouched; Confirm calls `confirm_all`. The new unit module and the
  writeback, statement, server, cancel and update unit tests pass; the PostgreSQL modules skip. The
  CLI drive (100 tasks, one failure, `-b1 -f`) gives 99 succeeded, 1 failed, `7` in the failures
  file, no row without a `client_id`, no stale/duplicate/contended/shutdown warning.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/server.py`,
  `tests/test_contended_recovery.py`, this file.

## Phase P3 — Results land when contended; a deferred result is held
**Satisfies:** R2, R3, R5, R6 · **Depends on:** P2
**Goal:** a contended result re-runs while its row still qualifies; at the bound it is held and
presented again; uncontended results issue exactly today's statements.

- [ ] `src/hypershell/data/model.py`: `ResultOutcome.DEFERRED = 4` and its docstring sentence; the
      classifying SELECT returns `completion_time` for the same dispatch; `__apply_result`'s bounded
      loop — PLAN §2.2. Keep the `schedule_time is None` guard and both existing comments.
- [ ] `src/hypershell/server.py`: `Receiver.held` and the `update_tasks` / `unload_bundle` /
      `unpack_bundle` changes — PLAN §2.3, log texts as given there.
- [ ] Extend `tests/test_contended_recovery.py`: result loses once → APPLIED, one DEBUG naming the
      id; result loses 4 → `DEFERRED`, WARNING once, nothing written; the next `update_tasks` with an
      empty bundle (driven through `unload_bundle` with a stub queue whose `completed.get` raises
      `QueueEmpty`, so the `UNLOAD → UPDATE` edge is exercised) applies it with the "Completed task"
      DEBUG and, for a non-zero exit, the failures line; a held result whose row is reverted meanwhile
      classifies STALE; a sentinel with a held result logs the shutdown WARNING and returns `FINAL`;
      uncontended APPLIED is one UPDATE and no SELECT; uncontended STALE and DUPLICATE are one UPDATE
      and one SELECT each; `in_memory` never holds.
- **Verify:** as P2's scope checks, plus `DEFERRED` present in `model.py` and the recovery module;
  `tests/test_writeback.py` and `tests/test_writeback_statement.py` unchanged since `0e8275e` and
  green (the statement-reuse guard still sees one UPDATE and at most one SELECT object); the same
  unit set and CLI drive as P2.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/server.py`,
  `tests/test_contended_recovery.py`, this file.

## Phase P4 — Record the hazard and the rule; seed the deferred paths
**Satisfies:** R8, R4 · **Depends on:** P3
**Goal:** future writers of `task` rows meet the rule where agents read; the paths this cycle does
not fix are recorded as a candidate with their evidence.

- [ ] `AGENTS.md` § Data layer specifics: the bullet in PLAN §2.6, naming `CONTENDED_RERUNS`,
      `Task.confirm_all`/`revert_all` and `Task.apply_results` as the pattern. § High-risk files &
      footguns, `data/model.py` entry: one clause pointing at it.
- [ ] `.agents/factory/invariants.md` §1: the same rule, in lockstep.
- [ ] `issues/hypertable-contended-scheduler-and-cli-writes.md` from
      `.agents/factory/templates/ISSUE.md` (`status: unshaped`, `kind: fix`, `appetite: small`,
      `lane: public`): Problem from research 02 rows for `Task.next`, the `retried` mark,
      `cancel_all` and the `hs task update` paths (with the in-server late-Confirm contender, the
      doubled-retry exhaustion hazard, and `hs task update`'s silent skip and false `NotFound`);
      the unprobed DELETE paths; the pre-existing Confirm/Receiver deadlock as a note for
      `coordinator-resilience-retry-backoff`; links to `stale-confirm-hides-orphan`,
      `late-confirm-overwrites-attribution`, `orphan-revert-loses-result`,
      `coordinator-resilience-retry-backoff`, and to this cycle's `spec/` as the pattern.
- [ ] `ROADMAP.md` Part I: an entry in the house style (heading, short problem paragraph, horizon
      line, `**Seed:**` link with `*status: unshaped*`), placed near the confirmation entries.
- **Verify:** no `src/` or `tests/` change since the last `src/` commit; both docs name
  `CONTENDED_RERUNS` and the hypertable hazard; the seed exists, is `unshaped`, links the four seeds,
  and `ROADMAP.md` links it.
- **Touches:** `AGENTS.md`, `.agents/factory/invariants.md`, the new seed, `ROADMAP.md`, this file.

## Phase P5 — Final local gate and the foreman hand-back
**Satisfies:** R6, R7 (request) · **Depends on:** P4
**Goal:** the whole suite is green on the final fix, and the foreman has an exact run request.

- [ ] Run the full suite (the `verify:` allows one recorded `--lf` rerun for the known localhost
      flakes; record both runs in the commit body).
- [ ] Create `spec/hypertable-update-retry/EVIDENCE.md` with a section **"Hand-back — contended-write
      runs and CI"**:
      - builds, by label and full commit: **develop** = `0e8275e…` (full sha); **fix** = the output
        of `git log -1 --format=%H -- src` (any later commit changes only `spec/` and docs);
      - the four runs, each a fresh TimescaleDB container on a test cluster, from a checkout of this
        branch at its final commit (`git fetch` of the pushed branch), with `git`, `uv` and network
        or a warm uv cache:
        ```
        python3 spec/hypertable-update-retry/contention_run.py --label ts231-develop --commit 0e8275e \
            --admin-url "$URL" --timescale community --out "$OUT"
        python3 spec/hypertable-update-retry/contention_run.py --label ts231-fix --commit <fix sha> \
            --admin-url "$URL" --timescale community --out "$OUT"
        ```
        and the same two with labels `ts224-develop`/`ts224-fix` against 2.24.0. Images: CI's own
        pins (`timescale/timescaledb:2.31.0-pg18@sha256:0fcff576…` and `2.24.0-pg17@sha256:8ab414f7…`,
        full digests from `.github/workflows/tests.yml`); `$URL` is the container's admin URL in CI's
        form (`postgresql+psycopg://postgres:postgres@localhost:<port>/postgres`); `--out` outside the
        checkout. Expected: develop on 2.31.0 exits 1 with exactly the three red tests `failed`;
        the other three runs exit 0.
      - what to return: the four `<label>.json` summaries as one fenced `json` block
        `{"runs": [ … ]}` directly after the marker line `<!-- evidence:r7 -->`, the four `.log` files
        (attached or pasted as a collapsed appendix), and a one-paragraph harness note (host kind,
        container runtime, Python) above the block;
      - also: push the branch and open the draft PR so CI runs. Return a fenced `json` block after
        `<!-- evidence:ci -->`: `{"run_id": …, "head_sha": "<full sha>", "jobs": {"<job name>":
        "<conclusion>", …}, "contended": {"PostgreSQL (<entry>)": {"passed": <n>, "skipped": <n>},
        …}}`, covering `tests (3.11)`…`tests (3.14)` and the five `PostgreSQL (…)` entries, the
        `contended` counts read from each `postgres` job log's lines for `test_contended_write.py`;
        an optional `reruns` list exactly as in `writeback-prebuilt-update`'s hand-back;
      - the bars, verbatim from GOAL R7, and what `check_evidence.py r7 ci` checks.
      Write the markers only inside backticks in this section, so the checker cannot mistake the
      request for evidence.
- **Verify:** no `src/` or `tests/` change since the final fix commit; `EVIDENCE.md` names both
  commits and both markers; `contention_run.py --dry-run` accepts the fix commit; the full suite
  passes.
- **Touches:** `spec/hypertable-update-retry/EVIDENCE.md`, this file.

## Phase P6 — Record the foreman's runs and CI
**Satisfies:** R7 · **Depends on:** P5
**Goal:** the foreman's evidence is in `EVIDENCE.md` and meets the bars.

- [ ] **External wait.** At the end of P5, `hs-build` sets this phase `blocked` with
      `blocked_reason: awaiting foreman contended-write runs and CI` and stops. It never runs a
      cluster job or a PostgreSQL server, pushes, or reads GitHub itself.
- [ ] When the foreman delivers: clear `blocked` with `set_phase.py`; paste the `r7` and `ci` blocks
      and the harness note under their markers as delivered, and a short table (run, server, build,
      exit, red tests).
- [ ] If `check_evidence.py r7` fails, do not edit the evidence. A failing
      `test_rerun_after_contended_update_matches` on 2.31.0 means the design's premise is false:
      stop and escalate (PLAN §5). A test that never observed the lock wait, or a red test that
      errored instead of failing, is a test defect: amend P1's module in a new commit, re-run P5's
      gate, and ask the foreman for new runs. A CI entry red for a reason outside this change needs
      a rerun or the foreman's ruling, recorded next to the `ci` block.
- **Verify:** no `src/` or `tests/` change since the final fix commit, and `check_evidence.py r7 ci`
  passes.
- **Touches:** `spec/hypertable-update-retry/EVIDENCE.md`, this file.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
