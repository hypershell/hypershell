# Research 02 — R2: the dispatch stamp off the ORM flush

Lines are `develop` `2ffb5cc` (src/tests identical to this worktree's head). Probes are in the research
agent's scratch, not the repo; the candidate was monkeypatched onto `Task.next`, never edited in.

## The code today

- `Task.next` (`data/model.py:793-810`) selects through `__next_eager` (`:813`) or `__next_not_eager`
  (`:823`), sets `server_id`, `server_host`, `schedule_time` on each ORM object (`:805-808`, one
  `datetime.now()` per row) and commits once (`:809`). The flush is **one executemany UPDATE by PK**
  (probe: 100 rows → 1 `UPDATE` with `executemany=True`); SQLAlchemy checks the summed rowcount
  (`supports_sane_multi_rowcount` is True on SQLite and psycopg), so one missing row raises
  `StaleDataError` for the whole chunk and nothing is committed.
- `select_new` (`:753`) defines "unscheduled" as `schedule_time IS NULL` only (§1). A cancel
  (`cancel_all`, `:1110`) stamps `schedule_time` **and** `completion_time`, so a cancelled row is never
  `schedule_time IS NULL`.
- Retry path (`__schedule_next_failed_tasks`, `:835`): `add_all` and `update_all` each **commit**, which
  expires every object in the session. In the not-eager order (new first, then failed) the `select_new`
  objects are expired before the stamp; reading `task.id` on one whose row was deleted raises
  `ObjectDeletedError` (probe). Use `sqlalchemy.inspect(task).identity[0]` (no load) for ids.
- Sole caller: `Scheduler.next_tasks` (`server.py:292-307`), once per zone refill
  `(zone, size)` from `Dispatcher.refills()` (`core/dispatch.py:362`). "Bundle" here is that per-zone
  chunk of up to `size` rows; `pack_bundle` (`server.py:354`) stages `(task.id, json.dumps(task.to_json()))`
  and the dispatcher cuts client bundles from the pool. Nothing assumes `len == limit`: a short chunk is
  already normal (fewer rows available), `if tasks:` drops an empty zone, and an empty `self.loaded`
  falls through `load_bundle`'s existing no-work branch (`:259-289`: stop only when
  `count_remaining() == 0`, else group check and back-off). The only `len(tasks) < limit` checks
  (`:817`, `:829`) run before the stamp.
- Who reads the stamped values: `to_json()` in `pack_bundle`. Because the commit expires the objects,
  today `to_json()` **reloads each row from the database** (probe: 100 `SELECT`s after `next(100)`),
  so the wire `schedule_time` is the stored value (naive on SQLite, matched later by
  `apply_results`/`revert_handed_back`). Keep that: write the stamp with Core, set nothing on the ORM
  objects, commit, and let them reload. Setting attributes (or `set_committed_value`) would either be
  flushed again or change the wire text on SQLite (tz-aware vs naive) — avoid.
- §3/§4: Scheduler stays the sole caller; the retry insert and `retried` mark are untouched (non-goal).
  `in_memory` has no Scheduler. Relay-local server and `hs cluster` local run the same Scheduler
  (`relay/site.py:130`, `max_retries=0`). Other `Task.next` users are tests only.

## Probes

SQLite, forced by `before_cursor_execute` deleting row 2 of 3 on the first `UPDATE task` (no threads):

```
DEVELOP  : StaleDataError("UPDATE statement on table 'task' expected to update 3 row(s); 2 were matched.")
           rows stamped after failure: 0
CANDIDATE: returned [0, 2]; wire schedule_time == stored (2026-11-13 21:13:49.054471)
```

SQLite, `tests/test_contended_recovery.py`'s `Contention` harness on the candidate: lose 1 →
`['UPDATE','SELECT','UPDATE']`, lands; lose 4 → one WARNING `Contended dispatch write did not land for
task (…) after 4 attempts - task left unscheduled`, row stays NULL and the **next call picks it up**;
lose + `cancelled`/`deleted` meanwhile → `['UPDATE','SELECT']`, skipped, no warning. On develop the
harness sees **no** statements (`[]`): the flush bypasses `Session.execute`.

PostgreSQL 18 and TimescaleDB (local), `contend()` from `tests/test_contended_write.py`, holder keeps
the row lock, `Task.next(limit=1)` in the worker:

| holder | develop (PG and TSDB) | candidate (PG and TSDB) |
|---|---|---|
| `UPDATE client_host` (row still qualifies) | lands | lands |
| `DELETE` the row | **StaleDataError** | skipped, returns `[]` |
| cancel (`schedule_time`, `completion_time`, `exit_status=-1`) | **re-stamps the cancelled row, stamps `server_id`, returns it for dispatch** | skipped, cancel kept, `server_id` NULL |

**Local TimescaleDB is 2.32.0, and it does not show the 0-row match**: a plain contended
`UPDATE … WHERE id` matched 1 in 4/4 two-session cases. The 0-row behaviour is recorded for 2.29-2.31
(`spec/hypertable-update-retry/EVIDENCE.md:127-129`); CI pins **2.31.0** (`.github/workflows/tests.yml:76,80`).
So develop's "flush fails on TSDB when merely contended" cannot be reproduced on this laptop; only CI's
timescale jobs (or a 2.31 container elsewhere) show it. The delete and cancel holders fail on develop on
every server database, including 2.32.

Baselines: `test_contended_write.py` 12 passed on TSDB and PG (needs `.venv/bin` on `PATH` for `hs
initdb`); `test_contended_recovery.py` 20 passed.

## Options

1. **Per-row conditional Core UPDATE** `SET server_id, server_host, schedule_time WHERE id = :id AND
   schedule_time IS NULL`, rowcount check, §1 re-run loop, re-read `SELECT schedule_time WHERE id`:
   gone → skip (DEBUG), not NULL → skip (DEBUG), NULL → re-run up to `CONTENDED_RERUNS`, then WARNING
   and leave unscheduled. Same shape as `confirm_all`/`revert_staged`; per-row 0 detection is exact.
2. **executemany** of the same statement: one rowcount total, no per-row 0 → cannot meet §1. Rejected.
3. **`UPDATE … WHERE id IN (…) AND schedule_time IS NULL RETURNING id`** (SQLite 3.53 and psycopg both
   support it): one statement uncontended, then classify the missing ids and re-run per row. Fewer
   round trips, but a second code shape for §1, no fit with the `Contention` harness (keys on one
   per-row param), and nothing else in `model.py` does it. Not worth it for this cycle.

Condition: exactly `schedule_time IS NULL`, as `select_new`. Adding `completion_time IS NULL` would
make a hand-edited `schedule_time NULL, completion_time set` row selected every round and never
stamped (a spin); a cancel already sets `schedule_time`.

Cost (2000 rows, chunks of 100, stamp + `to_json`): SQLite develop 361 ms / candidate 368 ms; PG
1249 / 1396 ms; TSDB 1201 / 1374 ms (about +0.07 ms per row). Optional: one
`Session.query(Task).filter(Task.id.in_(landed)).all()` after the commit reloads the chunk in one
statement instead of 100 lazy loads: PG 584 ms, TSDB 638 ms (half of develop). The values still come
from the database, so the wire text is unchanged.

## Recommendation

Option 1, reusing `__write_rows`/`__row_statements` (`:916`, `:937`) generalised with an
"unscheduled" condition (UPDATE matches `schedule_time IS NULL`; the SELECT reads `schedule_time`; a
gone row is skipped, not `StaleDataError`; the skip message says "already scheduled", not "already
complete"). `Task.next` takes the ids with `inspect(task).identity[0]`, calls
`__write_rows(ids, {server_id, server_host, schedule_time: now}, 'dispatch', 'task left unscheduled', …)`
and returns `[task for task, id in zip(tasks, ids) if id in landed]`, order kept.

**Binding constraint found by running the suite with the candidate patched in:** if the stamp's id
bind is named `r_id` (as every `__write_rows` statement is), two existing tests fail:
`test_contended_recovery.py::test_contended_confirm_exhausted_keeps_running` and
`::test_contended_interrupted_revert_exhausted_returns`. They `dispatch_all` and then count
`contention.kinds(id).count('UPDATE')` without `clear()`, and the harness keys on `params['r_id']`,
so the stamp's UPDATE is counted. With the bind named `d_id`, every dispatch-related file passes on
SQLite (`test_contended_recovery`, `group_order`, `relay_return`, `retry_gate`, `server_fsm`,
`writeback*`, `zone_model`, `zone_dispatch`, `restart`) and `test_contended_write`,
`test_writeback_postgres` and `test_zone_postgres` pass on PG and TSDB (41/41 each). The whole SQLite
suite with the `d_id` candidate passed too: 2055 passed, 95 skipped. So
`__row_statements` needs a bind-prefix (or key) parameter, and R10 rules out renaming the existing
binds or editing those tests.

## R9 test shape

- **Gone row (unit, SQLite):** in `tests/test_relay_return.py` style, 3 unscheduled rows (inserted via
  `store.insert_received` for the relay case, or `Task.add_all` for a primary). A
  `before_cursor_execute` hook deletes row 2 on the first `UPDATE task`. Assert `Task.next(limit=3)`
  returns rows 1 and 3, both stamped, no exception. Fails on develop with `StaleDataError`. Add the
  **cancelled** variant (hook cancels row 2): develop dispatches it; head skips it and keeps the cancel.
- **Contended, SQLite:** `tests/test_contended_write.py` is PG-only (`needs_server`; skipped without
  `HS_TEST_POSTGRES`), and SQLite has no row locks, so the SQLite leg is the fault-injection harness in
  `tests/test_contended_recovery.py`. It needs a new-test-only extension of `Contention` (or a
  subclass) that also keys on the stamp's bind (`d_id`): lose 1 → lands with one DEBUG re-run;
  lose `ALWAYS` → `['UPDATE','SELECT'] * attempts()`, one WARNING naming the task and "dispatch",
  row unscheduled and taken by the next `Task.next`; lose + `cancelled`/`deleted` meanwhile → skipped,
  no warning; uncontended → one UPDATE per row. These fail on develop: the flush never reaches
  `Session.execute`.
- **Contended, PG/TSDB** (`tests/test_contended_write.py`): `contend()` with holder `UPDATE client_host`
  → stamp lands, `Task.next` returns the row (fails on develop only on TSDB 2.29-2.31, i.e. in CI's
  timescale jobs; passes on develop on PG and local 2.32). Add holder `DELETE` → skipped, no error, and
  holder cancel → skipped, cancel kept. Those fail on develop on every server database.

## Risks and decisions for the planner

1. Evidence for "fails on `2ffb5cc`" in the merely-contended TSDB case comes **only from CI**
   (TSDB 2.31.0). The laptop has 2.32.0 with no 0-row match, and no Docker. Say so in PLAN/EVIDENCE.
2. GOAL R9 says the contended case runs in `test_contended_write.py` "on SQLite"; that file can't run
   on SQLite. Record that the SQLite leg is `test_contended_recovery.py` (its docstring already pairs
   the two files).
3. One `now` per call (`__write_rows` writes the same values onto each row) instead of develop's
   one per row: every row in a chunk gets the same `schedule_time`. Nothing found that needs distinct
   stamps (`apply_results` and `revert_handed_back` match on id + stamp; orderings only sort). Accept
   it, or pass a value per row.
4. Log levels: skip of a gone or already-scheduled row at DEBUG (as for confirm); the bound at WARNING.
   An all-skipped round reaches `load_bundle`'s back-off. A skipped row costs at most one poll (the wait
   ends early on new demand).
5. Optional reload of the chunk in one query (option above): a perf win, not required. If taken, a
   stamped row deleted before the reload leaves its object expired and `to_json` raises
   `ObjectDeletedError`, as develop already would. No relay deleter removes a stamped,
   incomplete row.
6. Develop's cancel overwrite (a cancelled row re-stamped and dispatched) is fixed as a side effect.
   It is in R2's "no longer unscheduled" and does not touch `cancel_all` (non-goal).
