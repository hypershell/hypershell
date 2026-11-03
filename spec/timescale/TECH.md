---
slug: timescale
title: 'TimescaleDB provider: hypertables out of the box (TS1)'
kind: feature
appetite: big
status: in_review
branch: feature/timescale
base: develop
current_phase: done
last_updated: '2026-11-03'
phases:
- id: P1
  name: 'CI lane: pinned TimescaleDB entries (Community, Apache, floor) and the scenario-module
    scaffold'
  status: done
  satisfies:
  - R9
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: grep -q 'timescale/timescaledb:2.31.0-pg18@sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579'
    .github/workflows/tests.yml && grep -q 'timescale/timescaledb:2.31.0-pg18-oss@sha256:cc00b0ede968b0c6e55212bd55a9a195726afe5a9e19dcf8af49a13334e92ff3'
    .github/workflows/tests.yml && grep -q 'tests/test_timescale.py' .github/workflows/tests.yml
    && grep -q 'test_invalid_setting_exits' .github/workflows/tests.yml && grep -q
    'three modules' issues/server-db-test-suite.md && uv run python -c "import yaml;
    yaml.safe_load(open('.github/workflows/tests.yml'))" && uv run pytest -rs tests/test_timescale.py
    && uv run pytest
- id: P2
  name: 'Environment interrogation before the schema step: extension, privilege, preload,
    version floor'
  status: done
  satisfies:
  - R5
  - R7
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: downhill
  verify: grep -q 'def prepare' src/hypershell/data/timescale.py && grep -q 'class
    TimescaleError(SchemaUpgradeError)' src/hypershell/data/timescale.py && uv run
    pytest -rs -m unit tests/test_timescale.py && uv run pytest
- id: P3
  name: 'The idempotent pass: hypertable, columnstore, policy, drift, Apache and non-empty
    tiers'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: downhill
  verify: '! grep -q ''not yet implemented'' src/hypershell/data/core.py && grep -q
    ''def converge'' src/hypershell/data/timescale.py && grep -q ''add_columnstore_policy''
    src/hypershell/data/timescale.py && uv run pytest -rs tests/test_timescale.py
    && uv run pytest'
- id: P4
  name: 'Docs: TimescaleDB as a first-class database section'
  status: done
  satisfies:
  - R8
  - R10
  depends_on:
  - P2
  parallel: true
  hammerable: false
  hill: downhill
  attempts: 2
  verify: grep -q 'to_uuidv7_boundary' docs/_include/database.rst && grep -q 'pull/9102'
    docs/_include/database.rst && ! grep -qi 'tiered' docs/_include/database.rst &&
    grep -q 'server-initdb-ignored' ROADMAP.md && grep -q 'Tiger Cloud' docs/_include/database.rst
    && grep -q '0A000' docs/_include/database.rst && grep -q 'max_background_workers'
    docs/_include/database.rst && grep -q -i 'timescale' docs/_include/database_alt.rst
    && uv run sphinx-build -E -b html -D extensions=sphinx.ext.autodoc,sphinx.ext.napoleon,sphinx.ext.intersphinx,sphinx.ext.extlinks,sphinx.ext.viewcode,sphinxext.opengraph,sphinx_inline_tabs,sphinx_copybutton,sphinxcontrib.details.directive,sphinx_reredirects
    docs docs/_build 2>&1 | grep -i 'warning' | grep -v -e 'task_submit.rst' -e 'manual.rst'
    -e 'pkg_resources is deprecated' -e '^build succeeded' | (! grep .)
- id: P5
  name: 'CI evidence for the load-bearing slice (run B: external wait on the draft
    PR)'
  status: done
  satisfies:
  - R2
  - R3
  - R4
  - R5
  - R7
  - R8
  - R9
  depends_on:
  - P3
  - P4
  parallel: false
  hammerable: false
  hill: downhill
  attempts: 1
  verify: test "$(gh run list --branch feature/timescale --workflow tests.yml --commit
    "$(git log -1 --format=%H -- . ':!spec')" --json conclusion --jq '[.[] | select(.conclusion
    == "success")] | length')" -ge 1
- id: P6
  name: 'R6 (deferrable): the [database.timescale] override table, validation and
    drift'
  status: done
  satisfies:
  - R6
  - R4
  - R10
  depends_on:
  - P5
  parallel: false
  hammerable: true
  hill: downhill
  verify: grep -q "pop('timescale'" src/hypershell/data/core.py && grep -q 'def _check_disjoint'
    src/hypershell/data/timescale.py && grep -q 'compress_after' docs/_include/database.rst
    && grep -q 'timescale' docs/_include/config_param_ref.rst && uv run pytest -rs
    tests/test_timescale.py && uv run pytest
- id: P7
  name: CI evidence for R6 (run B)
  status: done
  satisfies:
  - R6
  - R9
  depends_on:
  - P6
  parallel: false
  hammerable: true
  hill: downhill
  verify: test "$(gh run list --branch feature/timescale --workflow tests.yml --commit
    "$(git log -1 --format=%H -- . ':!spec')" --json conclusion --jq '[.[] | select(.conclusion
    == "success")] | length')" -ge 1
review:
  last_reviewed_commit: 41d99ffa435c0a164ce2538d118471825ac44a9d
  verdict: approved
  blocked_reason: ''
  cycle: 3
---
# TECH.md — TimescaleDB provider: hypertables out of the box (TS1)

The **context engine and finite-state machine** for building this feature. The YAML
frontmatter above is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/timescale/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via
`uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract (R2–R10; there
  is deliberately no R1).
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Frontmatter field reference

- `status` (top): `planned | in_progress | blocked | in_review | done`.
- phase `status`: `pending | in_progress | done | blocked`.
- `satisfies`: GOAL R-IDs this phase delivers. `depends_on`: phases that must be `done` first.
- `parallel`: `true` only for independent docs/tests work. `hammerable: false` = never cut.
- `hill`: `uphill` → `crest` → `downhill`. `attempts`: durable failed-verify counter.
- `verify`: the exact command that proves the phase. Folded scalars (`>-`) are one shell line.

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the curated checklist is
  [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) (§1, §3, §10, §12 and the
  Data-layer schema-step paragraph apply; see PLAN §3).
- One phase per `hs-build` invocation; one atomic commit with code and `TECH.md` state. Subjects:
  `[feature] Build timescale P<n>: …`. **No `Co-Authored-By` trailer.** Stage explicit paths.
- No CLI flag changes in this cycle, so `share/` completions are untouched. Man pages are rebuilt at
  release (PLAN §5).
- Public repo: generic names only in code, tests, docs and comments.
- **No local TimescaleDB.** Local gates are unit tests, the SQLite suite, YAML parsing and the
  Sphinx build. Integration scenarios run only in CI; P5/P7 are the run-B evidence phases (the
  foreman pushes and opens the draft PR; record run id, head sha and per-entry results in the phase
  body when marking done).

### Plan gate rulings (foreman, as judge under the maintainer's delegation, 2026-11-02)

These resolve PLAN §5's two `[NEEDS CLARIFICATION]` items and bind the build.

1. **R7: option (a).** Below TimescaleDB 2.25.0 the columnstore policy is gated off, with a logged
   WARNING that names the upstream fix version (2.25.0). The decision rests on the 2.24.0→2.25.0
   source diff (#9045) and the release note, not on observing the failing job. The 2.24.x CI entry
   exercises the gate: hypertable and columnstore settings present, no policy job, the warning
   logged.
2. **Keep the floor entry.** The `postgres` job has five entries (`pg13`, `pg18`, `timescale`,
   `timescale-oss`, `timescale-floor`). The `2.24.x-pg17` tag and its index digest are resolved at
   build time through the Docker Hub tag API, and the source (endpoint and date read) is recorded
   here and in a workflow comment above the entry.
3. **CI-evidence phases.** META F3 is noted. P5 and P7 are external waits: the foreman pushes the
   branch, opens the draft PR into `develop`, reads CI and records the evidence; `hs-build` stops
   with the FSM at P5.

### Touched files (the whole cycle — for lane-collision checks)

| File | Phases | Change |
|---|---|---|
| `src/hypershell/data/timescale.py` | P2, P3, P6 | **new** — `Settings`, `DEFAULTS`, gates, `prepare`, `converge`, `TimescaleError` |
| `src/hypershell/data/__init__.py` | P2, P3, P6 | `initdb`/`truncatedb` call the pass behind `TIMESCALE`; settings load (P6) |
| `src/hypershell/data/core.py` | P3, P6 | delete placeholder warning; export `TIMESCALE_ALIASES`; pop `timescale` (P6) |
| `tests/test_timescale.py` | P1, P2, P3, P6 | **new** — unit + gated integration scenarios |
| `.github/workflows/tests.yml` | P1 | `postgres` job: three TimescaleDB matrix entries, env, run step |
| `docs/_include/database.rst` | P4, P6 | TimescaleDB section; config reference (P6) |
| `docs/_include/database_alt.rst` | P4 | short TimescaleDB paragraph (man-page source) |
| `docs/install.rst` | P4 | `uuid7` extra: required on every id-minting host under TimescaleDB |
| `docs/_include/config_param_ref.rst` | P4, P6 | `.provider` names `timescale`; `[timescale]` table (P6) |
| `issues/server-db-test-suite.md`, `ROADMAP.md` | review-1, review-2 | F4, F7: the `postgres` job as it now runs |
| `issues/server-initdb-ignored.md`, `ROADMAP.md` | review-2 | F8: **new** seed for the pre-existing `hs server --initdb` defect |
| `spec/timescale/*` | all | state |

Not touched: `data/model.py`, `data/upgrade.py`, `core/*`, `server.py`, `client.py`, `submit.py`,
`cluster/*`, `tests/conftest.py`, `tests/test_upgrade_postgres.py`, `share/`, `pyproject.toml`,
`uv.lock`.

---

## Phase P1 — CI lane and scenario scaffold
**Satisfies:** R9, R8 · **Depends on:** —
**Goal:** the TimescaleDB servers exist in CI and the existing upgrade tests pass on them before any
feature code lands, so every later push is exercised against a real TimescaleDB.

- [x] `.github/workflows/tests.yml`, `postgres` job: replace the placeholder with three `include`
      entries (PLAN §2.7) carrying `timescale: community|apache` (empty on `pg13`/`pg18`). Pin
      `2.31.0-pg18@sha256:0fcff576…d579` and `2.31.0-pg18-oss@sha256:cc00b0ed…2ff3` in full.
      Resolve the floor entry: the highest released `2.24.x-pg17` tag and its index digest from
      `https://hub.docker.com/v2/repositories/timescale/timescaledb/tags/<tag>` (`digest` field);
      record tag, digest and the date read in a comment above the entry.
      **Resolved 2026-11-02:** the Hub tag listing (`…/tags/?name=2.24.`) holds only `2.24.0-*`
      (no 2.24 patch release), so the floor is `2.24.0-pg17@sha256:8ab414f7f73fd47dfa643097f1a753e2ac009c70fcb0de5717d0e79d21e7b7d6`,
      read from `https://hub.docker.com/v2/repositories/timescale/timescaledb/tags/2.24.0-pg17`
      (`digest`, an index over amd64/arm64/386/arm). The two 2.31.0 digests were re-read the same
      day and match the plan.
- [x] Service `env`: add `TIMESCALEDB_TELEMETRY: 'off'`. Job `env`: `HS_TEST_TIMESCALE: ${{
      matrix.timescale }}`. Run step: `pytest -v -rs tests/test_upgrade_postgres.py
      tests/test_timescale.py`. Update the job's header comment to say what the TimescaleDB entries
      run.
- [x] `tests/test_timescale.py` scaffold: module docstring (env contract, as in
      `test_upgrade_postgres.py`); `ADMIN_URL`, `TIMESCALE = os.getenv('HS_TEST_TIMESCALE', '')`;
      fixtures `admin`, `ts_database(template=None)` (throwaway, `DROP … WITH (FORCE)`), `ts_site(name,
      schema=None, provider='timescale')` setting `HYPERSHELL_DATABASE_*` (pattern of
      `test_upgrade_postgres.pg_site`; do not edit that module); markers `integration` / `unit`.
- [x] `test_server_matches_entry` (integration): with `TIMESCALE` set, `extversion` exists and
      `timescaledb.license` equals `timescale` (community) or `apache` — fail, not skip, otherwise.
- [x] `test_v4_id_rejected` (integration, community/apache): on a scratch hypertable keyed by a UUID
      (`create_hypertable` by hand), inserting a UUIDv4 raises SQLSTATE `0A000` and inserting a v7
      id succeeds — the R8 failure mode, measured.
- [x] **Review-2 F7 (reopened 2026-11-03).** After the `develop` merge the `postgres` job runs three
      modules, and its prose said otherwise. The `tests.yml` comment now says the TimescaleDB entries
      run the upgrade and writeback tests too, and names what the plain entries run from
      `tests/test_timescale.py`: its unit tests and the three integration tests marked only
      `needs_server` (or `needs_server` + `without_timescale`): `test_server_matches_entry`,
      `test_absent_fails_before_schema` and `test_invalid_setting_exits`. The
      `server-db-test-suite` seed says "three modules" and describes `test_writeback_postgres.py`
      (the result-to-dispatch match at the data layer); what stays SQLite-only is the writeback
      end to end through server and client. Its `ROADMAP.md` entry says the same. Verify gate
      tightened (`--verify`) to grep the comment and the seed.
- **Verify:** see frontmatter (local: workflow parses, pins present, module skips cleanly, SQLite
  suite green). **CI gate (run B, informal here):** all five `postgres` entries green on the draft
  PR, including the unchanged upgrade tests on the TimescaleDB images.
- **Touches:** `.github/workflows/tests.yml`, `tests/test_timescale.py`.

## Phase P2 — Environment interrogation (`prepare`)
**Satisfies:** R5, R7 · **Depends on:** P1
**Goal:** under a timescale alias, `initdb`/`truncatedb` refuse an unusable server with guidance and
`runtime_error` *before* creating anything; a usable one passes through unchanged.

- [x] `src/hypershell/data/timescale.py` (new; SPDX header, `__all__`, `Logger.with_name`):
      `TimescaleError(SchemaUpgradeError)`; `MIN_VERSION = (2, 22, 0)`, `POLICY_VERSION = (2, 25,
      0)`; `parse_version`; `gates(version, edition)`; message builders; `prepare(engine)` per PLAN
      §2.2 (probe ladder; `CREATE EXTENSION IF NOT EXISTS` on a dedicated AUTOCOMMIT connection,
      invalidated on error; SQLSTATE `42501` → privilege message; dropped connection or `must be
      preloaded` → preload message; `23505`/`42710` → re-probe).
- [x] `src/hypershell/data/__init__.py`: `TIMESCALE` constant; `prepare(engine)` first in `initdb()`
      and first in `truncatedb()`. No other function changes.
- [x] Unit tests: `parse_version` (`2.31.0`, `2.22.0-dev`, garbage → error); `gates` table (2.21.9
      fail; 2.22.0/2.24.9 no policy; 2.25.0 full; apache → hypertable only); each message names the
      problem and the fix; `TimescaleError` is a `SchemaUpgradeError`; the preload/privilege error
      mapping with a stub driver error.
- [x] Integration: on plain entries, `hs initdb --yes` under `provider = timescale` exits
      `runtime_error`, stderr names TimescaleDB and the fix, contains no `Traceback`, and the database
      has **no** tables afterwards. On community: a database from `template0` gets the extension
      created and `initdb` succeeds (tables plain until P3). *(Written as
      `test_absent_fails_before_schema` and `test_extension_created`; proven only in CI, at P5.)*
- [x] **Review-1 F3 (reopened 2026-11-02).** `prepare` now decides everything before it creates
      anything: absent → too old (the offered `pg_available_extensions.default_version`, or the
      created version) → not preloaded → `CREATE EXTENSION`. A server offering only a release below
      2.22.0 is refused with nothing created (`offered_too_old_message`). Preload is read from
      `current_setting('timescaledb.max_background_workers', true)`: the loader, run only from
      `shared_preload_libraries`, defines it in the postmaster (`src/loader/loader.c` `_PG_init` →
      `ts_bgw_counter_setup_gucs`, `src/loader/bgw_counter.c`, 2.22.0), and
      `shared_preload_libraries` itself needs elevated privileges to read. This also covers the
      created-but-not-preloaded sub-case, refused before the schema step. Unit tests on a fake
      engine: `test_prepare_refuses_old_offer_before_creating`,
      `test_prepare_refuses_without_preload_before_creating`, `test_prepare_refuses_created_without_preload`
      (all red at `6cbf341`), and `test_prepare_creates_offered_extension` (control).
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/data/timescale.py`, `src/hypershell/data/__init__.py`,
  `tests/test_timescale.py`.

## Phase P3 — The idempotent pass (`converge`)
**Satisfies:** R2, R3, R4, R5, R7 · **Depends on:** P2
**Goal:** `hs initdb` under a timescale alias converges `task` to hypertable + columnstore + policy,
idempotently, honestly at every tier; the placeholder warning is gone.

- [x] `timescale.py`: `Settings` + `DEFAULTS`; `converge(engine, schema, settings) -> list[str]`
      per PLAN §2.3 — one transaction under `upgrade._waiting` / `_set_lock_timeout` /
      `_lock_postgres`; schema-qualified `<task>`; probes and statements per research 01
      §Recommendation, **never `migrate_data`**, `create_default_indexes => false`; interval
      comparisons done in SQL; 0A000 `is not empty` → non-empty warning; unexpected `DBAPIError` →
      rollback + `TimescaleError`.
- [x] `data/__init__.py`: `converge(engine, schema, DEFAULTS)` after `upgradedb()` in `initdb()` and
      after `create_all` in `truncatedb()`.
- [x] `data/core.py`: delete the placeholder `log.warning`; keep the `uuid_utils` guard; add
      `TIMESCALE_ALIASES` to `__all__`.
- [x] Integration (community): fresh; re-run ×2 (no INFO lines from `hypershell.data.timescale`, one
      job); `--truncate`; tasks run via `hs cluster`, one chunk compressed by hand
      (`compress_chunk`), then `hs list` and `hs initdb --vacuum --yes`; empty plain table from a
      `postgres`-provider `initdb` → converted; non-empty plain table and the `hs283` template →
      warning, `success`, still plain, rows intact; drift (chunk interval changed by hand; policy
      re-added with another `after`) → restored with INFO; named schema; two concurrent `hs initdb`
      → one job. Apache: hypertable, `compression_enabled = false`, no job, edition warning, re-run
      quiet. Floor: columnstore on, no job, warning naming 2.25.0. *(Written as the
      `test_fresh_initdb` … `test_concurrent_initdb` scenarios, each asserting the tier the server
      reports; proven only in CI, at P5. Re-runs allow the tier WARNING each time and assert no INFO.)*
- [x] Unit: column normalization (`'id  desc'` = `'id DESC'`), decision helpers that do not need SQL.
- **Verify:** see frontmatter. **CI gate:** P5.
- **Touches:** `src/hypershell/data/timescale.py`, `src/hypershell/data/__init__.py`,
  `src/hypershell/data/core.py`, `tests/test_timescale.py`.

## Phase P4 — Docs
**Satisfies:** R8, R10 · **Depends on:** P2 · **Parallel:** yes (docs only; written from PLAN)
**Goal:** a TimescaleDB section of equal standing with SQLite and PostgreSQL.

- [x] `docs/_include/database.rst`: new `TimescaleDB` section (house style: `-------------------`
      rule, `---` underline, first-person plural, admonition code blocks): provider selection and
      install (`hypershell[postgres,uuid7]`); what `hs initdb` does (hypertable on `id`, 1-week
      chunks, columnstore `client_id`/`id DESC`, policy after 4 weeks, idempotent, drift applied or
      reported; `client`/`source` stay plain); the UUIDv7-as-partition-key story (identity + time +
      chunk routing; chunk exclusion on id predicates); support matrix — floor 2.22.0, full story
      2.25.0 (policy fix), validated on 2.31.0, PostgreSQL 16–18 for current releases; Community vs
      Apache edition behavior; self-hosted images and Tiger Cloud vs managed offerings without the
      extension (spot-check the editions page first; state the date); R8 — every id-minting process
      (anything running `submit`) needs the `uuid7` extra, a v4 id is rejected per row with SQLSTATE
      `0A000` and aborts the whole INSERT bundle; query ergonomics — `to_uuidv7_boundary()` for time
      filters, `uuid_timestamp()` for display, warning box that `WHERE uuid_timestamp(id) > …`
      defeats chunk exclusion; background workers (`timescaledb.max_background_workers`, default 16,
      one scheduler per database plus one per running job; exhausted → policies silently never run);
      *Converting an existing table* (manual `create_hypertable(… migrate_data => true)` with its
      caveats: long lock, one transaction, aborts on any v4 id; then rerun `hs initdb`); naming
      (TimescaleDB the extension; TigerData the company, rebranded 2025; Tiger Cloud the managed
      product). Teach the columnstore vocabulary only. Retention, caggs and tiered storage only as
      pointers/watch notes (non-goals). *(Build note: the editions page was not re-fetched — this
      lane's network allowance covered only the package index and the image digest lookup — so the
      managed-offering sentence stays generic and is dated "as of August 2026", the seed research.
      No labels inside `_include/database.rst`: the include directory is also built as standalone
      documents, so a label there is a duplicate-label warning; cross-references point at
      `:ref:`database <database>`` and name the TimescaleDB section.)*
- [x] `docs/_include/database_alt.rst`: two or three sentences (provider, what `initdb` does, link
      target name) — it feeds the man pages at release.
- [x] `docs/install.rst`: the `uuid7` bullet says TimescaleDB requires it on every host that submits.
- [x] `docs/_include/config_param_ref.rst`: `.provider` lists `timescale` (alias `timescaledb`).
- [x] **Review-2 F6, F8 (reopened 2026-11-03).** F6: *Beyond the defaults* no longer says retention,
      continuous aggregates and tiered storage "work on the `task` hypertable like any other". It
      names the release each needs on a UUIDv7-partitioned hypertable, above the 2.22.0 floor:
      `show_chunks`/`drop_chunks` with time arguments 2.23.0 (#8746), continuous aggregates 2.24.0
      (#8939), retention policies 2.25.0 (#9102), from upstream `CHANGELOG.md` at 2.31.0 (read
      2026-11-03). Tiered storage is dropped (a managed-offering feature; GOAL non-goal). F8: the
      three new passages now say what the code does. Verified by a probe under the `timescale`
      provider that records which of `initdb`/`checkdb` each start path calls (dead port, nothing
      connected): `hs initdb` → `initdb`; `hs initdb --truncate` → `checkdb` (where the probe stops; the code
      then calls `truncatedb`, which runs the pass); `hs submit --initdb` and `hs cluster --initdb` → `initdb`; `hs server
      --initdb` (file or `--forever`), `hs server`, `hs submit`, `hs cluster`, `hs task submit` →
      `checkdb`. So the docs name `hs initdb` and `hs submit`/`hs cluster` with `--initdb`, and say
      `hs server` does not initialize even with `--initdb`. `server.py` is unchanged; the defect
      is seeded as `issues/server-initdb-ignored.md` (`unshaped`, `kind: fix`) with a Part I
      `ROADMAP.md` entry. The older lines it also makes untrue (`database.rst:94`, `:106-107`,
      `database_alt.rst:9-10`, `server_help.rst`) are pre-existing and listed in the seed, not
      changed here. Also for F5: the settings validation note names the cross-list refusal. Verify
      gate tightened (`--verify`) to grep the #9102 link, the absence of "tiered", and the ROADMAP
      seed link.
- **Verify:** see frontmatter (Sphinx without `sphinx_sitemap`, sandbox-safe; only the pre-existing
  toctree warnings allowed). **Amended at build (attempts 2):** the planned filter matched Sphinx's
  own `build succeeded, 2 warnings.` summary and the pre-existing `pkg_resources is deprecated`
  UserWarning from `sphinxcontrib.details`, so it was red on every tree; both lines are now excluded.
  Any real Sphinx warning still turns it red (a mid-build duplicate-label warning did).
- **Touches:** `docs/_include/database.rst`, `docs/_include/database_alt.rst`, `docs/install.rst`,
  `docs/_include/config_param_ref.rst`.

## Phase P5 — CI evidence (run B)
**Satisfies:** R2, R3, R4, R5, R7, R8, R9 · **Depends on:** P3, P4
**Goal:** the draft PR's `Tests` workflow is green on the last code commit, with every TimescaleDB
entry having run its scenarios (the module fails rather than skips when `HS_TEST_TIMESCALE` is set).

- [x] Foreman pushes and opens/updates the draft PR. Record run id, head sha and per-entry results
      (`pg13`, `pg18`, `timescale`, `timescale-oss`, `timescale-floor`, SQLite matrix) here.
- [x] If a scenario fails: reopen P3 (or P2) per `hs-build` remediation; do not patch tests to pass.
      *(No scenario failed. The one block was the template fixture below, fixed at `8f367d0`.)*
- *(Attempt 1, run 41546097979 at `9c853d0`, red; the first block at P5 on this cause. `pg13` and
  `pg18` passed. All three TimescaleDB entries failed before any test ran, in the template-building
  step: `CREATE DATABASE hs290a1 TEMPLATE hs283` → `source database "hs283" is being accessed by
  other users` (1 other session), about 5 s after the last client exited, which is PostgreSQL's
  wait for other backends to leave the source. Cause, from TimescaleDB source (2.24.0 and 2.31.0
  read 2026-11-02): the image creates the extension in `template1`, so every new database has it;
  the launcher keeps a scheduler connected to every database that allows connections and is not a
  template (`src/loader/bgw_launcher.c`, `populate_database_htab`); and the loader's CREATE
  DATABASE hook sends RESTART to the scheduler of a TEMPLATE source that allows connections
  (`src/loader/loader.c`, `post_analyze_hook`, `database_allowconn`). The restarted scheduler
  connects first and only then waits on the CREATE DATABASE transaction's vxid
  (`ts_bgw_db_scheduler_entrypoint`), so the copy fails every time, not by chance. The same would
  hit every test clone of `hs283`/`hs290a1`. Fixture fix, no product change:
  `make-legacy-postgres.sh` `seal_template` sets `ALLOW_CONNECTIONS false` on each template once
  built and terminates its sessions, waiting until none remain. The hook then sends no RESTART and
  the launcher starts no scheduler there, so the fixture's clone and the tests' clones see no
  other session. The function returns at once unless `shared_preload_libraries` names
  `timescaledb`, so the plain entries run the same SQL as before. Test clones from `template1`
  and `template0` were never exposed: both are templates, and neither is named as a TEMPLATE source
  that allows connections.)*
- **Evidence (run B, read by the foreman from GitHub Actions; `hs-build` has no `gh` and did not
  re-run the gate):** workflow `Tests` (`tests.yml`), run `41548945416`, on draft PR #71 at head
  `8f367d0`, concluded `success` with all 14 checks green. `8f367d0` is the last commit outside
  `spec/` (the gate's target) and contains every code commit, P6 `9c853d0` and the P5 fixture fix
  included; the commit recording this touches only `spec/`.
  - `PostgreSQL (timescale-floor)`: 98 passed, 1 skipped. `PostgreSQL (timescale-oss)`: 98
    passed, 1 skipped. The skip in both is `test_absent_fails_before_schema`, which needs a server
    without TimescaleDB.
  - `PostgreSQL (timescale)`: passed.
  - `PostgreSQL (pg18)`: 84 passed, 15 skipped (the TimescaleDB-only scenarios skip without
    `HS_TEST_TIMESCALE`). `PostgreSQL (pg13)`: passed.
  - `tests (3.11)` through `tests (3.14)`, the zero-build install, and package metadata passed.
- [x] **Review-1 remediation (reopened 2026-11-02).** The foreman pushes the remediation head and
      records the new run: all five `postgres` entries and the SQLite matrix green, with
      `test_configured_columns_converge` passing (not skipped) in `timescale`, `timescale-oss` and
      `timescale-floor`, and `test_extension_created` still passing (it now crosses the preload
      probe on a real server). F4's seed and ROADMAP prose was corrected in the same commit.
  - **Evidence (recorded by the foreman, 2026-11-03):** the push of `ae38532` produced no
    `pull_request` run because PR #71 had become conflicting with `develop` once writeback landed
    (`6f216d0` edited the same workflow line). Merge `a9ab876` brought `develop` in and resolved
    that one conflict: the postgres job now runs `test_upgrade_postgres.py`,
    `test_writeback_postgres.py` and `test_timescale.py`. Run `41581150777` at `a9ab876`
    concluded `success` with all 14 checks green. `timescale`, `timescale-oss` and
    `timescale-floor`: 130 passed, 1 skipped each, with `test_extension_created` and
    `test_configured_columns_converge` PASSED. `pg13` and `pg18`: 115 passed, 16 skipped (the
    TimescaleDB scenarios skip without `HS_TEST_TIMESCALE`). (`ab7adb4` is an empty commit that
    tried to re-trigger CI before the conflict was found.)
- [x] **Review-2 remediation (reopened 2026-11-03).** F5 changes `Settings.from_config`, which every
      configured integration scenario crosses, and adds a `test_invalid_setting_exits` case. The
      foreman pushes the remediation head and records the new run: all five `postgres` entries and
      the SQLite matrix green, with both `test_invalid_setting_exits` cases passing in every entry
      and `test_configured_columns_converge` passing (not skipped) in `timescale`, `timescale-oss`
      and `timescale-floor`.
  - **Evidence (recorded by the foreman, 2026-11-03):** run `41587822550` at `cd3f96f` concluded
    `success`, 14/14 checks green. `timescale`, `timescale-oss`, `timescale-floor`: 137 passed,
    1 skipped each, with both `test_invalid_setting_exits` cases and `test_configured_columns_converge`
    PASSED. `pg13` and `pg18`: 122 passed, 16 skipped, with both `test_invalid_setting_exits` cases
    PASSED and the TimescaleDB-only scenarios skipped.
- **Verify:** see frontmatter (gates on the last commit outside `spec/`, not `HEAD`).

## Phase P6 — R6: `[database.timescale]` (deferrable — cut here if over appetite)
**Satisfies:** R6, R4, R10 · **Depends on:** P5
**Goal:** an operator tunes the pass from configuration; invalid values fail at startup.

- [x] `data/core.py`: `timescale_config = config.pop('timescale', {})` beside `pragmas` (always, so
      it never reaches the URL); export it.
- [x] `timescale.py`: `Settings.from_config(ns)` per PLAN §2.6 (keys, interval regex, `Task` column
      checks, `policy` without `columnstore` refused); `data/__init__.py` builds the settings at
      import under a timescale alias, `display_critical` + `sys.exit(exit_status.bad_config)` on
      error, and passes them to `converge`.
- [x] `converge`: `columnstore = false` with columnstore enabled → manual-action warning;
      `policy = false` → remove an existing policy (INFO).
- [x] Docs: `database.rst` reference table for the six keys with defaults and the future-chunks
      caveat; `config_param_ref.rst` `[timescale]` entry.
- [x] Tests: unit validation table; integration — configured `chunk_interval = "1 day"` and
      `compress_after = "2 weeks"` applied on fresh and as drift; `policy = false` removes the job;
      an invalid value exits `bad_config` with the key named and no traceback.
- *(Build notes: `timescale_config` is popped as `None` when absent, so zero configuration is
  `DEFAULTS`; the table is ignored under other providers. Booleans also accept the strings
  `"true"`/`"false"` (environment values arrive as text); `segmentby = ""` means none. The
  integration scenarios are proven only in CI, at P7. Local CLI drive: an invalid
  `chunk_interval` exits `bad_config` (3) with one CRITICAL line naming the key; a valid table
  reaches the connection, so it is not forwarded to psycopg.)*
- [x] **Review-1 F1, F2 (reopened 2026-11-02).** `segmentby`/`orderby` are validated as lists of
      `task` column names, bare or double-quoted, each named once (`SEGMENTBY_PATTERN`,
      `ORDERBY_PATTERN`, duplicate refused with "each column once"); the canonical setting keeps bare
      names. The pass emits every column through the dialect's identifier quoting (`quote_columns`,
      so `group` goes out as `"group"`), and compares the view with `effective_orderby`: TimescaleDB's
      own rule, `add_time_to_order_by_if_not_included` in `tsl/src/compression/create.c` (2.22.0
      through 2.31.0), appends `id DESC` (nulls first) when neither list names `id`.
      `normalize_columns` drops identifier quotes, since the view renders orderby with `%I`
      (`sql/views.sql`). Unit tests drive the real `_Pass.run()` on `FakeColumnstore`, which models
      the raw-SQL option parse, the duplicate refusal, the appended partition column and the view:
      `test_orderby_without_id_converges`, `test_group_column_emitted_quoted`,
      `test_settings_duplicate_column` (red at `6cbf341`); `test_settings_unknown_column` (green at
      `6cbf341` too: unknown names were already refused; kept as a guard). The `group` row of
      `test_settings_from_config` moved to `test_group_column_emitted_quoted`, which asserts the
      quoted emission. New CI scenario `test_configured_columns_converge`. Docs: the settings
      table and its validation note. CLI drive: `segmentby = "client_id, client_id"` exits
      `bad_config` (3) with one CRITICAL line before connecting; `client_id, group` /
      `group DESC` passes validation and reaches the connection.
- [x] **Review-2 F5 (reopened 2026-11-03).** A column may not be named in both `segmentby` and
      `orderby`: TimescaleDB refuses it when the settings are applied ("cannot use column … for both
      ordering and segmenting", `ts_compression_settings_update`, `src/ts_catalog/compression_settings.c`,
      2.22.0 through 2.31.0). `_check_disjoint` runs at the end of `Settings.from_config` on the lists
      the pass applies: segmentby as configured or by default, and `effective_orderby` (F1's append
      rule, which only appends `id` when neither list names it, so it never adds an overlap). It
      raises `ConfigurationError` naming the column and both values, so every command stops at
      startup with `bad_config` (3), like any other invalid setting. Red-first at `be8b7be`:
      `test_settings_column_in_both_lists` (4 cases: `orderby = "client_id DESC"` with the default
      segmentby, `segmentby = "id"` with the default orderby, a quoted name across lists, `group`)
      failed "DID NOT RAISE"; green after. `test_settings_duplicate_column` (same-list guard) and the
      new `test_settings_lists_disjoint_by_default` stay green throughout. Amended:
      `test_group_column_emitted_quoted` named `group` in both lists, a config TimescaleDB refuses;
      it is now parametrized with `group` in segmentby and in orderby separately, and
      `FakeColumnstore` models the upstream cross-list refusal. `test_invalid_setting_exits` gains
      the `orderby = "client_id DESC"` case (CI: every `postgres` entry). CLI drive (dead port): at
      `be8b7be` both natural configs passed validation and reached the connection (rc 5); now each
      exits 3 with one CRITICAL line naming the column, and `orderby = "id DESC, id"` still exits 3
      with "each column once". This supersedes the review-1 note above that `client_id, group` /
      `group DESC` passes validation.
- **If cut:** add a dated Clarification to `GOAL.md`, move R6 to Non-goals, return it to
  `issues/timescale.md`, mark P6/P7 `blocked` with the reason, and drop the config-reference bullet
  from R10's evidence (GOAL Clarifications, 2026-11-01).
- **Touches:** `src/hypershell/data/core.py`, `src/hypershell/data/timescale.py`,
  `src/hypershell/data/__init__.py`, `tests/test_timescale.py`, `docs/_include/database.rst`,
  `docs/_include/config_param_ref.rst`.

## Phase P7 — CI evidence for R6 (run B)
**Satisfies:** R6, R9 · **Depends on:** P6
- [x] As P5, for the R6 commit.
- **Evidence:** the same run as P5. Run `41548945416` at `8f367d0`, which contains P6 `9c853d0`,
  concluded `success` with all 14 checks green. The R6 integration scenarios ran in the three
  TimescaleDB entries: `timescale-floor` and `timescale-oss` each 98 passed, 1 skipped
  (`test_absent_fails_before_schema`), and `timescale` passed. They skip in `pg18` (84 passed,
  15 skipped) without `HS_TEST_TIMESCALE`. Per-entry detail is under P5.
- [x] **Review-1 remediation:** the same new run as P5 covers the F1/F2 changes to the R6 code
  (run `41581150777` at `a9ab876`; see P5).
- [x] **Review-2 remediation (reopened 2026-11-03):** the same new run as P5 covers the F5 change
  to the R6 validation (run `41587822550` at `cd3f96f`; see P5).
- **Verify:** see frontmatter.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable (`origin/develop`, never the stale local).
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
