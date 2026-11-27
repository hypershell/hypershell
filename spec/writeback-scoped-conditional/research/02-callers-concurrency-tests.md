# Research 02: callers, concurrency, test harness

Read-only research for `hs-plan`. Line numbers are against this branch (= `develop` `d996579` plus
the spec). "Probe" below means a throwaway script run against a temp SQLite file with the
worktree's `.venv` (SQLAlchemy 2.0.50). It is not committed.

## 1. Callers of `update_all` / `update` on `Task`

| Caller | Writes | Result writeback? |
|---|---|---|
| `server.py:361-365` `Confirm.update_info` → `ClientInfo.transpose()` (`client.py:137-140`) | `client_id`, `client_host` for each confirmed id | no |
| `server.py:459-469` `Receiver.update_tasks` | every column from the client copy | **yes: the only one** |
| `model.py:686` `__schedule_next_failed_tasks` | `retried=True`, `next_id` on the failed row | no |
| `model.py:721-744` `revert_all` (via `revert_interrupted` 751, `revert_orphaned` 793) | stamps, attribution and result columns to NULL. It does **not** clear `csvpath`, `cores_max`, `memory_max` | no |
| `model.py:757-772` `cancel_all` / `cancel` | `schedule_time=now`, `completion_time=now`, `exit_status=-1`. No caller in `src/` outside the model | no |
| `task.py:1052,1059` legacy `hs task update ID FIELD VALUE` → `Task.update`; catches `StaleDataError` as `NotFound` | any one column | no |
| `task.py:1136-1149` `hs update ... --limit` batches; `task.py:1155-1188` `query.update(...)` (no `--limit`), including `--cancel` (1098-1110, which also restamps `schedule_time=now`) and `--revert` | user fields | no |
| `server.py:596,606,634` `Client.update` | client table | n/a |

Nothing else writes a result. `client.py` only deserializes bundles (226). `submit.py` and
`cluster/*` have no `Task.update*`. `--no-db` has no Scheduler and skips writeback
(`server.py:823`, `:461`). `rotatedb` (`data/__init__.py:134-172`) and the `task.py:887` partition
`attach` are offline or read-only. If `Entity.update_all` stays unchanged, every row above
behaves as it does today (R9).

**`bulk_update_mappings` raises on a missing id.** The probe gives `StaleDataError: expected to
update 1 row(s); 0 were matched` on SQLite, and psycopg3 sets `supports_sane_multi_rowcount=True`
(`sqlalchemy/dialects/postgresql/psycopg.py:376`), so PostgreSQL raises too. Today a result for a
deleted or rotated-away row escapes `update_tasks`. The FSM logs CRITICAL and re-raises
(`core/fsm.py:57-62`), and the receiver thread stops without the session being rolled back. The new
path should treat "row not found" as a refusal and log it, not raise. The `tests/test_retry_gate.py`
`write_back` helper is a bulk update and would also raise on a missing id.

## 2. `ClientInfo` / Confirm and its ordering against Receiver

`ClientInfo` (`client.py:102-140`) carries `{client_id, client_host, task_ids}`. It has no
`schedule_time` and no per-task data. The client sends it before it runs anything
(`client.py:224-245`: UNPACK, then PUT_CONFIRM, then POP_TASK). It travels on the `confirmed`
queue, which a separate thread (`hypershell-confirm`) drains, so ordering against `completed`
(the Receiver thread) is not guaranteed.

- **Late confirm after the result (same client).** Confirm rewrites `client_id`/`client_host`
  with the same values. Harmless. In R4's relay case, a late confirm would overwrite the leaf
  attribution with the holder's id after the result applied. That happens today too, since the
  Receiver writes `client_id` from the copy, so it is not a regression. The R4 test controls the
  order (confirm, then result), so it is unaffected.
- **Stale confirm from an evicted client.** Unconditionally by id, it sets `client_id=A` on a row
  that is now reverted, or rescheduled and confirmed to B. If B is later evicted,
  `revert_orphaned(B)` misses the row (`select_orphaned` filters on `client_id`), so it stays
  in flight until restart. On a completed row the confirm only changes attribution. This is
  pre-existing.
- **Does the plan make it worse?** No. Confirm never writes `schedule_time` or `completion_time`,
  so it cannot make a legitimate result fail the fence. A result that does apply rewrites
  `client_id`, which repairs a stale confirm. Fencing Confirm needs the assignment token on the
  wire (an R9 wire change). **Recommendation: leave Confirm unchanged.** Record the stale-confirm
  and late-confirm residuals in `TECH.md` and route them to relay shaping and the
  fencing issue.

## 3. Sessions, threads, atomicity

- `Session = scoped_session(sessionmaker(bind=engine))` (`data/core.py:266-269`). It is
  thread-local, defaults to `expire_on_commit=True`, uses no custom pool or isolation, and sets no
  SQLite pragmas by default (`core.py:287-294`). That means the rollback journal and pysqlite's 5 s
  busy timeout. `check_same_thread=False`.
- DB-writing server threads, each with its own session: Scheduler (`Task.next`, retry insert and
  `retried` mark, `revert_interrupted` at start), Confirm, Receiver, and HeartMonitor (`Client.*`,
  `Task.revert_orphaned`, `server.py:624-638`). The Submitter inserts.
- **Versions.** `sqlalchemy>=2.0.50` (`pyproject.toml:34`), locked 2.0.50 (`uv.lock:1714`). The
  extras are psycopg 3 only (`>=3.1`, locked 3.3.4); psycopg2 is not a dependency.
- **rowcount.** The probe runs `Session.execute(update(Task).where(id, schedule_time==echo,
  completion_time IS NULL).values(...))` and gets a `CursorResult` with `.rowcount`. A match gives
  1. Repeating it in the same transaction gives 0, because the statement sees its own uncommitted
  write. Both SQLite (`changes()`) and PostgreSQL count *matched* rows, not changed values. Use
  one `execute` per row, not executemany, and no `RETURNING`: SQLite reports
  `supports_sane_rowcount_returning=False`.
- **`synchronize_session=False`.** The Receiver's copies are transient (from `deserialize_tasks`)
  and the session holds no live `Task` it relies on. The probe shows the default `'auto'` also
  works but costs an evaluate pass. Do the classification re-read as a **column select**
  (`select(Task.schedule_time, Task.completion_time, Task.exit_status).where(Task.id==id)`), not
  `Task.from_id`. An identity-mapped instance loaded earlier in the same transaction would not be
  refreshed by a plain query.
- **PostgreSQL READ COMMITTED.** An `UPDATE` that waits on a row a concurrent transaction is
  updating re-checks its `WHERE` against the newly committed version once that transaction commits
  (the PG docs, "Read Committed Isolation Level"). So the conditional UPDATE is atomic against a
  concurrent `revert_all`. If the revert commits first, the match fails, rowcount is 0, and the
  re-read (a new statement snapshot) sees the revert and classifies the result as stale. On SQLite
  the file write lock serializes writers.
- **The residual this fix does not close (R9 keeps revert as it is).** `revert_orphaned` runs
  `select_orphaned` and then an unconditional `revert_all` by id, in separate statements. A
  legitimate result committed between the two is wiped and the row runs again. pysqlite opens its
  transaction only at the DML, so the window is real. This is pre-existing and should be noted in
  `TECH.md`.
- **SQLite lock profile.** Today a bundle is one executemany inside one transaction. Per-row
  `UPDATE`s plus a re-read only on refusal, committed once per bundle, hold the RESERVED lock for
  about the same time. The default bundlesize is 1 (`core/config.py:124`). Roll back on exception
  so that one bad row cannot leave the thread's session poisoned.
- **The fence value round-trip.** On SQLite, `DateTime(timezone=True)` stores wall time without an
  offset. `Task.next` commits, which expires the instance, and `pack_bundle` reloads it, so the
  bundle carries a **naive** `schedule_time` (seen in the probe). An echoed naive value, or an aware
  value with the same offset, matches. *The same instant at a different offset does not match*
  (probe: rowcount 0). Clients never touch `schedule_time` (`client.py:721-808`), and 2.8 clients
  echo it verbatim, so R9 holds. Two consequences follow. A relay must return the upstream
  `schedule_time` unchanged, which is a constraint on relay's "stamps don't leak". And a copy with
  `schedule_time=None` never matches (SQL NULL), so it is refused.
- **For R3, SET all 13 result columns from the copy, NULLs included**, as today. `revert_all`
  leaves `csvpath`, `cores_max` and `memory_max` behind, and today's writeback overwrites them.
  Skipping None values would change outcomes.

## 4. Partitions and rotation

`rotatedb` (SQLite only, from `hs initdb --rotate`) stamps `part` on rows with `exit_status IS NOT
NULL`, uses `VACUUM INTO` to write a new file, then deletes those rows from main. In-flight rows
(`exit_status` NULL) never leave main. The server never attaches partitions (`task.py:887` is the
read-only search view). A conditional UPDATE by id therefore cannot miss an in-flight row. A late
result for a rotated row finds nothing: today that raises `StaleDataError` (section 1). After the
fix it should be refused and logged as "not found", which the plan can treat as stale.

## 5. Test harness and red-first sketches

**Precedent.** `tests/test_retry_gate.py:35-47` has a `task_db` fixture. It runs `create_all` on a
per-test SQLite file and rebinds the global `Session` (`Session.remove(); Session.configure(bind=…)`),
then restores the binding afterwards. `make_scheduler` constructs `Scheduler(queue=None, …)`
(`:91-96`). Logging is checked with `caplog.set_level(logging.DEBUG, logger='hypershell')` and
`record.getMessage()` (`:124-127`). The `hypershell` logger propagates and has only a
`NullHandler` (`core/logging.py:620-621`), so caplog sees WARNINGs. No test constructs `Receiver`,
`Confirm` or `HeartMonitor` today. All three take `queue=None` and do not touch the queue in
`update_tasks`, `update_info` or `check_clients`, so direct driving works. Put the new tests in a
new unit file, for example `tests/test_writeback.py`, and reuse `task_db`. Moving it into
`conftest.py` would be scope creep; copy it instead.

Helpers: `dispatch()` = `Task.next(...)` plus `deserialize_tasks(serialize_tasks(tasks))` (the
client's copies). `confirm(cid, ids)` = `Confirm(queue=None)` with `.client_info=ClientInfo(...)`
and `.update_info()`, or `Task.update_all(info.transpose())`. `receive(copies)` =
`r=Receiver(queue=None); r.tasks=copies; r.update_tasks()`. `row(id)` =
`Task.from_id(id, caching=False)`. Eviction: call `Task.revert_orphaned(A)` directly. To drive
`HeartMonitor(queue=None, evict_after=10).check_clients()` instead, first `Client.add(...)` the
client (else `Client.update` raises `StaleDataError`), then seed `beats[A]` with a heartbeat older
than 10 s.

Probe results on today's code (all red):
- **(a) R6.** Submit 1 task with attempts=3. Dispatch it, confirm to A, `revert_orphaned(A)`.
  Dispatch it again, confirm to B, B returns `exit_status=1`. The next dispatch creates the retry,
  and X has `retried=True` and `next_id` set. Then A's copy returns failed. Today X has
  `retried=False`, `next_id=None` and A's `schedule_time`, and the next `Task.next` creates a
  **second retry: 2 rows with `previous_id=X`**. Assert X's chain fields are unchanged, the count
  is 1, and a stale WARNING names X and A.
- **(b) R4.** Dispatch, confirm to A, return a copy with `client_id/host=C` and a different
  `server_id`/`server_host`, keeping the echoed `schedule_time`. Assert the result columns and C's
  attribution applied and the server stamps are unchanged. Red today only because the stamps get
  overwritten. Keep the stamp mutation in the test, or it passes on `develop`.
- **(c) R1.** As (b), but also mutate `attempt, retried, group, args, tag, previous_id, next_id,
  part`, the submit fields, `cores/memory/timeout`, `source` and `fingerprint`. Compare
  `to_dict()` before and after. Today 17 columns change, including `args, attempt, group, next_id,
  part, previous_id, retried, server_host, server_id, tag`. After the fix only the 13 result
  columns may differ.
- **(d) R5.** One bundle `[copy, copy]` (or the eager double, section 6), plus a re-delivery of
  the same bundle. Today both copies apply and log "Completed task" twice. Assert one completion log,
  a duplicate log line distinct from the stale one, and the row unchanged after the first.
- **(e) R7.** Dispatch, confirm, `Task.cancel(id)`, return `exit_status=0`. Today `exit_status`
  becomes 0. Assert it stays -1 and `completion_time` is unchanged.
- **(f) R2 log and `--failures`.** Use `Receiver(queue=None, redirect_failures=io.StringIO())`.
  Assert a refused failed result writes nothing to the stream and emits no "Completed task" or
  "Non-zero exit status" record, and that exactly one WARNING contains the task id and client id.
  Today no warning is emitted.
- **R8 / R3 regression.** The happy path (dispatch, confirm, return) must match today
  column-for-column. Snapshot the row after today's `update_all` for the same copy. A no-confirm
  variant skips the confirm step and must still apply (client_id NULL in the DB).

**CLI drive (integration).** These shell out through `tests.main([...])` with `temp_site` and
need the CLI installed (`uv sync`). `tests/test_cluster.py:139-148`
(`hs cluster FILE --no-db --no-confirm`, which counts `[hypershell.server] Completed task` ×4) is
the in-memory regression net already in place. Add a DB-backed no-confirm sibling:
`main(['hs','cluster',taskfile,'--no-confirm'])`. Assert rc 0, four `Completed task` lines, no
stale or duplicate warnings, `hs list --count` = 4, every `exit_status` 0, and `client_id`
non-null (now written only by the result). `hs server --no-confirm` exists (`server.py:1337-1338`)
but no test drives it on its own. `hsx` is the same app as `hs cluster` (`pyproject.toml:107`).

## 6. Can `Task.next` list a task twice in one bundle?

Yes, in **eager** mode only. `__next_eager` (`model.py:650-657`) creates retry rows (with
`schedule_time` NULL) and then tops up with `select_new`, which selects them again. The probe gives
2 entries and 1 distinct id. They are the **same ORM instance** (from the identity map), so both
serialized copies are byte-identical: same id and same `schedule_time`. Non-eager mode runs
`select_new` before creating retries, so it cannot duplicate. The client confirms the id twice
(harmless) and may return two results, possibly in one bundle. The fence handles them: the first
applies, and the second matches zero rows (its own write in the same transaction, or a committed
row in a later bundle). On re-read, `schedule_time` is equal and `completion_time` is set, so it is
classified as a duplicate (R5). Consider de-duplicating by id within one bundle, so that a stale
double logs only once.
