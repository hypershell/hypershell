---
status: unshaped
kind: fix
appetite: small
target: 2.9.0
lane: public
---

# On PostgreSQL, a task cannot ask for 2 GB of memory or more

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

`Task.memory` holds bytes (`hs submit -m` parses with `parse_bytes`) in a plain `INTEGER` column
(`data/model.py:349`, and `:250` on `master`). That is 64-bit on SQLite but 32-bit on PostgreSQL,
whose largest value is 2,147,483,647 bytes, just under 2 GiB. A task that declares more is refused
by the database at insert. Memory per task on an HPC node is commonly several to hundreds of GB, so
on PostgreSQL the `memory` resource is unusable for exactly the tasks that need it.

Reproduced on `develop` (ecbfbf46) against PostgreSQL 18, with a fresh `hs initdb --yes`:

- `hs submit -m 1GB one.txt` submits.
- `hs submit -m 4GB four.txt` logs `DataError: (psycopg.errors.NumericValueOutOfRange) integer
  out of range` from `DatabaseCommitter`, and then **hangs**: it was still running when a 90 s
  alarm killed it (exit 142). The same with `-m 8GB`.
- Retrying `four.txt` with `-m 2GiB` is then refused: `File … was already submitted as source …;
  pass --repeat …`, though none of its tasks was stored. The source row was committed before the
  tasks.

The same overflow reaches every submitter that sets memory: `hypershell-mcp`'s `submit_tasks`
fails the same way (its PostgreSQL test run, 2026-11-21, marks it as a strict expected failure),
and `nf-hypershell` passes Nextflow's `memory` directive through, so a process with
`memory '4 GB'` would fail on an attached PostgreSQL site.

None of the other integer columns is at risk in practice: `timeout`, `waited` and `duration` are
seconds, `group` and `part` are counters, and `cores`, `exit_status` and `attempt` are `SMALLINT`
on purpose. `memory_max` is a `FLOAT`.

**Pre-existing**: the column has been `INTEGER` on the 2.8 line too.

## Why it was deferred

Found from outside the repository, while running `hypershell-mcp`'s tests on PostgreSQL for the
first time. The fix needs a schema change that the upgrade step does not yet make
(`data/upgrade.py` only adds; it "never drops, renames, or retypes"), so it is not a one-line patch.

## Outcome / vision

On every provider a task can declare as much memory as a node has, and a database created by an
earlier release is upgraded to match.

## Sketch of the acceptance criteria

- **R1** — The `memory` column SHALL hold at least a 64-bit byte count on every provider:
  `BIGINT` on PostgreSQL and TimescaleDB.
- **R2** — WHEN a PostgreSQL or TimescaleDB database created by an earlier release is opened, the
  upgrade step SHALL retype `task.memory` to `BIGINT` in place, under the existing schema-upgrade
  lock, without losing a row.
- **R3** — A task submitted with `-m 4GB` SHALL be stored and scheduled on PostgreSQL, and
  `hs list` SHALL show its memory.
- **R4** — A test SHALL submit more than 2 GiB of memory on a server database, if the suite has
  one (see `issues/server-db-test-suite.md`); until then, the upgrade step's retype SHALL be tested
  as SQL against a PostgreSQL dialect.

## Notes

- Retyping on PostgreSQL rewrites the table and holds an exclusive lock while it does. On a large
  `task` table that is a long pause, so the upgrade should log what it is doing. On a TimescaleDB
  hypertable, check whether `ALTER COLUMN … TYPE` works on compressed chunks before relying on it.
- SQLite needs no change: its `INTEGER` already holds 64 bits.
- Should 2.8.x get a patch? A 2.8 user on PostgreSQL has the same limit, but a retyping migration
  in a patch release is a large step for one; it is the maintainer's call.
- The hang after the error is the committer death of
  `issues/database-locked-during-ingest-hangs.md`, by a new trigger. The source recorded for a
  submission that stored nothing is the all-or-nothing gap in `issues/ingest-robustness.md`.
- Related: `issues/server-db-test-suite.md`; `hypershell/hypershell-mcp`
  (`tests/conftest.py`, `memory_overflows`).
- Found by: `hypershell-mcp`'s first PostgreSQL test run, 2026-11-21; reproduced with `hs submit` on
  `develop` the same day.
