---
status: unshaped
kind: fix
appetite: small
lane: public
---

# Eager mode can list a new retry row twice in one bundle

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

In eager mode, `Task.next` calls `__next_eager` (`src/hypershell/data/model.py:643-650`), which
first creates retry rows for failed tasks (`__schedule_next_failed_tasks`, `model.py:663-682`) and
then, when the bundle limit still has room, tops the bundle up with `select_new`
(`model.py:586-591`). `select_new` selects rows with `schedule_time IS NULL`. The retry rows just
created still have `schedule_time` NULL, since `Task.next` stamps it only after both selections
return, so `select_new` picks them up again. The list `Task.next` returns then holds the same retry
row twice, and the server packs both entries into the bundle. A client can receive the same retry
twice whenever `bundlesize > 1`.

Evidence: a probe calling `Task.next(..., eager=True)` with one retryable failure and room in the
limit returns 2 entries with 1 distinct id, both before and after `spec/group-advance-retry-gate/`.
That cycle's `test_hold_matches_retry_selection` compares retries by row id for this reason, and
its `TECH.md` P2 amendment records the raw count (10 entries for 5 retries).

**Untested:** whether the client actually runs the command twice. What the second result does to
the row is settled: `writeback-scoped-conditional` (#70, its R5) leaves the row unchanged and logs
the repeat as a duplicate.

## Why it was deferred

Found while building `group-advance-retry-gate`, outside that cycle's scope: it concerns what a
bundle contains, not whether a group advances or a run stops. **Pre-existing** in `develop`.

## Outcome / vision

Each retry row appears at most once in what `Task.next` returns, in eager and non-eager mode, and a
client never receives the same task twice in one bundle.

## Sketch of the acceptance criteria

- **R1** — WHEN `Task.next` runs in eager mode with room left after creating retries, the returned
  list SHALL contain each row at most once.
- **R2** — The number of new tasks topping up the bundle SHALL be counted against the limit without
  the retry rows already selected.
- **R3** — A test SHALL establish whether a duplicated entry runs twice on a client today, so the
  fix's effect is measured rather than assumed.

## Notes

- Related: `spec/group-advance-retry-gate/` (`REVIEW.md` § Out of scope; `TECH.md` P2 amendment).
- Found by: `group-advance-retry-gate` build (P2) and review, 2026-11-01.
