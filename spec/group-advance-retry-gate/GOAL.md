# GOAL — Don't advance a task group past a pending retry

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).
> Keep this at the right altitude: solved and bounded, but not over-specified — leave design
> freedom for the plan. Edit requirements here; do **not** silently drift them during build.

- **slug:** group-advance-retry-gate
- **kind:** fix
- **appetite:** small

## Problem

`tests/test_groups.py::test_group_failed_task_with_retries` still fails intermittently in CI:
`hs list -c` reports **4** task rows where **5** are expected. It failed on GitHub Actions run
30681973049 (2026-08-01, Python 3.12), run 41360586622 (2026-11-01, Python 3.13) and run
41365455836 (2026-11-02, Python 3.11 and 3.13). The missing row is the third attempt of the
retried task: its second attempt failed with retries remaining, and the server moved on to the next
group without ever retrying it. The run still exits 0, so a user sees a "successful" run in which
a task that should have been retried was silently abandoned.

This is a **product defect, not a test flake.** When `Scheduler.load_bundle` (`server.py`) finds
nothing to schedule in the current group, it asks `Task.increment_group` (`data/model.py`) whether
the group is finished. `increment_group` holds the group back only for incomplete rows and for
failures whose retry budget is **exhausted**. A failure that still has retries left, and has not
yet been retried, blocks nothing. The Receiver thread writes results back concurrently. If it
commits such a failure between the scheduler's `Task.next` call (which saw the attempt still in
flight) and its `increment_group` call (which sees it complete and retryable), neither call holds
the group. The group advances, and the retry is never created. A deterministic replay against
`c1d4470` shows exactly this. With attempt 2 in flight, `Task.next` returns nothing. Committing
the failure and then calling `increment_group(0)` reports group 1 as viable. The database is left
with one retryable failure that nobody will retry, and four rows.

This **overturns the verdict of [`spec/deflake-group-retry-test/`](../deflake-group-retry-test/)**,
which concluded "no product defect" and rebuilt the test to assert on database state. The "lost
stdout" that cycle diagnosed, `"1"` missing from captured output, was this same skipped retry. The
final attempt that would have printed `"1"` was never scheduled. Moving the test onto authoritative
state is what exposed the defect as a row-count mismatch.

The same window exists on the scheduler's **stop path**. When `Task.next` comes back empty,
`load_bundle` first checks `Task.count_remaining() == 0`, which counts only incomplete rows, so a
retryable failure committed in that gap sends a non-`forever` run to FINAL without its retry, in
the last group or in a run with no groups at all.

## Outcome / vision

A task group advances only after every retryable failure in it has been retried, whatever the
timing between the scheduler and the result writeback, and a run stops only once no retryable
failure is pending. The integration test passes reliably without being touched. A deterministic unit test pins the interleaving, so the defect cannot
return unnoticed. A second one pins the stop-path interleaving.

## Acceptance criteria (the contract)

- **R1** — WHEN a task in the current group fails with retries remaining, and that failure is
  recorded after the scheduler has found nothing to schedule but before it decides whether the
  group is finished, the server SHALL still create that task's retry, and SHALL NOT schedule any
  task of a later group before doing so.
- **R2** — WHILE a group holds a failed task (non-zero `exit_status`, not `CANCEL_STATUS`) that
  has retries remaining and has not been retried, the server SHALL NOT advance to the next group.
  It SHALL NOT log `Completed task group N`, and it SHALL NOT treat the group as halted on failure.
- **R3** — One retryable-failure predicate SHALL govern both decisions, group advance and stop.
  The failures that hold a group as "awaiting retry", or keep a run from stopping, SHALL be exactly
  the failures the scheduler would select for retry under the same `attempts` value. A task the
  scheduler would retry always holds its group and the run, and a task that holds either is always
  one the scheduler will retry.
- **R4** — A regression test tagged `unit` SHALL replay the R1 interleaving deterministically,
  without sleeps or timing. It SHALL fail on `c1d4470` and pass with the fix.
- **R5** — Existing gating behaviour SHALL be unchanged: a group whose failures have exhausted
  their retries still stops the server (non-`forever`) or waits (`forever`); a cancelled task
  neither holds nor halts its group; runs with `attempts == 1` behave as before; and a group whose
  retried task succeeds advances as before; and runs with no failures reach FINAL exactly as
  before.
- **R6** — `tests/test_groups.py::test_group_failed_task_with_retries` SHALL pass unmodified,
  with 5 rows, across the CI matrix (Python 3.11–3.14).
- **R7** — WHEN a task fails with retries remaining, and that failure is recorded after the
  scheduler has found nothing to schedule but before it decides whether the run is finished, a
  non-`forever` server SHALL still create and run that retry before reaching FINAL. This holds in
  the last group and in runs without groups.
- **R8** — A second regression test tagged `unit` SHALL replay the R7 (stop-path) interleaving
  deterministically, without sleeps or timing. It SHALL fail on `c1d4470` and pass with the fix.

## Non-goals (no-gos)

- Editing `test_group_failed_task_with_retries` or any other integration test.
- Changing the retry model: new rows per attempt, the `previous_id`/`next_id` chain, how
  `attempts` is derived from `max_retries`, or retry selection order (`eager`).
- Changing the stall-by-design behaviour for exhausted failures (AGENTS.md, "Group-gating can stall
  by design").
- Reworking Scheduler/Receiver threading, transactions, or locking. The fix is in the gating
  and stop decisions, not the concurrency model.
- Making the run's exit status reflect abandoned or failed tasks
  (`issues/run-exit-status-failure-aware.md`).
- Retry policy for never-ran sentinels (`issues/resource-error-terminal-and-retry-defaults.md`).
- Amending `spec/deflake-group-retry-test/`. That record is retained as written, and this GOAL is
  the correction.

## Clarifications

- **2026-11-01 — Diagnosis approved** (Geoffrey, alignment memo Q13a): fix the gating predicate;
  replay as regression test; integration test unchanged.
- **Approved approach (input to `hs-plan`, not a requirement):** `Task.increment_group` refuses to
  advance while its group holds a retryable failure, defined by `select_failed`'s predicate:
  `exit_status != 0`, `exit_status != CANCEL_STATUS`, `attempt < attempts`, `retried = False`.
  The next LOAD then picks the retry up through `Task.next`. (resolved 2026-11-01)
- **Blast radius** (resolved 2026-11-01): the change is in `data/model.py`, the highest-blast-radius
  file. AGENTS.md says `attempts == max_retries + 1` spans `server.py`, `Task.select_failed` and
  `Task.increment_group`, and those three change together. This fix **consumes** that relationship
  but does not **change** it. `increment_group` already receives the scheduler's `attempts`, and
  the new hold uses the same value as `select_failed`, which is what R3 pins. `server.py`'s
  derivation of `attempts` and `select_failed` itself stay as they are. The `CANCEL_STATUS`
  exclusion is mandatory (AGENTS.md: every failure/retry query filters it).
- **2026-11-01 — Stop path in scope** (foreman, as judge under the SC26 mandate; same defect class
  as Q13a's "fix the gating predicate"): the scheduler's stop check SHALL NOT end a run while a
  retryable failure is pending. Window verified by reading: `count_remaining` filters only
  `completion_time IS NULL`.

## Related materials

- Prior cycle whose verdict this overturns: [`spec/deflake-group-retry-test/`](../deflake-group-retry-test/)
  (`PLAN.md` "Is the missing line a real product defect?", `REVIEW.md` R4 row).
- Failing CI runs (GitHub Actions): 30681973049 (py3.12), 41360586622 (py3.13), 41365455836
  (py3.11, py3.13).
- Source: `src/hypershell/server.py` (`Scheduler.load_bundle`),
  `src/hypershell/data/model.py` (`Task.increment_group`, `Task.select_failed`, `Task.next`).
- Test: `tests/test_groups.py` (`test_group_failed_task_with_retries`).
- Invariants: AGENTS.md, "Task lifecycle contract" (retry model, `attempts == max_retries + 1`,
  `CANCEL_STATUS` filter, group-gating).
