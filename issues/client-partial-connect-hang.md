---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A client cut off partway through connecting hangs until it is evicted

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A client opens several queue connections over its first seconds: the manager handshake, the proxy
creates and increfs, then one persistent connection per thread that uses a queue (scheduler,
collector, heartbeat). When the server accepts some of them and drops a later one, the thread that
lost its connection dies with `EOFError` ("Uncaught exception from ClientScheduler"), but the
client process does not exit. Tasks it had already received and confirmed stay assigned to it, so
the server keeps waiting for them until the HeartMonitor evicts the client (`server.evict`, 600 s
by default) and its tasks are rescheduled.

Evidence (`spec/zone-queue-primitive/` review-2 remediation, TECH P1 gate record): a server with
its open-file limit lowered to 128 (soft and hard) and 44 clients from `b819de6`, 300 `echo` tasks,
`--no-tls`. The server, stopped at 90 s, had completed 271/300 on `b819de6` and 205/300 with the
in-process queue server; 4 and 15 clients respectively were still running, each with a client
exception log for `EOFError` from its scheduler. On `b819de6` the drop comes from the manager child
running out of descriptors; on the in-process server, from its descriptor reserve refusing the
connection. The client behaviour is the same on both.

**Untested:** the same hang after other mid-start failures (a server restart, a network reset), and
whether the collector or heartbeat thread dying instead hangs the same way.

## Why it was deferred

Found while remediating `zone-queue-primitive` review cycle 2, outside that cycle's scope: it is
client-side, and `client.py` was off-limits. **Pre-existing** on `b819de6`.

## Outcome / vision

A client that loses a queue connection it needs exits promptly and non-zero, so its tasks are
recovered without waiting for eviction, or it retries the connection within a bound.

## Sketch of the acceptance criteria

- **R1** — WHEN a client thread that uses a queue dies with a connection error, the client SHALL
  stop its other threads and exit with `exit_status.runtime_error` within a bounded time.
- **R2** — A test SHALL drop one of a client's connections after its handshake and assert the exit,
  and that the server reschedules the client's confirmed tasks.

## Notes

- Related: `spec/zone-queue-primitive/` (REVIEW cycle 2 C3; TECH P1 gate record).
- Found by: `zone-queue-primitive` build, review-2 remediation, 2026-11-02.
