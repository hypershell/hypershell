---
status: unshaped
kind: fix
appetite: small
lane: public
---

# The Scheduler's `retried` mark and `hs update`'s task-row writes fail when contended on a TimescaleDB hypertable

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

Under the `timescale` provider, `task` is a hypertable. On TimescaleDB 2.29 and later, an `UPDATE …
WHERE id = X` that waited on another transaction's row lock matches **0 rows** once that transaction
commits, although the row exists and still satisfies the `WHERE` clause; PostgreSQL (and TimescaleDB
before 2.29) re-checks the row and matches 1. A new statement sees the commit and matches normally.
[`spec/hypertable-update-retry/`](../spec/hypertable-update-retry/GOAL.md) fixed the server's
in-server writers: Confirm (`Task.confirm_all`), the eviction and start-up reverts
(`Task.revert_all`), and the result write (`Task.apply_results`). Its audit
([`research/02-write-path-audit.md`](../spec/hypertable-update-retry/research/02-write-path-audit.md),
rows 3, 4 and 7–10; the verdicts are in its `TECH.md`) left these writers as they are:

- **The Scheduler's dispatch stamp** (`Task.next`) — **fixed since** by `relay-hardening` (#103, its
  R2). It was an ORM flush, which asserts the matched total, so a contended 0 raised
  `StaleDataError` in the Scheduler and the server exited mid-run. Each selected row is now stamped
  by its own UPDATE that matches only while the row is still unscheduled, re-run under contention as
  the other in-server writes are, and a row that never lands is left for a later round
  (`src/hypershell/data/model.py:793-822` on `develop`, `f6a1d62`). Kept here as the pattern for the
  writers below.
- **The `retried` mark** (`__schedule_next_failed_tasks`: `retried`, `next_id` via `update_all`,
  `model.py:847-867`) is a bulk update by id. Its in-server contender is a confirmation lagging a
  fast failure, or an eviction revert of a row selected in flight and then completed. A contended 0
  raises in the Scheduler; the retry rows are already committed by the preceding `add_all`, so the
  old row stays `retried IS FALSE` and is **retried a second time** on the next selection or
  restart. A recovery that gives up at a bound has the same hazard, so this path needs its own
  exhaustion outcome; it cannot reuse "warn and leave the row as it is".
- **`Task.cancel_all` / `Task.cancel`** (bulk update by id) have no in-tree caller (Python API only);
  a contended 0 raises to the caller.
- **`hs update`, single-row legacy form** (`Task.update` → `update_all`): a contended 0 raises
  `StaleDataError`, which the command reports as `Task.NotFound` — a **false "not found"** for a row
  that exists.
- **`hs update --limit` batches** (`update_all` per batch): a contended 0 raises after earlier
  batches have committed — a partial update, then a traceback.
- **`hs update` set / `--cancel` / `--revert` / tag edits** (`Query.update`, a filtered bulk
  UPDATE): contended rows are **silently skipped**, and "Updated N" is still logged from a count
  taken earlier. These inline their own column lists, so the `Task.revert_all` fix does not reach
  `--revert`.

Not probed: the DELETE paths (`hs update --delete`, `Task.delete_all`). Upstream #9315 moved
hypertable DELETE onto the same expansion as UPDATE, so a contended DELETE may miss rows the same
way.

**Pre-existing** in `develop` since the `timescale` provider landed (#71); introduced by the
TimescaleDB release, not by a HyperShell commit.

## Why it was deferred

`hypertable-update-retry` was a small-appetite fix for the paths reachable inside one server that CI
was failing on. Its GOAL (R4) seeds a cross-process path rather than fixing it unless the fix is
"the same few lines" and needs no new test harness; the foreman ruled at its plan gate that the
Scheduler's in-server contention is seeded here too. None of these is the same few lines:
`Task.next` had to move off the ORM flush onto a per-row core UPDATE with a count check and a choice
of what "still qualifies" means (done since in `relay-hardening`, #103, which took the stamp only and
left the rest here, its GOAL *Non-goals*); the `retried` mark needs a doubled-retry-safe exhaustion
outcome; and `hs update` needs its matched counts reconciled against its targets, with a re-run that
re-evaluates the user's filter.

## Outcome / vision

On a TimescaleDB hypertable of any supported version, every writer of `task` rows behaves as it does
on PostgreSQL when its write waited on another writer: the Scheduler never exits because a mark was
contended, a failed row is never retried twice on that account, and `hs update`
reports and applies exactly the rows its filter selects.

## Sketch of the acceptance criteria

- **R1** — *Met by `relay-hardening` (#103, its R2): a contended dispatch stamp of a row that still
  qualifies lands, and the Scheduler keeps running.* Kept so R2–R4 keep the numbers that GOAL cites.
- **R2** — WHEN the `retried` mark of a failed row is contended, the row SHALL end retried exactly
  once, including when the recovery bound is exhausted: the mark and its retry rows are written
  together or not at all.
- **R3** — WHEN `hs update` contends with a running server, it SHALL report a missing row only
  when the row is missing, and its "Updated N" SHALL count the rows actually written.
- **R4** — A test SHALL force each race deterministically on a server database, as
  `tests/test_contended_write.py` does, failing on TimescaleDB 2.29+ before the fix.

## Notes

- Pattern and harness: [`spec/hypertable-update-retry/`](../spec/hypertable-update-retry/PLAN.md)
  (`CONTENDED_RERUNS`, `Task.confirm_all`/`revert_all`, `Task.apply_results`,
  `tests/test_contended_write.py`, `tests/test_contended_recovery.py`); the rule is in `AGENTS.md`
  § Data layer specifics.
- Related: [`stale-confirm-hides-orphan`](stale-confirm-hides-orphan.md) (Confirm against eviction
  ordering; the late confirmation that contends with `Task.next`),
  `spec/relay/` R19 (#82: a confirmation applied to a completed row now writes only while the row
  is incomplete; named above as the contender of the `retried` mark),
  `spec/relay-hardening/` (#103: the eviction revert now writes a row only while it is still in
  flight, its R1, and the dispatch stamp is the per-row conditional write above, its R2, covering a
  row a relay deleted as well as a contended one),
  [`coordinator-resilience-retry-backoff`](coordinator-resilience-retry-backoff.md) (thread death
  turned into retry).
- For `coordinator-resilience-retry-backoff`: a pre-existing deadlock on every PostgreSQL provider.
  Confirm locks its bundle's rows in bundle order, and the Receiver locks its result bundle's rows
  in completion order and holds them all until one commit; interleaved, they can deadlock, and the
  losing thread dies with `OperationalError`. Not reproduced, and not introduced or worsened by
  `hypertable-update-retry` (research 02, finding 4).
- Found by: `hypertable-update-retry` planning (the R4 audit), 2026-11-04.
