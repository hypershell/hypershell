---
status: unshaped
kind: test
appetite: small
target: 2.9.x
lane: public
---

# The serial test suite outgrows the CI time limit

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The `tests` job runs the whole suite serially, once for each Python version (3.11 to 3.14). Each
cycle adds tests, and the run time keeps growing:

- Before `bounded-inputs` (#125): under 45 minutes.
- On #125: Python 3.14 hit the 45-minute limit twice, and the limit was raised to 60.
- On `update-typed-columns` (#126): 45 to 56 minutes, and Python 3.13 hit 60. The limit was raised
  to 90.

Raising the limit each cycle slows every PR and only puts off the next failure. When a job hits
the limit, the log is cut short and the failure gives no test result to read.

## Outcome sought

Each Python version's run finishes well inside its limit, with room for the suite to keep
growing. Every test still runs on every Python version, on every push and pull request.

## Notes

- The suite runs serially by design: tests start servers, bind ports and share site state.
  Running tests in parallel inside one job (`pytest-xdist`) would need that isolation checked
  first. Splitting the tests into shards, each its own CI job, avoids that.
- **Direction approved by the maintainer (2026-11-25):**
  - The matrix becomes Python version × shard, every shard on every version, on every push and
    pull request.
  - Shards are named chunks of the suite by area, so a job's name says what failed, for example
    `tests (3.13, update)`.
  - A catch-all shard runs every test file not named in another shard, so a new file is never
    skipped by accident.
  - Shaping takes a timing pass to cut areas of roughly equal length, about 15 to 20 minutes each.
  - Shaping also weighs the cost of setup in each job and the account's limit on concurrent jobs.
- Approaches considered:
  - a fixed split of test files into groups in the matrix;
  - a split by recorded duration (for example `pytest-split`, with its timings file committed);
  - finding and slimming the slowest tests, such as the range and update matrices.
- The `postgres` job runs a list of files and may need the same split. Its limit went from 20 to
  45 minutes on #126 and then to 75, after the `timescale` and `timescale-floor` entries were
  stopped at 45 while still passing. A quick win: that job runs the SQLite cases of the
  `test_update_*.py` files too (for example `test_near_miss_refused[sqlite-…]`), and the `tests`
  job already runs those. Selecting only the PostgreSQL cases there would cut its time without
  losing coverage.
- CI only: no change to the package or its interface.
