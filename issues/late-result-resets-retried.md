---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A late result from an evicted client can reset `retried` and break the retry chain

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Receiver.update_tasks` (`src/hypershell/server.py:459-462`) writes each returned task back with
`Task.update_all([task.to_dict() ...])`, which rewrites every column from the client's copy of the
row, including `retried=False` and `next_id=None` as they were when the bundle was scheduled.

Consider a client evicted while its task is running. `HeartMonitor` reverts the task, it runs
elsewhere, fails, and the scheduler creates the retry, setting `retried=True` and `next_id` on the
row. If the evicted client then reports its own (failed) result, the writeback sets
`retried=False` and `next_id=None` on that already-retried row. The row now matches the retryable
predicate again, so the next `Task.next` tries to create a second retry with the same
`previous_id`, which violates the UNIQUE `previous_id` constraint.

Evidence: found by reading during `group-advance-retry-gate`'s research
(`spec/group-advance-retry-gate/research/02-final-safety.md`, "Pre-existing hazards observed").
**Not reproduced**: no test or probe has driven this interleaving yet.

## Why it was deferred

Outside `group-advance-retry-gate`'s scope: that cycle gates group advance and stop on a pending
retry, and neither causes nor worsens this. **Pre-existing** in `develop`.

## Outcome / vision

A stale result can never reopen a retried row or desynchronise the `previous_id`/`next_id` chain.

## Sketch of the acceptance criteria

- **R1** — A test SHALL drive the interleaving above (evict, retry, late failed result) and show
  today's outcome.
- **R2** — WHEN a result arrives for a row that has already been retried, the writeback SHALL NOT
  change `retried`, `next_id`, `previous_id` or `attempt`.
- **R3** — The next `Task.next` after such a result SHALL NOT attempt a second retry of that row.

## Notes

- **Probably discharged by the writeback fix.**
  [`issues/writeback-scoped-conditional.md`](writeback-scoped-conditional.md) R1 makes `retried`
  and `previous_id`/`next_id` unwritable from a returned result and names this chain desync
  among its symptoms. This seed records the specific retry-chain path and its test so promotion
  of that work covers it; if that cycle lands first with such a test, retire this one with it.
- Related: `spec/group-advance-retry-gate/` (`PLAN.md` risks, item b);
  `issues/evicted-client-fencing-and-cancel-reach.md`.
- Found by: `group-advance-retry-gate` plan research, 2026-11-01.
