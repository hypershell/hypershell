---
status: unshaped
kind: feature
appetite: small
lane: public
---

# No live progress / ETA surface

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

To watch a running campaign an operator polls `hs list --count` against the same database the
scheduler is contending on. There is no periodic server-side summary and no progress/ETA surface.
The information — done, failed, rate, remaining — is already computed server-side for the
autoscaler's pressure signal.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, L2) — a review pass.
A capability gap, not a defect.

## Outcome / vision

A campaign reports its own progress without the operator adding load to the database to find out —
at minimum a periodic server log line, at most an `hs top`-style live summary.

## Sketch of the acceptance criteria

- **R1** — A running server SHALL periodically report progress (completed, failed, rate, estimated
  remaining) without requiring operator polling.
- **R2** — The surface SHALL NOT add material contention to the scheduler's database path.

## Notes

- Cheapest version is a periodic log line and costs almost nothing; the interactive view is a
  separate, larger conversation. Consider shipping the log line as part of another cycle rather than
  waiting for the full feature.
- Related: `spec/task-card-view/` (presentation precedent).
- Found by: GNU Parallel comparison review, 2026-08 (L2).
