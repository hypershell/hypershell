---
status: unshaped
kind: fix
appetite: small
target: 2.9.x
lane: public
---

# On a fresh SQLite site, the Python API's `run_local` fails on a missing table and then hangs

> **Candidate, not a contract.** `/hs-feature` promotes this into `spec/{slug}/GOAL.md`, where
> appetite, non-goals and R-IDs get negotiated. Do not copy it verbatim.

## Problem

The commands create the schema before they submit: `hs cluster` and `hs submit` call `initdb()` on
SQLite (`src/hypershell/cluster/__init__.py:651-652`, `src/hypershell/submit.py:1775-1776`), and
`hs server` calls `ensuredb()` (`src/hypershell/server.py:2002-2005`). The Python API does not.
`run_local` (`src/hypershell/cluster/local.py:31-51`) builds a `LocalCluster`, and nothing on that
path creates the tables. On a site whose SQLite database has never been initialized, with a
database (not `in_memory=True`, which the docstring's example uses):

- The submitter's committer dies on its first insert with
  `sqlite3.OperationalError: no such table: task` (`DatabaseCommitter.commit`, `submit.py:746`;
  `Task.add_all`, `src/hypershell/data/model.py:314`), and an exception log is written to the site.
  No task runs.
- The run then hangs. The submit thread waits for room on its full local queue (`put_end`,
  `submit.py:892`), the server waits for the submitter (`wait_submitter`, `server.py:1364`), and
  `run_local` waits in `join_waking` and never returns.
- `LocalCluster.stop()` hangs too: the submit thread's `stop` puts its end marker on the same full
  queue (`submit.py:878`), and nothing drains it.

Probed on `develop` `2af6a734` (macOS, Python 3.14), with `HYPERSHELL_SITE` and
`HYPERSHELL_DATABASE_FILE` pointing at a new directory and no configuration file:
`run_local(['echo AAA', 'echo BBB'], num_threads=1)` logged the `no such table: task` exception and
was still running when stopped at 25 s; `LocalCluster.new(...)` started the same way and given
`stop()` after 8 s had not returned from `stop()` 20 s later.

The hang is the committer-death hang of
[`database-locked-during-ingest-hangs.md`](database-locked-during-ingest-hangs.md): any error that
kills the committer leaves the run waiting. What is new here is that a library caller on a fresh
site reaches it every time, from a missing schema rather than a lock.

## Why it was deferred

Found by `update-client-retry` at plan time (2026-11-22), outside its scope; that cycle's drive runs
`hs initdb` first. **Pre-existing**: `cluster/local.py` creates no schema on `develop` or on the 2.8
line (`master`), read from the code; the probe ran on `develop` only.

## Outcome / vision

A library caller with a database gets what the commands give: on SQLite the schema is created
before the first insert, or the call fails at once with an error that says to initialize the
database. Either way it never hangs.

## Sketch of the acceptance criteria

- **R1** — WHEN `run_local` (and `LocalCluster`, `run_cluster`, `run_ssh` where the same path
  applies) is given a database on a fresh SQLite site, the call SHALL either create the schema
  before submitting, as `hs cluster` does, or raise a clear error before any thread starts.
- **R2** — IF the submitter's committer dies during a library run, THEN `run_local` SHALL raise
  and `stop()` SHALL return, within a bounded time.
- **R3** — A test SHALL cover R1 on a fresh SQLite site and fail on `2af6a734`.

## Open questions for shaping

- Create the schema (what the commands do on SQLite) or refuse? Creating it matches the CLI; on
  PostgreSQL the commands only check unless `--initdb` is given.
- R2 overlaps `database-locked-during-ingest-hangs`: one cycle for both, or this one first with R2
  left to that seed?

## Notes

- Related: [`database-locked-during-ingest-hangs.md`](database-locked-during-ingest-hangs.md) (the
  same hang after a committer death).
- Found by: `update-client-retry` `hs-plan` (2026-11-22); confirmed by a probe on `develop`
  `2af6a734`.
