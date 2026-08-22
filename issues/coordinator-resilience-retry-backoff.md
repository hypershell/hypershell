---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Transient coordinator failures should degrade to retry, not to process death

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

HyperShell has no notion of a *transient* failure on the coordinator path. A database lock, a
server restart, or a momentary network drop is treated as fatal, and the blast radius is the whole
process — plus, because in-flight work is neither durable nor re-deliverable, real task loss and
re-execution. Three specific holes, one shared root:

**1. SQLite runs on library defaults.** Engine setup injects only `check_same_thread=False`
(`src/hypershell/data/core.py:213-214`) and the default config carries no pragmas, so the pragma
listener (`data/core.py:287-292`) applies nothing — journal mode, synchronous level, and busy
timeout are all SQLite defaults. Meanwhile the documented topology writes one file from several
threads (scheduler commit, `Receiver`, `Confirm`, `HeartMonitor`) plus external `hs submit`
processes and live submit. There is no retry or backoff anywhere: the only `OperationalError`
handling maps to log-and-exit (`src/hypershell/data/__init__.py:226`). A routine
`database is locked` under submit-while-running kills the server mid-run — most likely
spurious-crash source in real deployments.

**2. One coordinator blip kills the whole fleet.** Client queue reads catch only `QueueEmpty`
(`src/hypershell/client.py:216`); `EOFError` maps to a fatal disconnect handler
(`client.py:1545-1548`), and there is no reconnect or backoff anywhere (grep `reconnect` is empty).
A server restart or a transient network drop terminates *every* client simultaneously.

**3. Completed-but-unrecorded results die with the process.** Results sit in the in-process
`completed` queue until the `Receiver` commits them (`server.py:455-458`); a server crash loses
them, the rows stay "interrupted", and restart reverts and re-runs finished work (`server.py:197`,
`data/model.py:695-698`). The same happens client-side: a client killed by (2) loses whatever its
collector had buffered.

These are one feature, not three fixes, because they share an invariant: **a transient failure of
the coordinator path degrades to retry-with-backoff — never to process death, and never to lost
results.** Fixing the database half without the reconnect half still drops fleets; fixing
reconnect without result parking still loses work on every reconnect.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, H10 + M5 + M6, with
M14 adjacent) — a review pass. All **pre-existing** in `develop`. Recorded as one shaped feature
rather than three fixes because the maintainer scoped it that way: the queue infrastructure itself
should retry and reconnect gracefully rather than crash, and that is a design, not a patch.

## Outcome / vision

A `database is locked` becomes a short backoff and a successful commit, not a dead server. A server
restart becomes a reconnect with jittered backoff on each client, not a dead fleet — clients park
completed-but-unsent results and redeliver them when the coordinator returns. A client keeps its
bundle until the server confirms the commit, so the ack window closes rather than being documented
as a hazard. Operators see retries in the log as events, not as crashes.

## Sketch of the acceptance criteria

- **R1** — The SQLite provider SHALL ship working defaults for the documented multi-writer topology
  (`journal_mode=WAL`, `synchronous=NORMAL`, a non-zero `busy_timeout`) through the existing
  `[database.pragmas]` plumbing, overridable by configuration.
- **R2** — WHEN a database operation on the coordinator path fails with a transient error
  (lock/busy contention), it SHALL be retried with bounded backoff; only after the bound is
  exhausted SHALL it escalate — and escalation SHALL NOT be the default response to one lock.
- **R3** — WHILE the coordinator is unreachable, a client SHALL NOT terminate; it SHALL reconnect
  with bounded, jittered backoff, and resume without operator intervention.
- **R4** — Completed-but-unreturned results SHALL survive a reconnect: a client SHALL park them and
  redeliver on reconnection, and SHALL retain a bundle until the server confirms the commit
  (the `confirmed` channel already exists), so the at-least-once window is bounded rather than
  merely documented.
- **R5** — Redelivery SHALL be idempotent — a duplicate return SHALL be harmless.
- **R6** — Retry, reconnect, and redelivery SHALL be observable: logged as such, and distinguishable
  from a real failure, so an operator can tell a healthy retry from a degrading system.
- **R7** — The control queues SHALL NOT grow without bound while a consumer is stalled
  (`heartbeat` and `confirmed` are `maxsize=0` today, `core/queue.py:379-380`) — a stalled consumer
  SHALL shed or aggregate rather than grow the coordinator's memory.
- **R8** — Every blocking operation introduced SHALL keep the finite-timeout, re-enter-the-state
  discipline `AGENTS.md` requires of FSM states; shutdown latency SHALL NOT regress.

## Notes

- **`relay` inherits this directly.** `relay` R9 is verbatim this machinery — "WHILE the upstream
  server is unreachable, the relay SHALL continue operating its site … reconnect with backoff, and
  return buffered results on reconnection; a relay restart SHALL NOT lose completed-but-unreturned
  results." Building it once in the client/queue core means the relay's upstream connector inherits
  it instead of reimplementing it. Strong argument for sequencing this **before** `relay`.
- **`turso` shares R1's seam.** `turso` R4 consolidates `data/core.py` into an explicit
  per-provider abstraction (connect-argument policy, pragma injection, `in_memory` detection).
  Landing pragma defaults here first gives that consolidation a real second case to generalise
  from; landing them after means conforming to it. Either order works — the shaped GOAL should say
  which and why.
- R7 overlaps the DoS surface tracked in the security lane (unbounded frames / no connection cap).
  Bounding the queues is defensible on its own operational merits; coordinate the wording so the
  public issue does not read as a vulnerability disclosure.
- Appetite is genuinely `big` and the phases are natural: pragmas + commit retry (self-contained,
  shippable alone) → reconnect/backoff → result parking + ack tightening → queue bounds. Consider
  a pilot/follow-up split at shaping if it exceeds ~8–10 criteria.
- Touches `core/queue.py`, `client.py`, `server.py`, `data/core.py` — the coupled, largely untested
  concurrency core. Research fan-out expected regardless of appetite.
- Found by: GNU Parallel comparison review, 2026-08 (H10, M5, M6, M14), scoped into one feature by
  the maintainer, 2026-08-07.
