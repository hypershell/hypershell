# PLAN — Run task groups in order, and never strand a late one

> **Status:** Draft for review · **Last updated:** 2026-11-03
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

`Task.increment_group` chooses the next group with one atomic query: the smallest group that
still has an incomplete task. That query can go back down to a lower group, so a late lower group
is never stranded, and a run with work left never polls forever. #68's gates stay in front of
it, unchanged.

Three small supporting changes complete the fix:
- On an empty database, the scheduler commits to no group until a task exists, so ascending
  input that begins below 0 runs in order.
- It logs one WARNING when it starts a group lower than one it has already started.
- For file input, `ServerThread` starts the scheduler only after the whole file is in, so an
  unsorted file runs in ascending order.

Every change sits in code that exists unchanged on 2.8.3 after #68, so the port is mechanical.
A scripted trial proves that before release. There are five phases: red replays, the race fix,
ingest first, docs, and the port trial with the final gate. About sixty lines of source change
in `data/model.py` and `server.py`.

## 2. Design

### 2.1 The next-group choice (`src/hypershell/data/model.py`)

- **`Task.lowest_incomplete_group() -> Optional[int]`** (new classmethod). It returns
  `min(group)` over rows with `completion_time IS NULL`, or `None` when every row has completed.
  It uses the lifecycle's "not terminal" predicate exactly (§1). A cancelled row is complete, so
  it never counts. `Task.group` is `NOT NULL`.
- **`Task.increment_group`** keeps #68's three gates on `previous_group` in their current order:
  incomplete rows hold, then retryable failures hold, then exhausted failures halt. Its last
  statement becomes `lowest_incomplete_group()`:
  - `None` returns `TaskGroupInfo(previous_group, 'no task groups remain')`, as today.
  - `previous_group` (rows committed while the gates ran) returns a viable
    `TaskGroupInfo(previous_group, '<n> new tasks submitted')`, or a similar reason. The scheduler
    stays, backs off once, and `Task.next` takes the rows.
  - Any other value, higher or **lower**, returns `TaskGroupInfo(value)`.

  The docstring and a declarative comment state why: the next group is chosen in one query, so
  rows committed during the checks are never skipped, and it may be lower than the previous group
  when tasks arrive late.
- `Task.current_group`, `count_remaining`, `Task.next` and the retry queries are unchanged. The
  `attempts == max_retries + 1` relationship is untouched.

### 2.2 The scheduler (`src/hypershell/server.py`, `Scheduler`)

**Empty-database start (R4, R5).**
- `start()` sets `self.first_group_pending = True` only in its "Empty database" branch.
- While that flag is set, `load_bundle` resolves `current_group = Task.lowest_incomplete_group()`
  before scheduling. When that is `None`, it skips `Task.next` for this pass (`self.tasks = []`),
  and the existing stop, increment and back-off path runs unchanged.
- The flag clears when the first bundle is scheduled.
- Effect: the scheduler never schedules into group 0 merely because it started there, so
  ascending input beginning below 0 runs in order. A restart, or any non-empty start, keeps
  `current_group()`'s choice, so `--restart` on a halted database still halts (R7)
  ([research 01](research/01-next-group-race.md)).

**Late-lower-group warning (R5).**
- New run-local attributes: `highest_started_group` and `last_scheduled_group` (both `None`).
- A helper (for example `Scheduler.mark_group_started()`) is called first inside
  `if self.tasks:`. When `current_group != last_scheduled_group` and
  `highest_started_group is not None and current_group < highest_started_group`, it logs
  `log.warning(f'Starting task group {current} after task group {highest} (tasks submitted late)')`.
  The builder may adjust the wording, but it must name both groups and keep the substring
  `after task group`, which the tests and verifies grep. The helper then updates both attributes
  and clears `first_group_pending`.
- A group the scheduler only waited on never schedules a bundle, so it is never "started".
- The existing `Completed task group A - starting task group B` INFO line is unchanged. It also
  prints on a downward move.

**Port placement (R9).** `develop`'s `Scheduler` and `ServerThread` differ from 2.8.3's around
the submitter plumbing. Every edit must sit where the three lines on each side are identical on
both branches, because a 3-way apply treats an adjacent change as a conflict.
- In `load_bundle`, edit only the `Task.next` assignment and the line after `if self.tasks:`.
  Never touch the stop branch (`elif … FINAL`).
- In `__init__`, add the attributes **before** `self.current_group = None`, and the annotations
  before `current_group:`. Neither goes next to `submitter`.
- In `start()`, the flag goes in the `else:` empty-database branch.

The P5 trial checks all of this.

### 2.3 File input runs whole before scheduling (`src/hypershell/server.py`, `ServerThread`) — R3

**`is_file_input(source) -> bool`** (new module function in `server.py`). It is true for a
seekable file object that is not `sys.stdin`, or an in-memory `list`/`tuple`. It is false for
standard input, pipes, FIFOs, process substitution and other iterables.
- It looks through a wrapper that carries its stream as `.iterable` (develop's `GatedSource`)
  with `getattr(source, 'iterable', source)`, without importing the class, so the same code runs
  on 2.8.3.
- The seekable test is the source gate's own file/stream split.

**`ServerThread.start_threads`.**
- Start the submitter, then confirm, heartmonitor and receiver. Then, when there is a scheduler,
  a submitter and `is_file_input(self.submitter.source)`, log at INFO that scheduling waits for
  the task file, and join the submitter. Then start the scheduler.
- Joining re-raises a submitter failure exactly where `wait_submitter` raises it today.
- The predicate is evaluated here, not in `__init__`. `__init__`'s neighbourhood differs on 2.8.3,
  while `start_threads` is identical on both branches.
- `SubmitThread.source` exists on both branches.

**`ServerThread.stop`.** Join the scheduler only if it was started:
`wait=wait and self.scheduler.ident is not None`. No in-tree caller passes `wait=True`, but
`ServerThread` is public API, and a stop during ingest would otherwise raise `RuntimeError`. A
stop during ingest stops the submitter, the join returns, and the scheduler starts with its halt
flag set, which `StateMachine.next()` honours at once.

**Unaffected.**
- `in_memory` (no scheduler), `--forever` with no file, and bare `--restart` (no submitter) are
  unaffected. Stream input keeps its streaming start.
- `Scheduler.start` then sees the whole file. `current_group()` returns the lowest group,
  because nothing is scheduled yet, and the race fix keeps every later choice ascending.
- On `develop`, `submission_complete()` is already true at that point.

### 2.4 Tests

`tests/test_group_order.py` (new) copies #68's `task_db` fixture and the write-back helpers, so
it is self-contained for the port. Precondition checks use `pytest.fail`, never `assert`. Every
drive loop is bounded.

| Test | Marker | Red on `b31c66e` | Pins |
|------|--------|------------------|------|
| `test_next_group_sees_rows_committed_during_check`. Groups 0 and 2 are submitted, and group 0 starts and drains. An injection armed only inside `increment_group` (a wrapper on it sets a flag, and a one-shot `count_retryable` wrapper fires under it) commits a group-0 row and a group-1 row. After that `load_bundle`, `current_group == 0`. The next bundle is the group-0 row. The order is 0, 1, 2, then FINAL. No late warning. | unit | strict `xfail(raises=AssertionError)` | R4, R8(a), R2 |
| `test_late_lower_group_runs_after_current[forever_mode]`. Group 1 starts, a group-0 row is committed through a separate session, and group 1 completes. Group 0 is then scheduled, and one WARNING names 0 and 1. In non-`forever` mode the run reaches FINAL. In `forever` mode it does not. | unit | strict xfail | R1, R5, R8(b), R2 |
| `test_ascending_input_below_zero_runs_in_order`. On an empty-database start, rows in `-1, -1, 0` are committed before the first LOAD. The order is `-1, -1, 0`, then FINAL, with no warning. | unit | strict xfail | R4, R5 |
| `test_restart_runs_stranded_lower_group`. The database is left as the defect leaves it: group 1 completed, group 0 unscheduled. `Scheduler(restart_mode=True)` runs group 0 and reaches FINAL, with no late warning. | unit | strict xfail | R6, R1 |
| `test_restart_on_halted_group_still_halts`. Group 0 holds an exhausted failure and group 1 is unscheduled. The restart reaches FINAL with `Failed task group 0`, and group 1 is never scheduled. | unit | green (guard) | R7 |
| `test_file_input_classification` (parametrized). A seekable temp file and a list or tuple are file input. `sys.stdin`, an `os.pipe` read end and a generator are not. A wrapper with `.iterable` set to a file is file input, and one with `.iterable` set to a pipe is not. | unit | n/a (new function, P3) | R3 |
| `test_cluster_runs_unsorted_file_in_group_order[layout]`, layouts `(1, 0)` and `(0, -1)`. `hs cluster FILE -N2` exits with success. From `hs list --json`, each lower group's last `completion_time` is no later than the next group's first `schedule_time`. The `Completed … starting …` lines are exactly the ascending transitions. No late warning. | integration | never run red (it can hang) | R3, R8(c) |

`tests/test_groups.py` and `tests/test_retry_gate.py` stay byte-identical.

### 2.5 Docs (R10)

- **`docs/_include/task_groups_alt.rst`.** Add a short paragraph in the file's voice:
  - A task file given to `hs cluster`/`hs server` is read in full before scheduling starts, so
    its groups run in ascending order whatever order the file lists them.
  - Input on standard input or a pipe is scheduled as it arrives. A lower group that arrives
    after a higher group has started runs once the current group finishes, and a warning is
    logged.
  - Optionally: with `-T/--timeout`, clients may idle while a large file is read.
- **`AGENTS.md` § Task lifecycle contract**, the "Group-gating can stall by design" paragraph:
  add the same two facts. Also note that the next group is the smallest one with incomplete
  tasks, which may be lower.
- **`.agents/factory/invariants.md` §3**, the group-gating bullet: the same facts, kept in
  lockstep with `AGENTS.md`.

CLI help, `docs/_include/*_help.rst` and `share/` are unchanged, and no Sphinx or man build runs.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `lowest_incomplete_group()` in `increment_group` moves down to a late lower group once the current group clears its gates. The same code runs in `forever` mode (§2.1). Pinned by `test_late_lower_group_runs_after_current` (both modes) and `test_restart_runs_stranded_lower_group`. |
| R2   | While `count_remaining() > 0`, the query never returns `None`. Incomplete rows are unscheduled (taken by `Task.next`) or in flight, and only in the current group. R7's holds and halts are the only stops (research 01). Pinned by every replay reaching FINAL. |
| R3   | Ingest first in `ServerThread.start_threads` for `is_file_input` sources (§2.3). The scheduler then starts on the whole file, in its lowest group, and the race fix keeps it ascending. Pinned by `test_cluster_runs_unsorted_file_in_group_order` and the P3 CLI drive. |
| R4   | The atomic query closes the commit-between-statements window (§2.1). The provisional empty-database start stops group 0 from running before a lower group of ascending input (§2.2). A file submitted with `hs submit` and then run by `hs server` starts in its lowest group, as today. Pinned by `test_next_group_sees_rows_committed_during_check` and `test_ascending_input_below_zero_runs_in_order`. |
| R5   | `mark_group_started()` warns once, naming both groups, on the first bundle of a group lower than the highest started this run. A group only waited on is never started (§2.2). Pinned by the late-lower test (warning present) and the race, below-zero, restart and integration tests (warning absent). |
| R6   | A restart starts in `current_group()`'s higher group, then `increment_group` returns the lower one (§2.1). Pinned by `test_restart_runs_stranded_lower_group` and the P2 CLI drive. |
| R7   | #68's gates are untouched and run before the query. The provisional start applies only to an empty database. Ingest first applies only with a scheduler, a submitter and file input. Pinned by `test_restart_on_halted_group_still_halts`, the unchanged `tests/test_retry_gate.py` and `tests/test_groups.py`, and the full suite. |
| R8   | (a), (b) and the further replays land red in P1 and turn green in P2. (c) lands with R3 in P3. No sleeps, threads or repetition (§2.4). |
| R9   | `src/` changes only `data/model.py` and `server.py`. No column, wire payload, CLI option or config key changes. Edits are placed for a clean 3-way apply (§2.2). Proved by the P5 trial in a detached `origin/master` worktree. |
| R10  | §2.5. P4 verifies the statements are present and the help text is unchanged. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research (gate #1: §1, §3, §4, §5 and §6 are touched, and the intent is sane against each) and
again against this design (gate #2).

- **§1 Task lifecycle.** The one new query uses "incomplete" exactly as
  `completion_time IS NULL` and lives in a `Task` classmethod. `server.py` calls it and hand-rolls
  no predicate.
- **§2 `exit_status`.** No failure, retry or count-as-failed query is added or changed. #68's
  `CANCEL_STATUS` filters stay where they are.
- **§3 Retry model and group gating.** No retry change. The gates still decide hold and halt
  before any move, so stall-by-design for exhausted failures is unchanged.
  `increment_group`'s place in the `attempts` relationship is unchanged. Group gating now also
  moves *down* to a late lower group. R10 updates §3's text to say so.
- **§4 Server modes.** `in_memory` has no scheduler, so it gets no provisional start, warning or
  ingest first. `no_confirm` is unaffected. `forever` gets the race fix and the downward move,
  and still skips the stop branch.
- **§5 Concurrency.** There is no new state, thread, lock or queue operation. The provisional
  start and the warning run inside `load_bundle`, after its signal poll. The submitter join in
  `ServerThread` is the same unbounded join `wait_submitter` already does, moved earlier, and it is
  not a blocking queue operation inside an FSM state. A stop during the join stops the submitter
  (§2.3).
- **§6 Shutdown ordering.** The stop and wait order (submitter, scheduler, sentinels) is
  unchanged. Only the *start* order changes, with the scheduler last, and `AGENTS.md` does not hold
  start order load-bearing. `stop()` gains a "started" guard on the scheduler join.
- **§12 Conventions.** New tests are tagged `unit` or `integration` and use `@mark.parametrize`.
  There are no R-IDs in source or tests, and no CLI change, so §12's docs and completions rule
  does not trigger.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| A provisional first group on an empty-database start (`first_group_pending`) | Without it, ascending input that begins below 0 can run group 0 first (R4) and log a warning (R5). The scratch prototype shows `[0, -1, -1]` on the candidate patch. | Re-resolving whenever nothing is scheduled breaks `--restart` on a halted database (R7). Resolving once and then calling `Task.next(0)` leaves a two-statement window. Changing `Task.current_group()` changes the start logged by `test_groups.py`. |
| `is_file_input` unwraps by duck typing (`getattr(source, 'iterable', source)`) | The same code must run on 2.8.3, which has no `GatedSource` (R9). | Importing `GatedSource` breaks the port. Plumbing an `ingest_first` flag through `ServerThread` and every cluster launcher touches about ten coupled-core constructors, which `GatedSource`'s own docstring was written to avoid. |
| The scheduler starts last, after the other server threads, in all cases | Queue consumers must be up while a long file is read. One order is simpler than two. | Keeping the old order for stream input only adds a branch, for no behaviour difference. |
| `ServerThread.stop` joins the scheduler only if it was started | A stop during ingest must not raise `RuntimeError` from `join`. | Leaving it unguarded breaks only `wait=True` callers, but `ServerThread` is public API. |

## 4. Rabbit holes (resolved)

- **Does the atomic query keep #68's gates, and is it stall-free?** Yes. The gates run first
  and are unchanged, a move happens only to a group with incomplete rows, and in-flight rows exist
  only in the current group ([research 01](research/01-next-group-race.md)).
- **Is the candidate fix enough for R4 and R5?** No, for the empty-database start: ascending
  input below 0 runs group 0 first. The provisional start fixes it without touching restarts
  ([research 01](research/01-next-group-race.md)).
- **Which R3 design survives R9?** Only ingest first in `ServerThread`. The scheduler-side wait
  and the census need code that 2.8.3 lacks
  ([research 02](research/02-file-input-order.md)).
- **Can (a) be replayed deterministically, and isolated?** Yes. The injection is armed only
  inside `increment_group`, from a started group, with a pending higher row, so neither the stop
  branch nor the provisional start masks the race. The scratch prototype is red on `b31c66e` and
  green on the candidate ([research 03](research/03-tests-and-port.md)).
- **Does #68 port cleanly to 2.8.3?** No. It has one known conflict in the stop branch, which the
  trial resolves by script. Its `model.py` and tests merge cleanly, and `increment_group` is
  byte-identical on 2.8.3 plus #68 and on `develop`
  ([research 03](research/03-tests-and-port.md)).

## 5. Risks & open questions (for the plan gate)

1. **Pre-existing early FINAL with stream input. Ruling needed.** After the first task is
   scheduled, the stop branch no longer checks the submitter, so a pipe that pauses lets the run
   reach FINAL with rows still arriving. A drive on `develop` confirmed it:
   `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1` runs `a`, exits 0, and leaves `echo b`
   unscheduled. It is not a hang, and file input is immune once ingest first lands. R2's first
   sentence ("IF tasks remain unscheduled … SHALL schedule them") could be read to cover it. The
   `develop` fix is one conjunct (`self.submission_complete()`), but 2.8.3 has no submitter
   reference, so a 2.8.4 port would breach R9.
   **Recommendation:** rule it outside part A and seed it (develop or 2.9, or with the plumbing).
   This plan writes nothing outside `spec/`.
2. **Client idle timeout during ingest. Ruling needed.** With ingest first, `hs cluster FILE -T N`
   with N shorter than the ingest can let launched clients exit before the first task. The
   default is "never", and the autoscaler relaunches clients.
   **Recommendation:** accept the risk, with an optional sentence in the R10 docs. No code.
3. **`--from-json` and Python-API lists count as file input. Choice.** Their records are whole
   in memory, so ingest first costs nothing extra and gives order. R3 does not require it.
   **Recommendation:** keep it. The alternative is to restrict `is_file_input` to file objects.
4. **One back-off pass on a same-group return.** When rows land during the gates,
   `increment_group` returns the current group, and `load_bundle` backs off once (0.5 s minimum)
   before taking them. That happens only inside the race window.
5. **The port trial needs a dependency environment for 2.8.3.** `uv sync --frozen` in the trial
   worktree uses the uv cache. If the environment cannot resolve it, the trial cannot run, and
   that is an environment fault to report, not a red result.
6. **The TLS localhost flake.** Suite verifies allow one recorded `--lf` rerun (§6).
7. **Not checked:** an empty task file (`hsx empty.in`) never reaches FINAL today, because the
   stop branch requires `Task.count() > 0`. This change does not alter that.

## 6. Verification strategy

- **Kill timers everywhere.** `timeout(1)` is not installed and the integration suite has no
  per-test timeout. Every verify that runs pytest or `hs`/`hsx` goes through a shell function,
  `kt SECONDS CMD…`, which runs the command in a new session and kills that process group on
  expiry (exit 124). It is defined inline in each `verify:` and was checked under `sh` and `zsh`.
- **P1 (red first).**
  - `git diff --quiet b31c66e -- src` proves the source under test is the base.
  - `pytest -m unit -rxX tests/test_group_order.py` exits 0 only if every replay fails by
    assertion (strict xfail) and the guard passes.
  - The builder also runs `--runxfail`, which must fail, and pastes the summary into the commit.
- **P2 (race fix).**
  - The replay file has no `xfail` left, and `tests/test_groups.py` is unchanged.
  - The unit tests of the new file and `test_retry_gate.py` pass.
  - CLI drive for R6 and R1: in a throwaway site, `hs submit` a group-1 task, run
    `hsx --restart -N1`, `hs submit` a group-0 task, and run `hsx --restart -N1` again. The second
    run exits 0, the group-0 task succeeded, 0 tasks remain, and stderr has no late-lower
    warning. On `b31c66e` that second run hangs, which is why the drive carries a timer.
- **P3 (ingest first).**
  - Run `tests/test_group_order.py` and `tests/test_groups.py` with the flake rerun.
  - CLI drive: an unsorted file (groups listed 1, 0, -1, interleaved) through `hsx -N2` runs in
    the group order `-1 0 1` by `schedule_time`, logs no late warning, and leaves 0 remaining.
  - No pipe drives, because of risk 1.
- **P4 (docs).** Each of the three files carries the file-order and stream-best-effort
  statements. CLI help snippets and `share/` are unchanged from `b31c66e`.
- **P5 (port trial and final gate).**
  - R9 static checks: `src/` changes are exactly `data/model.py` and `server.py`, no
    `mapped_column` or CLI-interface line changed, and no config or queue module changed.
  - The scripted trial (`port-trial.sh`, §2.2, [research 03](research/03-tests-and-port.md)).
  - Then the full `develop` suite under a 40-minute timer, with the one recorded flake rerun.
- **Flake rule (every suite run).** On failure, print the failed node ids, then rerun **only**
  those once with `--lf`. Record both runs and the failure signature in the commit body. A second
  failure is red. A first failure that is not the rc 5 / `ConnectionResetError` signature is
  logged as a finding even if the rerun passes.
- **No cluster jobs.** Hosted CI on Python 3.11–3.14 provides the cross-matrix evidence after
  publish.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
