---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A queue proxy keeps the stock decref finalizer until its constructor returns

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

In a process that serves queues in-process, a queue client's proxy finalizer must never connect to
that process's own server from a server thread: the cyclic garbage collector can run the finalizer
on the accept loop, which would then wait on itself. `QueueClient.connect` gives each proxy the
hand-off finalizer (`core/queue.py` `_decref`, applied by `_defer_decref`) only after the stock
proxy constructor has returned. Inside the registered `_get_*` method the proxy is already increfed
with the stock `BaseProxy._decref` finalizer, and the method then sends its own decref on a fresh
connection. If that connection fails while the server is alive, `connect()` raises and the
half-made proxy keeps the stock finalizer, held by the traceback. If the collector later runs it on
the accept loop, the server stalls: the deadlock the hand-off exists to prevent.

Reviewer's scenario (`spec/zone-queue-primitive/` REVIEW cycle 4, P4): a same-process client whose
one decref inside the proxy constructor fails with an injected `ConnectionResetError`, on 3.14 with
TLS on and off. A watchdog fires; the queue server's accept loop is stuck in `_admits` → (gc) →
`BaseProxy._decref` → `answer_challenge`/`secure_client`, and new clients hang.

**Untested:** a natural trigger. A lowered `RLIMIT_NOFILE` made 98 refusals in the reviewer's run,
all on the first handshake, never on that decref. The window existed before the hand-off too, when
every proxy kept the stock finalizer, so it is an incompleteness of that fix, not a regression.

## Why it was deferred

Found by `zone-queue-primitive` review cycle 4 (LOW, PLAUSIBLE), fault-injection only. The
maintainer's triage seeded it rather than widen the final remediation cycle.

## Outcome / vision

No proxy a queue client makes in a serving process ever holds the stock finalizer, including one
abandoned by a failed `connect()`.

## Sketch of the acceptance criteria

- **R1** — WHEN a connection inside a queue client's proxy construction fails while an in-process
  server of the same process is alive, the half-made proxy's finalizer SHALL NOT connect to that
  server from the thread that runs it.
- **R2** — A test SHALL inject that failure, force a collection on the accept loop, and assert the
  server keeps accepting within a bounded time.

## Notes

- Related: `spec/zone-queue-primitive/` (REVIEW cycle 4 P4; TECH P1 "Scope and limits").
- Found by: `zone-queue-primitive` review cycle 4, 2026-11-02; seeded by the review-4 remediation.
