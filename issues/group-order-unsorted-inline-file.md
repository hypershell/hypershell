---
status: adopted:group-advance-strands-lower-group
kind: fix
appetite: small
lane: public
---

# An unsorted inline task file can run a higher group first

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The docs say groups are "scheduled in ascending order: every task in the active group must reach a
terminal state before any task in the next group is scheduled" (`docs/_include/task_groups_alt.rst`).
Before `group-advance-strands-lower-group`, `hs cluster FILE` and `hs server FILE` ingested the file
on a submitter thread while the scheduler was already running, so the scheduler only saw the groups
whose rows had been committed. When a file listed group 1 before group 0, the scheduler could start
group 1 before any group-0 row existed, and the run either ran in order or hung forever with group 0
stranded (about half of runs with no load).

That cycle adopts this seed with the full hold below: a task file given to `hs cluster` or
`hs server` is read in full before scheduling begins, so its groups run in ascending order whatever
order the file lists them in. The cost, accepted by the maintainer (its GOAL Q1), is that a large
file's first task waits until the whole file is in. The cycle's return to a late lower group, with a
WARNING (its R1, R5), remains for input it cannot read ahead: a stream into `hs cluster`/`hs server`,
or a later `hs submit`.

On a fresh database, `hs submit FILE` followed by `hs server` was not affected: every row is present
before scheduling starts, and the server starts at the lowest group.

## Why it was deferred

Every small change considered either leaves part of the case open or costs files in ascending
order. Shaping recorded three options, and the cycle took the second, the full hold
(`spec/group-advance-strands-lower-group/GOAL.md`, Q1):

- A startup hold in `Scheduler.load_bundle`: while nothing has been scheduled, wait for
  `submission_complete()` or for the starting group to hold rows. Small, but it delays the first
  dispatch until full ingest for any file with no row in the starting group (files that begin at
  group 1, for example), and it does not cover a lower group that arrives after the starting group
  has started.
- A full hold: choose the first group only after submission completes. Complete for inline files,
  but every inline file then waits for full ingest before its first dispatch, which costs
  throughput on large single-group files.
- Something the submitter tells the scheduler: the lowest group in the file, or a pre-scan. This
  changes the ingest path.

**Pre-existing**: the out-of-order start has existed since task groups were added (`0844bf1`). Until
the fix cycle it showed up as a hang rather than as a wrong order.

## Outcome / vision

An inline task file runs its groups in ascending order whatever order it lists them in, as
`hs submit` followed by `hs server` already does, without delaying the first dispatch for files
that are already in ascending order.

## Sketch of the acceptance criteria

- **R1** — WHEN an inline task file lists a higher group before a lower one, the server SHALL
  schedule no task of the higher group until every task of the lower group has reached a terminal
  state.
- **R2** — WHEN an inline task file lists its groups in ascending order, the first task SHALL be
  dispatched no later than today.
- **R3** — Tasks given with `hs submit` to a running server after a higher group has started SHALL
  keep the fix cycle's behaviour: they run late, with a warning.

## Notes

- Related: `spec/group-advance-strands-lower-group/` (GOAL Q1, R4, R9, R10).
- Adopted with the full hold, which gives up this sketch's R2: an ascending file's first task
  now waits for the whole file (GOAL Q1, the named cost of its R3).
- Found by: `group-advance-strands-lower-group` shaping (2026-11-03).
