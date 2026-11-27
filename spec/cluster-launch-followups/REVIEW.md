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

## Review cycle 2 — approved (2026-11-21)

- **Reviewed commit:** `729dc347`  ·  **Base:** `origin/develop` (`bf03942d`; merge base `9c82842d`)
  ·  **Delta since cycle 1:** `836ae5bc..729dc347`
- **Verdict:** approved. No finding, CONFIRMED or PLAUSIBLE. F2 and F1 are remediated.
- **Mode:** scoped to remediation, on the foreman's brief. This cycle graded F2 and F1 and made a
  fresh blind pass over the delta (`docs/cli/cluster.rst`, `tests/test_cluster_locked.py`). The full
  spec-excluded diff was re-checked only where the delta touches it. `src/` is unchanged since
  `836ae5bc` (`git diff --quiet 836ae5bc..HEAD -- src`).

### How this cycle was run

- **Blind pass:** one fresh reviewer.
  - **Inputs:** `GOAL.md` with its GOAL-gate block; the delta and the full diff, both with `spec/`
    excluded; and the base copies of `invariants.md`, `review-rubric.md` and `AGENTS.md`.
  - **Binding readings:**
    - the GOAL-gate rulings;
    - the plan-gate rulings, including D2: the working-launcher case takes the lock only once the
      server is idle;
    - the review-cycle-1 rulings. F2 is one `versionchanged:: 2.9.0` clause in
      `docs/cli/cluster.rst` and none in `server.rst`. F1 is a command-line test of R2's
      working-launcher clause, red on `9c82842d` or naming the base behaviour it pins.
  - **Not read:** `PLAN.md`, `TECH.md`, `META.md` or `research/`. Its searches excluded `spec/`.
- **Orchestrator:** checked contract drift and merge status, and ran its own:
  - a red run on the base;
  - a merged-tree run;
  - a stable-line drive;
  - the docs build and the phase fences.

  It also judged the builder's two recorded deviations.
- **Contract drift:** `GOAL.md` changed only in `17ef8123` (shaping) and `ffdd53cb` (GOAL gate).
- **Environment:** CPython 3.14.6 (`/tmp/claude-501/venv314-clf-review`, an editable install of this
  worktree). Tests ran serially. Every drive used `.agents/factory/bin/temp_site.sh`, `-H 127.0.0.1`,
  an explicit free port and `--no-tls`.

### Verification run

- **The new test on the branch** (`test_autoscaling_cluster_with_working_clients_keeps_serving_after_a_lock`):
  passed, in 32.5 s for the reviewer and 32.2 s on the orchestrator's merged tree.
- **Red on the base (R7):** reproduced by the reviewer and the orchestrator independently.
  - **Setup:** `git archive 9c82842d` into a scratch directory, the branch's test file copied in,
    and the base `src` on `PYTHONPATH`. Checked: `hypershell.__file__` is the base copy, and
    `MEMORY_SETTLE` is present.
  - **Result:** 1 failed, at `assert ended is None` (`tests/test_cluster_locked.py:581`).
  - **Log:** `CRITICAL … Uncaught exception from <class 'hypershell.cluster.remote.AutoScaler'>`; an
    `OperationalError … database is locked` on the `client` registration SELECT
    (`client_registrations`, with failed launches at their default); and an `exception-*.log` file.
    It is red for the defect.
- **Timing probe (reviewer):** a timestamped copy of the test, run 9 times. Five runs were under 12
  `yes` processes and three under 30 (load average 28 on 10 CPUs), while the orchestrator's runs
  were also going. Every run passed with exactly one warning.
  - The lock lands 6–30 ms after the scheduler's 16 s back-off begins. The scheduler next wakes about
    7.5 s after the release, and the test has ended by then.
  - Checks come about 1.01 s apart. The first check to meet the lock starts 0.7–1.3 s after it. Its
    warning lands at lock + 5.7–6.3 s, and the release comes 2.2–2.8 s after the warning.
  - **Zero warnings** would need the first locked read to start after lock + 3.5 s. **Two** would
    need the release more than 5 s after the first warning. The margin is at least 2.2 s either way.
- **The whole module:** `tests/test_cluster_locked.py` → 25 passed (82.7 s, reviewer).
- **Merged-tree run (orchestrator):**
  - **Tree:** a scratch merge of `origin/develop`, `origin/fix/env-config-keys` (`cb5e122b`) and
    this branch.
  - **Result:** `test_cluster_locked.py` and `test_autoscale_failed_launches.py` → 54 passed (126 s).
  - **Why it matters:** the run used that branch's typed environment layer, and the new test relies
    on `HYPERSHELL_LOGGING_LEVEL=TRACE`.
- **Mutant (reviewer):** `skip_check` changed to return FINAL. The new test passes, and the six
  `test_locked_check_is_skipped_with_one_warning` cases fail. See the dropped candidates below.
- **Stable line, 2.8.7 (orchestrator):**
  - **Command:** `hs cluster tasks.txt --no-tls -H 127.0.0.1 --autoscaling fixed -P 2 --launcher
    false`, with the 2.8.7 `src` on `PYTHONPATH` and the lock held from 3 s to 12 s.
  - **Result:** exit 5 at 10.2 s, with `CRITICAL … Uncaught exception from … AutoScaler`, `database
    is locked` on the current-group SELECT, and a traceback file.
  - **Reviewer:** its own 2.8.7 drive (`-P 1`, the lock held from 3 s for 9 s) gave the same.
- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` under 3.12 → exit 0 (orchestrator
  and reviewer). The clause renders in `cli/cluster.html`.
- **Phase fences on `HEAD`:**
  - P2's retuned fence passes. Its old clause is red only on `server.py` (+3/−0).
  - P3's bound on `server.py` (0 removed, 2–8 added) passes at +3/−0, and `MEMORY_SETTLE` is absent.
  - P4's fence passes, with `docs/cli/cluster.rst` at +4/−0.

### Merge status

`git merge-tree --write-tree` gives a clean merge in each case:
- onto `origin/develop` (`bf03942d`);
- onto `origin/fix/env-config-keys` (`cb5e122b`, which has moved since cycle 1's `085538c8`);
- onto the merge of both.

That branch changes `cluster/remote.py` (`configured_failed_launches`), `server.py`
(`evict_after`), `cluster/launch.py` and `tests/test_autoscale_failed_launches.py`. This delta
touches none of them. The merged-tree run above found no semantic conflict.

### Remediation grades

**F2 (the `versionchanged` marker): remediated.** It is at `docs/cli/cluster.rst:56-58`: "With
`--autoscaling`, a check that finds the SQLite database locked is skipped with a warning and made
again at the next period, instead of ending the run."
- **Accurate and complete for a check.** The guards cover every read a check makes:
  - `check` wraps `count_failures`, the `client_registrations` read;
  - `check_fixed` and `check_dynamic` wrap `check_zones`, whose reads are `Task.current_group` and
    `starting_group`, `Client.count_connected`, and every read in `read_zone_loads`.

  START, INIT, SCALE and WAIT touch no database. `scheduler_group` and `registered` read server
  state in memory.
- **No overclaim.** The clause names "a check" only. The server's own threads still end the run on a
  lock: the scheduler's load, the heartbeat monitor's `Client` writes and the receiver. The GOAL's
  Non-goals exclude them, and the clause does not claim them. Plan-gate D2 puts that limit in the
  PR's description.
- **"The next period" is accurate.** `check` sets `last_check` before it reads, and `skip_check`
  returns WAIT. So the next check comes one period after the skipped one began, or at once when
  `-P` is shorter than the 5 s busy wait. The wording matches `--period`'s help ("waits for this
  period of time in between checks").
- **"Instead of ending the run" holds on the stable line.** The 2.8.7 drive above shows it.
- **`docs/cli/server.rst` correctly has no marker.**
  - `hs server --autoscaling` runs `TopologyPublisher` (`cluster/topology.py`), not `AutoScaler`,
    and that path is new in 2.9.0.
  - Relay site fleets (`SiteScaler`) do inherit the skip, but `hs relay` is new in 2.9.0 (not in
    2.8.7), so it needs no marker either.
- **Placement follows `AGENTS.md` § Docs.** The clause is one sentence, beside the other 2.9.0
  `--autoscaling` markers between the description and help includes, so no generated snippet is
  hand-marked. It has no `:pr:` yet (`hs-release` adds it), and no `_include` file changed. It is a
  marker, not a page, so the GOAL's "no doc page" stands.

**F1 (a command-line test of R2's working-launcher clause): remediated.** The test is at
`tests/test_cluster_locked.py:562-587`, with its helpers at `:457-488`.
- **What it asserts:**
  - every task is complete with exit status 0 (a database count);
  - standard output is `0`–`4`, as in an unlocked run;
  - the cluster is still running 6 s after the release;
  - exactly one `database was locked` WARNING;
  - no CRITICAL line and no traceback file.
- **It is red on `9c82842d` for the defect,** as shown above.
- **Only the autoscaler's reads meet the lock.** The lock is taken once every task is complete and
  the scheduler has logged a back-off of 16 s or more. That is longer than the 8.5 s hold, and
  nothing wakes the dispatcher while no client is connected. The heartbeat monitor, receiver and
  confirmer touch the database only on events. No client is relaunched during the hold: `size.min`
  is 0, there is no work, and `-T 1s` has retired the clients.
- **Deterministic and bounded.**
  - The readiness wait is at most `IDLE_READY` (45 s), and `BEGIN EXCLUSIVE` waits at most 10 s.
  - The joins are bounded at 58.5 s and 15 s, the cluster is waited 6 s, then `kill_group`.
  - "Exactly one WARNING" has the measured margin above, including under load.
- **The refactor leaves the earlier tests unchanged.**
  - `LockHolder.delay` keeps the old 0.5 s, and `condition` keeps the old error text.
  - `ready_timeout` still defaults to 30, and `hold_over` still defaults to `LockHolder`.
  - `start_cluster` without `out_path` writes to `os.devnull`, the same as `DEVNULL`.
  - The module's other 24 tests pass, on the branch and on the merged tree.

### The builder's recorded deviations

- **P2's fence drops `server.py`: accepted.**
  - The clause described P2's own commit (`7edac8cd`). P3's sanctioned `Receiver` counter (+3/−0)
    makes it red on every later tree, so re-running P2 for the remediation could never pass it.
  - Dropping it weakens nothing: P3's verify still bounds `server.py`, and it passes on `HEAD`. The
    friction is recorded as `META.md` F5.
  - A loose end in `spec/` (not graded): P2's prose "Verify" line in `TECH.md` still lists
    `server.py` among the files left unchanged.
- **P1 not reopened: accepted.**
  - P1's verify asserts `src` equal to the base, which cannot hold once P2 and P3 have landed.
  - R7 asks that each of R1, R2, R3 and R5 is pinned by a test red on the base. The new test's red
    run was reproduced here by two independent runs. The R2 test P1 already pinned
    (`test_exits_during_a_skipped_check_are_counted`) is unchanged.

### Requirement → evidence matrix (delta)

| R-ID | Delta change | Verified how | Status |
|------|--------------|--------------|--------|
| R1 | The marker documents the skip. The new test checks it on the command line: the registration read, with failed launches at their default. | The log shows one `Autoscale check skipped: database was locked (checking again in 0:00:01)`, no CRITICAL line and no traceback. The six unit cases (fixed and dynamic × registrations, sizing at the limit, sizing with the limit off) pass. | ✅ |
| R2 (working-launcher clause) | The new command-line test | Red on the base, green on the branch; 9 of 9 probe runs passed, under load too; passes on the merged tree | ✅ (closes cycle-1 F1) |
| R7 | The new test fails on `9c82842d` and passes on the branch. A second connection's `BEGIN EXCLUSIVE` holds the lock. | The two base red runs above | ✅ |
| §12 docs | One `versionchanged:: 2.9.0` clause | The docs build passes with `-W`; checked against 2.8.7 by the drive | ✅ (closes cycle-1 F2) |

R3 through R6 are untouched by the delta (`src` and `tests/test_cluster_nodb_finish.py` are unchanged
since `836ae5bc`), so cycle 1's evidence stands. Unmapped changes: none. The changes to
`LockHolder`, `hold_over` and `start_cluster` support the R2/R7 test, and they change no existing
test's behaviour.

### Findings

None.

### Candidates tried and dropped

- **Zero or two warnings under load.** The measured margin is at least 2.2 s on each side, and it
  held at load average 28.
- **The scheduler, or a relaunched client, meeting the lock during the hold.** The scheduler is in
  a back-off of 16 s or more and wakes about 7.5 s after the release. With no work and `size.min`
  at 0, no client is launched.
- **"Still serving" is only "process alive".** The mutant in which the autoscaler halts after a
  skip passes the new test.
  - That behaviour (R1's "check again at its next period") is pinned by the six unit skip cases,
    which fail on the mutant, and, by reading, by the limit-off command-line test.
  - R2's clause, keep serving, holds either way, since `--autoscaling` implies `--forever`.
  - So this is not a gap in what F1 asked for.
- **The marker landing in a later commit than the code.** The PR squash-merges into one commit, and
  the cycle-1 ruling directed the marker.
- **The warning's "checking again in {period}"** reads a few seconds long when the busy wait takes
  up part of the period. That text predates the delta and is accurate enough for its purpose.
- **`hs server --autoscaling` (`TopologyPublisher`) still ends on a locked read.** The GOAL's
  "may inherit" makes that a Non-goal. It is new in 2.9.0, so there is no stable-line change to
  mark.

### Human-gate triggers

- **Not triggered.** There is no CONFIRMED finding.
- The delta touches only `docs/cli/cluster.rst` and a test file. `src/` (including `server.py` and
  `cluster/remote.py`) is unchanged since cycle 1, which approved it without triggering the gate.
- No security or DB-lifecycle invariant is touched.

### Routing

- **Verdict:** approved. Cycle 2 of ≤3; the loop has converged.
- **Next:** `/hs-publish`. Per plan-gate D2, the PR's description should say plainly that a lock held
  while clients work still ends the run through the server's own threads.
