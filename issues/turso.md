---
status: shaped
kind: feature
appetite: big
lane: public
shaped-as: turso
---

# Turso provider: SQLite's contracts with room to grow

> **Shaped candidate — not yet a contract.** The shaping conversation already happened with a
> human: the clarifications below are dated and resolved, the appetite and non-goals are agreed,
> and the R-IDs are drafted. What has *not* happened is acceptance into a cycle — `/hs-feature`
> does that, creating the branch and `spec/{slug}/GOAL.md`, and it is `hs-review` that grades the
> result. Until then nothing here is graded and nothing here is locked.
>
> Promotion should re-confirm the scope against current `develop` and cite anything that has
> drifted since this was written, then adopt it largely as written — do **not** re-litigate the
> resolved clarifications.

- **slug:** turso
- **kind:** feature
- **appetite:** big  ·  *Smaller than originally feared — the dialect we expected to build
  **already shipped upstream** (`pyturso[sqlalchemy]`, official, since Turso 0.5.0 / March 2026),
  so the center of gravity moves from plugin development to **adoption + validation + the two
  capabilities the provider unlocks**: partition-attached listing at 1000+ rotations, and
  S3-backed captured output. The provider phase (R1–R8) — the swap itself plus the
  `data/core.py` provider-handling cleanup it forces — is independently shippable first;
  attached listing (R9–R12) and S3 output capture (R13–R15) are separable follow-on phases.
  This closes the 2.9.0 release cycle after `zone` and `relay`.*

## Problem

HyperShell's founding simplicity is a single SQLite file — no service to run, no credentials to
mint. But that simplicity taxes exactly the workflows 2.9.0 is opening up:

1. **Rotation partitions are write-only archives.** `hs initdb --rotate` splits completed tasks
   into `main.1`, `main.2`, … — and then `hs list`/`hs search` can no longer see them. SQLite's
   `ATTACH` cap (10 default, 125 compiled max) forecloses ever querying a long campaign's history
   as one thing; a year of weekly rotations is unreachable without ad-hoc scripts.
2. **Concurrency is adequate, not graceful.** The single-writer model plus polite WAL settings is
   fine for the server's writeback loop, but every off-process reader (`hs list` against a live
   campaign database) rides on hope and busy-timeouts, and the engine has no growth path —
   SQLite's answer to contention will always be "retry."
3. **Captured output is marooned on the execution host.** `--capture` / `-o`/`-e` write task
   stdout/stderr to client-local files; `hs task info --stdout` retrieves them over paramiko SFTP
   only if you can SSH to that node. On ephemeral or scratch-purged nodes, output dies with the
   allocation.

Turso (turso.tech) — the Rust rewrite of SQLite, **not** the older libSQL fork — now holds
SQLite's contracts from our side of the code: same file format, same SQL, same DBAPI shape,
`sqlite_version` and all. v0.7.0 (2026-07-13) dropped the beta warning; the official Python
binding (`pyturso`) ships a first-party SQLAlchemy 2.0 dialect registered at exactly the URL our
provider map already emits (`sqlite+turso://` — `data/core.py` was seeded for this). Its
`DatabaseCatalog` removes the compile-time `ATTACH` cap outright, and the engine's trajectory
(MVCC concurrent writes, io_uring, encryption, CDC) is the growth path SQLite will never have.
An empirical spike (2026-08-04, pyturso 0.7.2) confirmed: our schema, session patterns, pragmas,
`VACUUM INTO`, and a **1000-database ATTACH with UNION ALL across all of them** work today —
behind experimental feature flags, with a short list of real integration gaps (below).

What this cycle is *not* solving: Turso does not replace PostgreSQL's off-server story —
the OSS engine is an embedded local-file library, and its S3-backed "diskless" architecture is
Turso *Cloud* infrastructure (private beta), not a feature we can ship. We take what is real
today and keep a watch note on the rest.

## Outcome / vision

A user installs **`hypershell[uuid7,turso,s3]`** and sets `database.provider = turso` — nothing
else changes. The same database file, the same automatic init, the same `submit`/`list`/`update`;
the engine underneath is Turso via the **official** `pyturso[sqlalchemy]` dialect, selected by the
provider map that already anticipates it. Our fork-based `sqlalchemy-turso` plan is retired: gaps
we find are filed and fixed **upstream** (the spike found two immediately: `turso.connect()`
rejects pysqlite's `check_same_thread`, and the 0.7.2 wheel stubs index/FK reflection), with thin
shims in `data/core.py` only until upstream releases land.

On that provider, rotation stops being an archive graveyard: `hs list`/`hs search`/`hs info` gain
a flag (say `--parts`) that discovers the rotation family (`main.db`, `main.1`, `main.2`, …),
`ATTACH`es the partitions read-only on one connection, and answers across the whole campaign
history — **1000+ rotations** is the design point, gated by an early benchmark phase against
real-shaped partition files (the spike attached 1000 synthetic partitions in <1s and swept a
UNION ALL over all of them in 0.17s). The `part` column already stamped on every rotated task is
the provenance thread; `uuid7` time-ordered ids keep cross-partition ordering cheap.

Independently of provider, the **`[s3]` extra** un-maroons captured output: `outpath` /
`errpath` / `csvpath` accept `s3://bucket/key` URIs; the client streams captured stdout/stderr to
object storage instead of the node-local filesystem, and `hs task info --stdout` fetches it back
from anywhere with credentials — no SSH to a dead node. This is HyperShell-side plumbing
(boto3), deliberately provider-independent, and it quietly serves `relay` too: cross-site output
retrieval was a relay non-goal precisely because paths named site-local files.

Getting there forces a cleanup `data/core.py` has been owed anyway: the sqlite/turso handling
today is accreted conditionals — `check_same_thread` injected for both providers (fatal to the
official Turso driver), pragma injection and `in_memory` detection keyed on ad-hoc membership
tests, optional-dependency guards pasted per provider. This cycle leaves that module with an
explicit per-provider seam — each driver receives exactly the connect arguments it accepts, each
provider declares its own guards and defaults — so SQLite and Turso are both first-class and the
next provider is an entry, not another scattered `if`. The `pyproject.toml` TODO placeholders
(`turso` extra, dev-group pins, the `[tool.uv.sources]` stanza) are resolved by this feature —
none survive it.

Crucially, **the default SQLite path is untouched**, and provider switching is honest both ways:
Turso writes the SQLite file format, so `sqlite` ↔ `turso` on the same file is a config edit,
not a migration. PostgreSQL remains the recommendation for large multi-user deployments.

## Acceptance criteria (the contract)

**Provider adoption** *(first phase; independently shippable)*

- **R1** — The `turso` optional extra SHALL install `pyturso[sqlalchemy]` (the official dialect;
  floor at the first release validated by the plan, ≥0.7.2) and the existing `turso` provider
  SHALL select it through the already-present `sqlite+turso` mapping; the retired plan to
  develop/depend on a first-party `sqlalchemy-turso` package SHALL be abandoned, with the fork
  archived and a closing pointer left on `tursodatabase/sqlalchemy-libsql#11`.
- **R2** — IF `provider = turso` and `pyturso` is missing, THEN startup SHALL fail with the same
  actionable guidance pattern as the `postgres`/`uuid7` guards (name the extra, exit
  `runtime_error`), not a raw traceback.
- **R3** — Provider-specific connect handling SHALL be correct for the official dialect:
  `check_same_thread` SHALL NOT be passed to `turso.connect()` (it is a `TypeError` today), and
  engine features this cycle relies on (`attach`, `vacuum`) SHALL be enabled automatically via the
  dialect's `experimental_features` connect argument when and only when the operation requires
  them — a user SHALL NOT need to know Turso's feature-flag vocabulary.
- **R4** — The provider handling in `data/core.py` SHALL be consolidated into an explicit
  per-provider abstraction covering connect-argument policy (each driver receives only the
  arguments its DBAPI accepts — today `check_same_thread=False` is injected for *both* sqlite
  and turso, a `TypeError` against the official driver), pragma injection, `in_memory`
  detection, and the optional-dependency guards — replacing the accreted
  `provider in ['sqlite', 'turso']` membership tests so that SQLite and Turso are each
  first-class and a future provider is added by declaring an entry, not by threading new
  conditionals through the module. Behavior under `provider = sqlite` SHALL be bit-for-bit
  unchanged.
- **R5** — The `pyproject.toml` turso TODO placeholders (the commented `turso` optional
  dependency, the commented dev-group entries, and the commented `[tool.uv.sources]` stanza)
  SHALL be resolved by this feature — replaced by the real `turso` extra (per R1) and dev-group
  pins — such that no turso-related TODO comment remains in the file.
- **R6** — The full test suite SHALL run green with `provider = turso` as a CI matrix job
  alongside sqlite/postgres, covering submit, scheduling, writeback, retries, revert, group
  gating, `hs list`/`search`/`update`/`info`/`wait`, config pragmas, `vacuumdb`, and `rotatedb`;
  any behavioral exception SHALL be explicitly enumerated in docs rather than silently skipped.
  In-memory semantics (`:memory:` → `in_memory` mode) SHALL match the sqlite provider.
- **R7** — `[database.pragmas]` SHALL work under turso, and the docs SHALL state the supported
  vocabulary honestly — including that `synchronous` supports only OFF/FULL (the spike showed
  `NORMAL` is *accepted*; the plan SHALL determine whether it is honored or coerced, and document
  accordingly) and which of our conventionally recommended pragmas apply.
- **R8** — `hs initdb --rotate` and `--vacuum` SHALL work under turso: `VACUUM INTO` for the
  partition clone (works unflagged today) and in-place `VACUUM` via the experimental flag per R3,
  WITH a fallback strategy in the plan if in-place `VACUUM` proves unreliable (it is explicitly
  experimental engine-side).

**Partition-attached listing** *(rides on R1–R8)*

- **R9** — WHEN the provider supports uncapped `ATTACH` (turso), the read-only presentation
  commands (`hs list`, `hs search`, and `hs info` by id) SHALL be able to answer across the
  rotation family — main database plus rotated partition files discovered by the established
  `main.N` naming — via `ATTACH`, behind an explicit opt-in flag; results SHALL present
  identically to today (including `Source`/`Zone` name dereferencing) with the originating
  partition available as a column/filter (`part` is already stamped on every rotated row).
- **R10** — The scheduler's hot path SHALL be unaffected: partition attachment is a
  presentation-side, read-only concern; the server never attaches partitions and write behavior
  never depends on R9.
- **R11** — An early benchmark phase SHALL validate the design point with 1000+ real-shaped
  partition files (row counts and file sizes representative of actual rotations, not the spike's
  toy copies) measuring attach cost, query latency, memory, and file-descriptor budget; the
  supported bound SHALL be set (and documented) from measurement — 1000+ is the target, not a
  hope. IF the engine falls over below target, THEN the bound is re-scoped, not silently shipped.
- **R12** — Under `provider = sqlite`, the same flag SHALL degrade gracefully within SQLite's
  `ATTACH` budget (batching or refusing with a clear message naming the turso provider — plan's
  choice), never corrupting presentation.

**S3-backed captured output** *(provider-independent; separable phase)*

- **R13** — The `s3` optional extra SHALL enable `s3://bucket/key`-form URIs for `outpath`,
  `errpath`, and `csvpath`: WHEN a capture path is an `s3://` URI, the client SHALL deliver the
  captured stream to object storage (credentials/endpoint via the standard AWS environment and
  config chain, so S3-compatible stores — MinIO, Ceph RGW, institutional object stores — work
  unchanged) and the task row SHALL record the URI exactly as it records file paths today.
- **R14** — `hs task info --stdout`/`--stderr` SHALL retrieve `s3://` outputs directly from
  object storage from any host with credentials, bypassing the SFTP host-path retrieval; the
  existing SFTP path remains for file paths.
- **R15** — IF the `s3` extra is missing and an `s3://` capture or retrieval is requested, THEN
  the failure SHALL follow the R2 guidance pattern. S3 capture SHALL work identically under
  sqlite, turso, and postgres providers, and template expansion in capture paths SHALL work for
  URIs as it does for file paths.

**Convention**

- **R16** — CLI/config changes SHALL update the affected `docs/_include/*.rst` help snippets and
  `share/` completions in the same commit (repo convention); the database docs SHALL gain a Turso
  provider section (selection, pragmas, rotation-at-scale, honest maturity framing pre-1.0) and a
  short watch note distinguishing the OSS engine from Turso Cloud (diskless/S3, pg-wire frontend)
  so the S3-database question is answered *in the docs* rather than re-litigated in issues.

## Non-goals (no-gos)

- **Developing or maintaining an independent `sqlalchemy-turso` dialect.** The official dialect
  ships inside `pyturso`; we adopt it and contribute fixes upstream. The `../sqlalchemy-turso`
  fork is archived. (Resolved 2026-08-04.)
- **Database-on-S3.** The OSS Turso engine has **no** object-storage backing; the "diskless"
  S3 architecture is Turso Cloud infrastructure (private beta), and "bottomless" S3 WAL
  replication belongs to the old libSQL server. `[s3]` here means captured *output* on S3, full
  stop. Watch note in docs; revisit if the engine ever ships it. (Resolved 2026-08-04.)
- **Turso Cloud / remote sync — deferred, with a sanctioned future shape.** The
  `sqlite+turso_sync://` driver and Turso Cloud accounts are out of scope for 2.9.0; no
  managed-service dependency enters HyperShell. But this is the germ of the off-server story
  that motivated this whole line of work (a laptop's `hs list` against a synced snapshot beats
  exposing PostgreSQL), so the shape is decided now: **a provider alias, not an install
  variant** — `'turso-cloud': 'sqlite+turso_sync'` in the `providers` map (the `timescale`
  precedent), riding the same `[turso]` extra and picking up its own connect policy from R4's
  abstraction; likely scoped read-only (presentation commands) first. *Wait-on conditions:*
  next-gen Turso Cloud out of private beta; async remote sync implemented in the dialect (an
  explicit non-goal upstream today); and resolved conflict semantics for pushing writes into a
  database the scheduler is actively mutating — HyperShell's task-state transitions assume a
  single-owner transactional writer, and pull/push sync does not yet answer that.
- **MVCC / `BEGIN CONCURRENT` — watch-triggered, never an install variant.** Experimental
  engine-side and structurally unusable for us today: databases with indexes cannot be opened
  in MVCC mode at all, and the scheduler's hot path (`Task.next` select-by-state/group/zone)
  depends on indexed access — dropping indexes would trade write concurrency for full-table
  scans on every bundle load, a near-certain net loss at campaign scale. The manual further
  lists eager whole-DB load into memory, checkpointing that blocks all readers and writers,
  "queries may return incorrect results," and possible panics; and an MVCC-written file is not
  visible to a non-MVCC open without checkpointing first, which would break this cycle's
  `sqlite` ↔ `turso` switching-honesty promise. We keep the single-writer WAL model for 2.9.0;
  concurrent-writes is the headline *future* benefit, not a shipped claim. No
  `PRAGMA journal_mode = mvcc` anywhere. *Watch trigger:* revisit when `BEGIN CONCURRENT`
  supports indexed schemas — at which point it should need no variant at all, just a pragma
  through the existing `[database.pragmas]` path (plus the R3 feature-flag handling).
- **Encryption-at-rest, CDC, FTS, and other experimental Turso subsystems.** Not configured, not
  documented as supported.
- **Replacing SQLite as the default provider, or deprecating PostgreSQL.** SQLite stays the
  zero-config default; PostgreSQL stays the big-deployment recommendation. Turso is an opt-in
  third lane.
- **Off-server `submit`/`list`/`update` via Turso.** An embedded engine doesn't change the
  network story; Turso's pg-wire frontend is experimental and untouched here. Off-server needs
  remain PostgreSQL (or the `relay` topology).
- **Output-file lifecycle management.** We write and fetch `s3://` objects; retention, versioning,
  and cleanup are the bucket's policy, not ours.

## Clarifications

- **Q:** Build the dialect or adopt upstream's? — **A:** **Adopt official, contribute gaps**
  (resolved 2026-08-04). `pyturso[sqlalchemy]` registers `sqlite.turso`/`sqlite.aioturso`/
  `sqlite.turso_sync` entry points (verified in the 0.7.2 wheel), subclassing
  `SQLiteDialect_pysqlite` exactly as our fork did. Known upstream gaps to file/PR: the
  `check_same_thread` `TypeError` (drop-in compat), index/FK reflection stubbed in the shipped
  0.7.2 wheel (already restored on main), and the PyPI classifier still "3 - Alpha" (bumped to
  Beta on main). Close `tursodatabase/sqlalchemy-libsql#11` (our Sept-2025 issue — never
  answered, but the shipped dialect resolves exactly the obstacles it lists) with a pointer.
- **Q:** What does `[s3]` mean, given the engine has no S3 backing? — **A:** **Captured output
  only** (resolved 2026-08-04): `s3://` URIs for `outpath`/`errpath`/`csvpath`, implemented
  HyperShell-side with boto3, provider-independent. Database-on-S3 is a non-goal with a watch
  note.
- **Q:** Ship `turso` / `turso-cloud` / `turso-mvcc` as mutually exclusive install variants? —
  **A:** **No — one extra, capability by provider** (resolved 2026-08-04). Python extras cannot
  be made mutually exclusive (pip installs the union of whatever is named), and all three
  variants would install the *identical* dependency — `pyturso[sqlalchemy]` already contains
  the sync driver and the MVCC engine, so there is no dependency delta to hang a variant on;
  an extra that changes no packages but promises different behavior is configuration wearing a
  costume (contrast the `postgres`/`postgres-system`/`postgres-c` trio, which legitimately
  differ in how the dependency is *built*). Capability selection belongs to `provider =` in
  config: the `providers` map is the seam, `turso-cloud` becomes a one-line alias there when
  its wait-on conditions clear (see Non-goals), and MVCC — if it ever becomes viable — is a
  pragma, not an install identity. The single `[turso]` extra stands.
  gate** (resolved 2026-08-04): acceptance includes cross-partition listing; an early benchmark
  phase against real-shaped files sets the documented bound and may re-scope the number — the
  spike's 1000× synthetic attach (<1s, UNION ALL 0.17s) justifies the target but does not prove
  it at realistic row counts.
- **Note (for `hs-plan`):** *empirical spike results (2026-08-04, pyturso 0.7.2, this repo's
  session patterns).* Working out of the box: `create_all` on our schema, `has_table`,
  ORM insert/commit/query, named binds through `text()`, `PRAGMA journal_mode=wal`,
  `PRAGMA optimize`, `VACUUM INTO`, explicit `CREATE INDEX`, and
  `connect_args={'experimental_features': 'attach,vacuum'}` passing cleanly through the official
  dialect. Failing: `connect_args={'check_same_thread': False}` → `TypeError` (note
  `data/core.py` *injects this today for the turso provider* — the concrete bug R4's
  per-provider connect-argument policy exists to make structurally impossible);
  plain `VACUUM` and `ATTACH` error without their feature flags. `ATTACH` ×1000 on one
  connection: 0.84s, sweep queries 0.35s, UNION ALL across all 1000 in 0.17s.
- **Note (for `hs-plan`):** *thread-affinity is the open risk.* pyturso declares
  `threadsafety = 1` (module yes, connections no) and offers no `check_same_thread` escape hatch;
  our engine defaults to `QueuePool`, whose connections migrate across threads, and the spike's
  cross-thread reuse smoke passed but proves nothing. Determine and enforce a pool policy for
  turso (per-thread affinity, `NullPool`, or upstream clarification of the Rust core's actual
  guarantee) before calling R6 done — this is the load-bearing correctness question of the
  provider phase, and the chosen policy belongs in R4's per-provider abstraction.
- **Note (for `hs-plan`):** *compatibility posture is clean.* HyperShell uses no window
  functions, no recursive CTEs, no `create_function` (the dialect skips pysqlite's REGEXP hook),
  and no custom collations — Turso's documented gaps (COMPAT.md) miss us entirely. `qmark` is the
  dialect's paramstyle, which SQLAlchemy's sqlite dialect already renders. The engine reports
  `sqlite_version` 3.45.0 for compatibility checks.
- **Note (for `hs-plan`):** *rotation family discovery already exists* — `next_rotate_path()`
  enumerates `main.N`; R9's discovery is its read-side mirror. Attached-alias hygiene (schema
  names can't collide with `main`), read-only attachment if the engine grows an option for it,
  and per-file error tolerance (a corrupt partition should skip-and-warn, not kill the listing)
  are the plan's details. Consider whether `hs info <id> --parts` walks partitions newest-first
  (uuid7 makes id→time ordering trivial).
- **Note (for `hs-plan`):** *S3 touchpoints.* Capture happens in `client.py` (`TaskExecutor`
  redirection) — today it opens local files; the S3 path likely wants stream-to-part-upload on
  completion rather than buffering. Retrieval is `core/remote.py` (paramiko SFTP) branched by
  URI scheme in `task.py` (`hs task info`). Template expansion (`core/template.py`) must expand
  into URIs safely. Keep boto3 imports inside the `[s3]` guard.
- **Note (for `hs-plan`):** *version floors.* The dialect's in-repo doc says SQLAlchemy ≥2.0.45
  on main; we already require ≥2.0.50. pyturso wheels are cp310-abi3 (Linux/macOS/Windows) —
  compatible with our 3.11–3.14 support. Track pyturso's cadence (0.5.0 dialect → 0.6.0 asyncio
  → 0.7.x hardening; versions track engine releases) and pin the floor at what R4 validates.

## Related materials

- Dependency: none hard — sequenced after [`zone.md`](zone.md) and
  [`relay.md`](relay.md) as the closing phase of 2.9.0. Synergy: R13–R15 (S3 output)
  soften relay's "cross-site captured-output retrieval" non-goal — an `s3://` outpath is
  site-independent by construction.
- Source (likely touchpoints, to be confirmed by `hs-plan`): `src/hypershell/data/core.py`
  (provider map already emits `sqlite+turso`; swap the `sqlalchemy_turso` import guard for
  `pyturso`; the R4 per-provider consolidation — connect-argument policy incl. the injected
  `check_same_thread`, `experimental_features` handling, pragma event, `in_memory` detection,
  dependency guards); `src/hypershell/data/__init__.py` (`rotatedb`/`vacuumdb` under turso;
  partition discovery for R9); `src/hypershell/task.py` (`hs list`/`search`/`info` `--parts`
  flag; `s3://` retrieval branch); `src/hypershell/client.py` (`TaskExecutor` capture-to-S3);
  `src/hypershell/core/remote.py` (retrieval seam); `pyproject.toml` (extras: `turso`, `s3`;
  resolve all turso TODO placeholders per R5); CI workflow (turso matrix job);
  `docs/database.rst` (+ `docs/_include/*.rst`, `share/` completions).
- Upstream: `pyturso` (PyPI, 0.7.2) — official dialects `sqlite+turso://`/`sqlite+aioturso://`/
  `sqlite+turso_sync://` via the `sqlalchemy` extra; `tursodatabase/turso` `COMPAT.md` and
  `docs/manual.md` (feature flags, MVCC limitations, pragma vocabulary);
  `bindings/python/SQLALCHEMY_DIALECT.md` ("Status: Implemented"); our issue
  `tursodatabase/sqlalchemy-libsql#11` (to close); the archived-to-be fork `../sqlalchemy-turso`
  (its `check_same_thread` pop and isolation-level overrides are the prior art for what upstream
  now handles).
- Engine status (as researched 2026-08-04): Turso v0.7.0 (2026-07-13) dropped the beta warning;
  pre-1.0 with experimental subsystems (MVCC, encryption, views, multiprocess WAL); `ATTACH`
  uncapped by design (`DatabaseCatalog`, "supports unlimited attached databases") but publicly
  untested at scale — hence R11. Turso Cloud's diskless S3 architecture and the experimental
  pg-wire frontend are the watch items for a future off-server story.
- Prior art / motivation: TimescaleDB's provider-gated `uuid7` extra (the pattern R1/R2 mirror);
  the `part` column and `rotatedb` machinery (`spec/` history); fsspec/boto3 conventions for
  `s3://` capture paths in adjacent tools (Snakemake remote providers, Nextflow S3 work dirs) —
  the well-trodden shape of "paths that are URIs."
