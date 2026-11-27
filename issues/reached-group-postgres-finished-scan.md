---
status: unshaped
kind: perf
appetite: small
target: 2.9.x
lane: public
---

# On PostgreSQL, `Task.reached_group` walks a whole index when every task has finished

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`submit-input-checks` (#118) added `Task.reached_group` (`src/hypershell/data/model.py:838-874`).
It runs once per `hs submit` with a database (`src/hypershell/submit.py:1701`), and once at the
start of `hs cluster FILE` and `hs server FILE` (`src/hypershell/cluster/__init__.py:634`,
`src/hypershell/server.py:2020`). Before #118 none of these ran it.

Its pass start, the earliest submit time of an incomplete task, is an uncorrelated scalar
subquery. On a table where every task has finished, the pass start is NULL and no row can pass the
filter. PostgreSQL plans `max("group")` as a `Limit` over an `Index Scan Backward` on
`index_tasks_unscheduled`, so it walks the whole index before returning NULL. The read is linear,
and its results are identical to the earlier form's. The costs measured in #118's review (cycle 2,
*Triaged, not findings*, reviewer N2):

| Rows, all finished | #118 (`HEAD`) | cycle-1 form |
|---|---|---|
| 1M | 0.23–0.26 s (orchestrator); 0.67–0.79 s (reviewer) | 0.14–0.16 s |
| 3M | about 2.0 s (reviewer) | about 0.42 s |

In other table shapes the #118 form is as fast as the cycle-1 form or faster, and on SQLite it is
linear and at most 58 ms at 100k rows. The review judged this a performance difference, not a
correctness finding, and left a 2.9.x follow-up to the maintainer.

## Why it was deferred

Outside the review rubric's scope (correctness) and below a second at 1M rows, so #118 shipped
without it. **Introduced by #118**: base ran no such query.

## Outcome / vision

On PostgreSQL, a database whose tasks have all finished answers "no reached group" in about the
time of one indexed lookup, whatever its size, and every other shape is no slower than today.

## Sketch of the acceptance criteria

- **R1** — WHEN no task is incomplete, `Task.reached_group` SHALL return None without scanning the
  table or an index, on PostgreSQL and SQLite.
- **R2** — `Task.reached_group` SHALL return the same value as today for every table, as #118's
  randomized comparison checked.
- **R3** — A measurement on PostgreSQL at 1M finished rows SHALL be kept with the change, beside
  the review's figures above.

## Notes

- Direction from the review: read the pass start first, and return None when it is NULL.
- Evidence: [`spec/submit-input-checks/REVIEW.md`](../spec/submit-input-checks/REVIEW.md), review
  cycle 2: *Remediation of cycle 1* (PostgreSQL figures) and *Triaged, not findings* (cause and
  cost). Not re-measured for this seed.
- Found by: `submit-input-checks` review cycle 2 (2026-11-22).
