# GOAL — Open 2.8 databases in 2.9: an idempotent schema-upgrade step

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** schema-upgrade-step
- **kind:** fix
- **appetite:** small  ·  *One seam in the data layer (the schema setup that already runs at start)
  plus one schema subtraction carved in from the `timescale` seed. No migration framework, no data
  rewrite.*

## Problem

HyperShell 2.9 cannot open a database that HyperShell 2.8 created. The 2.9 line added columns to
the `task` table, and schema setup (`initdb`, which `ensuredb` runs on every SQLite start) is
`Entity.metadata.create_all(engine)` only: it creates tables that are missing but never alters a
table that exists. The `Task` model then selects columns the table does not have, and every
DB-backed command fails.

Reproduced 2026-11-01 against a clean SQLite site: `hs submit 'echo hello'` under hypershell 2.8.3,
then the same site under a build of current `develop` (2.9.0a1):

```
$ hs submit 'echo two'
CRITICAL [hypershell.submit] OperationalError: (sqlite3.OperationalError) no such column: task.source
$ hs list --all
CRITICAL [hypershell.task] OperationalError: (sqlite3.OperationalError) no such column: task.source
```

That failed run also left the database **half-upgraded**: `create_all` had already added the new
`source` table while `task` still lacked its new columns.

The schema delta from tag `2.8.3` to `develop` (verified with `git diff 2.8.3 origin/develop --
src/hypershell/data/model.py` and the 2.8.3-created SQLite schema):

| Table | 2.8.3 → develop |
|-------|-----------------|
| `task` | **+ `source`** (UUID, nullable) · **+ `fingerprint`** (TEXT, nullable) · **+ `part`** (INTEGER, NOT NULL, default `0` — declared as a Python-side default only, with no server default) · new index `index_tasks_source (source, fingerprint)`. No column removed, renamed or retyped. |
| `source` | **new table** (`id`, `path`, `fingerprint`, `task_count`, `created`) with `index_source_lookup`; `create_all` already creates it. |
| `client` | unchanged. |

2.8 rows also carry `{"part": 0}` inside their `tag` JSON. Since `part-tag-to-column`, the `part`
value lives in a column instead.

That cycle chose forward-only schema handling, with no add-column step, and older databases
"may require re-initialization". Re-initializing throws away task history, which is not acceptable
for an upgrade within one minor line. This cycle supersedes that non-goal (see Clarifications).

A second, related schema problem is carved into this cycle. The `timescale` seed's R1 found that
the `UNIQUE` constraints on `task.previous_id` and `task.next_id` block TimescaleDB hypertable
conversion: a hypertable needs every unique index to include its partition column, `id`. Dropping
them changes a documented invariant, the "UNIQUE chain" of the retry model. Today that invariant
turns a doubled retry of the same failed row into an `IntegrityError`, and once the constraints are
gone nothing does that. Its replacement has to be named.

## Outcome / vision

A user upgrades from 2.8 to 2.9 and keeps their database. The first 2.9 command that touches it
brings the schema current in place: missing columns, tables and indexes get added, and nothing is
dropped or rewritten except the retry-chain uniqueness that the model no longer declares. Every
later start finds nothing to do and says nothing. The same step is where the next 2.9 cycles
(`zone`, `relay`) add their columns, so "upgrade your database" never becomes a manual step.

New databases have a retry chain with no `UNIQUE` constraints, backed by plain indexes, on every
provider. Existing PostgreSQL databases lose the constraints during the upgrade. Existing SQLite
databases keep them, because SQLite cannot drop a table constraint without rebuilding the table;
that is documented and harmless. The retry-model invariant in `AGENTS.md` and
`.agents/factory/invariants.md` now says what actually guards against a doubled retry.

## Acceptance criteria (the contract)

- **R1** — WHEN any database-backed command (`hs submit`, `hs server` / `hs cluster` / `hsx`,
  `hs list` / `info` / `search` / `update` / `wait`, `hs initdb`) opens a SQLite **or PostgreSQL**
  database whose schema predates this build, it SHALL bring that schema current before its first
  query. That means creating every table, column and index the models declare that is missing, with
  no manual SQL and no re-initialization, and the command SHALL then succeed. The upgrade SHALL work
  for an untouched 2.8 database and for one left half-upgraded by an earlier 2.9.0a1 run (the
  `source` table present, `task` columns missing).
- **R2** — The upgrade step SHALL NOT drop, rename, retype or delete any table, column or row, and
  SHALL NOT rewrite existing values. The one removal it may make is the retry-chain constraint
  removal in R6. Every pre-existing row SHALL keep its values, its `tag` JSON as stored included.
  New columns SHALL read as NULL (`source`, `fingerprint`) or as their declared default (`part` =
  `0`) on pre-existing rows.
- **R3** — WHEN the schema is already current, the upgrade step SHALL change nothing and log
  nothing above DEBUG. A second start against an upgraded database SHALL be a no-op. WHEN it does
  change the schema, it SHALL log each column, index or constraint change at INFO, naming the table.
  IF two HyperShell processes run the step against the same database at the same time (e.g. a
  server and an `hs submit`), THEN neither SHALL fail because of the other.
- **R4** — The upgrade step SHALL take what to add from the model declarations, so a later change
  that declares a new nullable or defaulted column is upgraded with no per-column upgrade code.
  IF a missing column cannot be added safely in place (NOT NULL with no default the database can
  apply to existing rows), THEN the command SHALL exit with an actionable error that names the table
  and column, not a raw traceback, and SHALL leave the schema unchanged.
- **R5** — WHEN a database is newly created (`create_all` on an empty database, or `hs initdb
  --truncate`), on SQLite and PostgreSQL, `task.previous_id` and `task.next_id` SHALL carry **no**
  `UNIQUE` constraint, and each SHALL have a plain non-unique index. The rationale (hypertable
  compatibility; one schema for every provider) SHALL be recorded in the model. *(Discharges
  `timescale` seed R1.)*
- **R6** — WHEN the upgrade step runs against an existing **PostgreSQL** database that still has the
  `UNIQUE` constraints on `task.previous_id` / `task.next_id`, it SHALL drop them and create the
  plain indexes of R5, idempotently (R3).
- **R7** — WHEN the upgrade step runs against an existing **SQLite** database that still has those
  constraints, it SHALL leave them in place: forward-only, with no table rebuild. Retries SHALL work
  on such a database exactly as on a fresh one. The database documentation SHALL say that 2.9
  upgrades a 2.8 database in place on first use, and that pre-2.9 SQLite databases keep the
  constraints, with no effect on behaviour.
- **R8** — The retry-model invariant SHALL be amended in the same change, in both `AGENTS.md`
  ("Task lifecycle contract" → *Retry model*, and the `data/model.py` entry under "High-risk files &
  footguns") and `.agents/factory/invariants.md` §3. Each SHALL drop "UNIQUE chain", SHALL state what
  now prevents a doubled retry of one failed row (the plan identifies the mechanism and verifies it
  against the source), and SHALL note the SQLite forward-only exception of R7. IF the plan finds a
  configuration with no guard (e.g. more than one server against one database), THEN the invariant
  SHALL say so plainly instead of implying one.
- **R9** — A database **created by hypershell 2.8.3**, holding completed, failed-and-retried and
  still-pending tasks, SHALL be opened by this build on SQLite, and on PostgreSQL where testable,
  and SHALL work end to end:
    - `hs submit` adds tasks.
    - A server/client run (e.g. `hsx`) completes the pending tasks, including a retry of a failure.
    - `hs list` shows old and new rows.

  Pre-existing rows SHALL be intact (R2). Automated tests SHALL cover R1–R7 on SQLite, and on
  PostgreSQL by whatever means the plan settles (see Clarifications). The existing suite SHALL stay
  green and the docs build clean.

## Non-goals (no-gos)

- **A general migration framework** (Alembic or similar), version tables, or migration scripts.
  This is one idempotent "add what is missing" step.
- **Down-migrations**, including any promise that 2.8 keeps working against a database that 2.9 has
  upgraded.
- **Rewriting existing rows.** This includes stripping the legacy `part` key from 2.8 `tag` JSON
  and backfilling `fingerprint` / `source` for historical rows.
- **Backfilling `part` from tag JSON** for rows in pre-`part` rotated partitions; they read as
  `part = 0`.
- **Rebuilding SQLite tables** to drop the chain `UNIQUE` constraints (R7 is forward-only on purpose).
- **Hypertables or anything else from `timescale` R2+.** Only that seed's R1 is carved in here.
- **Changing 2.8 behaviour** for users who stay on 2.8; nothing is backported.

## Clarifications

- Database compatibility (Geoffrey, alignment Q5a): idempotent add-column step on SQLite and
  PostgreSQL, ships in 2.9.0a2. (2026-11-01)
- UNIQUE-chain invariant amendment approved (Geoffrey, alignment Q10a); timescale seed R1 carved into
  this cycle; forward-only for existing SQLite. (2026-11-01)
- **Q:** `part-tag-to-column` recorded "no add-column-on-connect … older databases may require
  re-initialization" as a non-goal. Does that still hold? — **A:** No. The Q5a decision above
  supersedes it for this cycle; that GOAL stays as its point-in-time record. (resolved 2026-11-01)
- **Note (for `hs-plan`) — PostgreSQL testing is an open question to settle in research.** CI has no
  PostgreSQL job today (`.github/workflows/tests.yml` runs SQLite only). Choose between adding a
  PostgreSQL service-container job, which `timescale` R9 will want anyway as a TimescaleDB matrix
  entry and could reuse, and testing PostgreSQL another way. Record the decision in `PLAN.md` and
  the coverage it buys for R1–R6 and R9.
- **Note (for `hs-plan`) — where the step runs.** On SQLite, `ensuredb` runs `initdb` on every
  start. On PostgreSQL it runs only `checkdb` (a `has_table` check) unless `--initdb` is given, so
  R1's "on first use" needs the upgrade step reachable from that path too. `data/core.py` builds the
  engine at import time (see `AGENTS.md`, "Data layer specifics"). Choose the seam with both facts in
  view.
- **Note (for `hs-plan`) — `part` has no server default.** `mapped_column(INTEGER, nullable=False,
  default=0)` is applied by SQLAlchemy at insert time only. Adding it to a table that has rows needs
  a default the database itself applies (SQLite rejects `ADD COLUMN … NOT NULL` without one;
  PostgreSQL would fail on existing rows). R4's "defaulted" means a default the database can apply.
- **Note (for `hs-plan`) — fixture provenance.** R9 needs a database *created by 2.8.3*, not one
  hand-built to look like it. Generate one by installing `hypershell==2.8.3` in a throwaway
  environment, or check in a small generated fixture with a note on how it was produced. The plan
  chooses, keeping in mind that `hs-review` re-drives this flow.

## Related materials

- Seed (partial): `issues/timescale.md`, **R1 is discharged by this cycle** (R5–R8 here). The seed
  itself is not edited now; `/hs-roadmap` updates it (drops R1, adjusts its "one schema subtraction"
  appetite note) once this cycle lands on `develop`.
- Superseded non-goal: `spec/part-tag-to-column/GOAL.md` (R6 and Non-goals: forward-only `part`
  column).
- Source: `src/hypershell/data/__init__.py` (`initdb`, `ensuredb`, `checkdb`, `truncatedb`),
  `src/hypershell/data/model.py` (`Task.previous_id` / `next_id`, the module-level `Index`
  definitions, `__schedule_next_failed_tasks`), `src/hypershell/data/core.py` (import-time engine).
  This is the highest-blast-radius area of the codebase; read `AGENTS.md` "Task lifecycle contract"
  and "Data layer specifics" first.
- Invariants to amend: `AGENTS.md` (*Retry model*; "High-risk files & footguns"),
  `.agents/factory/invariants.md` §3.
- Docs: `docs/database.rst`.
- Later users of the same step: `issues/zone.md`, `issues/relay.md` (2.9.0 cycle; both add columns).
