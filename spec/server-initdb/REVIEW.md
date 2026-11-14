# REVIEW — `hs server --initdb` initializes the database (server-initdb, cycle 1)

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 448add4ca0d696ababd63f06d90e962d496196fc  ·  **Base:** `6c46cbd` (`origin/develop`)  ·  **Date:** 2026-11-14
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** single blind correctness reviewer (no `debate`, no `completeness` sub-pass), plus an
  orchestrator re-run of the new tests on the branch and of the unit tests on a `develop` export.

## Pre-flight

- **Base.** `TECH.md` records `base: develop`. The review graded `origin/develop` at `6c46cbd`,
  fetched first; `git merge-base HEAD origin/develop` = `6c46cbd`, the commit the GOAL names.
- **Build state.** `TECH.md` `status: in_review`, both phases `done`.
- **Contract drift.** `git log --oneline 6c46cbd..HEAD -- spec/server-initdb/GOAL.md` shows the
  shaping commit `1714000` and the GOAL-gate lock `5c8a7c7`, both before planning. The lock only
  resolves the three open questions (R5 in scope, server only; the `PRAGMA optimize` note is
  corrected; no prompt). The contract did not move during the build.
- **Contract readings applied** (gate rulings, passed to the reviewer): R5 is server only; the SQLite
  note is corrected, not made true; no confirmation prompt; R2's "same error" is the same message
  text and exit status (the logger name may differ); `hs server --no-db` with no database configured
  at all starts in memory; `hs initdb`'s own help is unchanged this cycle.
- **Diff under review** (`git diff 6c46cbd...HEAD -- . ':(exclude)spec/'`): `server.py` (+6/−1:
  `ServerApp.check_database`, `ensuredb(auto_init=self.auto_initdb)` unless `--no-db`), docs
  (`server_help.rst`, `database.rst`, `database_alt.rst`, `cli/server.rst`), `tests/test_server_initdb.py`
  (new), the PostgreSQL CI job, and the seed/`ROADMAP.md` status lines.

## Verification run

Local PostgreSQL 18.7 with TimescaleDB 2.32.0 (community), Python 3.14.6, macOS. Real `hs server` and
`hs client` launches used throwaway sites, the key in `HYPERSHELL_SERVER_AUTH` only, and fresh
databases per case, all dropped afterwards.

- `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE=community uv run --extra postgres pytest -q -rs tests/test_server_initdb.py`
  → **57 passed** (48 unit, 9 integration; reviewer and orchestrator, separately).
- Same file against a `git archive` export of `origin/develop` with the branch's test file copied in
  → reviewer: **31 failed, 26 passed** (with PostgreSQL); orchestrator, unit only: **24 failed, 24
  passed**. The failures are every `--initdb` and `--no-db` case on `postgres`/`timescale`, every
  SQLite `--no-db` case, the TimescaleDB refusal test, `--no-db` with no database configured, and all
  seven `--initdb`/`--no-db` CLI drives. The passes are the cases develop already gets right: no
  flag, SQLite `--initdb`, `--autoscaling --no-db` (refused before the database step), and the two
  "without flag refused" drives.
- Mutation runs on scratch copies of HEAD (`test_server_initdb.py`): `ensuredb(auto_init=True)`
  unconditionally → 10 fail; a bare `ensuredb()` → 11 fail; the `in_memory` guard removed → 13 fail.
- Neighbouring suites on the branch: `test_relay_retire.py`, `test_relay_topology.py`,
  `test_server.py`, `test_topology_server.py`, `test_restart.py`, `test_initdb.py` → 146 passed;
  `test_timescale.py`, `test_zone_postgres.py`, `test_server_initdb.py` in one session → 155 passed,
  1 skipped, 1 failed: `test_timescale.py::test_legacy_database_upgraded_not_converted`, which needs a
  Docker-built template this host lacks (environmental, unrelated to the diff).
- Real-CLI launch matrix (below), 15 provider × mode rows × 3 flags, plus: SQLite existing file ×
  `--no-db` in four modes; `postgres --no-db` against a database that does not exist and against a
  closed port; the R2 refusal in four modes; the R3 older-schema and TimescaleDB-drift runs.
- Docs: `UV_PROJECT_ENVIRONMENT=$TMPDIR/… uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs …`
  → exit 0, no Sphinx warnings.

### Launch matrix (real `hs server` + `hs client`, branch HEAD)

**OK** = listened, the client ran its tasks, and the database is in the state the contract requires.
"refused 5" = exit 5 with `DatabaseUninitialized` and no table created; "refused 2" = argument error.
Forever and autoscaling servers were stopped with SIGINT (exit 4) after the client finished.

| provider / mode | no flag | `--initdb` | `--no-db` |
|---|---|---|---|
| sqlite FILE | OK, file created | OK, file created | OK, no file |
| sqlite stdin | OK, file created | OK, file created | OK, no file |
| sqlite `--forever` | OK | OK | OK, no file |
| sqlite `--restart` | OK | OK | OK, no file |
| sqlite `--autoscaling` | OK | OK | refused 2 |
| postgres FILE | refused 5 | OK, 4 tables | OK, 0 tables |
| postgres stdin | refused 5 | OK | OK, 0 tables |
| postgres `--forever` | refused 5 | OK | OK, 0 tables |
| postgres `--restart` | refused 5 | OK | OK, 0 tables |
| postgres `--autoscaling` | refused 5 | OK | refused 2 |
| timescale FILE | refused 5, no extension | OK, hypertable, 1 policy job | OK, 0 tables, no extension |
| timescale stdin | refused 5 | OK, hypertable | OK, no extension |
| timescale `--forever` | refused 5 | OK, hypertable | OK, no extension |
| timescale `--restart` | refused 5 | OK, hypertable | OK, no extension |
| timescale `--autoscaling` | refused 5 | OK, hypertable; served a real `hs relay` site (3 tasks) | refused 2 |

Not run here: a relay site behind a `postgres` or `sqlite` autoscaling server; the Apache-only and
pre-2.25 TimescaleDB editions (CI's matrix entries); the "extension absent", "not preloaded" and
"too old" refusals (this host cannot produce them; they raise the same `TimescaleError` as the
refusal that was run).

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `server.py` `ServerApp.check_database` (b414753), called from `run` before the mode branch | Real CLI, fresh `postgres` and `timescale` databases, all five modes with `--initdb`: four tables, listened, client completed tasks; `--autoscaling --initdb` also served a real relay site; `test_postgres_initdb_file`/`_forever`, `test_timescale_initdb_file`, `test_database_step` | ✅ |
| R2 | same call, `ensuredb(auto_init=True)` → `initdb()` (`timescale.prepare` + `converge`) | Shape: `task` is a hypertable with one columnstore policy job in every mode. Refusal (a role that may not create the extension): `hs server --initdb` in FILE, `--forever`, `--restart`, `--autoscaling` and `hs initdb --yes` all exit 5 with the identical `TimescaleError: Cannot create the TimescaleDB extension in database '…': permission denied. …` line; the port never accepted, no "Server listening", no tables, no extension. `test_timescale_refusal_matches_initdb`, `test_timescale_refusal_stops_launch` | ✅ |
| R3 | same call | Re-launch on a current schema: rows unchanged (integration tests). Older PostgreSQL schema (emulated 2.8.3 shape, `UNIQUE` retry-chain constraints restored, three completed/failed rows): `hs server --initdb FILE` logged the upgrade, the three rows were identical on every original column, and the schema equals `hs initdb --yes` on a twin database. TimescaleDB: a plain-initialized database was converted; with `chunk_interval`/`compress_after` drift the server logged the same drift lines as `hs initdb`, rows unchanged, and a following `hs initdb` logged nothing. Every launch ran non-interactively (stdin a file, pipe or `/dev/null`) and none prompted or refused | ✅ |
| R4 | same call, `auto_init=False` without the flag | Without the flag, fresh `postgres`/`timescale` → exit 5 `DatabaseUninitialized` in all five modes, no tables, no extension; a plain-initialized database under `timescale` runs with no TimescaleDB pass; SQLite with and without `--initdb` creates the file; `--initdb --no-db` still refused (exit 2). `test_database_step` "none" cells, `test_*_without_flag_refused` | ✅ |
| R5 | same call, skipped when `in_memory` | `--no-db` on fresh `postgres`/`timescale`: all four reachable modes run tasks, tables stay empty, no extension; also against a database that does not exist and a closed port. SQLite: no file created in any mode; an existing initialized file keeps size, mtime and sha256 after FILE, stdin, `--forever` and `--restart`, with no `-wal`/`-shm`/`-journal` file. Scheduler, heartbeat, receiver, confirm and shutdown keep their existing `in_memory` guards (read). `test_no_db_leaves_sqlite_file_alone`, `test_postgres_no_db`, `test_timescale_no_db`, `test_no_database_configured` | ✅ |
| R6 | `server_help.rst`, `database.rst` (SQLite note, Initialization, TimescaleDB "Then initialize…", "Each step…", "Only…"), `database_alt.rst`, `cli/server.rst` (b414753) | Read against the observed behaviour above: all match. The SQLite note names `hs initdb` alone, which matches the code (only `InitDBApp` passes `optimize=True`). `.. versionchanged:: 2.9.0` sits beside the include in `docs/cli/server.rst`. The `server.py` help lines ("Auto-initialize database.", "Disable database (submit directly to clients).") are unchanged and still accurate. Docs build passes under `-W` | ✅ |
| R7 | `tests/test_server_initdb.py` (b414753, 448add4); PostgreSQL CI job runs it | Red on the `develop` export, green on HEAD (above); unit matrix covers 3 providers × 5 modes × 3 flags with no database server; the CLI drives skip without `HS_TEST_POSTGRES`; mutations caught | ✅ |

Unmapped changes (possible scope creep): none. The seed and `ROADMAP.md` status lines
(`adopted:server-initdb`) are cycle bookkeeping consistent with other adopted entries; the
`tests.yml` change adds the new file to the PostgreSQL job (R7), and its comment is accurate
(`postgres`-provider cases need only a server; `timescale` cases are gated on the TimescaleDB entries).

Not covered by a test, verified by hand above: R3's older-schema clause and R5's "existing SQLite
file not altered" clause. The GOAL's evidence plan does not require either as a test, so neither is
a finding.

## Findings

None. No CONFIRMED and no PLAUSIBLE findings.

Candidates the reviewer tried and dropped under the refutation protocol:

- **`OperationalError` presentation differs.** `hs initdb` maps it to a one-line message, `hs server`
  to a traceback, both exit 5. It arises only on connection failures, which are not among R2's
  TimescaleDB refusal conditions (those all raise `TimescaleError`, handled identically by both
  apps), and it predates the diff.
- **No `versionchanged` in `docs/manual.rst`** beside its `server_help.rst` include. The manual omits
  the other `cli/` markers too (`:pr:` 78, 50, 99); the marker sits beside the include in
  `docs/cli/server.rst`, as AGENTS.md § Docs asks.
- **`database.rst` Tuning: "Changes take effect at the next `hs initdb`"** does not mention launches
  with `--initdb`. Unchanged text, already silent on `hs submit`/`hs cluster`, and not false; the
  passages R6 names list `hs server` correctly.

Invariants: §4, the `in_memory` guard is on the server's only start-up database call and the
existing guards downstream are untouched; §10 untouched; §12, docs land in the same commit as the
code (b414753), no CLI option changed so the completions and help snippets' option lists stay as
they are, every new test is tagged `unit` or `integration`, tests use `exit_status` constants, and
the docs build passes under `-W`.

## Human-gate triggers

- **Not triggered.** The rule fires on a CONFIRMED finding in the high-blast-radius core or on a
  security/DB-lifecycle invariant; there are none. The diff does touch `server.py` (the start-up
  database call), and the review weighted it accordingly: every launch mode, provider and flag was
  driven through the real CLI, including an autoscaling server with a live relay site.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
