---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Deterministic sentinels retry to exhaustion and then stall the group

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Both never-ran sentinels — `TASK_TEMPLATE_ERROR` (`-1001`) and `TASK_RESOURCE_ERROR` (`-1002`),
`client.py:612-613` — pass `select_failed`'s filters (`data/model.py:565-569`). So a *deterministic*
template error is retried to exhaustion, after which the group pins: `increment_group` finds the
group non-viable and, under `--forever`, the scheduler never advances (`server.py:225-233`). The
retry budget is spent re-running something that cannot succeed.

Separately, a task whose declared needs exceed the *dequeuing* node's totals is failed outright
with `TASK_RESOURCE_ERROR` (`client.py:579-586`, `:718-724`) — there is no requeue to a fitter
node, so on heterogeneous fleets big-node tasks die on small nodes by luck of the draw.

And the retry default diverges by launch path: standalone servers default to zero retries
(`core/config.py:135`) while clusters default to one (`cluster/remote.py:240` → `server.py:829`).
Same engine, two contracts, undocumented.

## Why it was deferred

Surfaced by the out-of-cycle GNU Parallel comparison (`.local/IMPROVEMENTS.md`, M2) — a review pass.
**Pre-existing** in `develop`.

## Outcome / vision

A deterministic never-ran failure fails once and stops consuming attempts. Retry defaults mean the
same thing everywhere, or differ for a documented reason. A task too large for the node that
dequeued it is not silently condemned by luck of the draw.

## Sketch of the acceptance criteria

- **R1** — A deterministic template error SHALL be terminal: it SHALL NOT consume the retry budget
  and SHALL be reported distinguishably from a task that failed at runtime.
- **R2** — The retry default SHALL be consistent across standalone and cluster launch paths, or the
  divergence SHALL be deliberate and documented.
- **R3** — The group-gating contract SHALL be preserved: a genuinely failing task still blocks its
  group by design (`AGENTS.md`) — this issue removes *wasted attempts*, not the gate.

## Notes

- **The heterogeneous-fleet half is answered by `zone`, not here.** Declaring a `large-mem` zone and
  submitting those tasks to it is the sanctioned routing mechanism (`zone` R5/R8); building
  fleet-capability introspection and cross-node requeue would duplicate it with a worse interface.
  The shaped GOAL should say so explicitly rather than leaving M2's "requeue to a fitter node"
  reading as accepted scope.
- Consistency check with `issues/task-start-spawn-guard.md`: whether a new spawn-error sentinel is
  terminal or retryable should be decided the same way as `-1001` here.
- Found by: GNU Parallel comparison review, 2026-08 (M2).
