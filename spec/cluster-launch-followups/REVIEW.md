# REVIEW — A locked database delays a cluster's decision instead of ending the run; a `--no-db` cluster whose work is done never reports a failed launch

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `836ae5bc`  ·  **Base:** `origin/develop` (`bf03942d`; merge base `9c82842d`)
  ·  **Date:** 2026-11-21
- **Verdict:** approved. No CONFIRMED finding; one PLAUSIBLE finding (F2) needs the maintainer's
  ruling before `/hs-publish`.
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`.

## How this cycle was run

- **Blind correctness pass:** one fresh reviewer. It was given `GOAL.md` (with its GOAL-gate block),
  the diff `git diff origin/develop...HEAD -- . ':(exclude)spec/'`, the base copies of
  `invariants.md`, `review-rubric.md` and `AGENTS.md`, and the binding readings from the two gates:
  - one WARNING per skipped check or failed read;
  - R5 restores the pre-#107 ending (the eviction wait), not a prompt end;
  - the Receiver counter is accepted, additions only;
  - relay site fleets and `hs server --autoscaling` may inherit the skip without their own requirement;
  - `MEMORY_SETTLE` is removed;
  - the working-launcher drive takes the lock only once the server is idle, and a lock held while
    clients work is a Non-goal;
  - the lock helper lives in `cluster/watch.py`, and R6 may rest on existing tests.

  It did not read `PLAN.md`, `TECH.md`, `META.md` or `research/`, and its searches excluded `spec/`.
- **Orchestrator:** checked contract drift and merge status, re-graded every finding, and ran its
  own merged-tree tests and a `--no-db` drive.
- **Contract drift:** `GOAL.md` changed only in the shaping commit (`17ef8123`) and the GOAL gate
  (`ffdd53cb`), which added the ruling block. Nothing moved during the build.
- **Environments:** fresh CPython 3.14.6 and 3.11.18 environments, not the worktree's 3.15 `.venv`.
  Tests ran serially. Every CLI drive used `.agents/factory/bin/temp_site.sh`, `-H 127.0.0.1`, an
  explicit free port and `--no-tls`.

## Verification run

- **Whole suite, 3.14, serial** → 3250 passed, 138 skipped, 0 failed (24 min 44 s).
  - The skips are environmental: PostgreSQL and TimescaleDB are not configured, fish is not
    installed, and some cases regenerate fixtures or are version-specific.
  - `test_waits_for_slow_peer_upgrade` passed on the first run.
- **New test files, 3.14 and 3.11.18:** `tests/test_cluster_locked.py` and
  `tests/test_cluster_nodb_finish.py` → 31 passed under each.
- **Related files, 3.14:** `test_cluster_exit.py`, `test_autoscale_failed_launches.py`,
  `test_zone_autoscale.py` and `test_relay_topology.py` → 206 passed.
- **Red on the base (R7):**
  - Setup: a scratch worktree at `9c82842d`, with the branch's two test files checked out into it
    and the base `src` imported (`MEMORY_SETTLE` present).
  - Result: 24 failed, 7 passed, each failure for the right reason:
    - the locked check and watch tests fail on `OperationalError: database is locked`, from the
      registration read and from the sizing read in `Task.current_group`;
    - the locked CLI tests fail on a `CRITICAL … database is locked` line and an `exception-*.log`
      file, and the limit-off test fails because the cluster exited;
    - the `--no-db` CLI test fails because `ClusterLaunchError` was raised after all 5 results were
      written;
    - the `--no-db` predicate test fails because work reads as remaining;
    - the Receiver-count and `database_locked` tests fail on the missing names.
  - The 7 that pass on the base are the tests of behaviour this cycle must preserve (R4, R6) and the
    session-reuse test.
- **Unit probe of `AutoScaler.check`** (real SQLite, `BEGIN EXCLUSIVE` held from a second connection):
  - three launches exit 1 across two skipped checks;
  - `unchecked` grows `[1]`, then `[1, 2]`, and `failures` stays `[]`;
  - the next good check reaches FINAL with `exhausted`, `failures == [1, 2, 3]` in launch order and
    `unchecked == []`;
  - when the count reads and the sizing read is locked, the count is kept, `unchecked` is cleared,
    the state goes to WAIT with nothing pending, and one warning is logged.
- **CLI drives** (lock times in seconds from start; each drive stopped its own process group):

  | Drive | Arguments | Lock | Outcome |
  |---|---|---|---|
  | A | `--autoscaling fixed -P 2s --launcher false` | 3–12 | Exit 5 at 14.5 s with "3 launched clients in a row exited non-zero". The client that exited during the skipped check is counted. One "Autoscale check skipped: database was locked" WARNING; no traceback file. |
  | B | A with `HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=0` | 3–12 | Running at 26 s (14 s after release), 7 relaunches after the skip. One lock WARNING; no CRITICAL; no traceback file. |
  | C | `--launcher false` | 0.5–9.5 | Exit 5 at 12.45 s (2.9 s after release) with #107's R1 error. One "Could not read whether tasks remain: database was locked" WARNING; no traceback file. |
  | H | `--ssh node-a`, with a stand-in `ssh` that exits 255 | 0.5–9.5 | Exit 5 at 12.43 s (2.9 s after release) with R1's error naming `node-a exited with status 255`. One lock WARNING; no traceback file. |
  | G | `--autoscaling fixed -P 2s --launcher env -T 2s` | 10–19, once the server is idle | Serving at 35 s. Output `0`–`4`, all 5 tasks complete in the database. One lock WARNING; no CRITICAL; no traceback file. |
  | D | `--no-db --launcher <client that exits 1 in place of FINISHED>` | none | Running at 25 s. All 5 results on standard output; the exit is warned about; no R1 error. |
  | D2 | D, run to its end | none | Exit 0 at 602.8 s after "Evicting client", every result written: the pre-#107 ending. |
  | E | `--no-db --launcher false` | none | Exit 5 at 4.4 s with R1's error. |
  | F | `--no-db --launcher env`, third task `kill -9 $PPID` | none | Exit 5 at 2.8 s with R1's error naming signal 9; only results 0 and 1 written. |
  | F′ (orchestrator) | `--no-db -N 4`, tasks `echo own1` and `sleep 12; kill -9 $PPID` | none | Exit 5 at 15 s with R1's error; `own1` written. |
  | R4 | A, with `DROP TABLE client` at 3 s | none | The same ending on the branch and on the base: exit 5 at 4.6 s, "Uncaught exception from AutoScaler" with `no such table: client`, and a traceback file. |

- **Merged-tree tests (orchestrator):** a scratch merge of `origin/develop`,
  `origin/fix/env-config-keys` and this branch, run under 3.14 on `test_cluster_locked.py`,
  `test_cluster_nodb_finish.py`, `test_autoscale_failed_launches.py`, `test_cluster_exit.py`,
  `test_cluster_launch.py`, `test_relay_launch.py` and `test_period_floor.py` → 469 passed,
  1 skipped.
- **Docs:** this cycle touches none, so they were not built (see F2).

## Merge status

`git merge-tree --write-tree` gives a clean merge in each case:
- onto `origin/develop` (`bf03942d`, one `ROADMAP.md`/`issues/` commit ahead of the merge base);
- onto `origin/fix/env-config-keys` (`085538c8`);
- onto the merge of both, the order if #113 lands first.

That branch also edits `cluster/remote.py` (`configured_failed_launches`) and `server.py`
(`evict_after`), in hunks apart from this cycle's. The merged-tree run above found no semantic
conflict.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `cluster/remote.py`: `AutoScaler.check`, `check_fixed`, `check_dynamic`, `skip_check`, using `cluster/watch.py::database_locked` | `test_locked_check_is_skipped_with_one_warning` (fixed and dynamic × registrations, sizing at the limit, sizing with the limit off); the two autoscaling CLI tests; drives A and B | ✅ |
| R2 | `AutoScaler.unchecked` carries exits to the next check that reads | `test_exits_during_a_skipped_check_are_counted`; `test_limit_off_relaunches_after_a_skipped_check`; the failed-launch CLI test; the probe; drives A, B and G. The working-launcher clause rests on drive G alone (F1) | ✅ |
| R3 | `cluster/watch.py::work_remains`: a locked `scheduled_work` read warns and returns False, so the watch reads again at its next poll | `test_watch_reads_again_after_a_locked_read` (asserts ≤ 10 s from release); `test_launcher_cluster_with_a_locked_database_exits_with_the_launch_error`; drives C and H (`--ssh`). `--mpi` was not driven: it runs the same `watch_launched` through `RemoteCluster` | ✅ |
| R4 | `database_locked` is true only for a SQLite error whose primary code is `SQLITE_BUSY` or `SQLITE_LOCKED`, or for an error with no code and SQLite's exact lock message; anything else re-raises | `test_database_locked` (codes 5, 6 and extended 262, and the message with no code, are locks; `SQLITE_IOERR`, a non-SQLite `orig` and an `IntegrityError` are not); `test_other_operational_errors_still_end_the_check` and `…_the_watch` (pass on the base and the branch); drive R4, the same ending on both. A PostgreSQL error is re-raised by reading only (its `orig` is not a `sqlite3` error); the PostgreSQL tests were skipped here | ✅ |
| R5 | `server.py`: `Receiver.written` (+3 lines, 0 removed); `watch.py`: once the input has ended, work remains only while `written < submitter.task_count` | `test_receiver_counts_written_results_in_memory` and `…_with_a_database`; `test_no_db_work_done_when_every_result_is_written`; `test_no_db_cluster_whose_client_dies_after_its_last_result_keeps_running`; drives D and D2 | ✅ |
| R6 | The same predicate: input still open, or fewer results written than tasks submitted, is work | `test_no_db_work_still_remains[one-missing, input-open]`; #107's `--no-db` tests in `test_cluster_exit.py` (unchanged, passing); drives E, F and F′ | ✅ |
| R7 | P1 changes only tests and precedes the source changes; locks are held by a raw second connection; every subprocess, thread and wait is bounded | The red run on the base above: R1, R2, R3 and R5 each have a test that fails on `9c82842d` for the right reason and passes on the branch | ✅ (coverage note F1) |

- **`server.py`:** additions only (`written: int`, `self.written = 0`, and `self.written += 1` after
  an applied result). The server never reads the counter, so its behaviour is unchanged.
- **`MEMORY_SETTLE`:** `grep -rn "MEMORY_SETTLE\|memory_settle" src tests docs` finds nothing. The
  path with a database is unchanged, and #107's `--no-db` tests pass.
- **Relay `SiteScaler`:** it overrides only `check_zones`, so the skip in `check_fixed`/`check_dynamic`
  covers it. With `failed_launches` at 0, `unchecked` is cleared at every check.
  `test_relay_topology.py` passes.

Unmapped changes: none in product code.
- `ROADMAP.md` and the two seeds change status to `adopted:cluster-launch-followups`; both links
  resolve.
- The rest are docstrings, comments, and `MEMORY_SETTLE` leaving `watch.__all__`.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### F2 — [HIGH/PLAUSIBLE] No `versionchanged` marker for an autoscaling cluster no longer ending on a locked database
- **Where:** `docs/cli/cluster.rst`, next to the 2.9.0 markers for `--autoscaling` (`:50-54`).
  Possibly also `docs/cli/server.rst` for `hs server --autoscaling`, which inherits the skip.
- **Scenario:**
  - The sizing reads predate this cycle; 2.8.7's `AutoScaler.check_fixed` reads
    `Client.count_connected()`, `Task.count_remaining()` and `Task.current_group()` with no guard
    (`git show 2.8.7:src/hypershell/cluster/remote.py`).
  - On the stable line, then, a locked SQLite database during an autoscaling check ends the
    autoscaler with a traceback. The branch warns and checks again.
  - AGENTS.md § Docs: "behaviour that differs carries `.. versionchanged:: X.Y.0`". The project
    marks comparable fixes, for example `docs/_include/database.rst:124-126` (a retry recorded
    twice).
  - The registration read, the R3 read and the `--no-db` change are all #107's and unreleased. The
    existing 2.9.0 markers already describe their intended behaviour ("with tasks remaining").
- **Evidence:**
  - `git diff origin/develop...HEAD -- docs` is empty.
  - The base red run shows the sizing read failing on the lock (`sizing-off`, `sizing-at-limit`).
  - Drives A, B and G show the branch's behaviour.
- **Why PLAUSIBLE:** the locked GOAL says "No new option, configuration key, wire change or doc page"
  and "The new WARNING lines are log output only". Whether that settles the marker question for the
  part that predates #107 is a ruling for the maintainer, not a reproduced defect. The same call was
  graded this way in `spec/size-and-duration-units/REVIEW.md`.
- **Triage:**
  - If a marker is required: one clause under `docs/cli/cluster.rst`, for example "With
    `--autoscaling`, a check that finds the SQLite database locked is skipped with a warning and
    made again one period later, instead of ending the run." Adding it means a build pass and a
    review cycle 2 limited to docs.
  - If not: accept as is.
- **Touches:** invariants §12 (HIGH if ruled required). No code, so it never triggers the human gate
  on its own.

### F1 — [LOW · coverage note, non-blocking] R2's working-launcher clause is pinned by no test
- **Where:** `tests/test_cluster_locked.py`. Every CLI test there uses `gated_launcher`, which exits 1.
- **Scenario:** R2's "one whose clients work SHALL complete every task, with the same output as an
  unlocked run, and keep serving" has no regression test.
- **Evidence:**
  - Drive G shows the behaviour is correct: every task completes and the cluster is still serving
    at 35 s, with one lock warning and no traceback.
  - The code path (the skipped sizing read) is the one the pinned R1/R2 tests exercise.
- **Why not CONFIRMED:** no wrong behaviour was observed. R7 holds: R2 is pinned by tests that fail
  on the base. The Evidence plan names this case as a drive. This follows
  `spec/task-input-fidelity/REVIEW.md` N1; `spec/contended-writes/` applied the opposite reading,
  and that rubric ambiguity is recorded in `META.md`.
- **Triage:** accept, or add a CLI test of drive G if F2 brings a build pass anyway.

### Candidates tried and dropped

- **A registration and exits seen together at the check after a skip.** The exits are discarded,
  exactly as in an unlocked run whose period covers both events.
- **Exits counted out of launch order across skipped checks.** `unchecked` keeps the order in which
  checks saw the exits. An unlocked run counts them in that same order, so the outcome matches.
- **Session state after a locked read.** Reads resume in drives A, C, G and H and in
  `test_session_still_reads_after_a_locked_read`. No `PendingRollbackError`.
- **The empty-bundle barrier meeting a locked database.** `apply_results([])` commits nothing, and
  drives C and H pass.
- **`written` over-counting in `--no-db`.** Without a database every outcome is APPLIED, there is no
  orphan recovery to deliver a task twice, and no stale, duplicate or deferred path. The one way to
  inflate the count is results for tasks from an outside producer, which needs the per-run key.
  That was not reached, so the candidate was not reproduced.
- **The submitter ending while a client exits, in `--no-db`.** The in-memory server tells clients
  to disconnect only after `wait_submitter`.
- **`TopologyPublisher`.** `hs server --autoscaling` with relay sites does not get the skip. The
  gate ruling allows that, and no requirement asks for it.
- **The margin in the launcher CLI test.** `LAUNCH_HOLD` of 7.5 s leaves about 1 s, but it was green
  in 3 runs and was not seen to flake.

## Human-gate triggers

- **Not triggered.** No CONFIRMED finding.
- `server.py` and `cluster/remote.py` are changed. The `server.py` change is additions only, and
  both files were found correct by tests and drives.
- F2 is PLAUSIBLE and docs-only, and F1 is a coverage note. Neither touches a security or
  DB-lifecycle invariant.

## Routing

- **Verdict:** approved.
- **Before `/hs-publish`:** the maintainer rules on F2 (add the marker, or accept as is) and on F1
  (accept, or add a test).
  - A marker or a test means `/hs-build`, then a review cycle 2.
  - Otherwise, go to `/hs-publish`.

## Optional completeness sub-pass

Not run (not requested).
