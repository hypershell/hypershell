# GOAL — De-flake `test_group_failed_task_with_retries`

> **Origin spec.** The *what* and *why* — the locked contract `hs-review` grades against.
> The *how* lives in [`PLAN.md`](PLAN.md) and [`TECH.md`](TECH.md) (written by `hs-plan`).

- **slug:** deflake-group-retry-test
- **kind:** fix
- **appetite:** medium — diagnosis-gated. Hardening the test alone is small; if first-principles
  analysis exposes a genuine product output-loss defect, fixing that is larger (the human has
  accepted that scope — see Clarifications).

## Problem

The integration test `tests/test_groups.py::test_group_failed_task_with_retries` fails
intermittently in CI on GitHub while passing reliably on the maintainer's local machine. It was
first assumed to be a Python 3.14-only issue, but PR #52 (2026-07-20) failed it on **Python
3.11** too, which points at a load/timing sensitivity of the *hosted runner*, not a
version-specific bug. Because it cannot be reproduced locally, it must be reasoned about from
first principles from the CI evidence — a green local run tells us nothing.

A flaky test in the required matrix blocks PRs and erodes trust in the suite: contributors can't
tell a real regression from noise, and re-running CI to get a pass is not a fix.

**What the CI log actually shows** (`logs_80518241903/5_tests (3.11).txt`, lines 633–692) —
and it contradicts the original hypothesis. The *timing/ordering* assertion the test was thought
to be fragile on — `assert group_idx < n2_idx` (group 0's retried task completes before group 1's
task) — **passed**, as did the `Non-zero exit status` count (exactly 2), the group-transition
message, and `rc == success`. The **only** assertion that failed was line 143:

```
assert sorted(stdout.strip().splitlines()) == ['0', '1', '2']   # got ['0', '2']
```

Task `n:1`'s stdout (`"1"`) is missing from the captured cluster stdout — even though the task
provably *succeeded*: it failed exactly twice (`[ $TASK_ATTEMPT -eq 3 ]` false on attempts 1–2),
then on attempt 3 the `&& echo 1` branch ran (exit 0), which is the *only* path that yields the
observed group transition + success. So the subprocess produced `"1"`, but it was lost from the
captured stream. Because `"2"` (a *later*, group-1 task) is present while `"1"` (earlier) is
absent, this is not tail-truncation at shutdown — it is a specific line dropped under load.

The test is therefore fragile in two ways: (1) it scrapes task stdout from the cluster process
and assumes every task's output is captured completely and in order, which does not hold under
CI concurrency/load; and (2) it verifies group-phase ordering by comparing raw log-line indices,
which is inherently timing-sensitive. The open question — and the reason this needs real
diagnosis — is whether the missing line is merely a test-harness capture assumption, or a genuine
product defect (task output dropped under load).

## Outcome / vision

`test_group_failed_task_with_retries` passes reliably across the full CI matrix (Python
3.11–3.14) on constrained/lagging hosted runners, **without weakening what it verifies** about
group-based retry behavior. The true root cause of the dropped `"1"` is understood from first
principles from the CI evidence. If that root cause is a fragile test assumption, the test is
rebuilt to assert on authoritative task state; if it is a genuine product defect (task output
lost under concurrent load), that defect is root-caused and fixed rather than masked. The
maintainer's acceptance signal is a green run of this job on the PR's CI (it cannot be confirmed
locally).

## Acceptance criteria (the contract)

- **R1** — The test SHALL verify the group-retry contract (a failing task is retried within its
  group until it succeeds, and the group advances only afterward) from **authoritative task
  state** — database rows, `exit_status`, attempt/row counts, completion — as the primary source
  of truth, rather than relying on scraped process stdout for correctness.
- **R2** — WHEN a failing task is retried until it succeeds, the test SHALL confirm the task
  terminally succeeded (`exit_status == 0`) and that the expected number of task rows exist
  (original attempt + retries), independent of what appears on captured stdout.
- **R3** — The test SHALL still assert the ordering guarantee (group 1 does not begin until the
  retried task in group 0 completes) using a signal robust to CI load, not a fragile comparison
  of raw log-line indices, wherever that is achievable without losing the guarantee.
- **R4** — IF first-principles diagnosis determines task stdout is genuinely dropped or lost
  under concurrent load (a product defect, not a test assumption), THEN that defect SHALL be
  root-caused and fixed — not merely hidden by changing an assertion.
- **R5** — The fix SHALL NOT weaken the behavioral coverage of group-based retries: the retried
  task must still be shown to fail, then succeed, and gate the group transition.
- **R6** — WHEN run in CI across Python 3.11–3.14 on constrained runners, the test (and any
  product change made under R4) SHALL pass reliably, with no timing- or capture-induced flakes.

## Non-goals (no-gos)

- De-flaking other timing-sensitive tests (e.g. `test_group_parallel_execution_within_group`'s
  wall-clock `elapsed > 10` assertion). If the same robust pattern trivially applies, noting it is
  welcome, but reworking those tests is a possible follow-up, not this unit of work.
- Changing group-gating / scheduler advancement semantics or the retry model (new-row,
  `attempt+1`, group stall-on-failure). Those invariants are load-bearing and out of scope.
- Broadly refactoring how task stdout is routed or printed, beyond the minimum required to fix a
  *confirmed* output-loss defect under R4.
- Introducing a general test-retry / flaky-test plugin or reruns to paper over instability.

## Clarifications

- **Q:** If root-cause analysis shows task stdout is genuinely dropped under load (a real product
  defect, not just a fragile test assumption), is fixing the product defect in scope for this
  fix? — **A:** Yes. Fix the product bug too; a larger appetite is accepted. Test-hardening that
  masks a real defect is not acceptable. (resolved 2026-07-20)
- **Note (reframing):** The CI failure is *missing task stdout*, not the group-phase
  timing/ordering assertion (which passed). The original "it's a timing test" framing is only
  partly right — the stdout-scraping assumption is the proximate failure; the log-index ordering
  check is a secondary fragility.

## Related materials

- Failing CI job: PR #52, job "tests (3.11)"; local log
  `~/Downloads/logs_80518241903/5_tests (3.11).txt` (failure block: lines 633–692).
- Test under repair: `tests/test_groups.py:116` (`test_group_failed_task_with_retries`).
- Behavioral contract to preserve: AGENTS.md — "Task lifecycle contract" (retry model,
  `CANCEL_STATUS`, group-gating via `Task.increment_group`) and "Concurrency model".
- Likely code touchpoints for R4 diagnosis (to be confirmed by `hs-plan`, do not presume):
  `client.py` (`TaskExecutor`, subprocess capture), `server.py` (`Scheduler` writeback/console
  print), `core/queue.py` (`completed` queue), `data/model.py` (task-state queries).
- Test harness: `tests/__init__.py` (`main`, `main_lines`, `assert_output`, `create_taskfile`),
  `tests/conftest.py` (`temp_site`).
