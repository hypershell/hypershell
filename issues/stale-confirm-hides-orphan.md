---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A task whose confirmation has not landed is invisible to orphan recovery

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A client confirms the tasks it received by sending their ids, and the server's `Confirm` thread
writes `client_id` and `client_host` onto those rows by id alone (`Confirm.update_info` →
`Task.update_all(ClientInfo.transpose())`, `src/hypershell/server.py`). Until that confirmation is
applied, a dispatched row has `schedule_time` set and `client_id` NULL. The `confirmed` queue is
drained by its own thread, so it is not ordered against eviction.

Orphan recovery finds an evicted client's rows by `client_id` alone (`HeartMonitor.check_clients` →
`Task.revert_orphaned(A)` → `select_orphaned`, `client_id == A`). Suppose client A is evicted after
it received a task but before its confirmation is applied:

1. `revert_orphaned(A)` skips the row, whose `client_id` is still NULL. The row stays in flight:
   `schedule_time` set, no `completion_time`, and `Task.next` never dispatches it again.
2. If A's confirmation is applied afterwards, it names the already-evicted A on the row. Unless A
   reconnects, it is not evicted again, and evicting any other client does not select the row
   either. (A reconnected A re-registers through the heartbeat, then either completes the row or
   is evicted again, which reverts it.)

Either way the row stays in flight with nobody running it until the server restarts and
`revert_interrupted` picks it up. In a long or `--forever` run that can be a long time, and the group
cannot advance past it.

Evidence: reproduced in-process during `writeback-scoped-conditional`'s review (cycle 1, F2,
`spec/writeback-scoped-conditional/REVIEW.md`). After a dispatch and `revert_orphaned(A)` with no
confirmation applied, the row keeps `schedule_time` with `client_id` NULL and `Task.next` returns
`[]`. After A's late confirmation and `revert_orphaned(B)`, the row is still in flight with
`client_id == A`. **Pre-existing** in `develop`; that cycle neither causes nor worsens it.

## Why it was deferred

`writeback-scoped-conditional` held the confirmation path and the wire fixed (its R9), and the row's
recovery is a question about eviction and confirmation, not about results.

## Outcome / vision

Every row dispatched to a client that is later evicted returns to pending without a restart,
whether or not that client's confirmation had been applied when it was evicted.

## Sketch of the acceptance criteria

- **R1** — A test SHALL drive both interleavings in-process and show the row is not reverted
  today: (a) `Task.next` dispatches the row; `Task.revert_orphaned(A)` with no confirmation
  applied; the row still has `schedule_time` set, `client_id` NULL, and `Task.next` returns `[]`.
  (b) Then A's confirmation is applied through `Confirm.update_info`, and `revert_orphaned` for
  another client leaves the row in flight with `client_id == A`.
- **R2** — WHEN a client is evicted, THEN every row dispatched to it and not completed SHALL return
  to pending, including a row whose confirmation had not yet been applied.
- **R3** — WHEN a confirmation arrives naming a client that has already been evicted, THEN the
  server SHALL NOT leave the confirmed rows in flight under that client.
- **R4** — Any wire change SHALL keep working with peers of the same schema version, or go through
  a negotiated version step.

## Notes

- The token `writeback-scoped-conditional` uses for results, the dispatched `schedule_time`, would
  let a confirmation be matched to its dispatch if it carried it per task id. Whether recovery needs
  that, or a bound on how long a dispatched row may stay unconfirmed, is for `/hs-plan`.
- Related: `issues/late-confirm-overwrites-attribution.md` (confirmations applied by id alone and
  unordered, relay-facing), `issues/evicted-client-fencing-and-cancel-reach.md`, `issues/relay.md`.
- Found by: `writeback-scoped-conditional` plan research, 2026-11-02. Corrected by that cycle's
  review (cycle 1, F2): the first version had the row reverted and rescheduled to a second client,
  which eviction cannot do to a row with no `client_id`.
