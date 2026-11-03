# REVIEW — TimescaleDB provider: hypertables out of the box (TS1)

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

## Review cycle 1 — changes-requested (2026-11-02)

- **Reviewed commit:** `77d34bd`  ·  **Base:** `d996579` (`origin/develop`)  ·  **Date:** 2026-11-02
- **Verdict:** changes-requested
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`
- **Mode:** a fresh blind pass over `git diff d996579...77d34bd -- . ':(exclude)spec/'`, then the
  orchestrator's own pass against `PLAN.md`, `TECH.md` and the plan gate rulings. The reviewer got
  no `git log` and no commit subjects. An API usage limit cut the blind reviewer off once. It
  was resumed in the same context with its rules restated, so it stayed blind.
- **Contract drift:** `GOAL.md` changed in `7ff9155` (shaping) and `caf7007`. `caf7007` recorded
  the 2026-11-02 shaping rulings before planning: R9 narrowed to option (a), the R6 deferral
  terms, and the seed adopted whole. Nothing changed during the build.
- **TimescaleDB evidence:** given, not re-run (no local server). CI run 41548945416 at `8f367d0`:
  all 14 checks green. `timescale-floor` and `timescale-oss` each had 98 passed and 1 skipped;
  `pg18` had 84 passed and 15 skipped. Claims about TimescaleDB behaviour are checked against
  upstream source at the cited tags.

### Verification run

| Command | Result |
|---|---|
| SQLite suite, Python 3.14, fresh venv (`uv run --frozen --python 3.14 pytest -q`) | 587 passed, 48 skipped |
| SQLite suite, Python 3.12, fresh venv (`uv run --frozen --python 3.12 pytest -q`) | 587 passed, 48 skipped |
| `pytest -rs tests/test_timescale.py` (local, no server) | 52 passed, 18 skipped (all integration tests) |
| `pytest --collect-only -m 'not unit and not integration' tests/test_timescale.py` | 0 of 70 collected: every test is marked |
| `pytest tests/test_initdb.py tests/test_upgrade_cli.py tests/test_upgrade_postgres.py` (reviewer) | 39 passed, 29 skipped |
| SQLite CLI end to end, base `d996579` (`git archive`) vs branch (reviewer) | identical normalized output (initdb ×2, submit, list, vacuum, backup, truncate, rotate, `sqlite_master`) |
| Sphinx html build | only the 2 pre-existing toctree warnings (`task_submit.rst`, `manual.rst`) |
| `hs initdb --yes` under `timescale` with invalid `[database.timescale]` values (reviewer `cfg.py`) | `"soon"`, `"1 year"`, `columnstore = false` and an unknown key exit `bad_config` (3) with one CRITICAL line naming the key |
| Unit fakes driving the real `_Pass.run()` and `prepare()` (reviewer `fake_pass.py`, `fake_prepare.py`; orchestrator equivalents) | reproduce F1–F3 below |
| `pglast` 8.7 (libpg_query) parse of `SELECT FROM "public"."task" GROUP BY group` and `ORDER BY group DESC` | syntax error at `group`; the quoted `"group"` parses |

### Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R2 | `data/__init__.py` (`initdb`: `prepare` → `upgradedb` → `converge`; `truncatedb` reconverges after `create_all`), `data/timescale.py`, placeholder warning removed from `data/core.py` | CI `test_fresh_initdb`; SQLite end to end identical to base; every new path is behind `TIMESCALE` | ✅ |
| R3 | `_Pass._columnstore`/`_policy`, using the 2.18+ vocabulary | Signatures checked upstream (`sql/policy_api.sql`, `sql/ddl_api.sql` at 2.22.0 and 2.31.0); CI `assert_converged` | ✅ |
| R4 | probe-then-change pass; `COALESCE(time_interval, integer_interval …)` covers the UUID dimension before 2.25 (`sql/views.sql` adds `uuid` to the time types only at 2.25.0) | CI `test_rerun_is_quiet`, `test_truncate_recreates`, `test_drift_restored`, `test_configured_drift`, `test_policy_off_removes_job`, `test_concurrent_initdb` | ❌ F1 (a configured `orderby` without `id` never converges) |
| R5 | `prepare` probe ladder; `extension_error`; Apache gate; non-empty plain-table warning | CI `test_absent_fails_before_schema` (pg13, pg18), `timescale-oss`, `test_nonempty_plain_table_untouched`, `test_legacy_database_upgraded_not_converted`; unit `test_extension_error_mapping`; `trusted = true` in `timescaledb.control.in` (no superuser) | ✅ (F3: a docs claim is wrong) |
| R6 | `Settings.from_config`; import-time `bad_config` exit; `_no_policy`; columnstore-off warning | unit `test_settings_*`; `cfg.py`; CI `test_configured_*`, `test_policy_off_removes_job` | ❌ F1, F2 |
| R7 | `MIN_VERSION` 2.22.0, `POLICY_VERSION` 2.25.0, `gates()` | unit `test_gates`; CI `timescale-floor`; upstream CHANGELOG #8505 (2.22.0) and #9045 (2.25.0) | ✅ |
| R8 | `uuid7` guard kept (`core.py`); docs and `install.rst` | CI `test_v4_id_rejected` (SQLSTATE 0A000, whole INSERT aborted) | ✅ |
| R9 | `tests.yml`: `timescale` 2.31.0, `-oss` 2.31.0 and 2.24.0-pg17, pinned by digest; `TIMESCALEDB_TELEMETRY: 'off'`; `seal_template` | CI run 41548945416; `seal_template` returns early unless `shared_preload_libraries` names `timescaledb` (no-op on pg13 and pg18, green) | ✅ |
| R10 | `docs/_include/database.rst` TimescaleDB section, `config_param_ref.rst`, `database_alt.rst`, `install.rst` | every bullet present; `to_uuidv7_boundary`/`uuid_timestamp` exist at 2.22.0 (`sql/uuidv7.sql`); PG15 dropped at 2.29.0 (CHANGELOG #10041); Sphinx clean | ✅ (F3: the "create nothing" line) |

Unmapped changes (possible scope creep): none. The fixture sealing and the CI matrix map to R9; the
`server-db-test-suite` seed and its ROADMAP entry map to the R9 ruling; the `timescale` pop maps to R6.

**Plan gate rulings (orchestrator):**

- **1, R7 option (a):** honoured. Below 2.25.0, `gates()` refuses the policy, and the logged WARNING
  names 2.25.0. The floor entry asserts it (`assert_tier_warnings`).
- **2, five-entry job:** honoured. The 2.24.0-pg17 digest and its source are recorded in TECH P1
  and in the workflow comment.
- **3, P5/P7 evidence phases:** recorded with the run id and per-entry results.
- **TECH "not touched" list:** holds. No change to `data/model.py`, `data/upgrade.py`, `core/*`,
  `share/` or `pyproject.toml`.
- **Same-commit rule:** P6 (`9c853d0`) updated `config_param_ref.rst` with the config change.

**Shared lock and transaction scope:**

- `converge` takes the upgrade step's `pg_advisory_xact_lock` key under the same `lock_timeout`
  slice and retry loop.
- `upgradedb()` commits and releases its connection before `converge` opens its own, so no
  connection holds the lock across both steps. Every session takes the advisory lock before its
  table locks, so there is no deadlock path.
- `create_hypertable` runs in a savepoint, and the pass commits once or rolls back in `finally`.
- `CREATE EXTENSION` runs in autocommit outside the lock, and the race SQLSTATEs are handled.

### Findings

#### [MEDIUM/CONFIRMED] F1 — A configured `orderby` that omits `id` never converges: every `hs initdb` re-alters the table
- **Where:** `src/hypershell/data/timescale.py:143-149` (any task column accepted) and `:488-506`
  (compare with `hypertable_columnstore_settings.orderby`; ALTER on any difference).
- **Failure scenario:**
  - The user sets `[database.timescale] orderby = "submit_time DESC"`.
  - On `ALTER TABLE … SET (timescaledb.orderby = …)`, TimescaleDB appends the partition column
    (`id DESC NULLS FIRST`) when neither `orderby` nor `segmentby` holds it.
  - The view then reads `submit_time DESC,id DESC`, which never equals the configured value.
  - So every `hs initdb` and every `--initdb` launch re-issues the ALTER, takes its lock on `task`,
    and logs INFO "Changed the columnstore settings of task …".
  - This contradicts R4 (re-runs converge) and the docs at `docs/_include/database.rst:201` ("A
    database already in shape logs nothing").
- **Evidence:**
  - Upstream `tsl/src/compression/create.c`: `add_time_to_order_by_if_not_included` is called on
    the explicit-orderby ALTER path at 2.22.0, 2.24.0 and 2.31.0 (2.31.0 L1045-1076, L2323).
  - The view renders `format('%I%s%s')` items joined by `,` (`sql/views.sql`).
  - The reviewer's `fake_pass.py` drives the real `_Pass.run()` three times and gets three
    `changes` entries and three identical ALTERs. Runs 2 and 3 should be no-ops.
- **Requirement:** R4, R6.

#### [MEDIUM/CONFIRMED] F2 — `[database.timescale]` validation accepts `segmentby`/`orderby` values the server rejects, so they fail at `hs initdb` instead of at startup
- **Where:** `src/hypershell/data/timescale.py:138-149` (validation) and `:497-500` (emitted
  unquoted). The unit test asserts that `'client_id, group'` is valid
  (`tests/test_timescale.py:653-654`).
- **Failure scenario:**
  - **The `group` column:** `segmentby` or `orderby` naming the task column `group` passes
    validation. TimescaleDB splices the option text raw into `SELECT FROM s.t GROUP BY <value>`
    (or `ORDER BY`) and parses it, and `GROUP` is a reserved keyword. The pass therefore fails
    with "unable to parse segmenting option". The quoted form `"group"` is refused by the
    validator (`\w+`), so the column cannot be used at all.
  - **Duplicates:** `segmentby = "client_id, client_id"` also passes validation, and upstream
    rejects it as a duplicate column.
  - **Effect:** `converge` raises `TimescaleError` and exits `runtime_error`, with no traceback
    and the pass rolled back. A fresh database stays plain until the config is fixed. R6 requires
    invalid values to fail at startup, with guidance.
- **Evidence:**
  - Orchestrator: `Settings.from_config({'segmentby': 'group', 'orderby': 'group DESC'})` is
    accepted, and the real `_Pass._columnstore()` on a fake connection emits
    `… timescaledb.segmentby = 'group', timescaledb.orderby = 'group DESC')`.
  - pglast 8.7: `… GROUP BY group` and `… ORDER BY group DESC` give a syntax error at `group`.
  - Upstream `src/with_clause/alter_table_with_clause.c` (2.24.0, and 2.31.0 L162-190): format
    `"SELECT FROM %s.%s GROUP BY %s"` with the user text inserted raw, then `raw_parser`; the
    duplicate-column error is at L256-261.
  - PostgreSQL `kwlist.h`: `group` is `RESERVED_KEYWORD`.
  - Reviewer `cfg.py`: these values reach the connection (rc 5, connection refused), while the
    invalid interval, boolean and key values exit 3.
- **Requirement:** R6.

#### [LOW/CONFIRMED] F3 — "Create nothing" is false when the server offers TimescaleDB older than 2.22.0
- **Where:** `src/hypershell/data/timescale.py:260-271` (`prepare` runs `CREATE EXTENSION` before
  checking the version), against `docs/_include/database.rst:171-175` ("… and create nothing, …
  when the extension is older than 2.22.0") and the module docstring at `:9` ("Every failure
  here leaves the database untouched").
- **Failure scenario:** the extension is not yet created in the database and the server's
  `default_version` is 2.21.x. `hs initdb` creates the extension at 2.21.x, then refuses with
  the too-old message. The guidance (`ALTER EXTENSION timescaledb UPDATE`) fits that end state,
  but the docs and the docstring do not.
- **Evidence:** a fake engine driving the real `prepare()`, run by both orchestrator and
  reviewer. It issues `CREATE EXTENSION IF NOT EXISTS timescaledb`, then raises
  `TimescaleError: TimescaleDB 2.21.3 is too old …`, and the extension stays at 2.21.3. Fix
  either the code (compare `available` with `MIN_VERSION` before creating) or the two texts.
- **PLAUSIBLE sub-case (read only, not reproduced):** the extension is already created but its
  library is not preloaded. `prepare` passes because it reads only `pg_extension`. The failure
  then surfaces in the schema step or in `converge`, not before any table is touched.
- **Requirement:** R10 (docs accuracy), R5.

#### [LOW/CONFIRMED] F4 — The new `server-db-test-suite` seed and its ROADMAP entry misdescribe CI as this diff leaves it
- **Where:** `issues/server-db-test-suite.md:17-20` and `ROADMAP.md:332-333`.
- **Failure scenario:** both say the `postgres` job (pg13, pg18) runs only
  `tests/test_upgrade_postgres.py`, and that the CLI is never exercised on PostgreSQL or
  TimescaleDB. At `77d34bd` the job has five entries and also runs `tests/test_timescale.py`,
  and `test_run_compress_list_vacuum` drives `hs submit`, `hs cluster` and `hs list` on
  TimescaleDB. The seed's core gap is still true: the whole suite never runs on a server database.
- **Evidence:** compare `.github/workflows/tests.yml` (matrix and the "Run PostgreSQL tests"
  step) with the cited lines.
- **Requirement:** none (seed prose, graded per the rubric).

**Dropped after refutation:**

- Segmentby/orderby overlap: no upstream rejection was found at 2.31.0.
- Background-worker wording: consistent with the upstream docs.
- `[database.timescale]` no longer forwarded as a URL parameter under `postgres`: the base made a
  broken `?timescale=…` parameter, and R6 intends the change.
- `share/` provider completions: unchanged from the base.
- `to_uuidv7_boundary` "since 2.23.0" in the upstream reference: the function is in
  `sql/uuidv7.sql` at tag 2.22.0, so the docs' "since 2.22.0" stands.

### Human-gate triggers

- Not triggered. No CONFIRMED finding touches a high-blast-radius file or a security or
  DB-lifecycle invariant. Every finding is in `data/timescale.py`, the docs, the tests or seed prose.
- The `§1–§11` invariants: no violation found.

### Next step

`/hs-build` to remediate R4/R6 (F1, F2) and the two texts (F3, F4):

- **F1:** have the comparison account for the partition column TimescaleDB appends. Either
  normalize `id DESC` into the configured `orderby` when neither list holds `id`, or require `id`
  in `orderby`.
- **F2:** either quote identifiers when emitting `segmentby`/`orderby`, and accept the quoted
  rendering in the comparison, or refuse reserved words and duplicates at validation. Add unit
  cases either way, and stop asserting `'client_id, group'` as valid unless it is made to work.
- **F3:** either check the available version before `CREATE EXTENSION`, or correct the docs line
  and the docstring.
- **F4:** correct the seed's and the ROADMAP entry's description of the `postgres` job.

After that, cycle 2 should re-run the TimescaleDB entries in CI.

## Triage — cycle 1 (foreman, as judge under the maintainer's delegation, 2026-11-02)

| Finding | Ruling | Disposition |
|---|---|---|
| F1 · CONFIRMED | fix | Convergence compares like with like. Before comparing a configured `orderby` with the view, normalise it with TimescaleDB's own rule (`add_time_to_order_by_if_not_included`: append `id DESC` when the partitioning column is absent), citing the upstream source. Red-first test: a configured `orderby` without `id`, run twice through the real pass with a fake connection, ALTERs once and then no more. |
| F2 · CONFIRMED | fix | `segmentby`/`orderby` are validated as lists of real `task` column names (no duplicates, each with an optional `ASC`/`DESC`/`NULLS FIRST|LAST` for orderby), and every column is emitted through SQLAlchemy's identifier quoting, so that `group` works and junk is refused at startup, not at `initdb`. Correct the test at `tests/test_timescale.py:653` to expect the quoted emission. Red-first tests: `group` accepted and emitted quoted; a duplicate is refused at validation; an unknown column is refused at validation. |
| F3 · CONFIRMED | fix | Check the offered version (`pg_available_extension_versions`/`pg_available_extensions`) **before** `CREATE EXTENSION`, and refuse a version below 2.22.0 having created nothing, as the docs promise. Treat the PLAUSIBLE created-but-not-preloaded sub-case the same way: check preload before creating. Red-first unit test with a fake connection: no `CREATE EXTENSION` is issued when the offered version is too old. |
| F4 · CONFIRMED | fix | Correct `issues/server-db-test-suite.md:17-20` and `ROADMAP.md:332`: after this cycle, the postgres job runs the upgrade tests and `test_timescale.py` across five entries. |

## Review cycle 2 — changes-requested (2026-11-03)

- **Reviewed commit:** `30a3efb`  ·  **Base:** `6f216d0` (`origin/develop`, after `writeback-scoped-conditional` landed; the branch merged it at `a9ab876`)  ·  **Date:** 2026-11-03
- **Verdict:** changes-requested
- **Cycle:** 2 of ≤3 — mirrors `review.cycle` in `TECH.md`. One cycle remains before escalation.
- **Mode:** a fresh blind pass over `git diff 6f216d0...30a3efb -- . ':(exclude)spec/'`, with no
  `git log` and no commit subjects (META F5). The orchestrator then checked the cycle-1
  remediation against the triage above, the merge resolution, and the preload probe against
  upstream sources.
- **Contract drift:** none since cycle 1. `GOAL.md` changed only in `7ff9155` (shaping) and
  `caf7007` (the 2026-11-02 shaping rulings).
- **TimescaleDB evidence:** given, not re-run (no local server). Run `41581150777` at `a9ab876`:
  all 14 checks green. `timescale`, `timescale-oss` and `timescale-floor` each had 130 passed and
  1 skipped, with `test_extension_created` and `test_configured_columns_converge` PASSED. `pg13`
  and `pg18` each had 115 passed and 16 skipped (TECH P5/P7).

### Verification run

| Command | Result |
|---|---|
| SQLite suite, Python 3.14, fresh venv (`uv run --frozen --python 3.14 pytest -q`) | 625 passed, 63 skipped |
| SQLite suite, Python 3.12, fresh venv (`uv run --frozen --python 3.12 pytest -q`) | 625 passed, 63 skipped |
| `pytest -rs tests/test_timescale.py` (local, no server) | 69 passed, 19 skipped (all integration) |
| `pytest --collect-only -m 'not unit and not integration' tests/test_timescale.py` | 0 of 88 collected: every test is marked |
| Sphinx html build into a scratch dir | only the 2 pre-existing toctree warnings (`task_submit.rst`, `manual.rst`) |
| `Settings.from_config` + `quote_columns`/`effective_orderby` probe (orchestrator) | `group` → `"group"`; duplicates, unknown and mixed-case columns, `id; DROP TABLE task` and a trailing comma are refused; `segmentby = "client_id"` with `orderby = "client_id DESC"` is **accepted** (F5) |
| Reviewer CLI drive, own throwaway site, provider `timescale` | invalid `chunk_interval` → rc 3 and one CRITICAL line; overlapping `segmentby`/`orderby` → rc 0 at startup (F5) |
| Reviewer fake driving the real `converge()` with upstream's overlap check | `TimescaleError: … cannot use column "client_id" for both ordering and segmenting …`, then ROLLBACK |
| Reviewer probe recording which helper each launch calls | `hs server --initdb` → `checkdb`; `hs submit --initdb` → `initdb` (F8) |
| Reviewer: SQLite CLI end to end, base `6f216d0` vs head | identical apart from UUIDs |
| Reviewer: libpg_query (pglast 8.7) parse of every emitted statement and option clause | 42 statements, 0 errors |

### Cycle-1 remediation (orchestrator)

| Finding | Remediated? | Evidence |
|---|---|---|
| F1 orderby convergence | ✅ | `effective_orderby` (`timescale.py:355-364`) appends `id DESC` when neither list names `id`. That is upstream's rule: `add_time_to_order_by_if_not_included` (`tsl/src/compression/create.c`, 2.31.0) checks both orderby and segmentby and appends DESC NULLS FIRST. `normalize_columns` drops the implied NULLS FIRST. Unit `test_orderby_without_id_converges`: one ALTER, then none. CI `test_configured_columns_converge` PASSED on all three TimescaleDB entries. |
| F2 identifier validation and quoting | ✅ (one cross-list gap, F5) | `_check_setting` (`:142-159`) accepts only real `task` columns, bare or quoted, each once per list. `quote_columns` (`:367-378`) emits each name through the dialect's `IdentifierPreparer`. The `tests/test_timescale.py:653` assertion was corrected (`test_group_column_emitted_quoted`). `test_settings_duplicate_column` and `test_settings_unknown_column` cover the refusals. |
| F3 offered-version and preload checks before `CREATE EXTENSION` | ✅ | `prepare` (`:266-291`) reads `pg_extension`, `pg_available_extensions.default_version` (the version `CREATE EXTENSION` installs) and the preload probe first. It refuses a too-old offer or a missing preload before creating anything, and refuses a created-but-not-preloaded extension before the schema step. `test_prepare_refuses_old_offer_before_creating`, `…_without_preload_before_creating` and `…_created_without_preload` cover this. The docs (`database.rst:170-175`) and the docstring now hold. |
| F4 seed prose | ✅ at `ae38532`, then ❌ after the merge | Corrected for five entries and two modules. The `develop` merge then added `test_writeback_postgres.py` to the job, which makes the prose wrong again (F7). |

**Merge resolution (`a9ab876`, `.github/workflows/tests.yml`):** correct. The postgres step runs
`test_upgrade_postgres.py`, `test_writeback_postgres.py` and `test_timescale.py`. The comment keeps
both sides: develop's "matches returned results to their dispatch on PostgreSQL" and the branch's
TimescaleDB paragraph. `6f216d0` is an ancestor of the head. The branch's diff to the workflow is
purely additive, so no `develop` change was lost.

**Preload probe (`_preloaded`, `timescale.py:305-313`):** correct and safe.
- **Ordinary roles can't read `shared_preload_libraries`.**
  - PostgreSQL `src/backend/utils/misc/guc_tables.c` (REL_18_STABLE) declares it `PGC_POSTMASTER`
    with `GUC_SUPERUSER_ONLY`.
  - `guc_funcs.c` `ConfigOptionIsVisible` hides such a setting from roles without
    `pg_read_all_settings`.
  - `guc.c` `GetConfigOptionByName` then raises "permission denied to examine" even with
    `missing_ok`, because `missing_ok` covers only an unknown name.
  - The docs (Predefined Roles) agree: `pg_read_all_settings` "allows reading all configuration
    variables, even those normally visible only to superusers", and `pg_monitor` is a member.
  - So the claim is right, with one nuance: superusers and members of `pg_read_all_settings` or
    `pg_monitor` can read the setting.
- **The probe itself.**
  - TimescaleDB 2.31.0 `src/loader/loader.c` `_PG_init` calls `extension_load_without_preload()`
    unless `process_shared_preload_libraries_in_progress`. That function, in
    `src/extension_utils.c`, raises FATAL "extension "timescaledb" must be preloaded".
  - It does so before `ts_bgw_counter_setup_gucs()` (`src/loader/bgw_counter.c`), which defines
    `timescaledb.max_background_workers` (`PGC_POSTMASTER`, default 16).
  - So the GUC exists in a session only when the loader was preloaded.
  - `current_setting(name, true)` returns NULL for an unknown name (PostgreSQL docs, Table 9.95).
- **Observation (not a finding):**
  - PostgreSQL makes a placeholder for any unknown dotted name set in `postgresql.conf`, by
    `ALTER ROLE/DATABASE … SET`, or by `SET` (`guc.c` `add_placeholder_variable`), and
    `current_setting` reads the placeholder.
  - So a leftover `timescaledb.max_background_workers` line on a server that no longer preloads
    the library reads as "preloaded".
  - The outcome stays safe. A not-yet-created extension then fails at `CREATE EXTENSION` with
    FATAL "must be preloaded", which `extension_error` maps to the preload message, and nothing is
    created. An already-created one reaches `converge`, which maps the same text. That is the
    cycle-1 PLAUSIBLE sub-case, reachable only through this misconfiguration.
  - `pg_settings` hides placeholders (`GUC_NO_SHOW_ALL`), if a stricter probe is ever wanted.

### Requirement → evidence matrix (blind reviewer, orchestrator-checked)

| R-ID | Verified how | Status |
|------|--------------|--------|
| R2 | SQLite end to end identical to base; `initdb`/`truncatedb` wiring behind `TIMESCALE`; CI `test_fresh_initdb` | ✅ |
| R3 | pglast parse of every emitted statement; upstream signatures; CI `assert_converged` | ✅ |
| R4 | 10 configured segmentby/orderby variants on the upstream-modelled fake: one ALTER, then none; CI rerun, truncate, drift and concurrency scenarios | ✅ (F1 fixed) |
| R5 | `FakeServer` unit tests; `trusted = true` upstream; CI absent, `-oss`, non-empty and legacy scenarios | ✅ |
| R6 | CLI rc 3 for invalid values; a fuzz of 47 values × 7 keys raises only `ConfigurationError` | ❌ F5 |
| R7 | `test_gates`; upstream CHANGELOG #8505 and #9045; CI `timescale-floor` | ✅ |
| R8 | CI `test_v4_id_rejected`; docs | ✅ |
| R9 | workflow read; run `41581150777`; skip counts consistent (16 `with_timescale` skip on plain entries, 1 `without_timescale` skips on TimescaleDB) | ✅ (F7 is prose) |
| R10 | Sphinx clean; upstream facts checked | ❌ F6, F8 (docs accuracy) |

Unmapped changes (possible scope creep): none.

### Findings

#### [MEDIUM/CONFIRMED] F5 — Validation accepts a column named in both `segmentby` and `orderby`, which TimescaleDB always rejects, so the config fails at `hs initdb` instead of at startup
- **Where:** `src/hypershell/data/timescale.py:142-159`, where each list is checked on its own.
  The only cross-field check is policy against columnstore (`:114-118`).
- **Failure scenario:**
  - A user sets `orderby = "client_id DESC"` and keeps the default `segmentby = "client_id"`.
    Equally, a user sets `segmentby = "id"` with the default `orderby = "id DESC"`.
  - Every command starts. `hs initdb` then exits `runtime_error`, and the whole pass rolls back,
    including the hypertable conversion of a fresh `task`.
  - Commands without `--initdb` keep writing to the plain table. Once it holds rows, a corrected
    config can no longer convert it: initdb warns "holds rows and is a plain table".
  - R6 requires invalid values to fail at startup, with guidance.
- **Evidence:**
  - Upstream `src/ts_catalog/compression_settings.c` `ts_compression_settings_update`, at 2.22.0
    (L328-343) and 2.31.0: for any orderby column in segmentby, `ERRCODE_SYNTAX_ERROR` "cannot use
    column "%s" for both ordering and segmenting".
  - The orchestrator's probe accepts `{'orderby': 'client_id DESC'}` and `{'segmentby': 'id'}`.
  - The reviewer's CLI drive starts with rc 0, and its fake `converge()` raises `TimescaleError`
    and rolls back.
- **Not a restated ruling:** the cycle-1 triage required no duplicates *within* a list. This is
  across the two lists. Cycle 1 dropped the overlap after failing to find the upstream check; the
  check is at the path above.
- **Requirement:** R6.

#### [LOW/CONFIRMED] F6 — "They work on the `task` hypertable like any other" over-claims for part of the supported range
- **Where:** `docs/_include/database.rst:393-395`.
- **Failure scenario:** the docs support TimescaleDB 2.22.0 and later. On UUIDv7 hypertables,
  upstream added:
  - `show_chunks`/`drop_chunks` with time arguments in 2.23.0 (#8746);
  - continuous aggregates in 2.24.0 (#8939);
  - retention policies in 2.25.0 (#9102).

  Tiered storage is Tiger Cloud only, and GOAL non-goals call it "Not a shipped claim".
- **Evidence:** upstream `CHANGELOG.md` at 2.31.0 (sections 2.23.0, 2.24.0 and 2.25.0) and the PR
  bodies. GOAL's own non-goal says continuous aggregates are supported "since 2.24.0".
- **Requirement:** R10.

#### [LOW/CONFIRMED] F7 — After the `develop` merge, the seed, its ROADMAP entry and the CI comment misdescribe the postgres job
- **Where:** `issues/server-db-test-suite.md:19-22`, `ROADMAP.md:386-389` and
  `.github/workflows/tests.yml:49-50`.
- **Failure scenario:**
  - The seed says each entry "runs two modules". `tests.yml:105` runs three.
  - The seed and the ROADMAP entry say the writeback is exercised on SQLite only, or never on
    PostgreSQL. `test_writeback_postgres.py` runs on every entry.
  - The CI comment says the plain entries "run only the scenario for a server without the
    extension". They run three integration tests: `test_server_matches_entry`,
    `test_absent_fails_before_schema` and `test_invalid_setting_exits`, which are only
    `needs_server`/`without_timescale`. That fits the given 115 passed / 16 skipped.
- **Still true:** the seed's core gap, its `status: unshaped`, the five entries and the image list.
- **Evidence:** `tests.yml:105`; the markers at `tests/test_timescale.py:141-143`, `:476-479` and
  `:958-960`; the reviewer's dead-port run with `HS_TEST_TIMESCALE=''`.
- **Requirement:** none (seed and comment prose, graded per the rubric).

#### [LOW/CONFIRMED] F8 — The docs imply `hs server --initdb` runs the TimescaleDB pass; it never runs `initdb`
- **Where:** `docs/_include/database.rst:162`, `:199-200` and `:212-213` (new), against
  `src/hypershell/server.py:1381` (unchanged from base).
- **Failure scenario:**
  - `ServerApp.run` calls `ensuredb()` without `auto_init`. `auto_initdb` (`:1343-1346`) is never
    read, unlike `submit.py:1373` and `cluster/__init__.py:543`.
  - So under `timescale`, `hs server --initdb` runs only `checkdb`. That is the schema step, or
    a refusal on a fresh database, and never the pass.
  - The new lines ("initialize … with `hs initdb` or `--initdb` at launch"; "Other commands
    (`hs cluster`, `hs server`, `hs submit` without `--initdb`) only run the schema step") imply
    otherwise.
- **Evidence:** the reviewer's probe gives `hs server --initdb` → `['checkdb']` and
  `hs submit --initdb` → `['initdb']`. Reading `server.py:1374-1384` confirms it.
- **Disposition note:**
  - The defect in `server.py` predates the diff, and the older PostgreSQL line
    `database.rst:106-107` shares it.
  - The finding graded here is the new docs text, which can be fixed this cycle without touching
    `server.py`. So the rubric's deferral exception does not apply to it.
  - Fixing `server.py` instead would change non-timescale PostgreSQL behaviour (R2's last
    sentence) and touch the high-blast-radius core. The pre-existing defect is a candidate seed
    for the judge.
- **Requirement:** R10.

**Dropped after refutation (reviewer):** `chunk_interval` in months (upstream counts a month as 30 days,
and interval equality normalises); ALTER with compressed chunks (allowed upstream); explicit
`segmentby = ""` (upstream accepts it, and it converges); `tag` as segmentby (JSONB); identifier and
literal emission in quoted schemas (safe); an invalid setting blocking `hs config set` (the base does
the same for an invalid provider); concurrent `CREATE EXTENSION` SQLSTATEs beyond 23505/42710, and
phantom schema-step diffs on hypertables (speculative); `share/` completions (no CLI change).
Orchestrator: the seed's "in one of them, `hs submit`, `hs cluster` and `hs list`" reads as true of
`test_run_compress_list_vacuum`.

### Human-gate triggers

- Not triggered.
  - F5 lies in `data/timescale.py`, F6 and F8 in the docs, and F7 in the seed, ROADMAP and
    workflow-comment prose.
  - None touches a high-blast-radius file or a security or DB-lifecycle invariant.
  - If the judge chooses to fix F8 in `server.py` rather than in the docs, that change touches the
    core and needs the human gate.
- The `§1–§11` invariants: no violation found.

### Next step

`/hs-build` to remediate:

- **F5:** refuse at validation a column named in both lists, with a message naming the column.
  Add a unit case for each of the two natural configs above.
- **F6:** state the version for each feature: show/drop chunks 2.23.0, continuous aggregates
  2.24.0, retention policies 2.25.0, tiered storage Tiger Cloud only. Or drop the "like any other"
  claim.
- **F7:** say three modules, and stop calling the writeback SQLite-only, in the seed and the
  ROADMAP entry. Name what the plain entries run in the CI comment.
- **F8:** say that `hs server` ignores `--initdb` (run `hs initdb` first), or name only `hs submit`
  and `hs cluster` for `--initdb`.

Then cycle 3, the last before escalation. The CI evidence needs a re-run only if F5 changes code
the integration scenarios exercise.

## Triage — cycle 2 (foreman, as judge under the maintainer's delegation, 2026-11-03)

| Finding | Ruling | Disposition |
|---|---|---|
| F5 · CONFIRMED | fix | Validate `segmentby` and `orderby` **together** at startup: a column may not appear in both, mirroring TimescaleDB's check (cite `compression_settings.c`). The check runs on the *effective* lists: the configured or default segmentby, and the effective orderby after F1's append rule. That way both natural cases are refused at startup with one clear message naming the column, rc as for other config errors: `orderby = "client_id DESC"` with the default segmentby, and `segmentby = "id"` with the default orderby. Red-first unit tests for both, plus a same-list duplicate guard staying green. |
| F6 · CONFIRMED | fix | `database.rst:393-395`: state precisely, with the version floors and upstream refs the review cites, which TimescaleDB features work on UUIDv7 hypertables (show/drop chunks 2.23.0, continuous aggregates 2.24.0, retention policies 2.25.0). Drop the tiered-storage claim (managed-offering only; not a shipped claim). |
| F7 · CONFIRMED | fix | Correct `issues/server-db-test-suite.md:19-22` ("three modules"; writeback's PostgreSQL test is not SQLite-only), its `ROADMAP.md` entry, and the `tests.yml:49-50` comment (say exactly which integration tests the plain entries run). |
| F8 · CONFIRMED | fix docs + seed | Make `database.rst` (:162, :199-200, :212-213) describe what the code does today: which commands run the TimescaleDB pass (`hs initdb`, and any start path that actually calls it, which you must verify), and that `hs server --initdb` does not run it. Do **not** change `server.py`. Seed the pre-existing `--initdb`/`auto_initdb` defect as `unshaped`, with a `ROADMAP.md` entry and the code reference, as a correctness issue. |
