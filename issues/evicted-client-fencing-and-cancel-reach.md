---
status: unshaped
kind: feature
appetite: big
lane: public
---

# No control path to an evicted client — or to a cancelled running task

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Two symptoms of one missing mechanism: the server has **no way to say anything to a client**. The
queue exposes only `scheduled`/`completed`/`heartbeat`/`confirmed` (`core/queue.py:377-380`) —
there is no control channel.

**Eviction is one-sided.** When a heartbeat ages past `evict_after`, the `HeartMonitor` marks the
client evicted and reverts its tasks (`server.py:621-635` → `Task.revert_orphaned`,
`model.py:737-740`). Nothing is sent to the evicted client: no disconnect, no stop sentinel, no
fence token. A client that was merely partitioned, SIGSTOP'd, or NFS-stalled keeps executing while
the same commands start elsewhere — non-idempotent work (writes, uploads, DB mutations) runs twice
*concurrently*, triggered by routine events like a 10-minute network partition or a paused VM. If
the client returns, the Issue-#29 re-registration branch silently un-evicts it
(`server.py:588-593`) without reconciling the tasks reverted out from under it.

**Cancel never reaches a running process.** `Task.cancel_all` stamps rows terminal
(`model.py:700-714`, correctly, so reverts cannot resurrect them) and `hs task update --cancel`
warns about already-scheduled tasks (`task.py:1059`), but nothing signals any client. The process
runs to completion and its writeback races the CANCEL row.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H1 + M4) — a review
pass. **Pre-existing** in `develop`.

Deferred deliberately rather than fixed: both halves need a server→client control path, and
**`zone`'s request-return dispatch RPC is that seam** (`zone` R3/R4 — a client advertises
identity and receives a targeted reply on its own connection). Building a control channel now and
again in `zone` means building it twice, on the load-bearing untested transport, and then
reconciling them. The corruption half of H1 is being closed independently and without new
transport in `issues/writeback-scoped-conditional.md`; what remains here is genuinely blocked on
the dispatch work.

## Outcome / vision

Eviction is a two-sided event: the server can tell an evicted client to stop, and a client that
returns from a partition reconciles rather than silently resuming. `hs task update --cancel`
reaches the running process. Duplicate concurrent execution stops being reachable through routine
operational events.

## Sketch of the acceptance criteria

- **R1** — WHEN the server evicts a client, it SHALL make a best-effort attempt to stop that client
  and SHALL fence its in-flight work so a late return cannot be applied.
- **R2** — WHEN an evicted client re-registers, the server SHALL reconcile or refuse its stale
  bundle rather than silently un-evicting it.
- **R3** — WHEN a scheduled task is cancelled, the executing client SHALL be informed and SHALL
  terminate the running process (post process-group work, the process *group*).
- **R4** — The control path SHALL NOT regress the default single-zone dispatch path or its
  throughput.

## Notes

- **Sequence after `zone`** — or shape it as a `zone` follow-on. `/hs-feature` should re-confirm
  whether the dispatch RPC actually landed the seam this assumes before accepting the appetite.
- `.local/IMPROVEMENTS.md` suggests piggybacking a cancellation list on heartbeat/confirm replies as
  a lighter alternative to a full control channel — worth evaluating at plan time, since it may not
  need `zone` at all.
- `issues/process-group-signal-teardown.md` R5 wants a remote-stop path for cluster teardown. Same
  question, possibly the same mechanism — decide once.
- Related: `issues/writeback-scoped-conditional.md` (the half already being fixed),
  `issues/zone.md` (the dependency).
- Found by: GNU Parallel comparison review, 2026-08 (H1, M4).
