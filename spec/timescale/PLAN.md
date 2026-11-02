# PLAN — TimescaleDB provider: hypertables out of the box (TS1)

> **Status:** Draft for review · **Last updated:** 2026-11-02
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/) (digest: [`00-digest.md`](research/00-digest.md)). Every design element
> traces to a GOAL R-ID.

## 1. Summary

One new module, `src/hypershell/data/timescale.py`, owns everything TimescaleDB-shaped. `initdb()`
and `truncatedb()` call it twice when the provider is a timescale alias: `prepare()` before the
schema step interrogates the server (extension, version, edition) and fails early with guidance, and
`converge()` after the schema step brings `task` to the target shape in one locked transaction:
hypertable on the UUIDv7 `id`, columnstore settings, and a columnstore policy, each probed first and
applied only where missing or drifted. Nothing changes for any other provider. CI gains Community
and Apache-only TimescaleDB entries on the existing `postgres` job, running the upgrade tests and a
new scenario module. Docs gain a first-class TimescaleDB section. The `[database.timescale]` override
table (R6) is the last, separable phase.

## 2. Design

### 2.1 Placement (R2)

`src/hypershell/data/__init__.py`:

```python
TIMESCALE: Final[bool] = DATABASE_PROVIDER in TIMESCALE_ALIASES   # imported from data/core.py

def initdb(optimize=False):
    if TIMESCALE: timescale.prepare(engine)
    upgradedb()
    if TIMESCALE: timescale.converge(engine, schema, timescale_settings)
    ...                                    # SQLite optimize unchanged

def truncatedb():
    if TIMESCALE: timescale.prepare(engine)   # before anything is dropped
    ...                                    # close_all_sessions/drop_all/create_all unchanged
    if TIMESCALE: timescale.converge(engine, schema, timescale_settings)
```

`checkdb()`, `ensuredb()`, `vacuumdb()` and `rotatedb()` are not edited. `checkdb()` stays as the GOAL
note asks: it runs on every PostgreSQL start, `has_table` is hypertable-transparent, and a fresh
timescale database always passes through `initdb` first. Every `--initdb` path (`hs initdb`,
`hs cluster|server|submit --initdb`) already reaches `initdb()`, so no app module changes. For every
other provider the guard is false and both functions run exactly the base-commit code (R2, last
sentence). `timescale_settings` is the module default `DEFAULTS` until R6 lands (§2.6).

`src/hypershell/data/core.py`: delete the placeholder `log.warning` (L232–233). The `uuid_utils`
import guard above it stays (R8). Export `TIMESCALE_ALIASES` in `__all__`.

### 2.2 `prepare(engine)` — environment interrogation before the schema step (R5, R7)

Read-only probes on a short connection, then at most one `CREATE EXTENSION`:

1. `SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'`. Found → step 4.
2. `SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'`. No row →
   **fail (absent)**: "TimescaleDB is not installed on this PostgreSQL server … install it, use a
   server image that ships it, or set `database.provider = "postgres"`."
3. `CREATE EXTENSION IF NOT EXISTS timescaledb` on a **dedicated AUTOCOMMIT connection** that is
   invalidated on any error (R5's MAY; `trusted = true` lets a database owner do it, no superuser):
   - SQLSTATE `42501` → **fail (privilege)**: "ask an administrator to run `CREATE EXTENSION
     timescaledb` in database `<name>`".
   - a dropped connection, or a message containing `must be preloaded` → **fail (preload)**: "add
     `timescaledb` to `shared_preload_libraries` and restart the server".
   - `23505`/`42710` (a concurrent creator won) → re-probe, continue.
   Then re-read `extversion`.
4. Parse `extversion` (`major.minor.patch`, tolerant of suffixes like `-dev`). `< 2.22.0` → **fail
   (version)**, naming the installed version, the 2.22.0 floor, and `ALTER EXTENSION timescaledb
   UPDATE`.

`prepare()` returns nothing; `converge()` re-probes version and edition inside its own transaction
(cheap, and it keeps the two calls independent for `truncatedb`). Every failure raises
`TimescaleError(SchemaUpgradeError)` — defined in `timescale.py` — so all apps print one critical
line and exit `runtime_error` with no traceback, and **no table has been created** because the schema
step has not run. When the extension already exists, nothing in `prepare()` needs any privilege
beyond `CONNECT` (R5, last paragraph).

### 2.3 `converge(engine, schema, settings)` — the idempotent pass (R2, R3, R4, R5, R7)

One transaction on one connection, serialized and lock-bounded exactly like the schema step: reuse
`upgrade._waiting`, `_set_lock_timeout`, `_lock_postgres` (same advisory key
`SCHEMA_UPGRADE_LOCK_KEY`, so a pass and a peer's upgrade never interleave) and `_waiting_for`.
`<task>` is the schema-qualified name rendered by the dialect's identifier preparer; information-view
probes filter on `hypertable_schema = <schema or current_schema()>`.

```
version, edition = extversion, current_setting('timescaledb.license', true)
is_ht            = row in timescaledb_information.hypertables for <task>
if not is_ht:
    if EXISTS (SELECT 1 FROM <task>):           -> WARNING non-empty (R5) ; return
    create_hypertable(<task>, by_range('id', INTERVAL :chunk), create_default_indexes => false,
                      if_not_exists => true)      -> INFO "Converted table task to a hypertable …"
    (SQLSTATE 0A000 "is not empty" from a racing insert -> same WARNING, roll back, return)
else:
    interval drift? (COALESCE(time_interval, integer_interval * '1 microsecond') vs :chunk)
        -> set_chunk_time_interval(<task>, INTERVAL :chunk)   INFO "… applies to new chunks only"
if edition == 'apache':                           -> WARNING edition (R5) ; commit ; return
if settings.columnstore:
    not enabled, or segmentby/orderby differ (normalized: whitespace and case)
        -> ALTER TABLE <task> SET (timescaledb.enable_columnstore = true,
                                   timescaledb.segmentby = :seg, timescaledb.orderby = :ord)
           INFO; when it was already enabled, say "applies to future compression only"
else if enabled:                                  -> WARNING manual action (no auto-disable)
if version < 2.25.0:                              -> WARNING policy refused, needs ≥ 2.25.0 (R7)
elif settings.policy:
    no policy_compression job                     -> CALL add_columnstore_policy(<task>,
                                                       after => INTERVAL :after, if_not_exists => true)
    job with compress_after ≠ :after              -> CALL remove_columnstore_policy(<task>, if_exists => true)
                                                     ; add again  (INFO with old and new values)
    job's last_run_status = 'Failed'              -> WARNING naming job_stats (honest status)
elif a policy job exists:                         -> remove it (INFO)          # R6 only
commit
```

- **Idempotency (R4).** Each statement is guarded by its probe, and the guarded statements are
  themselves idempotent (`if_not_exists`, `if_exists`). A converged database costs only the probes
  and logs nothing above `debug`. `truncatedb()`'s `drop_all` drops the hypertable and, by the
  catalog's `ON DELETE CASCADE`, its policy job; the pass then recreates both — one job, never two.
  Two concurrent `initdb`s serialize on the advisory lock; the second sees a converged table.
- **Drift (R4).** Applied where TimescaleDB permits (chunk interval, columnstore settings, policy
  `after`) with an INFO line naming old → new and the "future chunks only" caveat; logged as needing
  manual action where it does not (turning columnstore off with columnstore chunks present). Nothing
  is silently ignored. Without R6 the targets are the shipped defaults.
- **Non-empty plain table (R5).** Untouched. WARNING: "table task holds rows and is a plain table;
  HyperShell will not migrate it. See *Converting an existing table* in the database docs." `initdb`
  exits `success`, so a 2.8 database opened under a timescale alias keeps working. Ids minted by
  2.8 without the `uuid7` extra are v4 and would abort `migrate_data`; the docs say so.
- **Apache (R5).** Hypertable created; columnstore and policy skipped; one WARNING naming the
  `apache` edition and that compression needs the Community edition.
- **Log levels.** Changes at INFO (matching the schema step's convention for existing databases);
  tier and gate refusals at WARNING; probes at `debug`/`trace`.
- **Errors.** An unexpected `DBAPIError` rolls the transaction back and raises `TimescaleError` with
  the driver message and "the tables exist as plain PostgreSQL tables; fix the cause and rerun
  `hs initdb`". The schema step has already committed, which is safe: the next `initdb` converges.

### 2.4 Statements and probes (columnstore vocabulary; R3)

Exact SQL, gates and sources are in [`research/01`](research/01-timescale-sql-and-versions.md)
§Recommendation and the digest. Summary: `create_hypertable` generic form (never `migrate_data`);
`ALTER TABLE … SET (timescaledb.enable_columnstore, timescaledb.segmentby, timescaledb.orderby)`;
`CALL add_columnstore_policy` / `remove_columnstore_policy`; probes on
`timescaledb_information.{hypertables, dimensions, hypertable_columnstore_settings, jobs, job_stats}`
and `(config->>'compress_after')::interval`. Interval equality is decided by PostgreSQL
(`CAST(:a AS interval) = CAST(:b AS interval)`), not by string comparison.

### 2.5 Module shape (`src/hypershell/data/timescale.py`)

Pure, unit-testable pieces kept apart from SQL: `Settings` (frozen dataclass; `DEFAULTS =
Settings(chunk_interval='1 week', columnstore=True, segmentby='client_id', orderby='id DESC',
policy=True, compress_after='4 weeks')`), `parse_version(text) -> tuple`, `gates(version, edition)
-> Gates` (which steps are allowed and why), `normalize_columns(text)`, and the message builders.
`prepare(engine)` and `converge(engine, schema, settings) -> list[str]` (the changes made, like
`upgrade_schema`) take explicit arguments and read no import-time globals, so tests drive them with a
private engine. No new dependency; no model change.

### 2.6 R6 — `[database.timescale]` (deferrable; last phase)

- `data/core.py` pops `timescale` from `config.database` next to `pragmas`, **always** — otherwise
  the table would be forwarded to psycopg as a connection parameter (research 03).
- Keys (plan's vocabulary): `chunk_interval` (text, default `"1 week"`), `columnstore` (bool, `true`),
  `segmentby` (text, `"client_id"`), `orderby` (text, `"id DESC"`), `policy` (bool, `true`),
  `compress_after` (text, `"4 weeks"`).
- Validation in `timescale.Settings.from_config(ns)`, called at import of `data/__init__.py` only
  under a timescale alias: unknown keys; intervals must match `^\s*\d+\s*(microsecond|millisecond|
  second|minute|hour|day|week|month)s?\s*$`; `segmentby` must name `Task` columns; `orderby` items
  must be `<Task column> [ASC|DESC] [NULLS FIRST|LAST]`; `policy = true` with `columnstore = false`
  is a contradiction. A failure prints one `display_critical` line naming the key, the value and the
  accepted form, then `sys.exit(exit_status.bad_config)` — the `data/core.py` convention.
- Disabling pieces: `policy = false` removes an existing policy (INFO); `columnstore = false` with
  columnstore already enabled is the manual-action warning of §2.3.

### 2.7 CI (R9)

Extend the existing `postgres` job in `.github/workflows/tests.yml` (replace the placeholder line);
the plain entries keep their images and steps.

| entry | image (pinned) | `HS_TEST_TIMESCALE` |
|---|---|---|
| `pg13` | `postgres:13` (unchanged) | — |
| `pg18` | `postgres:18` (unchanged) | — |
| `timescale` | `timescale/timescaledb:2.31.0-pg18@sha256:0fcff5761363809d1242f2261a3482197b5a5337407f8b97a976fb174206d579` | `community` |
| `timescale-oss` | `timescale/timescaledb:2.31.0-pg18-oss@sha256:cc00b0ede968b0c6e55212bd55a9a195726afe5a9e19dcf8af49a13334e92ff3` | `apache` |
| `timescale-floor` | `timescale/timescaledb:2.24.<latest>-pg17@sha256:<resolved at build>` | `community` |

- Service `env` gains `TIMESCALEDB_TELEMETRY: 'off'` (ignored by plain images); job `env` gains
  `HS_TEST_TIMESCALE: ${{ matrix.timescale }}` (empty for plain entries). The legacy-template step is
  unchanged (`docker exec psql` works in every image; the templates inherit the extension from
  `template1`, harmless under provider `postgres`).
- The run step becomes `pytest -v -rs tests/test_upgrade_postgres.py tests/test_timescale.py`.
- Digests are Docker Hub index digests (multi-arch), read from
  `https://hub.docker.com/v2/repositories/timescale/timescaledb/tags/<tag>` on 2026-11-02. The floor
  entry's tag and digest are resolved the same way at build and recorded in a workflow comment.
- `tests/test_timescale.py` gating: `HS_TEST_POSTGRES` unset → whole module's integration tests
  skip (unit tests always run, also in the SQLite job). `HS_TEST_TIMESCALE` set → the server must have
  the extension and the stated edition, or the test **fails** (a green entry means the scenarios
  ran). `HS_TEST_TIMESCALE` empty with `HS_TEST_POSTGRES` set → only the extension-absent tier runs.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R2 | §2.1 placement in `initdb`/`truncatedb` behind `TIMESCALE`; §2.3 `create_hypertable` on `id`, 1-week default, `task` only; placeholder warning deleted in `data/core.py` |
| R3 | §2.3/§2.4 columnstore `ALTER TABLE … SET` (`client_id`, `id DESC`) and `add_columnstore_policy` (4 weeks); docs teach the same vocabulary |
| R4 | §2.3 probe-guarded statements under the advisory lock; truncate cascade; drift applied or warned |
| R5 | §2.2 absent/privilege/preload failures before the schema step; §2.3 Apache and non-empty tiers; `CREATE EXTENSION` only when available and not created |
| R6 | §2.6 (P6, deferrable) |
| R7 | §2.2 version floor 2.22.0 (fail); §2.3 policy refused below 2.25.0; docs state both; floor CI entry |
| R8 | `uuid_utils` guard untouched; docs (P4); scenario test asserting a v4 id is rejected with SQLSTATE 0A000 |
| R9 | §2.7 matrix entries + `tests/test_timescale.py` (fresh, re-run, truncate, vacuum, non-empty, absent, Apache, floor); upgrade tests on the TimescaleDB images; SQLite suite untouched |
| R10 | P4: `docs/_include/database.rst` section; `database_alt.rst` paragraph; `install.rst` and `config_param_ref.rst` cross-references |

## 3. Invariant gate (AGENTS.md constitution check)

Walked before research (gate #1: §1, §10, §12, and the Data-layer paragraphs on the schema step and
the retry chain) and again against this design (gate #2).

- **§1 Task lifecycle / `data/model.py`** — untouched. No query, predicate or transition changes; the
  hypertable is transparent to the ORM (root is an ordinary `relkind = 'r'` table). Not a
  high-blast-radius edit.
- **§3 Retry model** — `previous_id`/`next_id` stay plain indexed columns; the one-retry guard is
  unchanged. Conversion relies on #69 having removed the `UNIQUE`s; the pass runs after the step that
  drops them on old databases.
- **Data layer: the schema-upgrade step** — unchanged, and still runs from `initdb` and `checkdb`.
  Its rules (nullable/constant-default columns, non-unique indexes) are exactly what a compressed
  hypertable accepts (research 02 §4), so the invariant needs no amendment. The pass reuses the
  step's advisory key and lock-wait contract rather than inventing a second one.
- **§10 Configuration** — import-time singleton respected: settings are read once at import; R6
  validation exits `bad_config` at startup like `data/core.py`'s other guards; `timescale` is popped
  so it never leaks into the URL.
- **§12 Conventions** — no CLI flag or completion change (`share/` untouched; man pages are rebuilt
  at release, see Risks); new tests tagged `unit`/`integration`; exit codes via `exit_status`; no
  dependency or floor change (`uuid-utils` is already the `uuid7` extra and in `dev`).
- **Spirit rules** — SQLite and `postgres`-provider behavior byte-identical (guarded); a 2.8 database
  under a timescale alias succeeds with a warning; non-goals untouched (no retention, caggs,
  migration tooling, dialect or dependency); `AGENTS.md` § Invariants not altered.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| `data/timescale.py` imports `upgrade.py`'s underscore helpers (`_waiting`, `_lock_postgres`, …) | One lock key and one wait contract for both DDL passes, as the docs already describe | Copying them duplicates the contract; promoting them to public edits `upgrade.py` (another lane's recent file) for no behavior change. The build may promote them if review prefers. |

## 4. Rabbit holes (resolved)

- Current version, vocabulary across 2.22–2.31, introspection columns, drift behavior →
  columnstore vocabulary, gates 2.22/2.25, SQL-side interval comparison
  ([`research/01`](research/01-timescale-sql-and-versions.md)).
- CI image, editions, `template1` behavior, DDL on compressed chunks, workers, `DROP TABLE` cascade →
  pinned digests, a real `-oss` Apache entry, `template0` for the not-created case, schema step safe
  as-is ([`research/02`](research/02-ci-image-and-ddl.md)).
- Where the pass hangs, error mapping, config leak, test harness →
  [`research/03`](research/03-seam-and-tests.md).
- `migrate_data` → never; detect and warn ([`research/00`](research/00-digest.md) decision 2).

## 5. Risks & open questions

- **No local TimescaleDB.** No container runtime on the planning host; every integration scenario is
  proven only by CI on the draft PR (P5, P7). Mitigation: CI lane lands first (P1) so P2/P3 code is
  exercised from its first push; pure logic is unit-tested locally.
- **Unverified by execution:** `CALL add_columnstore_policy` inside a transaction block (source
  says safe); the rendering of `compress_after` (compare as interval regardless); the preload
  failure path (cannot be produced in CI — plain images lack the extension entirely, TimescaleDB
  images preload it). The preload branch is covered by a unit test of the error mapping only.
- **`hs283` and friends now carry the extension** in the TimescaleDB entries; `test_schema_qualified`
  asserts `public` has no tables, which holds (the extension creates no tables in `public`), but P1's
  CI run is the proof.
- **Schema-step edge:** a literal default cast to a domain type would pass `upgrade.py` and fail on a
  compressed hypertable (research 02 §4). No model column does this; recorded, not fixed.
- **Policy workers:** a policy added on an exhausted worker budget never runs; the pass reports a
  failed last run but cannot see a never-started one. Documented (R10).
- **Managed-offering matrix** is from 2026-08-04 research; P4 spot-checks the editions page before
  publishing it, and states the date.
- **Man pages** (`share/man/man1/*.1`) are generated from `database_alt.rst`/`config_param_ref.rst`
  and rebuilt at release; this cycle leaves them (open harness finding
  `spec/schema-upgrade-step/META.md` F4).
- **[NEEDS CLARIFICATION] 1 — R7 "decided by testing".** Options: (a) gate the policy below 2.25.0 on
  the source diff and release note, with a 2.24.x CI entry exercising the gate (recommended); (b) also
  run a policy job on 2.24.x in CI to observe the failure first-hand (adds `run_job` scaffolding and a
  test that asserts a known upstream bug); (c) gate the whole feature at 2.25.0 (simplest, but refuses
  working hypertables on 2.22–2.24).
- **[NEEDS CLARIFICATION] 2 — floor entry cost.** The `timescale-floor` entry is a fifth CI service
  job. Recommended: keep it (cheap, proves the R7 branch). Alternative: drop it and cover the gate by
  unit tests only.

## 6. Verification strategy

- **Unit (local and SQLite CI):** `parse_version`, `gates` table (2.21.x fail; 2.22.0–2.24.x no
  policy; ≥2.25.0 full; apache → hypertable only), message content (names problem and fix), column
  normalization, R6 validation table.
- **Integration (CI, `tests/test_timescale.py`, driving `hs` with
  `HYPERSHELL_DATABASE_PROVIDER=timescale` against throwaway databases):**
  fresh `hs initdb --yes` → hypertable on `id`, 7-day interval, columnstore `client_id`/`id DESC`,
  one `policy_compression` job with `compress_after = 28 days`; re-run twice → identical state, no
  INFO lines, one job; `hs initdb --truncate --yes` → same state, one job; tasks submitted and run
  with `hs cluster`, a chunk compressed by hand, then `hs list` and `hs initdb --vacuum --yes`
  succeed; an empty plain `task` from a `postgres`-provider `initdb` → converted; a non-empty plain
  `task` and the `hs283` template (v4 ids) → warning, `success`, still plain, rows intact; database
  from `template0` → extension created, full story; drift (interval changed, policy re-added with
  another `after`) → restored with INFO; named schema; v4 id insert → SQLSTATE 0A000; Apache entry →
  hypertable, no columnstore, no job, edition warning; floor entry → columnstore, no job, 2.25.0
  warning; plain entries → `runtime_error`, guidance, no traceback, **no tables**.
- **Unchanged suites:** `uv run pytest` (SQLite) and `tests/test_upgrade_postgres.py` on every entry.
- **Docs:** Sphinx build with no new warnings (sandbox-safe form without `sphinx_sitemap`).
- The `temp_site.sh` drive is not used (it needs a server for this provider, and its cleanup is
  disallowed in this lane).

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
