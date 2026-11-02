# GOAL — TimescaleDB provider: hypertables out of the box (TS1)

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** timescale
- **kind:** feature
- **appetite:** big  ·  *One seam and its documentation: an idempotent pass after the schema step in
  the data layer, a TimescaleDB CI job, and a first-class docs section. No new dialect, no new
  dependency beyond the `uuid7` extra the aliases already gate, and no schema change. The seed was
  shaped `medium`, about two days in one cycle; `big` is the skill's round-up (see Clarifications),
  so `hs-plan` does its research pass, which the version facts need anyway. The seed's R1 was
  discharged by `schema-upgrade-step` (#69); this cycle carries the seed's R2–R10, under the seed's
  own numbers.*

## Problem

The `timescale` and `timescaledb` provider aliases already do two things: they route to the
`postgresql+psycopg` dialect, and they require the `uuid7` extra so ids are time-ordered UUIDv7s.
They deliver nothing TimescaleDB-shaped. The schema comes up as plain PostgreSQL tables, and
`data/core.py` says so at every startup:

> `TimescaleDB provider selected: automatic hypertable management (post-create_all hook) is not
> yet implemented — the schema is created as plain PostgreSQL tables`

To get hypertable chunking, columnstore compression and a compression policy, a user has to run
`psql` by hand against a database HyperShell is already connected to:

```sql
select create_hypertable('task', by_range('id', INTERVAL '1 week'));
alter table task set (timescaledb.compress,
                      timescaledb.compress_orderby = 'id DESC',
                      timescaledb.compress_segmentby = 'client_id');
select add_compression_policy('task', interval '4 weeks');
```

The alias exists to remove exactly this step. Since TimescaleDB 2.22.0, a hypertable can partition
directly on a UUIDv7 column: the embedded millisecond timestamp drives time-based chunking. Our
UUIDv7 primary key is therefore the partition key, and every id-keyed operation carries row
identity, time and chunk routing in one value.

Running the statements by hand is not the same as out of the box. The seed's research (2026-08-04)
found rough edges that the automation has to own:

1. **Schema blocker — discharged.** A hypertable needs every unique index to include `id`; the old
   `UNIQUE` on `previous_id`/`next_id` blocked conversion. `schema-upgrade-step` (#69) dropped those
   constraints for new databases and, on upgrade, for existing PostgreSQL ones.
2. **A licensing cliff.** Hypertables are Apache-2. Compression, the columnstore and the policy jobs
   framework are Community edition (Timescale License) only. Apache-only builds have hypertables
   but no compression, and several managed PostgreSQL offerings don't ship the extension at all.
3. **A version floor with teeth.** UUIDv7 partitioning landed in 2.22.0; the compression-policy path
   on UUID hypertables was only fixed in 2.25.0.
4. **UUIDv4 poisoning.** Chunk routing rejects a non-v7 UUID per row (SQLSTATE `0A000`), aborting
   the whole INSERT. Every process that mints ids needs the `uuid7` extra.
5. **A SQLAlchemy trap.** Table-level `after_create` events do not fire for tables that already
   exist under `create_all(checkfirst=True)`. An event hook can never converge a database that was
   initialized earlier; this needs an explicit idempotent pass.

**Re-confirmed against `develop` at `d996579` (2.9.0a2)**, with the drift since the seed:

- The placeholder warning is still at `src/hypershell/data/core.py` L232–233, under the
  `TIMESCALE_ALIASES` guard (L225–231).
- `initdb()` is now `upgradedb()` plus optional SQLite optimize (`src/hypershell/data/__init__.py`
  L64–78). `upgradedb()` runs `data/upgrade.py`, which creates missing tables, columns and indexes
  and drops the retired `UNIQUE` constraints on PostgreSQL. `checkdb()` also runs `upgradedb()`, on
  every PostgreSQL start (L91–95). `truncatedb()` still does `drop_all` then `create_all` (L80–88).
  The pass therefore hangs off the schema step, not off a bare `create_all`.
- `previous_id`/`next_id` are now plain columns with plain indexes (`data/model.py` L300–301,
  L910–911). No further schema change is needed.
- CI has a `postgres` job (`.github/workflows/tests.yml` L40–83), pg13 and pg18. It runs only
  `tests/test_upgrade_postgres.py` against legacy template databases, and it already carries a
  commented-out `timescale` matrix entry (L54). The general suite runs on SQLite only
  (`tests/conftest.py` sets `HYPERSHELL_DATABASE_FILE`). This bears on R9; see Clarifications.
- `turso` is still a `shaped` seed. Its per-provider seam (turso R4) and its guidance pattern (turso
  R2) do not exist on `develop`. This cycle lands first, so it conforms to the existing `postgres`
  and `uuid7` guards and either establishes a small seam or does without; it does not take on
  turso's scope.
- `docs/_include/database.rst` (included by `docs/database.rst`) does not mention TimescaleDB yet.

## Outcome / vision

A user installs `hypershell[postgres,uuid7]`, sets `database.provider = timescale`, and runs
`hs initdb` (or launches with `--initdb`), and does nothing else. HyperShell brings the schema
current and then, on the connection it already holds, converges `task` to the TimescaleDB shape: a
hypertable partitioned on the UUIDv7 `id` with a sensible chunk interval, columnstore compression
segmented and ordered for our access patterns, and a policy that compresses sealed chunks. Running
it again changes nothing; running it after an upgrade adds whatever is missing. The startup warning
goes away because the thing it apologizes for now exists.

The environment is interrogated, not assumed. The pass reads the extension, its version and its
edition, and is honest at each tier: everything where the environment supports it, hypertable-only
with a loud warning on the Apache edition, and an actionable failure where the extension is absent
or too old. Nobody finds the licensing cliff through a stack trace.

Users of SQLite, and of PostgreSQL under the `postgres` provider, see no difference at all.

The documentation makes TimescaleDB a first-class citizen of the database page: why our UUIDv7
primary key suits a hypertable, what the automation does and how to tune it, an honest support
matrix, and the query ergonomics of a UUID-partitioned table.

This is the technical foundation for a later blog and demo, which are not graded here.

## Acceptance criteria (the contract)

Each R-ID is marked **load-bearing** (the cycle does not ship without it) or **deferrable** (it may
be cut to a follow-up if the cycle runs over appetite; see Clarifications). The version facts cited
below are as of 2026-08-04 (latest then 2.27.2): **recheck them at plan time**.

**Hypertable automation**

- **R2** *(load-bearing)* — WHEN `provider` is a timescale alias, `initdb` SHALL, after the schema
  step (`upgradedb()`, which runs `create_all`), converge the `task` table to a hypertable
  partitioned by `id` (`by_range` with an `INTERVAL`, default 1 week). It SHALL do so through an
  **explicit idempotent pass**, not `after_create` DDL events, which never fire for pre-existing
  tables under `checkfirst=True`. Only `task` is converted; `client` and `source` remain plain
  tables. The placeholder startup warning in `data/core.py` SHALL be removed. WHEN `provider` is not
  a timescale alias, `initdb`, `checkdb`, `truncatedb` and `vacuumdb` SHALL behave exactly as on the
  base commit.
- **R3** *(load-bearing)* — The same pass SHALL configure columnstore compression (defaults:
  `segmentby = 'client_id'`, `orderby = 'id DESC'`) and install a compression policy (default:
  compress chunks older than 4 weeks). Whether it emits the legacy vocabulary
  (`timescaledb.compress`, `add_compression_policy()`) or the 2.18+ columnstore vocabulary
  (`timescaledb.enable_columnstore`, `CALL add_columnstore_policy(...)`) is the plan's choice; both
  are accepted at the supported floor. The docs SHALL teach the modern vocabulary regardless.
- **R4** *(load-bearing)* — Every statement in the pass SHALL be idempotent (`if_not_exists` where
  the API offers it, guarded otherwise). Re-running `initdb`, launching with `--initdb` repeatedly,
  and `truncatedb`'s drop-all/create-all cycle SHALL all converge to the same state, with no errors
  and no duplicate policies. Settings drift (a configured chunk interval or compression option that
  differs from what the database holds) SHALL be handled honestly: applied where TimescaleDB
  permits, or logged as needing manual action, never silently ignored. (`ALTER TABLE ... SET`
  compression options and `set_chunk_time_interval` both affect future chunks only.)
- **R5** *(load-bearing)* — The pass SHALL interrogate the environment and degrade by tier:
    - Extension absent, or `CREATE EXTENSION` not possible → **hard fail** at `initdb` with
      actionable guidance, matching the existing `postgres` and `uuid7` dependency guards: name the
      problem, name the fix, exit `runtime_error`. Never a raw traceback, and never silently plain
      tables under a provider that promises otherwise.
    - Extension present, Apache edition (`timescaledb.license = 'apache'`) → create the hypertable,
      **skip** compression and the policy, and warn prominently, naming the edition limitation.
    - Extension present, Community edition → everything in R2–R4.
    - A pre-existing **non-empty plain** `task` table SHALL NOT be migrated silently
      (`migrate_data => true` takes long locks and aborts on any non-v7 id). The pass SHALL detect
      this case, leave the table untouched, warn with a pointer to the documented manual migration,
      and let `initdb` succeed, so that a database created by an earlier release under a timescale
      alias keeps working after the schema step upgrades it.

  WHEN the extension is installed on the server but not yet created in the database, the pass MAY
  attempt `CREATE EXTENSION IF NOT EXISTS timescaledb` (checking `pg_extension` first), but SHALL
  NOT require superuser when the extension already exists.
- **R6** *(deferrable)* — A `[database.timescale]`-style config table (name illustrative; the
  `[database.pragmas]` precedent) SHALL override the defaults: chunk interval, compression on/off,
  `segmentby`/`orderby`, policy on/off and its `after` interval. Zero configuration SHALL work.
  Invalid values SHALL fail at startup with guidance, matching config validation elsewhere. The
  exact key vocabulary is the plan's.
- **R7** *(load-bearing)* — The pass SHALL enforce a version floor from measurement, not hope. It
  reads the extension version from `pg_extension`. Below the floor for UUIDv7 partitioning
  (2.22.0) it SHALL hard fail with guidance. Between 2.22.0 and the fixed-policy release (2.25.0)
  the plan SHALL either refuse the policy step with a warning or gate the whole feature, decided by
  testing. The supported floor SHALL be stated in the docs. Validate against current stable
  (2.27.x as of 2026-08; recheck at plan time).
- **R8** *(load-bearing)* — UUIDv7 integrity SHALL be treated as a contract. The existing `uuid7`
  import guard remains. The docs SHALL name the failure mode for v4 ids on a UUID-partitioned
  hypertable (per-row SQLSTATE `0A000`, aborting the INSERT bundle) and state plainly that **every
  id-minting process** (anything running `submit`) needs the `uuid7` extra, not just the server.
- **R9** *(load-bearing; Apache-edition coverage in CI deferrable)* — CI SHALL gain a TimescaleDB
  service job, using a pinned `timescale/timescaledb` image (Community edition). It SHALL run the
  existing PostgreSQL upgrade tests against that image, and new tests for: a fresh `initdb`
  (hypertable, compression settings and policy present, asserted through the
  `timescaledb_information` views); an `initdb` re-run (idempotency); truncate-then-recreate;
  `vacuumdb` against a hypertable; the non-empty plain-table case (R5); and the extension-absent
  tier, run against plain PostgreSQL. The Apache-edition tier MAY be covered by unit-level seams if
  no Apache-only image is practical in CI; the plan decides and documents which. The SQLite suite
  and the existing `postgres` job SHALL pass unchanged.
  *(Narrowed from the seed's "the full test suite SHALL run green with a timescale provider"; open
  for sign-off, see Clarifications.)*

**Documentation**

- **R10** *(load-bearing)* — The database documentation (`docs/_include/database.rst`) SHALL gain
  a TimescaleDB section of equal standing with SQLite and PostgreSQL, covering:
    - provider selection and what the automation does;
    - the UUIDv7-as-partition-key story (the id carries identity, time and chunk routing; chunk
      exclusion on id predicates);
    - the `[database.timescale]` reference (if R6 lands);
    - an honest support matrix (version floor; Apache and Community editions; self-hosted images and
      Tiger Cloud, against managed offerings that lack the extension);
    - query ergonomics: `to_uuidv7_boundary()` for time filters, `uuid_timestamp()` for display, and
      a warning that filtering on `uuid_timestamp(id)` defeats chunk exclusion;
    - the background-worker budget that policies need in order to run;
    - current naming (the extension is TimescaleDB; the company is TigerData, rebranded 2025; the
      managed product is Tiger Cloud).

  Per repo convention, CLI or config changes update the affected `docs/_include/*.rst` snippets and
  `share/` completions in the same commit.

## Non-goals (no-gos)

*Carried verbatim from the seed.*

- **The SC26 blog post and the billion-task demo.** The "Scaling to One Billion Tasks" blog
  (title aspirationally "One Trillion"), the simulated-pipeline data generator (statistical
  ingestion model, `--from-json` bulk loading, REFITT-shaped topology), and the SC26 booth
  narrative are the *payoff* of this feature, not its contract — they live in
  `../hypershell.org/` and draw on `.local/timescale-ideas-transcript.txt`. This cycle ships the
  feature and docs they stand on. (Resolved 2026-08-04: feature + docs in scope; blog and demo
  are a named follow-on.)
- **Tiered storage / S3 offload.** Chunk offload to object storage is Tiger Cloud
  infrastructure only (no self-hosted availability as of 2026-08); the transcript's interest in
  it becomes a docs watch note, exactly like `turso`'s database-on-S3 non-goal. Not a shipped
  claim.
- **Retention policies and `drop_chunks` automation.** The entire point of this provider is
  *keeping* task history cheaply (compressed chunks), not deleting it. We do not auto-install
  retention; a user who wants it has the full TimescaleDB API and our docs pointer.
- **Continuous aggregates.** Supported on UUIDv7 hypertables since 2.24.0 and interesting for
  the blog's dashboard queries, but application-specific — nothing to automate generically.
  Docs may mention; nothing is created.
- **Hypertabling `client` or `source`.** Small dimension tables; plain PostgreSQL is correct.
- **A custom dialect or new dependency.** No `sqlalchemy-timescaledb` (stale, custom URL scheme,
  event-hook design that misses existing tables), no `timescaledb`-python package dependency —
  the pass is a handful of SQL statements we own. The provider aliases and `postgresql+psycopg`
  routing are untouched.
- **Migration tooling for large existing installs.** `migrate_data => true` is documented as the
  manual path with its caveats (long locks, single transaction, aborts on non-v7 ids); we do not
  wrap, schedule, or babysit it in 2.9.0.
- **`enable_chunk_skipping` on secondary columns.** Community-edition feature aimed at columns
  *correlated* with the partition key; our obvious candidates (`exit_status`, `group`) are
  uncorrelated and would skip nothing. Columnstore min/max and bloom sparse indexes already
  cover the transcript's "count failed tasks for all time" story. Revisit with benchmarks in
  the blog work, not here.

## Clarifications

The seed's own clarifications (all resolved 2026-08-04: explicit pass, not an event hook; uniqueness
dropped globally; tiered degradation; a config section plus defaults) stand and are not reopened.
See [`issues/timescale.md`](../../issues/timescale.md).

- **2026-11-01 — Scope of this cycle is the seed's R2–R10, one cycle** (maintainer, alignment memo,
  2026-11-01). R1 was discharged by `schema-upgrade-step` (#69), with its invariant amendment
  approved. The R-IDs keep the seed's numbers so the seed, the ROADMAP and this GOAL agree; there is
  deliberately no R1 here.
- **2026-11-01 — Load-bearing and deferrable** (maintainer, alignment memo, 2026-11-01).
  Load-bearing: R2–R5 and R7–R10. Deferrable: R6, and CI coverage of the Apache-edition tier, which
  R9 already allows at unit level. Deferring R6 defers the *override table* only: the zero-config
  defaults are carried by R2 and R3 and stay load-bearing, R4's settings-drift clause then applies
  only to changes in those defaults, and R10 drops the config reference. A deferral is recorded here
  as a dated Clarification, and the deferred piece moves to Non-goals and back to the seed, before
  `hs-review`.
- **2026-11-01 — Appetite `medium` rounds up to `big`** (hs-feature rule; maintainer, alignment
  memo, 2026-11-01, sized the work as one medium cycle of about two days). `big` buys `hs-plan`'s
  research pass, which the version facts need. The seed's frontmatter is left as written.
- **2026-11-01 — Spirit rules** (maintainer, alignment memo, 2026-11-01): non-timescale PostgreSQL
  and SQLite behave exactly as today (R2's last sentence; R9's unchanged suites); no break of a
  database created by 2.8 without the schema step (R5's non-empty case succeeds with a warning
  rather than failing `initdb`); the non-goals are not reopened; `AGENTS.md` § Invariants is not
  altered beyond what R1 already did.
- **2026-11-01 — CI image** (maintainer, alignment memo, 2026-11-01): a pinned
  `timescale/timescaledb` image is fine in CI. The tag is chosen at plan time against the then
  current stable.
- **2026-11-01 — Version facts are as of 2026-08-04 (2.27.2); recheck at plan time** (maintainer,
  alignment memo, 2026-11-01). Not researched during shaping.
- **2026-11-01 — Target: land as a 2.9.0 alpha before 2026-11-13** (maintainer, alignment memo,
  2026-11-01).
- **[NEEDS CLARIFICATION: R9's "full test suite with a timescale provider".]** The seed asks for
  the full suite to run green under a timescale provider. On `develop` the suite runs on SQLite only
  (`conftest.py` sets `HYPERSHELL_DATABASE_FILE`), and the `postgres` job runs only
  `test_upgrade_postgres.py`; there is no harness that points the whole suite at a server database.
  Options: **(a)** the TimescaleDB job runs the upgrade tests plus a new TimescaleDB test module
  with the R9 scenarios, as R9 is written above; **(b)** build a harness to run the whole suite
  against a server database (PostgreSQL and TimescaleDB), a separate piece of work that likely
  breaks the appetite. **Recommended: (a)**, with (b) filed as its own seed if wanted.

- **Note (for `hs-plan`):** *where the pass hangs.* `initdb()` and `truncatedb()` run it. `checkdb()`
  runs on every PostgreSQL start and already runs the schema step; the seed leaves it untouched
  (`has_table` is hypertable-transparent). Keep that unless the plan finds a reason; a fresh
  timescale database always goes through `initdb` first, since `checkdb` refuses an uninitialized
  one.
- **Note (for `hs-plan`):** *the schema step on a hypertable.* After this cycle, `upgradedb()` runs
  against hypertables, some of whose chunks are compressed. Its future additions (nullable or
  constant-default columns, non-unique indexes) must stay valid there; confirm what TimescaleDB
  allows on compressed hypertables, and decide whether the timescale `postgres` matrix entry
  (`tests.yml` L54) or a separate job is the home for R9.
- **Note (for `hs-plan`):** the seed's notes on version facts, exact error strings and probes,
  policies needing background workers, idempotency fine print, query-ergonomics helpers, and the
  touchpoint sketch (in `issues/timescale.md`, Clarifications) carry over as research input. Their
  version facts are as of 2026-08-04; recheck them.

## Related materials

- Seed: [`issues/timescale.md`](../../issues/timescale.md) — this cycle carries its R2–R10; R1 was
  discharged by [`schema-upgrade-step`](../schema-upgrade-step/GOAL.md) (#69).
- Source: `src/hypershell/data/core.py` (`TIMESCALE_ALIASES`, the `uuid7` guard, the placeholder
  warning); `src/hypershell/data/__init__.py` (`initdb`, `upgradedb`, `truncatedb`, `checkdb`,
  `vacuumdb`); `src/hypershell/data/upgrade.py` (the schema step the pass follows);
  `src/hypershell/data/model.py` (`Task`, its indexes); `src/hypershell/core/uuid.py` (the UUIDv7
  source, unchanged).
- Tests and CI: `tests/test_upgrade_postgres.py`, `tests/fixtures/make-legacy-postgres.sh`,
  `.github/workflows/tests.yml` (`postgres` job).
- Docs: `docs/_include/database.rst` (via `docs/database.rst`), `docs/config.rst`,
  `docs/install.rst` (the `uuid7` extra).
- Invariants: `AGENTS.md`, the schema-upgrade step paragraph (runs from `initdb` and `checkdb`; adds
  only nullable or constant-default columns and non-unique indexes), and the retry-chain note
  (`previous_id`/`next_id` not unique).
- Upstream: TimescaleDB `create_hypertable`/`by_range`, UUID functions, columnstore `ALTER TABLE`
  and `add_columnstore_policy`, the editions comparison, and the self-hosted background-worker
  configuration; release notes in the `timescale/timescaledb` repository.
