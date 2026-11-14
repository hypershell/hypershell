# PLAN — `hs server --initdb` initializes the database

> **Status:** Draft for review · **Last updated:** 2026-11-14
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

Route the server's one start-up database call through the flags it already parses: under `--no-db`
skip it, otherwise call `ensuredb(auto_init=self.auto_initdb)`. That reaches `initdb()` (schema plus,
under `timescale`, the extension pre-flight and hypertable pass) exactly when `--initdb` is given, in
every scheduling mode, before any listener starts, and leaves the no-flag path byte-for-byte as it
is. A few lines in `ServerApp`, one new test module (in-process unit matrix plus real-CLI drives on
SQLite, PostgreSQL and TimescaleDB), the CI line that runs it, and the doc passages that described the
defect. Probes on a local PostgreSQL 18 / TimescaleDB 2.32 server with the seam emulated show every
R-ID's behaviour before any source edit ([`research/00-digest.md`](research/00-digest.md)).

## 2. Design

### 2.1 The seam (`src/hypershell/server.py`, `ServerApp` only)

`ServerApp.run` keeps its order: `special_options()` → `resolve_auth` → `check_args()` →
`enable_tls()` → **database step** → `serve_with_sites` | `serve_forever` | `serve_from`. The bare
`ensuredb()` becomes `self.check_database()`:

```python
def check_database(self: ServerApp) -> None:
    """Initialize the database with --initdb, else check (and upgrade) it; never open it with --no-db."""
    if not self.in_memory:
        ensuredb(auto_init=self.auto_initdb)
```

- `--initdb` → `ensuredb(auto_init=True)` → `initdb()` on every dialect: `timescale.prepare` →
  `upgradedb` → `timescale.converge` under `timescale`, `upgradedb` alone under `postgres`/SQLite
  (R1, R2, R3). No prompt: `initdb()` has none; only `InitDBApp.confirm_action` prompts (R3).
- No flag → `ensuredb()` exactly as today: SQLite without a file refused (`ConfigurationError`,
  exit 3), SQLite auto-initialized, PostgreSQL/TimescaleDB `checkdb()` (refusal with
  `DatabaseUninitialized`, exit 5; in-place upgrade otherwise; no TimescaleDB pass) (R4).
- `--no-db` → no call: nothing opens, creates or upgrades the configured database (R5). The server
  threads already skip every database write under `in_memory` (invariants §4); the probe confirmed
  nothing else opens the SQLite file across a full server + client run
  ([01](research/01-startup-seam.md)).
- `--autoscaling` with `--no-db` stays refused by `check_args()` (exit 2) before the step.
- The step stays where the bare call was, after argument refusals and before every `serve_*`, so a
  refusal from `initdb()` stops the server before it listens (R2).

`ensuredb(auto_init=...)` is the existing public switch for exactly this; its default stays `False`,
so every other caller (`task.py` apps, `relay`) is untouched. The sibling commands keep their own
inline form (`SubmitApp.check_database`, `ClusterApp.__enter__`); the server differs from them only in
skipping SQLite under `--no-db`, which R5 requires and the GOAL's non-goals keep out of
`hs cluster`. The method name mirrors `SubmitApp.check_database`.

Help text: the `--initdb` line in `APP_HELP` ("Auto-initialize database.") is the same line
`hs submit` and `hs cluster` carry and is now true; it stays. No option, usage line or completion
changes, so `share/` and the `*_usage.rst` snippets are untouched.

### 2.2 Errors and exit status (R2)

`timescale.prepare` raises `TimescaleError` (a `SchemaUpgradeError`) for every condition under which
`hs initdb` refuses (extension not offered, too old, not preloaded, role may not create it), before
any table exists. `ServerApp.exceptions` already carries `get_shared_exception_mapping`, which maps
`SchemaUpgradeError` to a one-line `CRITICAL … TimescaleError: <message>` and
`exit_status.runtime_error` (5), as `InitDBApp` does. Probed: the server's line and `hs initdb`'s
are identical apart from the logger name (`[hypershell.server]` vs `[hypershell.data]`), and no
`Server listening` line, table or extension appears. No new exception handling is added.

### 2.3 Tests (R7) — new `tests/test_server_initdb.py`

**Unit, no database server (`@mark.unit`).** In-process `ServerApp.main(argv)` with recorders in
place of `hypershell.data.checkdb`, `hypershell.data.upgradedb`, `hypershell.data.timescale.prepare`
and `.converge`, the three `serve_*` entry points (`hypershell.server.serve_from`,
`.serve_forever`, `hypershell.cluster.topology.serve_with_sites`), and `hypershell.server.resolve_auth`
(a valid generated key; nothing on a command line); `hypershell.data.DATABASE_DIALECT` and `TIMESCALE`
set per provider through `monkeypatch`. `initdb()` and `ensuredb()` run for real. Parametrized over
provider (`postgres`, `timescale`, `sqlite`) × mode (FILE, `-`, `--forever`, `--restart`,
`--autoscaling fixed`) × flag (none, `--initdb`, `--no-db`), asserting the recorded sequence:

| provider | none | `--initdb` | `--no-db` |
|---|---|---|---|
| postgres | `checkdb, serve` | `upgradedb, serve` | `serve` |
| timescale | `checkdb, serve` | `prepare, upgradedb, converge, serve` | `serve` |
| sqlite | `upgradedb, serve` | `upgradedb, serve` | `serve` |

(`--autoscaling fixed --no-db` exits 2 with nothing recorded.) Plus: a `prepare` that raises
`TimescaleError` makes `--initdb` exit `exit_status.runtime_error` with the message on stderr and
neither `upgradedb` nor `serve` recorded. On `develop` every postgres/timescale `--initdb` row and
every `--no-db` row records `checkdb` (or SQLite `upgradedb`), so the matrix is red there (probed,
[01](research/01-startup-seam.md)).

**Real CLI, SQLite (`@mark.integration`, `temp_site`, no database server).** `hs server --no-db FILE`
plus one `hs client` with `HYPERSHELL_SERVER_AUTH` in the environment: both exit 0, the client runs
the tasks, and the configured SQLite file does not exist afterwards (R5; red on `develop`, which
creates it).

**Real CLI, PostgreSQL / TimescaleDB (`@mark.integration`, skipped without `HS_TEST_POSTGRES`).**
Reuse `tests/test_timescale.py`'s `admin`, `ts_database`, `ts_site` fixtures and shape helpers
(imported by name), one fresh database per case, key in the environment, a free port per launch, and
a shared helper that starts `hs server`, waits until its port accepts (bounded), runs `hs client`,
and always stops the server:

- `postgres` provider (needs a server, not TimescaleDB; runs on the plain CI entries too):
  `--initdb FILE` on a fresh database → exit 0, four tables, tasks completed (R1); `--initdb
  --forever` → tables, a `hs submit`ted task completed, server stopped (R1); re-launch `--initdb` on
  it → earlier rows unchanged, exit 0 (R3); no flag on a fresh database → exit 5,
  `DatabaseUninitialized`, no table (R4); `--no-db FILE` on a fresh database → exit 0, no table (R5).
- `timescale` provider (`with_timescale`): `--initdb FILE` fresh → `assert_converged` and tasks done
  (R1, R2); re-launch → `shape()` unchanged and no `hypershell.data.timescale` INFO line (R3);
  `--no-db FILE` fresh → no table, no extension created (R5); a role refused `CREATE EXTENSION`
  (non-superuser, `template0` database it does not own; role created and dropped by the test) →
  exit 5 with the same `TimescaleError` message text as `hs initdb --yes` on that database, no table
  (R2).

The module is added to the CI PostgreSQL job's explicit file list in `.github/workflows/tests.yml`
(and the job comment's list of tests that run on the plain entries), or its database cases never
run in CI.

### 2.4 Documentation (R6)

All in the same commit as the seam (invariants §12):

- `docs/_include/server_help.rst` `--initdb`: initializes the database at launch, or upgrades an
  existing one in place, as `hs initdb` does (under `timescale`, including the extension check and
  the hypertable pass), without prompting; automatic for SQLite. `--no-db`: add that the configured
  database is not opened.
- `docs/_include/database.rst` SQLite note (`:94`): only `hs initdb` runs `PRAGMA optimize`; drop
  "(or launching with `--initdb`)".
- `docs/_include/database.rst` Initialization (`:110-112`) and `docs/_include/database_alt.rst`
  (`:8-10`): name the commands — `hs initdb`, or `hs server`, `hs submit` or `hs cluster` launched
  with `--initdb`.
- `docs/_include/database.rst` TimescaleDB section: `:206-208` names `hs server` with the other two
  and drops "`hs server` does not initialize the database, even with `--initdb`…"; `:243-245`
  ("launching `hs submit` or `hs cluster` with `--initdb` every time") adds `hs server`;
  `:257-260` lists `hs server` among the commands that run the initialization with `--initdb` and
  among those that run only the schema step without it.
- `docs/cli/server.rst`: a third `.. versionchanged:: 2.9.0` beside the existing two, one clause:
  `--initdb` initializes the database at launch, as for `hs submit` and `hs cluster`, and `--no-db`
  leaves the configured database alone. (`hs-release` adds the `:pr:` link.) The help snippet
  itself carries no marker, matching every other `*_help.rst` (they feed the man page).

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | §2.1 `check_database()` → `ensuredb(auto_init=True)` before every `serve_*`; unit matrix (all five modes); PG drives FILE and `--forever` |
| R2   | §2.1 `initdb()` runs `prepare`/`converge` under `timescale`; §2.2 shared mapping gives `hs initdb`'s message and exit 5 before listening; unit refusal case; TimescaleDB drive (shape) and privilege-refusal drive |
| R3   | `initdb()` idempotent, no prompt in the path; PG/TimescaleDB re-launch drives (rows and shape unchanged, no pass line); older-schema behaviour is `upgradedb`'s (probed, [02](research/02-timescale-path.md)) |
| R4   | No-flag path calls `ensuredb()` unchanged; unit rows "none"; PG drive no-flag refusal |
| R5   | `if not self.in_memory` guard; unit `--no-db` rows; SQLite drive (file never created); PG/TimescaleDB `--no-db` drives (no table) |
| R6   | §2.4 passages in `server_help.rst`, `database.rst`, `database_alt.rst`, `docs/cli/server.rst`; docs build `-W` |
| R7   | §2.3 `tests/test_server_initdb.py`: red-on-`develop` check recorded at build; CI file list |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) before
research (Step 2) and again on this design (Step 5). The GOAL's intent is consistent with every
section; nothing is bent.

- **High-blast-radius file `server.py`** — touched only in `ServerApp` (the command-line class); no
  `ServerThread`, `Scheduler`, `HeartMonitor`, queue, FSM or shutdown code changes. Any CONFIRMED
  review finding still forces the human gate.
- **§4 Server modes** — `in_memory` already means "no database"; the design extends the guard to the
  one start-up call that lacked it. No server-side database write is added, and `no_confirm` is
  untouched.
- **§5/§6 Concurrency and shutdown** — untouched: the step runs on the main thread before any thread
  starts. The new tests always stop the processes they start.
- **§9 Queue security** — tests and drives give the key only through `HYPERSHELL_SERVER_AUTH`; no
  `-k` anywhere.
- **§10 Configuration** — the engine is still built once at import; `--no-db` relies on the SQLite
  engine connecting lazily (probed). The relay's private-database rebinding is unaffected (`relay`
  keeps its own `ensuredb()`).
- **§12 Conventions** — docs in the same commit as the behaviour; `.. versionchanged:: 2.9.0` on
  the prose page; no new option, so no completions or usage-snippet change; exit codes from
  `cmdkit.app.exit_status`; every new test tagged `unit` or `integration`; docs build with `-W`.
- **Schema/upgrade lock** — unchanged: `initdb()` already serializes concurrent launches
  (`upgradedb` lock, `converge` waits on it).

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

## 4. Rabbit holes (resolved)

- *Does anything besides the start-up call open the database under `--no-db`?* → No: a full
  `--no-db` server + client run never creates the SQLite file and creates no PostgreSQL table once
  the call is skipped ([01](research/01-startup-seam.md)).
- *Does R2's "same error, before it listens, create nothing" need new handling?* → No: `initdb()`
  orders `prepare` before the schema, and the shared exception mapping already gives exit 5 with the
  message; probed with a role refused `CREATE EXTENSION`
  ([02](research/02-timescale-path.md)).
- *Is a re-launch on an initialized, older, or populated-plain database safe?* → Yes: probed under
  both providers; no rows change, the pass is silent on a database in shape, an older schema is
  upgraded, a populated plain `task` keeps `hs initdb`'s warning ([02](research/02-timescale-path.md)).
- *Can R7's unit coverage run with no database server and no listener?* → Yes: the in-process
  recorder harness ([01](research/01-startup-seam.md)), red on `develop`.

## 5. Risks & open questions

For the plan gate:

1. **Test placement and CI.** A new module `tests/test_server_initdb.py`, importing
   `tests/test_timescale.py`'s fixtures by name, and a one-path addition (plus comment) to the CI
   PostgreSQL job in `.github/workflows/tests.yml`. Alternative: put the database cases in
   `tests/test_timescale.py` (already in CI) and the unit matrix in `tests/test_server.py`; rejected
   as scattering one regression over three files. The `postgres`-provider cases need only a server,
   so they also run on the pg13/pg18 entries.
2. **"The same error" (R2)** is read as the same message text and exit status; the log line's logger
   name differs (`hypershell.server` vs `hypershell.data`), as for every error the two commands share.
3. **`--no-db` with no database configured at all** (SQLite file unset or `:memory:`): today
   `hs server --no-db` exits 3, `Missing database configuration`; after the fix it starts. This
   follows from R5 ("without requiring … anything in the configured database"); without `--no-db`
   the refusal is unchanged (R4).
4. **`hs initdb`'s help** ("See also --initdb for the `hs cluster` command", `data/__init__.py` and
   `docs/_include/initdb_desc.rst`) is left as is: R6 does not name it and the non-goals keep
   `hs initdb` unchanged. Naming all three commands there is a one-line follow-up if wanted.
5. **Test runtime.** Each real-CLI case starts a server and a client (a few seconds each); about ten
   database cases plus one SQLite case. Bounded waits and a kill on every exit path keep a failure
   from hanging CI.
6. Not changed, by the non-goals: `hs cluster --no-db` still initializes a SQLite file; man pages
   are regenerated by `hs-release`; no 2.8 backport.

## 6. Verification strategy

- **Unit matrix** (always runs): `uv run pytest -m unit tests/test_server_initdb.py`. Red check at
  build: run the new module once against `develop`'s `server.py` before the seam lands and record
  the failing rows (`--initdb`, `--no-db`) in the commit body.
- **SQLite CLI drive** (no server): the module's integration case with `HS_TEST_POSTGRES` unset.
- **PostgreSQL / TimescaleDB CLI drives** (sandbox off, local socket):
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
  HS_TEST_TIMESCALE=community uv run --extra postgres pytest -rs tests/test_server_initdb.py` with no
  skip reported; the neighbouring `tests/test_timescale.py` stays green. The same red check against
  `develop`'s `server.py`.
- **Non-goal guard**: `git diff --quiet 6c46cbd -- src/hypershell/data src/hypershell/submit.py
  src/hypershell/cluster src/hypershell/task.py src/hypershell/relay`.
- **Docs**: `UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs
  sphinx-build -W --keep-going -q -E -b html docs $TMPDIR/docs-html` (sandbox off), plus greps that
  the stale sentences are gone and the marker is present; each R6 passage read against §2.1.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
