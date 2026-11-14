# GOAL — `hs server --initdb` initializes the database

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** server-initdb
- **kind:** fix
- **appetite:** small

## Problem

`hs server` accepts `--initdb`, and its help and the docs say it initializes the database at
launch, but on PostgreSQL and TimescaleDB it does nothing. Someone who points a fresh server
database at `hs server --initdb` gets `DatabaseUninitialized: Use 'initdb' to initialize the
database` and exit status 5, and has to run `hs initdb` by hand first. Under the `timescale`
provider the flag also never gives the `task` table its hypertable shape. `hs submit --initdb` and
`hs cluster --initdb` do what the flag says, so the three commands disagree about one option with
one help line. The defect is **pre-existing** on `develop` and in the 2.8 line: `70bcc63`
("Add central `ensuredb` implementation", 2023-04-03) replaced the server's
`if provider == 'sqlite' or self.auto_initdb: initdb()` with a bare `ensuredb()` and dropped the
flag.

Evidence on `develop` at `6c46cbd` (re-verified during shaping, 2026-11-13; the seed's line numbers
had drifted):

- The flag is defined at `src/hypershell/server.py:1706-1709` (`dest='auto_initdb'`, in a mutually
  exclusive group with `--no-db`) and is never read. `ServerApp.run` calls `ensuredb()` with no
  argument at `server.py:1745`, before it branches into `--autoscaling`, `--forever`, or the
  FILE/`--restart` path, so every scheduling mode shares the one call.
- `ensuredb` (`src/hypershell/data/__init__.py:121-132`) runs `initdb()` on SQLite always and
  otherwise `checkdb()`, unless called with `auto_init=True`. `checkdb` (`:114-118`) refuses a
  database with no `task` table and otherwise upgrades the schema in place; `initdb` (`:87-96`)
  also runs the TimescaleDB pre-flight (`timescale.prepare`) and hypertable pass
  (`timescale.converge`) under the `timescale` provider.
- `hs submit` (`submit.py:1402-1410`, `check_database`) and `hs cluster`
  (`cluster/__init__.py:555-562`, `__enter__`) call `initdb()` when `--initdb` is given, and neither
  prompts. `hs initdb` prompts for PostgreSQL unless `--yes`.
- A shaping probe (2026-11-13, one fresh database per case, local PostgreSQL 18 with TimescaleDB):
  `hs server --initdb` with a task file and with `--forever` exits 5 with `DatabaseUninitialized`
  under both `postgres` and `timescale` and creates no table, while `hs submit --initdb` creates the
  four tables and, under `timescale`, the `task` hypertable.

The same probe found an adjacent defect in the same start-up call, not named by the seed:
**`hs server --no-db` also touches the configured database.** On a fresh PostgreSQL or TimescaleDB
database it exits 5 with `DatabaseUninitialized`, although `--no-db` means the server never uses
the database; against a SQLite file it creates the tables. `hs cluster --no-db` starts on the same
fresh PostgreSQL database (its `__enter__` skips `checkdb` under `--no-db`), though on SQLite it
too still runs `initdb`. This was first recorded as "Hole C" in
`spec/schema-upgrade-step/research/02-upgrade-seam.md` and left unbuilt. Whether this cycle takes
the server half is R5, ruled in scope below.

The documentation describes the flag as working for `hs server`: `docs/_include/server_help.rst:110-117`
("auto-initialize tables if they don't already exist"), `docs/_include/database.rst:111-112` and
`docs/_include/database_alt.rst:9-10` ("or at launch with the `--initdb` command-line option").
The TimescaleDB section of `database.rst` (`:206-208`, `:257-260`) was corrected by `timescale`
(TS1) to say that `hs server` does **not** initialize, even with `--initdb`; that text becomes
wrong once this is fixed. Separately, `database.rst:94` says launching with `--initdb` also runs
`PRAGMA optimize` on SQLite; no launch path does (each calls `initdb()` without `optimize`), only
`hs initdb` itself.

## Outcome / vision

`hs server --initdb` does what its help says and what `hs submit --initdb` and `hs cluster --initdb`
already do: it initializes, or upgrades, the database at launch, including the TimescaleDB pass
under the `timescale` provider, in every scheduling mode. Without the flag nothing changes. The docs
say the same thing everywhere, and the TimescaleDB section names `hs server` among the commands
that initialize with `--initdb`.

## Acceptance criteria (the contract)

- **R1** — WHEN `hs server` is launched with `--initdb` against a PostgreSQL or TimescaleDB database
  that was never initialized, the server SHALL create the HyperShell tables and start (listen and
  schedule) instead of exiting with `DatabaseUninitialized`. This SHALL hold in every scheduling
  mode: a task FILE or `<stdin>`, `--forever`, `--restart`, and `--autoscaling`.
- **R2** — WHILE the provider is `timescale`, WHEN `hs server` is launched with `--initdb`, the
  database SHALL afterwards have the shape `hs initdb` gives it (the `task` hypertable, its
  columnstore and policy), and IF the server cannot take that shape (the conditions under which
  `hs initdb` refuses), THEN `hs server` SHALL exit with the same error `hs initdb` gives, before it
  listens, and create nothing.
- **R3** — WHEN `hs server` is launched with `--initdb` against a database that is already
  initialized, whether its schema is current or from an earlier release, the server SHALL leave the
  database in the state `hs initdb` would (upgraded in place, no task changed, under `timescale`
  converged to the configured shape) and start. The server SHALL NOT prompt for confirmation, as
  `hs submit --initdb` and `hs cluster --initdb` do not.
- **R4** — WHEN `hs server` is launched without `--initdb`, its database behaviour SHALL be
  unchanged: on PostgreSQL and TimescaleDB a never-initialized database is still refused with the
  same message and exit status, an initialized one is still upgraded in place, and no TimescaleDB
  pass runs; on SQLite, with or without `--initdb`, the database is initialized automatically as
  today.
- **R5** — WHEN `hs server` is launched with `--no-db`, the server SHALL start without requiring,
  creating, or upgrading anything in the configured database: a never-initialized PostgreSQL or
  TimescaleDB database SHALL NOT stop it, and a SQLite database file SHALL NOT be created or
  altered by it.
- **R6** — The documentation of `hs server --initdb` SHALL match the behaviour: the `hs server` help
  text and `docs/_include/server_help.rst`, the Initialization section of `docs/_include/database.rst`
  and `docs/_include/database_alt.rst`, and the TimescaleDB section of `database.rst`, which SHALL
  name `hs server` with `--initdb` among the commands that run the initialization. The SQLite note
  SHALL no longer claim that launching with `--initdb` runs `PRAGMA optimize` (see Clarifications).
  The changed `hs server` passage SHALL carry `.. versionchanged:: 2.9.0`.
- **R7** — The change SHALL ship with a regression test that fails on `develop` at `6c46cbd` and
  passes with the fix: it drives `hs server --initdb` through the command line against a fresh
  PostgreSQL database and a fresh TimescaleDB database (skipped when `HS_TEST_POSTGRES` is unset),
  plus unit coverage, needing no database server, that the flag reaches initialization in each
  scheduling mode and that its absence does not (R4).

## Evidence plan

- **Unit (always runs).** Record which of `initdb`/`checkdb` a `hs server` launch reaches, for each
  scheduling mode with and without `--initdb` (and `--no-db`, if R5 is taken), under the `postgres`
  and `timescale` providers, without starting a listener. Red on `6c46cbd`: every server case
  records `checkdb`.
- **CLI drive (`temp_site`, sandbox off, local PostgreSQL 18 + TimescaleDB 2.x).** With
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres` and
  `HS_TEST_TIMESCALE=community`, create a fresh database per case and launch the real `hs server`
  with `HYPERSHELL_SERVER_AUTH` set (never a key on the command line): `--initdb` with a task file,
  `--initdb --forever`, and (if R5) `--no-db`; assert tables exist, under `timescale` that `task` is
  a hypertable, and that a client completes a task. Re-launch with `--initdb` on the initialized
  database and assert nothing changes (R3); launch without the flag on a fresh database and assert
  the existing refusal (R4).
- **Docs.** The docs build passes with `-W`; R6's passages are checked by reading them against the
  behaviour above.

## Non-goals (no-gos)

- Changing `hs initdb`, `hs submit`, or `hs cluster`, including `hs cluster --no-db` still running
  `initdb` on a SQLite file (the cluster half of "Hole C"); that stays deferred work.
- Changing what any other command does at start-up (`hs info`, `hs wait`, `hs list` and the rest
  keep calling `ensuredb()` as today).
- Making a launch with `--initdb` run SQLite's `PRAGMA optimize` (the docs are corrected instead,
  as ruled below).
- Changing the server's automatic in-place schema upgrade without `--initdb` (R4 keeps it).
- Regenerating `share/man/` pages or editing the GitHub release notes; both are `hs-release`'s job
  at the next pre-release, which also drops the 2.9.0a8 known issue for this defect.
- A backport to the 2.8 line; that would be a separate hotfix.
- The whole-suite run against a server database (`issues/server-db-test-suite.md`).

## Clarifications

- **Q:** Kind and appetite? — **A:** `fix`, `small`, as the seed's frontmatter and the foreman's
  brief both say (shaping, 2026-11-13).
- **Q:** Does the seed still hold against current `develop`? — **A:** Yes in substance. Drift: six of the eight
  `file:line` locations it cites have moved (`server.py:1343-1346`/`:1381` → `:1706-1709`/`:1745`;
  `submit.py:1373` → `:1402-1410`; `cluster/__init__.py:543` → `:555-562`;
  `database.rst:106-107` → `:111-112`; `server_help.rst:109-116` → `:110-117`;
  `database.rst:94` and `database_alt.rst:9-10` hold), and so have the
  `ROADMAP.md` entry's (`:1626-1629`, `:1664`). The seed says the server "only checks the schema";
  `checkdb` also upgrades it in place. The seed attributes "auto-initialize tables if they don't
  already exist" to the `server.py` help as well as `server_help.rst`; the `server.py` help says
  only "Auto-initialize database." (shaping, 2026-11-13).
- **Q:** Should `hs server --initdb` prompt for confirmation, as `hs initdb` does on PostgreSQL? —
  **A:** No. The flag is the consent, a server launch is often non-interactive, and `hs submit
  --initdb` and `hs cluster --initdb` do not prompt (R3) (shaping, 2026-11-13; confirmed by the foreman at the GOAL gate).
- **Q:** Does this cycle also fix `hs server --no-db` touching the configured database (R5)? —
  **A:** Yes, server only (foreman at the GOAL gate, 2026-11-13). It is the same start-up call one line
  away, the shaping probe shows it as a user-visible failure (`hs server --no-db` exits 5 on a
  fresh PostgreSQL database that `hs cluster --no-db` runs against), and leaving it means the fix
  touches this call twice in two cycles. The cluster's SQLite `--no-db` path stays a non-goal.
- **Q:** The SQLite note says launching with `--initdb` also runs `PRAGMA optimize`; no launch path
  does. Correct the docs, or make launches optimize? — **A:** Correct the docs (R6) (foreman at the GOAL gate, 2026-11-13): it is the smaller change, it keeps `hs submit` and `hs cluster` as they are, and
  `PRAGMA optimize` on every launch is a behaviour change nobody asked for. The note then names
  `hs initdb` alone.
- **Q:** Version marker? — **A:** The behaviour differs from the stable 2.8 line, so the changed
  `hs server` passage carries `.. versionchanged:: 2.9.0` with one short clause; `hs-release` adds
  the `:pr:` link (`AGENTS.md` § Docs) (shaping, 2026-11-13).

**Ruled at the GOAL gate** (2026-11-13): approved by the foreman, gate judge under the maintainer's
mandate. R5 is in scope, server only; the SQLite `PRAGMA optimize` note is corrected rather than
made true; no confirmation prompt. The contract is locked; `hs-plan` designs against it.

## Related materials

- Seed: [`issues/server-initdb-ignored.md`](../../issues/server-initdb-ignored.md)
- `spec/timescale/REVIEW.md` (cycle 2, F8, and its triage), which found the defect and corrected
  the TimescaleDB docs to describe it.
- `spec/schema-upgrade-step/research/02-upgrade-seam.md` §5–6 and `PLAN.md` follow-ups ("Hole C",
  `--initdb` ignored).
- Source: `src/hypershell/server.py` (`ServerApp`), `src/hypershell/data/__init__.py` (`initdb`,
  `checkdb`, `ensuredb`, `InitDBApp`), `src/hypershell/submit.py` (`check_database`),
  `src/hypershell/cluster/__init__.py` (`ClusterApp.__enter__`).
- Docs: `docs/_include/server_help.rst`, `docs/_include/database.rst`,
  `docs/_include/database_alt.rst`, `docs/cli/server.rst`.
- Tests: `tests/test_timescale.py` (`HS_TEST_POSTGRES`/`HS_TEST_TIMESCALE` conventions),
  `tests/test_upgrade_postgres.py`, `tests/conftest.py` (`temp_site`).
