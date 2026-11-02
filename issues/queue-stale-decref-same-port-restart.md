---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A same-key, same-port restart in one process receives the old server's decrefs

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

With the in-process queue server, one process can stop a server and start another on the same port.
A dropped same-process client of the old server, collected after the new server starts, sends its
decrefs under the old object ids to the new server. When a new queue reuses an old id (frequent in
one address space), the new server releases it while a live client holds it, and that client's next
call fails with `RemoteError` (`KeyError`).

Reviewer's reproduction (`spec/zone-queue-primitive/` REVIEW cycle 4, Observations): servers
restarted on one port in one process with the same key, short-lived clients dropped as cyclic
garbage, and live clients on each successor. Both the build before the decref hand-off and the one
with it send the stale decref. Failures appear only with the in-process server: the earlier
manager-child build had 0 failures over about 1,500 servers per run, with 396–591 stale decrefs,
though it cannot reliably restart on the same port in one process either (a start fails with
`EOFError` from the child).

It needs the same key, the same port and live clients on the successor. There is no CLI path:
`run_local` uses a random key (150 `run_local` runs on one port passed), and no CLI path restarts a
server in-process.

**Untested:** a server that tags the objects it issues per lifetime, so that a stale id cannot name
a new object.

## Why it was deferred

An observation of `zone-queue-primitive` review cycle 4, outside the reviewed delta: it came with
that feature's same-port restart in one process (its R5). The maintainer's triage seeded it. The
hand-off's delay makes it more frequent:
[`queue-decref-handoff-delay.md`](queue-decref-handoff-delay.md).

## Outcome / vision

A decref sent to a server never releases an object that server did not issue to that client.

## Sketch of the acceptance criteria

- **R1** — WHEN an in-process server restarts on the same port with the same key, a decref issued
  against the previous server SHALL NOT release an object of the new one.
- **R2** — A test SHALL reproduce the restart with a dropped old client and a live new one and
  assert the live client's queues keep working.

## Notes

- Related: `spec/zone-queue-primitive/` (REVIEW cycle 4 Observations and P5).
- Found by: `zone-queue-primitive` review cycle 4, 2026-11-02; seeded by the review-4 remediation.
