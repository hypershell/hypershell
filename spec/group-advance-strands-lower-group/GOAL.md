# GOAL — Run task groups in order, and never strand a late one

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** group-advance-strands-lower-group
- **kind:** fix
- **appetite:** small · part A of a split (Q5); the submit-time refusal is part B, seeded as
  [`issues/submit-refuses-lower-group.md`](../../issues/submit-refuses-lower-group.md)

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
such window: the higher group's row can be the only one present when the scheduler looks. After
the last group, `Task.count_remaining()` stays above zero, `increment_group` reports "no task
groups remain", and the run polls forever without reaching FINAL (`Scheduler.load_bundle`,
`src/hypershell/server.py:206-247`).

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
not caused by #68 (`group-advance-retry-gate`) or #70 (`writeback-scoped-conditional`).

The docs promise that groups are "scheduled in ascending order: every task in the active group must
reach a terminal state before any task in the next group is scheduled"
(`docs/_include/task_groups_alt.rst`). Today nothing keeps that promise when a lower group's tasks
arrive after a higher group has started. That happens in three ways: an unsorted file ingested
while the scheduler runs, a stream piped into `hs cluster`/`hs server`, and a later `hs submit`
into a database whose schedule has already moved past that group. The current code can keep the
promise only by never running those tasks, which is the hang.

## Outcome / vision

Groups run in ascending order whenever HyperShell can know the whole input, and a run with work
left never hangs. When a task file is handed to `hs cluster`/`hs server` to run start to finish,
HyperShell takes in the whole file before it starts, so the groups run in order whatever order the
file lists them. The cost, accepted by the maintainer: file input no longer starts streaming
before the whole file is in.

Where the whole input cannot be known in advance, HyperShell does the best it can and says so. A
lower group that arrives late on a pipe, or from a later `hs submit`, still runs once the current
group finishes, with a warning. Refusing such a `hs submit` up front is part B. Underneath every
path, the race in choosing the next group is closed, so no path can strand a group or hang a run.
All of this ships on `develop` and in a 2.8.4 patch after #68.

## Acceptance criteria (the contract)

Terms.

- A group has **started** once the server has scheduled at least one of its tasks in this run. A
  group is **lower** or **higher** by its integer value.
- **File input** is a named, seekable file given to `hs cluster FILE` or `hs server FILE`, the
  inputs the source gate already reads up front. **Stream input** is `<stdin>`, a pipe, a FIFO or
  a process substitution given to those commands.

Requirements.

- **R1** — WHEN tasks are committed in a group lower than the server's current group, by any path
  (stream input, a `hs submit` into the database while the server runs, or a group below the one
  the server started at), the server SHALL schedule and run them, in `forever` mode as well. This
  is the safety net under R3 and R4.
- **R2** — IF tasks remain unscheduled and no group is held or halted by a failure (R7), THEN the
  server SHALL schedule them, and SHALL NOT keep polling with nothing in flight. A non-`forever` run
  SHALL reach FINAL and exit once every task has completed.
- **R3** — WHEN `hs cluster` or `hs server` is given file input, the server SHALL run the file's
  groups in ascending order, whatever order the file lists them in and whatever the timing between
  ingest and scheduling. It SHALL schedule no task of a group until every task of each lower
  group in the file has reached a terminal state.
- **R4** — WHEN every task is in the database before the server starts scheduling (`hs submit`, then
  `hs server`), or stream input lists its groups in ascending order, the server SHALL run the groups
  in ascending order, whatever the timing between the submitter's commits and the scheduler,
  including under CPU contention.
- **R5** — WHEN the server starts a group lower than a group it has already started in this run, it
  SHALL log a WARNING naming both groups. A group the server only waited on, with no task
  scheduled, does not count as started, so input in ascending order that begins below 0 logs no
  such warning. Together with R1, this is the best-effort behaviour for stream input and for a
  later `hs submit`.
- **R6** — WHEN a server is restarted (`--restart`) on a database where a group holds unscheduled
  tasks below the last group it scheduled, as a run hung by this defect leaves it, the server SHALL
  run those tasks and, in non-`forever` mode, reach FINAL.
- **R7** — Existing gating SHALL be unchanged: a group holding a failure that still has retries
  left is not advanced past and does not let the run stop (#68); a group whose failures have used
  their retries stops a non-`forever` server, and a `forever` server waits; a cancelled task neither
  holds nor halts its group; runs with no groups or no failures behave as before. Servers without a
  database and queue-mode `hs submit` SHALL behave as today. `tests/test_groups.py` SHALL pass
  unmodified.
- **R8** — Tests:
  - (a) a `unit` test SHALL replay the R4 interleaving: the current group's tasks, and a task of a
    higher group, are committed after the server has found the current group empty but before it
    chooses its next group.
  - (b) a `unit` test SHALL replay the R1 case: a lower group's tasks are committed after a higher
    group has started.
  - (c) a test SHALL run a file that lists a higher group before a lower one through
    `hs cluster` and check R3's order.

  (a) and (b) SHALL be deterministic, with no sleeps, wall-clock timing, racing threads or
  repetition, and SHALL fail on `b31c66e` and pass with the fix. (c) SHALL NOT depend on timing to
  pass. The suite SHALL NOT gain a load or repetition test for this defect. These tests are what
  the 2.8.4 port carries (Q2).
- **R9** — The fix SHALL change no database schema, wire format, CLI option or configuration key,
  and SHALL NOT depend on code that landed after 2.8.3 other than #68's group gate, so that its
  `src/` and `tests/` changes can be ported to 2.8.4 after #68 (Q2).
- **R10** — The task-group documentation (`docs/_include/task_groups_alt.rst`), and the group-gating
  text in `AGENTS.md` § Task lifecycle contract and `.agents/factory/invariants.md` §3, SHALL state:
  - file input runs its groups in ascending order, and starts only once the file is in;
  - stream input is best effort: a late lower group runs after the current group, with a warning.

  CLI help text is unchanged.

## Non-goals (no-gos)

- **Refusing a `hs submit` below the group the schedule has reached, and the same refusal for file
  input onto an advanced database.** That is part B, develop only (2.9). Seeded as shaped:
  [`issues/submit-refuses-lower-group.md`](../../issues/submit-refuses-lower-group.md). Until B
  lands, such tasks run late with R5's warning.
- **Ascending order for stream input.** The maintainer ruled it best effort (R1, R5). A stream
  cannot be read ahead without giving up streaming.
- **A per-test timeout for the integration suite.** It would turn a hang like this into a fast,
  named failure instead of a CI job timeout. Seeded:
  [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
- **All-or-nothing ingest in general** (`issues/ingest-robustness.md`).
- Reworking the submitter/scheduler threading, transactions, isolation levels or locking.
- A load, stress or repetition test in the suite (R8). Load figures stay diagnosis evidence.
- Changing the stall-by-design behaviour for exhausted failures (`AGENTS.md`, "Group-gating can
  stall by design"), the retry model, or `attempts == max_retries + 1`.
- Group dependencies beyond ascending order: a DAG, or group-level concurrency limits.
- `issues/restart-pending-retry.md` (START stopping before a pending retry) stays its own seed.
- Cutting the 2.8.4 patch itself. That is `/hs-release`'s, after this lands on `develop`.

## Clarifications

- **2026-11-03 — Diagnosis confirmed** (foreman, as judge under the maintainer's delegation): the
  mechanism, triggers and age are as stated in Problem. The defect is pre-existing since 2.8.0.
- **Candidate race fix (input to `hs-plan`, not a requirement):** replace the next-group query in
  `Task.increment_group` with one atomic query: the smallest group among rows with
  `completion_time IS NULL`. If there is none, keep "no task groups remain". Otherwise return that
  group, even when it is lower than the current one. #68's remaining, retryable and exhausted gates
  stay in place. A scratch extraction measured injection 0/20, load 0/300, unsorted 0/10 and
  `tests/test_groups.py` 11/11. Not yet tested: live `hs submit` into a running `hs server` (R1)
  and `--restart` (R6). (2026-11-03)
- **Red test** (foreman, 2026-11-03): the regression tests are in-repo and deterministic, and
  replay the interleaving (R8). No load test.
- **Q1 — Group order for input the scheduler has not seen yet. Resolved 2026-11-03** (maintainer,
  verbatim): "In situations where we are handed a file to run start-to-finish, we should process
  the whole file up-front so as to run groups in order. But there are situations where this is not
  possible; (a) because we're piping into the cluster process, and (b) because we submitted to the
  database and it's possible to submit again with a group id that precedes what has already run. I
  want to do the best thing when it's possible, otherwise it's a best-effort with a warning (the
  pipe), or an error at submit time (if we're already past group 5 and you submit group 0 it
  should refuse)." Mapped by the foreman:
  - file input is ingested before the first dispatch (R3);
  - stream input is best effort with a warning (R1, R5);
  - the submit-time refusal is part B (Q5);
  - the race fix stays as the safety net for every path (R1, R2).
- **Cost of R3, named:** file input loses its streaming start. Today the first task can run while
  the rest of the file is still being committed. After the fix, a large file's first task waits
  until the whole file is in. The ruled approach is ingest before the first dispatch. The source
  gate already reads a seekable file once up front for its fingerprint and count
  (`submit.source_fingerprint_and_count`). So `hs-plan` may meet R3 with a lighter design, for
  example a per-group census from that read, if R3 still holds. Ingest-first is the default.
- **Q2 — 2.8.4 and #68. Resolved 2026-11-03** (foreman): the 2.8.4 patch carries #68 (`b819de6`)
  first, then this cycle. The port is path-limited to `src/` and `tests/`, which includes the R8
  tests; `spec/`, `ROADMAP.md` and `issues/` do not go to 2.8.4. With the Q5 split, all of A goes
  to 2.8.4 and none of B does.
- **Q3 — The reached group once a database's work is finished. Resolved 2026-11-03** (maintainer):
  option (ii). The reached group counts only while unfinished work remains: incomplete tasks or
  pending retries, with the plan defining exhausted failures. This belongs to B and is recorded in
  its seed.
- **Q4 — Refusal for stream input to `hs submit`. Resolved 2026-11-03** (maintainer): option (b),
  buffer the whole stream and refuse all or nothing. This is the maintainer's choice, against the
  shaper's recommendation of (a), stopping at the first refused task. It belongs to B and is
  recorded in its seed.
- **Q5 — Split. Resolved 2026-11-03** (maintainer): split.
  - **A**, order and the race, keeps this slug, stays `small` and goes to 2.8.4. It holds the
    earlier draft's R1–R5, R8, R9, R10 (a)–(c), R11 and R12's first two bullets, renumbered here
    as R1–R10.
  - **B**, the submit-time refusal, is develop only (2.9). It holds the earlier draft's R6, R7,
    R10 (d) and R12's third bullet, and is seeded as shaped in
    `issues/submit-refuses-lower-group.md`. B depends on A, because A's R1 and R5 cover a
    submission that races a server past its refusal check.

  The shaper proposed the split because twelve R-IDs tripped the shaping circuit-breaker, and
  because B makes commands that succeed today fail, which fits 2.9.0 better than a patch.
- **Blast radius:** `data/model.py` is the highest-blast-radius file. `Task.increment_group`
  belongs to the `attempts` relationship in `AGENTS.md`; this fix keeps that relationship unchanged.
  A adds no new error path: a command that succeeds today still succeeds.

## Related materials

- Source: `src/hypershell/data/model.py` (`Task.increment_group`, `Task.current_group`,
  `Task.count_remaining`), `src/hypershell/server.py` (`Scheduler.start`, `Scheduler.load_bundle`,
  `Scheduler.submission_complete`), `src/hypershell/submit.py` (`SubmitThread`,
  `source_fingerprint_and_count`), `src/hypershell/cluster/__init__.py` (file versus stream input).
- Tests: `tests/test_groups.py`.
- Docs: `docs/_include/task_groups_alt.rst`.
- Prior cycle on the same method: [`spec/group-advance-retry-gate/`](../group-advance-retry-gate/)
  (#68), whose gates this keeps (R7).
- Origin of task groups: `0844bf1`.
- Invariants: `AGENTS.md` § Task lifecycle contract (retry model, `attempts == max_retries + 1`,
  group-gating) and `.agents/factory/invariants.md` §3.
- Part B: [`issues/submit-refuses-lower-group.md`](../../issues/submit-refuses-lower-group.md).
- Seed adopted by this cycle: [`issues/group-order-unsorted-inline-file.md`](../../issues/group-order-unsorted-inline-file.md).
- Seed deferred by this shaping: [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
- Related, not in scope: [`issues/ingest-robustness.md`](../../issues/ingest-robustness.md),
  [`issues/restart-pending-retry.md`](../../issues/restart-pending-retry.md).
