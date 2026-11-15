---
slug: hs-update-contended-writes
title: hs update writes every requested change to exactly the rows it counts, contended
  or not, and says plainly when it cannot
kind: fix
appetite: big
status: blocked
branch: fix/hs-update-contended-writes
base: develop
current_phase: P7
last_updated: '2026-11-15'
phases:
- id: P1
  name: 'Tests first: R6''s fault-injection, real-race, trigger and CLI tests and
    the red lists (tests only)'
  status: done
  satisfies:
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; spec=spec/hs-update-contended-writes;
    git diff --quiet f00f853 -- src .github docs share && test -z "$(git diff --name-only
    f00f853 -- . ':!spec' ':!tests')" && for t in test_contended_update_legacy_lands
    test_contended_update_filtered_lands test_contended_update_limit_lands test_contended_update_delete_lands
    test_contended_update_delete_limit_lands test_contended_update_leaves_row_moved_out_of_filter
    test_update_filtered_skipped_row_written; do grep -q "^def $t(" tests/test_contended_write.py
    || exit 1; done && test "$(grep -cvE '^#|^$' $spec/red.txt)" -ge 20 && test "$(grep
    -cvE '^#|^$' $spec/ci-red.txt)" -ge 5 && test -f $spec/drive/ci_check.py && test
    -f $spec/drive/rest_green.sh && for ts in '' community; do RED_BASE_REV=f00f853
    RED_LIST="$PWD/$spec/red.txt" HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/relay-hardening/drive/red_check.sh
    || exit 1; done && sh $spec/drive/rest_green.sh && for ts in community ''; do
    HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh $spec/drive/rest_green.sh -rs tests/test_contended_write.py
    tests/test_writeback_postgres.py || exit 1; done
- id: P2
  name: CI red at the tests-first commit (external wait on the draft PR)
  status: done
  satisfies:
  - R6
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python spec/hs-update-contended-writes/drive/ci_check.py red spec/hs-update-contended-writes/evidence/ci-red
    --code-rev "$(git log -1 --format=%H --grep='^\[fix\] Build hs-update-contended-writes
    P1:')"
- id: P3
  name: 'The legacy form on the re-run helper: Task.update, Task.Contended, the abort
    at the bound'
  status: done
  satisfies:
  - R1
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; spec=spec/hs-update-contended-writes;
    uv run pytest -q -p no:cacheprovider tests/test_contended_recovery.py -k 'not
    update_limit and not update_delete_limit and not update_filtered' && uv run pytest
    -q -p no:cacheprovider tests/test_cancel.py tests/test_writeback.py tests/test_dispatch_staged.py
    tests/test_server_fsm.py tests/test_retry_gate.py tests/test_source.py && for
    ts in community ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts uv run pytest
    -q -p no:cacheprovider -rs tests/test_contended_write.py tests/test_writeback_postgres.py
    -k 'test_contended_update_legacy or not (test_update_ or test_contended_update_)'
    || exit 1; done && for ts in '' community; do RED_BASE_REV=f00f853 RED_LIST="$PWD/$spec/red.txt"
    HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/relay-hardening/drive/red_check.sh
    || exit 1; done
- id: P4
  name: '--limit: one filter, one SET, per-row writes under the filter in one transaction;
    counts and the warning'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P3
  parallel: false
  hammerable: false
  hill: downhill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; spec=spec/hs-update-contended-writes;
    uv run pytest -q -p no:cacheprovider tests/test_contended_recovery.py -k 'not
    update_filtered' && uv run pytest -q -p no:cacheprovider tests/test_update.py
    tests/test_list.py tests/test_cancel.py -k 'not moved_out_of_filter and not cancel_and_tag
    and not set_and_remove' && for ts in community ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts
    uv run pytest -q -p no:cacheprovider -rs tests/test_contended_write.py tests/test_writeback_postgres.py
    -k 'test_contended_update_legacy or test_contended_update_limit or test_contended_update_delete_limit
    or (combined_changes_pg and limit) or not (test_update_ or test_contended_update_)'
    || exit 1; done && for ts in '' community; do RED_BASE_REV=f00f853 RED_LIST="$PWD/$spec/red.txt"
    HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/relay-hardening/drive/red_check.sh
    || exit 1; done && out=${TMPDIR:-/tmp}/hu-drive-p4.txt; (sh $spec/drive/update_drive.sh
    sqlite > $out; HS_TEST_POSTGRES=$pg sh $spec/drive/update_drive.sh timescale >>
    $out; true) && for s in 'sqlite limit' 'sqlite prompt' 'timescale limit' 'timescale
    prompt'; do grep -qx "PASS $s" $out || exit 1; done
- id: P5
  name: 'Without --limit: one set-based SET, the PostgreSQL residual read, DELETE
    alike'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  depends_on:
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; spec=spec/hs-update-contended-writes;
    uv run pytest -q -p no:cacheprovider tests/test_contended_recovery.py tests/test_update.py
    tests/test_list.py tests/test_cancel.py tests/test_restart.py && for ts in community
    ''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts uv run pytest -q -p no:cacheprovider
    -rs tests/test_contended_write.py tests/test_writeback_postgres.py tests/test_zone_postgres.py
    || exit 1; done && for ts in '' community; do RED_BASE_REV=f00f853 RED_LIST="$PWD/$spec/red.txt"
    HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/relay-hardening/drive/red_check.sh
    || exit 1; done && sh $spec/drive/update_drive.sh sqlite && HS_TEST_POSTGRES=$pg
    sh $spec/drive/update_drive.sh postgres && HS_TEST_POSTGRES=$pg sh $spec/drive/update_drive.sh
    timescale
- id: P6
  name: Docs (versionchanged 2.9.0), AGENTS.md and invariants §1 drop the hs update
    exception; full local evidence
  status: done
  satisfies:
  - R7
  - R6
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'pg=postgresql+psycopg://postgres:postgres@localhost:5432/postgres; spec=spec/hs-update-contended-writes;
    ! grep -q ''does not follow it yet'' AGENTS.md .agents/factory/invariants.md &&
    ! grep -q ''issues/hs-update-contended-writes.md'' AGENTS.md .agents/factory/invariants.md
    && grep -q ''update_matching'' .agents/factory/invariants.md && grep -q ''update_matching''
    AGENTS.md && grep -q ''Task.Contended'' AGENTS.md && grep -q ''Task.Contended''
    .agents/factory/invariants.md && grep -q ''versionchanged:: 2.9.0'' docs/cli/task_update.rst
    && grep -q ''no longer matched'' docs/cli/task_update.rst && grep -q ''no longer
    matched'' docs/_include/task_update_desc.rst && UV_PROJECT_ENVIRONMENT=${TMPDIR:-/tmp}/docs-venv
    uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b html docs
    ${TMPDIR:-/tmp}/docs-html-hu && uv run pytest -q -p no:cacheprovider && for ts
    in community ''''; do HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts uv run pytest
    -q -p no:cacheprovider -rs tests/test_contended_write.py tests/test_writeback_postgres.py
    tests/test_zone_postgres.py || exit 1; done && for ts in '''' community; do RED_BASE_REV=f00f853
    RED_LIST="$PWD/$spec/red.txt" HS_TEST_POSTGRES=$pg HS_TEST_TIMESCALE=$ts sh spec/relay-hardening/drive/red_check.sh
    || exit 1; done'
- id: P7
  name: CI green on the fix (external wait on the draft PR)
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P2
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run python spec/hs-update-contended-writes/drive/ci_check.py green spec/hs-update-contended-writes/evidence/ci-green
    --code-rev "$(git log -1 --format=%H -- . ':!spec')"
review:
  last_reviewed_commit: ee5fcd45d51f101dabb8f72b84581c9043c96fe0
  verdict: changes-requested
  blocked_reason: 'review cycle 1: F1 PostgreSQL residual pass re-runs a missed row
    CONTENDED_RERUNS+1 times (invariants §1); F2 invariants prose; F3 delete coverage;
    human gate'
  cycle: 1
---
# TECH.md — `hs update` writes every requested change to exactly the rows it counts, contended or not, and says plainly when it cannot

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/hs-update-contended-writes/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one
atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs and probes.

## Frontmatter field reference

As in [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the touched invariants are
  [`invariants.md`](../../.agents/factory/invariants.md) §1, §2, §3 (unchanged) and §12 (PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build hs-update-contended-writes P<n>: …` (P2's verify finds the P1 commit by that
  subject). **No `Co-Authored-By` trailer.** Signed commits.
- **Every verify runs with the sandbox off**: it reaches the local PostgreSQL 18 + TimescaleDB 2.32
  (`HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`; the one local
  server serves both `HS_TEST_TIMESCALE=community` and empty) and the suite starts servers. Set
  `UV_CACHE_DIR` and `TMPDIR` to writable directories. As in #109, `tests/test_upgrade_postgres.py` (no
  Docker-built templates locally) and `tests/test_timescale.py` (two server-identity checks refuse
  the shared local server) are never run by a verify; CI runs them.
- **The base is `f00f853`** (develop `fbc656e` + the GOAL; `src/` identical). `red_check.sh` (from
  `spec/relay-hardening/drive/`, reused as is) exports its `src` and runs each [`red.txt`](red.txt)
  line against it; every phase re-runs it, so the tests keep proving the defect.
- **Test names carry the slice**, so verifies can select them: SQLite fault injection
  `test_update_legacy_*`, `test_update_limit_*`, `test_update_delete_limit_*`, `test_update_filtered_*`
  (`tests/test_contended_recovery.py`); CLI `tests/test_update.py`; server database
  `test_update_*` (trigger and CLI) and `test_contended_update_*` (real race) in
  `tests/test_contended_write.py`. A phase may rename a node; it updates `red.txt`/`ci-red.txt` and the
  verifies with it, and drops none without a PLAN §5 note.
- **`hs update` command lines in tests and docs put the changes before `-t`** (`-t` takes every
  following word; research/02). Top-level `hs` commands only.
- No option changes: no `share/` completion, help text or `docs/_include/task_update_help.rst` change.

### Where each test is expected red

PLAN §6 has the table. In short: `red.txt` = the SQLite fault-injection and CLI tests red on the base
(`tests (3.x)` jobs on CI) and the `pg:` trigger, PostgreSQL CLI and moved-out-of-filter real-race tests
(red on PG 18 and TS 2.32 locally and on every `PostgreSQL (…)` job); [`ci-red.txt`](ci-red.txt) = the
five outcome-only real-race tests, red on `timescale` and `timescale-oss` (2.31.0) only. Guards (green
before and after): legacy missing id; `--limit` uncontended cost; SQLite CLI several keys / several
removals; the PostgreSQL uncontended statement pin.

---

## Phase P1 — Tests first (tests only)
**Satisfies:** R6 · **Depends on:** —
**Goal:** every R6 test in the tree, red on the base for the defect, with nothing else red; no `src/`
change. This commit is what the foreman pushes to the draft PR for P2.

- [x] `tests/test_contended_recovery.py`: `DriverContention(engine, kinds=('mark', 'cancel'))` — the
      kinds it may lose, default unchanged; `classify` also names `DELETE FROM task WHERE task.id = ?`
      kind `'DELETE'`; `hs update`'s tests use `kinds=('UPDATE', 'cancel', 'DELETE')`. A helper runs
      `TaskUpdateApp.main(argv)` in-process on the per-test engine (`monkeypatch` replaces
      `hypershell.task.ensuredb`; `-f` always), returning the exit status, with `logs` capturing.
      A `meanwhile`/`waits` change targets the first row the update writes (SQLite's write lock is
      held from the first write to the commit; research/01 §4).
- [x] Legacy: `test_update_legacy_contended_lands_on_rerun` (lose once: exit 0, row written, one DEBUG
      re-run), `test_update_legacy_contended_exhausted_writes_nothing` (lose always: exit 5, a record at
      ERROR or above naming the task and saying contended and nothing written, row unchanged, no
      "traceback written" line), guard `test_update_legacy_missing_task_not_found` (exit 5, not found).
- [x] `--limit`: `test_update_limit_contended_lands_on_rerun` (4 rows, `--limit 4 -R group=7 color:red`,
      one row lost once: every row both changes, `Updated 4 tasks`, exit 0);
      `test_update_limit_contended_exhausted_writes_nothing` (150 rows, `--limit 150`, the 120th lost
      always: exit 5, no row changed, the error naming it, no traceback);
      `test_update_limit_skips_task_changed_out_of_filter` (the first row written completed meanwhile
      with `exit_status = 0`: left with its result, `Updated 3 tasks`, the warning `1 of the 4 tasks
      counted no longer matched and were left as they are`, exit 0);
      `test_update_limit_cancel_keeps_result_landed_meanwhile` (the same with `--cancel`); guard
      `test_update_limit_uncontended_costs_one_update_per_row` (one UPDATE per row, no SELECT of `task`
      after the selection).
- [x] `--delete --limit`: `test_update_delete_limit_contended_lands_on_rerun`,
      `test_update_delete_limit_contended_exhausted_deletes_nothing`. If the base's ORM delete only
      warns (`SAWarning`) the red is the assertion that the row is gone; if pytest turns the warning
      into an error, mark the line ` raises=…` with that class.
- [x] Pin: `test_update_filtered_uncontended_is_one_statement` — `group=7 color:red --remove-tag size
      -f -w group==0` over 5 and over 30 rows: exactly one UPDATE of `task` each time (the base runs
      three).
- [x] `tests/test_update.py` (`@mark.integration`, subprocess, `temp_site`):
      `test_update_limit_applies_every_change`, `test_update_tag_reaches_rows_moved_out_of_filter`,
      `test_update_cancel_and_tag_together` (the GOAL's three R5 lines; every row both changes;
      "Updated N" equal to the rows written); `test_update_tag_keys_and_removals[keys|removals|set_and_remove]`
      (`color:blue size:big -f -t color:red`; `-f --remove-tag color size -t color:red`;
      `color:blue --remove-tag size -f -t color:red`).
- [x] `tests/test_contended_write.py`: `blocked_by` matches `UPDATE task` or `DELETE FROM task`
      (research/03 §3). A fixture installs the skip trigger (research/01 §1: control table
      `skip_once(id uuid, times int)`, `BEFORE UPDATE OR DELETE … FOR EACH ROW` returning NULL while the
      row has times left) on `contended_db`. Trigger tests (subprocess `hs update`):
      `test_update_filtered_skipped_row_written` (`-f -R group=5`, one row skipped once: all 4 written,
      `Updated 4 tasks`), `test_update_filtered_skipped_row_exhausted_writes_nothing` (skipped always:
      exit 5, no row changed, the error naming it), `test_update_delete_filtered_skipped_row_deleted`
      (`-f -R --delete`, skipped once: none left, `Deleted 4 tasks`). R5 on PostgreSQL:
      `test_update_combined_changes_pg[limit|moved|cancel]`, `test_update_tag_keys_and_removals_pg[keys|removals|set_and_remove]`.
      Guard `test_update_filtered_uncontended_statements_pg`: in-process `TaskUpdateApp.main` on the
      bound session (`ensuredb` replaced), field change only (`group=7 -f -w group==0`: the change
      before `-w`, which takes every following word), a
      `before_cursor_execute` recorder on the engine: exactly one UPDATE of `task` and the same number
      of statements on `task` for 5 and for 30 rows.
- [x] Real race (`contend`, `hs update … -f` through `tests.main` in the worker; the holder `UPDATE task
      SET client_host = 'holder'` on one of the rows; outcome-only): `test_contended_update_legacy_lands`
      (`hs update ID group 3`: exit 0, written), `test_contended_update_filtered_lands` (`-R group=5`:
      all 4), `test_contended_update_limit_lands` (`--limit 4 -R group=5`: all 4, exit 0),
      `test_contended_update_delete_lands` (`-R --delete`: none left), `test_contended_update_delete_limit_lands`
      (`--limit 4 -R --delete`: none left); and `test_contended_update_leaves_row_moved_out_of_filter`
      (the holder sets `exit_status = 0` on its row; `-R group=5`: 3 written, that row's group 0,
      `Updated 3 tasks` and the warning — red on the base by its count everywhere).
- [x] `spec/hs-update-contended-writes/drive/`: copy `ci_check.py` and `rest_green.sh` from
      `spec/contended-writes/drive/` (they read their own spec directory; fix the docstrings' paths).
      `update_drive.sh` is already there (plan commit; dry-run on the base: every scenario FAIL on
      PostgreSQL and TimescaleDB, `keys`/`removals` PASS on SQLite).
- [x] Plan-gate ruling D2: `tests/test_update.py::test_update_name_only_tags_check_every_tag` and
      `tests/test_list.py::test_list_name_only_tags_check_every_tag` (`-t a b` over tasks tagged `a b`,
      `b` and `a`: only the first; SQLite, red on the base, which checks only the last name).
- [x] [`red.txt`](red.txt): every test above that is not a guard and not CI-only, `pg:` for the server
      ones; [`ci-red.txt`](ci-red.txt): `timescale,timescale-oss` for the five outcome-only real-race
      tests. Record `red_check.sh`'s output (every line RED, both `HS_TEST_TIMESCALE` settings) in the
      commit body.
- **Verify:** see frontmatter (no `src`/`.github`/`docs`/`share` change; the server tests exist; ≥20
  and ≥5 listed; the drives copied; `red_check.sh` all red on the base for PG 18 and TS 2.32; the rest
  of the suite and the server modules otherwise green).
- **Touches:** `tests/test_contended_recovery.py`, `tests/test_update.py`, `tests/test_list.py` (D2),
  `tests/test_contended_write.py`,
  `spec/hs-update-contended-writes/{red.txt,ci-red.txt,drive/}`.

## Phase P2 — CI red at the tests-first commit (external wait)
**Satisfies:** R6 · **Depends on:** P1
**Goal:** CI shows the R6 tests red before the fix, each on the jobs expected and nowhere else.

- [ ] **External wait.** The foreman pushes `fix/hs-update-contended-writes` **at the P1 commit** and
      opens the draft PR into `develop`; `hs-build` never pushes. Nothing past P1 is pushed until this
      run is recorded. P3 may be built locally meanwhile.
- [ ] The foreman records the run under `spec/hs-update-contended-writes/evidence/ci-red/`
      (`run.json` from `gh run view <id> --json databaseId,headSha,conclusion,jobs`; `failed.log` from
      `gh run view <id> --log-failed`).
- [ ] If a job differs from the table (a real-race test green on 2.31; the `--delete` ones may be, if
      #10902's DELETE case does not reproduce through the CLI), stop and report: the foreman decides.
      A `--delete` real-race test green there is recorded as the finding and stays as the guard (GOAL
      Evidence plan); its line moves out of `ci-red.txt` with a PLAN §5 note.
- **Verify:** `ci_check.py red` on the recorded evidence, with the P1 commit as `--code-rev`.
- **Touches:** `spec/hs-update-contended-writes/evidence/ci-red/`.

## Phase P3 — The legacy form (R1, R4)
**Satisfies:** R1, R4 · **Depends on:** P1
**Goal:** `hs update ID FIELD VALUE` lands under contention, reports not found only for a missing row,
and at the bound writes nothing, logs the contended error and exits 5.

- [x] `src/hypershell/data/model.py`: `class Contended(DatabaseError)` beside `NotFound`;
      `Task.Contended`. `__write_rows` gains `abort: bool = False` (at the bound: today's WARNING, then
      rollback and raise `Task.Contended` with `Update of task ({id}) was contended after N attempts -
      nothing was written`). Docstring.
- [x] `Task.update(id, **changes)` overrides `Entity.update`: `__write_rows([id], changes, 'update',
      'nothing written', abort=True, prefix='u_')`. A gone row still raises `StaleDataError`.
- [x] `src/hypershell/task.py`: `TaskUpdateApp.exceptions` maps `Task.Contended` to
      `handle_exception` with `exit_status.runtime_error`; `update_legacy` keeps `StaleDataError` →
      `Task.NotFound`.
- [x] The legacy tests pass; every existing contended test still passes.
- **Verify:** see frontmatter (the recovery module minus the `--limit`/filtered slices; the server
  write modules; the server-database modules minus the new slices but with the legacy real race, on PG
  18 and TS 2.32; `red_check.sh`).
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/task.py`.

## Phase P4 — `--limit` (R2–R5)
**Satisfies:** R2, R3, R4, R5 · **Depends on:** P3
**Goal:** `--limit` applies every change to every selected row still matching at its write, in one
transaction, counts the rows written, and writes nothing when contended at the bound.

- [x] `task.py` `SearchableMixin.filter_criteria()` (cached; PLAN §2.1): the `-w`/alias clauses
      through `WhereClause.compile()`, the tag criteria with the SQLite name-only tag as
      `func.json_extract(Task.tag, literal('$."name"')).isnot(None)`; `build_query()` filters on them
      (subject to the foreman's ruling D2; `tests/test_list.py` stays green).
- [x] `model.py` `Task.__change(values, tags, drop, dialect)` (PLAN §2.2) from the session bind's
      dialect; `__write_rows` gains `where=` (condition `'filter'`: match and lookup `id` plus the
      criteria, read `id`; no row → DEBUG `Ignored {path} for task ({id}) - task gone or no longer
      matches`, skipped; built per call), SET expressions in `values`, and `delete: bool = False`
      (built as `remove: bool = False`: a `delete` parameter would shadow SQLAlchemy's `delete()` in
      the helper; the statements come from `Task.__filter_statements`).
- [x] `Task.update_selected(ids, criteria, values, tags, drop) -> int` and
      `Task.delete_selected(ids, criteria) -> int`: one `__write_rows(…, where=criteria, abort=True,
      commit=False, prefix='u_')`, one commit, return the count written. Docstrings name the lock held
      to the commit. (Built with the helper's own commit: one call is the whole transaction, so
      `commit=False` plus a caller commit would be the same.)
- [x] `TaskUpdateApp.update_tasks` (`--limit` branch): criteria and change built once; `--revert` uses
      `Task.revert_columns`; ids selected after the prompt (`select(Task.id)`, order, limit); the call;
      WARNING `{K} of the {M} tasks counted no longer matched and were left as they are` when lower;
      `Updated N tasks` / `Deleted N tasks`; `UPDATE_BATCH_SIZE` and the shared iterator go.
      An update with nothing to change logs `Updated 0 tasks` (PLAN D4, as ruled).
- [x] The `--limit` slice's tests pass (SQLite recovery and CLI; PostgreSQL CLI `[limit]`, the
      `--limit` real races); `update_drive.sh` `limit` passes on SQLite and TimescaleDB.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/task.py`, `src/hypershell/data/model.py`.

## Phase P5 — Without `--limit` (R2–R5)
**Satisfies:** R2, R3, R4, R5 · **Depends on:** P4
**Goal:** the filtered form is one SET in one statement; on PostgreSQL its missed rows are found and
written or the update aborts; DELETE alike; counts are of rows written.

- [x] `Task.update_matching(criteria, values, tags, drop) -> int` (PLAN §2.6): `UPDATE … WHERE
      <criteria>` (PostgreSQL: `RETURNING id`); PostgreSQL only, the residual `SELECT id … WHERE
      <criteria>` minus the returned ids; those through `__write_rows(…, where=criteria, abort=True,
      commit=False)`; one commit; return the total. `Task.delete_matching(criteria) -> int`: `DELETE …
      WHERE <criteria>`; PostgreSQL residual = the filter alone; same re-run; one commit. Docstrings say
      why SQLite takes no residual and what the PostgreSQL UPDATE holds in memory.
- [x] `TaskUpdateApp.update_tasks` (no `--limit`): the calls; the same WARNING/INFO lines; the
      `DATABASE_DIALECT` tag branches go.
- [x] Every test in `red.txt` and `ci-red.txt` passes locally; `update_drive.sh` passes on SQLite,
      PostgreSQL and TimescaleDB.
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/task.py`, `src/hypershell/data/model.py`.

## Phase P6 — Docs, AGENTS.md, invariants (R7); full local evidence
**Satisfies:** R7, R6 · **Depends on:** P5
**Goal:** the user docs and the constitution say what the code now does, in the same change.

- [x] `docs/cli/task_update.rst`: `.. versionchanged:: 2.9.0` entries for the six user-visible changes
      (GOAL list), after the existing `versionadded`.
- [x] `docs/_include/task_update_desc.rst`: what "Updated N"/"Deleted N" count and the "no longer
      matched" warning; the filter re-checked as each task is written; a contended update writes
      nothing and exits 5, so rerunning it is safe; `--limit` holds its rows until it finishes;
      changes go before `-t`.
- [x] `AGENTS.md` § Data layer specifics and `.agents/factory/invariants.md` §1: the closing "`hs
      update` does not follow it yet" sentence goes; the pattern list gains `hs update` (`Task.update`
      by id, raising on a gone row; `update_selected`/`delete_selected` by id under the user's filter;
      `update_matching`/`delete_matching` set-based with the PostgreSQL residual read; all-or-nothing
      at the bound, `Task.Contended`). `AGENTS.md` § High-risk files' `data/model.py` line names the
      filter condition if it lists conditions.
- [x] `sphinx-build -W` clean; the full suite on SQLite; the server modules on both local servers.
- [x] As built, beyond the list: the plan-gate rulings' wording fix (the TimescaleDB miss dated
      2.28.1 through 2.31.0, fixed in 2.32.0, and named for DELETE too) in AGENTS.md, invariants §1,
      the `CONTENDED_RERUNS` docstring and the two contended test modules' docstrings; the plan-gate
      risks (SQLite's write lock for a long `--limit`, the PostgreSQL ids held in memory) as a
      sentence in `task_update_desc.rst`; ruling D2's `-t a b` fix as a `versionchanged` on
      `docs/cli/task_search.rst` too. The entries carry no `:pr:` role: the PR number is the
      publisher's to add.
- **Verify:** see frontmatter.
- **Touches:** `docs/cli/task_update.rst`, `docs/_include/task_update_desc.rst`,
  `docs/cli/task_search.rst`, `AGENTS.md`, `.agents/factory/invariants.md`, `src/hypershell/data/model.py`
  and `tests/test_contended_{recovery,write}.py` (docstrings only).

## Phase P7 — CI green on the fix (external wait)
**Satisfies:** R1–R7 · **Depends on:** P2, P6
**Goal:** every CI job green at the last code commit, the TimescaleDB 2.31 jobs included.

- [ ] **External wait.** The foreman pushes the branch to the draft PR and records the run under
      `spec/hs-update-contended-writes/evidence/ci-green/run.json`.
- **Verify:** `ci_check.py green`, with the last commit outside `spec/` as `--code-rev`.
- **Touches:** `spec/hs-update-contended-writes/evidence/ci-green/`.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative).
2. Pre-flight: clean tree, on `fix/hs-update-contended-writes`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command, sandbox off — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP and escalate only on a
   **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
