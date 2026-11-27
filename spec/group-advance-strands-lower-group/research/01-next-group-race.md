# 01 — The next-group choice: race, late lower groups, and the empty-database start

> Read-only research brief for `hs-plan`, written by the planner. Questions: does the candidate
> "smallest incomplete group" query close the race without breaking #68's gates, what else must
> change for R4 and R5, and does anything stall?

## The code at `b31c66e`

- `Task.increment_group(previous, attempts, eager)` (`data/model.py`) runs four statements in
  order: the incomplete count for `previous`, `count_retryable` (#68), the exhausted-failure
  count, then `min(group) > previous` among **all** rows. Only the last one picks the group, and
  it can only go up.
- `Scheduler.load_bundle` (`server.py`) calls it only after `Task.next(group=current)` returned
  nothing and the stop branch declined. A different returned value logs
  `Completed task group A - starting task group B` and re-enters LOAD.
- `Scheduler.start` takes `Task.current_group()`: the group of the most recently scheduled row,
  else `DEFAULT_TASK_GROUP` (0) on an empty database ("no tasks submitted"), else the lowest
  group.
- `Task.group` is `NOT NULL`, so `min()` never meets a NULL group.

## The candidate fix

`min(group) WHERE completion_time IS NULL`, one statement, replacing the last query. If it is
`None`, keep "no task groups remain"; otherwise return it, even when it is lower than `previous`.
The three gates before it stay as they are.

- **The race closes.** The query reads the current group's late rows and any higher group's rows
  in one snapshot (pysqlite runs no transaction for SELECTs; PostgreSQL READ COMMITTED takes one
  snapshot per statement). If `previous` gained incomplete rows after the first count, the query
  returns `previous` itself, and `load_bundle` sees no change, backs off once and re-enters LOAD,
  where `Task.next` schedules them. The plan should give that return a reason string; today it
  would log `(None)` at trace.
- **Late lower groups run (R1).** A lower group with incomplete rows is the minimum, so the
  scheduler moves down to it once the current group clears its gates. In `forever` mode the stop
  branch is skipped, but `increment_group` is the same, so R1 holds there too.
- **No poll with work left (R2).** While `count_remaining() > 0`, some group has an incomplete
  row, so the query never returns `None`. Incomplete rows are either unscheduled (which `Task.next`
  takes) or in flight. Rows are in flight only in the current group, because leaving a group
  requires its incomplete count to be zero. The exceptions are the R7 holds and halts.
- **Gates unchanged (R7).** The retryable hold and the exhausted halt are evaluated for
  `previous` before the query, exactly as now. A cancelled row has `completion_time` set, so it
  is never "incomplete". A lower group that holds an exhausted failure **and** new incomplete rows
  is entered, its new rows run, and it then halts. That is consistent with "a group whose
  failures have used their retries stops a non-`forever` server".
- **Restart (R6).** On a database the defect hung, `current_group()` returns the higher group H
  (most recently scheduled). `Task.next(H)` is empty, the gates pass, the query returns the lower
  group L, and L runs. Then the query returns `None`, the stop branch sees `count_remaining() == 0`,
  and the run reaches FINAL. The restart-of-a-halted-run case is unchanged: `current_group()`
  is the halted group, and the exhausted check returns not-viable before the query.
- **Liveness.** A move happens only to a group with incomplete rows, and each LOAD in that group
  schedules or holds, so no new oscillation exists. Moving down cannot loop: a group is left
  only when it has no incomplete rows.

## What the candidate leaves open

**1. The empty-database start (R4, R5).** On an empty database the scheduler starts in group 0
and calls `Task.next(group=0)` on every LOAD. Take ascending stream input that begins below 0,
for example `-1, -1, 0`. If the first LOAD runs after both groups' rows are committed, which is
likely after one back-off, group 0 runs first and group -1 runs after it with a warning. That
breaks R4 (ascending input runs in order whatever the timing). It also breaks R5's sentence that
ascending input beginning below 0 logs no warning. The scratch prototype shows it on the
candidate patch: order `[0, -1, -1]`.

The fix is to make the first group provisional when `start()` finds an empty database. Until
the first task is scheduled, each LOAD resolves the group as the smallest incomplete group. When
there is none, that LOAD skips `Task.next` entirely, so the scheduler never schedules into a
group it picked before any task existed. Two narrower rules fail:

- Re-resolving whenever nothing is scheduled yet would let `--restart` on a halted database skip
  the halted group. `current_group()` points at that group, and the smallest incomplete group is
  the next one. That breaks R7.
- Resolving once, then calling `Task.next(0)` when the result is `None`, leaves a window between
  the two statements in which `-1` and `0` can both land.

For ascending input, no lower row can land between the resolve query and `Task.next(group)`,
because rows commit in order. Unsorted streams stay best effort (R1, R5).

**2. The R5 warning.** It needs run-local state in `Scheduler`: the highest group in which a
task was scheduled this run, and the group of the last scheduled bundle. On the first bundle
scheduled in a group that is lower than the highest started group, log one WARNING that names
both groups. A group the scheduler only waited on never schedules a bundle, so it never counts as
started. A restart has started nothing at that point, so moving down from H to L there logs no
warning.

## Prototype (scratch, out of tree)

A standalone script drove `Scheduler(queue=None, poll=0)` against a per-run SQLite file, with
commits through a separate session.

| Case | `b31c66e` | Candidate patch |
|------|-----------|-----------------|
| Race: group 0 drained; group-0 and group-1 rows land inside `increment_group` (pending group-2 row keeps the stop branch off) | moves to 1, runs `1, 2`, group 0 stranded, no FINAL | stays at 0, runs `0, 1, 2` after the first, FINAL |
| Lower group committed after group 1 started | group 0 never runs, no FINAL | group 0 runs, FINAL |
| Same, `forever` | group 0 never runs | group 0 runs |
| Empty-database start, `-1,-1,0` committed before the first LOAD | `[0]` then hangs | `[0,-1,-1]`: out of order |

Two harness lessons follow.

- The stop branch also calls `count_retryable`, so an injection keyed on `count_retryable` alone
  can fire in the stop branch instead of in `increment_group`. The replay must arm the injection
  only while `increment_group` runs, by wrapping it.
- When the race is replayed from an empty database, the provisional start resolves it, which
  masks the race fix. The race replay must start from a group that has already started.

## Found in passing: early FINAL with a co-running submitter (pre-existing, not this defect)

Once a task has been scheduled, the stop branch no longer checks the submitter. It reaches FINAL
as soon as every committed row is complete, even while the submitter is still reading a stream.
A CLI drive confirmed it on `develop`: `(echo 'echo a'; sleep 8; echo 'echo b') | hsx -N1` runs
`a`, exits 0, and leaves `echo b` unscheduled in the database. This is the opposite failure to
the hang (work dropped, not polled forever).

File input is immune once ingest-first lands (02). For stream input, the fix on `develop` is one
conjunct (`self.submission_complete()`). 2.8.3's `Scheduler` has no submitter reference,
because that plumbing came with `b27ee58`, so a port would breach R9. It goes to the foreman,
not into this plan.
