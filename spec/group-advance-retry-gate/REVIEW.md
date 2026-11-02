# REVIEW — Don't advance a task group past a pending retry

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 9bb144218c78bd06f0e958b848adc199324ca0ee  ·  **Base:** `c1d4470` (`develop` at the fork point)  ·  **Date:** 2026-11-01
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md` (escalate to human on non-convergence)
- **Mode:** single blind correctness reviewer (no `debate`, no `completeness` sub-pass), plus an
  orchestrator re-run of the suite, the base red check and the CLI drives.

## Pre-flight

- **Base resolution.** `TECH.md` records `base: develop`, but the local `develop` ref is stale: it
  carries unsquashed harness commits, and its merge base with this branch is `aee41ae`, not the
  fork point. `git diff --stat develop...HEAD -- . ':(exclude)spec/'` lists 38 files, including the
  `uv.lock` refresh and unrelated harness work. The review graded `c1d4470...HEAD`, which touches
  only the four files below.
- **Contract drift.** `git log --oneline c1d4470..HEAD -- spec/group-advance-retry-gate/GOAL.md` shows
  the shaping commit `b352d73` and one amendment, `fc5755b` ("Fold the stop path into …
  goal"). The amendment rewrote R3 and added R7 and R8. It landed one minute after shaping
  and before the plan commit `8d5a823`, and `GOAL.md` § Clarifications records it ("Stop path in
  scope"). Nothing moved the contract mid-build. The review graded the `GOAL.md` at HEAD. **For
  human confirmation:** that this amendment is the accepted contract.

## Verification run

Commands actually executed and their outcomes. The reviewer and the orchestrator ran these
independently. "Base" runs import an export of `c1d4470`'s `src/` through `PYTHONPATH`, confirmed
by `hypershell.__file__`, so no tracked file was touched.

- `uv run pytest` (full suite, HEAD, Python 3.14) → **483 passed**, exit 0 (both runs).
- `uv run pytest tests/test_retry_gate.py` at HEAD → **22 passed**; `-m unit` selects all 22.
- The same file against base → **14 failed, 8 passed**:
  - `test_group_advance_waits_for_pending_retry[False|True]` fails at `tests/test_retry_gate.py:160`
    with `assert 1 == 0`: the server advanced to group 1.
  - `test_stop_waits_for_pending_retry[4 params]` fails at `:198` with `assert FINAL is not FINAL`.
  - `test_hold_matches_retry_selection[8 params]` fails with `AttributeError` (`count_retryable` is
    new).
  - The 8 tests that guard unchanged behaviour pass at base and at HEAD.
- `git diff --quiet c1d4470 -- tests/test_groups.py` → unmodified. The only changed path under
  `tests/` is the new `tests/test_retry_gate.py`.
- `tests/test_groups.py::test_group_failed_task_with_retries` ×10 at HEAD → **10/10 passed** (both
  runs).
- `uv run --isolated --python 3.12|3.13 pytest tests/test_retry_gate.py tests/test_groups.py` →
  **33 passed** on each. Python 3.11 was not run locally (no interpreter installed). The diff uses
  no version-specific syntax.
- Statement-boundary sweep (reviewer probe). A SQLAlchemy `before_cursor_execute` hook commits the
  attempt-2 failure before the k-th SQL statement of the `load_bundle` call that starts with attempt
  2 in flight, for every k. It covers three layouts (groups, no groups, last group) with eager on
  and off, then drives to FINAL. → **HEAD 0/30 bad; base 17/30 bad.** At base, a group-1 task was
  scheduled with the retry abandoned, or FINAL was reached with a retryable failure pending.
- R3 property probe (reviewer). 300 random sets of terminal rows: exit statuses
  {0, 1, 2, 255, −1, −9, −15, −1001, −1002}, attempt 0–5, random `retried`, groups 0/1, `attempts`
  1–4, eager on and off. → **0 mismatches** between `count_retryable` and the distinct retries
  `Task.next` creates, and 0 between "`increment_group` holds" and "a retry is created".
- CLI drives, `temp_site.sh` + `hsx t.in -r 3 -N2` (orchestrator, HEAD):
  - Grouped taskfile → rc 0, 5 rows, `n:1` succeeded on attempt 3, `n:2` in group 1, 0 remaining.
  - Ungrouped taskfile → rc 0, 4 rows, `n:1` succeeded on attempt 3, 0 remaining.
- CLI drives with a 2 s sleep injected after an empty `Task.next` (reviewer, in copies of the tree
  under `$TMPDIR`):
  - Base → grouped run gave 3 rows with "Completed task group 0" logged and the retry abandoned;
    the ungrouped run gave 1 row and went to FINAL with the retry pending.
  - HEAD, eager on and off → 5 rows (grouped) and 3 rows (ungrouped), with `n:1` succeeding on
    attempt 3.
- Group holding an exhausted and a retryable failure (reviewer probe, attempts=3):
  - HEAD holds the group ("waiting on 1 failed tasks to be retried"), runs the retry, then halts
    with "Failed task group 0 … exceeding allowed retries".
  - Base halts immediately.
- `git status --porcelain` → empty after the reviewer handed back and after every orchestrator
  run.

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 | `Task.increment_group` checks `count_retryable` after the incomplete count (`data/model.py`), 9bb1442 | R4 replay red at base and green at HEAD; sweep 0/30 bad at HEAD; injected-delay CLI drive | ✅ |
| R2 | Same check, placed before the exhausted-failure halt | Sweep: no group-0 completion or failure log before the retry; exhausted+retryable probe holds, then halts | ✅ |
| R3 | `Task.query_retryable` is shared by `select_failed` and `count_retryable`; `count_retryable` mirrors the non-eager `attempts > 1` gate; `load_bundle` passes `eager` to both decisions | `test_hold_matches_retry_selection` (8 params); 300-case property probe, 0 mismatches. See P1 for a scope question | ✅ (P1 open) |
| R4 | `test_group_advance_waits_for_pending_retry` (`@mark.unit`, `poll=0`, monkeypatched one-shot injection, no sleeps), fef5655 | Fails at base on the behaviour (`:160`), passes at HEAD | ✅ |
| R5 | Check order keeps the exhausted halt and the forever-mode wait; `CANCEL_STATUS` filtered in `query_retryable`; `count_retryable` is 0 when `attempts ≤ 1` without eager | Guard tests pass at base and HEAD; full suite (incl. `test_group_hard_failure_halts`, `test_cancel.py`) passes | ✅ |
| R6 | Product fix only; `tests/test_groups.py` unmodified | Diff guard; 10/10 repeats; passes on 3.12, 3.13 and 3.14 | ✅ (3.11 left to CI) |
| R7 | `Scheduler.load_bundle` stop condition adds `count_retryable(...) == 0` after `count_remaining() == 0` (`server.py`), 9bb1442 | R8 replay red at base and green at HEAD; sweep (no-groups and last-group layouts) 0 bad at HEAD; injected-delay CLI drive | ✅ |
| R8 | `test_stop_waits_for_pending_retry` (`@mark.unit`; no groups and last group, × eager) | Fails at base (`:198`), passes at HEAD | ✅ |

Unmapped changes (possible scope creep): none.
- The `data/model.py` and `server.py` changes map to R1, R2, R3 and R7.
- `tests/test_retry_gate.py` maps to R3, R4, R5 and R8.
- The `AGENTS.md` edit is the lockstep prose update for the retry model and stall-by-design.
  It matches the code.

Invariants walked (§1–§5, §12): lifecycle predicates and the `CANCEL_STATUS` filter are
reproduced exactly. The retry model and the `attempts == max_retries + 1` derivation are
untouched. `in_memory` paths are untouched. There is no new FSM state; the hold path re-enters
LOAD after the existing finite back-off, and LOAD still polls signals. New tests carry
`@mark.unit`. No violations.

## Findings

No CONFIRMED findings. One PLAUSIBLE item for human triage.

### [MEDIUM/PLAUSIBLE] P1 — A resumed run stops at START while a retryable failure is pending
- **Where:** `src/hypershell/server.py:182-193` (`Scheduler.start`), unchanged by this diff.
- **Failure scenario:** the database holds a failed task (`exit_status=1`, `attempt=1`,
  `retried=False`) and nothing incomplete. A non-`forever` server starts with `--restart` and
  `attempts=3`, either with no submitter or with the submitter finished. `start()` sees
  `count_remaining() == 0`, logs "All previous tasks completed - stopping" and returns FINAL. The
  retry `Task.next` would create is never made. A run can reach this state when it is interrupted
  after a failure is written back but before the next LOAD, or when it is resumed with a higher
  `-r`.
- **Evidence:** an in-process probe run through `.agents/factory/bin/temp_site.sh uv run python
  <probe>`. It creates the schema, adds that one failed row, and calls
  `Scheduler(queue=None, attempts=3, restart_mode=True, poll=0).start()`. Result: "retryable
  pending: 1", `start() -> SchedulerState.FINAL`. The base export gives the same FINAL. The
  reviewer reproduced it independently.
- **Touches:** R3 scope. The behaviour predates the diff, and R7's window (a failure recorded after
  `Task.next` finds nothing) does not cover it. R3 ("keep a run from stopping") and the Outcome
  ("a run stops only once no retryable failure is pending") can be read to include the START path.
  **Human triage:** decide whether the contract covers it. If it does, it becomes a CONFIRMED HIGH
  finding in `server.py` (human gate) and blocks. If not, it is a candidate `issues/` seed.

### Out of scope (pre-existing; recorded, not graded)
- **Eager mode lists a new retry row twice.** `Task.next(..., eager=True)` returns the same retry
  row twice: 2 entries, 1 distinct id, at base and at HEAD (reviewer probe). Fresh retry rows still
  have `schedule_time IS NULL` when `select_new` runs. `test_hold_matches_retry_selection` compares
  retries by id for this reason. Whether a client runs the task twice was not tested. Candidate
  `issues/` seed, for the human to decide.
- **`invariants.md` §3 lags `AGENTS.md`.** This diff changed `AGENTS.md` to name
  `query_retryable`/`count_retryable` in the `attempts == max_retries + 1` relationship.
  `.agents/factory/invariants.md` §3 still names only `select_failed` and `increment_group`.
  This is harness doc drift with no behavioural effect.

## Human-gate triggers

- No CONFIRMED finding, so no mandatory gate fires.
- P1 sits in `server.py`, a high-blast-radius file. If triage promotes it to CONFIRMED, the gate
  applies before `/hs-publish`.
- The `fc5755b` GOAL amendment is surfaced above for confirmation.

## Optional completeness sub-pass (separate reviewer; may see TECH.md)

- Not run (not requested).

## Human triage (2026-11-01)

Recorded at publish, on the items this review left for a human.

- **P1 (resumed run stops at START with a retry pending): out of scope.** It is not a defect in
  this change. It predates the diff (the base gives the same FINAL), and it is outside the R1/R7
  window, which opens only after `Task.next` returns nothing in LOAD. Deferred as
  [`issues/restart-pending-retry.md`](../../issues/restart-pending-retry.md). The verdict stays
  **approved**.
- **GOAL amendment `fc5755b`** (R3 rewritten, R7 and R8 added) is the accepted contract. It is the
  ruling made at the GOAL gate, logged in `GOAL.md` § Clarifications ("Stop path in scope").
- **Eager mode lists a new retry row twice:** deferred as
  [`issues/eager-retry-double-bundle.md`](../../issues/eager-retry-double-bundle.md).
- **Late duplicate result resets `retried`** (found in plan research, `PLAN.md` risks item b):
  deferred as [`issues/late-result-resets-retried.md`](../../issues/late-result-resets-retried.md).
- **`invariants.md` §3 lag:** not fixed in this cycle. The next cycle that rewrites that section
  picks it up; the PR names it as a known follow-up.

The three seeds and their `ROADMAP.md` entries landed in `1c51ebb`, after the reviewed commit
`9bb1442` and outside `spec/`. Following `hs-review`'s deferral mechanic, `review.last_reviewed_commit`
is re-pinned to `1c51ebb` with `set_phase.py --reviewed-commit` (no verdict call, so `review.cycle`
stays 1). `git diff --stat 9bb1442..1c51ebb -- . ':(exclude)spec/'` lists only `ROADMAP.md` and the
three `issues/` files; no code changed after the review.
