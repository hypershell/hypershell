# GOAL — Result writeback at or below 2.8.3's CPU cost

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** writeback-prebuilt-update
- **kind:** fix
- **appetite:** small · lands before the 2.9.0a3 pre-release (see Clarifications)

## Problem

`writeback-scoped-conditional` (#70) made result writeback safe. A returned result now writes only
the 13 result columns, and only onto the row's current assignment. A stale result is refused and a
repeated one is ignored. That change also made the server's main process slower per task, and on a
busy server that shows up as lost throughput.

The foreman's cluster runs measured it with 256 single-executor clients, bundle size 1, TLS and
SQLite. On a 16-CPU server node, 2.8.3 completed 129.0 tasks/s and `develop` completed 123.5
(−4.3%). On a 1-CPU server node, `develop` was 8.0% slower. A build with only #70 on top of
`b819de6` matches `develop`, so #70 accounts for all of the loss and the TimescaleDB change (#71)
for none of it. The SQL itself is cheaper than before: 13 columns, keyed on
`id = ? AND schedule_time = ? AND completion_time IS NULL`. Statements and commits per task are
unchanged, at 5.02 and 3.01. The extra cost is CPU in the server process, about +6% per task.

The foreman's profiling located it in `Task.__apply_result` (`src/hypershell/data/model.py:837-860`
at `bc6547a`). That method builds a new ORM `update(cls).where(…).values({13 columns})` for every
result, and SQLAlchemy coerces the statement and generates a fresh cache key on every execute. An
in-process microbenchmark of the receiver's write alone, over 5,000 results, measured 0.385 ms of
CPU per result for the 2.8 path (`Task.update_all` of the full row, at `b819de6`) and 0.50–0.51 ms
for #70's `apply_results` (+30%). A scratch variant that builds the conditional UPDATE once, against
the table with typed bind parameters, ran the same SQL text at 0.29–0.32 ms per result, with all
5,000 results applied and 50 failures recorded.

This is **introduced on `develop`** by #70 (`6f216d0`). It is not on any 2.8.x release, because #70
is not.

## Outcome / vision

The 2.9.0a3 pre-release keeps every guarantee #70 added and costs no more server CPU per result
than 2.8.3 did, so throughput is back at or above 2.8.3 on the configuration that showed the loss.
An in-repo test catches the regression if it comes back. That test does not depend on how fast the
machine running it is.

## Acceptance criteria (the contract)

"The writeback path" below is the server's application of a returned result:
`Receiver.update_tasks` → `Task.apply_results` at `bc6547a`, or whatever replaces it. "The 2.8
writeback" is 2.8.3's `Task.update_all` of the full row; `b819de6` carries the same path and may
stand in for it in a measurement.

- **R1** — WHEN the server applies a result that belongs to its row's current assignment, the CPU
  it spends per result SHALL be at or below what the 2.8 writeback spends on the same results. The
  evidence SHALL be an in-process measurement of the write alone, with the 2.8 writeback, `develop`
  (`bc6547a`) and the fix run interleaved on one machine with one Python version, over at least
  5,000 results. The method and the numbers SHALL be recorded in `spec/writeback-prebuilt-update/`.
- **R2** — For every result, the writeback path SHALL classify it exactly as `bc6547a` does
  (APPLIED, DUPLICATE or STALE), with the same rows changed, the same log lines, the same
  completion counting and the same `--failures` behaviour. That includes a result with no
  `schedule_time`, a cancelled row, and a task listed twice in one bundle. The existing tests in
  `tests/test_writeback.py` SHALL pass with their assertions unchanged.
- **R3** — WHEN a result is APPLIED, the writeback path SHALL write the 13 result columns
  (`Task.result_columns`) and no others. Each SHALL be stored with the value `bc6547a` stores,
  including timezone-aware datetimes and UUID-typed ids. The write SHALL apply only while the row
  still has the result's `schedule_time` and no `completion_time`, checked and written as one
  statement, as on `bc6547a`. This SHALL hold on SQLite, PostgreSQL and TimescaleDB, as the CI
  matrix runs them.
- **R4** — A test in the default suite SHALL guard against the per-result cost coming back. It
  SHALL be deterministic and SHALL NOT measure wall-clock time or CPU time. It SHALL fail when run
  against `bc6547a`'s writeback path and pass on the fix. `hs-plan` chooses what it observes; two
  candidates are that the same statement object serves every result, or that SQLAlchemy's
  compiled-statement cache misses stay flat across N results.
- **R5** — The fix SHALL NOT change the bundle wire format, the database schema, or the behaviour of
  other callers of `Task.update_all` (`hs task update`, cancel, revert). Behaviour under `in_memory`
  and `no_confirm` SHALL be as on `bc6547a`.
- **R6** — Before `/hs-publish`, the foreman SHALL record cluster throughput evidence in
  `spec/writeback-prebuilt-update/`, as the P-phase hand-back. The configuration is 256
  single-executor clients, bundle size 1, TLS, SQLite, and a 16-CPU server node, running 2.8.3,
  `develop` (`bc6547a`) and the fix interleaved. The fix's throughput SHALL be at or above 2.8.3's
  [NEEDS CLARIFICATION: the bar for "at or above". Proposed: the fix's mean over at least three
  interleaved runs per build is at or above 2.8.3's mean. If it falls short by less than the
  runs' spread, the foreman reports the numbers and the maintainer rules.]. Build agents never run
  this; `hs-review` checks that the evidence is recorded and meets the bar.

## Non-goals (no-gos)

- **2.8.4.** #70 is not on 2.8.x, and 2.8.x writes back through `Task.update_all`, which this cycle
  does not touch. Nothing here is backported.
- **Other per-task server overheads.** The scheduler's per-bundle SELECT, UPDATE and refresh, and
  the confirm write, are the same in 2.8.3 and are not part of the regression. Seeded as
  [`issues/server-per-task-statement-overhead.md`](../../issues/server-per-task-statement-overhead.md),
  with batching results across bundles noted there as one option.
- **Batching results across bundles.** It changes when results land and is a design question, not
  a regression fix. Covered by the seed above.
- **The 1.6% gap from 2.8.3 to `b819de6`.** It predates #70, no cause has been isolated, and the
  maintainer's a3 bar is throughput at or above 2.8.3, which R1's CPU margin is expected to cover.
  Not seeded. If R6's evidence shows the fix still short of 2.8.3, that evidence is what a seed
  would need.
- **Any change to what #70 guarantees.** The result-column set, the assignment fence, and the
  APPLIED/DUPLICATE/STALE outcomes stay exactly as they are (R2, R3).
- **A wall-clock or CPU benchmark in CI.** The CPU and throughput evidence is recorded once, in
  `spec/` (R1, R6). The standing guard is R4.

## Clarifications

- **Q:** When, and for which release? — **A:** Fix before cutting pre-release 2.9.0a3, which will
  carry writeback (#70), timescale (#71), the TLS fix (#72) and this fix, with cluster evidence
  showing throughput at or above 2.8.3 (maintainer, 2026-11-04).
- **Q:** Appetite? — **A:** `small`. The diagnosis is done and a candidate fix has been tried in a
  scratch microbenchmark (foreman's shaping brief, 2026-11-04).
- **Q:** The `fix`-kind rule keeps the suspected mechanism out of the R-IDs. How firm is the
  diagnosis? — **A:** The foreman's profiling confirmed it (2026-11-04), and the Problem section
  records it as evidence. R1 and R6 are still stated as the observable cost, so `hs-plan` is free
  to find more than the one cause. R4 names the mechanism only as candidate guards and leaves the
  choice to the plan.
- **Q:** Must the DUPLICATE/STALE classifying SELECT be prebuilt too? — **A:** Optional. It runs
  only when the UPDATE matches nothing, which is rare. R2 holds either way, and the plan decides
  (foreman's shaping brief, 2026-11-04).
- **Q:** Which databases? — **A:** SQLite, PostgreSQL and TimescaleDB, as in the existing CI matrix
  (foreman's shaping brief, 2026-11-04).
- **Q:** Who produces the cluster evidence? — **A:** The foreman, as the P-phase hand-back. Build
  agents do not touch cluster resources (foreman's shaping brief, 2026-11-04).
- **Open:** the R6 bar — see the `[NEEDS CLARIFICATION]` marker in R6. The foreman rules at sign-off.

## Related materials

- Prior cycle: [`spec/writeback-scoped-conditional/`](../writeback-scoped-conditional/) (#70), which
  introduced the conditional writeback this fix keeps intact. See its R1, R2, R3, R5, R7 and R8.
- Source: `src/hypershell/data/model.py` (`Task.result_columns`, `Task.apply_results`,
  `Task.__apply_result`, `ResultOutcome`), `src/hypershell/server.py` (`Receiver.update_tasks`).
- Tests: `tests/test_writeback.py`; the PostgreSQL and TimescaleDB entries in
  `.github/workflows/tests.yml`.
- Baselines: tag `2.8.3`; `b819de6` (develop before #70); `bc6547a` (develop at shaping).
- Follow-up seed: [`issues/server-per-task-statement-overhead.md`](../../issues/server-per-task-statement-overhead.md).
