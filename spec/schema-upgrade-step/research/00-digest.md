# 00 — Research digest

Six briefs, one per open question. The GOAL handed four questions to `/hs-plan`, and the change
touches `data/model.py`, so the full fan-out ran despite `appetite: small`. Each decision below
resolves its brief. Where briefs disagreed, this file picks one answer.

| # | Brief | Decision |
|---|-------|----------|
| 01 | [PostgreSQL testing](01-postgres-testing.md) | Add a separate `postgres` job to `tests.yml`, using a service container. The matrix runs `postgres:13` (floor) and `postgres:18` (current) with `uv run --extra postgres`. The gate is `HS_TEST_POSTGRES`, an admin URL; it can't use a `HYPERSHELL_` prefix because the conftest scrubs those. Each test gets its own `CREATE DATABASE … TEMPLATE`. A CI step drives real 2.8.3 to build template `hs283`, and real 2.9.0a1 to build the half-upgraded `hs290a1`. A later `timescale` matrix row reuses the job. |
| 02 | [Where the upgrade runs](02-upgrade-seam.md) | One function, `upgradedb()`, called from `initdb()` and from `checkdb()` (once `task` exists). It runs on its own connection at the command seam, never at import. Two holes need closing: `hs initdb --truncate/--rotate` call no helper, and the rotated-partition `select *` union breaks. The no-op path logs at `trace` (below DEBUG). Refusals raise `SchemaUpgradeError`, exit `runtime_error`. |
| 03 | [DDL from the model](03-ddl-from-model.md) | Columns are added with `ALTER TABLE {format_table} ADD COLUMN {CreateColumn(c)}`. Indexes are diffed by name and added with `CreateIndex(if_not_exists=True)`. Missing tables use `CreateTable(if_not_exists=True)` plus their indexes. `part` gets `server_default=text('0')`. Six refusal rules, all checked before any DDL. R6 drops reflected unique constraints on exactly `[previous_id]`/`[next_id]`, PostgreSQL only. |
| 04 | [Concurrent upgrades](04-concurrency.md) | Serialize. A lock-free plan runs first, and an empty plan returns without taking a lock. Otherwise one transaction takes the lock: SQLite `BEGIN IMMEDIATE` on an AUTOCOMMIT connection, PostgreSQL `pg_advisory_xact_lock(K)`. It re-plans on that same connection and applies everything. One whole-step retry covers a leftover duplicate/exists error. Timeouts apply to the step only: SQLite `busy_timeout` 60 s, PostgreSQL `lock_timeout` 30 s. |
| 05 | [The 2.8.3 fixture](05-fixture-and-red-first.md) | Commit `tests/fixtures/hypershell-2.8.3.db` (40 KB), made by real 2.8.3, together with its generator and a provenance README. An opt-in test regenerates it from PyPI. The half-upgraded state comes from two frozen 2.9.0a1 `CREATE` statements applied to a copy. Run step: `hsx --restart -r 2`. Red on base: exit 5, `no such column: task.source`. |
| 06 | [What guards a doubled retry](06-retry-guard.md) | No schema guard holds in normal operation. The real guard is the single Scheduler thread, the only caller of `Task.next`, plus `Task.query_retryable`'s `retried IS FALSE`. Three cases are unguarded: two servers on one database, a kill between the retry insert and the mark (two commits), and a reset `retried` flag. |

## Cross-brief resolutions

- **The `create_all` call (02 vs 03).** Brief 02 ordered the step "refuse → `create_all` →
  columns". Brief 03 replaced `create_all` with per-table `CreateTable(if_not_exists=True)` inside
  the plan. Brief 03's form wins. A missing table becomes one more plan step, it runs under the
  same lock and transaction (04), and a refusal is raised while the plan is built, before any
  statement runs. That keeps the GOAL's half-upgrade complaint from recurring.
- **Lock always vs. lock-free fast path (03 vs 04).** Brief 03's sketch locks first. Brief 04 runs
  a lock-free plan first. Brief 04 wins: every start against a current schema stays read-only and
  never queues behind a writer.
- **Fresh databases and logging.** R3 asks for INFO on changes, but about 60 existing tests
  assert exact stderr on a fresh `temp_site` database (02). The step therefore logs INFO only when
  it upgrades a database that already holds a `task` table. Creating tables in an empty database
  is initialization, and it logs at `trace`, as `create_all` is silent today.
- **What `part` reads as for 2.8 partition rows (02).** The GOAL non-goal says they "read as
  `part = 0`", so the partition view substitutes `0`, not the partition index.
- **Hole C (`--no-db` touches the DB) and `hs server --initdb` ignored (02).** Both bugs predate
  this cycle and neither R-ID needs them fixed. They are recorded as follow-up candidates in
  `PLAN.md` §5, not built.
- **R7's "no effect on behaviour" (06).** It holds in normal operation only. This is a GOAL
  wording question and is raised in `PLAN.md` §5. The plan does not settle it.
