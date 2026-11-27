# 00 — Digest: decisions from the TS1 research pass

Briefs: [01 SQL surface and versions](01-timescale-sql-and-versions.md) (web, 2.31.0 source and docs),
[02 CI image and DDL](02-ci-image-and-ddl.md) (web, image repo and source),
[03 seam and tests](03-seam-and-tests.md) (codebase). Researched 2026-11-02.

## Version facts (rechecked; supersede the GOAL's 2026-08-04 figures)

- **Current release: TimescaleDB 2.31.0 (2026-10-20)**, supporting PostgreSQL 16–18; 2.29.0 dropped
  PG15 (PG15 users top out at 2.28.x). Nothing released after 2.27.2 changes UUID partitioning or the
  API this pass uses (01 §1).
- Floors hold: UUIDv7 partitioning and the `uuidv7` helpers 2.22.0; UUID compression on by default
  2.23.0; `time_bucket`/caggs on UUIDv7 2.24.0; **compression-policy fix #9045 in 2.25.0**. Before
  2.25.0 `add_columnstore_policy` succeeds but every job run on a UUID hypertable fails (the job's
  dimension-type `CASE` has no UUID branch; the 2.24.0→2.25.0 diff adds exactly that) (01 §1).
- Edition: `current_setting('timescaledb.license', true)` → `timescale` | `apache`. It cannot be
  changed per session or per database ("Cannot change a license in a running session"), so the Apache
  tier needs its own server: the `-oss` images are real Apache-only builds (02 §3).
- `trusted = true` from 2.22.0 through 2.31.0; un-preloaded `CREATE EXTENSION` is FATAL (01 §8).

## Decisions (one each)

1. **Vocabulary: columnstore only.** `ALTER TABLE … SET (timescaledb.enable_columnstore = true,
   timescaledb.segmentby = …, timescaledb.orderby = …)`, `CALL add_columnstore_policy(…, after =>
   INTERVAL …, if_not_exists => true)`, `CALL remove_columnstore_policy(…, if_exists => true)`,
   `timescaledb_information.hypertable_columnstore_settings`. Present 2.18 → 2.31; the `compress*`
   aliases are deprecated, not removed. Code and docs use the same vocabulary (R3).
2. **`create_hypertable` generic form, never `migrate_data`:**
   `create_hypertable(<task>, by_range('id', INTERVAL '1 week'), create_default_indexes => false,
   if_not_exists => true)`. Brief 01 suggested `migrate_data => true`; **rejected** — R5 forbids
   silent migration. The non-empty case is detected first, and a racing insert surfaces as SQLSTATE
   0A000 `table "task" is not empty`, which the pass maps to the same warning.
   `create_default_indexes => false` keeps the index set exactly the model's (01 §2 left it
   unverified whether `true` adds `(id DESC)`; `false` removes the question).
3. **Version gates (R7):** below 2.22.0 → hard fail. 2.22.0–2.24.x → hypertable + columnstore, policy
   refused with a warning naming 2.25.0 (#9045). ≥2.25.0 → everything. Evidence is the source diff
   plus the release note; a CI matrix entry on the last 2.24.x image exercises the gate (see PLAN
   [NEEDS CLARIFICATION] 1).
4. **Probe before the schema step, converge after it.** Extension absent, too old, not preloaded, or
   not creatable → fail *before* `upgradedb()` so no plain tables are left behind. Conversion runs
   after `upgradedb()` because the retired `UNIQUE` constraints must be gone first (02 §4).
5. **Drift is compared in SQL** (`CAST(:a AS interval) = CAST(:b AS interval)`, so `4 weeks` equals
   `28 days`). The chunk interval reads `COALESCE(time_interval, integer_interval * INTERVAL '1
   microsecond')`, because 2.22–2.24 report a UUID dimension in `integer_interval` (01 §4b). Policy
   drift is remove + add: `if_not_exists` with different arguments only WARNs (01 §5).
6. **CI:** extend the existing `postgres` job's matrix (the placeholder line), not a new job. Pin
   `timescale/timescaledb:2.31.0-pg18@sha256:0fcff576…d579` (index digest, Docker Hub API) and the
   `-oss` twin `2.31.0-pg18-oss@sha256:cc00b0ed…2ff3`. Set `TIMESCALEDB_TELEMETRY=off`; keep
   `timescaledb-tune` on (it sizes `max_worker_processes`) (02 §1–2, §6).
7. **Fixtures:** the image creates the extension in `template1`, so every test database has it; the
   "installed but not created" case uses `CREATE DATABASE … TEMPLATE template0`, and the "absent" tier
   runs on the plain `postgres:13`/`postgres:18` entries (02 §2).
8. **Schema step on a hypertable is safe as the invariant already states it** (nullable or
   constant-default columns, non-unique `CREATE INDEX IF NOT EXISTS`); never `CONCURRENTLY` or
   `transaction_per_chunk` inside the step (02 §4). No `upgrade.py` change. One unverified edge: a
   literal default cast to a domain type passes `upgrade.py`'s check but is not constant-folded by
   TimescaleDB — no model column does this today; recorded as a risk.
9. **Errors:** `TimescaleError(SchemaUpgradeError)` in `data/timescale.py` reaches every app's shared
   mapping (cmdkit dispatches by `isinstance`) → critical line, `runtime_error`, no traceback, and no
   `core/` edit (03).
10. **`DROP TABLE` cascades policy jobs** (`bgw_job` FK `ON DELETE CASCADE`), so `truncatedb`'s
    drop/create cycle needs no job cleanup; the pass re-adds the policy (02 §7). Database-wide
    `VACUUM` covers chunks with no columnstore-specific errors (02 §5).

## Not rechecked

The managed-offering support matrix (which clouds ship which edition) is the seed's 2026-08-04
research. R10's docs state it "as of" a date; the build should spot-check the editions page before
writing the matrix.
