---
status: unshaped
kind: test
appetite: big
lane: public
---

# Run the whole test suite against a server database

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The test suite runs on SQLite only. `tests/conftest.py` isolates every test on a temporary SQLite
file by setting `HYPERSHELL_DATABASE_FILE`, and nothing lets the suite point at a server database
instead. CI's `postgres` job (`.github/workflows/tests.yml`) has five entries: plain PostgreSQL 13
and 18, and three TimescaleDB images (Community and Apache-only 2.31.0, and Community 2.24.0,
which predates the columnstore policy fix). Each runs two modules:
`tests/test_upgrade_postgres.py`, against legacy template databases, and `tests/test_timescale.py`,
whose scenarios drive `hs initdb` and, in one of them, `hs submit`, `hs cluster` and `hs list`. The
rest of the suite never runs on a server database: the scheduler, the writeback, retries, groups,
`restart` and most of the CLI are exercised on SQLite only, while PostgreSQL and TimescaleDB are
supported providers with different locking, typing and transaction behaviour.

## Why it was deferred

The `timescale` seed's R9 asked for "the full test suite to run green with a timescale provider".
While that seed was being shaped into `spec/timescale/` (TS1), the request turned out to need a
harness that does not exist, which would break TS1's appetite. The foreman, as judge under the
maintainer's delegation, ruled on 2026-11-02 that TS1's CI job runs the upgrade tests plus a new
TimescaleDB scenario module, and that running the whole suite against a server database becomes
its own seed: this one.

## Outcome / vision

The whole suite can run unchanged against a PostgreSQL or TimescaleDB server, selected by an
environment variable, with each test isolated (its own database or schema) and torn down
afterwards. CI runs it on at least one PostgreSQL version and one TimescaleDB image, alongside the
SQLite matrix.

## Sketch of the acceptance criteria

- **R1** — WHEN a server-database URL is supplied to the test run, the suite SHALL run every test
  against that server, with no test sharing state with another, and SHALL behave exactly as today
  when none is supplied.
- **R2** — Tests that are SQLite-specific (pragmas, file vacuum and backup, in-memory behaviour)
  SHALL be marked and skipped on a server database, with the reason shown.
- **R3** — CI SHALL run the suite against PostgreSQL and against a pinned TimescaleDB image.

## Notes

- Related: `spec/timescale/` (R9), `tests/conftest.py` (`isolate_environment`, the autouse
  fixture), `tests/test_upgrade_postgres.py` (the existing `HS_TEST_POSTGRES` opt-in and skip
  pattern), `.github/workflows/tests.yml` (`postgres` job).
- Open questions for shaping: isolation by database versus by schema; how `data/core.py`'s
  import-time engine creation interacts with per-test databases; CI time budget.
- Filed: 2026-11-02, from `timescale` (TS1) shaping.
