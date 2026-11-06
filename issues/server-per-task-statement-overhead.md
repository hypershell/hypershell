---
status: unshaped
kind: perf
appetite: small
lane: public
---

# Server CPU per task bounds throughput at small bundle sizes

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

With bundle size 1, every task costs the server's main process its own scheduling, confirm and
writeback statements: 5.02 statements and 3.01 commits per task in the foreman's profiling
(2026-11-04). With 256 single-executor clients, TLS and SQLite on a 16-CPU server node, 2.8.3
peaked at 129.0 tasks/s. The evidence points at the server process's CPU as the limit: #70 added
about 6% CPU per task and cost 4.3% of throughput, and on a 1-CPU server node it cost 8.0%. Nobody
has yet shown how much of the ceiling the database accounts for.

`writeback-prebuilt-update` showed that building an ORM statement per call is expensive.
`Task.__apply_result` built a new `update(cls).where(…).values(…)` for every result, and SQLAlchemy
coerced it and generated a fresh cache key on every execute, which cost about 0.1 ms of CPU per
result. Prebuilding that statement brought the write below 2.8's cost. The scheduler's per-bundle
`Task.next` (SELECT, UPDATE, refresh) and the confirm write may build their statements per call in
the same way. That is a hypothesis: nobody has profiled those paths to that depth.

**Pre-existing**: the scheduler and confirm paths behave the same in 2.8.3 and on `develop`.

## Why it was deferred

It was a non-goal of `writeback-prebuilt-update`, whose appetite was to recover #70's regression
before 2.9.0a3. These paths are not part of that regression, and `Task.next` sits in
`data/model.py`, the repository's highest-blast-radius file.

## Outcome / vision

At small bundle sizes the server spends less CPU per task, so a single server sustains more tasks
per second before it saturates. Scheduling order, group gating, the retry chain and the assignment
fence stay exactly as they are.

## Sketch of the acceptance criteria

- **R1** — WHEN the server schedules, confirms and applies the result of a task at bundle size 1,
  its main process SHALL spend measurably less CPU per task than `develop` at promotion, with the
  method and numbers recorded.
- **R2** — The task-lifecycle predicates in `AGENTS.md` § Task lifecycle contract and every
  scheduling outcome SHALL be unchanged.
- **R3** — A deterministic test (not a wall-clock benchmark) SHALL guard each path that is changed,
  as `writeback-prebuilt-update` R4 does for the writeback.

## Notes

- Options to weigh at promotion: prebuilt statements for `Task.next` and the confirm write;
  fewer commits per task; batching results across bundles in the `Receiver`. The last one changes
  when results land, so it is a design decision, not a mechanical speed-up.
- Related: `spec/writeback-prebuilt-update/` (the writeback half, and the measurement method),
  `spec/writeback-scoped-conditional/` (#70), `spec/relay/` (#82; relay multiplies
  fan-out, not row throughput, so this per-task cost still bounds a relayed campaign).
- Found by: `writeback-prebuilt-update` shaping, from the foreman's 2026-11-04 profiling.
