---
status: unshaped
kind: fix
appetite: small
lane: public
---

# A relay can abort when an eviction revert or a scheduler stamp meets a row it has removed

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

A relay's local server shares its SQLite database with two writers that remove `task` rows and
take no lock the server's threads share: the returner moves complete rows to the outbox and deletes
them (`collect_results`), and the puller deletes rows not yet scheduled at the site when the
upstream server has restarted (`drop_superseded`). Every relay-local write was audited against a
row gone this way, and two still raise. Either one ends a thread of the relay-local server;
`check_site` then aborts the relay with exit 5 and no FINISHED beat, so the upstream server waits
out its eviction period (`server.evict`, 600 s by default) before it puts back what the relay held.
No result is lost in either case.

Where, on `origin/develop` (`b4b37cd`, 2.9.0a6):

- **C7-1, the eviction revert.** When the relay-local server evicts a site client,
  `HeartMonitor.check_clients` (`src/hypershell/server.py:861`, the call at `:874`) runs
  `Task.revert_orphaned` (`src/hypershell/data/model.py:1092-1097`). It selects the client's
  in-flight rows (`select_orphaned`, `:1079-1089`), then reverts them by id with `revert_all`
  (`:940-945`), a per-row write with neither `pending` nor non-strict set, so a row gone since the
  select raises `StaleDataError` (`:903-908`). If the evicted client's result is applied and the
  returner collects and deletes the row (`src/hypershell/relay/store.py:320`, the DELETE at `:348`)
  in that gap, the heart monitor ends. The window is the one gap: once the first UPDATE runs,
  SQLite's write lock holds the other writers off.
- **C7-2, the scheduler's stamp.** `Task.next` (`src/hypershell/data/model.py:758-775`) selects
  unscheduled rows, sets `server_id`, `server_host` and `schedule_time` on the ORM objects
  (`:770-773`) and commits once (`:774`). If the puller's `drop_superseded`
  (`src/hypershell/relay/store.py:236-253`, the DELETE at `:252`, called from `insert_received` at
  `:227`) deletes one of those rows between the SELECT and the flush, the flush raises SQLAlchemy's
  `StaleDataError` and the relay-local scheduler ends. It needs an upstream restart whose first
  bundle reaches the relay in that gap.

Evidence: both reproduced at unit level by forced interleaving (scratch, not kept): the result
applied and rows collected on another thread inside `select_orphaned` (C7-1), and
`insert_received` from a new upstream server id run inside `select_new` (C7-2). Neither reproduced
end to end, and no integration test restarts the upstream server under a relay backlog.

## Why it was deferred

Found by `relay`'s review cycle 7 (2026-11-06) and its audit of the relay-local server's writes,
both rated PLAUSIBLE follow-ups that need not block 2.9.0a6, and listed as known follow-ups on #82.
Introduced with relay (#82): only a relay deletes rows under a running server. Neither fix is local
to relay:

- The narrow C7-1 fix is about three lines: `revert_orphaned` reverts through the same per-row write
  with `pending` set and not strict, as `revert_staged` does, so a row completed or gone since the
  select is skipped. But that is the primary server's eviction path too, which
  `tests/test_contended_recovery.py` and `tests/test_writeback.py` pin. The review preferred a
  relay-local variant; the build's note preferred the shared change. Probed and not kept: with the
  shared change the unit suite passed unchanged and the interleaving no longer raised.
- C7-2 is about a day. `Task.next` would stamp each selected row with a conditional per-row UPDATE
  (`schedule_time IS NULL`) and skip a row that matched nothing, instead of an ORM flush. The retry
  path (`__schedule_next_failed_tasks`, `:800`: the `retried` marks and the retry inserts) needs the
  same treatment, and the returned tasks must be built from the rows that actually landed. That is
  every server's dispatch path.

## Outcome / vision

A relay never aborts because a row it removed was still named by its own server's eviction or
dispatch: the revert skips a row that has completed or gone, the scheduler skips a row it could not
stamp and dispatches the rest, and neither leaves a retry chain half-written.

## Sketch of the acceptance criteria

- **R1** — WHEN the relay-local server reverts an evicted site client's rows and a row has completed
  or gone since it was selected, the revert SHALL skip that row and the heart monitor SHALL keep
  running.
- **R2** — WHEN a row `Task.next` selected is deleted before its dispatch stamp lands, the scheduler
  SHALL dispatch the other rows, skip that one and keep running; a failed row's `retried` mark and
  its retry row SHALL be written together or not at all.
- **R3** — A deterministic test SHALL force each interleaving with no threads or sleeps (engine
  events, as `tests/test_relay_return.py` does for the cycle-8 group check), failing before the fix.
- **R4** — A primary server's eviction and dispatch SHALL behave as today, or the GOAL SHALL state
  the change; a shared C7-1 fix also changes what a primary server does with a result that lands
  between the select and the revert (see Notes).

## Notes

- C7-1's shared fix is the fix [`orphan-revert-loses-result.md`](orphan-revert-loses-result.md)
  asks for (a revert conditional on the row still being in flight). On a primary server the same
  gap discards a result rather than raising; on a relay it raises on a gone row and discards a
  result applied but not yet collected. Shape the two together: one change closes both, and that
  seed's R1 replay test is the primary-server half of R3 here.
- C7-2 overlaps [`hypertable-contended-scheduler-and-cli-writes.md`](hypertable-contended-scheduler-and-cli-writes.md),
  which also moves `Task.next` off the ORM flush, but for a different case: there the row still
  exists and the write waited on a lock; here the row is gone. A contended-write re-run alone still
  raises on a gone row, so whichever lands first should cover both.
- Not reachable in a relay, by the same audit: the Receiver (a gone row is `STALE`), Confirm
  (non-strict on the site server since #82, `src/hypershell/relay/site.py:112`), the staged reverts,
  `revert_interrupted` and every `Client` write. Review cycle 8 re-swept the read-then-use paths and
  found no third path.
- Related: [`stale-confirm-hides-orphan.md`](stale-confirm-hides-orphan.md),
  [`coordinator-resilience-retry-backoff.md`](coordinator-resilience-retry-backoff.md) (thread death
  turned into retry), `spec/relay/REVIEW.md` (cycle 7, C7-1 and C7-2; cycle 8, question 4),
  `spec/relay/TECH.md` (publish notes, 2026-11-06).
- Found by: `relay` review cycle 7, 2026-11-06.
