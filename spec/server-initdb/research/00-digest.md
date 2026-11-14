# 00 — Digest

`appetite: small`, `kind: fix`, but the change lands in `server.py`, a high-blast-radius file, so
`hs-plan` Step 3 calls for pinned contracts before design. Two narrow briefs, done in-session and
backed by probes against a local PostgreSQL 18 / TimescaleDB 2.32 server (fresh databases created
and dropped per case). The change is confined to `ServerApp` (the command-line class); no thread,
FSM, queue or model code moves.

1. **Start-up seam** ([01](01-startup-seam.md)). The one bare `ensuredb()` in `ServerApp.run`
   precedes every scheduling mode and every listener, and ignores both `--initdb` and `--no-db`.
   Replace it with `ServerApp.check_database()`: nothing under `--no-db`, else
   `ensuredb(auto_init=self.auto_initdb)`. Probed (seam emulated by a wrapper): `--no-db` then
   leaves a SQLite file uncreated through a whole server + client run and creates no PostgreSQL
   table, so nothing else in the server opens the database. An in-process harness records the
   calls per mode without a listener or a database and is red on `develop` for every `--initdb`
   and `--no-db` case; it is the R7 unit test.
2. **TimescaleDB path** ([02](02-timescale-path.md)). `initdb()` already orders `prepare` →
   schema → `converge`, so every refusal comes before any table (a later failure is an unexpected database error in
   the pass, reported as `hs initdb` reports it); the shared
   exception mapping gives the server `hs initdb`'s message and exit 5 (only the logger name
   differs). Probed: fresh `--initdb` under both providers in FILE and `--forever` modes creates
   the four tables (and under `timescale` the hypertable, columnstore and policy) and a client
   completes the tasks; a re-launch changes nothing and logs no pass line; an older schema is
   upgraded in place with its rows kept; a populated plain `task` under `timescale` gets
   `hs initdb`'s warning and stays plain; a role refused `CREATE EXTENSION` makes the server exit 5
   with `hs initdb`'s exact message before it listens, creating nothing.

No cross-brief contradiction. Single recommendation: route the existing call; add nothing to
`data/`, `submit.py` or `cluster/`.
