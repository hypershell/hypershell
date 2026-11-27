# PLAN — De-flake `test_group_failed_task_with_retries`

> **Status:** Draft for review · **Last updated:** 2026-07-20
> **Authoritative technical design.** The *how*. Vision/contract is [`GOAL.md`](GOAL.md);
> the phased executable roadmap is [`TECH.md`](TECH.md). Backing detail is in
> [`research/`](research/). Every design element traces to a GOAL R-ID.

## 1. Summary

First-principles diagnosis (three read-only research briefs, see
[`research/00-digest.md`](research/00-digest.md)) shows the CI failure is **not** a HyperShell
defect: task console output is a documented best-effort echo written *directly* to the inherited
stdout fd, with no HyperShell code path that buffers, relays, or stores it — the database is the
source of truth. The missing `"1"` is lost at the OS/CI-runner boundary, and the test was wrong to
treat merged console stdout as a complete, ordered oracle. The fix is therefore **test-only and
small**: rebuild `test_group_failed_task_with_retries` to assert on authoritative task state via
`hs list`, keeping the robust single-writer *stderr* log assertions. This honors the human's
"fix the product bug too" decision — we investigated and found no product bug (GOAL R4's antecedent
is unmet), and say so rather than silently narrowing scope.

## 2. Design

Single-file change: `tests/test_groups.py`, function `test_group_failed_task_with_retries`
(lines 115–146). No `src/` change — the product behaves correctly and as documented.

**Keep** (robust — read `stderr`, the single-writer serialized log stream; all passed in CI):
- `rc == exit_status.success`.
- `assert_output(r'Non-zero exit status', stderr, 2)` — the two failed attempts.
- `assert_output(r'Completed task group 0 - starting task group 1', stderr, 1)` — the transition.
- The ordering proof `group_idx < n2_idx` (index of the group-transition log line vs. n:2's
  `Completed task (<id>)` log line). Both come from `stderr`; group-gating already guarantees this
  ordering, so it is not timing-fragile.

**Replace** the fragile stdout scrape (`assert sorted(stdout.strip().splitlines()) == ['0','1','2']`,
`test_groups.py:143`) and the trailing count check with authoritative `hs list` queries against the
per-test SQLite DB (the server has written back by the time `hs cluster` returns):

- `hs list -c` → `5` (n:0 = 1 row, n:1 = 3-row retry chain, n:2 = 1 row).
- `hs list -c -t n:1` → `3` (chain length; retries copy the tag, `model.py:620-627`).
- `hs list -c -t n:1 -F` → `2` (superseded failed rows; `-F` = `exit_status != 0`, `task.py:516`).
- `hs list exit_status -t n:1 -S` → `['0']` and `hs list attempt -t n:1 -S` → `['3']` — the key
  oracle: n:1 terminally **succeeded on its 3rd attempt**. Because the task is
  `[ $TASK_ATTEMPT -eq 3 ] && echo 1`, a `0` exit is only reachable when `echo 1` runs, so this
  proves the output was produced **without** scraping stdout (no `--capture` needed).
- `hs list exit_status -t n:2 -S` → `['0']` and `hs list group -t n:2` → `['1']` — n:2 ran, in
  group 1.
- `hs list -c -R` → `0` — nothing left incomplete (no interrupted/orphaned rows).

A short declarative comment will explain *why* stdout is not asserted (best-effort concurrent echo;
DB is source of truth) so the next reader does not "restore" the scrape.

### Requirement → design map

| R-ID | Design element(s) that satisfy it |
|------|-----------------------------------|
| R1   | Correctness assertions move to authoritative DB state via `hs list` (counts, `exit_status`, `attempt`, `group`); stdout is no longer an oracle. |
| R2   | `hs list exit_status/attempt -t n:1 -S` → `['0']`/`['3']` + `hs list -c` → `5` prove terminal success and row count independent of captured stdout. |
| R3   | Ordering kept via the `stderr` group-transition vs. n:2-completion log indices (single-writer stream), backed by group-gating semantics — robust to runner load. |
| R4   | Diagnosis (research 00–03) determined there is **no** product output-loss defect; antecedent unmet, so no `src/` change. Recorded transparently, not silently. |
| R5   | DB assertions are strictly stronger than the old scrape (chain length, exact success attempt, failure count, group), so retry coverage is preserved/deepened. |
| R6   | Test no longer depends on the non-deterministic merged stdout stream, removing the CI-load flake at its source; acceptance is green CI on the PR across 3.11–3.14. |

## 3. Invariant gate (AGENTS.md constitution check)

Checked against [`invariants.md`](../../.agents/factory/invariants.md) before research and again
after this design. Touched sections and compliance:

- **§1 Task lifecycle** — assertions read state via the nullable-column predicates exactly as
  `hs list` exposes them (`-C` = `exit_status != null` = done; `-R` = `exit_status == null` =
  remaining; `-X` = `CANCEL_STATUS`). We add no new query logic and put none in FSM code — we only
  *call the CLI*. Honored.
- **§3 Retry model** — assertions rely on the new-row/`attempt+1`/tag-copy chain (3 rows for n:1)
  and read `attempt`/`exit_status`; nothing mutates the chain or the `attempts == max_retries + 1`
  relationship. Honored (read-only).
- **§12 Project conventions** — the test stays `@mark.integration`; uses `cmdkit.app.exit_status`
  constants (already imported); uses `main`/`main_lines`/`assert_output` helpers. No CLI/behavior
  change → no `docs/_include` or `share/` updates required. Honored.

§4–§11 (server modes, concurrency, shutdown ordering, resources, signals, transport, config,
cluster) are **not touched** — no `src/` change.

### Deviation justifications

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| —         | —         | — |

None. (Considered and rejected — see Risks: adding `--capture` to prove literal `"1"`.)

## 4. Rabbit holes (resolved)

- **Is the missing line a real product defect (dropped task output)?** → No. HyperShell never
  buffers/relays/stores console output; with `DEFAULT_NUM_THREADS = 1` a single executor writes
  sequentially to one pipe drained to EOF, so no in-process loss is possible; docs declare console
  echo best-effort and the DB canonical. ([`research/01-output-path.md`](research/01-output-path.md),
  [`research/03-design-intent-history.md`](research/03-design-intent-history.md)).
- **What can we assert on instead, and are the filters retried-aware?** → `hs list` filters are
  plain `exit_status` predicates (no `retried` exclusion), so superseded rows are counted; a full
  verified assertion menu exists. `main()` runs the CLI as a subprocess against the per-test DB, so
  `hs list` after `hs cluster` sees the writeback. ([`research/02-test-harness.md`](research/02-test-harness.md)).
- **Regression or environment?** → Environment. No churn in the test or output path; CI is serial
  (no xdist), so it is runner load, not a code change. ([`research/03-design-intent-history.md`](research/03-design-intent-history.md)).

## 5. Risks & open questions

- **Not locally reproducible.** The flake is CI-runner-bound; a green local run proves *determinism*
  (no stdout dependence) but not the CI fix. **Mitigation:** the rewritten test has zero dependence
  on the flaky stream, so it is immune by construction; final acceptance is a green CI run on the PR
  (the human's stated gate).
- **Rejected alternative — `--capture` for literal content proof.** We could run with `--capture`
  and assert `hs info <id> --stdout == '1'` to prove the exact bytes. Rejected: it adds surface for
  no gain — `exit_status == 0 ∧ attempt == 3` already proves `echo 1` ran, and `--capture` changes
  the scenario under test. Noted so a reviewer knows it was considered.
- **Residual unknown (non-blocking):** the exact OS/runner mechanism that drops one sequential-pipe
  line is undetermined; it does not affect the fix.
- **Out of scope (GOAL non-goals), flagged for follow-up:** ~15 other assertions scrape task stdout
  the same way (`research/00-digest.md` lists them) and `test_groups.py:292` uses a wall-clock
  bound. This fix is the pilot; a follow-up can apply the same state-based pattern.

## 6. Verification strategy

- **Primary:** run the rewritten test in a repeat loop to confirm determinism under local load, plus
  the full group suite:
  `uv run pytest -v tests/test_groups.py::test_group_failed_task_with_retries --count=20` is *not*
  available (no `pytest-repeat`); instead loop in shell:
  `for i in $(seq 1 20); do uv run pytest -q tests/test_groups.py::test_group_failed_task_with_retries || break; done`
  then `uv run pytest -q tests/test_groups.py`.
- **Cross-check the oracle by driving the real CLI** in a throwaway site (mirrors the test's DB
  assertions), e.g. via `.agents/factory/bin/temp_site.sh` running the 3-task file with `-r 3` and
  then the `hs list` queries above, confirming `5 / 3 / 2 / ['0'] / ['3'] / 0`.
- **Full regression:** `uv run pytest -q` locally.
- **Acceptance:** green `tests` job across Python 3.11–3.14 on the PR CI.

---

*Backing research: [`research/00-digest.md`](research/00-digest.md).*
