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

## Triage (human gate) — cycle 1

Cycle 1 (`ef0c653`, reviewing `31d8771`) found two HIGH findings in `server.py`, which triggers the human gate. Under the run's mandate the foreman acts as judge at every gate. These rulings date from 2026-11-03.

| Finding | Ruling | Disposition |
|---|---|---|
| F1 · HIGH · CONFIRMED | fix | `Scheduler.start` takes the last scheduled group as its starting group only while that group still has unfinished work (incomplete rows, retryable failures, or exhausted failures to halt on). Otherwise it takes `Task.lowest_incomplete_group()`. The halted-restart path (R6/R7 guard) is unchanged. **Red first:** the reviewer's deterministic replay (two ascending grouped files run in turn on one database) becomes a unit test that fails at `31d8771`. The R10 docs sentences must be true afterwards. |
| F2 · HIGH · CONFIRMED | fix, narrow | For file input, `hs cluster` starts the clients it manages (local, and any launcher or remote path it owns) only once the scheduler has started, so their `-T` idle clock never runs during ingest. This keeps "a command that succeeds today still succeeds" for `hs cluster FILE -T`. External clients of `hs server FILE` with a `-T` shorter than the ingest remain the accepted cost of the maintainer's ingest-first ruling. The docs sentence must say accurately what happens to them: they time out and the server waits for new clients. It must not say "may time out" and stop there. The per-group-count alternative is ruled out because the source gate is not on 2.8.3 (R9). **Red first:** an integration test, `hsx FILE -N2 -T <short>` on a file large enough that ingest outlasts `-T`, kill-timered, that hangs or fails at `31d8771` and exits 0 with every task completed after the fix. Size it so it runs in under about 60 s. If `hs cluster`'s client launch can't be deferred without touching code 2.8.3 lacks, stop and report. |
| F3 · LOW · CONFIRMED | fix | Restate `ROADMAP.md` and `issues/group-order-unsorted-inline-file.md` to match ingest-first. |
| Note: an empty task file hangs `hsx` (on HEAD and on base) | defer | Seed `issues/empty-task-file-hangs.md` (unshaped, fix, small, public) with a ROADMAP entry. |

Gates for cycle 2: P1's red tests plus the two new red tests pass. `test_groups.py`, `test_retry_gate.py` and `test_group_order.py` pass. The full suite passes once on each of 3.11, 3.12, 3.13 and 3.14. `port-trial.sh` passes, and the new diff applies to 2.8.3 + #68 without conflict.

## Review cycle 2 — changes-requested (2026-11-04)

- **Reviewed commit:** 5c7f381  ·  **Base:** b31c66e (the fork point; `origin/develop` has since
  moved to bc6547a with #72)  ·  **Date:** 2026-11-04
- **Verdict:** changes-requested — one CONFIRMED MEDIUM regression in `server.py` (N1), one
  CONFIRMED LOW prose fix (N2). F1, F2 and F3 are remediated.
- **Cycle:** 2 of ≤3, and the loop has not converged, so this goes to the human (the foreman, as
  judge).

**Mode:** one fresh blind reviewer (no `debate`) made a full pass over
`git diff b31c66e...HEAD -- . ':(exclude)spec/'`, graded against every R-ID. It also verified
cycle 1's findings against the triage, with particular attention to `ef0c653..5c7f381`.
- **It saw:** `GOAL.md`, this file (cycle 1's findings and triage), `invariants.md`,
  `review-rubric.md`, `AGENTS.md`, and the diff. The orchestrator also relayed the foreman's
  ratified F1 reading (2026-11-04) and the F2 probe list.
- **It did not see:** `PLAN.md`, `TECH.md`, `META.md` or `research/`. The orchestrator read only
  TECH's metadata and its environment notes (the offline venvs and the port trial's uv cache), and
  passed none of TECH's content to the reviewer.
- **Contract drift:** none since cycle 1. `GOAL.md`'s last commit is still `b242fc3`, which
  precedes the plan.

### Verification run

Every run used frozen offline environments, ran in a throwaway site, mostly with `--no-tls`, and
had its own process group and kill timer.

- **Unit suite, HEAD:** 356 passed on each of 3.11, 3.12, 3.13 and 3.14.
- **Full suite, HEAD 3.12:** 651 passed, 64 skipped, 0 failed on the first run. Neither known
  flake appeared.
- **`test_group_order.py`, `test_groups.py` and `test_retry_gate.py`, HEAD:** 53 passed in each
  of two runs. `tests/test_groups.py` is unmodified (`git diff b31c66e...HEAD --stat` is empty).
- **Red checks:**
  - At `31d8771`, the three new F1 unit tests fail with `[2, 0, 1] != [0, 1, 2]` on 3.11, 3.12
    and 3.14. The new F2 integration test `test_cluster_clients_with_timeout_wait_for_file` fails
    on its 180 s timer.
  - At `b31c66e` (3.12): 16 failed and 1 passed in 3 of 3 runs. R8 (a) and (b) fail for behaviour
    reasons (`current_group` 1 ≠ 0; `[] != [(0, id)]`). The 8 `is_file_input` cases fail only
    because the function does not exist on base.
  - At HEAD the F2 test passed in 51–54 s in three runs by the reviewer, and in 48.7 s in the
    orchestrator's own run.
- **R9 port, `port-trial.sh` (orchestrator), unedited, at 5c7f381 on `origin/master` 4d5771b
  (2.8.3):**
  - #68 hit its one known conflict, which the script resolved.
  - This cycle then applied with no conflict: `M` `client.py`, `cluster/local.py`,
    `cluster/remote.py`, `cluster/ssh.py`, `data/model.py`, `server.py`; `A`
    `tests/test_group_order.py`, plus #68's `tests/test_retry_gate.py`.
  - The group tests gave 53 passed in 121.7 s, with no rerun.
  - The trial worktree was removed, `master` stayed at dd0264c, and no other worktree changed
    except one unrelated to this review, which another session advanced.
  - **Environment substitution:** `UV_OFFLINE=1`, `UV_NO_GROUP=docs`, `UV_PYTHON_DOWNLOADS=never`,
    `UV_NO_CONFIG=1`, a local CPython 3.12.16, `UV_PROJECT_ENVIRONMENT` outside the repo, and
    `UV_CACHE_DIR` pointed at a private copy of a local uv cache that holds `hatchling`.
  - **Static check:** the `src/` changes are exactly those six files. No added or removed line
    touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP` or `_USAGE`. The reviewer also
    checked that every symbol the change relies on exists on 2.8.3 + #68.
- **Orchestrator's own reproductions:**
  - N1 and N2, both below.
  - The F1 reproducer: two ascending files in turn give `B0 B1 B2` at HEAD with no warning,
    against `B2 B0 B1` and 2 false warnings at `31d8771`.
  - With another listener holding the port, the first `hsx` run hangs on both HEAD and base, and
    later runs pass on both. This is pre-existing, so it is not a finding.
- **The optional restart flake:** `test_restart_update_adds_novel_and_runs` passed 25 of 25 at
  HEAD (3.12). The mechanism matches the diagnosis: the file is fully in before the restart
  scheduler starts. The 200-run figure was not re-run.

### Cycle-1 remediation

- **F1 — remediated.** On a database whose first run finished groups 0–2, each of these now
  runs `0 1 2` at HEAD with rc 0 and no warning, against `2 0 1` with 2 false warnings at
  `31d8771`:
  - two ascending files in turn;
  - `--repeat` of the same file;
  - `hs submit -f` then `hsx --restart`;
  - an ascending pipe.

  Hold probes:
  - **Retryable failure in the last group,** plain and `--eager`: A2 is retried first, then B0
    and B1 run with a WARNING naming both groups; rc 0. Base hangs.
  - **Exhausted failure in the last group with no new rows there:** the run halts with "Failed
    task group 2", the same as base.
  - **Exhausted failure in a middle group,** under `--restart` and `--repeat`: halts again.
  - **`--forever` with an exhausted failure:** waits, the same as base.
  - **R6:** a stranded database built with base runs group 0 under `--restart` at HEAD with rc 0;
    base hangs.
- **F2 — remediated.**
  - **Cycle 1's reproducer** (`hsx` on 10,000 `echo` tasks, `-N4 -T 3`): rc 0 in 87.9 s, with
    10,000 of 10,000 succeeded.
  - **The foreman's probe list:**
    - **A loader error mid-file:** rc 5 in 2.5 s with 0 scheduled. This holds for local,
      `--launcher env`, `-T 2`, and autoscaling fixed and dynamic. Base hangs under autoscaling.
    - **Bad input caught by the up-front read** (a bad tag, invalid UTF-8, an unreadable file, a
      directory): rc 5 in 0.3 s.
    - **SIGINT during the read:** rc 4 in 0.1 s on both, and HEAD schedules nothing. With
      `--launcher`, HEAD launches no client, while base leaves its launched client running.
    - **SIGUSR1 or SIGUSR2 during the read:** rc 0 after the whole read, with nothing scheduled
      and nothing left running.
    - **SIGTERM:** the same as base (see below).
    - **`--no-db`:** plain, `-T 2` and launcher runs are identical to base.
    - **Pipe, `/dev/stdin`, FIFO and `<(…)`:** the first task completes before the input ends.
    - **Autoscaling:** the first client launches only after the file is in; 6,000 of 6,000
      complete, in group order.
    - **`RemoteCluster.stop` and `SSHCluster.stop` before any client exists** (in process): no
      error, no launch after the stop, and nothing scheduled.
    - **An external `hs client -T 2` against `hs server` on a 12,000-task file:** the client exits
      during the read with nothing scheduled, and the server waits. A new client then completes
      all 12,000 and the server exits rc 0, so the new docs sentence is accurate.
- **F3 — remediated.** `ROADMAP.md` and `issues/group-order-unsorted-inline-file.md` now
  describe ingest-first. An unsorted `[2, 1, 0, -1]×3` file ran ascending in 6 of 6 runs.
- **The empty-file deferral — seeded**, `status: unshaped`, with a ROADMAP entry. Its mechanism
  (`Task.count() > 0` in the stop branch) is present at HEAD. The seed's wording is too broad;
  see N2.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.lowest_incomplete_group`, `Task.increment_group` (`data/model.py`) | Live `hs submit -g 0` into `--forever` after group 1 started runs it, with a WARNING (base strands it); unit (b) | ✅ |
| R2 | Same, plus `Scheduler.next_tasks` and `Scheduler.starting_group` (`server.py`) | Unit (a); 10k `-T 3` reproducer rc 0; R6 restart rc 0 | ✅ |
| R3 | `ServerThread.start_threads` + `is_file_input`; `starting_group` | Unsorted file 6/6 ascending; integration (c); F1 reproducers | ✅ |
| R4 | Atomic next-group query + `starting_group` | `hs submit` then `--restart` and an ascending pipe after a finished run give `0 1 2` | ✅ |
| R5 | `Scheduler.mark_group_started` | WARNING names both groups; none for ascending input from -1 or on restart | ❌ partial — N1 |
| R6 | Lowest-incomplete start | Stranded database built with base: base hangs, HEAD rc 0 | ✅ |
| R7 | #68 gates; the failure hold in `starting_group` | `test_groups.py` (unmodified), `test_retry_gate.py`; hold, halt and forever probes match base; `--no-db` identical | ✅ |
| R8 | `tests/test_group_order.py` | (a) and (b) red on `b31c66e` 3/3, green at HEAD, no sleeps, threads or repetition; (c) compares database times; new F1/F2 tests red at `31d8771` | ✅ |
| R9 | No schema, wire, CLI or config change; client launch deferred in four cluster/client files | `port-trial.sh` 53 passed, no conflict, `master` unmoved; static check | ✅ |
| R10 | `task_groups_alt.rst`, `AGENTS.md`, invariants §3 | Sentences present and true at HEAD; the `-T` sentence verified with an external client; CLI help and `share/` unchanged | ✅ (N2 is a seed, not R10) |

Unmapped changes: none. The `client.py` idle-clock reset belongs to the F2 fix.

### Findings

#### [MEDIUM/CONFIRMED] N1 — A non-integer group crashes the scheduler, where base runs it
- **Where:** `src/hypershell/server.py:271`, `Scheduler.mark_group_started`: `group < highest`
  (and `max(group, highest)` on the next line).
- **Failure scenario:**
  - The inline tag parser accepts `# HYPERSHELL: group:x`, and SQLite stores `x` as text in the
    integer column.
  - Once group 0 has started, `Task.next` commits `schedule_time` for the `x` row, and the
    comparison raises `TypeError: '<' not supported between instances of 'str' and 'int'`.
  - The scheduler dies and the run exits rc 5. The row is left scheduled and never completed.
  - The scope is SQLite only: PostgreSQL rejects a non-integer group at insert. The docs say
    groups are integers, so this is an edge path. But it breaks GOAL Clarifications → Blast radius:
    "A adds no new error path: a command that succeeds today still succeeds."
- **Evidence:** a two-line file, `echo a` then `echo b  # HYPERSHELL: group:x`, run with
  `hsx bad.txt -N1 --no-tls`.
  - **HEAD:** rc 5, with the `TypeError` in the Scheduler's CRITICAL log. The `x` row is
    `typeof(group)='text'`, scheduled, with no completion and no exit status. Reproduced by both
    the reviewer and the orchestrator.
  - **Base:** rc 0, and both rows completed with exit 0.
  - **At scale:** with 6,001 lines and one bad line mid-file, HEAD gave rc 5 after 53 s, while
    base ran all 6,001 with rc 0.
- **Touches:** R5's warning path, and the Blast-radius clause. `server.py` is core, so the human
  gate applies.
- **Suggested fix:** pick one; any of them is portable to 2.8.3:
  - make the warning's comparison tolerate a non-integer group, for example by skipping the
    late-group check when either value is not an `int`;
  - or coerce the group to an integer at ingest.

  Add the two-line file as a test.

#### [LOW/CONFIRMED] N2 — The empty-file seed and its ROADMAP entry claim more than happens
- **Where:**
  - `issues/empty-task-file-hangs.md:15` and `:19-20`: "`hsx` given an empty task file never
    exits" and "a run that submits no task never reaches FINAL";
  - `ROADMAP.md:233-235`.
- **Failure scenario:** both hold only on an empty database. On a database that already holds
  finished tasks, `Task.count() > 0`, so the stop branch is reached and the run exits 0.
- **Evidence:**
  - `hsx one.txt` then `hsx empty.txt -N2 --no-tls` on the same database: rc 0 in about 2 s on both
    HEAD and base (orchestrator).
  - On a fresh database, both time out (reviewer).
- **Touches:** prose accuracy of a graded seed (review rubric).
- **Suggested fix:** qualify both with "on an empty database".

### For the judge (not findings)

- **The F1 hold applies only when the last scheduled group has no incomplete rows**
  (`server.py:288`, `if Task.count_remaining(group=group) == 0`). If that group holds a failure
  and also gets new rows, HEAD starts at the lowest incomplete group instead.
  - **Exhausted failure in group 2, then a file with groups 0–2:** HEAD runs B0, B1, B2 and exits
    0 with nothing above group 2. Base runs B2 first, then halts with CRITICAL.
  - **Retryable failure in group 2, same file:** HEAD runs B0, B1, B2, then the A2 retry, and exits
    0. Base runs B2 and the retry, then hangs.
  - This keeps R3's order and the R7 gate: nothing above the failed group runs, and the retry
    still runs before the run stops.
  - The ratified reading ("failures hold the group") would start at B2 instead. Please confirm the
    implemented reading.
- **The idle-clock reset (`client.py:204`) also changes a standalone `hs client -d 4 -T 3`:** it
  now lives about 8.5 s, against 6.4 s on base, because the start delay no longer counts toward
  `-T`. That fits the help text ("shutdown if no tasks received"). Plain idle `-T` timings are
  unchanged.

### Out of scope, observed (pre-existing, unchanged by this diff)

- **A "database is locked" error during ingest hangs both HEAD and base.** The committer dies, and
  the loader then blocks on its full local queue, so the server never gets past `wait_submitter`.
  `wait_scheduling` cannot see the failure because the server thread is still alive. It may
  warrant a seed.
- **SIGTERM during the read leaves a process in the command's session alive for at least 25 s,**
  on both HEAD and base.
- **A library-level `cluster.stop()` during ingest blocks on both.** `SubmitThread.stop` and
  `SubmitThread.run` each put `None` on a size-1 queue. This path cannot be reached from the CLI.
- **An empty stream hangs, like an empty file.** The seed's Notes already ask for this to be
  checked.
- **The stream-stop seed reproduces** on both HEAD and base.

### Human-gate triggers

- **Triggered.** N1 is a CONFIRMED finding in `server.py`, which is high-blast-radius core.
- This is cycle 2 of ≤3 without convergence, so it escalates to the human: a human must sign off
  before any further step.

## Triage (human gate) — cycle 2

Cycle 2 (`0378250`, reviewing `5c7f381`) confirmed that F1–F3 are fixed. It requested changes for N1, a MEDIUM in `server.py`, which triggers the human gate. The foreman ruled on 2026-11-04 as judge. Cycle 3 is the last cycle allowed.

| Finding | Ruling | Disposition |
|---|---|---|
| N1 · MEDIUM · CONFIRMED | fix, narrow | A non-integer group stored by SQLite (for example `group:x`) must never crash the scheduler, and a file that runs on base must still run. Make the late-group warning's comparison (`Scheduler.mark_group_started`, `server.py:271-272`) tolerate a group that is not comparable with the tracked one: when the comparison is impossible, skip the warning and keep the tracked value unchanged. Do not coerce or reject at ingest; that would change what base accepts. **Audit** every comparison or arithmetic on group values this cycle added in `server.py` and `data/model.py` (`starting_group`, `next_tasks`, `lowest_incomplete_group` and any Python-side `min`/`max`) for the same flaw, and record the audit in TECH. **Red first:** the reviewer's two-line file (`echo 0 # HYPERSHELL: group:0` and `echo x # HYPERSHELL: group:x`, or the reviewer's exact form) through `hs cluster -N1 --no-tls` is an integration test. It must fail at `5c7f381` (exit 5 or a stranded row) and pass after the fix, with exit 0 and every task completed. Must remain portable to 2.8.3 + #68. |
| N2 · LOW · CONFIRMED | fix | Add "on an empty database" to `issues/empty-task-file-hangs.md` and its ROADMAP entry. |
| Reading: the F1 hold applies only when the last scheduled group has no new rows | confirmed | It keeps R3 and R7, and matches the cycle-1 ratification. Record it in TECH. |
| Reading: `client.py:204`'s idle-clock reset excludes the `-d` start delay from `-T` | confirmed | No command that succeeds today fails, and a client lives slightly longer. State it in the `-T` docs sentence or the `hs client` help notes, whichever already documents `-d`. |
| Pre-existing: "database is locked" during the read hangs, on HEAD and on base | defer | Seed `issues/database-locked-during-ingest-hangs.md` (unshaped, fix, small, public) with a ROADMAP entry. Leave the other pre-existing items as recorded in REVIEW.md. |

Gates for cycle 3:
- the N1 red test, then green;
- `test_group_order.py`, `test_groups.py` and `test_retry_gate.py` pass;
- the full suite once on each of 3.11, 3.12, 3.13 and 3.14;
- `port-trial.sh` passes, with the recorded uv cache substitution.

## Review cycle 3 — changes-requested (2026-11-04)

- **Reviewed commit:** eca86c8  ·  **Base:** b31c66e (the fork point; `origin/develop` is at
  bc6547a)  ·  **Date:** 2026-11-04
- **Verdict:** changes-requested, on two CONFIRMED LOW findings. O1, in `server.py`: the R5
  warning is lost for the rest of a run once a non-integer group starts first. O2: a seed added on
  this branch misdescribes how integration tests run. Neither changes run order, exit status or
  stored data. N1 and N2 are remediated, and F1–F3 stay fixed.
- **Cycle:** 3 of ≤3, the last one allowed. The loop has not converged, so this stops and goes to
  the human (the foreman, as judge).

**Mode:** one fresh blind reviewer (no `debate`) made a full pass over
`git diff b31c66e...HEAD -- . ':(exclude)spec/'`, graded against every R-ID. It looked hardest at
the cycle-2 remediation, `git diff 0378250..eca86c8 -- . ':(exclude)spec/'`.
- **It saw:** `GOAL.md`, this file (cycles 1 and 2 and their triage), `invariants.md`,
  `review-rubric.md`, `AGENTS.md`, and the diff. It was pointed at these files by path rather than
  given their text inline. The orchestrator relayed the foreman's rulings (F1 ratified, the
  `-d`/`-T` reading, N1 narrow), the N1 probe list and the known pre-existing items.
- **It did not see:** `PLAN.md`, `TECH.md`, `META.md`, `research/` or `port-trial.sh`. The
  orchestrator read only TECH's frontmatter, and META's finding headings only after the reviewer
  returned, to number the meta-note.
- **Contract drift:** none. `GOAL.md`'s last commit is still `b242fc3`, which precedes the plan.

### Verification run

Every run used frozen offline environments, ran in a throwaway site with `--no-tls`, and had its own
process group and kill timer.

- **Unit suite, HEAD:** 356 passed on each of 3.11, 3.13 and 3.14.
- **Full suite, HEAD 3.12:** 652 passed, 64 skipped, 0 failed in 393 s, with no rerun.
- **`test_group_order.py`, `test_groups.py` and `test_retry_gate.py`, HEAD 3.12:** 54 passed in
  each of two runs. `tests/test_groups.py` is unmodified against `b31c66e`.
- **Red checks:**
  - `test_cluster_runs_group_stored_as_text` at `5c7f381` failed 2 of 2 (`assert 5 == 0`). At HEAD
    it passes alone in 3.3 s, and in every group-file and full-suite run.
  - HEAD's `test_group_order.py -m unit` on a `b31c66e` extract: 16 failed, 1 passed, as in
    cycle 2.
- **R9 port, `port-trial.sh` (orchestrator), unedited, at eca86c8 on `origin/master` 4d5771b
  (2.8.3):**
  - #68 hit its one known conflict, which the script resolved.
  - This cycle then applied with no conflict: `M` `client.py`, `cluster/local.py`,
    `cluster/remote.py`, `cluster/ssh.py`, `data/model.py`, `server.py`; `A`
    `tests/test_group_order.py`, plus #68's `tests/test_retry_gate.py`.
  - The group tests gave 54 passed in 124.5 s, with no rerun. That includes the N1 test, so the
    N1 fix holds on 2.8.3 + #68.
  - The trial worktree was removed, `master` stayed at dd0264c, and the worktree list and every
    branch ref were unchanged.
  - **Environment substitution,** as in cycle 2: `UV_OFFLINE=1`, `UV_NO_GROUP=docs`,
    `UV_PYTHON_DOWNLOADS=never`, `UV_NO_CONFIG=1`, a local CPython 3.12.16,
    `UV_PROJECT_ENVIRONMENT` outside the repo, and `UV_CACHE_DIR` pointed at this review's own
    copy of the builder's uv cache, which holds `hatchling`.
- **Orchestrator's own reproductions:**
  - **N1's two-line file** (`group:0`, then `group:x`): rc 0 at HEAD and at base, both rows
    complete, no `TypeError`.
  - **O1,** through a `--forever` cluster fed one-line files by `hs submit -f`, 6 s apart:
    - `x, 5, 2`: HEAD runs all three with no late-group warning. Base runs `x` and strands 5
      and 2.
    - `5, 2` (control): HEAD logs "Starting task group 2 after task group 5".
    - `5, x, 2`: HEAD logs the same warning. Only a text group started first loses it.
  - **O2:** `tests/__init__.py:36`, `main()` calls `subprocess.run`.
  - A pipe with pauses between its lines left rows unscheduled with rc 0 on both HEAD and base.
    That is the known stream-stop item, so it is not a finding.

### Cycle-2 remediation

- **N1 — remediated.**
  - **Cycle 2's CLI case** (`echo a`, then `echo b  # HYPERSHELL: group:x`, through
    `hsx bad.txt -N1 --no-tls`): rc 0 at HEAD and at base, with both rows done. At `5c7f381` it
    gives rc 5 with the `TypeError`, and the `x` row is left scheduled.
  - **At scale** (6,001 lines in groups 0–2, with one `group:x` line mid-file, `-N4`): HEAD gives
    rc 0 in 51 s, with all 6,001 complete, the groups in order and `x` last.
  - **The foreman's probes,** at HEAD against base:
    - **Text and integer groups mixed across restarts.** HEAD gives rc 0 at every step, integers
      ascending and then the text groups, for each of: `--repeat`; a second file on the same
      database; `hs submit -f` then a bare `hsx --restart`; `--restart --update` adding `y`, 2
      and -1. Base hangs at each of these.
    - **A kill with `x` in flight, then `--restart`:** `x` is reverted and the run exits 0.
    - **A base-built database with 0 stranded below a finished `x`:** HEAD's `--restart` runs 0
      and exits 0.
    - **`--forever` with live submits** of `x`/1, then 0/`z`, then -1: HEAD runs `1, x, 0, z,
      -1` and warns "0 after 1" and "-1 after 1". Base strands 0 and -1.
    - **A late lower group after a text group started,** on a pipe: HEAD runs it, rc 0. Base hangs
      with it stranded. The warning gap is O1.
    - **Eager and plain, with failures in and around `x`:** HEAD matches base row by row. An
      exhausted failure in `x` as the last group gives rc 0, and a new file after it halts with
      "Failed task group x", the F1 hold as ruled. A retryable failure in `x` is retried. An
      exhausted failure in group 0 halts before `x`.
    - **`--no-db`:** identical, rc 0.
  - **Other values the ingest paths store in the group column.** Each runs to rc 0 at HEAD:
    - inline `1.5` is REAL and runs between 0 and 2;
    - inline `''` and `0x10`, JSON `"x"`, `[1]` and `{"a":1}`, and `hs submit -t group:x` are
      TEXT and run after the integers;
    - `true`, `1e3`, JSON `"7"` and `inf` are numeric;
    - inline and JSON `null` take the column default, 0.

    `hs submit -g x` is rejected by argparse. Inline `nan` is out of scope; see below.
  - **Audit of group comparisons:**
    - `lowest_incomplete_group` uses SQLite `min()`, which orders numbers before text.
    - `increment_group` and `starting_group` compare only for equality, which is safe across
      types.
    - `<` and `max` appear only inside the new `try`.
  - **PostgreSQL, by reasoning only:** `group` is INTEGER, so text is rejected at insert, as on
    base. Every value read back is an integer, so the `except` branch is unreachable there.
  - **Catching `TypeError`:** the `try` holds only the two group comparisons, an f-string and a
    `log.warning`, and every value comes from the database. It hides no other bug. Its side effect
    is O1.
- **N2 — remediated.** The seed and its ROADMAP entry now say "on an empty database". On a fresh
  database `hsx empty.txt` times out on both HEAD and base. After a finished run, it exits 0 in
  about 2 s on both.
- **The `-d`/`-T` reading — documented accurately** (`docs/_include/client_help.rst:91-92`). An
  idle `hs client -d 4 -T 3` lives 8.5 s at HEAD against 6.5 s at base, and `-T 3` alone lives
  4.5 s on both. CLI help text is unchanged.
- **The database-locked seed and its ROADMAP entry — accurate.** A 20,000-line file with
  `BEGIN EXCLUSIVE` held from 1.5 s for 60 s hangs both HEAD and base, killed at 120 s after the
  committer's `database is locked` CRITICAL. Stack dumps put the loader in `put_task`, and the
  server in `wait_submitter`, with HEAD's `LocalCluster` in `wait_scheduling`, as the seed says.
  Every `issues/` link in `ROADMAP.md` resolves.
- **F1, F2 and F3 stay fixed:**
  - F1: the mixed-group repeat, second-file, update and restart probes run ascending, and the F1
    unit tests pass.
  - F2: `test_cluster_clients_with_timeout_wait_for_file` passes.
  - F3: the prose is unchanged since cycle 2.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.lowest_incomplete_group`, `Task.increment_group` (`data/model.py`) | `--forever` live submits; a late group on a pipe; unit (b) | ✅ |
| R2 | Same, plus `Scheduler.next_tasks` and `Scheduler.starting_group` (`server.py`) | Every N1 probe exits 0 where base hangs; F2 test; unit (a) | ✅ |
| R3 | `ServerThread.start_threads` + `is_file_input`; `starting_group` | Mixed text and integer files run ascending; the 6,001-line run; integration (c) | ✅ |
| R4 | Atomic next-group query + `starting_group` | `hs submit` then `--restart`; a second file; `--repeat` | ✅ |
| R5 | `Scheduler.mark_group_started` | Warnings name both groups in integer runs and after an integer first group | ❌ partial — O1 |
| R6 | Lowest-incomplete start | A base-stranded database below `x` runs 0 under `--restart`, rc 0 | ✅ |
| R7 | #68 gates; the failure hold in `starting_group` | `test_groups.py` unmodified and passing; failure probes match base row by row; `--no-db` identical | ✅ |
| R8 | `tests/test_group_order.py` | (a) and (b) red on base; the N1 test red at `5c7f381`, green at HEAD; no sleeps, threads or repetition | ✅ |
| R9 | No schema, wire, CLI or config change | `port-trial.sh` 54 passed, no conflict, `master` unmoved; static check | ✅ |
| R10 | `task_groups_alt.rst`, `AGENTS.md`, invariants §3 | Sentences present and true at HEAD; CLI help and `share/` unchanged | ✅ |

Unmapped changes: none. The cycle-3 delta is the N1 `try`/`except` and its test, the ruled `-d`/`-T`
sentence, the N2 prose and the ruled database-locked seed.

### Findings

#### [LOW/CONFIRMED] O1 — A non-integer group started first turns off the R5 warning for the rest of the run
- **Where:** `src/hypershell/server.py:274`, `Scheduler.mark_group_started`. With nothing tracked
  yet, the line records the started group as `highest_started_group` without comparing it.
- **Failure scenario:**
  - On SQLite, stream input or a later `hs submit` starts a text group such as `x` first.
  - Every later comparison against `'x'` raises `TypeError`, which line 275 swallows, so the
    tracked value stays `'x'` for the rest of the run.
  - When an integer group then starts below an integer group that has already started, no WARNING
    is logged. R5 requires one.
  - Order, exit status and data are correct; only the log line is missing. Base strands the same
    input instead. The N1 ruling ("when the comparison is impossible, skip the warning and keep
    the tracked value unchanged") does not cover this first assignment.
- **Evidence:**
  - Reviewer, on a pipe: `x`, then 5, then a late 0. HEAD gives rc 0 and runs `x, 5, 0` with no
    WARNING. With `group:9` in place of `x`, HEAD warns "Starting task group 5 after task group 9"
    and "Starting task group 0 after task group 9". Base hangs (rc 124), with 5 and 0 stranded.
  - Orchestrator, on `--forever`: `x, 5, 2` gives no warning. `5, 2` and `5, x, 2` each warn
    "Starting task group 2 after task group 5".
- **Touches:** R5. `server.py` is core, so the human gate applies.
- **Suggested fix:** record as the highest started group only a value that is comparable with an
  integer group, so a text group never becomes the tracked value. Extend the N1 test or add a unit
  replay.

#### [LOW/CONFIRMED] O2 — A shaping-era seed and its ROADMAP entry misdescribe how integration tests run
- **Where:**
  - `issues/integration-test-hang-timeout.md:15`: "run `hs cluster` and similar commands in
    process through `tests.main`";
  - `issues/integration-test-hang-timeout.md:28`: "threads that do not exit inside an in-process
    run";
  - `ROADMAP.md:472`: "run commands in process".

  It was added on this branch in `9645a2d`, during shaping. Cycles 1 and 2 did not grade it.
- **Failure scenario:** `tests/__init__.py:36`, `main()`, runs every command through
  `subprocess.run` with no timeout, and no test builds a cluster or server in process. The seed's
  claim that a hang costs the CI job its whole `timeout-minutes` holds. Its in-process premise
  points the eventual fix at thread handling, where a subprocess timeout would be enough.
- **Evidence:** the reviewer ran
  `grep -rln "run_local\|LocalCluster(\|serve_from(\|serve_file(\|ServerThread(" tests/`, which
  returns nothing. The orchestrator read `tests/__init__.py:36`.
- **Touches:** prose accuracy of a graded seed (review rubric), as with cycle 1's F3.
- **Suggested fix:** say the tests run each command as a subprocess with no timeout, and drop the
  in-process thread clause from "Why it was deferred".

### Blocking or observation (for the judge)

- **Under the rubric, every CONFIRMED in-scope finding blocks.** So O1 and O2 set
  changes-requested. The reviewer labelled both observations, because neither changes order,
  exit status or data.
- **Both fixes are small.** O1 is one guard in `mark_group_started` plus a test. O2 is three
  sentences of prose.
- **This was the last review cycle allowed,** so the judge decides how they close.

### Out of scope, observed (pre-existing, unchanged by this diff)

- **Inline `group:nan` hangs both HEAD and base.** SQLite stores NaN as NULL, so the `NOT NULL`
  constraint kills the committer. It is the same mechanism as the database-locked seed, which
  could name it.
- **A run halted by an exhausted failure exits rc 0** on both HEAD and base.
- **The stream-stop item, the empty-input hang and the database-locked hang** reproduce on both,
  unchanged.

### Human-gate triggers

- **Triggered.** O1 is a CONFIRMED finding in `server.py`, which is high-blast-radius core.
- This is cycle 3 of ≤3 without convergence, so it stops and escalates: a human must sign off
  before any further step.

## Triage (human gate) — cycle 3

Cycle 3 (`0564295`, reviewing `eca86c8`) confirmed that N1 and N2 are fixed. It requested changes
for two LOW findings, O1 in `server.py` and O2 in seed prose. It was the last cycle allowed and did
not converge, so the foreman stopped and asked the maintainer. The maintainer ruled on 2026-11-04,
choosing "Fix + quick review": a narrow fix, then a quick scoped blind review of only that diff,
then publish.

The maintainer's seed instruction, verbatim:

> "I did not think we allowed non-integer group IDs. In SQLite, I understand the types are more of a suggestion; are you saying, that if a naive user applies a non-integer group ID, it breaks our logic in a way that is unexpected? If so, we should make a note, and commit an issues/ seed calling this out and have it scoped to guard against this by enforcing integer group ID at submit time both bulk (read ahead whole file) and over pipe (limiting to error on just the occurance)."

| Finding | Ruling | Disposition |
|---|---|---|
| O1 · LOW · CONFIRMED | fix, narrow | In `Scheduler.mark_group_started` (`server.py:274`), record a group as `highest_started_group` only when it is comparable with an integer group, so a text group never becomes the tracked value and the late-group warning still fires for later integer groups. Keep the N1 tolerance: a non-comparable group never raises. **Red first:** a deterministic test that fails at `eca86c8` and passes after the fix, on the reviewer's scenario (SQLite; `x` starts first, then 5, then a late lower integer group; the warning names both integer groups). A unit-level replay of the Scheduler path is preferred. |
| O2 · LOW · CONFIRMED | fix | Correct `issues/integration-test-hang-timeout.md` (lines 15 and 28) and its `ROADMAP.md` entry: the integration tests run each command through `subprocess.run` with no timeout (`tests/__init__.py:36`), not in process, and the fix direction is a subprocess or per-test timeout, not thread handling. |
| Maintainer's seed: a non-integer group is accepted at submit time | seed | Write `issues/group-tag-integer-validation.md` (unshaped, fix, small, public) with a `ROADMAP.md` entry. Problem: the inline `group:` tag accepts non-integer values, which SQLite stores as text and PostgreSQL rejects at insert; 2.8.3 runs them with text after integers, this cycle needed the N1 and O1 guards, and `group:nan` is stored as NULL and hangs the run on the committer's `NOT NULL`, on base too. Scope: enforce an integer group at submit time, refusing a whole file read ahead before anything is inserted, and erroring on just the offending occurrence of a pipe or stream, across inline tags and `--from-json`. Open for shaping: a behaviour change, so 2.8.x patch or 2.9 only. Cross-reference the database-locked seed and N1/O1. |
| Pre-existing items in "Out of scope, observed" | unchanged | `group:nan` is covered by the new seed. The others stay as recorded. |

Gates for the remediation:
- the O1 test fails at `eca86c8` and passes after the fix;
- `test_group_order.py`, `test_groups.py` and `test_retry_gate.py` pass;
- the full suite once on 3.12 and once on 3.14;
- `port-trial.sh` passes, with the recorded uv cache substitution.

Next: a scoped blind review (cycle 4, scoped) of only the remediation diff, then publish.
