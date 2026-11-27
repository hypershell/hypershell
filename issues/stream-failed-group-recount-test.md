---
status: unshaped
kind: test
appetite: small
target: 2.9.x
lane: public
---

# No test pins the re-count before a quiet stop when a failed group ends a finished stream run

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`task-input-fidelity` (#110) made a failed group halt a run only while a task waits in another
group. When none does, `Scheduler.load_bundle` (`src/hypershell/server.py`, the not-viable branch)
stops quietly only if the input has ended **and** a second count, `Task.count_remaining() == 0`,
still finds nothing left; otherwise it backs off and looks again on the next pass.

That second count covers one interleaving: the input has ended, the current group has just failed
permanently, and another writer (`hs submit` into the same database) commits a row after
`count_remaining_outside` read zero. With the re-count the pass backs off, and on the next pass a
row in another group meets the failed-group halt while a row in the failed group is scheduled.
Without it the run stops quietly and leaves that row unscheduled, with no message.

The behaviour is right on `develop`, but no test pins it. The review of #110 (cycle 3, note N1)
removed the re-count as a mutant and every test in `test_stream_input`, `test_task_wire`,
`test_group_order`, `test_retry_gate`, `test_server_fsm` and `test_restart` still passed; the
reviewer's probe showed the difference (`[LOAD, FINAL]` with the critical against `[FINAL]` with
none).

## Direction

Add a deterministic test in `tests/test_stream_input.py`, in the style of its existing
interleaving tests: commit a row between `count_remaining_outside` and the re-count, and assert the
pass backs off rather than returning `FINAL`, for a row in another group and for one in the failed
group. The test must fail with the re-count removed.

## Related

- `spec/task-input-fidelity/REVIEW.md` (cycle 3, N1; probes P1 and P2) and the maintainer's
  "Approve; seed N1" in its PLAN's review-cycle-3 ruling.
- Found by: review of `task-input-fidelity`, 2026-11-15.
