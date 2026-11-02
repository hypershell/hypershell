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
