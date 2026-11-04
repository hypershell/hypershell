# GOAL — A task-row write that waited on a row lock still lands on a TimescaleDB hypertable

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** hypertable-update-retry
- **kind:** fix
- **appetite:** small  ·  *Fix before cutting pre-release 2.9.0a3 (see Clarifications). Two
  demonstrated write paths, an audit of the others, and one deterministic CI regression test. No
  schema, wire or dependency change.*

## Problem

Under the `timescale` provider (#71), `task` is a TimescaleDB hypertable. TimescaleDB 2.29.0
regressed how an `UPDATE … WHERE id = X` behaves after it waits on another transaction's row lock.
PostgreSQL re-checks the row once the other transaction commits and updates it, so the statement
matches 1 row. On a hypertable under TimescaleDB 2.29.0 and later, the same statement matches **0
rows**, even though the row still exists and still satisfies the `WHERE` clause. The writer is told
nothing was there.

A deterministic two-session probe against TimescaleDB containers confirms it. One session holds the
row lock and the other session's UPDATE blocks on it; when the holder commits, the blocked UPDATE
matches 1 row on 2.24.0 and 2.28.0, and 0 rows on 2.29.0 and 2.31.0, in both the Community and the
Apache builds. A plain table on the same server matches 1. `SET
timescaledb.enable_hypertable_expansion_for_dml = off` (2.29.1+) does not restore the PostgreSQL
behaviour. The likely cause is upstream #9315 (2.29.0), which moved hypertable UPDATE/DELETE onto
TimescaleDB's own expansion.

HyperShell runs into this inside a single server. The `Confirm` thread and the `Receiver` thread
update the same `task` row milliseconds apart:

- **Confirm** stamps the confirming client onto the bundle's rows (`Confirm.update_info` →
  `Task.update_all(self.client_info.transpose())`, `src/hypershell/server.py` L422–425), which goes
  through `Entity.update_all` → `Session.bulk_update_mappings` (`src/hypershell/data/model.py`
  L196–201). SQLAlchemy asserts the matched row count. When Confirm loses the race, the 0-row match
  raises `StaleDataError`, the Confirm thread dies, and the server exits with status 5.
- **Receiver** writes each result with the prebuilt conditional UPDATE (`Task.__result_statements`
  and `Task.__apply_result`, `model.py` L845–882, from #70 and #73). When the Receiver loses, the
  0-row match is classified as STALE: a current, legitimate result is refused with a warning, and
  the task never completes.

This is the CI failure on `develop` `0e8275e`: run 41906240784, job "PostgreSQL (timescale-oss)",
`tests/test_timescale.py::test_run_compress_list_vacuum`, whose log names `Confirm`. The failure is
timing-dependent: the same test passed 10 times out of 10 at each of four `develop` commits in a
reproduction. CI's `timescale` and `timescale-oss` entries both run 2.31.0 (`.github/workflows/tests.yml`),
so both are exposed; `timescale-floor` runs 2.24.0 and is not.

Other writers of `task` rows may meet the same thing wherever two writers can contend on a row under
this provider. Nobody has audited them yet:

- the Scheduler stamping rows as scheduled (`Task.next`, `model.py` L671–687) and marking a failed
  row `retried` (`model.py` L726);
- `Task.revert_orphaned` (eviction, from `HeartMonitor`, `server.py` L709) and
  `Task.revert_interrupted` (server start, `server.py` L205), both through `Task.revert_all`
  (`model.py` L761);
- `Task.cancel_all` and `hs task update` (`src/hypershell/task.py` L1052–1144), run from another
  process against a live server;
- the heartbeat and eviction marks on `client` rows (`server.py` L654–709). `client` is a plain
  table under every provider (#71 hypertables only `task`), which the probe shows unaffected.

**Introduced by the environment, not by a HyperShell commit.** The code paths are unchanged; what
changed is the TimescaleDB release CI and users run. It exists on `develop` since the `timescale`
provider landed (#71).

## Outcome / vision

On a TimescaleDB hypertable of any supported version, a write to a `task` row that had to wait for
another writer behaves the way it does on PostgreSQL: once the other writer commits, the write lands
if the row still qualifies, and is refused only for the reason it would be refused without the wait.
The server no longer dies when Confirm loses that race, and a legitimate result is no longer refused
as STALE. SQLite, plain PostgreSQL and TimescaleDB before 2.29 see no change at all. CI carries a
deterministic test that forces the race, so the next regression of this kind fails a build instead
of flaking one.

## Acceptance criteria (the contract)

Terms. A write is **contended** when it blocks on a row lock held by another transaction, and that
transaction then commits. The row **still qualifies** when, after that commit, it exists and satisfies
the write's own condition: for Confirm, the row exists; for a returned result, the row is still in
the result's current assignment, as `spec/writeback-scoped-conditional/` defines it.

- **R1** — WHEN Confirm's write to a dispatched `task` row is contended and the row still qualifies,
  the server SHALL record the confirming client's `client_id` and `client_host` on the row, and the
  Confirm thread SHALL keep running. The server SHALL NOT exit on that account.
- **R2** — WHEN a returned result's write is contended and the row still qualifies (for example, the
  holder was Confirm stamping the client), the server SHALL apply the result exactly as
  `writeback-scoped-conditional` R3 requires: outcome APPLIED, the result columns written, and the
  same completion logging and `--failures` behaviour as an uncontended result.
- **R3** — IF a contended write's row no longer qualifies once the holder commits (it was reverted,
  rescheduled, completed or cancelled in the meantime, or no longer exists), THEN the outcome SHALL
  be the one `develop` `0e8275e` gives the same row state without contention: a returned result is
  classified STALE or DUPLICATE per `writeback-scoped-conditional` R2 and R5, and writes nothing.
  Waiting SHALL NOT turn a write the condition refuses into one that lands.
- **R4** — Every code path that UPDATEs `task` rows SHALL be audited, and `TECH.md` SHALL list each
  with its verdict: either it can be contended under the `timescale` provider and gets the R1–R3
  behaviour, or it cannot, with the reason. The paths named in Problem are the minimum list.
  `Task.revert_orphaned` is reachable inside one server (HeartMonitor eviction against a late
  Confirm or result for the same row) and SHALL get the R1 behaviour: a contended revert lands and
  the HeartMonitor thread does not die. `client`-table writes are expected to need no change; the
  audit confirms it.
- **R5** — Recovering a contended write SHALL be bounded: no unbounded loop, and each extra attempt
  logged at DEBUG naming the task id. IF the bound is exhausted while the row still qualifies, THEN
  the server SHALL log a WARNING naming the task id and the write path, and `TECH.md` SHALL state the
  outcome in that case. The bound's value is the plan's.
- **R6** — WHILE the database is SQLite, plain PostgreSQL, or TimescaleDB before 2.29, and for any
  write that is not contended, behaviour SHALL be identical to `develop` `0e8275e`: the same rows,
  outcomes, and log lines at INFO and above, and a write that matches on its first attempt is
  neither re-run nor re-checked. WHILE the server runs `in_memory`, it SHALL still do no database writeback. The
  schema, the bundle wire format and the task-lifecycle predicates (`AGENTS.md` § Task lifecycle
  contract) SHALL be unchanged.
- **R7** — A test SHALL force the race deterministically on a server database: a second connection
  takes the row lock, HyperShell's write is shown to be **waiting on that lock** (observed from the
  server, not inferred from a sleep), and the holder then commits. It SHALL cover Confirm (R1), the
  Receiver (R2), the refused case (R3: the holder reverts the row and the result is STALE), and each
  further path R4 brings into scope. It SHALL run in every entry of the CI `postgres` job. On
  `timescale` and `timescale-oss` (TimescaleDB 2.31.0) it SHALL fail on `0e8275e` and pass after the
  fix. On `pg13`, `pg18` and `timescale-floor` (2.24.0) it SHALL pass both before and after, as
  controls. On SQLite it SHALL be skipped.
- **R8** — `AGENTS.md` § Data layer specifics and `.agents/factory/invariants.md` SHALL record the
  hazard and the rule for future writers: under the `timescale` provider, an UPDATE of a `task` row
  that matches 0 rows is not proof the row is gone or no longer qualifies, and a new task-row write
  follows the R1–R5 policy.

## Non-goals (no-gos)

- **Working around the bug inside TimescaleDB.** No extension patch, no GUC, no change to how the
  hypertable is created or partitioned. The probe shows the one relevant GUC does not help.
- **Pinning or refusing TimescaleDB versions.** The maintainer chose fix-first over a pin (see
  Clarifications). No version gate, no startup warning for 2.29+, and the CI images stay at 2.31.0
  and 2.24.0, which is what lets R7 fail before the fix.
- **Filing upstream.** The foreman drafts the upstream issue separately, for the maintainer's
  review. It is not this cycle's work, and nothing here waits on an upstream fix.
- **A 2.8.x backport.** The fix does not apply to 2.8.4 (see Clarifications).
- **A general concurrency redesign** of the server's writers: row locking across threads, a change
  of isolation level, or serializing Confirm and Receiver through one thread. The plan may use any
  of these narrowly if it is the smallest fix, but re-architecting the writers is not the goal.
- **`issues/stale-confirm-hides-orphan.md`.** The ordering of Confirm against eviction is its own
  seed. This cycle only makes a contended write land; it does not change which rows eviction finds.
- **Running the whole suite against a server database** (`issues/server-db-test-suite.md`).
  `test_run_compress_list_vacuum` stays as it is; R7 is the regression gate.
- **Two servers on one database**, which is unsupported.

## Clarifications

- **Q:** Fix in HyperShell, or ship 2.9.0a3 with a version pin or a known issue? — **A:** "Fix in
  HyperShell first (Recommended)": the fix lands before 2.9.0a3 is cut (maintainer, 2026-11-04).
- **Q:** Is the upstream behaviour confirmed, and where does it start? — **A:** Confirmed by the
  foreman, 2026-11-04, with a deterministic two-session probe against TimescaleDB containers on a test
  cluster. A blocked `UPDATE … WHERE id = X` on a hypertable matches 1 row on 2.24.0 and 2.28.0, and
  0 on 2.29.0 and 2.31.0, in both the Community and the Apache builds. A plain table on the same
  server matches 1. `timescaledb.enable_hypertable_expansion_for_dml = off` (2.29.1+) does not restore
  it. The likely cause is upstream #9315 (2.29.0). The CI failure (run 41906240784, timescale-oss)
  names Confirm, and the same test passed 10/10 at four `develop` commits in a reproduction, so the
  failure is timing-dependent (foreman, 2026-11-04).
- **Q:** Is the "re-run the statement on a 0-row match while the row still qualifies" approach the
  contract? — **A:** No. It is the foreman's candidate: Confirm moves off the count-asserting bulk
  update to a statement whose count HyperShell checks, and the Receiver re-runs before classifying a 0
  as STALE or DUPLICATE, with bounded retries logged at DEBUG. The GOAL states outcomes (R1–R6) and
  leaves the mechanism to `hs-plan` (foreman, 2026-11-04).
- **Q:** Does the fix apply to 2.8.4? — **A:** No. 2.8.x has no `timescale` provider: the `2.8.3`
  tag mentions TimescaleDB nowhere under `src/`. Under 2.8's `postgres` provider the `task` table
  carries `UNIQUE` on `previous_id` and `next_id` (`model.py` L276–277 at `2.8.3`). TimescaleDB requires
  every unique index on a hypertable to include the partitioning column, so `task` cannot be converted
  without dropping those constraints by hand, which HyperShell has never documented for 2.8.
  2.8's whole-row `update_all` writeback would meet the same regression on such a hand-built
  hypertable, but that setup is unsupported (shaping, 2026-11-04).
- **Q:** What policy covers the other UPDATE paths? — **A:** An audit with a recorded verdict per
  path (R4). Confirm, the Receiver and `revert_orphaned` are in scope because each is reachable inside
  one server. The Scheduler's stamps, `revert_interrupted`, cancel and `hs task update` contend only
  with another process; whether they need the treatment is the audit's call, and `client` rows are
  expected to need none because `client` is never a hypertable (shaping, 2026-11-04).
- **Q:** Appetite? — **A:** `small`, the default for a fix and the foreman's guidance; the size
  circuit-breaker is not tripped at eight criteria (shaping, 2026-11-04).
- **Q:** Which CI entries prove the fix? — **A:** Read from `.github/workflows/tests.yml` at
  `0e8275e`: `timescale` and `timescale-oss` run 2.31.0 and are exposed, `timescale-floor` runs 2.24.0
  and is not, and `pg13`/`pg18` are plain PostgreSQL. R7's fails-before applies to the first two only;
  the other three are controls (shaping, 2026-11-04).

**Open for sign-off** (defaults chosen above; none blocks `hs-plan` unless the maintainer overturns
it):

- **R8 edits `AGENTS.md`.** It adds a data-layer hazard and a rule for new task-row writes. It
  strengthens a guarantee and relaxes nothing, as `writeback-scoped-conditional` R10 did, but an
  `AGENTS.md` change is flagged to the maintainer at sign-off.
- **R5's exhaustion outcome is left to the plan.** The GOAL requires only that it be bounded,
  WARNING-logged and stated in `TECH.md`. If the maintainer wants a fixed outcome (for example,
  Confirm keeps running and the Receiver classifies the result STALE), it goes in R5 now.
- **R4's cross-process paths.** If the audit finds that `Task.next`, cancel or `hs task update`
  can be contended, they get the R1–R3 behaviour and an R7 test each. That is the most likely way
  this cycle outgrows `small`; the alternative is to record a verdict and defer them to a seed.

## Related materials

- Defect evidence: CI run 41906240784 (job "PostgreSQL (timescale-oss)",
  `tests/test_timescale.py::test_run_compress_list_vacuum`) on `develop` `0e8275e`; the foreman's
  two-session probe and version bisect (Clarifications, 2026-11-04).
- Upstream: `timescale/timescaledb` #9315 (2.29.0, hypertable UPDATE/DELETE expansion), the likely
  origin; the 2.29.0–2.31.0 release notes.
- Prior cycles: [`spec/timescale/`](../timescale/GOAL.md) (#71: the provider, the hypertable pass,
  and the CI `postgres` matrix entries); [`spec/writeback-scoped-conditional/`](../writeback-scoped-conditional/GOAL.md)
  (#70: the conditional result write, ResultOutcome, R2/R3/R5); `writeback-prebuilt-update` (#73:
  the prebuilt statements).
- Related seeds: [`issues/stale-confirm-hides-orphan.md`](../../issues/stale-confirm-hides-orphan.md),
  [`issues/server-db-test-suite.md`](../../issues/server-db-test-suite.md).
- Source: `src/hypershell/server.py` (`Confirm.update_info`, `Receiver`, `HeartMonitor`),
  `src/hypershell/data/model.py` (`Entity.update_all`, `Task.next`, `Task.revert_all`,
  `Task.revert_orphaned`, `Task.revert_interrupted`, `Task.cancel_all`, `Task.apply_results`,
  `Task.__result_statements`, `Task.__apply_result`), `src/hypershell/task.py` (`hs task update`).
- Tests and CI: `tests/test_writeback_postgres.py`, `tests/test_timescale.py`,
  `.github/workflows/tests.yml` (`postgres` job and its matrix).
