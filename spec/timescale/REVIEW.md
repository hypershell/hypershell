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
