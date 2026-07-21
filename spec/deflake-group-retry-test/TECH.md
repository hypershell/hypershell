---
slug: deflake-group-retry-test
title: De-flake test_group_failed_task_with_retries
kind: fix
appetite: small
status: in_review
branch: fix/deflake-group-retry-test
base: develop
current_phase: done
last_updated: '2026-07-20'
phases:
- id: P1
  name: Rebuild the test on authoritative DB state
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  depends_on: []
  parallel: false
  hammerable: false
  hill: downhill
  verify: for i in $(seq 1 15); do uv run pytest -q tests/test_groups.py::test_group_failed_task_with_retries
    || exit 1; done && uv run pytest -q tests/test_groups.py
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — De-flake `test_group_failed_task_with_retries`

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/deflake-group-retry-test/TECH.md`).

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Why one phase

First-principles diagnosis (research 00–03) established there is **no product defect**: task console
output is a documented best-effort echo (`Popen(stdout=sys.stdout)`, no relay/buffer/store;
`DEFAULT_NUM_THREADS = 1`; docs declare the DB canonical). GOAL R4's antecedent is unmet, so there
is **no `src/` change** — only the fragile test is rebuilt onto authoritative task state. That is a
single, independently-verifiable vertical slice.

## Conventions (apply to the phase)

- Commit conventions, style, and invariants come from [`AGENTS.md`](../../AGENTS.md); curated
  footguns in [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md).
- One atomic commit containing **both** the test change and this `TECH.md` state change. Subject:
  `[fix] Build deflake-group-retry-test P1: …`. **No `Co-Authored-By` trailer.**
- No CLI/behavior change → **no** `docs/_include/*.rst` or `share/` updates (invariant §12 does not
  trigger here).

---

## Phase P1 — Rebuild the test on authoritative DB state
**Satisfies:** R1, R2, R3, R4, R5, R6 · **Depends on:** —
**Goal:** `tests/test_groups.py::test_group_failed_task_with_retries` verifies the group-retry
contract entirely from authoritative task state + the single-writer stderr log stream, with **zero**
dependence on the non-deterministic merged task-stdout stream — removing the CI-load flake at its
source while strengthening coverage.

- [x] Edit **only** `tests/test_groups.py`, function `test_group_failed_task_with_retries`
      (lines 115–146). Keep the task list, `create_taskfile`, and the `hs cluster … -r 3` invocation
      unchanged (preserve the exact scenario).
- [x] **Keep** the robust `stderr`/`rc` assertions: `rc == exit_status.success`;
      `assert_output(r'Non-zero exit status', stderr, 2)`;
      `assert_output(r'Completed task group 0 - starting task group 1', stderr, 1)`; and the
      `group_idx < n2_idx` ordering proof (both indices come from `stderr`, the serialized log).
- [x] **Remove** the fragile stdout scrape at line 143
      (`assert sorted(stdout.strip().splitlines()) == list(map(str, range(len(tasks))))`) and the
      trailing `hs list -c == ['5']` line; replace with authoritative `hs list` queries (via
      `main`/`main_lines`, all against the per-test DB the server has already written back):
  - [x] `main_lines(['hs','list','-c'])` → `['5']` (n:0=1, n:1=3-row chain, n:2=1).
  - [x] `main_lines(['hs','list','-c','-t','n:1'])` → `['3']` (retry chain length).
  - [x] `main_lines(['hs','list','-c','-t','n:1','-F'])` → `['2']` (superseded failed rows;
        `-F` = `exit_status != 0`).
  - [x] `main_lines(['hs','list','exit_status','-t','n:1','-S'])` → `['0']` **and**
        `main_lines(['hs','list','attempt','-t','n:1','-S'])` → `['3']` — key oracle: n:1 succeeded
        on attempt 3, which (given `[ $TASK_ATTEMPT -eq 3 ] && echo 1`) proves `echo 1` ran without
        scraping stdout.
  - [x] `main_lines(['hs','list','exit_status','-t','n:2','-S'])` → `['0']` **and**
        `main_lines(['hs','list','group','-t','n:2'])` → `['1']` (n:2 ran, in group 1).
  - [x] `main_lines(['hs','list','-c','-R'])` → `['0']` (nothing incomplete).
  - [x] Use `NO_OUTPUT` for the stderr slot in `main_lines` comparisons, matching existing tests.
- [x] Add a short **declarative** comment stating *why* stdout is not asserted (best-effort
      concurrent console echo; the database is the source of truth) so a future reader does not
      reinstate the scrape. Do **not** embed spec R-IDs (AGENTS.md comment rule).
- [x] Confirm the exact `hs list` field names / flags against `src/hypershell/task.py`
      (`exit_status`, `attempt`, `group` positionals; `-t/--with-tag`, `-c/--count`, `-F/--failed`,
      `-S/--succeeded`, `-R/--remaining`) before relying on them — fix any drift.
- **Verify:** `for i in $(seq 1 15); do uv run pytest -q tests/test_groups.py::test_group_failed_task_with_retries || exit 1; done && uv run pytest -q tests/test_groups.py`
  (15 consecutive green runs prove local determinism; the full groups file guards against
  regressions). Optionally cross-check by driving the CLI in a throwaway site:
  `.agents/factory/bin/temp_site.sh sh -c "printf 'echo 0  #HYPERSHELL: n:0 group:0\n[ \$TASK_ATTEMPT -eq 3 ] && echo 1  #HYPERSHELL: n:1 group:0\necho 2  #HYPERSHELL: n:2 group:1\n' > t.in && uv run hs cluster t.in -r 3 >/dev/null 2>&1; uv run hs list -c; uv run hs list attempt -t n:1 -S"`
  → expect `5` then `3`.
- **Touches:** `tests/test_groups.py` (only), `spec/deflake-group-retry-test/TECH.md` (state).

---

## How `hs-build` drives this

1. `next_phase.py` prints P1 as the next actionable phase.
2. Pre-flight: clean tree, on `fix/deflake-group-retry-test`, `develop` reachable.
3. Execute every `[ ]` above (consult `PLAN.md` / `research/` for detail).
4. Run the `verify:` command — never advance on a checkbox alone.
5. Mark P1 `done`, `--touch`; one `[fix]` commit; stop and report.

**Acceptance is CI-bound:** the flake is not locally reproducible, so the true gate is a green
`tests` job across Python 3.11–3.14 on the PR (the human's stated sign-off).
