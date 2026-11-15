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

## Review cycle 2 — changes-requested (2026-11-15)

- **Reviewed commit:** d5a9836b5bd40c94c21ed2de031b5f82c9640be7  ·  **Base:** develop (`origin/develop` 48cc505; merge base 6da633d)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over the full spec-excluded diff, plus verification of cycle 1's F1 and F2 remediation.

**Pre-flight.** `TECH.md` read `status: in_progress`, `current_phase: P6` (CI on this head, the
foreman's external wait). Reviewed at the foreman's direction. Contract drift: only the shaping commit
92af9c1 touches `GOAL.md`. Graded under cycle 1's rulings, with the review-cycle-1 ruling (maintainer
sign-off) added. F1: the docs drop the kill case, with a `versionchanged` marker on pages that carry
markers. F2: tests that pin the retry mark's own row condition — a real-race test on
PostgreSQL/TimescaleDB and a deterministic SQLite counterpart, red on the mutant — and no code change.

**Merge status.** `git merge-tree --write-tree origin/develop HEAD` is clean against 48cc505 (after
#107, #108 and the roadmap commit): tree aedf193. In the merged tree, the branch side of `AGENTS.md`,
`.agents/factory/invariants.md`, `ROADMAP.md`, `src/`, `tests/`, `docs/` and `issues/` equals the
branch diff line for line. Develop's edits to `AGENTS.md` and `invariants.md` do not touch the retry,
cancel or hypertable passages. The merged tree ran on SQLite: `test_contended_recovery.py`,
`test_source.py`, `test_contended_write.py`, `test_writeback.py`, `test_retry_gate.py`,
`test_dispatch_staged.py`, `test_topology_server.py`, `test_cluster_exit.py` and `test_submit*.py`
gave 245 passed, 23 skipped (PostgreSQL only). That count is with the venv's `hs` on `PATH`; a first
run without it failed 64 subprocess tests on `FileNotFoundError`, which is environmental.

**Reviewer.** One fresh `general-purpose` reviewer received:
- `GOAL.md`;
- the spec-excluded diff command;
- `review-rubric.md`, and the base `invariants.md` and `AGENTS.md` (`origin/develop`) by exact command;
- the binding rulings;
- cycle 1's F1 and F2 failure scenarios and the signed-off remediation;
- `drive/`, `red.txt`, `ci-red.txt`, `mutant-red.txt`, `evidence/` and `spec/relay-hardening/drive/red_check.sh`.

The brief told it to keep every search off `PLAN.md`, `TECH.md`, `META.md`, `REVIEW.md` and
`research/` (META F7's recommended fix). It reports that it opened none of them. It left the tree clean
(`git status --porcelain` empty, HEAD d5a9836), and its one background process, the full SQLite suite,
exited.

### Verification run (cycle 2)

- `git diff e0e61c8 HEAD -- src/` is empty: the cycle-1 remediation is tests, docs and evidence only.
- Full SQLite suite: `python -m pytest -q -p no:cacheprovider -x tests` gave 2652 passed, 114 skipped.
- The touched test files (`test_contended_recovery.py`, `test_contended_write.py`, `test_source.py`)
  on PostgreSQL 18.7 and on TimescaleDB 2.32.0: 80 passed, 1 skipped (the hs283 template) on each.
- CI's PostgreSQL file list, less `test_upgrade_postgres.py` (it needs `HS_TEST_POSTGRES_TEMPLATES`):
  - PostgreSQL 18: 181 passed. The 2 failures are `test_timescale` entry checks ("server offers
    TimescaleDB 2.32.0, but HS_TEST_TIMESCALE is empty").
  - TimescaleDB: 201 passed. The 1 failure needs the hs283 template.
  - All three failures come from the local setup, and the diff touches none of those files.
- `drive/mutant_check.sh` (TMPDIR under `/tmp/claude-501/`):
  - SQLite: 2 of 2 red, 1 skipped. PostgreSQL 18 and TimescaleDB 2.32: 3 of 3 red each. Exit 0 on all three.
  - The export differs from the tree in exactly one line, `model.py:997`.
  - Failure messages on the mutant: the SQLite pair gives `assert ['mark'] == ['mark', 'SELECT']`; the
    real race gives `assert [(<retry id>, <failed id>)] == []`, meaning the cancelled row's retry was dispatched.
  - `MUTANT_REV=92af9c1` exits 2 ("the 'retryable' match line is not in … once").
- `red_check.sh` with `RED_BASE_REV=92af9c1` and `red.txt`, using the HEAD tests: SQLite 12 of 12 red
  (1 skipped); PostgreSQL 18 and TimescaleDB 2.32 13 of 13 red. The cycle-2 harness change turned no
  base-red test green. With `mutant-red.txt` at base on PostgreSQL 18: 3 of 3 red.
- Stability: the new SQLite tests ran 10 times and the new real-race tests 5 times on each server, with
  no failure.
- `sphinx-build -W --keep-going -q -E -b html docs <scratch>` exits 0. This used the worktree venv's
  Sphinx (Python 3.14): `uv run --python 3.12 --group docs` needed a download, which the network
  restriction refused. The rendered HTML contains no "killed between/while".
- `drive/ci_check.py`:
  - `red evidence/ci-red --code-rev 23e47ef`: 15 jobs, 0 differences.
  - `green evidence/ci-green --code-rev e0e61c8`: 15 jobs, 0 differences.
  - CI on d5a9836 is P6, still running; not graded here.
- Drives in `temp_site.sh`:
  - Six concurrent first `hs submit -f -`, 10 rounds each, on fresh SQLite and on a 2.8.3 database
    upgraded in place: 0 failed rounds.
  - `hsx -t 'exit 3' -r 1 -N4`, 40 tasks, plain and `--eager`, on SQLite and on the TimescaleDB
    hypertable: 40 first attempts, 40 retry rows, 0 problems and exit 0 each time.
- The compiled retry-selection SQL is identical at HEAD and at base.
- The `issues/hs-update-contended-writes.md` citations (`task.py:1070-1216`) match the code; `task.py`
  is unchanged since 6da633d.
- Two-scheduler probe (N1): two threads call `Task.next(limit=1, attempts=2)`, each held at its first
  mark or insert until both have selected the same failed row.
  - The reviewer ran it on SQLite and PostgreSQL 18: HEAD leaves 1 retry row of the failed row, base
    92af9c1 leaves 2.
  - The orchestrator re-ran it on SQLite: HEAD `dispatched per round: [[], [<id>]] | retry rows … 1`;
    base `[[<id>], [<id>]] | … 2`.

### Cycle-1 findings

- **F1 — fixed.** The kill case is gone from both pages:
  - `docs/_include/database.rst:124-133` also adds `.. versionchanged:: 2.9.0` ("A failed task's retry
    and its `retried` mark are recorded together …"), which is true at HEAD (one transaction).
  - `docs/_include/database_alt.rst:15-19` has no marker. It is included by `manual.rst`'s Database
    section, which carries no markers (its 2.9.0 upgrade text has none either), so the ruling's "where
    markers exist" holds.
  - The `-W` build passes.
  - `share/man/man1/*.1:3247` still carries the old sentence. Man pages are rebuilt only in `[release]`
    commits (AGENTS.md), so this is not a finding.
- **F2 — fixed.** No `src/` change since e0e61c8.
  - The new tests are red on the `(by_id, )` mutant and green at HEAD on SQLite, PostgreSQL 18 and
    TimescaleDB 2.32: `test_retry_mark_that_waited_matches_only_a_row_awaiting_retry[cancelled|retried]`
    and `test_contended_retry_mark_skips_cancelled_row`.
  - `mutant_check.sh` tests the committed rev's export, not the installed tree; `red_check.sh` refuses
    to run unless `hypershell` imports from the export. It mutates exactly one line, checks it before
    and after, and exits 2 when the mutation stops applying.
  - The harness change (`DriverContention.waits`, the reordered `take`) differs from before only when
    `meanwhile` is set with `losing` at 0. Only `waits` creates that state, and every `lose()` call
    uses a count ≥ 1, so the earlier tests behave as before; the base red check confirms it.
  - `waits` models PostgreSQL's re-check (the other change commits, then the write runs as written).
    It relies on the tested row being the batch's first mark, which `failed()` orders by completion time.
  - **TimescaleDB 2.31 coverage:** sufficient for the ruling.
    - The mutant fails 7 of CI's 9 test jobs: the four SQLite jobs (`tests (3.11–3.14)`) through the
      SQLite pair; `pg13`, `pg18` and `timescale-floor` (2.24 re-checks the row) through the real race.
    - On `timescale`/`timescale-oss` (2.31) a waited-on mark matches 0 rows with or without the
      condition. The lookup that follows applies the same awaiting-retry filters, so the condition has
      no work to do there and the mutant is harmless on 2.31.
    - At HEAD the test should pass on 2.31 by that same path. This is reasoned, since no 2.31 server is
      available locally. It is the path of `test_contended_dispatch_skips_cancelled_row`, which CI ran
      green on 2.31 at e0e61c8.

### Requirement → evidence matrix (cycle 2)

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `data/model.py:861-899`: marks via `__write_rows(retryable=True, each=next_ids, commit=False, prefix='m_')` (:889-890), retry rows only for marks that landed (:891-893); condition :994-997; re-run loop :1063-1097. 13a552d | `test_contended_retry_mark_lands_on_rerun` (SQLite, red at base); `test_contended_retry_mark_lands` green on PG 18 and TS 2.32, CI red on 2.31 at 23e47ef and green at e0e61c8 (`ci_check.py`, 0 differences); hsx drive on SQLite and the hypertable | ✅ |
| R2 | Same function: marks and inserts in one transaction (:884-896); a row no longer awaiting retry skipped (:1079-1081); WARNING at the bound (:1094-1096) | Exhausted, cancelled/deleted/retried-meanwhile and stop-between tests; the waited pair and `test_contended_retry_mark_skips_cancelled_row`; red check at base; `mutant_check.sh` 3 of 3 red | ✅ (F2 closed) |
| R3 | `data/model.py:1228-1251`, `cancel_all` returns the ids cancelled, `cancel` a bool. 005cb33 | `test_contended_cancel_lands_on_rerun`, `…_exhausted_reports_rows_not_cancelled`, `test_cancel_of_gone_row_raises_and_cancels_nothing`, the uncontended-cost pin, `test_contended_cancel_lands` | ✅ |
| R4 | `data/model.py:1660-1686`, `ON CONFLICT (id) DO NOTHING` via `UPSERT_BY_DIALECT`, then a re-read. 005cb33 | SQLite two-session tests ×4; PostgreSQL `[fresh]` on both providers (`[upgraded]` needs hs283, CI only); submit drive 20 of 20 rounds clean | ✅ |
| R5 | `tests/` at 23e47ef and d5a9836 | Red check 12/12 and 13/13 at base; CI red set exact; cost pins pass | ✅ |
| R6 | `AGENTS.md:232-254, :499-514, :571-577`; `invariants.md:44-55, :75-81`. 4d9d61a, docs in d5a9836 | Each claim read against the code at HEAD | ✅ as R6 words it (the mark, `cancel_all` and the kill case dropped); N1 for the two-server clause left in the same lists |

Unmapped changes (possible scope creep): none.
- `Task.__awaiting_retry` shares the selection's filters with the R2 mark, and the compiled SQL is
  unchanged from base.
- The seed and ROADMAP edits follow the GOAL-gate split. The new seed's Problem section matches the code
  it cites, its status is `unshaped`, and its ROADMAP links resolve.

### Findings (cycle 2)

#### [LOW/CONFIRMED] N1 — The doubled-retry lists still name two servers on one database, which the conditional mark now guards
- **Where:**
  - `AGENTS.md:232-238` and `:252-254` ("in the two cases above they raise `IntegrityError`");
  - `.agents/factory/invariants.md:75-81` (§3);
  - `docs/_include/database.rst:129-133`;
  - `docs/_include/database_alt.rst:17-19`.
- **Failure scenario:** Each passage lists two servers on one database as a case that adds a second
  retry row with the same `previous_id`, which a pre-2.9 SQLite database rejects with an error. At HEAD
  that no longer happens. The retry mark matches only a row that is still a failure with
  `retried IS FALSE`, and it commits in one transaction with its retry row. A second Scheduler that
  selected the same failed row finds its mark matching nothing (on SQLite after the first commit; on
  PostgreSQL by re-check, or on TimescaleDB 2.29+ by miss and lookup), and it skips the row. The diff
  rewrote these very lists (R6, F1) and left this clause, so the invariant file, AGENTS.md and the user
  docs now state a guard gap the code closed.
- **Evidence:** the two-scheduler probe. HEAD leaves 1 retry row and base 92af9c1 leaves 2, on SQLite
  (reviewer and orchestrator) and on PostgreSQL 18 (reviewer).
- **Touches invariant / requirement:** R6, read as claims true at HEAD, and the §12 docs rule. Graded
  LOW, not as F1's HIGH: the prose and marker for the user-visible change exist and the build passes.
  The leftover clause overstates a risk in a configuration the docs already call unsupported, and the
  GOAL leaves two servers out of scope. No GOAL criterion requires keeping it, so the deferral exception
  does not apply. It blocks, as cycle 1 applied the rule to a CONFIRMED LOW (META F8).
- **Remediation (wording only):** take two servers out of the doubled-retry lists, or move it out of
  those lists as a statement of its own about the unsupported setup that makes no retry claim. Then fix
  the counts that follow ("in the two cases above"; "the rare cases"). If the maintainer prefers to keep
  the clause as a conservative warning, that is the alternative to rule on.
- **Not a finding:** the same bullet's "new tasks double-schedule too" may be stale since #103's
  conditional dispatch stamp. This diff did not change it, and it was not probed.

Dropped after checking: the man pages' old sentence (rebuilt at release). The PostgreSQL CI-list
failures were checked and are all environmental (the TimescaleDB entry check, the hs283 template).

### Human-gate triggers (cycle 2)

- **Not triggered by N1.** It is wording only, with no core file touched and no invariant broken in code.
  That is the reading cycle 1 applied to F1, the same class of finding. The remediation edits the text
  of invariants §3, which the maintainer sees at publish.
- Cycle 1's gate (F2) was met by the maintainer's sign-off on the remediation. F2 is closed with no
  `src/` change.

### Loop status

This is the second consecutive changes-requested verdict; per the rubric's bounded loop it is escalated
to the maintainer. One build↔review cycle remains within the bound (≤3). There are two routes:
- a wording-only `hs-build` for N1, with cycle 3 scoped to verifying that remediation;
- the maintainer rules N1 accepted as a conservative overstatement, and the verdict is re-set to
  approved.

### Optional completeness sub-pass (cycle 2)

- Not run (not requested).
