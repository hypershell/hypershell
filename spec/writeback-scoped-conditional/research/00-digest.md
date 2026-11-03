# Research digest — writeback-scoped-conditional

Two briefs, read against `develop` `d996579`:
[`01-assignment-token.md`](01-assignment-token.md) (what identifies an assignment, and whether it
survives the wire and the database) and
[`02-callers-concurrency-tests.md`](02-callers-concurrency-tests.md) (other writers, threads,
atomicity, test harness). They agree on every point. The decisions below are what `PLAN.md` builds on.

## Decisions

1. **The assignment token is the row's `schedule_time`.** `Task.next` stamps a fresh
   `schedule_time` on every dispatch, revert sets it NULL, and cancel restamps it with `completion_time`.
   The client echoes it untouched; 2.8.3 and current clients set exactly the 13 result columns and
   nothing else. No other echoed field is per-dispatch (`server_id` is constant for a server run,
   and R4 rules out `client_id`). A new token field is ruled out because `Task.from_json` is
   `cls(**data)` and an older peer rejects an unknown key (R9). **No schema change, no wire change,
   so #69's upgrade step is not involved.** (01 §1–§5)
2. **The fence is one atomic conditional UPDATE per returned task:**
   `UPDATE task SET <13 result columns> WHERE id = :id AND schedule_time = :echo AND completion_time
   IS NULL`, through `Session.execute(update(Task)…)` with `synchronize_session=False`, reading
   `.rowcount` (matched rows on SQLite and psycopg 3). One commit per bundle, rollback on error.
   PostgreSQL READ COMMITTED re-checks the WHERE after a concurrent revert commits; SQLite
   serializes writers on the file lock. A read-then-write in Python is rejected: it races the
   HeartMonitor's revert and the Scheduler's reschedule. (02 §3)
3. **Equality holds on SQLite, proven by probe:** the echo matches (1); a 1 µs shift, a revert, a
   revert plus re-dispatch, a replay of a completed row and a cancel all give 0. The bundle is
   serialized after a reload (`expire_on_commit=True`), so on SQLite it carries naive wall-clock
   time. The same instant re-expressed at a different offset does **not** match: a relay must echo
   the upstream value verbatim. PostgreSQL `timestamptz` compares instants exactly (reasoned, not
   run). (01 §3)
4. **An echoed `schedule_time` of `None` is refused without a query.** In SQLAlchemy, `column ==
   None` renders `IS NULL`, which would match a reverted, unscheduled row. (Planner's addition;
   brief 02 assumed SQL NULL semantics.)
5. **Refusal classification is a second SQL predicate, not a Python comparison:** after rowcount 0,
   `SELECT 1 WHERE id = :id AND schedule_time = :echo AND completion_time IS NOT NULL AND
   exit_status != CANCEL_STATUS` → duplicate (R5); anything else, including a missing row, is
   stale (R2). That avoids naive-vs-aware comparisons in Python and the identity map's stale
   instances. (02 §3)
6. **Set all 13 result columns from the copy, NULLs included** (R3): `revert_all` leaves
   `csvpath`, `cores_max`, `memory_max` behind and today's writeback overwrites them. (02 §3)
7. **`Entity.update_all` stays unchanged;** Confirm, retry marking, revert, cancel and `hs task
   update` keep today's behaviour (R9). Confirm cannot be fenced without a wire change, never
   writes `schedule_time`/`completion_time`, and so cannot make a legitimate result fail. (02 §1–§2)
8. **Duplicates inside one bundle need no special case:** the second identical copy matches 0 rows
   (it sees its own uncommitted write) and classifies as duplicate. Eager `Task.next` really does
   list a retry twice (02 §6).
9. **Missing rows stop crashing the receiver.** Today `bulk_update_mappings` raises
   `StaleDataError` on an unknown id (a rotated or deleted row) and the receiver thread dies. The
   fence refuses and logs it as stale. (02 §1, §4)

## Residuals recorded, not fixed (out of scope)

- A stale confirm from an evicted client can rewrite `client_id` on a rescheduled row, hiding it
  from `revert_orphaned`; a late confirm can overwrite a relay's leaf attribution. Pre-existing;
  fencing Confirm needs the token on the wire. Route to relay shaping and
  `issues/evicted-client-fencing-and-cancel-reach.md`.
- `revert_orphaned` selects, then reverts by id unconditionally: a legitimate result committed in
  between is wiped and the task runs again. Pre-existing; R9 keeps revert as it is.
- `from_json_type` turns date-like strings (e.g. an `args` of `20261102`) into datetimes. R1 stops
  the writeback rewriting `args`, but the client still builds `command` from the converted value.
  Worth its own seed. (01 §6)
