# Research 02 — Audit of every `task` UPDATE path (R4)

Read-only audit at the worktree HEAD. SQLAlchemy 2.0.50 + psycopg 3 (`supports_sane_multi_rowcount = True`),
so `bulk_update_mappings` and ORM flushes assert the summed rowcount of an executemany: **one**
contended row in a batch raises `StaleDataError` and `_bulk_save_mappings` rolls the **whole batch**
back (all of a Confirm bundle's stamps, all ≤100 rows of a revert pass). `Entity.update_all` has no
`rollback()` of its own, so the scoped session is left needing one. Isolation is the driver default
(READ COMMITTED); `Session` is `scoped_session` (per thread), `data/core.py` L268–270.

**How a dead thread surfaces** (`core/thread.py`): `Thread.run` stores the exception; it re-raises only
on `join()`. `ServerThread` joins submitter → scheduler → heartbeat → receiver → confirm (server.py
~L920–927). So: **Scheduler** death raises at once (server exits, run aborted); **Confirm** death is
silent until the end (`confirmed` queue is unbounded, so later bundles are never attributed, which
blinds `select_orphaned` for them; exit 5 at shutdown); **HeartMonitor** death stops eviction and
orphan recovery, so a run with an orphan never reaches `count_remaining()==0` and hangs;
**Receiver** death stops all writeback, `completed` (bounded) fills, clients block — hang.

## Path table

| # | Path (file:line) | Form | WHERE ("still qualifies") | Thread | Contenders (in-server / cross-process) | Contended 0-row today |
|---|---|---|---|---|---|---|
| 1 | `Confirm.update_info` server.py:422–425 → `Task.update_all` model.py:196 | bulk_update_mappings (executemany, all bundle ids) | `id` only → row exists | Confirm | **Receiver** (result for same bundle, fast tasks; Receiver holds every row of its bundle until one commit); **Scheduler** stamp/retried mark (late Confirm on a row eviction reverted / a failed row); HM revert (needs a 2nd confirm of an already-attributed row — rare) / `hs task update` | StaleDataError, whole bundle unstamped, Confirm dies (CI failure) |
| 2 | `Task.apply_results`/`__apply_result` model.py:820–882 | core `update(table)` per result, one txn per bundle | `id`, `schedule_time == S`, `completion_time IS NULL` | Receiver | **Confirm** (bundle stamp); **HM revert_all** of same row; `hs task update` on in-flight rows. Scheduler cannot: it only locks unscheduled rows (fail `schedule_time == S` at scan) or completed ones | silent: classified STALE, result dropped, row stranded |
| 3 | `Task.next` stamp model.py:682–686 (`server_id`,`server_host`,`schedule_time` + `Session.commit()`) | ORM flush (executemany by PK) | `id` only | Scheduler | **in-server: late Confirm** stamping a row eviction just reverted (Confirm's UPDATE is unconditional, so it locks an unscheduled row); cross: `hs task update --cancel/--tag/...` on remaining rows during a run | StaleDataError → **Scheduler dies → server exits mid-run** |
| 4 | `__schedule_next_failed_tasks` retried mark model.py:726 | bulk_update_mappings | `id` only | Scheduler | **in-server: late Confirm** on a completed failed row (fast failures, eager); HM `revert_all` of a row selected in-flight then completed (orphan-revert window); cross: `hs task update --revert -F` | StaleDataError → Scheduler dies; retry rows already committed by `add_all` (L725) but old row unmarked → doubled retry on restart |
| 5 | `Task.revert_all` model.py:761–784 via `revert_orphaned` L898–901 | bulk_update_mappings, ≤100 rows | `id` only (selection's in-flight test is **not** re-checked) | HeartMonitor | **Receiver** (result write on the same in-flight row — the real one); Confirm only via a second confirmation of a row already attributed to the evicted client | StaleDataError, whole batch rolled back, **HM dies** → orphans stay in flight → hang |
| 6 | `revert_all` via `revert_interrupted` L791–794 | same | `id` only | Scheduler (`Scheduler.start`, server.py:205) | in-server none: Confirm/HM/Receiver threads are started, but nothing is dispatched yet and old clients have no reconnect path; cross: `hs task update` at startup | Scheduler dies at start |
| 7 | `Task.revert` L786, `Task.cancel_all`/`cancel` L797–817 | bulk_update_mappings | `id` only | **no in-tree caller** (tests/Python API only) | cross-process only | StaleDataError to caller |
| 8 | `hs task update` legacy, task.py:1045–1061 | `Task.update` → update_all | `id` | CLI process | server Scheduler/Confirm/Receiver/HM holding the row | StaleDataError → re-raised as **`Task.NotFound`** (false "not found") |
| 9 | `hs task update --limit` batches, task.py:1136–1146 | update_all, `UPDATE_BATCH_SIZE` | `id` | CLI | same | StaleDataError, earlier batches already committed (partial), traceback exit |
| 10 | `hs task update` set/`--cancel`/`--revert`/tags, task.py:1154–1190 | `Query.update` (filtered bulk UPDATE; per-key on PG for tags) | the user's filter | CLI | same; also the **holder** of many rows at once, which makes the server paths above the victims | **silent**: contended rows skipped, still logs "Updated {count}" (count taken earlier). On PG the filter is re-checked |
| 11 | `rotatedb` `.update({part})` data/__init__.py:169–174 | Query.update | `exit_status IS NOT NULL` | CLI | — | SQLite only (raises otherwise) → unaffected |
| 12 | `Client.update` server.py:668/678/706 | update_all on `client` | `id` | HeartMonitor | none: HM is the only writer of `client` rows; `client` never a hypertable | n/a |

No UPDATE of `task` rows in `submit.py` (INSERT only), `data/upgrade.py` (DDL + advisory lock),
`data/timescale.py` (DDL / columnstore policy; background compression takes chunk locks, not row
locks — a waiting DML there scans after the holder, so not this bug), `vacuumdb`, `client.py`,
`cluster/*`. `hs task update` `--revert`/`--cancel` do **not** go through `Task.revert_all`/`cancel_all`:
they inline the same column lists (task.py:1098–1124), so they are **not** fixed for free. DELETE
paths (`--delete` → `query.delete()` / `Task.delete_all`) are outside R4's "UPDATE" but upstream
#9315 moved DELETE too; unprobed.

## Findings beyond the GOAL's list

1. **Scheduler has an in-server contender** (rows 3, 4): a late Confirm. GOAL frames Task.next as
   cross-process only. Probability is low (needs eviction + a late confirm, or a confirm lagging a fast
   failure), but the consequence is the worst of all: immediate server exit.
2. **Revert loops can spin forever once revert stops raising.** `revert_orphaned` and
   `revert_interrupted` are `while tasks := select_…(100): revert_all(...)`. If the new helper gives up
   on a row at its bound (R5) and the row is still in flight, the next select returns it again —
   unbounded loop. The helper's callers must exclude given-up ids or stop on a no-progress pass.
3. **R6 vs revert's condition.** Today revert's only condition is `id`, so a re-run after a Receiver
   commit lands and wipes the result — exactly PG behaviour (`orphan-revert-loses-result`). Making the
   retry re-check "still in flight" would change uncontended PG behaviour too; keep `id`/exists to
   honour R6 and leave the conditional revert to that seed.
4. **Deadlock (pre-existing, all PG providers).** Confirm locks its bundle in bundle order; the
   Receiver locks its result bundle in completion order and holds all until one commit. Interleaved,
   they can deadlock → `OperationalError` → one thread dies. Not reproduced. A per-row commit (or
   sorted id order on both sides) in the new helper removes it; otherwise belongs to
   `coordinator-resilience-retry-backoff`.
5. **Deleted row + Confirm** dies on every provider today (StaleDataError), uncontended. A helper that
   treats "row gone" as no-op changes that — a benign R6 deviation the plan should name.
6. **Receiver classify can stay one statement:** replace `completed` with a select of `completion_time`
   under `same_dispatch`: no row → STALE, NULL → still qualifies (re-run), set → DUPLICATE. Same
   statement count as today on the uncontended STALE path.

## `Entity.update_all` — change it or add a Task-level helper?

Add a Task-level helper; do not change `update_all`. Dependents on its raising: `update_legacy` maps
StaleDataError to `Task.NotFound` (a missing id would silently exit 0 — untested, so nothing would
catch it); the revert loops rely on the raise to avoid spinning (finding 2); `Client.update` and
`tests/test_writeback_postgres.py:182` share it. A non-raising `update_all` would also hide every
cross-process contention behind a silent no-op.

## Existing seeds that overlap

`stale-confirm-hides-orphan` (Confirm vs eviction ordering: rows 1/3/5), `late-confirm-overwrites-attribution`
(Confirm on completed rows: rows 1/4), `orphan-revert-loses-result` (rows 4/5, finding 3),
`late-result-resets-retried` (adopted; closed by the conditional write), `evicted-client-fencing-and-cancel-reach`
(cancel semantics of row 10), `coordinator-resilience-retry-backoff` (thread death → retry; finding 4;
its R3 already names `hs task update --revert`). None covers the hypertable 0-row hazard for the
Scheduler or `hs task update`, so a new seed is needed; it should cross-reference these.

## Recommendation (TECH.md verdicts)

- **Confirm.update_info** — (a) in scope: Task-level per-row helper, condition = row exists.
- **Receiver `__apply_result`** — (a) in scope; also covers cross-process contenders for free.
- **revert_orphaned → revert_all** — (a) in scope; condition stays `id`; fix finding 2 in the loop.
- **revert_interrupted, Task.revert** — (b) free via `revert_all`; no in-server contender; loop guard as above.
- **retried mark (model.py:726)** — (b) recommend: same mapping-by-id form as Confirm, reuse the helper;
  needs one more R7 case on the same harness (lock the failed row). If declined → (c) with row 3.
- **Task.next stamp** — (c) seed: ORM flush must become a per-row core UPDATE with rowcount check;
  the seed must decide whether "qualifies" is exists (PG parity: re-stamps a cancelled row) or
  `schedule_time IS NULL`. In-server contender is a late Confirm; cross-process is `hs task update`.
- **Task.cancel_all / cancel** — (c) fold into the CLI seed; no in-tree caller.
- **`hs task update` legacy, `--limit` batches, Query.update set/cancel/revert/tags** — (c) seed:
  needs rowcount-vs-target reconciliation and a re-run that re-evaluates the user's filter; legacy
  must stop reporting contention as NotFound. DELETE paths noted there, unprobed.
- **rotatedb, vacuumdb, upgradedb, timescale DDL/compression, submit** — (d) no task UPDATE / SQLite-only.
- **`client` writes in HeartMonitor** — (d) plain table, single writer thread.
