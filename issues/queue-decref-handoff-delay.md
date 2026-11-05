---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Handed-off decrefs can outlive their server and reach a same-port successor

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

In a process that serves queues in-process, a decref finalized on the server side is handed to one
sender thread (`core/queue.py` `_decref`, `_send_decrefs`), which sends them in order, one connection
each. (Since the review-5 remediation the server side is decided by thread. The measurements below
were taken under the earlier rule, which handed off every decref for an address the process served,
on any thread.) A decref collected while its own server is alive used to reach that server at
once; now it waits for the sender. Under decref churn the sender falls behind, and a decref then
reaches whatever server holds the port when it is sent, which can be a same-key successor in the
same process. When the successor's queues reuse the old object ids, it releases a queue that a live
client holds, whose next call fails with `RemoteError` (`KeyError`): the class of
[`queue-stale-decref-same-port-restart.md`](queue-stale-decref-same-port-restart.md).

Reviewer's scenario (`spec/zone-queue-primitive/` REVIEW cycle 4, P5): 1,800 servers in one
process, 4 lanes, same-port restarts, short-lived clients dropped from many threads, collection
confined to one dedicated thread (so no build can deadlock), 3.13 and 3.14, no TLS. Each decref
that reached a server other than its issuer was attributed by when it was collected:

| Python | build | stale, collected while its own server listened | stale, collected after it stopped | live-client `RemoteError` |
|---|---|---|---|---|
| 3.13 | before the hand-off | — | 4593 | 9 |
| 3.13 | with the hand-off | 2160 and 2882 | 1598 and 2746 | 43 and 43 |
| 3.14 | before the hand-off | — | 4257 | 0 |
| 3.14 | with the hand-off | 2678 | 2220 | 22 |

With normal collection thresholds the deferred share was 0–36 of about 3,200–3,500 stale decrefs
per run. The increase is statistical; no single failure was traced to a deferred decref. The same
stress also ran 103–158 s with the hand-off against 31 s without it.

**Untested:** whether it occurs without same-port, same-key restarts in one process, which no CLI
path performs.

## Why it was deferred

Found by `zone-queue-primitive` review cycle 4 (LOW, PLAUSIBLE), stress only. The maintainer's
triage seeded it rather than widen the final remediation cycle. It does not share a cause with
[`queue-proxy-finalizer-swap-window.md`](queue-proxy-finalizer-swap-window.md).

## Outcome / vision

A handed-off decref reaches the server that issued the reference, or is dropped once that server
has stopped; never a successor.

## Sketch of the acceptance criteria

- **R1** — A handed-off decref whose server has stopped SHALL NOT be sent to another server on the
  same address.
- **R2** — A stress test of same-port restarts in one process SHALL show no stale decref attributed
  to the hand-off.

## Notes

- Related: `spec/zone-queue-primitive/` (REVIEW cycle 4 P5 and Observations).
- Found by: `zone-queue-primitive` review cycle 4, 2026-11-02; seeded by the review-4 remediation.
