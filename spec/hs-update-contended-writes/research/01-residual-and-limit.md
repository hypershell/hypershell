# Research 01 — telling a set-based write's missed row from one it wrote; `--limit` as one transaction (2026-11-15)

Probes: `probes/01_residual.py` (`postgres` and `timescale` providers), `probes/01_columnstore.py`,
`probes/04_limit_cost.py` (`sqlite` and `postgres`). Local PostgreSQL 18.7, TimescaleDB 2.32.0,
SQLite 3.53.1, SQLAlchemy 2.0.50; every probe database is created and dropped by the probe.

## 1. A deterministic stand-in for the miss on a server database

A `BEFORE UPDATE OR DELETE … FOR EACH ROW` trigger that returns NULL for a chosen row skips that row
and leaves it out of the statement's count, exactly what TimescaleDB 2.28.1–2.31.0 do to a row whose
lock the statement waited on (research/03: upstream #10902). A control table says how many times to
skip the row. It works on a plain PostgreSQL table and on the `task` hypertable (the trigger reaches
the chunks):

```
provider=postgres  hypertable=False  UPDATE kept-in-filter: rowcount=3 (4 match, 1 skipped) ... rerun=1
provider=timescale hypertable=True   UPDATE kept-in-filter: rowcount=3 (4 match, 1 skipped) ... rerun=1
                                     DELETE: rowcount=3 residual=True rerun=1
```

So the set-based form's recovery can be forced on every PostgreSQL job, locally and on CI, without
TimescaleDB 2.31, by fault injection in the server (the real race stays as the CI-only proof).

## 2. Which rows did the set-based UPDATE not write?

A filtered UPDATE has one matched count for all its rows, so the rows it missed must be found by a
read afterwards, in the same transaction (a new statement sees the other writer's commit). Rows that
match the filter after the UPDATE are of three kinds: rows it wrote whose change kept them in the
filter (a tag change under `-w group==0`), rows it missed, and rows that came to match meanwhile
(written and counted, by the GOAL-gate ruling). The first kind must be told from the other two.

- **DELETE needs nothing extra.** A row this transaction deleted is gone for it, so `SELECT id FROM
  task WHERE <filter>` after the DELETE returns exactly the missed and newly matching rows (both
  providers, `delete + residual: ok [6, 0]` on compressed chunks too).
- **`xmin` (rows whose version this transaction wrote) works on plain tables and uncompressed
  hypertable chunks** (`residual=True` on both providers; `RETURNING == xmin-written: True`), but
  **fails on columnstore chunks**, which the `timescale` provider creates by default (a policy after
  4 weeks): `InvalidColumnReference: transparent decompression only supports tableoid system column`
  for the read alone and after an UPDATE (`01_columnstore.py`). Rejected: an `hs update` over old
  tasks would fail on every database that has compressed a chunk.
- **`UPDATE … RETURNING id` works on columnstore chunks** (`update RETURNING id: ok [6]`). The rows
  written are then known; the residual is `SELECT id FROM task WHERE <filter>` minus that set, taken
  client-side. Cost: the written ids are held in memory (about 100 bytes each in Python), and the
  residual read returns every written row that is still in the filter (none for `--cancel -R`,
  `--revert -C`, a field change that moves rows out; all of them for a tag change that does not).
  Statement count stays two, whatever the number of rows. A server-side difference (`id <> ALL(:ids)`)
  was rejected: PostgreSQL 13 does not hash that array, so it is quadratic. A temporary table of
  written ids (`WITH w AS (UPDATE … RETURNING id) INSERT INTO tmp …`) avoids the memory but needs the
  TEMP privilege and a data-modifying CTE on a hypertable; left as an option if the memory matters.
- **SQLite cannot miss.** A SQLite write transaction excludes every other writer for its whole length,
  so a set-based UPDATE's count is exact; no residual is needed there. (SQLite 3.26 on RHEL 8 has no
  `RETURNING` either.)

Per-row re-run, once a missed id is known: `UPDATE task SET <change> WHERE id = :id AND <filter>`;
0 rows → `SELECT id FROM task WHERE id = :id AND <filter>`: no row means gone or no longer matching
(skipped, not counted), a row means contended again (re-run, bounded). Both probes' `rerun=1`.

## 3. `--limit` as one transaction: what it costs

`04_limit_cost.py`, one `UPDATE … WHERE id = :id AND exit_status IS NULL` per row with one commit,
against today's `bulk_update_mappings` per batch of 100 with a commit each:

```
sqlite   N=100000: batches of 100 1.30s; one transaction per-row 1.34s (13 us/row); another writer waited 1.37s
postgres N=10000:  batches of 100 0.55s; one transaction per-row 0.95s (95 us/row); another writer waited 0.90s
```

The write itself costs about the same. The difference is the lock: every written row stays locked
(on SQLite, the whole database's write lock) until the one commit, so another writer of a selected
row (PostgreSQL) or of any row (SQLite) waits for the whole update, where today it waits at most one
batch. Locally that is about 13 µs per row on SQLite and 0.1 ms per row on PostgreSQL; over a network
expect about one round trip per row (for `--limit 100000`, minutes). A running server's writes to
those tasks (confirmations, results, reverts) wait meanwhile; on SQLite a wait past the driver's busy
timeout (pysqlite's default is 5 s, about 350 000 rows at the local rate) fails that write as it
would under any long write transaction. Accepted at the GOAL gate; the plan documents it.

## 4. In-process testing of `hs update`

`TaskUpdateApp.main(argv)` returns the exit status and applies the app's `exceptions` mapping, so a
test can run it in-process with the scoped session bound to its own SQLite engine and the
driver-level injector installed on it, provided `hypershell.task.ensuredb` is replaced (it would
initialize the import-time engine). Two consequences for the design: dialect choices in the new
model code must come from the session's bind (`Session.get_bind().dialect.name`), not the import-time
`DATABASE_DIALECT`; and on SQLite, a test's `meanwhile` change (another connection's commit) can only
run before this transaction's first write, since SQLite's write lock is then held to the commit.
