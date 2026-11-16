---
slug: cluster-launch-followups
title: A locked database delays a cluster's decision instead of ending the run; a
  --no-db cluster whose work is done never reports a failed launch
kind: fix
appetite: small
status: in_progress
branch: fix/cluster-launch-followups
base: develop
current_phase: P2
last_updated: '2026-11-16'
phases:
- id: P1
  name: Regression tests first, red on 9c82842d
  status: done
  satisfies:
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet 9c82842d -- src && l=/tmp/claude-501/clf-p1-red.log; uv
    run pytest -q -p no:cacheprovider -rf tests/test_cluster_locked.py tests/test_cluster_nodb_finish.py
    -k "not still" > "$l" 2>&1; r=$?; tail -n 40 "$l"; s="$(tail -n 1 "$l")"; test
    $r -eq 1 && echo "$s" | grep -qE "^[0-9]+ failed" && ! echo "$s" | grep -qE "passed|error"
    && uv run pytest -q -p no:cacheprovider tests/test_cluster_locked.py tests/test_cluster_nodb_finish.py
    -k still && uv run pytest -q -p no:cacheprovider tests/test_cluster_exit.py tests/test_autoscale_failed_launches.py
- id: P2
  name: The cluster's own checks skip a locked read, warn once and decide at their
    next pass
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_cluster_locked.py tests/test_cluster_exit.py
    tests/test_autoscale_failed_launches.py tests/test_autoscale.py tests/test_zone_autoscale.py
    tests/test_relay_launch.py tests/test_relay_topology.py tests/test_topology_server.py
    && for m in locked-scale-fail locked-scale-off locked-scale-ok locked-launch;
    do .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-followups/drive/followups_drive.sh"
    $m || exit 1; done && git diff --quiet 9c82842d -- src/hypershell/server.py src/hypershell/cluster/demand.py
    src/hypershell/data src/hypershell/relay src/hypershell/cluster/topology.py
- id: P3
  name: A --no-db cluster counts the results its server wrote
  status: pending
  satisfies:
  - R5
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q -p no:cacheprovider tests/test_cluster_nodb_finish.py tests/test_cluster_exit.py
    tests/test_cluster_no_db.py tests/test_server.py tests/test_server_fsm.py tests/test_writeback.py
    tests/test_task_wire.py && for m in nodb-crashy nodb-fail; do .agents/factory/bin/temp_site.sh
    sh "$PWD/spec/cluster-launch-followups/drive/followups_drive.sh" $m || exit 1;
    done && ! grep -q -e MEMORY_SETTLE -e memory_settle src/hypershell/cluster/watch.py
    && d="$(git diff --numstat 9c82842d -- src/hypershell/server.py)" && test "$(echo
    "$d" | cut -f2)" = 0 && test "$(echo "$d" | cut -f1)" -ge 2 && test "$(echo "$d"
    | cut -f1)" -le 8
- id: P4
  name: 'Gate: every drive, the whole suite under 3.14 and 3.11, docs and pinned tests
    unchanged'
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet 9c82842d -- docs share tests/test_cluster_exit.py tests/test_autoscale_failed_launches.py
    src/hypershell/cluster/demand.py src/hypershell/data src/hypershell/submit.py
    src/hypershell/core && for m in locked-scale-fail locked-scale-off locked-scale-ok
    locked-launch nodb-crashy nodb-fail; do .agents/factory/bin/temp_site.sh sh "$PWD/spec/cluster-launch-followups/drive/followups_drive.sh"
    $m || exit 1; done && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-clf uv run
    --python 3.14 pytest -p no:cacheprovider -q && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv311-clf
    uv run --python 3.11 pytest -p no:cacheprovider -q
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — A locked database delays a cluster's decision instead of ending the run; a `--no-db` cluster whose work is done never reports a failed launch

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/cluster-launch-followups/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03. The test
  prototypes (red 3 of 3 on the base) are kept in [`research/prototypes/`](research/prototypes/),
  named so the suite never collects them; run one with
  `PYTHONPATH=$PWD:$PWD/spec/cluster-launch-followups/research/prototypes uv run pytest -p tests.conftest <file>`.
  Their `CLF_STANDIN*` hooks are inert here (the stand-in they name was a scratch oracle, not kept).
- **Drive:** [`drive/followups_drive.sh`](drive/followups_drive.sh) (six modes; dry-run at plan time
  on `9c82842d`'s code, where `nodb-fail` passes and the other five fail as the defects predict, and
  with the design emulated, where all six pass, the locked ones twice). The builder may amend it; a
  change to a check is noted in the commit.

## Running the verify commands here

- Run every `verify:` from the worktree root with the sandbox off, `UV_CACHE_DIR=/tmp/claude-501/uv-cache-clf`
  and `TMPDIR=/tmp/claude-501`. Explicit `/tmp/claude-501/...` paths are used throughout.
- `pytest -n auto` is broken on this suite: everything runs serially.
- The worktree's `.venv` is Python 3.15 (outside the supported 3.11–3.14) and is not used: per the
  plan gate (D5), every phase runs with `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-clf` and
  `UV_PYTHON=3.14` exported (so each `uv run` in a verify, the drive included, uses 3.14), with that
  environment's `bin` first on `PATH` for subprocess tests. The gate also runs 3.11 in
  `/tmp/claude-501/venv311-clf`, never the other builder's environments.
- The drive binds `127.0.0.1` on a free port and stops every process it starts by PID (its process
  tree). The tests bound every subprocess (`wait(timeout)`, then `kill_group`) and every thread join.
- Expected durations: P1 red selection ≈ 45 s; each locked drive mode 15–60 s; the whole suite a few
  ≈ 25 min per Python (3,357 tests; on the base, 3.14: 3219 passed, 138 skipped in 24 min 27 s; 3.11: 3216 passed,
  141 skipped in 23 min 14 s).

## Conventions (apply to every phase)

- `AGENTS.md` is the constitution; [`invariants.md`](../../.agents/factory/invariants.md) §4, §5, §6,
  §11 and §12 are the sections this change touches (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state
  change, subject `[fix] Build cluster-launch-followups P<n>: …`, signed. **No `Co-Authored-By`
  trailer.** No push.
- No spec ids in source or test names; comments are declarative statements. Tests are `@mark.unit` or
  `@mark.integration`. Docs and tests name only top-level commands; a key travels as
  `HYPERSHELL_SERVER_AUTH`, never `-k`. Public repo: no site or host names.
- **Scope fence:** `src/hypershell/cluster/remote.py` (`AutoScaler` only) and
  `src/hypershell/cluster/watch.py`, plus `server.py`'s `Receiver` counter (P3 only, at most a few
  added lines, nothing removed). No edit to `cluster/demand.py`, `data/`, `submit.py`, `core/`,
  `relay/`, `cluster/topology.py`, docs, completions or configuration. A phase that finds it needs
  one stops and escalates.
- Log wording the drive and tests match (keep the phrase `database was locked`):
  `Autoscale check skipped: database was locked (checking again in {period})` and
  `Could not read whether tasks remain: database was locked (reading again)`, both WARNING. The
  existing error texts (`every launched client has exited with tasks remaining`, `N launched clients
  in a row exited non-zero`) are unchanged.

---

## Phase P1 — Regression tests first, red on `9c82842d`
**Satisfies:** R7 · **Depends on:** —
**Goal:** every R1, R2, R3 and R5 behaviour has a test that fails on the base tree, none hangs, and
the guards pass. No source change.

- [x] New `tests/test_cluster_locked.py`, from `research/prototypes/` (`locking.py`, `locked_*_proto.py`). Helpers:
      `locked_db(path)` (binds the scoped `Session` to a per-test SQLite file through its own engine
      with `connect_args={'check_same_thread': False, 'timeout': 0.5}`, creates the tables, restores
      `data.core.engine` after; the product's busy wait is untouched), `exclusive(path)` (a second raw
      `sqlite3` connection, `isolation_level=None`, holding `BEGIN EXCLUSIVE` for the block), a bounded
      `LockHolder` thread for the CLI tests (waits for the launch line and a staged task, then 0.5 s,
      locks, touches a flag, holds, releases; records times), and the gated launcher (waits ≤ 30 s
      for the flag, then exits 1). Reuse `failing_scaler`/`run_checks` (`test_autoscale_failed_launches.py`,
      `test_zone_autoscale.py`), `StandInServer`/`kill_group` (`test_cluster_exit.py`). Reach new names
      through their module (`watch.database_locked`) so each test fails alone on the base.
  - `test_database_locked[...]` (unit): locked (5), busy, table locked (extended 262), a hand-made
    `sqlite3.OperationalError('database is locked')` with no code → True; `disk I/O error`, an
    `OperationalError` whose `orig` is not a `sqlite3.OperationalError` (its message saying "locked"
    is not enough), a non-`OperationalError` → False. (Every row fails on the base: the name is
    missing.)
  - `test_locked_check_is_skipped_with_one_warning[policy-read]` (unit), `policy` ∈ {fixed, dynamic},
    `read` ∈ {registrations (`failed_launches=3`, real `client_registrations`), sizing-at-limit
    (`failed_launches=3`, registrations `(0, 0)`, so only the zone-sizing reads are locked),
    sizing-off (`failed_launches=0`)}: one CHECK pass inside `exclusive(db)` returns `WAIT`, not
    `FINAL`/`HALT`, `exhausted` False, exactly one WARNING containing `database was locked`, no
    CRITICAL; `last_check` advanced.
  - `test_exits_during_a_skipped_check_are_counted` (unit): the first launch has exited 1 before a
    locked check; after release `run_checks` reaches FINAL with 3 failures and `failures[0]` is that
    first launch's pid.
  - `test_limit_off_relaunches_after_a_skipped_check` (unit): `failed_launches=0`; after release the
    next check scales again; never exhausted.
  - `test_other_operational_errors_still_end_the_check[registrations, sizing]` (unit, guard): a
    constructed `disk I/O error` propagates from the check.
  - `test_watch_reads_again_after_a_locked_read` (unit): `StandInServer` with a live-scheduler
    stand-in (`scheduler.is_alive()` True, `machine.current_group` None, receiver not alive), the real
    `work_remains`, one failed launched process; lock held 1.5 s; the watch in a thread joined with a
    15 s bound; `ClusterLaunchError` within 10 s of the release, a `database was locked` WARNING, no
    CRITICAL.
  - `test_other_operational_errors_still_end_the_watch` (unit, guard): a constructed non-lock error
    from the read propagates out of `watch_launched`.
  - `test_session_still_reads_after_a_locked_read` (unit, guard): the premise of PLAN §2.1, no
    rollback needed.
  - `test_autoscaling_cluster_with_a_locked_database_reaches_the_failed_launch_stop` (integration):
    `hs cluster tasks.txt --no-tls -H 127.0.0.1 -p <free> --autoscaling fixed -P 1s --launcher <gated>`
    in `temp_site`, lock 8.5 s; `wait(45)` then `kill_group`; exit 5, `3 launched clients in a row
    exited non-zero`, a `database was locked` WARNING, no `exception-*.log` anywhere under the site,
    ended after the release.
  - `test_autoscaling_cluster_with_the_limit_off_keeps_relaunching_after_a_lock` (integration):
    the same with `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0`; alive 6 s after the release, at least 3
    `exited with status 1`, no traceback file; then `kill_group`.
  - `test_launcher_cluster_with_a_locked_database_exits_with_the_launch_error` (integration):
    `hs cluster tasks.txt --no-tls -H 127.0.0.1 -p <free> --launcher <gated>`, lock 7.5 s; exit 5,
    `every launched client has exited with tasks remaining`, ended ≤ 10 s after the release, a
    `database was locked` WARNING, no traceback file.
- [x] New `tests/test_cluster_nodb_finish.py`:
  - `test_receiver_counts_written_results[in-memory, database]` (unit): a `Receiver` fed bundles
    through a stand-in queue; in memory every result counts; with a database (`task_db`), a result
    applied once counts 1 and its duplicate does not.
  - `test_no_db_work_done_when_every_result_is_written` (unit): a stand-in in-memory server (alive,
    `scheduler` None, receiver not alive, `receiver.machine.written == 5`) and an ended submitter
    with `task_count == 5` → `work_remains` False, within 1 s (red: the base says True after 3 s).
  - `test_no_db_work_still_remains[one-missing, input-open]` (unit, guard): `written == 4` with the
    submitter ended, and a submitter still alive → True.
  - `test_no_db_without_submitter_still_has_no_work` (unit, guard).
  - `test_no_db_cluster_whose_client_dies_after_its_last_result_keeps_running` (integration):
    `hs cluster tasks.txt --no-db --no-tls -H 127.0.0.1 -p <free> --launcher "<sys.executable>
    <tmp_path>/crashy.py"` (the wrapper patches `ClientThread.wait_heartbeat` to `os._exit(1)` and
    drops the remote-exe word; research 03 §4), five tasks; `wait(13)` must time out; stdout holds all
    five results; a WARNING with `exited with status 1`; no `every launched client has exited`; then
    `kill_group`.
- [x] Guards carry `still` in their names (the verify selects on it); no red test does.
- [x] Record the red list (the failing node ids from the verify log) in the commit body.
- **Expected red on `9c82842d`** (`-k "not still"`; all fail, none passes, no error), 22 node ids:
  `test_database_locked` ×6 (no such name); `test_locked_check_is_skipped_with_one_warning` ×6
  (`OperationalError` from `client_registrations`, `remote.py:392`, or `current_group`, `:599`);
  `test_exits_during_a_skipped_check_are_counted`, `test_limit_off_relaunches_after_a_skipped_check`
  (`OperationalError`); `test_watch_reads_again_after_a_locked_read` (the thread ends with
  `OperationalError`); the three locked CLI tests (exit 5 at ≈ 7–9 s with `CRITICAL … database is
  locked` and a traceback file); `test_receiver_counts_written_results` ×2 (no `written`);
  `test_no_db_work_done_when_every_result_is_written` (True after 3 s); the crashy CLI test
  (`ClusterLaunchError`, exit 5 at ≈ 5 s). The builder may split or merge rows; the count follows.
- **Recorded red at build (Python 3.14), 24 node ids:** `test_database_locked` ×7 (busy, table-locked,
  table-locked-extended, no-code; disk-io, not-sqlite, not-operational) and
  `test_database_locked_by_a_held_lock` (the error a real held lock raises), all `AttributeError`;
  `test_locked_check_is_skipped_with_one_warning` ×6, `test_exits_during_a_skipped_check_are_counted`,
  `test_limit_off_relaunches_after_a_skipped_check` (`OperationalError: database is locked`);
  `test_watch_reads_again_after_a_locked_read` (the watch ended with `OperationalError`); the three
  locked CLI tests (CRITICAL `database is locked` and a traceback file); the receiver count split
  into `test_receiver_counts_written_results_in_memory` / `_with_a_database` (no `written`);
  `test_no_db_work_done_when_every_result_is_written` (True); the crashy CLI test
  (`ClusterLaunchError`). Guards: 7 green.
- **Expected green on `9c82842d`:** the 7 `still` guards, and `tests/test_cluster_exit.py` and
  `tests/test_autoscale_failed_launches.py` unchanged (R6 is already covered there:
  `test_launcher_cluster_exits_when_every_client_fails[*-no-db]`,
  `test_cluster_reading_open_input_exits_when_every_client_fails[no-db]`).
- **Verify:** see frontmatter (the red selection exits 1 with only failures; the guards and the two
  pinned files pass). Dry-run at plan time against the prototypes: 9 red, 2 guards green; the kept unit prototypes again at commit time: 5 red, 2 green.
- **Touches:** `tests/test_cluster_locked.py`, `tests/test_cluster_nodb_finish.py`.

## Phase P2 — The cluster's own checks skip a locked read
**Satisfies:** R1, R2, R3, R4 · **Depends on:** P1
**Goal:** a locked SQLite read in an autoscaler check or in the every-client-exited check is one
WARNING and a decision at the next pass; exits seen meanwhile still count; any other error ends the
run as today.

- [ ] `cluster/watch.py`: `database_locked(error)` (PLAN §2.1), exported; docstring with what it
      accepts and why the mask.
- [ ] `cluster/watch.py::work_remains`: both `scheduled_work` reads in one `try`; a locked read →
      WARNING `Could not read whether tasks remain: database was locked (reading again)`, `False`;
      anything else re-raised. Docstring: a locked read is read again at the next poll.
- [ ] `cluster/remote.py::AutoScaler`: `unchecked` (class attribute annotation, `[]` in `__init__`);
      `check()` per PLAN §2.2 (`unchecked += clean()`, `last_check` set, `count_failures(unchecked)`
      under the catch, cleared after it returns); `check_fixed`/`check_dynamic` wrap `check_zones`
      the same way; `skip_check()` logs the WARNING and returns `WAIT`. Comment in `count_failures`:
      its read precedes every change to the run of failures. Class docstring: a check that meets a
      locked database is skipped, and exits it saw are counted at the next check.
- [ ] No edit to `demand.py`, `AutoScalingCluster`, `AutoScalerThread`, `relay/`, `topology.py`.
- **Verify:** see frontmatter (P1's locked tests green with the autoscaling and relay suites; the
  four locked drive modes; `server.py`, `demand.py`, `data/`, `relay/`, `topology.py` unchanged).
  Dry-run at plan time: the pytest files other than the new one are green on the base; the four
  drive modes are red on the base and green twice with the design emulated.
- **Touches:** `src/hypershell/cluster/watch.py`, `src/hypershell/cluster/remote.py`.

## Phase P3 — A `--no-db` cluster counts the results its server wrote
**Satisfies:** R5, R6 · **Depends on:** P2
**Goal:** without a database, "every result written and the input ended" is no work, exactly; a
client that dies after its last result is warned about and the run ends as before #107; work left
still raises within 10 s.

- [ ] `server.py::Receiver`: `written: int` (annotation with a trailing comment, `0` in `__init__`);
      `self.written += 1` as the last statement of the APPLIED tail of `update_tasks`. Nothing else
      in `server.py`: no existing line changes (the verify counts 0 deletions).
- [ ] `cluster/watch.py::work_remains`, no-database branch per PLAN §2.4 (`written <
      submitter.task_count` once the input has ended; a live submitter is work; no server or no
      submitter is none). Remove `MEMORY_SETTLE`, the `memory_settle` parameter, its `__all__` entry
      and the known-gap paragraph; the module and function docstrings say the count is exact behind
      the `results_written` barrier.
- **Verify:** see frontmatter (the count tests and the existing cluster, `--no-db`, server and
  receiver suites; `nodb-crashy` and `nodb-fail`; `MEMORY_SETTLE` gone; `server.py` only gains lines,
  at most 8). Dry-run at plan time: the existing files are green on the base, `nodb-crashy` red and
  `nodb-fail` green on the base, both green twice with the design emulated (`nodb-fail` 9 s → 4 s).
- **Touches:** `src/hypershell/server.py`, `src/hypershell/cluster/watch.py`.

## Phase P4 — Gate
**Satisfies:** R1–R7 · **Depends on:** P3
**Goal:** the whole change holds together: every drive mode, the whole suite serially under 3.14 and
3.11, and nothing outside the fence moved.

- [ ] Run the verify. A failure in a test unrelated to this change that passes alone on a rerun is
      recorded in the commit body with its node id and both outcomes (the 3.11 suite had one such
      failure in an earlier cycle); one that fails twice stops the phase.
- [ ] Commit body: the drive results per mode and the suite totals per Python.
- **Verify:** see frontmatter. Dry-run at plan time: the fence check passes and the whole suite on the
  base is green under both Pythons (totals in the plan commit); the drives as in P2/P3.
- **Touches:** `spec/cluster-launch-followups/TECH.md` only.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the `current_phase`
   pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
