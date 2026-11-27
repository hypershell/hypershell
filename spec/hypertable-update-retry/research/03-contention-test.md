# 03 — Forcing the race in a test, and proving it red/green without a local server

Scope: GOAL R7 (deterministic, server-observed lock wait, every CI `postgres` entry, red on
`0e8275e` at 2.31.0 only) and the runner the foreman executes on a test cluster. Codebase-only.

## What exists

- `tests/test_timescale.py`: `ADMIN_URL`/`TIMESCALE` from `HS_TEST_POSTGRES`/`HS_TEST_TIMESCALE`;
  `needs_server`; a module-scoped `admin` engine (autocommit); `ts_database()` (a throwaway
  database, dropped `WITH (FORCE)` afterwards); `ts_site(name, provider=…)` (points the CLI's
  environment at that database, returns a private engine); `database_engine`, `is_hypertable`,
  `uuid7()`. Its integration tests drive `hs` as a subprocess (`tests.main`).
- `tests/test_writeback_postgres.py`'s `pg_tasks` binds the in-process scoped `Session` to a
  test database (`Session.remove(); Session.configure(bind=engine)`, restored afterwards), and
  drives `Task`, `Confirm` and `Receiver` in-process: `Confirm(queue=None)` with `client_info` set,
  then `update_info()`; `Receiver(queue=None, redirect_failures=…)` with `tasks` set, then
  `update_tasks()`. Its tables come from `metadata.create_all`, so they are plain tables.
- CI's `postgres` job runs three named files only:
  `pytest -v -rs tests/test_upgrade_postgres.py tests/test_writeback_postgres.py tests/test_timescale.py`.
  A new module must be added to that line.
- Under the `timescale` provider the hypertable is partitioned by `id`, and a v4 id aborts its
  INSERT (`test_v4_id_rejected`). The in-process config is SQLite's, so in-process `Task.new` ids
  must be overridden with `uuid7()` (harmless on plain PostgreSQL).

## Fixture

`contended_db` (new module `tests/test_contended_write.py`, importing `admin`, `ts_database`,
`ts_site`, `database_engine`, `is_hypertable`, `uuid7`, `needs_server`, `TIMESCALE` from
`tests.test_timescale`):

1. `name = ts_database()`; `ts_site(name, provider='timescale' if TIMESCALE else 'postgres')`;
   `hs initdb --yes` must succeed. With `TIMESCALE` set, `is_hypertable` must hold (a
   `pytest.fail` precondition), so a green run on 2.31.0 really exercised a hypertable.
2. Bind the in-process `Session` to `database_engine(name)` as `pg_tasks` does; restore in
   `finally`.
3. Helpers: `submit()` (Task.new + `id = uuid7()` + `Task.add`), `dispatch_one()` (`Task.next`
   through the bundle codec, as the writeback tests do), `finish()`.

## The race, deterministic and observed from the server

```
holder = engine.connect()                       # its own transaction, not autocommit
holder_pid = holder.execute(text('SELECT pg_backend_pid()')).scalar()
holder.execute(<holder's UPDATE of the row>)    # row lock taken, not committed
worker = Thread(target=<HyperShell's write>, captures return value / exception, Session.remove() in finally)
worker.start()
wait_blocked(observer, holder_pid)              # see below; pytest.fail if not observed
holder.commit()
worker.join(timeout)                            # pytest.fail if still alive
<assertions on the worker's result and the row>
```

`wait_blocked` polls, from an autocommit observer connection, every 20 ms up to 30 s:

```sql
SELECT pid, wait_event_type, wait_event, query FROM pg_stat_activity
WHERE datname = current_database() AND :holder = ANY(pg_blocking_pids(pid))
```

It returns once a row shows `wait_event_type = 'Lock'` and a query starting `UPDATE task`, and
fails the test (`pytest.fail`, a precondition, not an assertion) if the worker finished first or
the deadline passed. `pg_blocking_pids` exists from 9.6, so it works on pg13. No sleep decides the
outcome; the only waits are the poll and the join deadline.

## Cases

| Test | Holder (uncommitted) | HyperShell write in the worker | Expect after | `0e8275e` on 2.31.0 |
|------|----------------------|--------------------------------|--------------|---------------------|
| re-run premise | raw `UPDATE … SET client_host = 'h'` | raw `UPDATE … WHERE id`, then the same statement again | second match is 1 | pass |
| Confirm | result columns written (as the Receiver does) | `Confirm.update_info()` for a bundle holding the row | no exception, `update_info` returns `UNLOAD`, `client_id`/`client_host` are the confirming client's, the holder's result kept | **fail** (`StaleDataError` captured) |
| Result | Confirm's stamp (`client_id`, `client_host`) | `Receiver.update_tasks()` for the finished copy, exit status 3, `redirect_failures` a `StringIO` | row completed with the result columns, the args in the failures buffer, no "Refused stale result" WARNING | **fail** (STALE) |
| Result refused | revert (`schedule_time` and the rest NULL) | `Receiver.update_tasks()` | row unscheduled, no result column written, the stale WARNING logged | pass |
| Eviction revert | a late Confirm for the same client | `Task.revert_orphaned(client_id)` | no exception, row reverted (`schedule_time` NULL) | **fail** (`StaleDataError`) |

Every test references only symbols present on `0e8275e`, so `0e8275e` fails by assertion, not by
import. On pg13, pg18 and 2.24.0 all pass before and after (PostgreSQL's re-check matches 1). Log
assertions use `caplog` on the `hypershell` loggers (the writeback tests do the same).

## The runner (`spec/hypertable-update-retry/contention_run.py`)

The tests live on the fix branch; `0e8275e` has none. The runner therefore runs **this branch's
tests against a chosen commit's `src/`**, the way `writeback-prebuilt-update/cpu_bench.py` extracts
`src/` per build:

1. Resolve `--commit` (the `src/` under test) and `--tests` (default `HEAD`); refuse unless
   `pyproject.toml` and `uv.lock` are identical between them (no dependency change, R6).
2. `git worktree add --detach <out>/tree-<label> <tests>`; replace its `src/` with
   `git archive <commit> src`.
3. `uv sync --frozen --extra postgres` there (`--python` passes through), so `hs` on the test's
   `PATH` is the build under test.
4. Record the server: `SELECT version()` and the `timescaledb` extension version, if any.
5. `uv run --frozen --extra postgres pytest -v -rs -p no:cacheprovider --junitxml … tests/test_contended_write.py`
   with `HS_TEST_POSTGRES`/`HS_TEST_TIMESCALE`, in its own session under a kill timer.
6. Write `<label>.log`, `<label>.xml` and a `<label>.json` summary (label, commit, tests commit,
   server, per-test outcome `passed|failed|error|skipped`, exit), print the summary, remove the
   worktree. Exit 0 all passed, 1 a test failed, 2 harness error, 124 timeout. `--dry-run` resolves
   and checks everything that needs no server and prints the plan.

The foreman runs it four times: `0e8275e` and the fix, each on TimescaleDB 2.31.0 and on 2.24.0.
CI on the PR is the second proof (all five `postgres` entries, with the module's tests passing and
none skipped).
