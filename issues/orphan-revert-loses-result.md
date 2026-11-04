---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Orphan recovery can discard a result that lands while it runs

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

When the heartbeat monitor evicts a client, `Task.revert_orphaned` (`src/hypershell/data/model.py`)
first selects that client's in-flight rows (`select_orphaned`: scheduled, not completed,
`client_id` matches), then reverts them by id with `revert_all`, an unconditional update in a
separate statement. Nothing re-checks that the rows are still in flight at the moment they are
reverted.

If the evicted client's legitimate result is committed by the Receiver between the select and the
revert, the revert wipes it: `revert_all` nulls 10 of the 13 result columns (only `csvpath`,
`cores_max` and `memory_max` survive), the row returns to the unscheduled pool, and the task runs a
second time. The work and its recorded result are lost, and the operator sees no sign of it. On
SQLite the window is real because the driver opens the write transaction only at the update.

Evidence: found by reading during `writeback-scoped-conditional`'s research
(`spec/writeback-scoped-conditional/research/02-callers-concurrency-tests.md` §3). **Not
reproduced**. **Pre-existing** in `develop`.

## Why it was deferred

`writeback-scoped-conditional` fenced the result write and kept revert unchanged (its R9). Fixing
this changes revert.

## Outcome / vision

Reverting an orphaned task is conditional on the row still being in flight, so a result that has
already landed is never undone.

## Sketch of the acceptance criteria

- **R1** — A test SHALL inject a result commit between the select and the revert (a deterministic
  replay, no sleeps) and show today's outcome.
- **R2** — WHEN orphan recovery reverts a row, THEN it SHALL do so only while the row has no
  `completion_time`, in the same statement as the check.
- **R3** — Revert by operator command (`hs update --revert`) and restart recovery SHALL behave
  as today, or any change to them SHALL be stated in the GOAL.

## Notes

- The single-statement form is the same shape as the result fence: `UPDATE … WHERE id IN (…) AND
  completion_time IS NULL AND client_id = :evicted`.
- Related: `issues/evicted-client-fencing-and-cancel-reach.md`,
  `issues/coordinator-resilience-retry-backoff.md`.
- Found by: `writeback-scoped-conditional` plan research, 2026-11-02.
