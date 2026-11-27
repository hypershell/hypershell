# REVIEW — De-flake `test_group_failed_task_with_retries`

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** 1f3737c6dfea5a0c6c25014f41993b2f2e627c03  ·  **Base:** develop  ·  **Date:** 2026-07-21
- **Verdict:** approved
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`

## Verification run

Commands actually executed and their outcomes (the spine of the review):

- Blind reviewer diff: `git diff develop...HEAD -- . ':(exclude)spec/'` → one non-spec file changed
  (`tests/test_groups.py`, `test_group_failed_task_with_retries`); **no `src/` change**.
- `for i in $(seq 1 10); do uv run pytest -q tests/test_groups.py::test_group_failed_task_with_retries; done`
  → 10/10 PASS (reviewer). Independent orchestrator re-run → 8/8 PASS. Local determinism strong.
- `uv run pytest -q tests/test_groups.py` → 11 passed (reviewer).
- First-principles source read for R4: `client.py:671-672` (`redirect_output = … or sys.stdout`),
  `client.py:772-773` (`Popen(stdout=self.redirect_output)`), `client.py:757-761` (`--capture` routes
  to per-task files) — task console stdout is a best-effort direct write to the inherited shared fd;
  not relayed/buffered/stored; the DB is canonical for task *state*.
- `git status --porcelain` → empty at reviewer hand-back and after the sanity pass (clean tree).

## Requirement → evidence matrix

| R-ID | Implemented by (file/commit) | Verified how | Status |
|------|------------------------------|--------------|--------|
| R1 — verify contract from authoritative task state, not scraped stdout | `tests/test_groups.py` (1f3737c) — scrape replaced with `hs list` queries; cluster stdout discarded (`rc, _, stderr = …`) | 10/10 + 8/8 target runs green; full file 11 passed | ✅ |
| R2 — terminal `exit_status==0` + expected row count independent of stdout | `hs list -c == ['5']`, `-c -t n:1 == ['3']`, `exit_status -t n:1 -S == ['0']`, `attempt -t n:1 -S == ['3']` | Test passes; retries copy parent tag so `-t n:1` matches the chain | ✅ |
| R3 — assert ordering with a robust signal, not fragile raw log-line indices, where achievable | Retained `group_idx < n2_idx` over the single-writer serialized stderr | Defensible: the group-transition line is a true happens-before the group-1 schedule (`server.py` `increment_group`); GOAL's CI evidence shows the ordering assertion *passed* (the flake was stdout), so this is not the flake source | ✅ |
| R4 — IF stdout genuinely dropped under load is a product defect, fix it | n-a — antecedent unmet; diff correctly makes no `src/` change | Source read shows console stdout is best-effort direct-to-fd echo, not committed-data loss; DB canonical → no product defect to fix | ✅ (n-a) |
| R5 — do not weaken group-retry coverage (fail → succeed → gate transition) | `assert_output('Non-zero exit status', stderr, 2)`, `-c -t n:1 -F == ['2']`, `-S` exit 0 on attempt 3, transition-count == 1 + ordering | Coverage preserved/strengthened; test passes | ✅ |
| R6 — pass reliably across CI 3.11–3.14 on constrained runners | Removes the load-sensitive stdout scrape (root cause); remaining assertions are on serialized-stream/DB state | **CI-bound** — strong local determinism (10/10 + 8/8), but hosted-runner matrix green is the maintainer's acceptance signal, unverifiable locally | ⏳ CI-bound |

Unmapped changes (possible scope creep): **none**. Every assertion maps to R1/R2/R3/R5; R4 is a
correct no-op. Other group tests that still scrape stdout were untouched (out of GOAL scope).

## Findings

**None.** No correctness bugs, no AGENTS.md invariant violations, no requirement gaps, no scope
creep. The change is test-only and touches no high-blast-radius core file.

## Human-gate triggers

None. No CONFIRMED finding (there are none), and the diff touches no high-blast-radius core file
(`data/model.py`, `server.py`, `client.py`, `core/queue|tls|fsm|thread|signal.py`,
`cluster/remote|ssh.py`) and no security/DB-lifecycle invariant — it is a test-only change.

## Notes

- **R6 is the residual risk and it is CI-bound by design.** GOAL and TECH both state the flake is not
  locally reproducible, so the true acceptance gate is a green `tests` job across Python 3.11–3.14 on
  the PR. Local evidence (root cause removed + strong determinism) is necessary but not sufficient;
  the maintainer's PR-CI sign-off closes R6.
- `hs cluster -r 3` → `max_retries=3` → `attempts=4`, but n:1 succeeds on attempt 3, so the chain
  caps at 3 rows / 2 failures — consistent with every row/failure-count assertion.
