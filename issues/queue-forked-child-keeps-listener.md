---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A forked child of a serving process keeps the queue server's port

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

With the in-process queue server, the listening socket belongs to the serving process itself. A
child forked from that process (for example with the `fork` start method of `multiprocessing`)
inherits the listening descriptor. After the server shuts down in the parent, the child still holds
the socket:

- the port cannot be bound again (`EADDRINUSE`) while the child lives;
- a new client connects (the inherited socket still listens) and then gets no reply, instead of
  being refused.

Once the child exits, the port can be bound again. With the earlier manager-child server
(`b819de6`), the same sequence lets the port be re-bound at once, and a new connection is refused.

Reviewer's reproduction (`spec/zone-queue-primitive/` REVIEW cycle 5, Observations): start a
`QueueServer` on an ephemeral port, start a `fork`-context `multiprocessing.Process` that sleeps for
a few seconds, shut the server down, then, while the child lives, try to bind the port and to connect
to it (re-bind fails with `EADDRINUSE`; the connect gets no reply within 2 s). After the child exits,
the re-bind succeeds. Identical at `aa55e86`, at `43d2d85` and after the review-5 remediation.

It touches R5 of `zone-queue-primitive` (shutdown releases the port). There is no CLI path:
HyperShell never forks a serving process. It starts tasks and clients with `subprocess` and exec,
and the listening socket is close-on-exec. Only a library user who forks a serving process sees it.

**Untested:** closing the inherited listener in the child (the queue module's `register_at_fork`
hook already resets the hand-off state there), and whether live connections inherited the same way
also delay the peer's end-of-file.

## Why it was deferred

An observation of `zone-queue-primitive` review cycle 5, outside the reviewed delta (identical at
the cycle-4 head). The maintainer's cycle-5 triage seeded it.

## Outcome / vision

Shutting down an in-process queue server releases its port even while a forked child of the
serving process is alive.

## Sketch of the acceptance criteria

- **R1** — WHEN a process that serves queues in-process forks AND the server then shuts down in the
  parent, the port SHALL be bindable again and a new connection SHALL be refused, while the child
  lives.
- **R2** — A test SHALL fork a serving process, shut the server down, and assert the re-bind and the
  refusal within a bounded time; skipped where `fork` is unavailable.

## Notes

- Related: `spec/zone-queue-primitive/` (REVIEW cycle 5 Observations; GOAL R5).
- Found by: `zone-queue-primitive` review cycle 5, 2026-11-02; seeded by the review-5 remediation.
