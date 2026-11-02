---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A stale confirmation can hide a rescheduled task from orphan recovery

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A client confirms the tasks it received by sending their ids, and the server's `Confirm` thread
writes `client_id` and `client_host` onto those rows by id alone (`Confirm.update_info` →
`Task.update_all(ClientInfo.transpose())`, `src/hypershell/server.py`). The confirmation carries no
dispatch identity, and the `confirmed` queue is drained by its own thread, so it is not ordered
against eviction, revert, or a later dispatch.

Suppose client A is evicted after receiving a task but before its confirmation is applied. The task
is reverted, rescheduled, and confirmed to client B. If A's confirmation is applied after that, the
row now names A while B is running it. If B is later evicted, `Task.revert_orphaned(B)` selects rows
by `client_id == B` and misses this one, so it stays "in flight" with nobody running it until the
server restarts and `revert_interrupted` picks it up. In a long or `--forever` run that can be a long
time, and the group cannot advance past it.

Evidence: found by reading during `writeback-scoped-conditional`'s research
(`spec/writeback-scoped-conditional/research/02-callers-concurrency-tests.md` §2). **Not
reproduced**. **Pre-existing** in `develop`; that cycle neither causes nor worsens it (a result that
applies rewrites `client_id`, which repairs the row).

## Why it was deferred

Fencing a confirmation needs the dispatch identity (the row's `schedule_time` as dispatched) on the
confirmation wire, which is a wire-format change, and `writeback-scoped-conditional` held the wire
fixed (its R9).

## Outcome / vision

A confirmation attributes a row only while that row is still under the dispatch the confirmation
is for, so orphan recovery always finds every task an evicted client held.

## Sketch of the acceptance criteria

- **R1** — A test SHALL drive the interleaving above (dispatch to A, evict A, reschedule and confirm
  to B, apply A's confirmation, evict B) and show the row is not reverted today.
- **R2** — WHEN a confirmation arrives for a row no longer under the dispatch it confirms, THEN the
  server SHALL leave the row's attribution unchanged.
- **R3** — Any wire change SHALL keep working with peers of the same schema version, or go through
  a negotiated version step.

## Notes

- The natural token is the one `writeback-scoped-conditional` uses for results: the dispatched
  `schedule_time`. Confirmation would need to carry it per task id.
- Related: `issues/late-confirm-overwrites-attribution.md` (same mechanism, relay-facing),
  `issues/evicted-client-fencing-and-cancel-reach.md`, `issues/relay.md`.
- Found by: `writeback-scoped-conditional` plan research, 2026-11-02.
