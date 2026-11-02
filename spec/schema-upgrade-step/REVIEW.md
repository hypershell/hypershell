# REVIEW — Open 2.8 databases in 2.9: an idempotent schema-upgrade step

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 1a466d6 (code head 9aee1c7; 1a466d6 touches only `spec/`)  ·  **Base:** `origin/develop` (b819de6)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

The base is `origin/develop`: the local `develop` ref had diverged from it, so the skill's default
`develop` would have graded the wrong diff. The graded diff was
`git diff origin/develop...HEAD -- . ':(exclude)spec/'`. The GOAL has one commit on the branch (its
shaping commit, 53bd6ee), so the contract did not drift during the build.

## Verification run

Commands actually executed and their outcomes (the spine of the review):

- `uv run pytest -rxX` (blind reviewer) → 532 passed, 28 skipped, 0 failed. 27 skips are
  `tests/test_upgrade_postgres.py` (no local PostgreSQL server; `HS_TEST_POSTGRES` unset). 1 skip is
  `test_fixture_provenance`, then run separately with `HS_TEST_REGEN_FIXTURE` pointing at a
  `hypershell==2.8.3` env → passed.
- `uv run pytest tests/test_upgrade_cli.py tests/test_data/test_upgrade.py tests/test_upgrade_postgres.py`
  (orchestrator re-run) → 49 passed, 28 skipped.
- `.agents/factory/bin/temp_site.sh` drives on copies of `tests/fixtures/hypershell-2.8.3.db`, all
  rc=0 and schema brought current: `hs list --all`, `hs submit`, `hs initdb --yes`, `hs info`,
  `hs task search`, `hs update`, `hs cluster --restart`, `hsx --restart -N2 --max-retries 3`.
  First start logs each change at INFO naming the table; a second start emits nothing from
  `hypershell.data.upgrade` (only `hs submit`'s own INFO lines) at default and DEBUG levels.
- Half-upgraded database made for real: `origin/develop` code (`git archive` → `$TMPDIR`, on
  `PYTHONPATH`) run against a fixture copy failed with `no such column: task.source` and left the
  `source` table behind; the branch's `hs submit` then upgraded it, rc=0.
- R4 refusals: a monkeypatched model wrapper adding a NOT NULL column with no default, a
  `CURRENT_TIMESTAMP` server default, and a UNIQUE index → one CRITICAL `SchemaUpgradeError` naming
  the table and column (or index), rc=5, `.schema` identical before and after, no traceback file.
- Concurrency: mixed drives (cluster, two submits, list, search) × 4 iterations in delete and WAL
  journal modes → all rc=0, exactly one upgrader. Large-database drives at 2M and 8M rows → see C1.
- Fixture honesty: sha256 matches `tests/fixtures/README.md`; regenerated with `hypershell==2.8.3`
  from PyPI → schema identical apart from index order, same row shape; `strings` shows no hostname,
  user path or organisation (host columns read `fixture-host`).
- PostgreSQL (no local server): `upgrade.py` planned against a mocked 2.8.3 PostgreSQL inspector
  with the `postgresql` dialect → `ADD COLUMN source UUID`, `fingerprint TEXT`,
  `part INTEGER DEFAULT 0 NOT NULL`, two `DROP CONSTRAINT IF EXISTS task_*_key`, four
  `CREATE INDEX IF NOT EXISTS`; a current schema plans nothing. 2.8.3's model compiled for PostgreSQL
  carries `UNIQUE (previous_id)` / `UNIQUE (next_id)`, so `make-legacy-postgres.sh` (real 2.8.3 and
  2.9.0a1 from PyPI) builds an honest legacy database.
- External evidence, weighed but not reproduced: GitHub Actions run 41401695878 of `tests.yml` at
  9aee1c7 (draft PR #69) succeeded in every job; `PostgreSQL (pg13)` and `(pg18)` each logged
  `Built template databases: hs283 (7 tasks), hs290a1 (half-upgraded)` and `27 passed`;
  `tests (3.11–3.14)` passed.
- Docs: `sphinx-build -E -b html docs <tmp>` could not start in the sandbox (`sphinx_sitemap` needs a
  Unix socket); rerun with `-D extensions=<conf.py list minus sphinx_sitemap>` → build succeeded,
  2 warnings, both the known `task_submit.rst` / `manual.rst` toctree ones.
- In-memory mode: `hsx --no-db` on branch and base → identical behaviour.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `data/upgrade.py`; `data/__init__.py` (`initdb`/`checkdb` → `upgradedb`); `task.py` by-name partition view (dd21023, 41a8750) | CLI drives above on the 2.8.3 fixture and a real half-upgraded DB; PostgreSQL by reading, offline DDL, CI | ✅ |
| R2 | `data/upgrade.py` (add-only plan) | raw `sqlite3` dumps before/after: original 30 columns unchanged, `tag` keeps `"part": 0`, new columns NULL/NULL/0; `test_rows_preserved` | ✅ |
| R3 | `data/upgrade.py` (trace on no-op, INFO per change, `BEGIN IMMEDIATE` / advisory lock) | second-start silence; mixed concurrency drives pass; **large-DB concurrent first start fails (C1)** | ⚠️ partial |
| R4 | `data/upgrade.py` `_refusal`, `SchemaUpgradeError` mapping in `core/exceptions.py` | monkeypatched-model refusals (rc=5, named, schema unchanged, no traceback); generic add of nullable / constant-default columns | ✅ |
| R5 | `data/model.py:295-301` (rationale comment, `unique` dropped), `:910-911` (`index_tasks_previous_id` / `index_tasks_next_id`) | fresh `hs submit` and `hs initdb --truncate` → no UNIQUE, both plain indexes; PostgreSQL `CREATE TABLE` compiled offline has no UNIQUE | ✅ |
| R6 | `data/upgrade.py:131-136` (reflected names, `DROP CONSTRAINT IF EXISTS`, one transaction under an advisory xact lock) | mocked-inspector plan; `test_chain_constraints_dropped` asserts the constraints exist first; CI pg13/pg18 | ✅ |
| R7 | SQLite path never drops; `docs/_include/database.rst`, `database_alt.rst` | upgraded SQLite keeps both UNIQUE; legacy failed chain retried 2→3→4 and new failures chained 1→4 in one `hsx` run | ✅ |
| R8 | `AGENTS.md` (*Retry model*, `data/model.py` footgun), `invariants.md` §3 (9aee1c7) | every claim checked against source: single `Task.next` caller (`server.py:211`); `retried IS FALSE` (`model.py:608`); insert and mark are separate commits (`model.py:179`, `:198`); full-row writeback (`server.py:462`); no `with_for_update` in `src`; on a legacy SQLite DB `hs update retried=false` + `hs cluster -r 3` → `IntegrityError: UNIQUE constraint failed: task.previous_id` | ✅ |
| R9 | `tests/fixtures/` (fixture, generator, README), `tests/test_upgrade_cli.py`, `tests/test_upgrade_postgres.py`, CI job | fixture honesty above; SQLite end-to-end drive; PostgreSQL via CI evidence; suite green; docs clean | ✅ |

Unmapped changes (possible scope creep), all documentation and harmless:
- `AGENTS.md` repository map corrects where `InitDBApp` lives, and "Data layer specifics" documents
  the new step (the file's own "update this file when the code moves" rule).
- `invariants.md` §3 also re-syncs the `attempts` bullet (`query_retryable`/`count_retryable`, the
  `CANCEL_STATUS` reminder) and the group-gating bullet with text `AGENTS.md` already carried on
  the base. No new rule; it restores the lockstep the file requires, beyond R8's letter.
- `InitDBApp` docstring and `docs/api/data.rst` (`upgradedb` autodoc, `ensuredb()` note).

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### [MEDIUM/CONFIRMED] C1 — A concurrent first start on a large SQLite database fails with a raw lock error
- **Where:** `src/hypershell/data/upgrade.py:92-93` (the first, unlocked `_plan` reflection runs
  outside the step's busy timeout and outside the `try` at `:97-106`).
- **Failure scenario:** the first 2.9 process to open a large 2.8 SQLite database builds the new
  indexes and holds SQLite's EXCLUSIVE lock while its page cache spills. A second process started in
  that window reflects the schema under the engine's default 5 s `busy_timeout`, not the step's
  60 s, and exits rc=5 with
  `CRITICAL [hypershell.task] OperationalError: (sqlite3.OperationalError) database is locked - [SQL: PRAGMA main.table_info("client")]`
  — neither the R3 guarantee ("neither SHALL fail because of the other") nor the actionable
  `SchemaUpgradeError` that `_wrap()` gives for the locked case. No data is harmed; a rerun works.
- **Evidence:** 8,000,007-row 2.8.3-shaped database (3.7 GB, delete journal): upgrader A
  (`hs list --count`) rc=0 at 21.3 s; peers B1–B6 started every 1.5 s rc=0; B7 (10.6 s → 16.4 s)
  and B8 (12.1 s → 17.8 s) rc=5 with the error above; a `timeout=0` probe saw readers blocked
  10.9 s. At 2M rows the window was ~2.4 s and all peers passed (threshold ~4–5M rows by default).
  Mechanism re-run by the orchestrator: a fixture copy held under `BEGIN EXCLUSIVE` for 8 s, then
  `hs list --count` started 0.5 s in → rc=5 after ~5 s with the same raw `OperationalError`; rerun
  after release → upgrade INFO lines, rc=0.
- **Touches invariant / requirement:** R3. Not an AGENTS.md invariant; not in the high-blast-radius
  file list.

### [MEDIUM/PLAUSIBLE] P1 — A peer upgrade longer than the bounded lock wait fails the waiter with a misleading message
- **Where:** `src/hypershell/data/upgrade.py:54-55` (`SQLITE_BUSY_TIMEOUT` 60 s,
  `POSTGRES_LOCK_TIMEOUT` 30 s), `:290-294`, `:321-324`.
- **Failure scenario:** a process waiting on `BEGIN IMMEDIATE` (SQLite) or `pg_advisory_xact_lock`
  under `lock_timeout` (PostgreSQL) while a peer's upgrade of a very large table runs longer than the
  bound exits with "another session holds a lock on it (an older server still running?)", although
  the holder is a peer upgrade that would have finished.
- **Evidence:** shown only with a scaled-down timeout (`shortwait.py 500 list --count` against the
  2M-row upgrade → rc=5 with that `SchemaUpgradeError`). Not shown at the shipped timeouts, and not
  on PostgreSQL. Same root as C1 (R3 on large databases). **Needs human triage.**

## Human-gate triggers

- **Mandatory gate: not triggered.** C1 lies in `data/upgrade.py`, outside the high-blast-radius
  list, and touches no listed security/DB-lifecycle invariant.
- **For the human:** triage P1. This cycle amends a gated invariant class ("retry-chain UNIQUE") on
  purpose: the amendment in `AGENTS.md` and `invariants.md` §3 is what R8 and the recorded approval
  (Q10a) authorise, plus the §3 lockstep re-sync noted above.
- **Out-of-diff drift (not graded):** `ROADMAP.md` ("A late result can reopen a retried row"),
  `issues/late-result-resets-retried.md` and `issues/writeback-scoped-conditional.md` say a doubled
  retry "hits the UNIQUE constraint". After this change that holds only for pre-2.9 SQLite
  databases; elsewhere the second retry row is written silently. The GOAL accepts this (R8 names the
  case as unguarded), but those seeds' Problem text is now stale.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Human triage (2026-11-02)

Recorded by the foreman before the cycle-1 remediation build.

- **C1 (CONFIRMED): fix.** Every database touch in the upgrade step, the lock-free pre-check
  included, either succeeds or waits under the same bounded-wait policy as P1; a lock contention
  error is never surfaced raw. The pre-check stays lock-free when uncontended. Red first: a fixture
  copy held under `BEGIN EXCLUSIVE` past SQLite's default 5 s, a second database-backed command
  started meanwhile, which must succeed once the holder releases.
- **P1 (PLAUSIBLE): accepted, fixed in the same pass.** R3 says neither process fails because of
  the other, and an index build on a large PostgreSQL `task` table can outlast a fixed 30–60 s
  bound. The waiter waits in bounded slices, logging one INFO line per slice, and re-plans once it
  holds the lock. A generous ceiling (a module constant) stops a truly stuck holder from hanging a
  process forever; only past it does the step fail, with an accurate message naming what it waited
  on and how to find it, never the "older server" guess. Advisory transaction locks and SQLite file
  locks die with their session, so waiting is safe. Tested with scaled-down timeouts: a peer that
  finishes within the ceiling, and one past it (rc and no traceback, as R4's refusals).
- **Out-of-diff drift: fix in this cycle.** `ROADMAP.md`, `issues/late-result-resets-retried.md`
  and `issues/writeback-scoped-conditional.md` now say a doubled retry hits the UNIQUE constraint
  only on a pre-2.9 SQLite database and is written silently elsewhere, per the amended *Retry model*
  in `AGENTS.md`. `.agents/factory/review-rubric.md`'s human-gate list names the amended invariant
  in place of "retry-chain UNIQUE".

## Review cycle 2 — changes-requested (2026-11-02)

- **Reviewed commit:** 20a12cb (code head ba4fe5e; 20a12cb touches only `spec/`)  ·  **Base:** `origin/develop` (b819de6)
- **Mode:** a fresh blind pass over the full spec-excluded diff (`git diff origin/develop...HEAD -- . ':(exclude)spec/'`).
  The reviewer was also handed cycle 1's C1 and P1 findings and the out-of-diff drift note, with the human
  rulings on them, to verify by re-running the original scenarios. It got no `PLAN.md`/`TECH.md`/`META.md` content.
- **Contract drift:** none. `git log origin/develop..HEAD -- spec/schema-upgrade-step/GOAL.md` lists only the
  shaping commit 53bd6ee.
- **Base:** `origin/develop` again, for the cycle-1 reason: the local `develop` (222075b) has diverged from it.

### Verification run

- `uv run pytest -rxX` (blind reviewer) → 535 passed, 30 skipped, 0 failed. The skips are the 29 PostgreSQL tests
  (`HS_TEST_POSTGRES` unset; no local server) and `test_fixture_provenance`. The upgrade test files were then run
  with `HS_TEST_REGEN_FIXTURE` pointing at a `hypershell==2.8.3` environment from PyPI → 53 passed, 29 skipped
  (all PostgreSQL). Fixture sha256 matches `tests/fixtures/README.md`, and `strings` shows no host, user or path.
- CLI drives ran in throwaway sites under `$TMPDIR`, with `temp_site.sh`'s isolation copied by hand.
  The script itself was not run, because its `EXIT` trap runs `rm -rf` and this session forbids that.
  - **Real half-upgraded database** (`origin/develop` code via `git archive`, run against a fixture copy):
    `hs submit` → rc=5, `no such column: task.source`, `source` table left behind. Then HEAD `hs list --all`
    → rc=0, 3 columns and 3 task indexes logged at INFO, no `Created table source`. The original 30 columns of
    all 7 rows and the client rows are byte-identical before and after. A second start prints nothing on stderr.
  - **R9 on a 2.8.3 copy:** `hsx --restart -r 2 -N2` → rc=0. It retried the pre-existing failure (attempt 3
    chained to the old attempt-2 row) and a new failure twice; `hs list` shows all 12 rows. The SQLite
    `UNIQUE (previous_id)` / `UNIQUE (next_id)` remain.
  - **R4 by command line:** a wrapper that adds a NOT NULL column with no default → rc=5, one CRITICAL line
    naming `task.weight`, schema unchanged, no traceback file. A nullable column and a `server_default=text('7')`
    column are added and read NULL / 7 on old rows.
  - **R3 mixed concurrency:** `hsx`, two `hs submit`s and `hs list` started together, 4 times each in delete and
    WAL journal modes. There was exactly one upgrader every time and the step never failed. One `hs submit` failed
    after the step; see *Out-of-diff context*.
- **PostgreSQL, offline only.** The plan was compiled with the psycopg dialect against mocked 2.8.3,
  half-upgraded and current inspectors. It produced the expected `ADD COLUMN`s, the two
  `DROP CONSTRAINT IF EXISTS task_*_key`, and four `CREATE INDEX IF NOT EXISTS`. The current schema produced no
  steps. `lock_timeout` is set transaction-locally, and a timeout (55P03) becomes a retried wait.
- **External evidence, weighed but not reproduced:** GitHub Actions run 41409167940 of `tests.yml` at ba4fe5e
  succeeded in every job. `PostgreSQL (pg13)` and `(pg18)` each logged
  `Built template databases: hs283 (7 tasks), hs290a1 (half-upgraded)` and `29 passed`, and `tests (3.11–3.14)`
  passed. `git diff --name-only ba4fe5e 20a12cb` lists only `spec/` files.
- **Docs:** `sphinx-build -E -b html` without `sphinx_sitemap` → rc=0, with only the two known toctree warnings.
  A `-b man` build diffed against `share/man/man1/` → see C2.

### Cycle-1 findings

| Finding | Status | Evidence |
|---------|--------|----------|
| C1: concurrent first start fails with a raw lock error | **Resolved** | The pre-check now runs under the bounded wait (`upgrade.py:113`, `:129-145`, `:368-386`). Same mechanism as cycle 1 (fixture copy under `BEGIN EXCLUSIVE` for 8 s, `hs list --count` started 0.5 s in). At HEAD: rc=0, prints `7`, one `INFO … Waiting for another session to release the database, likely a concurrent schema upgrade (5s elapsed)`, then the upgrade lines. The orchestrator's independent re-run gave the same result. Negative control at the cycle-1 head 375a55e: rc=5 with the raw `database is locked - [SQL: PRAGMA main.table_info("client")]`. With no contention there is no added wait (`test_repeat_check_does_not_wait`). |
| P1: a long peer upgrade fails the waiter with a misleading message | **Resolved** | `LOCK_WAIT_SLICE` 30 s and `LOCK_WAIT_CEILING` 900 s (`upgrade.py:59-60`). At the real constants, a peer held `BEGIN IMMEDIATE` with the DDL applied for 75 s, longer than the old 60 s bound. HEAD logs INFO waits at 32 s and 64 s, then gives rc=0 and finds the schema current. 375a55e gives rc=5 at 64 s with "an older server still running?". Past a scaled ceiling (slice 0.5 s, ceiling 2 s): rc=5, one CRITICAL `gave up after 2 s waiting for …` plus the `lsof` hint, schema unchanged, no traceback file. The PostgreSQL equivalents ran only in CI. |
| Out-of-diff drift (triaged to fix) | **Resolved** | With `retried` reset by `hs update` and then `hsx --restart`: a fresh 2.9 SQLite database writes a second retry row silently; an upgraded 2.8.3 copy raises `IntegrityError: UNIQUE constraint failed: task.previous_id`. That matches `ROADMAP.md`, both seeds, AGENTS.md *Retry model* and invariants §3. The rubric's gate list now names the `retried`-filter guard. |

### Requirement → evidence matrix

| R-ID | Verified how (cycle 2) | Status |
|------|------------------------|--------|
| R1 | `test_first_command` (7 commands × untouched/half-upgraded) and `test_first_command_server`; drives on the 2.8.3 fixture and the real half-upgraded database. PostgreSQL in CI (`test_first_use_without_initdb`). | ✅ |
| R2 | Byte-identical before/after hashes of the old rows; new columns read NULL/NULL/0; `tag` keeps `"part": 0`; `test_rows_preserved`. | ✅ |
| R3 | Silent second start; one INFO line per change; mixed concurrency in delete and WAL modes; C1 and P1 re-runs. | ✅ |
| R4 | Command-line refusal and generic adds; `test_refusal_leaves_schema_unchanged`; `test_refusal_on_command_line`. | ✅ |
| R5 | Fresh-site DDL has no UNIQUE; `test_initdb_truncate`; PostgreSQL `test_fresh_schema` in CI. | ✅ |
| R6 | Offline-compiled DDL; CI `test_chain_constraints_dropped` (the second run is silent). | ✅ (CI-verified only) |
| R7 | UNIQUE kept after the upgrade and through a retrying `hsx` run; `docs/_include/` wording correct. The man pages lag (C2). | ⚠️ docs (C2) |
| R8 | Each claim checked against the source; the doubled-retry drive above. | ✅ |
| R9 | End-to-end drive; provenance test green against real 2.8.3; suite green; docs build clean. | ✅ |

There are no unmapped changes beyond those listed for cycle 1.

### Findings

#### [LOW/CONFIRMED] C2 — The man pages still describe the upgrade lock as brief and Postgres-only
- **Where:** `share/man/man1/hs.1:2634`, `hsx.1:2634` and `hyper-shell.1:2634`. These are the branch's own hunks,
  added in 9aee1c7.
- **Failure scenario:** the man pages say the upgrade "on Postgres … drops the `UNIQUE` constraints …, and holds a
  brief exclusive lock while it runs". 958577f amended the source paragraph (`docs/_include/database_alt.rst:14`)
  to "It holds an exclusive lock while it runs (minutes, on a large database), and processes started meanwhile
  wait for it", which is the lock behaviour that settles R3/P1. The man pages were not updated. So a user reading
  `man hs` is told the lock is brief and Postgres-only, which the code no longer guarantees.
- **Evidence:** a fresh `sphinx-build -b man` differs from the committed pages at exactly this sentence. The
  orchestrator compared `grep -n "exclusive lock"` across the three pages and the `.rst`. The other man-page
  drift (`--part`, `card`) is already on `origin/develop` and is not this branch's.
- **Touches invariant / requirement:** R7 (database documentation). This is not an AGENTS.md invariant and not
  in the high-blast-radius list. Docs only; no code impact.

The reviewer dropped these under refutation: the scaled-ceiling message understating the wait; table creation on
a fresh database logging at trace (that is initialization, not an upgrade); `server_default=false()` being refused
as non-constant (gold-plating). It also noted a boundary and set it aside: `checkdb()`'s `has_table`
(`data/__init__.py:91-95`) runs before the step. On SQLite it is reached only by `hs initdb --truncate/--rotate`,
and it still fails raw under a held lock. `origin/develop` fails the same way at `Task.count()`, so this is not a
regression, and it is not part of the step.

### Out-of-diff context (not graded)

- `Source.reserved` (`src/hypershell/data/model.py:1045-1057`, unchanged by the diff) is a get-or-create with no
  conflict handling. Concurrent first `hs submit`s against an empty `source` table can both insert `<direct>`,
  and one exits rc=5 with `IntegrityError: UNIQUE constraint failed: source.id` and a traceback file.
  Reproduced with 6 concurrent submits: 1 in 20 runs on `origin/develop` with a fresh database; 2 in 20 at HEAD
  with a fresh database; 2 in 10 at HEAD on an upgraded 2.8.3 copy. Every upgraded 2.8 database starts with an
  empty `source` table, so the race now applies to them too. The upgrade step itself never failed. This is a
  candidate seed for the human; it is not an R3 failure of the step.

### Human-gate triggers

- **Mandatory gate: not triggered.** C2 is in `share/man/`, outside the high-blast-radius list, and touches no
  security or DB-lifecycle invariant.
- **For the human:** C2 is a one-sentence fix in three man pages. The skill routes any CONFIRMED finding back to
  `/hs-build`, so this costs a remediation build and a third review, the last one the bound allows. Whether a
  LOW docs-only finding should cost a full cycle is your call. Also decide whether the `Source.reserved` race
  above gets an `issues/` seed.
- **Completeness sub-pass:** not run (not requested).
