---
status: shaped
kind: feature
appetite: medium
lane: public
shaped-as: timescale
---

# TimescaleDB provider: hypertables out of the box

> **Shaped candidate — not yet a contract.** The shaping conversation already happened with a
> human: the clarifications below are dated and resolved, the appetite and non-goals are agreed,
> and the R-IDs are drafted. What has *not* happened is acceptance into a cycle — `/hs-feature`
> does that, creating the branch and `spec/{slug}/GOAL.md`, and it is `hs-review` that grades the
> result. Until then nothing here is graded and nothing here is locked.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** timescale
- **kind:** feature
- **appetite:** medium  ·  *One seam and its documentation: an idempotent post-`create_all`
  pass in the data layer plus a first-class docs treatment. No new dialect, no new dependency
  beyond what the aliases already gate (`uuid7`), no schema additions — one schema *subtraction*
  (R1). Joins the 2.9.0 cycle after `zone`/`relay`; sequencing against `turso` is loose, but
  `turso` R4's per-provider abstraction in `data/core.py` is the natural home for this
  provider's hooks — whichever lands second plugs into (or establishes) that seam.*

## Problem

The `timescale`/`timescaledb` provider aliases already exist and already do two things: route
to the `postgresql+psycopg` dialect, and gate the `uuid7` extra so `Task.id` is a time-ordered
UUIDv7. What they do *not* do is deliver anything TimescaleDB-shaped. Today the schema comes up
as plain PostgreSQL tables and `data/core.py` says so out loud at every startup:

> `TimescaleDB provider selected: automatic hypertable management (post-create_all hook) is not
> yet implemented — the schema is created as plain PostgreSQL tables`

To actually get the value — hypertable chunking, columnstore compression, a compression policy —
a user must hand-run `psql` against a database HyperShell is already connected to:

```sql
select create_hypertable('task', by_range('id', INTERVAL '1 week'));
alter table task set (timescaledb.compress,
                      timescaledb.compress_orderby = 'id DESC',
                      timescaledb.compress_segmentby = 'client_id');
select add_compression_policy('task', interval '4 weeks');
```

This is exactly the kind of pain the provider alias was created to remove. The prerequisite that
used to make this hard is gone: since TimescaleDB 2.22.0 (2025-09) hypertables partition
**directly on a UUIDv7 column** — the embedded 48-bit millisecond timestamp drives time-based
chunking, `by_range('id', INTERVAL ...)` is the documented syntax, and no separate timestamp
column or compound key is needed. Our UUIDv7 primary key *is* the partition key: every id-keyed
operation (the writeback loop's bread and butter) implicitly carries row identity, timestamp, and
chunk routing in one value, and chunk exclusion prunes id-predicate queries to a single chunk.
Empirically the three statements above work today.

But "works when I run it by hand" is not out-of-the-box, and research (2026-08-04) surfaced real
rough edges an automation must own rather than let users trip over:

1. **A hard schema blocker.** `create_hypertable('task', ...)` fails outright against our current
   schema: `Task.previous_id` and `Task.next_id` are `UNIQUE`, and a hypertable requires every
   unique index to include the partition column
   (`ERROR: cannot create a unique index without the column "id" (used in partitioning)`).
   Widening to composite `UNIQUE (previous_id, id)` passes validation but enforces nothing
   (`id` is already unique per row), and exclusion constraints go through the same check.
2. **A licensing cliff.** Hypertables are Apache-2; compression/columnstore **and** the policy
   jobs framework are Timescale-License (Community edition) only. Self-hosted default images and
   Tiger Cloud have everything; Apache-only builds (`-oss` images, Azure Flexible Server, Crunchy)
   have hypertables but no compression; the major managed offerings (RDS, Aurora, Cloud SQL,
   AlloyDB) don't ship the extension at all.
3. **A version floor with teeth.** UUIDv7 partitioning landed in 2.22.0, but the surrounding
   machinery matured across releases — `show_chunks`/`drop_chunks` fixed in 2.23.0, `time_bucket`
   and continuous aggregates in 2.24.0, and the compression-policy path on UUID hypertables had a
   missing-check bug fixed only in **2.25.0**. Our three statements are reliable at ≥2.25.0, not
   at "any version with UUID support."
4. **UUIDv4 poisoning.** Chunk routing rejects non-v7 UUIDs per row
   (`SQLSTATE 0A000: ... is not a version 7 UUID`) — one stray v4 id aborts the whole INSERT.
   Ids are minted by whichever process runs `submit`; the existing `uuid7` guard covers processes
   configured with the timescale provider, but the failure mode deserves honest documentation.
5. **A subtle SQLAlchemy trap.** Table-level `after_create` events do **not** fire for tables that
   already exist under `create_all(checkfirst=True)` — an event-hook design can never converge a
   database initialized before this feature (or repair a half-configured one). The pragmas-style
   connect event is the wrong template here; this wants an explicit idempotent pass.

## Outcome / vision

A user installs `hypershell[postgres,uuid7]`, sets `database.provider = timescale`, and runs
`hs initdb` (or launches with `--initdb`) — nothing else. HyperShell creates the schema and then,
on the same connection it already holds, converges the `task` table to the TimescaleDB shape:
hypertable partitioned by the UUIDv7 primary key with a sensible chunk interval, columnstore
compression segmented and ordered for our access patterns, and a compression policy that
graduates sealed chunks to compressed columnar storage automatically. Run it again and nothing
happens; run it after an upgrade and anything missing is added. The startup warning in
`data/core.py` is deleted because the thing it apologizes for now exists.

Defaults work with zero configuration and mirror the hand-run statements above (1-week chunks,
compress after 4 weeks, `segmentby = client_id`, `orderby = id DESC`). A `[database.timescale]`
config table (name illustrative — the pragmas precedent) overrides chunk interval and compression
settings or disables pieces outright, so an operator who knows their workload can tune without
ever leaving HyperShell configuration.

The environment is interrogated, not assumed: initdb detects the extension, its version, and its
license edition, and behaves honestly at each tier — full story where the environment supports
it, a degraded-but-working story where only Apache-edition features exist, and an actionable
failure where the extension is absent. Nobody discovers a licensing cliff via a stack trace.

The documentation makes Timescale a first-class citizen of the database page: what a hypertable
is and why our UUIDv7 primary key makes HyperShell unusually suited to it (the id *is* the
timestamp *is* the partition key), how the automation behaves and how to configure it, an honest
support matrix (versions, editions, where managed offerings fall short), and the query
ergonomics of a UUID-partitioned world — including that time-range filters belong on the id via
`to_uuidv7_boundary()` and that wrapping the id in `uuid_timestamp()` defeats chunk exclusion.

This is the technical foundation for the SC26 story (docs → blog → 1-billion-task demo), but the
blog and demo are deliberately *not* graded here — they live in `../hypershell.org/` (see
Non-goals).

## Acceptance criteria (the contract)

**Schema readiness** *(unblocks everything; provider-independent)*

- **R1** — The `UNIQUE` constraints on `Task.previous_id` and `Task.next_id` SHALL be removed
  **for all providers** (one schema everywhere — provider-switching honesty), replaced with
  plain non-unique indexes preserving lookup performance; uniqueness of these app-minted UUIDs
  is enforced by construction, and the rationale SHALL be recorded in the model. No other
  schema change is required for hypertable conversion (the lone-column primary key `id` *is*
  the partition column and passes validation as-is). (Resolved 2026-08-04.)

**Hypertable automation** *(the feature)*

- **R2** — WHEN `provider` is a timescale alias, `initdb` SHALL — after `create_all` — converge
  the `task` table to a hypertable partitioned by `id` (`by_range` with a configurable
  `INTERVAL`, default 1 week), via an **explicit idempotent post-create pass**, not
  `after_create` DDL events (which never fire for pre-existing tables under `checkfirst=True`).
  Only `task` is converted; `client` and `source` remain plain tables. The placeholder startup
  warning in `data/core.py` SHALL be removed.
- **R3** — The same pass SHALL configure columnstore compression (defaults:
  `segmentby = 'client_id'`, `orderby = 'id DESC'`) and install a compression policy (default:
  compress chunks older than 4 weeks). Whether to emit the legacy vocabulary
  (`timescaledb.compress`, `add_compression_policy()`) or the 2.18+ columnstore vocabulary
  (`timescaledb.enable_columnstore`, `CALL add_columnstore_policy(...)`) is the plan's choice —
  both are accepted at the supported floor; docs SHALL teach the modern vocabulary regardless.
- **R4** — Every statement in the pass SHALL be idempotent (`if_not_exists` where the API offers
  it; guarded otherwise): re-running `initdb`, launching with `--initdb` repeatedly, and
  `truncatedb`'s drop-all/create-all cycle all converge to the same state with no errors and no
  duplicate policies. Settings drift (a config change to chunk interval or compression options)
  SHALL be handled honestly: applied where TimescaleDB permits, or logged as requiring manual
  action — never silently ignored (note: `ALTER TABLE SET` compression options affect future
  chunks only; `set_chunk_time_interval` affects future chunks only).
- **R5** — The pass SHALL interrogate the environment and degrade by tier
  (resolved 2026-08-04):
    - Extension absent (or `CREATE EXTENSION` not possible) → **hard fail** at initdb with the
      established actionable-guidance pattern (`turso` R2; the `postgres`/`uuid7` guards):
      name the problem, name the fix, exit `runtime_error` — never a raw traceback, never
      silently-plain tables under a provider that promises otherwise.
    - Extension present but Apache edition (`timescaledb.license = 'apache'`) → create the
      hypertable (supported), **skip** compression and policies with a prominent warning naming
      the edition limitation; documented, not buried.
    - Extension present, Community edition → full story.
    - A pre-existing **non-empty plain** `task` table SHALL NOT be silently migrated:
      `migrate_data => true` takes long locks and aborts on any non-v7 id; the pass SHALL detect
      this case, leave the table untouched, and point to a documented manual migration path.
  When the extension is installed but not yet created in the database, the pass MAY attempt
  `CREATE EXTENSION IF NOT EXISTS timescaledb` (pre-checking `pg_extension` first, per the
  Django precedent) but SHALL NOT require superuser when the extension already exists.
- **R6** — A `[database.timescale]`-style config table SHALL override the defaults: chunk
  interval, compression on/off, `segmentby`/`orderby`, policy on/off and its `after` interval.
  Zero configuration SHALL work. Invalid values fail at startup with guidance, matching config
  validation conventions elsewhere. (Resolved 2026-08-04.)
- **R7** — The pass SHALL enforce a version floor from measurement, not hope: extension version
  detected from `pg_extension`; below the floor for UUIDv7 partitioning (2.22.0) → hard fail
  with guidance; between 2.22.0 and the fixed-policy floor (2.25.0) the plan SHALL either refuse
  the policy step with a warning or gate the whole feature — decided by testing, and the
  supported floor SHALL be stated in docs. Target validation version: current stable
  (2.27.x as of 2026-08).
- **R8** — UUIDv7 integrity SHALL be treated as a contract: the existing `uuid7` import guard
  remains; the docs SHALL name the failure mode for v4 ids on a UUID-partitioned hypertable
  (per-row `SQLSTATE 0A000` aborting the INSERT bundle) and state plainly that **every
  id-minting process** (anything running `submit`) needs the `uuid7` extra, not just the server.
- **R9** — The full test suite SHALL run green with a timescale provider as a CI matrix job
  (service container from the official `timescale/timescaledb` image), additionally covering:
  fresh initdb (hypertable + settings + policy present — assert via
  `timescaledb_information` views), initdb re-run (idempotency), truncate-then-recreate,
  `vacuumdb` against a hypertable, and the R5 degradation tiers that can be simulated
  (extension-absent against plain postgres; Apache-edition tier MAY be covered by unit-level
  seams if no OSS image is practical in CI — the plan decides and documents).

**Documentation** *(first-class citizen)*

- **R10** — The database documentation SHALL gain a TimescaleDB section of equal standing with
  SQLite and PostgreSQL: provider selection and what the automation does; the UUIDv7-as-partition-key
  story (id carries identity + time + chunk routing; chunk exclusion on id predicates); the
  `[database.timescale]` reference; an honest support matrix (version floor, Apache vs Community
  editions, self-hosted images and Tiger Cloud vs managed offerings that lack the extension);
  query ergonomics (`to_uuidv7_boundary()` for time filters, `uuid_timestamp()` for display,
  and the anti-pattern warning that filtering on `uuid_timestamp(id)` defeats chunk exclusion);
  and current naming (the extension is TimescaleDB; the company is TigerData, rebranded 2025;
  the managed product is Tiger Cloud). Per repo convention, CLI/config changes update the
  affected `docs/_include/*.rst` snippets and `share/` completions in the same commit.

## Non-goals (no-gos)

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

- **Q:** Event hook (the SQLite-pragmas pattern) or explicit pass? — **A:** **Explicit
  idempotent post-`create_all` pass** (resolved 2026-08-04). Verified against SQLAlchemy source:
  table-level `after_create` does not fire for pre-existing tables under `checkfirst=True`, and
  MetaData-level events receive only actually-created tables — an event design can never
  converge an already-initialized database. Prior art agrees: the maintained
  `timescaledb`-python package uses exactly this two-phase shape; the event-based
  `sqlalchemy-timescaledb` is unmaintained (last release 2023). The connect-event pragma
  precedent stays what it is — per-connection state, not one-time DDL.
- **Q:** What about `previous_id`/`next_id` uniqueness? — **A:** **Dropped globally, plain
  indexes remain** (resolved 2026-08-04). Composite `UNIQUE (previous_id, id)` is semantically
  vacuous; exclusion constraints hit the same validation; a side-table adds a write and an FK
  entanglement with chunk operations for belt-and-suspenders on app-minted UUIDs. One schema
  for all providers preserves switching honesty.
- **Q:** Fail or degrade when the environment falls short? — **A:** **Tiered** (resolved
  2026-08-04): missing extension → hard fail with guidance; Apache edition → hypertable
  without compression, loudly; Community → everything. `provider = timescale` must never
  produce silently-plain tables.
- **Q:** How much config? — **A:** **Section + defaults** (resolved 2026-08-04). Zero-config
  mirrors the hand-run statements; `[database.timescale]` overrides; the exact key vocabulary
  is the plan's.
- **Note (for `hs-plan`):** *version facts (researched 2026-08-04, two independent passes +
  spot-verification).* UUIDv7 partitioning: 2.22.0 (2025-09-02, PR #8505), GA from day one;
  `show_chunks`/`drop_chunks` on UUID hypertables fixed 2.23.0; UUID compression enabled by
  default 2.23.0; `time_bucket` on UUIDv7 + continuous aggregates 2.24.0; retention policies on
  UUIDv7 + the compression-policy missing-UUID-check fix 2.25.0; real-time cagg fixes
  2.25.2/2.26.0. Latest released: **2.27.2 (2026-06-02)**; supports PG 15–18, with 2.27.0 notes
  flagging the June-2026 series as the last with PG15. Default chunk interval when omitted:
  7 days. (Claims about 2.28+/2.29 circulating from the main-branch CHANGELOG are unreleased —
  do not pin against them.)
- **Note (for `hs-plan`):** *exact strings and probes.* Unique-index rejection:
  `cannot create a unique index without the column "id" (used in partitioning)` (from
  `ts_indexing_verify_columns`, also covers exclusion constraints via
  `verify_constraint_hypertable`). v4 rejection: `<uuid> is not a version 7 UUID`,
  SQLSTATE `0A000`, per row at chunk routing. Presence/version:
  `SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'`. Edition:
  `SELECT current_setting('timescaledb.license', true)` → `timescale` | `apache` (the `true`
  returns NULL rather than erroring when not loaded). Un-preloaded extension:
  `CREATE EXTENSION` fails **FATAL** (`extension "timescaledb" must be preloaded` —
  `shared_preload_libraries`), killing the connection — catch the dropped connection, not just
  an error result. Extension control file is `trusted = true` on current releases (non-superuser
  create with database CREATE privilege, once preloaded).
- **Note (for `hs-plan`):** *policies need workers to actually run.* `add_*_policy` succeeds
  even when `timescaledb.max_background_workers` (default 16) is exhausted — the job then never
  fires. Consider a post-install sanity read of `timescaledb_information.jobs` /
  `job_stats.last_run_status`, and put the worker-budget note in docs either way.
- **Note (for `hs-plan`):** *idempotency fine print.* `create_hypertable(..., if_not_exists =>
  true)` warns instead of erroring and reports `created = false`; `add_columnstore_policy` /
  `add_compression_policy` accept `if_not_exists` too. `ALTER TABLE ... SET` compression
  options re-run cleanly but apply to *future* chunks (known idempotency thread:
  timescale/timescaledb#6137); `set_chunk_time_interval` likewise. R4's "honestly handled"
  language exists for exactly this.
- **Note (for `hs-plan`):** *query-ergonomics facts for R10.* Helper functions (all 2.22.0):
  `generate_uuidv7()`, `to_uuidv7(timestamptz)`, `to_uuidv7_boundary(timestamptz)` (zeroed
  random bits — the documented device for range predicates that keep chunk exclusion),
  `uuid_timestamp(uuid)`, `uuid_timestamp_micros(uuid)`, `uuid_version(uuid)`. PG18 natively:
  `uuidv7()`, `uuid_extract_timestamp()`. Setting `orderby` creates an implicit min/max sparse
  index on `id` in columnstore chunks; bloom sparse indexes (2.19+) accelerate point lookups on
  compressed chunks. `WHERE uuid_timestamp(id) > ...` scans everything — the anti-pattern box
  writes itself.
- **Note (for `hs-plan`):** *touchpoint sketch (to confirm).* `src/hypershell/data/core.py`
  (provider aliases + `TIMESCALE_ALIASES` exist; the placeholder warning to delete; per-provider
  seam shared with `turso` R4); `src/hypershell/data/__init__.py` (`initdb`/`truncatedb` —
  the pass hangs off initdb; `checkdb` is unaffected — `has_table` is hypertable-transparent);
  `src/hypershell/data/model.py` (R1: `previous_id`/`next_id` at lines ~295–296, plus the
  existing `Index` definitions at module bottom as the pattern to follow);
  `src/hypershell/core/uuid.py` (guard already correct); `docs/_include/database.rst` (+
  `docs/_include/*.rst` help snippets, `share/` completions); CI workflow (timescale matrix
  job, `timescale/timescaledb:latest-pg17` or similar service image). Existing indexes
  (`index_tasks_unscheduled` etc.) are created by `create_all` *before* conversion and
  propagate to chunks. The hand-run snippet's extra plain `schedule_time` index is *not*
  equivalent to the model's composite `(group, schedule_time)` — whether it earns a place in
  the model (all providers) is the plan's call; it is not part of the timescale pass.

## Related materials

- Dependency: soft — `turso` R4's per-provider consolidation in `data/core.py` is the
  intended seam for this provider's connect/init policy; whichever GOAL builds second conforms
  to the abstraction. R1 here is provider-independent and can land any time.
- Origin: `.local/timescale-ideas-transcript.txt` (2026-08 voice notes: the feature, the docs,
  the SC26 blog/demo ambitions — the latter two-thirds live in `../hypershell.org/`).
- Prior art: the `uuid7`-gating provider aliases and their guard (this repo, `data/core.py`);
  the `[database.pragmas]` connect-event precedent (per-connection state; instructive contrast
  to the one-time DDL pass); `timescaledb`-python (jmitchel3, 0.2.x 2026 — the maintained
  post-`create_all` two-phase pattern); `sqlalchemy-timescaledb` (dorosch, stale 2023 — the
  cautionary event-hook design); Django's `CreateExtension` pre-check pattern.
- Upstream: TimescaleDB `create_hypertable` / `by_range` reference (UUIDv7 example included),
  UUID functions reference, hypercore/columnstore `ALTER TABLE` + `add_columnstore_policy`
  reference, editions comparison (Apache vs Community feature table), unique-indexes-on-
  hypertables doc, `create_hypertable_old` (`migrate_data` caveats), self-hosted config
  (background workers) — all under `tigerdata.com/docs`; release notes at
  `github.com/timescale/timescaledb` (2.22.0 → 2.27.2 arc researched 2026-08-04).
- Deployment reality (as researched 2026-08-04): full feature set on self-hosted default images
  (`timescale/timescaledb`, `timescale/timescaledb-ha`) and Tiger Cloud; Apache-edition-only on
  `-oss` images, Azure Flexible Server, Crunchy Bridge (Supabase: deprecated, gone on PG17);
  extension entirely absent on RDS, Aurora, Cloud SQL, AlloyDB — the R5 tiers and the R10
  support matrix mirror this table.
- Follow-on (not graded here): `../hypershell.org/` blog "Scaling to One Billion Tasks" + the
  simulated REFITT-shaped data generator; SC26 (Chicago, November 2026) booth talk. The demo
  will exercise exactly the surface this cycle ships — treat its needs (bulk `--from-json`
  ingest into a hypertable, `hs list -c` latency at scale) as informal stress tests when
  validating R9.
