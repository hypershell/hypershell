# 01 — The `retried` mark and `Task.cancel_all` on develop, and who can contend them

Read and probed on `92af9c1` (develop `6da633d` plus the GOAL). Probes are in [`probes/`](probes/);
their output is quoted below as run on 2026-11-14 (macOS, SQLite 3.53.1, PostgreSQL 18.7,
TimescaleDB 2.32.0).

## What the retry path does today

`Task.next` → `__next_eager`/`__next_not_eager` → `__schedule_next_failed_tasks`
(`src/hypershell/data/model.py:847-867`) → `Task.next`'s dispatch stamp (`__write_rows`, prefix
`d_`). [`probes/retry_path.py`](probes/retry_path.py), one failed row, `attempts=2`:

```
1. statements: SELECT -> SELECT -> INSERT -> COMMIT -> SELECT -> SELECT -> UPDATE -> COMMIT -> UPDATE -> COMMIT
```

Three transactions: the retry row (`add_all`, committed), the mark (`update_all` →
`bulk_update_mappings`, committed), the dispatch stamp. The mark is the second commit.

**A contended mark today** (the probe makes the mark's statement match 0 rows at the driver, as a
2.29+ hypertable does):

```
2. contended mark: StaleDataError: UPDATE statement on table 'task' expected to update 1 row(s); 0 were matched.
   rows after: [failed: attempt 1, retried 0, next -], [retry: attempt 2, previous = failed, unscheduled]
   next round retried it again: [failed, failed]
   rows after next round: failed marked once, two attempt-2 rows naming it
```

`Task.next` raises (the Scheduler thread dies, the server exits); the retry row stays committed
and unscheduled while the failed row stays unmarked, so the next round selects the orphaned retry
row as a new task *and* retries the failed row again: two retry rows with one `previous_id`.

**A stop between the two commits** (a `BaseException` at the mark, then the connection dropped):

```
3. stop between insert and mark, rows left: [failed: retried 0], [retry: previous = failed, unscheduled]
```

Same doubled retry on the next start.

**Aside (out of scope, unchanged by this plan):** with `eager=True`, `Task.next` returns a fresh
retry row twice (`4. eager next returned [x, x] (duplicate)`; also with the server's own arguments,
`group=0, zone=DEFAULT_ZONE_ID`): `select_new` re-reads the retry row the same call just committed.
An `hsx -r 2 --eager -N4 -b 8` drive ran each attempt once, so something downstream absorbs it; not
traced. The plan keeps the commit before `select_new`, so this is neither fixed nor worsened.
Candidate seed.

## `Task.cancel_all` / `Task.cancel`

`model.py:1165-1185`: `update_all` (`bulk_update_mappings` by id, committed). A contended row that
exists raises `StaleDataError`; a gone row raises the same; `update_all` has no rollback, so the
session is left for the caller. Returns `None`. In-tree callers: none (tests call `Task.cancel` to
set up cancelled rows: `tests/test_dispatch_staged.py`, `test_writeback.py`, `test_topology_server.py`,
`test_contended_recovery.py`). `hs update --cancel` does not use it (`task.py`'s filtered
`Query.update`), so `Task.cancel_all` is Python API only, as the GOAL says.

## Can an in-server writer still contend the mark? Yes, narrowly.

Every in-server `task` writer is now conditional on `completion_time IS NULL`: Confirm
(`confirm_all`, pending), the Receiver (`apply_results`, same dispatch and incomplete), the eviction
revert (`held`), the staged reverts (pending), the hand-back revert (incomplete). `revert_all`'s
unconditional callers run before the Scheduler starts (`revert_interrupted`). A failed row is
complete, and the Scheduler can select it only after the result write committed. So a statement
that *begins* after that commit never matches, and PostgreSQL does not lock a row its WHERE
rejects in the statement's snapshot.

But a conditional write that *began before* the result committed waited on the Receiver's row lock,
then re-checked the new version and matched 0 rows. [`probes/pg_probes.py`](probes/pg_probes.py)
shows that it **keeps the row lock until its own transaction ends**, on plain PostgreSQL 18 and on
a TimescaleDB 2.32 hypertable alike:

```
hs_probe_cw_pg:
  late confirm after the result committed matched 0 row(s); its transaction stays open
  retried mark while the late confirm is open: blocked (LockNotAvailable) - the failed re-check kept the row lock
  retried mark once the late confirm committed: matched 1
  control: a confirm begun after the result committed matched 0; the mark beside its open transaction matched 1 (no wait)
hs_probe_cw_ts:
  timescaledb 2.32.0, t is a hypertable
  (the same four lines)
```

So the in-server contenders are: a late confirmation for the failed task racing its own result (a
fast-failing task, the case the seed named), and likewise an eviction revert, staged revert,
hand-back revert or duplicate result that began before the failure's result committed. The window
runs from the result's commit to the end of that writer's transaction (one bundle's per-row
writes), and the Scheduler must select and mark the row inside it: narrow, but nothing prevents it.
The holder in that case only *locked* the row; whether TimescaleDB 2.29–2.31 reports 0 rows after
waiting on a lock-only holder (rather than one that updated the row, the known trigger) cannot be
checked locally (2.32 does not reproduce the miss at all) and is not needed: the design re-runs the
mark on any 0-row match whatever the holder did. The cross-process contenders are unchanged: `hs
update` (any form) and a Python API caller.

## Shape of the fix (feeds PLAN §2.1–2.2)

- `__write_rows` already gives the re-run loop, the per-row matched count, the bound, the WARNING and
  the gone-row rule. The mark needs two things it lacks: a value that differs per row (`next_id`)
  and an open transaction (the retry rows' INSERT must commit with the marks). `cancel_all` needs
  nothing new: `condition=None` (any row with that id) is exactly "raise only for a row that is
  gone", and the ids returned say which rows were written.
- The mark's condition is "still awaits retry": the row-state filters of `query_retryable`
  (`retried IS FALSE`, `exit_status` not NULL, not 0, not `CANCEL_STATUS`). Selection scoping
  (`attempt < attempts`, group, zone) is not re-asked: no contender this cycle covers changes them,
  and the selection already applied them.
- Order inside the one transaction: marks first, then INSERT only the retry rows whose mark landed,
  then one commit. Mark-first also keeps SQLite's write lock free until the first mark, which the
  fault-injection harness's `meanwhile` writes (another connection) depend on.
- There is no foreign key on `next_id`/`previous_id` (`model.py:363-370`), so a mark naming a retry
  row not yet inserted is legal inside the transaction.
