---
status: unshaped
kind: fix
appetite: big
target: 2.10.0
lane: public
---

# A client takes work it cannot start while other clients have room

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The server hands a task to whichever client asks first, and a client asks whenever an executor
thread is free, not when it has the cores for the next task. The work request carries the client's
identity, zone and bundle size, but nothing about its free capacity (`client.py:242-246`). By
default a client runs one executor thread per core (`client.py:1338-1340`). Each executor takes a
task off the local queue (`client.py:812`) and only then asks for its cores
(`acquire_resources`, `client.py:628`, called at `:844`), waiting until they are free. So a client
holds as many tasks as it has threads, whatever they need, and a wide task can wait on a busy node
while another node sits idle.

Seen on Gautschi with `nf-hypershell` (2.9.0a8, static scaling, three 192-core nodes, job
19034701): 1000 one-core tasks of 1 s, plus six 32-core tasks of 20 s. The client that connected
0.8 s before the others took all six wide tasks. Five fit beside the one-core tasks it was running;
the sixth waited about 20 s for that node while the other two had cores free. In a rerun with a
faster database (job 19044491) the six wide tasks went to two nodes (4 and 2), not three.

**Pre-existing**: the request has never carried capacity.

## Why it was deferred

Found from outside the repository, while building `hypershell/hypershell-nextflow`. Fixing it
changes what a client tells the server, or adds a hand-back from client to server, either of which
is a protocol change and so a minor release. Until then, the plugin documents putting wide tasks in
a zone of their own.

## Outcome / vision

A task waits for cores only when no client in its zone has them. A client does not hold work it
cannot start while another client could start it now.

## Sketch of the acceptance criteria

- **R1** — WHILE a client in a zone has the cores and memory a pending task declares, that task
  SHALL NOT wait on another client's resources for longer than a bound to be agreed.
- **R2** — Tasks that declare no resources SHALL be dispatched as today, at today's throughput.
- **R3** — Group gating, the assignment fence and the retry chain SHALL be unchanged.
- **R4** — A test with two clients of unequal free capacity SHALL show a wide task go to the one
  that can start it.

## Notes

- Three directions, to weigh at promotion:
  - The request carries free cores and memory, and the scheduler fills it with tasks that fit.
    This needs placement in the scheduler's staging, which today stages by zone, not by fit.
  - A client stops asking while its free capacity is below what the work it already holds needs.
    This is client-only, but it starves a client that only ever sees wide tasks.
  - A client hands back a task it cannot start within a while, with the `handback` call relays
    already use (`core/dispatch.py`). This is the smallest change, and it bounds the wait rather
    than removing it.
- Related: `issues/head-of-line-blocking.md` is the same wait seen inside one client: an executor
  holding an unstartable task does not yield its slot. A fix to that one alone does not move work
  between clients.
- Zones are the workaround today: right-sized clients for right-sized work (`zone-dispatch`, #78).
- Found by: `hypershell/hypershell-nextflow` development, 2026-11-21 (its `DESIGN.md`, *What
  HyperShell should grow*, item 10).
