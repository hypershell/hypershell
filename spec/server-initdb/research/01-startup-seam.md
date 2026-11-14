# 01 — The `hs server` start-up seam

Scope: where `ServerApp.run` touches the database, what each mode needs, and whether anything else
in a `--no-db` server opens the configured database. Read against `develop` at `6c46cbd`; probed on
2026-11-13 (local PostgreSQL 18, TimescaleDB 2.32 Community, CPython 3.14).

## The call today

`ServerApp.run` (`src/hypershell/server.py:1738-1790`) runs, in order: `special_options()`,
`resolve_auth('server')`, `check_args()` (argument refusals, including `--autoscaling` with
`--no-db`), `enable_tls()`, then a bare `ensuredb()` at `:1745`, then branches into
`serve_with_sites` (`--autoscaling`), `serve_forever` (`--forever`), or `serve_from` (FILE,
`<stdin>`, `--restart`). Every mode shares the one call, and every `serve_*` starts the queue
listener only after it returns, so whatever the call raises stops the server before it listens.

`auto_initdb` (`--initdb`) and `in_memory` (`--no-db`) are parsed (`:1705-1709`, mutually
exclusive) but neither reaches the call. `ensuredb(auto_init=False)` (`data/__init__.py:121-132`)
raises `ConfigurationError('Missing database configuration')` for SQLite without a file, runs
`initdb()` for SQLite, and `checkdb()` otherwise; `ensuredb(auto_init=True)` runs `initdb()` on
every dialect. That keyword is the existing API for exactly this switch; nothing in `server.py`
passes it.

The siblings: `SubmitApp.check_database` (`submit.py:1402-1410`) and `ClusterApp.__enter__`
(`cluster/__init__.py:555-562`) both run `initdb()` when the dialect is SQLite or `--initdb` is
given, else `checkdb()`; the cluster skips `checkdb()` under `--no-db` but still runs `initdb()`
on SQLite (the non-goal half of "Hole C"). Neither prompts.

## In-process harness (red on `develop`)

`ServerApp.main(argv)` runs in-process with `hypershell.data.checkdb`, `upgradedb`,
`timescale.prepare`/`converge`, the three `serve_*` entry points, and `server.resolve_auth`
replaced by recorders, and `hypershell.data.DATABASE_DIALECT`/`TIMESCALE` set per provider. No
listener starts, no database is needed, and `initdb()` itself runs for real (it reads the patched
module globals). On `develop` every PostgreSQL and TimescaleDB case records `['checkdb', 'serve']`,
with or without `--initdb` or `--no-db`, in all five modes (FILE, `-`, `--forever`, `--restart`,
`--autoscaling fixed`); every SQLite case records `['upgradedb', 'serve']`, `--no-db` included;
`--autoscaling fixed --no-db` exits 2 with no call. This is the R7 unit test's shape; the `--initdb`
and `--no-db` rows are its red cases.

## `--no-db` touches nothing else

The SQLite engine is created at import (`data/core.py`) but connects lazily, so a file appears only
when something opens a session. Probe: `hs server --no-db FILE` plus one `hs client`, SQLite file
configured but absent. On `develop` the file is created (by `ensuredb()` → `initdb()`); with the
call skipped under `--no-db` (emulated, below) the whole run (submit thread, scheduler-less dispatch,
receiver, client completion, shutdown) completes with exit 0 and **the file never exists**. On fresh
PostgreSQL and TimescaleDB databases the same emulated run exits 0 and creates no table; `develop`
exits 5 with `DatabaseUninitialized`. So guarding the one call is sufficient for R5; no thread-level
change is needed (`ServerThread` already guards its writes on `in_memory`, invariants §4).

## Emulating the seam without a source edit

The probes ran the planned seam through a wrapper that replaces the bare call before `main()`:

```python
import sys, hypershell.server as srv
from hypershell.data import ensuredb
argv = sys.argv[1:]
srv.ensuredb = (lambda: None) if '--no-db' in argv else (lambda: ensuredb(auto_init='--initdb' in argv))
from hypershell import main; sys.exit(main(['server', *argv]))
```

## Recommendation

Replace the bare call with a `ServerApp.check_database()` step (named like `SubmitApp`'s) that does
nothing under `--no-db` and otherwise calls `ensuredb(auto_init=self.auto_initdb)`. It keeps the
call's position (after `check_args`/`enable_tls`, before any `serve_*`), keeps `ensuredb`'s SQLite
configuration refusal and auto-initialization (R4), and changes no other command (`ensuredb`'s
default stays `False`).
