# PLAN — Don't advance a task group past a pending retry

> **Status:** Draft for review · **Last updated:** 2026-11-01
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Factor `Task.select_failed`'s filters into one `Task` query helper, and add one count helper on
top of it that answers "how many failures in this group will `Task.next` retry?". Both scheduler
decisions use that count. `Task.increment_group` holds the group while it is non-zero, and the
`load_bundle` stop branch refuses FINAL while it is non-zero. In both places the count runs
**after** the incomplete-rows check. The next LOAD then creates the retry through the unchanged
`Task.next`. Two deterministic `unit` replays land first, committed red against `c1d4470`'s source,
and the fix turns them green. The work is two phases and touches about thirty lines of source. It
fits `appetite: small`.

## 2. Design

### 2.1 The shared predicate (`src/hypershell/data/model.py`)

- **`Task.query_retryable(attempts, group=None) -> Query`.** This is the existing `select_failed`
  filter chain, moved without change: `exit_status IS NOT NULL`, `exit_status != 0`,
  `exit_status != CANCEL_STATUS`, `attempt < attempts`, `retried IS FALSE`, and `group == group`
  when a group is given. It is the single definition of "a failure still eligible for retry".
- **`Task.select_failed`** keeps its signature and result. It becomes
  `query_retryable(attempts, group).order_by(completion_time).limit(limit).all()`. The SQL is
  equivalent, so retry selection and order are unchanged (a non-goal).
- **`Task.count_retryable(attempts, group, eager=False) -> int`.** `group` is required, so the
  helper can never count across all groups. It returns `0` when `attempts <= 1 and not eager`, and
  `query_retryable(attempts, group).count()` otherwise. The guard mirrors `Task.next`: non-eager
  `__next_not_eager` selects failures only when `attempts > 1`, while eager `__next_eager` always
  does. Its docstring states the contract in declarative form: *the failures counted are exactly
  the failures `Task.next` retries for the same `group`, `attempts` and `eager`*.
- **`Task.increment_group(previous_group, attempts, eager=False)`** gains the `eager` keyword. Its
  checks run in this order:
  1. Incomplete rows in the group → hold. This check exists today.
  2. **New:** `count_retryable(attempts, previous_group, eager) > 0` → hold. The result is
     `TaskGroupInfo(previous_group, 'waiting on N failed tasks to be retried')`, which is viable.
  3. Exhausted failures → not viable. Unchanged.
  4. Advance, or "no task groups remain". Unchanged.

  A declarative comment states why the order matters. The incomplete check runs first, so a result
  committed between two checks is always seen by the second. The retryable hold precedes the
  exhausted halt, so a group holding both is held rather than halted.

The exhausted-failure query (`attempt >= attempts`) is the complement of the shared predicate and
stays where it is. It is not part of "retryable", and R3 does not cover it.

### 2.2 The stop path (`src/hypershell/server.py`, `Scheduler.load_bundle`)

- The stop branch gains a conjunct placed **after** `Task.count_remaining() == 0`:
  `Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0`. A
  declarative comment says that a failure committed after `Task.next` must still be retried before
  the run stops. Python's left-to-right `and` gives the required order.
- When the conjunct blocks FINAL, control falls through to `increment_group`. That call sees the
  same failure and holds. `load_bundle` then backs off by `poll` through the existing branch and
  returns LOAD, and the next `Task.next` creates the retry. The branch structure, logging and
  back-off are otherwise unchanged.
- `increment_group` is called with `eager=self.eager`.
- `count_remaining` keeps its meaning, because the autoscaler and the ETA depend on it.
  `Scheduler.start` is unchanged, since it is outside the R7 window (§5).
- `in_memory` mode is unaffected (there is no Scheduler). `forever` mode skips the stop branch,
  as today, but gets the `increment_group` hold.

### 2.3 Why this is stall-free (FINAL safety)

A hold occurs only when `count_retryable(attempts, current_group, eager) > 0`. By construction,
that is exactly when the next `Task.next(group=current_group, attempts, eager)` selects at least
one failure. While a group is held it has no incomplete rows, so non-eager `select_new` returns
nothing and the failure path runs. Every hold is therefore followed by a LOAD that schedules a
retry, and each retry raises `attempt` by one, so the holds per task are bounded by `attempts`.
The two cases that could otherwise stall are designed out:

- A failure in a group the scheduler has already left would hold forever under a global check. It
  is excluded by the required `group` scope.
- A user-set `attempt < 1` row at `attempts == 1` in non-eager mode would hold forever without the
  guard. It is excluded by the `attempts <= 1 and not eager` guard.

Server invocations that use a different `--max-retries` are safe as well, because both decisions
and `Task.next` read the same `self.attempts`. See
[`research/02-final-safety.md`](research/02-final-safety.md).

### 2.4 Tests (`tests/test_retry_gate.py`, new, all `@mark.unit`)

**Fixture `task_db`.**
- Builds a per-test file SQLite engine under `tmp_path` and runs `Entity.metadata.create_all`.
- Calls `Session.remove()`, then `Session.configure(bind=engine)`.
- On teardown, calls `Session.remove()`, rebinds `Session` to `hypershell.data.core.engine`, and
  disposes the test engine.
- The rebind keeps in-process queries off the developer's real per-user database
  ([`research/01-test-harness.md`](research/01-test-harness.md)).

**Helpers.**
- **`fail(task_id)`** commits `exit_status=1` and `completion_time` through a separate session
  from `Session.session_factory()`, as the Receiver thread does.
- **`inject_after_next(monkeypatch, action)`** wraps `Task.next` once. It calls the real method,
  runs `action` only if the real call returned `[]`, then restores the original and returns `[]`.
- **Precondition checks** (for example "this LOAD scheduled attempt 2") use `pytest.fail(...)`,
  never `assert`. A broken replay then cannot pass as an expected failure.
- Each test drives `Scheduler(queue=None, attempts=3, eager=eager, poll=0)` through `start()` and
  `load_bundle()`. `poll=0` makes the back-off `sleep(0)`. No other time, sleep or threads are
  involved.

**Red replays.** These are committed in P1 with `@mark.xfail(strict=True, raises=AssertionError)`,
and P2 removes the markers. Both are parametrized over `eager ∈ {False, True}`.

- **`test_group_advance_waits_for_pending_retry`** (R1, R2, R4). This test replays the R1
  interleaving.
  - Setup: a failing task in group 0 and a task in group 1. LOAD schedules attempt 1, which is
    then failed. LOAD schedules attempt 2. Arm the injection to fail attempt 2.
  - `load_bundle()` must return LOAD with `current_group == 0`, and the log must hold no
    `Completed task group` or `Failed task group` record (`caplog` on the `hypershell` logger).
  - The next `load_bundle()` must return PACK with one task: group 0, attempt 3,
    `previous_id ==` attempt 2's id.
  - No group-1 row may have a `schedule_time`.
- **`test_stop_waits_for_pending_retry`** (R7, R8). This test replays the stop-path interleaving.
  It is parametrized over `layout ∈ {no-groups, last-group}`. In the last-group layout, group 0
  holds a task that succeeds, and the failing task is in group 1.
  - Setup: drive attempt 2 into flight and arm the injection.
  - `load_bundle()` must not return FINAL.
  - The next `load_bundle()` must return PACK with attempt 3.
  - Failing attempt 3 (exhausted) must then make `load_bundle()` return FINAL. This pins
    termination.

**"Unchanged otherwise" guards.** These are green on `c1d4470`, are committed in P1, and stay
green after the fix (R5).

- **Exhausted failure.**
  - Setup: group 0 fails at its final attempt, and a group 1 exists. Parametrized over
    `attempts ∈ {1, 3}`.
  - Non-`forever`: FINAL with a `Failed task group 0` record, and group 1 never scheduled.
  - `forever`: LOAD with `current_group == 0`, and no FINAL.
- **Cancelled task.** A `CANCEL_STATUS` row in group 0 with a group 1 present (`attempts=3`)
  advances to group 1 and logs `Completed task group 0`.
- **Retried success.** Attempt 1 of the group 0 task fails, and attempt 2 succeeds. The group then
  advances to group 1.
- **No failures.** A single group that succeeds reaches FINAL.

**Shared-predicate test.** This one is new in P2, because `count_retryable` does not exist before
the fix.

- **`test_hold_matches_retry_selection`** (R3). It is parametrized over `attempts ∈ {1, 2, 3, 4}`
  and `eager ∈ {False, True}`.
- It seeds one group with only complete rows of every kind:
  - success;
  - `CANCEL_STATUS`;
  - a failure at attempt 1;
  - a failure at attempt 3;
  - an already-retried failure, with its successful successor;
  - a signal death (`-9`);
  - a never-ran sentinel (`-1001`);
  - a user-set `attempt=0` failure.
- It also seeds a retryable failure in another group.
- It asserts `count_retryable(attempts, 0, eager) == len(Task.next(limit=100, group=0,
  attempts=attempts, eager=eager))`, and that every returned row is a retry, with `previous_id`
  set. Both directions of R3 are pinned in one equality. The test is the drift guard that keeps
  the predicate and the scheduler's selection from parting.

### 2.5 Manual (`AGENTS.md`)

The "Group-gating can stall by design" paragraph says `increment_group` "won't advance while the
current group has incomplete tasks". P2 extends that to "incomplete tasks or failures still
awaiting retry". The new shared helper joins the "`attempts == max_retries + 1` spans …" sentence,
which now reads `server.py`, `Task.query_retryable`/`select_failed`/`count_retryable` and
`Task.increment_group`. `invariants.md` §3 stays accurate as written, so no harness edit is needed.
`docs/_include/task_groups_alt.rst` already states the intended behaviour, so it is unchanged. No
CLI change is made, so §12's docs and completions rule does not trigger.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | `increment_group` retryable hold (§2.1), placed after the incomplete check, so a failure committed after `Task.next` holds the group. The next LOAD's `Task.next` creates the retry before any later group is touched. Pinned by `test_group_advance_waits_for_pending_retry`. |
| R2   | The hold returns a viable same-group `TaskGroupInfo`, so `load_bundle` neither logs `Completed task group` nor takes the `Failed task group` branch. The hold precedes the exhausted check, so a group holding both is held, not halted. Asserted from log records in the R4 replay. |
| R3   | `query_retryable` is the one filter chain under both `select_failed` and `count_retryable`. `count_retryable` mirrors `Task.next`'s `attempts`/`eager` gate and requires `group`. Both decisions call it with the scheduler's own `attempts`, `eager` and `current_group`. Pinned by `test_hold_matches_retry_selection`. |
| R4   | `test_group_advance_waits_for_pending_retry` (`unit`, deterministic, red on `c1d4470` in P1, green in P2). |
| R5   | The hold never fires outside the race window, because the decisions run only after `Task.next` returned nothing. The exhausted, `forever`, cancelled, `attempts == 1`, retried-success and no-failure paths are unchanged in code and pinned by the P1 guards plus the existing integration tests (§6). |
| R6   | The fix closes the window `test_group_failed_task_with_retries` lost its third row to. The test file stays byte-identical to `c1d4470` (checked in P2's verify). Its cross-matrix pass is CI evidence after publish. |
| R7   | The stop-branch conjunct (§2.2), after `count_remaining`, scoped to `current_group`. It covers the last group and runs without groups (group 0). Pinned by `test_stop_waits_for_pending_retry`. |
| R8   | `test_stop_waits_for_pending_retry` (`unit`, deterministic, red on `c1d4470` in P1, green in P2). |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research (gate #1: the GOAL is sane against §1–§5) and again against this design (gate #2).

- **§1 Task lifecycle.** No new state and no new column. "Retryable" is built from the existing
  terminal predicate: `exit_status IS NOT NULL` implies the row was written back by the Receiver.
  The logic lives in `Task` classmethods. `server.py` only calls them and hand-rolls no predicate.
- **§2 `exit_status` / `CANCEL_STATUS`.** The shared query keeps `!= 0` and `!= CANCEL_STATUS`, so
  every consumer (selection, hold, stop) inherits the filter from one place. No sentinel is added.
- **§3 Retry model.** Retries are still new rows created only by `Task.next`. Nothing mutates a row
  to retry it, and the `previous_id`/`next_id` chain is untouched. `attempts == max_retries + 1` is
  consumed, not changed: `server.py` still derives it, and the hold uses the scheduler's own value.
  Stall-by-design for exhausted failures is unchanged, and the new hold always resolves (§2.3).
- **§4 Server modes.** In `in_memory` mode there is no Scheduler. `no_confirm` is unaffected.
  `forever` gets the group hold and keeps skipping the stop branch.
- **§5 Concurrency.** No new state, thread, lock or queue operation. The hold reuses
  `load_bundle`'s existing back-off and LOAD re-entry, and the signal poll at the top of
  `load_bundle` still runs every pass. Threading and transactions are a non-goal and stay
  untouched.
- **§6 Shutdown ordering.** Unchanged. FINAL still requires zero incomplete rows, so no result can
  arrive after the scheduler stops.
- **§12 Conventions.** New tests are tagged `unit`, use `@mark.parametrize`, and carry no R-IDs in
  source or comments. No CLI change.

### Deviation justifications

No row changes what any R-ID SHALL do. These rows record added complexity only.

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `count_retryable` returns 0 when `attempts <= 1 and not eager`, duplicating `__next_not_eager`'s `attempts > 1` gate rather than sharing it | `attempt` is user-writable (`hs update attempt=0`). At `attempts == 1` non-eager `Task.next` never selects that row, so an unguarded hold would loop forever, breaking R3 ("a task that holds … is one the scheduler will retry") and R5. Eager `Task.next` does select it, so the guard must include `eager` to keep R3 exact. `test_hold_matches_retry_selection` pins the two copies together. | **No guard:** possible infinite hold. **Guard on `attempts` alone:** diverges from eager selection. **Moving the gate into the shared query:** changes `select_failed` in eager mode, a retry-selection change the GOAL rules out. **Refactoring `__next_not_eager` to call a shared gate:** edits retry selection to save one line. |
| `Task.increment_group` gains an `eager=False` keyword | The hold must know the mode, for the guard above. | Reading `eager` from config inside the model would make the model depend on server config and could disagree with a programmatic `Scheduler(eager=…)`. |

## 4. Rabbit holes (resolved)

- **Can the extra check stall FINAL forever?** No, when it is scoped to `current_group` and
  mirrors `Task.next`'s `attempts`/`eager` gate (§2.3)
  ([`research/02-final-safety.md`](research/02-final-safety.md)).
- **Do successive SELECTs see the Receiver's commit?** Yes. The session is thread-local,
  `Task.next` commits, pysqlite does not open a transaction for SELECTs, and PostgreSQL READ
  COMMITTED takes a snapshot per statement. The incomplete-first order closes the window under
  REPEATABLE READ too ([`research/02-final-safety.md`](research/02-final-safety.md)).
- **Can a `unit` test drive the scheduler deterministically?** Yes. Rebind `Session` to a per-test
  SQLite file, use `Scheduler(queue=None, poll=0)`, and inject a one-shot `Task.next` wrapper. A
  scratch prototype reproduced both defects on `c1d4470`'s source in both `eager` modes
  ([`research/01-test-harness.md`](research/01-test-harness.md)).
- **Does `count_remaining` need to change?** No. The autoscaler and the ETA depend on its meaning,
  and folding retryable failures into it would make `Scheduler.start` stall across groups.

## 5. Risks & open questions

- **CLI drives need local port binding.** `hsx` binds a loopback port. A sandbox that forbids
  local binding fails the drive in `find_available_ports` with `StopIteration`, before any task
  runs. That failure is an environment fault, not a verify failure. The builder needs an
  environment that allows local binding. The integration tests in `uv run pytest` have the same
  need.
- **Back-off latency on a hold.** A hold costs one `poll` back-off (0.5 s minimum, doubling up to
  `--poll`). It fires only inside the race window, so its cost is one ordinary idle cycle.
- **Out of scope, recorded for a possible `issues/` seed. The foreman decides; this plan writes
  nothing outside `spec/`.**
  - (a) `Scheduler.start` stops at once on `--restart` into a database whose only unfinished work
    is a retryable failure, for example a server killed between writeback and retry. That is
    before any LOAD, so outside R7. Changing it changes restart semantics.
  - (b) A late duplicate result from an evicted client writes the whole row back, including
    `retried=False`. That can resurrect an already-retried row and break the UNIQUE `previous_id`
    chain on the next `Task.next`. This defect is pre-existing; the fix neither causes nor worsens
    it.

## 6. Verification strategy

- **P1 (red first).**
  - `git diff --quiet c1d4470 -- src` proves the code under test is `c1d4470`'s.
  - `uv run pytest -m unit -rxX tests/test_retry_gate.py` exits 0 only if every replay case fails
    **by assertion** (strict `xfail`, `raises=AssertionError`; an XPASS or any other exception
    fails the run) and every guard passes.
  - The builder also runs `uv run pytest -m unit --runxfail tests/test_retry_gate.py`, which must
    fail, and pastes its failure summary into the P1 commit body as the red record.
- **P2 (fix).**
  - `git diff --quiet c1d4470 -- tests/test_groups.py` covers R6's "unmodified" clause.
  - No `xfail` may remain in `tests/test_retry_gate.py`, or the strict markers would XPASS-fail
    anyway.
  - The full `uv run pytest` suite must pass. It covers the new unit tests and the existing
    integration coverage for R5: `test_groups.py::test_group_hard_failure_halts`,
    `test_cancel.py::test_cancelled_task_not_retried` and `::test_cancelled_task_not_reverted_on_restart`,
    the `attempts == 1` group and restart runs, `test_restart.py::test_cluster_new_file_runs_against_completed_db`,
    and `test_group_failed_task_with_retries` itself.
  - Two CLI drives run through `.agents/factory/bin/temp_site.sh`:
    - **Grouped retry:** n:1 in group 0 succeeds only on attempt 3, and group 1 holds n:2. The
      drive asserts 5 rows, n:1's successful attempt is 3, n:2 succeeded in group 1, and 0
      remaining.
    - **Ungrouped retry:** the same without groups. The drive asserts 4 rows, attempt 3
      succeeded, and 0 remaining.
- **R6 cross-matrix evidence** comes from hosted CI (Python 3.11–3.14) after publish. No phase
  waits on it. No cluster jobs are needed.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
