# Research 01: the conditional eviction revert (R1)

Read on `fix/relay-hardening`. The code is identical to `develop` `2ffb5cc`, and the line numbers are that commit's.

## Current path

- `HeartMonitor.check_clients` (`server.py:913`) is guarded by `not in_memory and not no_confirm` (`:925`, §4), then calls `Task.revert_orphaned(uuid)` (`:926`).
- `revert_orphaned` (`model.py:1224`) loops `while tasks := select_orphaned(client_id, 100)`, which reads rows with `schedule_time IS NOT NULL`, `completion_time IS NULL` and `client_id = :c` (`:1211`). Each pass then calls `revert_all(ids)` (`:1002`), and the loop stops after a pass that reverts nothing.
- `revert_all` calls `__write_rows(ids, dict.fromkeys(revert_columns), 'revert', 'task left in flight')` (`:937`) without `pending`. That makes one `UPDATE … WHERE id = :r_id` per row, setting the 13 `revert_columns` (`:439`) to NULL.
- When a row matches 0, `__write_rows` re-reads it by id:
  - Row gone: it raises `StaleDataError` and the whole pass rolls back.
  - Row exists: it re-runs up to `CONTENDED_RERUNS` (=3, `:66`) times, logs a DEBUG line per re-run (`Re-running contended revert write …`), and at the bound logs a WARNING (`… - task left in flight`).
- `confirm_all` (`:988`) and `revert_staged` (`:1010`) use the same loop with `pending=True`. Then `__row_statements` (`:916`) adds `completion_time IS NULL`, the re-read reads `completion_time`, a complete row is settled at DEBUG (`task already complete`), and a gone row is skipped unless `strict`.
- `revert_handed_back` (`:1050`, statements at `:1028`) has its own copy of that loop, with an `id + schedule_time + completion NULL + client_id in (c, NULL)` predicate.
- `apply_results` / `__apply_result` (`:1133`/`:1182`) match `id + schedule_time + completion_time IS NULL` and classify the outcome as APPLIED, DUPLICATE, STALE or DEFERRED.
- Other users of `revert_all`:
  - `revert_interrupted` (`:1102`, start-up only, `server.py:232`).
  - `Task.revert` (`:1097`, no caller in `src/`).
  - `hs update --revert` does not use it; it has its own path (`task.py:1137`).
  All of these keep today's behaviour (R10; the seed's R3).
- The heart monitor does not survive an exception today. `StateMachine.next` re-raises (`core/fsm.py:57-63`) and `Thread.run` captures the exception, which ends the thread (`core/thread.py:36-41`); on a relay that means exit 5.

## Probes (scratch `$TMPDIR/rh-01/`, nothing in the worktree)

1. **`probe.py` (SQLite).** An engine `before_cursor_execute` hook fires once, on the first `UPDATE task` the revert issues (after the read; pysqlite opens the write transaction only at the DML).
   - Relay-local case: the hook commits the site result and runs `store.collect_results`, which deletes the row. Primary case: the hook commits the Receiver's conditional result write on its own connection.

   | Case | develop | candidate |
   |---|---|---|
   | relay-local (row deleted) | **`StaleDataError`**. The other row is also left in flight (whole pass rolled back). | Returns; the gone row is skipped and the other row reverted. |
   | primary (result committed) | **Result wiped** (`scheduled=False completed=False`); `Task.next` dispatches it again. | `completed=True exit=0`; not re-dispatched; the other row reverted. |

   In both runs, a late result for the reverted row is `STALE`, as today.
2. **`test_probe_contended.py`** reuses `tests/test_contended_write.py`'s `contend`. Runs: PG18 plain and TimescaleDB 2.32 hypertable, with `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community|''` and the sandbox off.

   | Holder transaction | develop (PG and TSDB) | candidate |
   |---|---|---|
   | Result write | **Wipes the committed result** (`completion=None`) | Result kept |
   | `DELETE` | **`StaleDataError`** | Skipped |
   | Same-client confirm (row still qualifies) | Reverted | Reverted after the lock wait, on both |

   - On local TSDB 2.32, a contended UPDATE matched 1 on its first attempt in all six holder/predicate combinations I tried (`test_probe_first.py`). The 2.29+ 0-row miss did not reproduce here.
   - The re-run arm was therefore shown by SQLite fault injection (`Contention.lose(...)`, `test_probe_inject.py`). A lost first attempt followed by a commit on the other connection gave:

   | Committed in between | develop | candidate |
   |---|---|---|
   | Completion | Re-runs and wipes the result | Settled at DEBUG `Ignored revert … task already complete`, no WARNING |
   | Confirm to client B | Wipes B's dispatch | Left with B |
   | Delete | `StaleDataError` | Skipped |
3. **R10.** With the candidate monkeypatched in by a pytest plugin (`rhpatch.py`), the existing suites pass:
   - SQLite, 176 passed: `test_contended_recovery`, `test_writeback`, `test_writeback_statement`, `test_relay_return`, `test_relay_roster`, `test_topology_server`, `test_server_fsm`, `test_server`, plus the probe.
   - PG and TSDB, 36 passed on each: `test_contended_write` and `test_writeback_postgres`.

   This includes `test_contended_revert_lands_on_rerun`, `…_exhausted_leaves_row_in_flight` (UPDATE count == `1+CONTENDED_RERUNS`, one WARNING with `revert` and `left in flight`), `test_uncontended_confirm_and_revert_cost_one_update_per_row`, and `test_contended_orphan_revert_lands`.

## SQL shape (minimal predicate)

```sql
UPDATE task SET <revert_columns> = NULL
 WHERE id = :r_id AND client_id = :r_held_by
   AND schedule_time IS NOT NULL AND completion_time IS NULL
```

This is `select_orphaned`'s own predicate, applied per row by id. It contains no `schedule_time = :read` and no `server_id`, so it is not a dispatch token (Non-goals; that work stays with `stale-confirm-hides-orphan`).

When a row matches 0, re-read `SELECT completion_time FROM task WHERE id = :r_id AND client_id = :r_held_by AND schedule_time IS NOT NULL`:
- No row: the task is gone, reverted, or held by another client. Skip it at DEBUG.
- `completion_time` set: settled. DEBUG `task already complete`.
- `completion_time` NULL: the row still qualifies. Re-run up to `CONTENDED_RERUNS`, then the WARNING above.

The comparison runs in the database. Comparing `client_id` in Python would compare `uuid.UUID` against `str` on PostgreSQL. The statements behave the same on SQLite, PostgreSQL and TimescaleDB (probes 1–3).

## Interleavings under the candidate

| Between read and write | Outcome |
|---|---|
| Receiver commits the result | Not matched; the result is kept and not re-run. A late result for a reverted row is still STALE. |
| Row deleted (relay `collect_results`, `hs update --delete`) | Skipped; no raise; the rest of the pass commits. |
| Late confirm by the same client | Still qualifies; reverted, after a lock wait on PG/TSDB. |
| Reverted and re-dispatched/confirmed to another client | Not matched; left with that client. |
| Contended on a hypertable, still qualifies | Re-run lands (§1); WARNING at the bound. |
| Unconfirmed row (`client_id` NULL) | Never selected, as today. That is `stale-confirm-hides-orphan`, a non-goal. |

`drop_superseded` deletes only unscheduled rows, so it never meets a selected row. `collect_results` is the relay-local deleter that matters here.

## Options

- **A — recommended.** Give `__row_statements`/`__write_rows` an optional `held_by`. It adds the client and `schedule_time` conditions and implies `pending`, non-strict. The re-read carries the same binds. `revert_orphaned` calls `__write_rows(..., 'revert', 'task left in flight', held_by=client_id)`.
  - This keeps one §1 loop and the exact log texts the R10 tests pin.
  - `revert_all` stays unconditional for start-up recovery.
- **B.** A dedicated `__orphan_statements` plus its own loop, like `revert_handed_back`. This is a third copy of the §1 loop, and it must reproduce the pinned messages.
- **C.** One set-based `UPDATE … WHERE client_id = :c …`. Rejected: under §1 a set-based UPDATE cannot tell which row a TSDB 0-row miss skipped, and it breaks the pinned one-UPDATE-per-row cost test.
- **D.** `pending=True` alone, or relay-local only (the C7-1 suggestion). Rejected:
  - `pending=True` alone would wipe another client's re-dispatch.
  - Relay-local only is ruled out by the GOAL gate.

**Recommendation: A.** Probes 1–3 show it fixes both defects on all three databases while every existing suite passes unchanged.

## R9 test shape

- **`tests/test_relay_return.py`** (`site_session`/`upstream_db` fixtures, `before_cursor_execute` on the first `UPDATE task` after the orphan read, no threads):
  - (a) Relay-local: dispatch two rows, `confirm(strict=False)`, then the hook applies one result and runs `collect_results`. Assert that `revert_orphaned` returns, the other row is reverted, and the collected row is `RESULT_PENDING`.
  - (b) Primary: the hook commits the Receiver's result on its own connection. Assert the result is kept, `Task.next` does not return it, and the other row is reverted.
  - Optionally drive (a) through `HeartMonitor.check_clients` (as in `test_relay_roster.py:227`) and assert it returns `SWITCH`.
  - Both (a) and (b) fail on `2ffb5cc`.
- **`tests/test_contended_recovery.py`**: `lose(id, 1, meanwhile=…)` cases where the meanwhile change is completion, confirm to another client, or delete. Assert the outcomes above, no WARNING, and DEBUG for the settled row. All three fail on develop.
- **`tests/test_contended_write.py`** (SQLite is skipped; PG and TSDB): `contend` with a result-write holder (develop wipes the result) and a `DELETE` holder (develop raises). The existing `test_contended_orphan_revert_lands` already covers "lands after a lock wait".

## Risks and what the planner must decide

1. **Bind name.** Use `r_held_by`, not `r_client_id`. The revert's own SET binds `r_client_id` = NULL.
2. **Re-read binds.** The re-read must receive `r_held_by` too. My first patch omitted it and failed with `InvalidRequestError`.
3. **Narrow residue (accepted under Non-goals).** A row reverted, re-dispatched to the same client after it reconnected, and re-confirmed, all inside the gap, is reverted again. Nothing is lost: that result is STALE and the task re-runs.
4. **Heart monitor survival.** The heart monitor keeps running because the revert no longer raises on these rows. Any other DB error still ends the thread. Wrapping `check_clients` in a try/except would be "thread death into retry in general", a non-goal. I recommend not wrapping it.
5. **Optional INFO line.** `revert_orphaned` could return a count for an INFO summary, like `revert_staged` (`server.py:397-399`). It returns `None` today and no caller uses it.
6. **TimescaleDB 0-row miss not reproduced locally.** It did not reproduce on 2.32 here, so the hypertable re-run arm rests on SQLite injection plus CI's TimescaleDB.
7. **Same-commit docs.** Update the docstrings of `revert_orphaned`, `select_orphaned` and `__write_rows`. Name the eviction revert in invariants §1's "Pattern" line.
