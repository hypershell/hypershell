# 04 — Concurrent upgrades (R3)

## Recommendation

Use **(B) serialize**, with one bounded retry in the style of (A) as a fallback.

1. **Lock-free plan.** Open a connection, `inspect()` it, and diff against `Entity.metadata`. An empty plan returns without taking a lock, without DDL and without any log line above DEBUG. This is R3's no-op path on every later start.
2. **Locked apply.** Use one connection and one transaction for the whole step.
   - **SQLite:** `engine.connect().execution_options(isolation_level='AUTOCOMMIT')`, then `exec_driver_sql('BEGIN IMMEDIATE')`. **PostgreSQL:** `engine.connect()` (psycopg begins implicitly), then `SELECT pg_advisory_xact_lock(:k)`.
   - **Re-inspect on that same connection.** Apply all DDL (`ADD COLUMN`, `create_all(conn)`, `CREATE INDEX`, PG `DROP CONSTRAINT`), then `COMMIT`. On any error, `ROLLBACK` in a `finally`.
   - The work list comes from the second inspection, never the first.
3. **Fallback.** If a DDL statement still fails with a duplicate or already-exists error (SQLite `duplicate column name` / `already exists`; PG SQLSTATE `42701`, `42P07`, `23505`), roll back and rerun the whole step once. The rerun's plan will then be empty. This also covers peers that don't use the lock (Turso, hand-run SQL).

(A) alone was rejected for two reasons:

- **No atomicity.** Pure (A) needs per-statement transactions, or savepoints on PG, where one error aborts the whole transaction. That breaks R4's "leave the schema unchanged" and can leave the half-upgraded state this cycle exists to fix.
- **PG `CREATE INDEX` races are not reliably caught.** Two non-concurrent `CREATE INDEX` statements take `SHARE` locks, which don't conflict with each other, so both proceed. The loser then hits a catalog unique violation (see below).

## SQLite experiment

**Setup.** Python 3.14.6, SQLite 3.53.1, SQLAlchemy 2.0.50, pysqlite. The atomicity checks were repeated on Python 3.12 and 3.13 with identical results. The input is a copy of a 2.8-created, half-upgraded `task.db`: the `source` table is present and the `task` columns are missing. The script was a throwaway under `$TMPDIR` and was not checked in.

**Script outline.** `spawn` processes, each with its own `create_engine('sqlite:///…', connect_args={'timeout': …})`. Each worker:

1. Runs the lock-free `plan(conn)`: 3× `ADD COLUMN` (with `part INTEGER NOT NULL DEFAULT 0`) plus `CREATE INDEX index_tasks_source`.
2. Waits on `multiprocessing.Barrier`, so every worker has already seen "work needed". This forces the TOCTOU race deterministically.
3. Applies its plan in one of four modes:
   - **naive:** apply the stale plan.
   - **optimistic (A):** per-statement transactions, swallowing duplicate/exists errors.
   - **serialize (B):** AUTOCOMMIT connection, `BEGIN IMMEDIATE`, re-plan on the same connection, apply, `COMMIT`.
   - **serialize_legacy:** `BEGIN IMMEDIATE` through `exec_driver_sql` on a normal connection, then `conn.commit()`.

**Observed output (abridged):**

```
[naive n=2]            trials=10 results={'ok': 10, 'FAIL': 10}
    FAIL OperationalError: duplicate column name: source
[optimistic n=2]       trials=10 results={'ok': 20, 'FAIL': 0}   (winner 4 stmts, loser 0)
[serialize n=2]        trials=10 results={'ok': 20, 'FAIL': 0}
    ok 4 stmts after re-inspect (lock wait 0.000s)
    ok 0 stmts after re-inspect (lock wait 0.002s)
[serialize n=4]        trials=10 results={'ok': 40, 'FAIL': 0}   (one does 4, three do 0)
[serialize n=4 WAL]    trials=5  results={'ok': 20, 'FAIL': 0}
[serialize_legacy n=2] trials=10 results={'ok': 20, 'FAIL': 0}
--- atomicity: BEGIN IMMEDIATE; ADD source; CREATE TABLE; ADD bad NOT NULL (no default)
    in-txn inspect sees source: True
    error: Cannot add a NOT NULL column with default value NULL
    after ROLLBACK: source present = False | extra table present = False
--- atomicity: default engine.begin() (no explicit BEGIN)
    after rollback: source present = True        <-- each DDL autocommitted
--- create_all(conn) inside BEGIN IMMEDIATE, then forced error -> ROLLBACK: table gone
--- pooled AUTOCOMMIT conn closed mid-transaction: pool reset rolled back, lock released
--- busy: peer holds BEGIN IMMEDIATE 2.0 s
    timeout=0.5: database is locked after 0.57s
    timeout=5.0: acquired after 2.10s
    lock-free inspect during peer's RESERVED lock: no wait
```

**Findings:**

- **Default path is not atomic.** Under pysqlite's legacy transaction control, SQLAlchemy's `begin()` emits no `BEGIN`, and pysqlite only opens one before DML. Today every DDL statement, including `create_all`'s, commits on its own. That is how the half-upgraded database arose.
- **Explicit `BEGIN IMMEDIATE` makes DDL atomic.** `ADD COLUMN`, `CREATE TABLE` and `CREATE INDEX` all roll back together. `inspect(conn)` on that connection sees its own uncommitted DDL and the peer's committed DDL. A fresh `Inspector` per call has no stale cache.
- **Which form to use.** The AUTOCOMMIT form (SQLAlchemy sets `isolation_level=None`) is the documented way to take over `BEGIN` without touching the shared engine, and it works on Python 3.11–3.14. The legacy form also worked, but it relies on pysqlite not interfering. Avoid the docs' engine-wide `begin` event hook recipe ("Serializable isolation / Savepoints / Transactional DDL"): it would change every HyperShell transaction.
- **Busy timeout.** pysqlite defaults to `timeout=5.0` (`PRAGMA busy_timeout` → 5000). `connect_args: {timeout: N}` or the URL `?timeout=` raises or lowers it.
- **Index build can outlast the timeout.** `CREATE INDEX index_tasks_source` on a 2M-row table took 0.46 s on local SSD, and could be several times slower on network or parallel filesystems. A waiter whose peer holds the lock longer than its busy timeout gets `database is locked` and would fail because of the other process, which violates R3. **Raise the wait for the step only:** on the upgrade connection, `PRAGMA busy_timeout = 60000` (or loop on `BEGIN IMMEDIATE` until a deadline, logging at DEBUG), restored in `finally`. If the deadline expires, raise an actionable error ("another process holds the database write lock"), not a traceback.

### Self-blocking (same process)

Same process, timeout 0.5 s, with a `Session` on one pooled connection and the upgrade on another:

| Session state | rollback journal | WAL |
|---|---|---|
| SELECT fully consumed (pysqlite opens no txn) | ok | ok |
| SELECT cursor left open (holds SHARED) | **FAIL at COMMIT: database is locked** | ok |
| uncommitted UPDATE (RESERVED) | **FAIL at BEGIN IMMEDIATE** | **FAIL at BEGIN IMMEDIATE** |

Each failure is a self-deadlock that lasts until the busy timeout. **Ordering rule:** the step runs inside `ensuredb`/`initdb`/`checkdb`, before the process's scoped `Session` issues any statement. It uses its own `engine.connect()`, never `Session`, and `initdb(optimize=True)`'s `PRAGMA optimize` runs after it. Optionally call `Session.remove()` before the step to make the rule hold by construction.

## PostgreSQL reasoning

- **Transactional DDL.** `ADD COLUMN`, `CREATE INDEX`, `CREATE TABLE` and `DROP CONSTRAINT` all roll back with the transaction ([wiki](https://wiki.postgresql.org/wiki/Transactional_DDL_in_PostgreSQL:_A_Competitive_Analysis)). So R4's "leave the schema unchanged" falls out of a single transaction.
- **Lock levels.** "An ACCESS EXCLUSIVE lock is acquired unless explicitly noted" covers `ADD COLUMN` and `DROP CONSTRAINT` ([ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)). Adding a column with a non-volatile `DEFAULT` (or none) requires no table rewrite, so `part … DEFAULT 0` is metadata-only. `CREATE INDEX` without `CONCURRENTLY` takes `SHARE`, which does not conflict with `SHARE` ([explicit locking](https://www.postgresql.org/docs/current/explicit-locking.html)).
- **Unserialized races.** Two `ALTER TABLE task ADD COLUMN source` statements: the loser queues on ACCESS EXCLUSIVE, then fails `42701 duplicate_column`. `ADD COLUMN IF NOT EXISTS` probably resolves after the lock is granted, but that is unverified, so pin it in CI. Two `CREATE INDEX` statements both build, then one hits `pg_class_relname_nsp_index` (23505) or `42P07`.
- **`IF NOT EXISTS` is not race-safe** for `CREATE TABLE`/`CREATE INDEX`. Maintainers call it a known limitation and recommend serializing with an advisory lock:
  - [BUG #9956](https://postgresql.org/message-id/20140411145120.10532.93247%40wrigleys.postgresql.org): `pg_type_typname_nsp_index`.
  - [R. Haas](https://postgresql.org/message-id/CA+TgmoZAdYVtwBfp1FL2sMZbiHCWT4UPrzRLNnX1Nb30Ku3-gg@mail.gmail.com): "doesn't pretend to handle concurrency issues any better", with the suggestion "pg_advisory_lock/CINE/pg_advisory_unlock".
  - [2018 race report](https://postgresql.org/message-id/93336f06-86c2-71b6-abe3-0d42cc614b2b%40postgrespro.ru).
  - [T. Lane on pg_class_relname_nsp_index](https://postgresql.org/message-id/31800.1432737044%40sss.pgh.pa.us).
- **Advisory lock semantics.** `pg_advisory_xact_lock(bigint)` "obtains an exclusive transaction-level advisory lock, waiting if necessary", and it is released at transaction end with no manual unlock. The bigint and (int, int) key spaces do not overlap ([advisory functions](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)). The lock is re-entrant within a session.
- **Visibility.** The transaction must be **READ COMMITTED**, which is the PG default and is not changed by HyperShell. Under that level each later statement takes a fresh snapshot, so the re-inspection after the lock sees the winner's committed DDL. Under REPEATABLE READ the snapshot would be taken by the lock `SELECT` itself, before the wait, and the re-inspection would be stale. Don't run the step on an engine with another isolation level.
- **SQLAlchemy usage:**

```python
with engine.connect() as conn:                    # psycopg: implicit BEGIN
    conn.execute(text('SET LOCAL lock_timeout = :t'), {'t': '30s'})   # or f-string of a constant
    conn.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': SCHEMA_UPGRADE_LOCK_KEY})
    work = plan(conn)                              # inspect(conn), same connection
    for stmt in work: conn.execute(stmt)
    conn.commit()                                  # releases the advisory lock
```

`SET LOCAL` may not accept a bind parameter on the server side. Use `SELECT set_config('lock_timeout', :t, true)` instead and verify it in CI.

## Lock key & timeouts

- **Key.** Use one constant `SCHEMA_UPGRADE_LOCK_KEY = -4139901733377976503`, the first 8 bytes (big-endian, signed) of `sha256(b'hypershell:schema-upgrade')`. Hard-code the literal next to a comment giving that derivation, so it never changes across releases; a 2.9.x and a 2.9.y must contend on the same key.
- **Schemas share the key.** Advisory locks are not schema-scoped, so sites using different `schema:` values in one database serialize against each other. That is harmless (milliseconds), so don't fold the schema into the key.
- **Third-session blocking.** An ALTER waits for any open transaction that has touched `task`, for example an older server idle in transaction. Worse, a queued ACCESS EXCLUSIVE request blocks every later `task` query from everyone.
- **Recommend `lock_timeout` ≈ 30 s,** set with `SET LOCAL` before the advisory lock. It "applies separately to each lock acquisition attempt" and covers implicitly acquired locks ([lock_timeout](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-LOCK-TIMEOUT)). On `55P03 lock_not_available`, roll back (the schema is unchanged) and exit with an actionable error: "schema upgrade could not lock table `task`; another session holds it (an older HyperShell server?). Stop it or retry." This failure comes from a third party, not from the concurrent HyperShell step, so it stays within R3.
- **Waiting peers.** A peer waiting on the advisory lock behind a winner that is stuck on such a lock also times out, after at most about 2× the timeout. Its error is the same actionable one.
- **SQLite.** Raise `busy_timeout` for the step only, as above, and don't change the user's `connect_args` default.
- **Index builds block reads on PG.** Building `index_tasks_source` inside the ACCESS-EXCLUSIVE transaction blocks `task` reads for the duration of the build. `CREATE INDEX CONCURRENTLY` cannot run in a transaction. Accept this for a one-time upgrade and document it.

## Test design

1. **Deterministic in-process race (SQLite; same test on PG).**
   - Set up two engines, `ea` and `eb`, on one temp copy of the 2.8.3 fixture. Structure the step as `plan(conn)` → `_acquire_lock(conn)` → `plan(conn)` → `apply`.
   - Monkeypatch `_acquire_lock` so that on `eb`'s call it first runs `upgrade(ea)` to completion, then performs the real lock.
   - **Assert:**
     - `eb`'s first plan was non-empty (spy).
     - Its second plan is empty.
     - Both calls return normally.
     - `caplog` holds no record above DEBUG from `eb`, and INFO records from `ea` name the table.
     - The final schema is current.
   - **Mutation check:** patch out the re-inspection and expect `duplicate column name`. This proves the test bites.
2. **No-op path.** Run the step twice. On the second run, the `_acquire_lock` spy is not called, no DDL is emitted (count `before_cursor_execute` events for `ALTER`/`CREATE`), and nothing is logged above DEBUG.
3. **Atomicity (R4 overlap).** Add a model column `NOT NULL` with no server default. The step raises the actionable error, and the schema is byte-identical afterwards, including the columns it would have added before the failing one.
4. **Self-blocking guard.** Open a `Session` with an uncommitted write and call the step. Expect a clear error within the busy timeout, not a hang. Or assert that `ensuredb` calls `Session.remove()` first, whichever the plan chooses.
5. **Real two-process smoke.** Use `multiprocessing` `spawn`, a `Barrier`, and N=4 subprocesses running `hs list` (or the step's entry point) against one 2.8.3-fixture SQLite file, in both rollback-journal and WAL modes. All exit 0, and exactly one logs INFO changes.
6. **PG in CI.** Run tests 1, 2, 3 and 5 against a service container. Add a third-session test: `BEGIN; SELECT 1 FROM task LIMIT 1` held open, step run with a short `lock_timeout`, expecting the actionable `55P03` error and an unchanged schema. Add a `CREATE INDEX` race without the lock, as a documented `xfail`/evidence test (optional).

## Needs CI / open risks

- **PostgreSQL entirely unverified locally.** No local server was available. Everything in the PG section is reasoned from the docs and threads above, so CI must confirm:
  - lock-level behaviour, `42701`/`23505` under races, `ADD COLUMN IF NOT EXISTS` behaviour after the lock wait;
  - that `SET LOCAL lock_timeout` covers the advisory wait;
  - bind-parameter handling for `SET LOCAL`.
- **TimescaleDB.** It is PostgreSQL, so advisory locks and transactional DDL are unchanged. Re-check once hypertables exist (`timescale` R2+), since ALTERs on hypertables propagate to chunks.
- **Turso (`sqlite+turso`).** `COMPAT.md` lists `BEGIN TRANSACTION` but says nothing of `BEGIN IMMEDIATE`. It also states mixed SQLite/Turso multi-process access is unsupported. Unknown → try `BEGIN IMMEDIATE`; if it raises at the `BEGIN`, fall back to a plain `BEGIN` plus the (A)-style whole-step retry. Test this with the experimental provider before claiming R3 for it.
- **Python transaction-control default.** If pysqlite's `autocommit` default moves away from `LEGACY_TRANSACTION_CONTROL`, or a user hook sets `autocommit=False`, SQLAlchemy's AUTOCOMMIT mode is documented as incompatible. Guard by checking `dbapi_connection.in_transaction` after `BEGIN IMMEDIATE`, or pin the behaviour with a test.
- **Older peers are out of scope.** 2.8 processes running concurrently against an upgrading database are not covered by R3. A peer with an open write transaction surfaces only as the actionable lock-timeout error.
