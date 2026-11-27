# 02 — FINAL-stall safety, scoping and ordering

> Read-only research brief for `hs-plan`. Question: can holding the group-advance and stop
> decisions on a retryable failure stall a run forever, how should the check be scoped, and does
> its ordering close the race?

## Liveness: every hold is resolved by the next `Task.next`

A hold leaves `load_bundle` through the existing back-off branch (`time.sleep(poll)`, return
`LOAD`). The next `Task.next(limit=bundlesize, group=current_group, attempts, eager)` runs the same
filters on the same group with the same `attempts`, so it selects the failure that caused the hold:

- **Eager** (`__next_eager`): failures are selected first, unconditionally.
- **Non-eager** (`__next_not_eager`): during a hold the group has no incomplete rows, so
  `select_new` returns nothing, `len(tasks) < limit` holds, and failures are selected **if
  `attempts > 1`**.
- Each retry adds one to `attempt`, so holds per logical task are bounded by `attempts`.
- `current_group` is always an `int` at LOAD (`start` sets it before the only transition into
  LOAD), and `Task.group` is `nullable=False`, so the group arguments always match.
- `bundlesize` is not validated. `0` already stalls a run (nothing is ever scheduled); a negative
  value is "no limit" in SQLite. Neither adds a stall through the new check.

**The one stall path: `attempts == 1` in non-eager mode.** `attempt` is user-writable
(`hs update attempt=0` passes `TaskUpdateApp.process_arguments`), so a failed row with
`attempt < 1` is reachable. At `attempts == 1` such a row matches `attempt < attempts`, but
non-eager `Task.next` never selects failures (`attempts > 1` guard): the hold would loop forever.
Eager `Task.next` *does* retry it. The hold must therefore mirror `Task.next` exactly: no hold when
`attempts <= 1` and not `eager`. With valid data (`attempt >= 1`) the predicate is empty at
`attempts == 1` anyway, so default runs see no behaviour change.

## Scoping: the current group, never global

- A retryable failure in a group **below** `current_group` (left by an earlier invocation, or by a
  database written under the defect) is never selected: `Task.next` filters `group ==
  current_group` and groups never move backwards. A global stop check would block FINAL forever
  ("no task groups remain" → sleep → LOAD → repeat).
- R3 settles it: the failures that keep a run from stopping must be exactly those the scheduler
  would select, and the scheduler selects only from `current_group`.
- A retryable failure in a **later** group with every row complete already reaches FINAL today
  without visiting that group. It can only arise from a prior invocation, is outside the R7
  window, and stays unchanged (R5).
- `select_failed` treats `group=None` as all groups. The gating helper must take `group` as a
  required argument so a `None` can never turn the check global.

## Ordering and visibility

- `Session` is a thread-local `scoped_session`; `Task.next` ends with `Session.commit()`, so the
  checks that follow start fresh. pysqlite issues `BEGIN` only before DML, so each SELECT sees all
  prior commits. PostgreSQL READ COMMITTED takes a fresh snapshot per statement.
- For an in-flight row the Receiver only moves it from incomplete to complete, and only the
  scheduler thread sets `retried=True`. So once a check sees zero incomplete rows in scope, nothing
  the Receiver does can change that scope again, and a later retryable-failure SELECT sees every
  failure committed in the gap. **The incomplete check must run first.** The reverse order is
  exactly the defect: the retryable check misses a row still in flight, the Receiver commits, the
  incomplete check sees zero.
- Under REPEATABLE READ or SERIALIZABLE all checks share one snapshot, in which the row is either
  incomplete (hold) or complete and retryable (hold). Still safe; the next commit refreshes it.
- Inside `increment_group` the order is incomplete → retryable → exhausted. Retryable before
  exhausted is what R2 requires ("not treated as halted") for a group holding one of each.
- Outside the race window the hold never fires: `increment_group` and the stop check are reached
  only after `Task.next` returned nothing, which (when it selects failures at all) means no
  retryable failure was visible a moment earlier. Non-race behaviour is unchanged.

## Other paths that end a run or advance a group

| Path | In the R1/R7 window? | Treatment |
|------|----------------------|-----------|
| `load_bundle` stop branch | yes | gains the retryable check (R7) |
| `load_bundle` → `increment_group` | yes | gains the retryable hold (R1, R2); covers `forever` too |
| `Scheduler.start` early FINAL on an all-complete database | no (before any LOAD) | unchanged; changing it changes restart semantics |
| SIGUSR1/SIGUSR2 → FINAL | no | deliberate abandonment, unchanged |
| `ServerThread` shutdown ordering | no | starts after the scheduler joins; FINAL still needs zero incomplete rows |
| HeartMonitor `revert_orphaned` | no | acts on incomplete rows only; `select_new` picks them up |
| `in_memory` | n/a | Scheduler is `None`; no gating |
| `no_confirm` | no | incomplete rows stall as before |

## `count_remaining` stays unchanged

Its callers include the autoscaler (`cluster/remote.py`) and `time_to_completion`/`task_pressure`.
Folding retryable failures into it would change autoscaler pressure and would turn `start`'s early
FINAL into a cross-group stall. The stop branch gets a separate conjunct instead.

## Pre-existing hazards observed (not caused or worsened by this fix)

- **Late duplicate result.** `Receiver.update_tasks` writes the client's whole-row dict, including
  `retried=False` and `next_id=None` as they were when scheduled. A result from an evicted client
  that reports after its task was reverted and retried could reset `retried` on an already-retried
  row; the next `Task.next` would then violate the UNIQUE `previous_id` chain. Exists today through
  `Task.next`.
- **Restart into a pending retry.** If a server stops after the Receiver commits a retryable
  failure but before the retry row exists, a later `--restart` sees every row complete and stops
  in `start` without retrying.
