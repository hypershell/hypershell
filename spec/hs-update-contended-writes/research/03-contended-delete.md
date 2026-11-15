# Research 03 — does a contended DELETE miss? (2026-11-15)

Probes: `probes/03_contended_delete.py`, `probes/03_cli_delete_blocked.py`, `probes/03_xmin.py`
(local PostgreSQL 18.7, TimescaleDB 2.32.0; every probe database created and dropped by the probe).

## 1. Upstream

- **#9315** "Speed up `DML` operations on hypertables by using the optimized TimescaleDB hypertable
  expansion code instead of the generic PostgreSQL inheritance hierarchy expansion" — UPDATE and
  DELETE both (MERGE excluded), merged 2026-07-17, first shipped in **2.29.0 (2026-07-28)**, GUC
  `timescaledb.enable_hypertable_expansion_for_dml` (default on).
  <https://github.com/timescale/timescaledb/pull/9315>, CHANGELOG 2.29.0.
- **The 0-row bug is upstream issue #10902**, "Concurrent UPDATE/DELETE on hypertable silently
  affects 0 rows under READ COMMITTED (regression in 2.28.1)". It says outright: "The same happens
  with a waiting DELETE (DELETE 0, row remains)." Reported bad: 2.28.1, 2.28.2, 2.28.3, 2.29.0,
  2.30.2, 2.31.0; 2.28.0 good. Turning `enable_hypertable_expansion_for_dml` (and other
  optimizer GUCs) off did not help on 2.31.0, so #9315 is not the cause; workaround
  `SELECT … FOR UPDATE` first. <https://github.com/timescale/timescaledb/issues/10902>
- **Fix: PR #10906** "Recheck concurrent row updates on hypertables", merged 2026-10-29: "Because
  the ModifyTable child plan is created late, Postgres set up EvalPlanQual with the dummy Result
  placeholder", so the recheck never returned a row; it covers UPDATE and DELETE. Shipped in
  **2.32.0 (2026-11-11)**: "#10902 Fix concurrent `UPDATE`/`DELETE` on hypertables silently skipping
  rows under `READ COMMITTED`". Backported to 2.31.x (#10917, milestone v2.31.1), but the CHANGELOG
  has no 2.31.1 release yet, so the CI pin `2.31.0` still has the bug.
  <https://github.com/timescale/timescaledb/pull/10906>,
  <https://raw.githubusercontent.com/timescale/timescaledb/main/CHANGELOG.md>
- Aside for AGENTS.md wording (not this cycle's to change unless the plan wants to): upstream puts the
  first bad release at 2.28.1, not 2.29. CI's floor 2.24 is unaffected either way.

## 2. Local probe (`03_contended_delete.py`)

The holder updates X (`client_host = 'holder'`, plus `exit_status = 0` in the "leaves" cases) and
keeps its transaction open. The worker is seen waiting on it (`wait_event_type = 'Lock'`) every time,
then the holder commits. Each case uses 4 tasks, all with `exit_status IS NULL`. Results were
identical on plain PG 18 (`postgres` provider) and the TS 2.32 hypertable (`timescale`):

| case | worker statement | rowcount | after |
|---|---|---|---|
| a | `DELETE FROM task WHERE id = $1` | 1 | X gone |
| b | `DELETE FROM task WHERE exit_status IS NULL` | 4 | all gone |
| c | `UPDATE task SET "group" = 7 WHERE exit_status IS NULL` | 4 | X `('holder', None, 7)` |
| d (X leaves) | `DELETE … WHERE exit_status IS NULL` | 3 | X kept `('holder', 0, 0)` |
| e (X leaves) | `UPDATE … "group" = 7 WHERE exit_status IS NULL` | 3 | X kept `('holder', 0, 0)` |
| f (X leaves) | `DELETE … WHERE id = $1 AND exit_status IS NULL` | 0 | X kept |

**Locks after a re-check to 0.** In d, e and f, X is not written, yet the worker's open transaction
still holds it: a third session's `SELECT … FOR UPDATE` hits `lock_timeout` (500 ms), and X's `xmax`
equals the worker's xid (e.g. f on TS: `worker_xid 49403`, `x_xmax ['49403']`). PostgreSQL locks
the newest row version before EvalPlanQual re-checks it, and keeps that lock to the end of the
transaction even when the re-check fails. Consequences:

- A same-transaction re-run of the write does not block on itself.
- The skipped row stays locked until commit. This matters for R4's single `--limit` transaction and
  for any "fresh read says still qualifies" check; that read sees the committed version.
- 2.31's miss returns no row from the dummy EPQ plan *after* the tuple lock. So the missed row is
  probably also left locked there, but this cannot be verified locally.

## 3. `--delete` real-race test design (`03_cli_delete_blocked.py`)

An `hs update` subprocess blocks on the holder, is seen in `pg_stat_activity`, and finishes after the
holder commits, on both providers (TS 2.32 output shown; PG 18 identical):

```
--- filtered delete: hs update --delete -f -R
    blocked: 'DELETE FROM task WHERE task.exit_status IS NULL'  widened=True  today=False
    rc 0, 'Deleted 3 tasks', rows left 0
--- --limit delete: hs update --delete -f -R --limit 3
    blocked: 'DELETE FROM task WHERE task.id = $1::UUID'        widened=True  today=False
    rc 0, 'Deleted 3 tasks', rows left 0
--- filtered update: hs update -f -R group=7
    blocked: 'UPDATE task SET "group"=$1::INTEGER WHERE task.exit_status IS NULL'  widened=True today=True
    rc 0, 'Updated 3 tasks', 3 rows group=7
```

So `contend`'s worker can call `main(['hs', 'update', '--delete', '-f', ...])` from `tests/__init__.py`
(it runs `subprocess.run(..., env=os.environ)`, and `contended_db`'s `ts_site` has set the env).
Seed the tasks with `submit()` (uuid7 ids); the subprocess reads the same database. The only change
the harness needs is the regex `blocked_by` matches:

```python
# The statement a blocked HyperShell write is running, with or without a schema prefix.
WRITE_TASK = re.compile(r'^\s*(UPDATE|DELETE\s+FROM)\s+(\S+\.)?"?task"?\s', re.I)
```

Use it in `blocked_by` in place of `UPDATE_TASK`, and fix the docstring ("an UPDATE or DELETE of
task"). Every existing test still matches, since all of them are UPDATEs. The `DELETE_SQL` holder is
unaffected: it is the holder, not the waiter.

**Silent miss under `--limit`.** `Task.delete_all` is a `Session.delete` flush. On a rowcount
mismatch, SQLAlchemy 2.0.50 (`orm/persistence.py`, `_emit_delete_statements`) only *warns*
("DELETE statement on table 'task' expected to delete N row(s); M were matched"), because `Task` has
no version column. So a contended `--limit --delete` miss on 2.31 is silent, unlike the
`StaleDataError` an UPDATE batch raises.

## 4. `xmin` (`03_xmin.py`)

`SELECT xmin FROM task` works through the hypertable. One transaction updated 2 of 5 rows; every
form counted exactly 2, on both providers:

| form | before write | after UPDATE of 2 | +1 row in a savepoint | +`FOR UPDATE` of a 4th |
|---|---|---|---|---|
| `xmin = CAST(CAST(pg_current_xact_id() AS text) AS xid)` | 0 | 2 | 2 | 2 |
| `xmin = pg_current_xact_id()::xid` | 0 | 2 | 2 | 2 |
| `xmin::text = (txid_current() % 4294967296)::text` | 0 | 2 | 2 | 2 |

Caveats:

- **Savepoints break it.** A row updated inside a savepoint (`begin_nested`) carries the
  subtransaction's xid, so it is not counted (2, not 3), even after the savepoint is released. The
  writes being counted must not run in a savepoint.
- A row that is only locked (`FOR UPDATE`, or case d/e/f above) is not counted: correct.
- **The text route breaks after xid wraparound:** `CAST('4294967301' AS xid)` raises
  `NumericValueOutOfRange`, while `CAST(CAST('4294967301' AS xid8) AS xid)` gives `5`. Use
  `CAST(pg_current_xact_id() AS xid)`, an explicit `pg_cast` (`castcontext 'e'`, function `xid`).
  It is PG 13-compatible: the PG 13 docs say `xid8` "can be converted to `xid` by casting if
  required" (<https://www.postgresql.org/docs/13/functions-info.html>; `pg_current_xact_id` is new
  in 13, and CI's oldest server is `postgres:13`). The modular `txid_current()` form also works on
  13 but is deprecated.
- `WHERE xmin = …` cannot use an index: it is a scan of `task`. Bound it with the id list (`--limit`)
  or with the filter, where possible.
- It counts updated rows only. A DELETE has to count by rowcount or `RETURNING id`.

## Recommendation

- **R6's `--delete` real-race test is expected RED on CI's TimescaleDB 2.31.0 jobs**, not a guard.
  Upstream #10902 names DELETE explicitly ("DELETE 0, row remains"), and 2.31.0 is in its affected
  list; the fix (#10906) is only in 2.32.0, with 2.31.1 unreleased. Locally (2.32) and on plain PG it
  passes. If CI shows it green, record that as the GOAL's guard finding, but expect red.
- Expect red on both `--delete` forms. Filtered: the row is not deleted, and "Deleted N" is the
  pre-count. `--limit`: a SQLAlchemy warning only, no error.
- Harness change: widen `UPDATE_TASK` to `WRITE_TASK` above, and drive `hs update` as a subprocess
  via `tests.main` inside `contend`.
- For the plan: a filtered write that re-checks a row out of its filter keeps that row locked to
  the end of the transaction, on PG and TS alike. The xmin count works on hypertables and on PG 13
  with `CAST(pg_current_xact_id() AS xid)`, provided the counted writes run outside savepoints.
