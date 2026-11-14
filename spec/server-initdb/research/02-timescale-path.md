# 02 — `initdb()` from the server: TimescaleDB pass, refusals, idempotence

Scope: what `initdb()` does when the server reaches it, how its refusals surface through
`ServerApp`, and whether a re-launch on an initialized or older database is safe. Probed on
2026-11-13 against local PostgreSQL 18 with TimescaleDB 2.32 Community, one fresh database per
case (created and dropped by the probe), the seam emulated as in [01](01-startup-seam.md), each
case a real `hs server -p PORT …` plus one real `hs client` with `HYPERSHELL_SERVER_AUTH` in the
environment.

## Order and error path

`initdb()` (`data/__init__.py:87-96`) runs `timescale.prepare(engine)` first under the `timescale`
provider, then `upgradedb()`, then `timescale.converge(...)`. `prepare` refuses, before any table
exists, when the server does not offer the extension, offers or has one older than 2.22.0, does not
preload it, or the role may not create it; each raises `TimescaleError`. `converge` turns a populated
plain table or a table partitioned by another column into warnings, and raises `TimescaleError` only
when a statement fails (its pass rolled back, tables left as the schema step made them: "The tables
exist, but this pass changed nothing; fix the cause and rerun hs initdb"). Its version and extension
gates repeat checks `prepare` already made. So every condition under which `hs initdb` *refuses*
comes from `prepare`, before any table; the only later failure is an unexpected database error in
the pass, which `hs initdb` reports the same way after creating the tables.

`TimescaleError` subclasses `SchemaUpgradeError`, which `get_shared_exception_mapping` maps to
`handle_exception(..., status=exit_status.runtime_error)` (5). `ServerApp.exceptions` and
`InitDBApp.exceptions` both include that mapping, so the server exits with the same status and the
same `TimescaleError: <message>` text as `hs initdb`; the only difference is the logger name in the
line (`[hypershell.server]` vs `[hypershell.data]`). `InitDBApp` also maps `OperationalError`
(connection failures) to a one-line exit 5; `ServerApp` sends it to the generic `Exception` handler
(traceback file, exit 5), as today for `checkdb()`. That is not a TimescaleDB refusal and is
unchanged by this fix.

**Probe — refusal.** A non-superuser role connecting to a `template0` database it may not `CREATE`
in: emulated `hs server --initdb FILE` exits 5 with
`CRITICAL [hypershell.server] TimescaleError: Cannot create the TimescaleDB extension in database
'…': permission denied. Ask an administrator …, then rerun hs initdb.`, no `Server listening` line,
no table, no extension; `hs initdb --yes` on the same database prints the identical message from
`[hypershell.data]` and exits 5; `develop`'s `hs server --initdb` exits 5 with
`DatabaseUninitialized`. (A role that *owns* the database is not refused: TimescaleDB 2.32 lets a
database owner create the extension, as the docs say.)

## Fresh database, every mode

| Case (emulated seam) | postgres | timescale |
|---|---|---|
| `--initdb FILE`, fresh | exit 0, client 0, tables `client source task zone`, 2/2 tasks done | same, `task` a hypertable, pass logs the three INFO changes (hypertable, columnstore, policy) |
| `--initdb --forever`, fresh; `hs submit` then client; server terminated | tables created, 2/2 done | same, hypertable |
| no flag, fresh | exit 5 `DatabaseUninitialized`, no table (R4) | same |
| `--no-db FILE`, fresh | exit 0, 2/2 done by client, no table | same |
| `develop` `--initdb FILE` / `--no-db FILE`, fresh | exit 5 `DatabaseUninitialized`, no table | same |

## Already initialized or older (R3)

- **Re-launch with `--initdb`** on the database the first case left: exit 0, the earlier rows
  unchanged, no `hypershell.data.timescale` line (the pass logs nothing on a database in shape), the
  new file's tasks run. Same under both providers.
- **Older schema (postgres).** After `hs initdb` and one submitted task, `zone` table and
  `task.zone` dropped: `--initdb` logs `Created table zone`, `Added column task.zone`, `Created index
  index_tasks_zone on task`, keeps the task row, and serves.
- **Populated plain table under `timescale`.** Initialized and given a task under `postgres`, then
  launched under `timescale` with `--initdb`: the pass warns `Table task holds rows and is a plain
  table; HyperShell will not convert it …`, leaves it plain, keeps the row, exit 0 — the identical
  warning `hs initdb --yes` gives on that database.
- No prompt anywhere: `initdb()` has none; only `InitDBApp.confirm_action` prompts.

Concurrent launches are already serialized: `upgradedb` takes the schema-upgrade lock and
`converge` waits on it (`database.rst`, "Initialization runs in one transaction, serialized with the
schema step"), the same as two `hsx --initdb` launches today.

## Recommendation

Call `initdb()` (through `ensuredb(auto_init=True)`) and add nothing around it: its order already
gives R2's "before it listens, create nothing" on a fresh database, the shared exception mapping
gives `hs initdb`'s message and status, and its idempotence gives R3. The integration tests should
assert the message text and exit status, not the logger name.
