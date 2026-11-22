---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# `Task.cancel_all` overwrites a result that lands before it

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Task.cancel_all` writes each row by `id` "whatever state it is in" (`data/model.py:1436-1453`):
schedule and completion time to now, `exit_status` to `CANCEL_STATUS`. A caller that cancels its
unfinished tasks has to read which are unfinished first, and then cancel those ids. A result that
lands between the read and the write is overwritten: a task that succeeded or failed reads as
cancelled, its real exit status is gone, and its completion time is restamped.

The other order is already safe. A result that arrives after a cancel is classified STALE, because
the cancel restamps `schedule_time` and the result write matches on the dispatch
(`data/model.py:1525-1526`). `hs update --cancel` is safe too: its filter is part of the UPDATE
itself (`Task.update_matching`, or `Task.update_selected` under `--limit`), so
`hs update --cancel --remaining` cannot reach a row that completed after the selection. Only the
model's by-id cancel is unconditional.

External submitters use it for exactly the read-then-cancel pattern:

- `hypershell-mcp`'s cancel action selects the eligible ids, then calls `Task.cancel_all(chunk)`
  (`hypershell_mcp/backend.py:927`).
- `nf-hypershell`'s bridge cancels a run's unfinished tasks. It does not call `cancel_all`; it
  writes the same fields with `completion_time IS NULL` on the UPDATE, for this reason.

**Pre-existing** on `develop`.

## Why it was deferred

Found from outside the repository, while building `hypershell/hypershell-nextflow`, which worked
around it in its own bridge. No cycle was open on the model's write paths.

## Outcome / vision

Cancelling a task that has already completed is a no-op, decided in the same statement as the
write, so no caller needs its own copy of the cancel.

## Sketch of the acceptance criteria

- **R1** — WHEN `Task.cancel_all` is given the id of a task whose `completion_time` is set, it SHALL
  leave that row unchanged and SHALL leave its id out of the ids it returns.
- **R2** — The condition SHALL be part of the UPDATE, not a separate read.
- **R3** — The existing contract SHALL hold: one transaction, a gone row raises `StaleDataError`,
  and a contended row is re-run up to `CONTENDED_RERUNS` times.
- **R4** — A test SHALL complete a task and then cancel it by id, and assert that its result
  survives.

## Notes

- Decide at promotion whether an explicit "cancel even if complete" is still wanted by anyone.
  Nothing in `src/` calls `cancel_all` except `Task.cancel`.
- The returned ids already let a caller tell which rows were cancelled. With R1, a completed row
  falls into the same "not cancelled" set as a contended one, so the docstring should say which is
  which.
- A prerequisite for `issues/submitter-lease.md` (its sweep cancels a lapsed owner's unfinished
  tasks, R4).
- Related: `issues/evicted-client-fencing-and-cancel-reach.md` (a cancel that reaches the running
  process).
- Found by: `hypershell/hypershell-nextflow` development, 2026-11-21 (its `DESIGN.md`, *What
  HyperShell should grow*, item 7).
