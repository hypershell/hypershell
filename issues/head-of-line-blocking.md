---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Head-of-line blocking on unstartable tasks

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

An executor that cannot acquire resources for its *current* task sleeps and retries the same task
(`client.py:730-740`) rather than yielding the slot to another runnable one. Throughput erodes when
declarations are mis-sized, and the condition is invisible to the operator — the client looks busy.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, L8) — a review pass.
**Pre-existing** in `develop`. Low severity: it wastes throughput, it does not corrupt or lose work.

## Outcome / vision

A blocked executor makes progress on work it *can* run, and a persistently blocked one is visible.

## Sketch of the acceptance criteria

- **R1** — WHEN an executor cannot acquire resources for its current task, it SHALL be able to make
  progress on another eligible task rather than spinning on the same one.
- **R2** — Persistent blocking SHALL be observable to the operator.
- **R3** — Task ordering and group-gating semantics SHALL be preserved.

## Notes

- R1 interacts with priority aging/backfill in `core/resource.py` — check that reordering here does
  not defeat the aging that already exists.
- `zone` reduces the *frequency* of this (right-sized clients pull right-sized work) without fixing
  the mechanism.
- Found by: GNU Parallel comparison review, 2026-08 (L8).
