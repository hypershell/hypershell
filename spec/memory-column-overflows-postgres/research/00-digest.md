# 00 — Digest

1. **Placement.** One enumerated widening, `WIDENED = {('task', 'memory')}`, beside
   `RETIRED_UNIQUE` in `upgrade.py`, planned in `_plan`'s PostgreSQL block, applied in the existing
   single transaction under the advisory lock. Due only when the reflected type is `INTEGER` or
   `SMALLINT`, so a current schema costs nothing new ([01](01-upgrade-step.md)).
2. **Lock ceiling.** Unchanged: 30 s slices, 900 s ceiling. `lock_timeout` bounds the wait for the
   `task` lock, not the rewrite. A concurrent first use plans the widening lock-free (reflection is
   not blocked by the uncommitted `ALTER`, probed on PostgreSQL 18), waits on the advisory lock,
   re-plans to nothing and proceeds; one process logs the change. A waiter past 900 s gets the
   existing `WAIT_UPGRADE` error and a rerun succeeds; the docs say so ([01](01-upgrade-step.md)).
3. **TimescaleDB.** 2.24.0+ allows `ALTER COLUMN TYPE` with the columnstore on and no compressed
   chunk (PR #8849, issue #8840, `src/process_utility.c`); 2.22/2.23 refuse it whenever the
   columnstore is enabled; every release refuses with a compressed chunk. So Q1 option A is:
   probe (extension, hypertable, `compression_enabled`, any `is_compressed` chunk, version);
   compressed → refuse before any statement; enabled and below 2.24.0 → turn the columnstore off,
   then widen; otherwise widen directly ([02](02-timescale-alter-type.md)).
4. **Who turns it back on.** `converge` follows the step only in `initdb`. `checkdb` (every other
   first use) must run `converge` when, and only when, the step turned the columnstore off, and
   the step may turn it off only when its caller will converge (the `timescale` provider);
   otherwise it refuses with the manual steps ([01](01-upgrade-step.md)).
5. **Tests.** Only plain PostgreSQL runs here (no Docker, no TimescaleDB). A template-free
   "narrowed" database (a fresh `hs initdb`, then `memory` set back to `integer`) gives local
   evidence for R3–R6; the `hs283`/`hs290a1` template cases and every TimescaleDB case run in CI
   only. The 2.22/2.23 branch is exercised in CI by forcing the release gate on the Community
   entries.
6. **Appetite.** Option A fits small: three phases, the TimescaleDB part one of them. No split.

**Superseded at the plan gate (2026-11-23):** items 3 and 4's "turn the columnstore off, then widen"
for TimescaleDB 2.22/2.23 is replaced by a refusal (PLAN §7). The step never toggles the columnstore,
so `checkdb` gains no converge and `upgrade_schema` gains no `converges` flag. Brief 02's findings
stand; its "dance" section is background only.
