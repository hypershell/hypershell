# REVIEW — Contended writes: the `retried` mark and `Task.cancel_all` land when contended on a hypertable; concurrent first submits share the reserved source row

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** e0e61c8625ca2ce5c594dd4bfa0f53a3f5561b84  ·  **Base:** develop (`origin/develop` 06e0468; merge base 6da633d)  ·  **Date:** 2026-11-14
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)

**Pre-flight.** `TECH.md` read `status: in_progress`, `current_phase: P6` (CI green on the fix, an
external wait the foreman records); P1–P5 `done`. Reviewed at the foreman's direction. Contract drift:
`git log origin/develop..HEAD -- spec/contended-writes/GOAL.md` shows only the shaping commit 92af9c1.
Graded under the foreman's gate rulings: every `hs update` change is a non-goal (the split); "green
throughout" on plain PostgreSQL means the real-race tests never fail there (the two-session source
test is red on every PostgreSQL job before the fix); `cancel_all` returning the ids cancelled and
`cancel` a bool are approved; only the tests-first commit was pushed for CI red; the optional
AGENTS.md lock-retention clause is out; eager `Task.next` returning a retry row twice is out of scope.

**Merge status.** `git merge-tree --write-tree HEAD origin/develop` is clean (tree 3f5ecb7) after
#107 and #108; the merged tree carries exactly the branch's changes to `AGENTS.md`,
`.agents/factory/invariants.md`, `ROADMAP.md`, `src/` and `tests/`.

**Reviewer.** One fresh `general-purpose` reviewer, given GOAL.md inline, the spec-excluded diff, the
base `invariants.md`/`AGENTS.md` and `review-rubric.md` by exact command, the gate rulings, and the R5
evidence (`red.txt`, `ci-red.txt`, `evidence/ci-red/`, `drive/`, `spec/relay-hardening/drive/red_check.sh`).
Blindness: one repo-wide `grep` matched lines in `PLAN.md` and `TECH.md`; the reviewer did not
open them and graded nothing from them (meta-note F7).

## Verification run

- `PATH=.venv/bin:$PATH python -m pytest -q -p no:cacheprovider tests` (SQLite, full suite) → 2650 passed, 113 skipped.
- `HS_TEST_POSTGRES=… HS_TEST_TIMESCALE={'',community} pytest tests/test_contended_write.py tests/test_contended_recovery.py tests/test_source.py` → 77 passed, 1 skipped (hs283 template) on each provider (PostgreSQL 18, TimescaleDB 2.32).
- CI's PostgreSQL set less the Docker-dependent files → postgres 103 passed / 5 skipped; timescale 107 passed / 1 skipped.
- `RED_BASE_REV=92af9c1 RED_LIST=spec/contended-writes/red.txt sh spec/relay-hardening/drive/red_check.sh` → SQLite 12 of 12 red (1 `pg:` skipped); with `HS_TEST_POSTGRES`, `HS_TEST_TIMESCALE=` and `=community` → 13 of 13 red. The mark and cancel tests fail by `StaleDataError`, stop-between by assertion, the source race by `IntegrityError` / `UniqueViolation` on the source primary key.
- `drive/ci_check.py red … --code-rev 23e47ef` → 0 differences: `timescale`/`timescale-oss` (2.31) fail the retry-mark and cancel real-race tests plus the source race; `timescale-floor`, `pg13`, `pg18` fail only the source race; the SQLite jobs fail the 12 listed tests. The real-race tests pass at base locally on PostgreSQL 18 and TimescaleDB 2.32. `git diff 23e47ef HEAD -- tests/` is empty.
- `drive/retry_drive.sh` (SQLite and timescale, plain and eager) → 40 first attempts, 40 retry rows, 0 problems, `hsx` exit 0.
- `drive/concurrent_submit.sh`, 10 rounds × 6 submits → 0 failed on fresh SQLite, upgraded 2.8.3 SQLite and fresh PostgreSQL; control at 92af9c1 on upgraded 2.8.3 SQLite → 4 of 10 rounds failed (`IntegrityError`).
- SIGKILL drive (a process held at the retry `INSERT` after its mark ran, then killed) on SQLite, `postgres` and `timescale` → `victim exit -9`; `retried False, next_id None, retry_rows 0`; next round `retried True, retry_rows 1`.
- Mutations in scratch copies via `PYTHONPATH` (tree untouched): mark and insert in separate commits, all retries inserted, lookup without the row-state filters, and `Source.reserved` without on-conflict are each caught; the mark's UPDATE without its row-state condition survives (F2).
- Orchestrator: merged tree 3f5ecb7 exported, `pytest tests/test_contended_recovery.py tests/test_source.py tests/test_writeback.py tests/test_dispatch_staged.py tests/test_topology_server.py tests/test_cluster_exit.py tests/test_submit*.py` → 221 passed. F2's probe re-run on PostgreSQL 18: HEAD passes (`outcome [] retries [] retried False exit -1`); the mutant gives the cancelled row a retry (`AssertionError: assert [(…)] == []`), while `tests/test_contended_recovery.py tests/test_retry_gate.py` pass on the mutant (61 passed).

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `data/model.py` `__schedule_next_failed_tasks` (:861-899): marks via `__write_rows(retryable=True, each=…, commit=False)`; 13a552d | `test_contended_retry_mark_lands_on_rerun` (SQLite fault injection); `test_contended_retry_mark_lands` on PG 18 / TS 2.32 and red on CI TS 2.31; `retry_drive.sh` | ✅ |
| R2 | Same: marks first, retry rows added only for marks that landed, one commit, rollback on error; WARNING naming the task at the bound; `retryable` condition skips cancelled/deleted/marked rows | Fault-injection tests (exhausted, cancelled/deleted/retried meanwhile, stop-between); SIGKILL drive on three backends; mutations A, C, D caught | ✅ (code); pin gap F2 |
| R3 | `Task.cancel_all`/`cancel` (:1228-1251) through `__write_rows`, returning the ids cancelled / a bool; 005cb33 | `test_contended_cancel_lands_on_rerun`, `…_exhausted_reports_rows_not_cancelled`, `test_cancel_of_gone_row_raises_and_cancels_nothing`, `test_contended_cancel_lands` (PG/TS); no `src/` caller uses the return value | ✅ |
| R4 | `Source.reserved` (:1660-1686): `INSERT … ON CONFLICT (id) DO NOTHING`, commit, re-read; 005cb33 | `test_source.py` fresh/upgraded × direct/stdin; PG race test on both providers; `concurrent_submit.sh` (0 failures vs 4/10 at base); mutation F caught | ✅ |
| R5 | `tests/test_contended_recovery.py`, `tests/test_contended_write.py`, `tests/test_source.py`; 23e47ef (tests first) | red_check 12/12 and 13/13 at base; CI red matches `ci-red.txt` (0 differences); cost pins `test_uncontended_retry_mark_costs_one_update_per_row` / `…cancel…` pass; all green at HEAD on SQLite, PG 18, TS 2.32 | ✅ |
| R6 | `AGENTS.md` (§ Task lifecycle, § Data layer specifics, the `data/model.py` blast-radius note), `.agents/factory/invariants.md` §1, §3; 4d9d61a | Each claim checked against the code and drives; only `hs update` (`task.py`) still uses `update_all`/`bulk_update_mappings` on task rows | ✅ (user docs: F1) |

Unmapped changes (possible scope creep): none. The `__awaiting_retry` refactor of `query_retryable`
(same filters) serves R2; the seed and ROADMAP edits follow the GOAL-gate split. Graded as prose:
`issues/hs-update-contended-writes.md` cites `task.py` lines that match 6da633d, and both of its
non-contention defects reproduce in a temp site (`--limit 2 -f group=7 color:red` → group 7 on 2
rows, red on 0; `-f group=8 color:blue -w group==0` → group 8 on 2, blue on 0); the two adopted
seeds' statuses and the ROADMAP links are correct.

Observation (not a finding): the PostgreSQL CI jobs run with `-rs`, so `evidence/ci-red/failed.log`
records no failure reason for the TimescaleDB 2.31 real-race failures; only the job pattern (red on
2.31, green on 2.24, plain PostgreSQL and local 2.32) ties them to the defect.

## Findings

### [HIGH/CONFIRMED] F1 — User docs still list the kill between the retry insert and the mark as a doubled-retry case
- **Where:** `docs/_include/database.rst:126-130` (included by `docs/database.rst`), `docs/_include/database_alt.rst:17-21` (included by `docs/manual.rst:265`)
- **Failure scenario:** Both pages tell users that "a server killed between scheduling a retry and
  marking the original task as retried" (alt: "a server killed while scheduling a retry") is one of
  the rare cases where a failed task is retried twice and an older SQLite database rejects the
  second retry. After this diff that case leaves no retry row and no error, then exactly one retry.
  R6 updated `AGENTS.md` and `invariants.md`; the diff touches no `docs/` file.
- **Evidence:** `grep -n "server killed between\|a server killed while" docs/_include/database*.rst`
  matches `database.rst:127` and `database_alt.rst:18`; the SIGKILL drive above shows the new
  behaviour on SQLite, `postgres` and `timescale`.
- **Touches invariant / requirement:** §12 docs same-commit rule (HIGH per the rubric); R2/R6. Not a
  CLI change, so the GOAL's "no user-visible CLI change" does not cover it; fixing it fails no GOAL
  criterion, so the deferral exception does not apply.

### [LOW/CONFIRMED] F2 — No test pins the row-state condition on the retry mark's own UPDATE
- **Where:** `src/hypershell/data/model.py:994-997` (`'retryable'`: `match = awaiting`); tests in
  `tests/test_contended_recovery.py`, `tests/test_contended_write.py:578`
- **Failure scenario:** Fault injection replaces the mark with a 0-row UPDATE, so only the lookup's
  re-check is exercised; the one real-race mark test holds the row with a `client_host` write that
  leaves it qualifying. Dropping the UPDATE's condition (`match = (by_id,)`) lets a mark that waited on
  a cancel land on PostgreSQL / TimescaleDB < 2.29 (and on any uncontended path) and give the
  cancelled row a retry — resurrecting a cancelled task (invariant §2) — and no committed test fails.
  HEAD behaves correctly; the pin is missing.
- **Evidence:** the mutant passes the new SQLite tests and `test_contended_recovery.py` /
  `test_retry_gate.py` (61 passed), and on PG 18 / TS 2.32 `test_contended_write.py`,
  `test_contended_recovery.py`, `test_writeback.py`, `test_retry_gate.py` (104 passed each); a
  real-race probe (`contend(CANCEL_SQL)` around `Task.next(limit=1, attempts=2)`, mirroring
  `test_contended_dispatch_skips_cancelled_row`) passes at HEAD and fails on the mutant, re-run by the
  orchestrator on PG 18.
- **Touches invariant / requirement:** R2 ("cancelled meanwhile … SHALL get no retry row"), R5 test
  strength; the `CANCEL_STATUS` filter (§2) and one retry row per failure (§3) on the retry path.

Dropped after checking: pre-existing `__write_rows` callers (with `each=None`, `commit=True`,
`retryable=False` the condition, cached statement, parameters and commit are unchanged); autoflush
(retry objects are not in the session while the Core marks run); `next_id` has no foreign key;
`Source.reserved` reuses the `Zone.ensure` on-conflict pattern with the old commit/rollback
semantics, an upgraded database's `source.id` is its primary key, and only `task` is a hypertable.

## Human-gate triggers

- **Triggered — by F2.** It is CONFIRMED, its locus is `data/model.py` (high-blast-radius), and its
  subject is the guard of two listed DB-lifecycle invariants (the `CANCEL_STATUS` filter; one retry
  row per failure) on the retry path. The code at HEAD is correct and the remediation is test-only,
  but the rule is applied as written: explicit human sign-off is required before any further step.
- F1 alone would not trigger it (docs only; no core file or invariant broken in code).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).
