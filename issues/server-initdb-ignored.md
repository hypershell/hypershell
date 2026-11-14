---
status: adopted:server-initdb
kind: fix
appetite: small
lane: public
---

# `hs server --initdb` never initializes the database

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`hs server` accepts `--initdb` (`src/hypershell/server.py:1343-1346`, `dest='auto_initdb'`) and its
help promises to "auto-initialize tables if they don't already exist", but nothing reads
`auto_initdb`. `ServerApp.run` calls `ensuredb()` with no argument (`server.py:1381`), so
`ensuredb` (`src/hypershell/data/__init__.py`) takes its `auto_init=False` path: SQLite is
initialized as always, and every server provider gets only `checkdb()`. On a database that was
never initialized, `hs server --initdb` stops with `DatabaseUninitialized` ("Use 'initdb' to
initialize the database") instead of creating the tables. Under the `timescale` provider it also
never runs the TimescaleDB pass, which only `initdb()` runs.

The other two commands with the flag honour it: `hs submit` (`submit.py:1373`) and `hs cluster`
(`cluster/__init__.py:543`) both call `initdb()` when `auto_initdb` is set.

A probe under the `timescale` provider (recording which of `initdb`/`checkdb` each command calls,
2026-11-03) gives `hs server --initdb` and `hs server --forever --initdb` → `checkdb`, against
`hs submit --initdb` and `hs cluster --initdb` → `initdb`.

Origin: `70bcc63` ("Add central `ensuredb` implementation", 2023-04-03) replaced the server's
`if provider == 'sqlite' or self.auto_initdb: initdb()` with a bare `ensuredb()`, dropping the
flag. **Pre-existing**: present on `develop`.

Documentation that still describes the old behaviour for `hs server`:
`docs/_include/database.rst:106-107` and `docs/_include/database_alt.rst:9-10` ("at launch with
the `--initdb` command-line option"), `docs/_include/database.rst:94` (`--initdb` also optimizes
SQLite; launch paths call `initdb()` without `optimize`, so it does not), and
`docs/_include/server_help.rst:109-116` with the matching `server.py` help text. The TimescaleDB
section of `database.rst` was corrected in `timescale` (TS1) to say that `hs server` does not
initialize, even with `--initdb`; it needs revisiting when this is fixed.

## Why it was deferred

Found by the `timescale` (TS1) review, cycle 2 (finding F8), against new docs text that implied
`hs server --initdb` runs the TimescaleDB pass. The judge ruled to correct that text in TS1 and
not to change `server.py` there: fixing it changes PostgreSQL behaviour outside TimescaleDB, which
TS1's R2 holds identical to the base, and touches the server's start-up path, a high-blast-radius
file. So it is its own correctness fix.

## Outcome / vision

`hs server --initdb` does what its help says and what `hs submit --initdb` and
`hs cluster --initdb` already do: initialize (or upgrade) the database at launch, including the
TimescaleDB pass under the `timescale` provider. The docs that promise it become true again, and
the TimescaleDB section names `hs server` among the commands that initialize with `--initdb`.

## Sketch of the acceptance criteria

- **R1** — WHEN `hs server` is launched with `--initdb` against a server database, the server
  SHALL run the same initialization as `hs initdb` before scheduling, and SHALL start on a
  database that was never initialized.
- **R2** — WHEN `hs server` is launched without `--initdb`, behaviour SHALL be unchanged (the
  schema step only, refusing an uninitialized database).
- **R3** — The `--initdb` documentation for `hs server` (help text, `server_help.rst`,
  `database.rst`, `database_alt.rst`) SHALL match the behaviour, including the SQLite
  `PRAGMA optimize` claim.

## Notes

- Related: `spec/timescale/REVIEW.md` (cycle 2, F8, and the cycle-2 triage),
  `issues/server-db-test-suite.md` (a server-database suite would have caught this).
- Found by: `timescale` (TS1) review cycle 2, 2026-11-03.
