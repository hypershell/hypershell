# REVIEW — `hs update` writes every requested change to exactly the rows it counts, contended or not, and says plainly when it cannot

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** ee5fcd45d51f101dabb8f72b84581c9043c96fe0  ·  **Base:** `fbc656e` (`origin/develop`)  ·  **Date:** 2026-11-15
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** one blind correctness reviewer (no `debate`, no `completeness` sub-pass), plus an
  orchestrator re-run of the deciding finding (F1).

## Pre-flight

- **Base.** `origin/develop` fetched; it is `fbc656e`, equal to the local `develop` and to
  `git merge-base HEAD origin/develop`. The graded diff is
  `git diff origin/develop...HEAD -- . ':(exclude)spec/'` (13 files).
- **Build state.** `TECH.md` is `status: in_progress`, P1–P6 `done`, P7 (CI green on the fix, an
  external wait on the draft PR) `pending`. The review covers the code at `ee5fcd4`; CI green on this
  head is the foreman's wait and was not checked here (no GitHub calls).
- **Contract drift.** `git log --oneline origin/develop..HEAD -- spec/hs-update-contended-writes/GOAL.md`
  shows the shaping commit `86bd92d` and the GOAL-gate lock `f00f853`, which records the gate's rulings
  on the five proposed answers. No drift after the lock.
- **Merge.** Clean: `origin/develop` is the merge base. Against PR #110's head
  (`origin/fix/task-input-fidelity`, `664027a`), `git merge-tree --write-tree HEAD
  origin/fix/task-input-fidelity` reports no conflict.
- **Readings that bound grading** (gate rulings, passed to the reviewer): GOAL gate (newcomer rows
  written and counted; the WARNING level; the PostgreSQL multi-key variant in R5; set-based without
  `--limit`, per-row with it; `--limit` one transaction) and plan gate D1–D5, the two documented
  risks (SQLite write lock through a long `--limit`; PostgreSQL ids held in memory) and the
  2.28.1–2.31.0 dating.

## Verification run

Commands the reviewer executed (sandbox off for PostgreSQL; its own `hurv_` databases, all dropped):

- `uv run pytest -q -p no:cacheprovider tests/test_update.py tests/test_list.py tests/test_contended_recovery.py tests/test_contended_write.py`
  → 77 passed, 39 skipped (PostgreSQL tests skip without a server).
- Full SQLite suite, serial: `uv run pytest -q -p no:cacheprovider` → 2868 passed, 130 skipped, 0 failed.
- CI's PostgreSQL file set (`test_writeback_postgres`, `test_timescale`, `test_contended_write`,
  `test_zone_postgres`, `test_server_initdb`): plain PostgreSQL 18 → 197 passed, 2 failed; TimescaleDB
  2.32 (`HS_TEST_TIMESCALE=community`) → 217 passed, 1 failed. All three failures are environmental
  (TimescaleDB offered while `HS_TEST_TIMESCALE` is empty; a template that needs Docker); every
  `test_contended_write.py` test passes on both.
- Red evidence: `RED_BASE_REV=fbc656e RED_LIST=spec/hs-update-contended-writes/red.txt sh spec/relay-hardening/drive/red_check.sh`
  → 15 of 15 SQLite lines red (AssertionError); the 10 `pg:` lines red on the base on plain PostgreSQL
  and on TimescaleDB 2.32; the 5 `ci-red.txt` real-race tests pass on the base locally (2.32), as the
  list says; `uv run python spec/hs-update-contended-writes/drive/ci_check.py red spec/hs-update-contended-writes/evidence/ci-red --code-rev d228a44`
  → "red, 15 jobs, 0 difference(s)". `failed.log` shows the five real-race tests (legacy, filtered,
  `--limit`, `--delete`, `--delete --limit`) red only on `timescale` and `timescale-oss` (2.31.0), so
  the contended DELETE does miss there.
- Base-vs-HEAD differential drive on one seeded database (9 tasks; mixed tags, booleans, floats,
  every lifecycle state): `hs list --count` and id listings over ~41 filter combinations (`-t` names,
  values, booleans; `-w` with every operator; `-F/-S/-C/-R/-X/--retries/--signal/-g`), 25 single-kind
  `hs update` forms (filtered and `--limit`; cancel, revert, tag set/remove, delete, null, regex) and
  8 legacy forms. SQLite: the only differences are the D2 fix (`-t a b` requires every name, in
  `hs list` and `hs update`). Plain PostgreSQL and TimescaleDB: 0 differences.
- Compressed hypertable chunks (old UUIDv7 ids, `compress_chunk`): every form succeeds, `RETURNING`
  included; the only difference from base is the intended R5 fix.
- Row-trigger fault injection on PostgreSQL (HEAD): filtered combined field+tag+remove, filtered
  `--delete`, `--limit 150` with row 120 exhausted, `--limit --delete`, legacy — each exhausted → exit
  5, nothing written or deleted, WARNING then `CRITICAL ... Contended: Update of task (<id>) was
  contended after 4 attempts - nothing was written`, no traceback file. Skipped once or twice
  (legacy, legacy tag, removal under its own tag filter, `--revert color:red -C`, `--limit -R --cancel
  color:red --remove-tag c`) → exit 0, every change on every row, correct count. No change given →
  "Updated 0 tasks", no warning (D4).
- Real race on local PostgreSQL 18 and TimescaleDB 2.32 (holder seen blocking in `pg_stat_activity`,
  then committed): `--limit -R --cancel` and `--limit -R --delete` with the holder completing the row,
  filtered `--delete` and `--cancel color:x` likewise → completed row kept, WARNING "1 of the 4 tasks
  counted no longer matched and were left as they are", "Updated/Deleted 3", exit 0; filtered update
  with the holder deleting the row → "Updated 3" plus warning; legacy with the holder deleting the row
  → NotFound, exit 5; legacy and `--limit` combined with the holder touching the row → written, exit 0.
- Set-based pin on PostgreSQL (`HYPERSHELL_DATABASE_ECHO`): `hs update -f group=7 color:red --remove-tag c -w group==0`
  → 3 statements on `task` (count; one `UPDATE ... tag=((task.tag || $1::JSONB) - $2::TEXT[])`; one
  residual SELECT) for 5 rows and for 30, plain PostgreSQL and TimescaleDB.
- `sh spec/hs-update-contended-writes/drive/update_drive.sh sqlite` → PASS (limit, moved, cancel, keys,
  removals, prompt). Its PostgreSQL modes were not run (a fixed database name another session might
  hold); the probes above cover them.
- Docs: `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-hurv`
  → exit 0, no Sphinx warnings; `task_update.html` renders the seven `versionchanged` blocks.
- Orchestrator re-run of F1 (plain PostgreSQL 18, the row trigger, a throwaway `hurv_orch_f1_*`
  database, dropped after): see F1.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.update` → `__write_rows(abort=True)`; `StaleDataError` → `Task.NotFound` only for a gone row (`task.py` `update_legacy`) | `test_update_legacy_contended_lands_on_rerun`, `test_update_legacy_contended_exhausted_writes_nothing`, `test_update_legacy_missing_task_not_found`, `test_contended_update_legacy_lands` (red on CI 2.31); race and trigger probes | ✅ |
| R2 | `--limit`: `Task.update_selected`/`delete_selected`, per row under `filter_criteria()`; no `--limit`: `Task.update_matching`/`delete_matching`, set-based plus the PostgreSQL residual pass | `test_update_limit_skips_task_changed_out_of_filter`, `test_update_limit_cancel_keeps_result_landed_meanwhile`, `test_update_filtered_skipped_row_written`, `test_update_delete_filtered_skipped_row_deleted`, `test_contended_update_{filtered,limit,delete,delete_limit}_lands`, `test_contended_update_leaves_row_moved_out_of_filter`; race probes | ✅ (F3: `--delete` leaving a row is not pinned by a test) |
| R3 | `TaskUpdateApp.report_written`: N = rows written, WARNING when N < count, exit 0 | `NOT_MATCHED` assertions in `test_contended_recovery.py` and `test_contended_write.py`; delete forms by probe only | ✅ (F3) |
| R4 | `abort` → rollback of the one transaction, `Task.Contended` → `handle_exception` (CRITICAL), exit 5, no traceback | `test_update_limit_contended_exhausted_writes_nothing` (150 rows, past the old batch of 100), `test_update_delete_limit_contended_exhausted_deletes_nothing`, `test_update_filtered_skipped_row_exhausted_writes_nothing`; trigger probes | ✅ (bound off by one on PostgreSQL without `--limit`: F1) |
| R5 | `Task.__change`: one SET (SQLite `json_remove(json_set(...))`, PostgreSQL `(tag || patch) - names`) | `tests/test_update.py` (limit, moved, cancel, keys/removals/set_and_remove), `test_update_combined_changes_pg[*]`, `test_update_tag_keys_and_removals_pg[*]`; `update_drive.sh sqlite` | ✅ |
| R6 | Fault injection on SQLite (`DriverContention`), the PostgreSQL row trigger (D1), the real race (`contend`), CLI drives | red_check 15/15 SQLite, 10/10 `pg:` on plain PostgreSQL and TimescaleDB; `ci_check.py red` 0 differences | ✅ |
| R7 | `docs/cli/task_update.rst` (7 × `versionchanged:: 2.9.0`), `docs/_include/task_update_desc.rst`, AGENTS.md § Data layer specifics, invariants §1 | Docs build `-W` exit 0; prose read against the code | ✅ (F2: one inaccurate clause) |
| D2 | Shared `SearchableMixin.filter_criteria()` | `test_list_name_only_tags_check_every_tag`, `test_update_name_only_tags_check_every_tag` (red on base); `docs/cli/task_search.rst` `versionchanged`; differential drive | ✅ |
| Set-based pin | `update_matching` | `test_update_filtered_uncontended_is_one_statement` (SQLite, 1 UPDATE for 5 and 30 rows), `test_update_filtered_uncontended_statements_pg`; echo count | ✅ |

Unmapped changes (possible scope creep): `docs/_include/task_update_desc.rst` adds that `-w`/`-t`
take the words up to the next option, so the changes go before them (accurate for argparse
`nargs='+'`; maps to no R-ID; not blocking). The `ROADMAP.md` and `issues/hs-update-contended-writes.md`
status flip to `adopted:hs-update-contended-writes` is cycle bookkeeping and matches reality.

## Findings

### F1 [CRITICAL/CONFIRMED] On PostgreSQL without `--limit`, a missed row is re-run one time more than `CONTENDED_RERUNS`, and the bound's messages under-count the attempts
- **Where:** `src/hypershell/data/model.py:1253-1259` (`Task.update_matching`) and `:1281-1287`
  (`Task.delete_matching`), with the per-row bound at `:1105`.
- **Failure scenario:** The set-based UPDATE (or DELETE) is the row's first attempt. A row it missed
  is handed to `__write_rows`, which gives it a full `1 + CONTENDED_RERUNS` = 4 attempts of its own:
  5 statements, 4 re-runs, where invariants §1 (base copy) allows "at most `CONTENDED_RERUNS`
  times". At the bound the WARNING and the `Task.Contended` error both say "after 4 attempts" when 5
  were made, and the first per-row write has no DEBUG line of its own (only "Re-checking 1 tasks
  the update did not write"; the next one logs "attempt 2 of 4"). The `--limit` and legacy paths
  keep the bound exactly.
- **Evidence:** reviewer `skipprobe.py postgres`, and the orchestrator's re-run
  (`/tmp/claude-501/hurv-orch/f1probe.py postgres …`, plain PostgreSQL 18, row trigger skipping one
  row N times): `hs update -f -R group=5` with N=4 → exit 0, "Updated 4 tasks", DEBUG "attempt 2/3/4
  of 4"; with N=5 → exit 5, "contended after 4 attempts". `hs update -f --limit 4 -R group=5` with
  N=4 → exit 5, "contended after 4 attempts" (4 statements, the bound).
- **Impact:** still bounded and all-or-nothing; one extra statement for a missed row, and a
  misreported attempt count. The fix is small (give the residual pass `CONTENDED_RERUNS` attempts,
  or count the set-based statement as attempt 1). Graded CRITICAL by the rubric's rule for any §1
  violation, not for its consequence.
- **Touches invariant / requirement:** invariants §1 (the `CONTENDED_RERUNS` bound and its WARNING);
  R4 (the re-runs before "nothing written").

### F2 [LOW/CONFIRMED] invariants §1 credits `delete_matching` with `RETURNING id`
- **Where:** `.agents/factory/invariants.md:53-54`.
- **Failure scenario:** The clause "`Task.update_matching`/`delete_matching` (… on PostgreSQL
  `RETURNING id` and one residual read of the filter find the rows missed …)" is wrong for the
  delete: `delete_matching` takes the DELETE's rowcount and one residual read of the ids still
  matching (`model.py:1281-1286`), with no `RETURNING`. AGENTS.md's wording ("the UPDATE returns the
  ids it wrote") is accurate.
- **Evidence:** the prose read against `model.py:1269-1292` at `ee5fcd4`.
- **Touches invariant / requirement:** R7 (the invariant record's accuracy).

### F3 [LOW/CONFIRMED] No test pins a `--delete` that leaves a row no longer matching
- **Where:** `tests/test_contended_recovery.py`, `tests/test_contended_write.py`.
- **Failure scenario:** R2 lists `--delete` among the forms that must leave a counted row that no
  longer matches, and R3 the "Deleted N" count with its warning. Every delete test asserts all rows
  deleted ("Deleted 4 tasks"); none has a row leave the filter between the count and the delete.
  The behaviour is correct by drive, so this is coverage only.
- **Evidence:** `grep -n "no longer matched\|NOT_MATCHED\|def test_.*delete"` over the three test files
  finds the warning asserted only by update tests (`test_contended_recovery.py:1424,1440`,
  `test_contended_write.py:947`); the reviewer's race probes (`--limit -R --delete`, filtered
  `--delete`, holder completing the row) show "Deleted 3" plus the warning, exit 0.
- **Touches invariant / requirement:** R2, R3 (test coverage), R6.

No PLAUSIBLE findings. Candidates the reviewer refuted and dropped: double counting (`landed` and
`missed` are disjoint); unbounded re-runs (one residual read, each per-row loop bounded); a write
landing without its condition (every per-row write carries `id AND criteria`); rows deleted or moved
out of the filter between the UPDATE and the residual read (the lookup skips them); bind-name
collisions between the user's criteria, the tag literals and the `u_` binds; the filter's where-list
mutated more than once.

## Human-gate triggers

- **Triggered.** F1 is CONFIRMED in `data/model.py` (high-blast-radius core) and against invariants §1's
  re-run bound. Human sign-off is required before any further step, whatever the remediation.
- The DB-lifecycle invariants named in the gate list (`CANCEL_STATUS`/`exit_status` filters,
  `completion_time` terminality, one retry row per failure, `in_memory` write guards) are not hit:
  `--cancel` still stamps `schedule_time`, `completion_time` and `CANCEL_STATUS` together; `--revert`
  clears `Task.revert_columns`; no new failure/retry query was added; a `--limit -R --cancel` leaves a
  result that landed meanwhile in place (race probe).

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Review cycle 2 — approved (2026-11-15)

- **Reviewed commit:** 839dfff8ebb1e0c23b7b5bd2a845bbf70a09b903  ·  **Base:** `fbc656e` (`origin/develop`, unchanged since cycle 1)
- **Verdict:** approved
- **Cycle:** 2 of ≤3
- **Mode:** a fresh blind pass over the full spec-excluded diff, which also graded the remediation of
  cycle 1's F1–F3. One reviewer; no `debate`, no `completeness` sub-pass. The reviewer got
  `GOAL.md`, the drive scripts, `red.txt`, `ci-red.txt` and `evidence/ci-red/`, and the base copies
  of `invariants.md` and `AGENTS.md`. It also got the GOAL-gate, plan-gate (D1–D5, risks, wording)
  and review-cycle-1 rulings as contract readings, plus a neutral statement of F1–F3. It did not
  see `PLAN.md`, `TECH.md`, `research/`, `META.md`, this file, or branch commit bodies. The
  orchestrator re-ran the deciding evidence.

### Pre-flight

- **Base.** `origin/develop` was fetched at the start and again at the end. Both times it was
  `fbc656e`, the merge base. The cycle-2 delta outside `spec/` (`git diff ee5fcd45 HEAD -- .
  ':(exclude)spec/'`) is four files: invariants §1, `data/model.py` (the `tried` parameter and its
  two call sites), `tests/test_contended_recovery.py` and `tests/test_contended_write.py`.
- **Build state.** `TECH.md` reads `status: in_progress`, with P7 (CI green on the fix, an external
  wait on the draft PR) `pending`. CI on this head is the build's to record and was not checked
  here (no GitHub calls).
- **Contract drift.** None after the lock: `GOAL.md`'s history is still the shaping commit `86bd92d`
  and the GOAL-gate lock `f00f853`.
- **Merge.** Clean against `origin/develop`, which is the merge base. PR #110's head
  (`origin/fix/task-input-fidelity`) has moved to `424cefd` since cycle 1. Against it,
  `git merge-tree --write-tree HEAD origin/fix/task-input-fidelity` reports no conflict (tree
  `6221a92`). That merge tree was exported and the affected suites run from it:
  - SQLite (`test_update`, `test_list`, `test_contended_recovery`, `test_task_wire`,
    `test_stream_input`): 258 passed.
  - `test_contended_write` and `test_writeback_postgres`: 70 passed, 1 skipped (the 2.8.3
    template) on plain PostgreSQL 18, and the same on TimescaleDB 2.32.

### Verification run

Orchestrator (sandbox off for PostgreSQL; test fixtures create and drop their own databases):

- Full SQLite suite, serial: `uv run pytest -q -p no:cacheprovider` → 2869 passed, 136 skipped, 0
  failed.
- Docs: `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-hurv2`
  → exit 0 with no Sphinx warnings. `cli/task_update.html` renders seven "Changed in version 2.9.0"
  blocks.
- The seven tests added since cycle 1 (`test_update_filtered_skipped_row_lands_at_bound[update|delete]`,
  `test_update_filtered_skipped_row_past_bound_writes_nothing[update|delete]`,
  `test_contended_update_delete_leaves_row_moved_out_of_filter[filtered|limit]`,
  `test_update_delete_limit_skips_task_changed_out_of_filter`) → 7 passed on plain PostgreSQL 18 and
  7 passed on TimescaleDB 2.32.
- `RED_BASE_REV=ee5fcd45 RED_LIST=<the four F1 lines of red.txt> sh spec/relay-hardening/drive/red_check.sh`
  on plain PostgreSQL 18 → 4 of 4 RED (AssertionError). The F1 tests tell the cycle-1 code from the
  fix.

Reviewer (its own `hurv2r_` databases, all dropped; tree clean on hand-back):

- `tests/test_contended_recovery.py tests/test_update.py tests/test_list.py` (SQLite) → 78 passed.
- `tests/test_contended_write.py` → 44 passed, 1 skipped, on plain PostgreSQL 18 and on TimescaleDB
  2.32.
- `red_check.sh` with `RED_BASE_REV=f00f853` and the full `red.txt` on plain PostgreSQL → 32 of 32
  RED.
- `red_check.sh` with `RED_BASE_REV=ee5fcd45` and the four F1 tests → 4 of 4 RED, on both servers.
- An attempt-counting probe:
  - **Setup.** A row trigger bumps a non-transactional sequence, so the count survives a rollback,
    on top of the `skipping` trigger. It ran on both servers, over the filtered update, the
    filtered `--delete`, the `--limit` update, `--limit --delete` and the legacy form.
  - **At HEAD,** every form makes exactly 4 attempts on the skipped row, both at the bound (3
    skips: exit 0, written) and past it (4 skips: exit 5, every row unchanged). Past the bound the
    log shows `WARNING … did not land for task (<id>) after 4 attempts - nothing written|deleted`,
    then `CRITICAL … Contended: Update of task (<id>) was contended after 4 attempts - nothing was
    written`.
  - **At `ee5fcd45`,** the two filtered forms make 5 attempts with 4 skips and exit 0. This is the
    cycle-1 defect.
- SQL capture on PostgreSQL (`before_cursor_execute`):
  - The filtered update sends `UPDATE … WHERE … RETURNING task.id`, then one `SELECT task.id … WHERE …`.
  - The filtered `--delete` sends `DELETE FROM task WHERE …` with no `RETURNING`, then the same
    `SELECT`.
- Compressed TimescaleDB chunks: six filtered and `--limit` forms, update and delete. Every
  "Updated N" and "Deleted N" equals the rows written.
- Base-vs-HEAD differential drive in `temp_site.sh` (SQLite; 17 `hs list` queries and 14 `hs update`
  scenarios): only the D2 lines differ (`-t a b`, `-t b a`).
- `update_drive.sh` → 6 of 6 PASS on sqlite, postgres and timescale.
- Other callers of `Task.update` (`test_zone_postgres`, `test_writeback_postgres`, `test_update`,
  `test_list`) → 54 passed on plain PostgreSQL, and the same on TimescaleDB.

### Cycle-1 findings

- **F1 [CRITICAL] — fixed.** `update_matching` and `delete_matching` pass `tried=1` (`model.py:1268`,
  `:1296`), so a missed row gets attempts 2 to 4 by id (`model.py:1111-1118`). The WARNING and
  `Task.Contended` (`:1151-1155`) say "after 4 attempts", the true total. The `--limit` and legacy
  paths pass no `tried` and run `range(1, 5)` with no extra DEBUG line, as before. Evidence: the F1
  tests are red on `ee5fcd45` and green at HEAD; the attempt-counting probe gives 4 attempts on
  every form.
- **F2 [LOW] — fixed.** invariants §1 now says that the UPDATE returns its ids (`RETURNING id`),
  while the DELETE takes its row count and the one read of the ids still matching, with no
  `RETURNING`. The SQL capture shows exactly that. `AGENTS.md` was already right.
- **F3 [LOW] — fixed.** `test_update_delete_limit_skips_task_changed_out_of_filter` (SQLite, `--limit`)
  and `test_contended_update_delete_leaves_row_moved_out_of_filter[filtered|limit]` (the real race)
  each assert "Deleted 3 tasks", the warning and exit 0. All three pass at HEAD and are red on
  `f00f853`.

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `Task.update` → `__write_rows(abort=True)`; `update_legacy` maps a gone row's `StaleDataError` to `Task.NotFound`; `Task.Contended` in `exceptions` | `test_update_legacy_contended_lands_on_rerun`, `…_exhausted_writes_nothing`, `…_missing_task_not_found`; `test_contended_update_legacy_lands` (red on CI 2.31 only); attempt probe; CLI drive of a missing id (CRITICAL NotFound, exit 5) | ✅ |
| R2 | `update_selected`/`delete_selected` (by id under the filter); `update_matching`/`delete_matching` (set-based, then the PostgreSQL residual read) | `…_skips_task_changed_out_of_filter`, `test_update_limit_cancel_keeps_result_landed_meanwhile`, `test_contended_update_*_lands`, `…_leaves_row_moved_out_of_filter` (now `--delete` too), `test_update_filtered_skipped_row_written`/`…_deleted` | ✅ |
| R3 | `TaskUpdateApp.report_written` | `NOT_MATCHED` assertions (update and now delete); `update_drive.sh` prompt scenario on three databases; compressed-chunk probe | ✅ |
| R4 | `abort=True` on every `hs update` path; `Task.Contended` → `handle_exception`, CRITICAL, exit 5 | exhausted tests (incl. `--limit` over 150 rows); the past-bound F1 tests; attempt probe across five forms | ✅ |
| R5 | `Task.__change`: one SET for every change | `tests/test_update.py` R5 tests; `test_update_combined_changes_pg[*]`, `test_update_tag_keys_and_removals_pg[*]`; `update_drive.sh` on three databases | ✅ |
| R6 | `DriverContention` kinds; the PostgreSQL `skipping` trigger; `contend` | `red.txt` 32 of 32 RED at `f00f853`; `evidence/ci-red/failed.log`: the five real-race tests red only on the 2.31.0 jobs | ✅ (CI green is P7's) |
| R7 | `task_update.rst` (seven `versionchanged:: 2.9.0`), `task_update_desc.rst`, `task_search.rst`, AGENTS.md, invariants §1 | docs build `-W` exit 0; prose checked against the SQL capture and the attempt probe | ✅ |
| D2 | the shared tag-criteria builder | `test_list_name_only_tags_check_every_tag`, `test_update_name_only_tags_check_every_tag`; differential drive (only `-t a b`/`-t b a` differ) | ✅ |
| Set-based pin | `update_matching` | `test_update_filtered_uncontended_is_one_statement`, `test_update_filtered_uncontended_statements_pg`; SQL capture | ✅ |
| D4, D5 | early return; `handle_exception` | drive: "Updated 0 tasks", exit 0, no warning; CRITICAL lines in the probe | ✅ |

Unmapped changes are as in cycle 1 and none is harmful:
- the argument-order sentence in `task_update_desc.rst`, which the reviewer drove and found accurate;
- `--revert` using `Task.revert_columns` (the same thirteen columns);
- `UPDATE_BATCH_SIZE` removed (no other user);
- test infrastructure;
- the seed and `ROADMAP.md` status flip.

### Findings

None. No CONFIRMED and no PLAUSIBLE findings.

Candidates refuted and dropped:
- **A row that starts matching only after the set-based statement.** It is handed over with
  `tried=1`, so it gets three attempts by id while the messages say four. The review-cycle-1 ruling
  counts the set-based statement as each missed row's first attempt, and the case needs such a row
  to be contended three times in a row. Not reproduced.
- **`tried` at or above the bound.** The loop would make no attempt, but the only value any caller
  passes is 1.
- **Bind-name collisions, the `commit=False` rollback, and `remove=True` with `tried`.** All correct
  in the probes: an abort rolls back the rows the set-based statement had already written.
- **Counts on compressed chunks.** Exact.
- **Any change to the `--limit` and legacy paths in cycle 2.** None: `tried` defaults to 0.

### Human-gate triggers

- **Not triggered this cycle.** There is no CONFIRMED finding. Cycle 1's trigger (F1 in
  `data/model.py`, against invariants §1) was discharged by the maintainer's sign-off on the
  review-cycle-1 rulings, given before the remediation.
- The remediation is itself a `data/model.py` change: the `tried` parameter, in 27 changed lines.
  This cycle verified it by executed evidence: the F1 tests red on `ee5fcd45`, and the
  attempt-counting probe on both servers.
- The DB-lifecycle invariants are not hit. The cycle-2 delta changes only how many attempts the
  filtered form makes on a missed row.

### Optional completeness sub-pass

- Not run (not requested).
