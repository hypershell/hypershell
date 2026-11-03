# REVIEW — Run task groups in order, and never strand a late one

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 31d8771  ·  **Base:** `origin/develop` b31c66e  ·  **Date:** 2026-11-03
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

Mode: a single fresh blind reviewer (no `debate`). It saw `GOAL.md`, `invariants.md`,
`review-rubric.md`, `AGENTS.md` and `git diff b31c66e...HEAD -- . ':(exclude)spec/'`. The base is
pinned to `origin/develop` b31c66e: local `develop` is stale and is not an ancestor of the branch,
and `develop...HEAD` spans 87 files. **Contract drift:** `GOAL.md` has three commits (`d1db1b9`
shape, `86511a3` re-cut on Q1/Q2, `b242fc3` cut to part A). All three precede the plan commit, and
the foreman relayed the maintainer's rulings, so the contract did not move mid-build. Part B (the
submit-time refusal) is out of scope.

## Verification run

Every run used frozen offline environments built from `git archive` extracts, ran in a throwaway
site with `--no-tls`, and had its own process group and kill timer.

- **Unit suite, HEAD:** 353 passed on each of 3.11, 3.12 and 3.14. Base on 3.12: 339; the 14 new
  unit tests account for the difference.
- **Integration suite, HEAD 3.12:** 294 passed, 64 skipped (PostgreSQL, Timescale, fixture
  regeneration).
- **`tests/test_group_order.py` plus `tests/test_groups.py`, HEAD:** 27 passed in 3 of 3 runs.
  `tests/test_groups.py` is unmodified.
- **Red check:** HEAD's `tests/test_group_order.py` on a base extract fails 13 and passes 1, in 3 of
  3 runs.
  - The R8 (a) and (b) replays fail for behaviour reasons (`current_group` 1 ≠ 0; `[] != [(0, id)]`)
    and pass on HEAD.
  - The 8 `is_file_input` cases fail only because the function does not exist on base.
- **R9 port, `port-trial.sh`:** applied to 2.8.3 (`origin/master` 4d5771b), plus #68 with its one
  known conflict resolved, plus this cycle's `src/` and `tests/` with no conflict.
  - The group, retry-gate and group-order tests: 49 passed.
  - Ran with `UV_NO_GROUP=docs` (META F8).
  - After the trial, `master` is unmoved at dd0264c, the trial worktree is removed, and the worktree
    list is unchanged.
  - The reviewer's independent extract port also passed 49/49 group tests and 118/118 unit tests.
    It used the HEAD 3.12 interpreter because no 2.8.3 offline lock could be built.
- **CLI drives, HEAD vs base:**
  - unsorted file, 10 of 10 runs ascending on HEAD; base hung 1 of 3;
  - unsorted `--from-json` input runs ascending;
  - FIFO input still streams (first task runs before the second line arrives);
  - a live `hs submit -g 0` into a `server --forever` after group 1 started runs group 0, with the
    WARNING; base strands it;
  - a late lower group on a pipe runs, with the WARNING;
  - ascending input starting at -1 logs no warning;
  - a base-hung database with group -1 stranded runs -1 under `hsx --restart` and exits 0;
  - #68 gates (plain and `--eager`), halted run and halted `--restart` (CRITICAL; the higher group
    never scheduled), and a `--forever` server waiting on an exhausted failure all hold;
  - `--no-db` runs;
  - a mid-ingest loader error exits rc 5 promptly on both.
- **Stop during file read (60k lines):** identical on both — SIGINT rc 4 in 0.14 s; SIGTERM
  immediate; SIGUSR1 waits for the whole ingest (33 s HEAD, 35 s base), and HEAD schedules nothing.
- **Large file (300k lines):** peak RSS 182.7 MB on HEAD vs 184.5 MB on base, so memory is
  unchanged. Time to first task: 187.5 s on HEAD vs 1.07 s on base. The host was under load from
  other work, so timings are approximate. GOAL accepts this latency.
- **PostgreSQL, by reasoning only:** `lowest_incomplete_group` is one `min()` statement, so it reads
  one READ COMMITTED snapshot. Ascending input commits lower groups first, so the old window, in
  which a higher group was visible and the current group was not, is gone.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `Task.increment_group` + `Task.lowest_incomplete_group` (`data/model.py`) | Live `hs submit` into a running `--forever` server; late group on a pipe; unit replay (b) | ✅ |
| R2 | Same, plus `Scheduler.next_tasks` (`server.py`) | Unit replay (a); unsorted drives 10/10 | ❌ partial — F2 |
| R3 | `ServerThread.start_threads` + `is_file_input` (`server.py`) | Fresh database: text 10/10 ascending, JSON ascending, integration test (c) | ❌ partial — F1 |
| R4 | Atomic next-group query | `hs submit` then `hsx --restart` on a fresh database runs ascending; PostgreSQL by reasoning | ❌ partial — F1 |
| R5 | `Scheduler.mark_group_started` (`server.py`) | WARNING names both groups; none for ascending input from -1 | ✅ (false warnings under F1) |
| R6 | Lowest-incomplete next group | `hsx --restart` on a base-hung database; unit test | ✅ |
| R7 | #68 gates unchanged | Retry gate plain/`--eager`, halted run, halted `--restart`, forever wait, `--no-db`; `test_groups.py` 11/11 unmodified | ✅ |
| R8 | `tests/test_group_order.py` | (a), (b) red on base 3/3, green on HEAD 3/3, no sleeps or threads; (c) timing-independent | ✅ (no test covers F1) |
| R9 | No schema, wire, CLI or config change | `port-trial.sh` 49 passed, `master` unmoved; independent extract port | ✅ |
| R10 | `docs/_include/task_groups_alt.rst`, `AGENTS.md`, invariants §3 | Text present; CLI help and `share/` unchanged | ✅ in text (false under F1) |

Unmapped changes (possible scope creep): none. The new `issues/` seeds and their `ROADMAP.md`
entries are deferrals that `GOAL.md` references.

## Findings

### [HIGH/CONFIRMED] F1 — Groups run out of order on a database whose earlier work finished at a higher group
- **Where:**
  - `src/hypershell/server.py:209-211`: `Scheduler.start` seeds `current_group` from
    `Task.current_group()`.
  - `src/hypershell/data/model.py:548-565`: `Task.current_group()` returns the group of the most
    recently scheduled task, even when that group's work finished in an earlier run.
- **Failure scenario:** the server first runs the new tasks in that old group, then goes back down
  through the lower groups, and each lower group logs a false "(tasks submitted late)" WARNING. A
  persistent SQLite file is the default database, so any second grouped file on the default
  database hits this. The affected paths:
  - `hsx FILE --repeat` or `--update`;
  - a different file on a kept database;
  - `hs submit` followed by `hsx --restart`;
  - an ascending stream.
- **Evidence:**
  - Two files on one database, each listing groups 0, 1, 2 in order: `hsx A.txt -N2 --no-tls` then
    `hsx B.txt -N2 --no-tls`.
    - Run 2 schedules `B2, B0, B1`, logging "Starting task group 0 after task group 2" and the same
      for group 1. Reproduced independently by the orchestrator.
    - Base hangs on the same input, with B0 and B1 never run.
  - The same order, `[2, 0, 1]`, for:
    - `hsx A.txt` then `hsx A.txt --repeat`;
    - `hs submit -f B.txt` then `hsx --restart`;
    - `hsx < B.txt`.
  - A deterministic replay from HEAD's test helpers (an earlier run finished groups 0-2, then a new
    ascending file) gives `[2, 0, 1]` with both `restart_mode=False` and `restart_mode=True`.
- **Touches invariant / requirement:** R3 and R4. Part B does not exempt this: under Q3 (ii), a
  database whose work is all finished has no reached group, and the part B seed relies on
  `--repeat` of a grouped file working after a completed run. R10's new sentences in
  `task_groups_alt.rst`, `AGENTS.md` and invariants §3 are false in this case.
- **Suggested fix:**
  - Seed the starting group from the most recently scheduled group only while that group still has
    unfinished work: incomplete rows or retryable failures, or exhausted failures to halt on.
    Otherwise start from `Task.lowest_incomplete_group()`.
  - Keep the halted-restart path.
  - Add the replay as a unit test.

### [HIGH/CONFIRMED] F2 — A file run with client `-T` hangs forever where base completes
- **Where:** `src/hypershell/server.py:907-921`. `ServerThread.start_threads` joins the submitter
  before it starts the scheduler.
- **Failure scenario:**
  - Local clients sit idle through the whole ingest, reach their `-T` idle timeout and exit.
  - The scheduler then posts a bundle that nobody takes, and the run never finishes.
  - Ingest runs at about 1,600 rows/s at the default bundlesize, so for example a 100k-line file
    with `-T 60` hangs.
- **Evidence:** `hsx seq10k.txt -N4 -T 3 --no-tls -o /dev/null`, 10,000 `echo` tasks.
  - HEAD: killed at 120 s with 0 completed, 9,998 unscheduled and 2 posted to no one. Reproduced
    independently by the orchestrator.
  - Base: rc 0 in 85 s, all 10,000 complete.
  - The reviewer saw the same on 20,000 tasks: HEAD timed out at 150 s; base rc 0 in 184 s.
- **Touches invariant / requirement:** R2 and GOAL Outcome ("a run with work left never hangs"). It
  also breaks GOAL Clarifications → Blast radius: "a command that succeeds today still succeeds".
  The maintainer accepted the cost of losing the streaming start, but not a hang.
  `task_groups_alt.rst` notes that `-T` clients may time out during the read; it does not say that
  the run then hangs.
- **Suggested fix (one of):**
  - Use the lighter design the GOAL allows: a per-group census from the source gate's up-front read,
    so the lowest group can schedule before ingest ends.
  - Keep local clients from timing out before the first dispatch, for example by launching them, or
    starting their idle clock, only once scheduling starts.
  - At minimum, end the run with an error once every client has exited with work left, instead of
    polling forever.

### [LOW/CONFIRMED] F3 — An adopted seed and its ROADMAP entry misstate HEAD's behaviour
- **Where:**
  - `ROADMAP.md:195-198`;
  - `issues/group-order-unsorted-inline-file.md:17-25`, its Problem section.
  - Both are added on this branch.
- **Failure scenario:** both say that file input is scheduled while the file is still being
  ingested, and that after this cycle "group 1 can now finish before group 0 starts". At HEAD the
  file is ingested first.
- **Evidence:** the unsorted file ran ascending in 10 of 10 runs at HEAD.
- **Touches invariant / requirement:** prose accuracy of a graded seed (review rubric).
- **Suggested fix:** restate both to the ingest-first resolution.

### Out of scope, observed
- **An empty task file hangs `hsx` on both HEAD and base.** Reproduced by the orchestrator: both
  timed out at 25 s. It is pre-existing and unchanged by this cycle, and no seed covers it.
- **The three known pre-existing items are not made worse:**
  - the TLS localhost flake: TLS is untouched;
  - a stream run stopping before a pipe's input ends: identical on base and HEAD, and file input is
    now immune;
  - `test_waits_for_slow_peer_upgrade`: passed.

## Human-gate triggers

- **Triggered.**
  - F1 touches `server.py` (`Scheduler.start`) and the `data/model.py` group choice.
  - F2 touches `server.py` (`ServerThread.start_threads`).
  - Both are high-blast-radius core. A human must sign off before any further step.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
