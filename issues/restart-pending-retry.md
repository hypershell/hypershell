---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A restarted run stops at START while a retryable failure is pending

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Scheduler.start` (`src/hypershell/server.py:178-193`) decides whether a resumed run has anything
left to do with `Task.count_remaining() == 0`, which counts only rows with `completion_time IS
NULL`. A failed task whose retry has not been created yet (`exit_status != 0`, not
`CANCEL_STATUS`, `attempt < attempts`, `retried` false) is complete by that predicate. So when the
database holds such a failure and nothing incomplete, a non-`forever` server started with
`--restart` (no submitter, or its submitter finished) logs "All previous tasks completed -
stopping" and returns FINAL. The retry `Task.next` would create is never made, and the run exits 0.

A database reaches this state when a server is interrupted after the Receiver writes a retryable
failure back but before the next LOAD creates the retry row, or when a finished run is resumed
with a higher `-r`.

Evidence: an in-process probe under `.agents/factory/bin/temp_site.sh` creates the schema, adds
one failed row (`exit_status=1`, `attempt=1`, `retried=False`) and calls
`Scheduler(queue=None, attempts=3, restart_mode=True, poll=0).start()`. It prints "retryable
pending: 1" and returns `SchedulerState.FINAL`, both before and after
`spec/group-advance-retry-gate/`. That cycle's review recorded it as PLAUSIBLE finding P1 and two
reviewers reproduced it independently.

## Why it was deferred

Found by `group-advance-retry-gate`'s review and triaged out of that cycle's scope. That cycle
closed the race in LOAD, where a failure is committed after `Task.next` finds nothing to schedule;
this is the START path, before any LOAD runs. **Pre-existing** in `develop`: the probe gives the
same FINAL on the commit that cycle forked from. Changing it changes restart semantics, which
deserves its own contract.

## Outcome / vision

A resumed run stops only once no retryable failure is pending, whichever state it starts in. A
run with retries left finishes them, and a run with none left still stops at once.

## Sketch of the acceptance criteria

- **R1** — WHEN a non-`forever` server starts with no incomplete rows but a retryable failure
  pending (by the predicate `Task.count_retryable` shares with `Task.next`), the scheduler SHALL
  proceed to LOAD and create the retry rather than stop.
- **R2** — WHEN no retryable failure is pending, `start` SHALL stop exactly as today.
- **R3** — Cancelled rows (`CANCEL_STATUS`) and failures that have used their attempts SHALL NOT
  keep a resumed run alive.

## Notes

- Related: `spec/group-advance-retry-gate/` (`REVIEW.md` finding P1; `research/02-final-safety.md`,
  "Restart into a pending retry"). `Task.count_retryable` from that cycle is the predicate to
  reuse.
- Found by: `group-advance-retry-gate` review, 2026-11-01.
