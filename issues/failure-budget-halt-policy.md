---
status: unshaped
kind: feature
appetite: small
lane: public
---

# No failure-budget halt policy

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The only early stop is group-gating — by design (`server.py:225-241`, `data/model.py:512-550`).
There is no "abort after N failures", no "abort after X percent", no "stop on first success", and no
distinction between halting *now* (kill running work) and halting by *draining*. A large campaign
whose first thousand tasks all fail for the same environmental reason keeps burning allocation until
someone notices.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, M3) — a review pass.
**Pre-existing** in `develop`; a genuine capability gap rather than a defect.

## Outcome / vision

An operator can give a campaign a failure budget and have the server stop when it is exceeded,
choosing whether to drain or to stop immediately.

## Sketch of the acceptance criteria

- **R1** — A server-side halt policy SHALL be configurable in terms of failure count and/or rate.
- **R2** — The policy SHALL distinguish draining from stopping in-flight work.
- **R3** — Group-gating semantics SHALL be unchanged; this is an additional stop condition.

## Notes

- Of the deferred (no-cycle-scheduled) items, this is the one most likely to be requested by a real
  user first — worth revisiting its horizon if anyone asks.
- The "stop now" variant needs to reach running processes, which is
  `issues/evicted-client-fencing-and-cancel-reach.md`'s control path. Drain-only has no such
  dependency and could ship alone.
- Found by: GNU Parallel comparison review, 2026-08 (M3).
