---
slug: timescale
title: "TimescaleDB provider: hypertables out of the box (TS1)"
kind: feature
appetite: big
status: in_progress
branch: feature/timescale
base: develop
current_phase: P1
last_updated: "2026-11-02"
phases:
  - id: P1
    name: "CI lane: pinned TimescaleDB entries (Community, Apache, floor) and the scenario-module scaffold"
    status: pending
    satisfies: [R9, R8]
    depends_on: []
    parallel: false
    hammerable: false
    hill: uphill
    verify: >-
      grep -q 'timescale/timescaledb:2.31.0-pg18@sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579' .github/workflows/tests.yml
      && grep -q 'timescale/timescaledb:2.31.0-pg18-oss@sha256:cc00b0ede968b0c6e55212bd55a9a195726afe5a9e19dcf8af49a13334e92ff3' .github/workflows/tests.yml
      && grep -q 'tests/test_timescale.py' .github/workflows/tests.yml
      && uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/tests.yml'))"
      && uv run pytest -rs tests/test_timescale.py
      && uv run pytest
  - id: P2
    name: "Environment interrogation before the schema step: extension, privilege, preload, version floor"
    status: pending
    satisfies: [R5, R7]
    depends_on: [P1]
    parallel: false
    hammerable: false
    hill: uphill
    verify: >-
      grep -q 'def prepare' src/hypershell/data/timescale.py
      && grep -q 'class TimescaleError(SchemaUpgradeError)' src/hypershell/data/timescale.py
      && uv run pytest -rs -m unit tests/test_timescale.py
      && uv run pytest
  - id: P3
    name: "The idempotent pass: hypertable, columnstore, policy, drift, Apache and non-empty tiers"
    status: pending
    satisfies: [R2, R3, R4, R5, R7]
    depends_on: [P2]
    parallel: false
    hammerable: false
    hill: uphill
    verify: >-
      ! grep -q 'not yet implemented' src/hypershell/data/core.py
      && grep -q 'def converge' src/hypershell/data/timescale.py
      && grep -q 'add_columnstore_policy' src/hypershell/data/timescale.py
      && uv run pytest -rs tests/test_timescale.py
      && uv run pytest
  - id: P4
    name: "Docs: TimescaleDB as a first-class database section"
    status: pending
    satisfies: [R8, R10]
    depends_on: [P2]
    parallel: true
    hammerable: false
    hill: uphill
    verify: >-
      grep -q 'to_uuidv7_boundary' docs/_include/database.rst
      && grep -q 'Tiger Cloud' docs/_include/database.rst
      && grep -q '0A000' docs/_include/database.rst
      && grep -q 'max_background_workers' docs/_include/database.rst
      && grep -q -i 'timescale' docs/_include/database_alt.rst
      && uv run sphinx-build -E -b html -D extensions=sphinx.ext.autodoc,sphinx.ext.napoleon,sphinx.ext.intersphinx,sphinx.ext.extlinks,sphinx.ext.viewcode,sphinxext.opengraph,sphinx_inline_tabs,sphinx_copybutton,sphinxcontrib.details.directive,sphinx_reredirects docs docs/_build 2>&1
      | grep -i 'warning' | grep -v -e 'task_submit.rst' -e 'manual.rst' | (! grep .)
  - id: P5
    name: "CI evidence for the load-bearing slice (run B: external wait on the draft PR)"
    status: pending
    satisfies: [R2, R3, R4, R5, R7, R8, R9]
    depends_on: [P3, P4]
    parallel: false
    hammerable: false
    hill: uphill
    verify: >-
      test "$(gh run list --branch feature/timescale --workflow tests.yml
      --commit "$(git log -1 --format=%H -- . ':!spec')" --json conclusion
      --jq '[.[] | select(.conclusion == "success")] | length')" -ge 1
  - id: P6
    name: "R6 (deferrable): the [database.timescale] override table, validation and drift"
    status: pending
    satisfies: [R6, R4, R10]
    depends_on: [P5]
    parallel: false
    hammerable: true
    hill: uphill
    verify: >-
      grep -q "pop('timescale'" src/hypershell/data/core.py
      && grep -q 'compress_after' docs/_include/database.rst
      && grep -q 'timescale' docs/_include/config_param_ref.rst
      && uv run pytest -rs tests/test_timescale.py
      && uv run pytest
  - id: P7
    name: "CI evidence for R6 (run B)"
    status: pending
    satisfies: [R6, R9]
    depends_on: [P6]
    parallel: false
    hammerable: true
    hill: uphill
    verify: >-
      test "$(gh run list --branch feature/timescale --workflow tests.yml
      --commit "$(git log -1 --format=%H -- . ':!spec')" --json conclusion
      --jq '[.[] | select(.conclusion == "success")] | length')" -ge 1
review:
  last_reviewed_commit: ""
  verdict: none
  blocked_reason: ""
  cycle: 0
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
| `spec/timescale/*` | all | state |

Not touched: `data/model.py`, `data/upgrade.py`, `core/*`, `server.py`, `client.py`, `submit.py`,
`cluster/*`, `tests/conftest.py`, `tests/test_upgrade_postgres.py`, `share/`, `pyproject.toml`,
`uv.lock`.

---

## Phase P1 — CI lane and scenario scaffold
**Satisfies:** R9, R8 · **Depends on:** —
**Goal:** the TimescaleDB servers exist in CI and the existing upgrade tests pass on them before any
feature code lands, so every later push is exercised against a real TimescaleDB.

- [ ] `.github/workflows/tests.yml`, `postgres` job: replace the placeholder with three `include`
      entries (PLAN §2.7) carrying `timescale: community|apache` (empty on `pg13`/`pg18`). Pin
      `2.31.0-pg18@sha256:0fcff576…d579` and `2.31.0-pg18-oss@sha256:cc00b0ed…2ff3` in full.
      Resolve the floor entry: the highest released `2.24.x-pg17` tag and its index digest from
      `https://hub.docker.com/v2/repositories/timescale/timescaledb/tags/<tag>` (`digest` field);
      record tag, digest and the date read in a comment above the entry.
- [ ] Service `env`: add `TIMESCALEDB_TELEMETRY: 'off'`. Job `env`: `HS_TEST_TIMESCALE: ${{
      matrix.timescale }}`. Run step: `pytest -v -rs tests/test_upgrade_postgres.py
      tests/test_timescale.py`. Update the job's header comment to say what the TimescaleDB entries
      run.
- [ ] `tests/test_timescale.py` scaffold: module docstring (env contract, as in
      `test_upgrade_postgres.py`); `ADMIN_URL`, `TIMESCALE = os.getenv('HS_TEST_TIMESCALE', '')`;
      fixtures `admin`, `ts_database(template=None)` (throwaway, `DROP … WITH (FORCE)`), `ts_site(name,
      schema=None, provider='timescale')` setting `HYPERSHELL_DATABASE_*` (pattern of
      `test_upgrade_postgres.pg_site`; do not edit that module); markers `integration` / `unit`.
- [ ] `test_server_matches_entry` (integration): with `TIMESCALE` set, `extversion` exists and
      `timescaledb.license` equals `timescale` (community) or `apache` — fail, not skip, otherwise.
- [ ] `test_v4_id_rejected` (integration, community/apache): on a scratch hypertable keyed by a UUID
      (`create_hypertable` by hand), inserting a UUIDv4 raises SQLSTATE `0A000` and inserting a v7
      id succeeds — the R8 failure mode, measured.
- **Verify:** see frontmatter (local: workflow parses, pins present, module skips cleanly, SQLite
  suite green). **CI gate (run B, informal here):** all five `postgres` entries green on the draft
  PR, including the unchanged upgrade tests on the TimescaleDB images.
- **Touches:** `.github/workflows/tests.yml`, `tests/test_timescale.py`.

## Phase P2 — Environment interrogation (`prepare`)
**Satisfies:** R5, R7 · **Depends on:** P1
**Goal:** under a timescale alias, `initdb`/`truncatedb` refuse an unusable server with guidance and
`runtime_error` *before* creating anything; a usable one passes through unchanged.

- [ ] `src/hypershell/data/timescale.py` (new; SPDX header, `__all__`, `Logger.with_name`):
      `TimescaleError(SchemaUpgradeError)`; `MIN_VERSION = (2, 22, 0)`, `POLICY_VERSION = (2, 25,
      0)`; `parse_version`; `gates(version, edition)`; message builders; `prepare(engine)` per PLAN
      §2.2 (probe ladder; `CREATE EXTENSION IF NOT EXISTS` on a dedicated AUTOCOMMIT connection,
      invalidated on error; SQLSTATE `42501` → privilege message; dropped connection or `must be
      preloaded` → preload message; `23505`/`42710` → re-probe).
- [ ] `src/hypershell/data/__init__.py`: `TIMESCALE` constant; `prepare(engine)` first in `initdb()`
      and first in `truncatedb()`. No other function changes.
- [ ] Unit tests: `parse_version` (`2.31.0`, `2.22.0-dev`, garbage → error); `gates` table (2.21.9
      fail; 2.22.0/2.24.9 no policy; 2.25.0 full; apache → hypertable only); each message names the
      problem and the fix; `TimescaleError` is a `SchemaUpgradeError`; the preload/privilege error
      mapping with a stub driver error.
- [ ] Integration: on plain entries, `hs initdb --yes` under `provider = timescale` exits
      `runtime_error`, stderr names TimescaleDB and the fix, contains no `Traceback`, and the database
      has **no** tables afterwards. On community: a database from `template0` gets the extension
      created and `initdb` succeeds (tables plain until P3).
- **Verify:** see frontmatter.
- **Touches:** `src/hypershell/data/timescale.py`, `src/hypershell/data/__init__.py`,
  `tests/test_timescale.py`.

## Phase P3 — The idempotent pass (`converge`)
**Satisfies:** R2, R3, R4, R5, R7 · **Depends on:** P2
**Goal:** `hs initdb` under a timescale alias converges `task` to hypertable + columnstore + policy,
idempotently, honestly at every tier; the placeholder warning is gone.

- [ ] `timescale.py`: `Settings` + `DEFAULTS`; `converge(engine, schema, settings) -> list[str]`
      per PLAN §2.3 — one transaction under `upgrade._waiting` / `_set_lock_timeout` /
      `_lock_postgres`; schema-qualified `<task>`; probes and statements per research 01
      §Recommendation, **never `migrate_data`**, `create_default_indexes => false`; interval
      comparisons done in SQL; 0A000 `is not empty` → non-empty warning; unexpected `DBAPIError` →
      rollback + `TimescaleError`.
- [ ] `data/__init__.py`: `converge(engine, schema, DEFAULTS)` after `upgradedb()` in `initdb()` and
      after `create_all` in `truncatedb()`.
- [ ] `data/core.py`: delete the placeholder `log.warning`; keep the `uuid_utils` guard; add
      `TIMESCALE_ALIASES` to `__all__`.
- [ ] Integration (community): fresh; re-run ×2 (no INFO lines from `hypershell.data.timescale`, one
      job); `--truncate`; tasks run via `hs cluster`, one chunk compressed by hand
      (`compress_chunk`), then `hs list` and `hs initdb --vacuum --yes`; empty plain table from a
      `postgres`-provider `initdb` → converted; non-empty plain table and the `hs283` template →
      warning, `success`, still plain, rows intact; drift (chunk interval changed by hand; policy
      re-added with another `after`) → restored with INFO; named schema; two concurrent `hs initdb`
      → one job. Apache: hypertable, `compression_enabled = false`, no job, edition warning, re-run
      quiet. Floor: columnstore on, no job, warning naming 2.25.0.
- [ ] Unit: column normalization (`'id  desc'` = `'id DESC'`), decision helpers that do not need SQL.
- **Verify:** see frontmatter. **CI gate:** P5.
- **Touches:** `src/hypershell/data/timescale.py`, `src/hypershell/data/__init__.py`,
  `src/hypershell/data/core.py`, `tests/test_timescale.py`.

## Phase P4 — Docs
**Satisfies:** R8, R10 · **Depends on:** P2 · **Parallel:** yes (docs only; written from PLAN)
**Goal:** a TimescaleDB section of equal standing with SQLite and PostgreSQL.

- [ ] `docs/_include/database.rst`: new `TimescaleDB` section (house style: `-------------------`
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
      pointers/watch notes (non-goals).
- [ ] `docs/_include/database_alt.rst`: two or three sentences (provider, what `initdb` does, link
      target name) — it feeds the man pages at release.
- [ ] `docs/install.rst`: the `uuid7` bullet says TimescaleDB requires it on every host that submits.
- [ ] `docs/_include/config_param_ref.rst`: `.provider` lists `timescale` (alias `timescaledb`).
- **Verify:** see frontmatter (Sphinx without `sphinx_sitemap`, sandbox-safe; only the pre-existing
  toctree warnings allowed).
- **Touches:** `docs/_include/database.rst`, `docs/_include/database_alt.rst`, `docs/install.rst`,
  `docs/_include/config_param_ref.rst`.

## Phase P5 — CI evidence (run B)
**Satisfies:** R2, R3, R4, R5, R7, R8, R9 · **Depends on:** P3, P4
**Goal:** the draft PR's `Tests` workflow is green on the last code commit, with every TimescaleDB
entry having run its scenarios (the module fails rather than skips when `HS_TEST_TIMESCALE` is set).

- [ ] Foreman pushes and opens/updates the draft PR. Record run id, head sha and per-entry results
      (`pg13`, `pg18`, `timescale`, `timescale-oss`, `timescale-floor`, SQLite matrix) here.
- [ ] If a scenario fails: reopen P3 (or P2) per `hs-build` remediation; do not patch tests to pass.
- **Verify:** see frontmatter (gates on the last commit outside `spec/`, not `HEAD`).

## Phase P6 — R6: `[database.timescale]` (deferrable — cut here if over appetite)
**Satisfies:** R6, R4, R10 · **Depends on:** P5
**Goal:** an operator tunes the pass from configuration; invalid values fail at startup.

- [ ] `data/core.py`: `timescale_config = config.pop('timescale', {})` beside `pragmas` (always, so
      it never reaches the URL); export it.
- [ ] `timescale.py`: `Settings.from_config(ns)` per PLAN §2.6 (keys, interval regex, `Task` column
      checks, `policy` without `columnstore` refused); `data/__init__.py` builds the settings at
      import under a timescale alias, `display_critical` + `sys.exit(exit_status.bad_config)` on
      error, and passes them to `converge`.
- [ ] `converge`: `columnstore = false` with columnstore enabled → manual-action warning;
      `policy = false` → remove an existing policy (INFO).
- [ ] Docs: `database.rst` reference table for the six keys with defaults and the future-chunks
      caveat; `config_param_ref.rst` `[timescale]` entry.
- [ ] Tests: unit validation table; integration — configured `chunk_interval = "1 day"` and
      `compress_after = "2 weeks"` applied on fresh and as drift; `policy = false` removes the job;
      an invalid value exits `bad_config` with the key named and no traceback.
- **If cut:** add a dated Clarification to `GOAL.md`, move R6 to Non-goals, return it to
  `issues/timescale.md`, mark P6/P7 `blocked` with the reason, and drop the config-reference bullet
  from R10's evidence (GOAL Clarifications, 2026-11-01).
- **Touches:** `src/hypershell/data/core.py`, `src/hypershell/data/timescale.py`,
  `src/hypershell/data/__init__.py`, `tests/test_timescale.py`, `docs/_include/database.rst`,
  `docs/_include/config_param_ref.rst`.

## Phase P7 — CI evidence for R6 (run B)
**Satisfies:** R6, R9 · **Depends on:** P6
- [ ] As P5, for the R6 commit.
- **Verify:** see frontmatter.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase.
2. Pre-flight: clean tree, on `branch`, `base` reachable (`origin/develop`, never the stale local).
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file if reality diverges (note it in the commit body). STOP on a `GOAL.md` contradiction.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[feature]` commit; stop and report.
