# GOAL — Don't strand a task group whose tasks arrive late

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** group-advance-strands-lower-group
- **kind:** fix
- **appetite:** small

## Problem

`hs cluster FILE` and `hs server FILE` can hang forever with work left. The scheduler skips a task
group, never comes back to it, and polls without end after the last group, because the run still
counts those tasks as remaining. The process never exits, and in CI the job hangs until its
timeout.

Both commands ingest the task file on a submitter thread in the same process. That thread commits
rows (one commit per row at the default `submit.bundlesize`) while the scheduler is already
running. On a fresh database the scheduler starts at group 0 ("no tasks submitted"). When it finds
nothing to schedule it calls `Task.increment_group` (`src/hypershell/data/model.py:578-623` at
`b31c66e`). That method takes several separate steps: it counts the current group's incomplete
rows, checks for retryable and exhausted failures, and then picks the smallest group greater than
the current one. If the current group's rows are committed between the count and that last query,
and a higher group's row already exists, the scheduler moves past the current group. It only ever
moves up, so the skipped group is stranded. A file that lists a higher group first needs no
such window: the higher group's row can be the only one present when the scheduler looks. After the last group, `Task.count_remaining()` stays
above zero, `increment_group` reports "no task groups remain", and the run polls forever without
reaching FINAL (`Scheduler.load_bundle`, `src/hypershell/server.py:206-247`).

The exposure, from a diagnosis run for this cycle against an out-of-tree reproducer:

- **A task file that lists a higher group before group 0** hangs in about half of runs with no
  load: 5 of 10 on `b31c66e`, 7 of 10 on 2.8.3.
- **Groups below 0** are always exposed on a fresh database: the scheduler starts at group 0 and
  never moves down.
- **A file in ascending group order** hangs in about 1–3% of runs under CPU contention, CI
  included. Over 300 loaded runs each: 2.8.3 hung 9 times, `b819de6` 9, `6f216d0` 7, `b31c66e` 4.
- **Deterministic:** a 0.3 s delay on the submitter's first commit plus 0.5 s after
  `increment_group`'s first count hangs 20 of 20 runs on every commit tested.

**Pre-existing**, latent since task groups were added (`0844bf1`, first released in 2.8.0). It is
not caused by #68 (`group-advance-retry-gate`) or #70 (`writeback-scoped-conditional`). The docs
promise that groups are "scheduled in ascending order: every task in the active group must reach a
terminal state before any task in the next group is scheduled" (`docs/_include/task_groups_alt.rst`).
When a lower group's tasks arrive after a higher group has started, the code can keep that promise
only by never running them.

## Outcome / vision

Every submitted task runs, and a run with work left never hangs. A file that lists its groups in
ascending order, or a database filled before the server starts, runs its groups in ascending order
whatever the timing between the submitter and the scheduler. When a lower group's tasks arrive
after a higher group has started, the server goes back and runs them once the current group
finishes, and it says so in the log. A deterministic unit test pins the interleaving, so the defect
cannot return unnoticed. The change is small and self-contained enough to be cherry-picked to a 2.8.4
patch.

## Acceptance criteria (the contract)

Terms. A group has **started** once the server has scheduled at least one of its tasks in this run.
A group is **lower** or **higher** by its integer value.

- **R1** — WHEN tasks are committed in a group lower than the server's current group, whether a
  group the server has already passed or one below the group it started at, the server SHALL
  schedule and run them. This holds for an inline task file (`hs cluster FILE`, `hs server FILE`),
  for groups below 0, and for tasks given with `hs submit` to a running server, in `forever` mode
  as well.
- **R2** — IF tasks remain unscheduled and no group is held or halted by a failure (R5), THEN the
  server SHALL schedule them, and SHALL NOT keep polling with nothing in flight. A non-`forever` run
  SHALL reach FINAL and exit once every task has completed.
- **R3** — WHEN every task is in the database before the server starts scheduling (`hs submit`, then
  `hs server`), or an inline task file lists its groups in ascending order, the server SHALL run
  the groups in ascending order, scheduling no task of a group until every task of each lower group
  has reached a terminal state, as the docs state. This SHALL hold whatever the timing between the
  submitter's commits and the scheduler, including under CPU contention.
- **R4** — WHEN the server starts a group lower than a group it has already started in this run, it
  SHALL log a WARNING naming both groups. A group the server only waited on, with no task
  scheduled, does not count as started, so a file in ascending order that begins below 0 logs no
  such warning.
- **R5** — Existing gating SHALL be unchanged: a group holding a failure that still has retries
  left is not advanced past and does not let the run stop (#68); a group whose failures have used
  their retries stops a non-`forever` server, and a `forever` server waits; a cancelled task neither
  holds nor halts its group; runs with no groups or no failures behave as before.
  `tests/test_groups.py` SHALL pass unmodified.
- **R6** — Regression tests tagged `unit` SHALL replay, deterministically and without sleeps,
  wall-clock timing, racing threads or repetition, (a) the R3 interleaving: the current group's
  tasks, and a task of a higher group, are committed after the server has found the current group
  empty but before it chooses its next group; and (b) the R1 case: a lower group's tasks are
  committed after a higher group has started. Each SHALL fail on `b31c66e` and pass with the fix.
  The suite SHALL NOT gain a load or repetition test for this defect.
- **R7** — WHEN a server is restarted (`--restart`) on a database where a group holds unscheduled
  tasks below the last group it scheduled, as a run hung by this defect leaves it, the server SHALL
  run those tasks and, in non-`forever` mode, reach FINAL.
- **R8** — The fix SHALL change no database schema, wire format, CLI option or configuration key,
  and SHALL NOT depend on code that landed after 2.8.3 other than #68's group gate, so that it can
  be cherry-picked to a 2.8.4 patch (see Q2).
- **R9** — The task-group documentation (`docs/_include/task_groups_alt.rst`), and the
  group-gating text in `AGENTS.md` § Task lifecycle contract and `.agents/factory/invariants.md` §3,
  SHALL state the R1/R4 behaviour: tasks of a lower group that arrive after a higher group has
  started run once the current group finishes, with a warning. Ascending order is guaranteed only
  as R3 states. CLI help text is unchanged.
- **R10** — *Conditional on Q1.* `[NEEDS CLARIFICATION: Q1 — include only if the judge picks option
  (b)]` WHEN an inline task file that holds tasks in group 0 lists a higher group first, the server
  SHALL schedule no task of a higher group before group 0's tasks, and SHALL keep R3's ascending
  order from then on.

## Non-goals (no-gos)

- **Full ascending order for unsorted inline task files.** Beyond R4's warning, and R10 if Q1
  includes it, a lower group whose tasks arrive after a higher group has started runs late rather
  than first. Seeded: [`issues/group-order-unsorted-inline-file.md`](../../issues/group-order-unsorted-inline-file.md).
- **A per-test timeout for the integration suite**, which would turn a hang like this into a fast,
  named failure instead of a CI job timeout. Seeded:
  [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
- Reworking the submitter/scheduler threading, transactions, isolation levels or locking. The fix
  is in how the next group is chosen, not in the concurrency model.
- A load, stress or repetition test in the suite (R6). Load figures stay diagnosis evidence.
- Changing the stall-by-design behaviour for exhausted failures (`AGENTS.md`, "Group-gating can
  stall by design"), the retry model, or `attempts == max_retries + 1`.
- Group dependencies beyond ascending order: a DAG, group-level concurrency limits, or a
  submission barrier option.
- `issues/restart-pending-retry.md` (START stopping before a pending retry) stays its own seed;
  R7 covers only restarting onto unscheduled tasks in a lower group.
- Cutting the 2.8.4 patch itself. That is `/hs-release`'s, after this lands on `develop`.

## Clarifications

- **2026-11-03 — Diagnosis confirmed** (foreman, as judge under the maintainer's delegation): the
  mechanism, triggers and age are as stated in Problem. The defect is pre-existing since 2.8.0.
- **Candidate approach (input to `hs-plan`, not a requirement):** replace the next-group query in
  `Task.increment_group` with one atomic query: the smallest group among rows with
  `completion_time IS NULL`. If there is none, keep "no task groups remain". Otherwise return that
  group, even when it is lower than the current one. #68's remaining, retryable and exhausted gates
  stay in place. A scratch extraction of this change measured injection 0/20, load 0/300, unsorted
  0/10 and `tests/test_groups.py` 11/11. Not yet tested: live `hs submit` into a running
  `hs server` (R1) and `--restart` (R7). (2026-11-03)
- **Scope** (foreman, 2026-11-03): going back to a lower group whose tasks arrive late, and never
  hanging with work left, are in scope. Appetite is small; this is a fix, not a redesign.
- **Red test** (foreman, 2026-11-03): the regression test is an in-repo, deterministic unit test
  that replays the interleaving (R6), not a load test.
- **Release target** (foreman, 2026-11-03): the fix lands on `develop` and is later cherry-picked to
  a 2.8.4 patch, so it stays free of 2.9-only dependencies (R8).
- **Blast radius:** `data/model.py` is the highest-blast-radius file. `Task.increment_group`
  belongs to the `attempts` relationship in `AGENTS.md`; this fix keeps that relationship unchanged
  and changes only which group comes next.
- **Size check:** ten R-IDs, at the edge of the shaping circuit-breaker. The behaviour is R1–R4 and
  R7. R5 and R8 are constraints, R6 is the test and R9 is a docs line, and R10 is conditional. No
  pilot split proposed.
- **Q1 — Keep ascending group order for unsorted inline files?** `[NEEDS CLARIFICATION: judge picks
  (a), (b) or (c)]` With R1 alone, an inline file that lists group 1 before group 0 no longer hangs,
  but may run group 1 first and group 0 after it, with R4's warning. Today the same file either
  runs in order or hangs. For a user who uses groups as dependencies, a warning and a wrong order
  is quieter than a hang.
  - **(a) Defer** (recommended): ship R1–R9 and document the limit (R9). The seed carries the
    ordering work.
  - **(b) Narrow startup hold (R10):** while nothing has been scheduled, wait for submission to
    complete or for the starting group to hold tasks before choosing a group. It is a small change
    in `Scheduler.load_bundle` and fixes the common case (group 0 plus higher groups in any order).
    It does not fix groups below the starting group that arrive after it has started. It also
    delays the first dispatch until the whole file is ingested for any inline file with no task in
    the starting group, including files in ascending order that begin at group 1, which today
    start at once.
  - **(c) Full hold:** choose the first group only after submission is complete. Ascending order
    for every inline file, but the first dispatch waits for full ingest on every inline file,
    which costs throughput on large single-group files. Not small.
  The shaper recommends (a): (b) is small in code, but its cost falls on files in ascending order,
  which this fix otherwise leaves alone.
- **Q2 — Does the 2.8.4 patch carry #68?** `[NEEDS CLARIFICATION: judge confirms]` The code this
  fix changes was rewritten by #68 (`b819de6`), which landed after 2.8.3. A clean cherry-pick needs
  #68 first; without it the change has to be ported by hand. #68 is itself a gating-only fix
  with no schema or wire change. The shaper recommends carrying both, #68 first. This affects
  release order only, not the contract above, beyond R8's wording.

## Related materials

- Source: `src/hypershell/data/model.py` (`Task.increment_group`, `Task.current_group`,
  `Task.count_remaining`), `src/hypershell/server.py` (`Scheduler.start`, `Scheduler.load_bundle`,
  `Scheduler.submission_complete`), `src/hypershell/submit.py` (the in-process submitter).
- Tests: `tests/test_groups.py`.
- Docs: `docs/_include/task_groups_alt.rst`, `docs/_include/submit_help.rst` (`-g/--group`).
- Prior cycle on the same method: [`spec/group-advance-retry-gate/`](../group-advance-retry-gate/)
  (#68), whose gates this keeps (R5).
- Origin of task groups: `0844bf1`.
- Invariants: `AGENTS.md` § Task lifecycle contract (retry model, `attempts == max_retries + 1`,
  group-gating) and `.agents/factory/invariants.md` §3.
- Seeds deferred by this shaping: [`issues/group-order-unsorted-inline-file.md`](../../issues/group-order-unsorted-inline-file.md),
  [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
