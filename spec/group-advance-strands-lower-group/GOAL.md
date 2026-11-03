# GOAL — Run task groups in order, and never strand a late one

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** group-advance-strands-lower-group
- **kind:** fix
- **appetite:** big · re-assessed after the Q1 ruling: no longer small. The judge said "medium",
  which this skill's two-value vocabulary rounds up to `big`. Q5 proposes a split into two `small`
  cycles.

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
before the whole file is in. Where the whole input cannot be known in advance, HyperShell does the
best it can and says so. A lower group that arrives late on a pipe still runs, once the current
group finishes, with a warning. A `hs submit` that would put a task behind a group the schedule has
already reached is refused at submit time with a clear error. Underneath all three paths, the
race in choosing the next group is closed, so no path can strand a group or hang a run. The
race fix is self-contained enough to be cherry-picked to a 2.8.4 patch after #68.

## Acceptance criteria (the contract)

Terms.

- A group has **started** once the server has scheduled at least one of its tasks in this run. A
  group is **lower** or **higher** by its integer value.
- **File input** is a named, seekable file given to `hs cluster FILE` or `hs server FILE`, the
  inputs the source gate already reads up front. **Stream input** is `<stdin>`, a pipe, a FIFO or
  a process substitution given to those commands.
- A database's **reached group** is the group its schedule has reached. `hs-plan` defines it
  precisely, for example the highest group holding any scheduled row, and how it is computed,
  subject to Q3.

Requirements.

- **R1** — WHEN tasks are committed in a group lower than the server's current group, by any path
  (stream input, a `hs submit` that races the server's advance, or a group below the one the
  server started at), the server SHALL schedule and run them, in `forever` mode as well. This is
  the safety net under R3–R7.
- **R2** — IF tasks remain unscheduled and no group is held or halted by a failure (R9), THEN the
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
  such warning. Together with R1, this is the best-effort behaviour for stream input.
- **R6** — WHEN `hs submit` is given a task whose group is lower than the database's reached group,
  it SHALL refuse that task: it SHALL NOT insert it, and SHALL exit nonzero with an error naming the
  task's group and the reached group. It SHALL accept tasks in the reached group or higher as
  today, and any group on a database where nothing has been scheduled. The rule SHALL hold whether
  or not a server is running. For a file given to `hs submit`, the refusal SHALL come before any
  task from that file is inserted. For stream input to `hs submit`, see Q4.
- **R7** — WHEN `hs cluster` or `hs server` is given file input on a database that has already
  reached a group (`--restart`, `--update`, `--repeat`, or a database kept from an earlier run),
  and the file would insert a task in a lower group, the command SHALL refuse under R6's rule
  before inserting or dispatching any task from the file, and SHALL exit nonzero. Tasks the
  source gate skips as already present are not being submitted, and SHALL NOT trigger the refusal.
  Stream input to these commands is not refused; it gets R1 and R5.
- **R8** — WHEN a server is restarted (`--restart`) on a database where a group holds unscheduled
  tasks below the last group it scheduled, as a run hung by this defect leaves it, the server SHALL
  run those tasks and, in non-`forever` mode, reach FINAL. Restarting with the same file inserts
  no new task (the source gate skips them all), so R7 does not refuse it.
- **R9** — Existing gating SHALL be unchanged: a group holding a failure that still has retries
  left is not advanced past and does not let the run stop (#68); a group whose failures have used
  their retries stops a non-`forever` server, and a `forever` server waits; a cancelled task neither
  holds nor halts its group; runs with no groups or no failures behave as before. Servers without a
  database and queue-mode `hs submit` SHALL behave as today. `tests/test_groups.py` SHALL pass
  unmodified.
- **R10** — Tests:
  - (a) a `unit` test SHALL replay the R4 interleaving: the current group's tasks, and a task of a
    higher group, are committed after the server has found the current group empty but before it
    chooses its next group.
  - (b) a `unit` test SHALL replay the R1 case: a lower group's tasks are committed after a higher
    group has started.
  - (c) a test SHALL run a file that lists a higher group before a lower one through
    `hs cluster` and check R3's order.
  - (d) tests SHALL cover R6's and R7's refusals and acceptances.

  (a) and (b) SHALL be deterministic, with no sleeps, wall-clock timing, racing threads or
  repetition, and SHALL fail on `b31c66e` and pass with the fix. (c) SHALL NOT depend on timing to
  pass. The suite SHALL NOT gain a load or repetition test for this defect.
- **R11** — The fix SHALL change no database schema, wire format, CLI option or configuration key.
  The race fix (R1, R2, R4, R5, R8) SHALL NOT depend on code that landed after 2.8.3 other than
  #68's group gate, so it can be ported to 2.8.4 (Q2).
- **R12** — The task-group documentation (`docs/_include/task_groups_alt.rst`), and the group-gating
  text in `AGENTS.md` § Task lifecycle contract and `.agents/factory/invariants.md` §3, SHALL state:
  - file input runs its groups in ascending order, and starts only once the file is in;
  - stream input is best effort: a late lower group runs after the current group, with a warning;
  - `hs submit` refuses a task below the reached group.

  CLI help text is unchanged.

## Non-goals (no-gos)

- **A `--force` override for R6/R7.** It is not trivial here: it adds a CLI option, which by repo
  convention also changes the docs includes and shell completions, and R11 keeps the CLI unchanged
  for the 2.8.4 port. The workaround is to submit at or above the reached group, or to use a fresh
  database.
- **Ascending order for stream input.** Ruled best effort (R1, R5). A stream cannot be read ahead
  without giving up streaming.
- **A per-test timeout for the integration suite.** It would turn a hang like this into a fast,
  named failure instead of a CI job timeout. Seeded:
  [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
- **All-or-nothing ingest in general** (`issues/ingest-robustness.md`). R6 requires refusal
  before insert only for file input.
- Reworking the submitter/scheduler threading, transactions, isolation levels or locking.
- A load, stress or repetition test in the suite (R10). Load figures stay diagnosis evidence.
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
  and `--restart` (R8). (2026-11-03)
- **Red test** (foreman, 2026-11-03): the regression tests are in-repo and deterministic, and
  replay the interleaving (R10). No load test.
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
  - `hs submit` refuses below the reached group, with or without a running server (R6);
  - the same rule covers file input onto an advanced database (R7);
  - the race fix stays as the safety net for every path (R1, R2).

  This replaces the earlier options (a)/(b)/(c) and the conditional R10.
- **Cost of R3, named:** file input loses its streaming start. Today the first task can run while
  the rest of the file is still being committed. After the fix, a large file's first task waits
  until the whole file is in. The ruled approach is ingest before the first dispatch. The source
  gate already reads a seekable file once up front for its fingerprint and count
  (`submit.source_fingerprint_and_count`). So `hs-plan` may meet R3 with a lighter design, for
  example a per-group census from that read, if R3 still holds. Ingest-first is the default.
- **R7 against R8:** no conflict found. The rows a hung run leaves stranded are already in the
  database. A `--restart` with the same file inserts nothing new, because the source gate skips
  every task already present, so there is nothing to refuse, and R8 runs them. R7 refuses only
  tasks that `--update`, `--repeat` or a different file would newly insert below the reached group.
  Q3 matters here for `--repeat`.
- **Q2 — 2.8.4 and #68. Resolved 2026-11-03** (foreman): the 2.8.4 patch carries #68 (`b819de6`)
  first, then this fix. The port is path-limited to `src/` and `tests/`; `spec/`, `ROADMAP.md` and
  `issues/` do not go to 2.8.4. Whether R3, R6 and R7 also go to 2.8.4, or only the race fix, is
  part of Q5.
- **Blast radius:** `data/model.py` is the highest-blast-radius file. `Task.increment_group`
  belongs to the `attempts` relationship in `AGENTS.md`; this fix keeps that relationship unchanged.
  R6 adds an error path to `hs submit`, and R7 adds one to `hs cluster` and `hs server`: commands
  that succeed today will fail there. Release notes need to say so.
- **Refusal versus a running server:** R6 reads the reached group at submit time. A server that
  advances between that read and the insert can still be passed by the submitted tasks. R1 and R5
  cover that case: the tasks run late, with a warning, and never strand.
- **Q3 — What is the reached group once a database's work is all finished?**
  `[NEEDS CLARIFICATION: judge picks (i) or (ii)]`
  - **(i) Strict:** the highest group ever scheduled. Every later lower-group submission is refused
    for the life of the database. That includes `hs cluster FILE --repeat` of any grouped file after
    a completed run, and reusing a database for a new pass.
  - **(ii) Active run only** (recommended): the reached group counts only while the database holds
    unfinished work, meaning incomplete tasks or pending retries; `hs-plan` defines this exactly,
    including exhausted failures. Once every task has finished, a new submission starts a new pass
    and any group is accepted. A `forever` server idling past group 5 that then receives group 0
    goes back under R1 and R5.

  The maintainer's example ("already past group 5 … submit group 0") is a run in progress, and
  (ii) keeps exactly that refusal without breaking `--repeat`.
- **Q4 — R6 for stream input to `hs submit`?** `[NEEDS CLARIFICATION: judge picks (a) or (b)]`
  - **(a) Stop at the first refused task** (recommended): bundles committed before it stay, exit
    nonzero, and the error also says how many tasks were submitted. All-or-nothing stays with
    `issues/ingest-robustness.md`.
  - **(b) Buffer the whole stream and refuse all or nothing.** This changes how `hs submit` streams.
- **Q5 — Split the cycle?** `[NEEDS CLARIFICATION: judge rules]` Twelve R-IDs trips the shaping
  circuit-breaker. Proposed split:
  - **A — order and the race** (`small`): R1–R5, R8, R9, R10 (a)–(c), R11, and R12's first two
    bullets. This slug keeps A.
  - **B — submit-time refusal** (`small`): R6, R7, R10 (d), and R12's third bullet. A new fix
    cycle, seeded from this GOAL, and dependent on A for R1's safety net.

  A also matches the 2.8.4 port better: B makes commands that succeed today fail, which is a
  behaviour change better suited to 2.9.0 than to a patch. The shaper recommends the split, with
  A for 2.8.4 and B for develop only. Unsplit, the cycle stays `big`.

## Related materials

- Source: `src/hypershell/data/model.py` (`Task.increment_group`, `Task.current_group`,
  `Task.count_remaining`), `src/hypershell/server.py` (`Scheduler.start`, `Scheduler.load_bundle`,
  `Scheduler.submission_complete`), `src/hypershell/submit.py` (`SubmitThread`, `SubmitApp`,
  `apply_source_gate`, `source_fingerprint_and_count`), `src/hypershell/cluster/__init__.py`
  (file versus stream input).
- Tests: `tests/test_groups.py`.
- Docs: `docs/_include/task_groups_alt.rst`.
- Prior cycle on the same method: [`spec/group-advance-retry-gate/`](../group-advance-retry-gate/)
  (#68), whose gates this keeps (R9).
- Origin of task groups: `0844bf1`.
- Invariants: `AGENTS.md` § Task lifecycle contract (retry model, `attempts == max_retries + 1`,
  group-gating) and `.agents/factory/invariants.md` §3.
- Seed adopted by this cycle: [`issues/group-order-unsorted-inline-file.md`](../../issues/group-order-unsorted-inline-file.md).
- Seed deferred by this shaping: [`issues/integration-test-hang-timeout.md`](../../issues/integration-test-hang-timeout.md).
- Related, not in scope: [`issues/ingest-robustness.md`](../../issues/ingest-robustness.md),
  [`issues/restart-pending-retry.md`](../../issues/restart-pending-retry.md).
