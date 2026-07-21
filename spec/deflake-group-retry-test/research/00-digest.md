# Research digest — deflake-group-retry-test

Consolidated decisions from the three briefs. Where briefs diverged, a single recommendation is
given here. Backing detail: [`01-output-path.md`](01-output-path.md),
[`02-test-harness.md`](02-test-harness.md), [`03-design-intent-history.md`](03-design-intent-history.md).

## Verdict: no product defect — the fix is test-only

All three lines of evidence converge: the missing `"1"` is **not** a HyperShell defect. Console
task-output is a documented best-effort echo, not an authoritative record.

- **No relay/buffer/store in HyperShell code.** Task subprocesses are spawned with
  `Popen(..., stdout=self.redirect_output)` where, with the default `capture=False`,
  `redirect_output` **is** `sys.stdout` (`client.py:671`, `:772-774`). Output bytes go straight
  from the child to the inherited fd. No HyperShell thread ever re-prints task stdout; the
  `completed`-queue result carries only metadata (`exit_status`, timing), never output bytes
  (`server.py:456-466`). So there is no code path that could drop a line.
- **`DEFAULT_NUM_THREADS = 1`** (`client.py:1067`) — this test spawns a *single* executor writing
  `0\n`, `1\n`, `2\n` sequentially to one ordered pipe that the test's `subprocess.run(stdout=PIPE)`
  (`tests/__init__.py:30`) drains to EOF. In-process, a mid-stream line **cannot** be lost. The
  loss is therefore at the OS/stdio/CI-runner boundary — environmental, and it can't be pinned from
  code alone (residual open question, see below).
- **Docs say so.** `docs/getting_started.rst:74-75`: task stdout/stderr are "joined and written out
  **directly**" — no completeness/ordering claim; `--capture` is the way to keep per-task output.
  `docs/security.rst:334`, `docs/alternatives.rst:45`: "the database is the source of truth."
- **Not a regression.** Test added in `0844bf1` (2025-12-23, task-groups feature), body unchanged
  since; the console-echo path has no recent churn; no prior flaky/timing/output-loss commits exist.
- **CI shape rules out a test-runner fix.** Matrix Py 3.11–3.14 on `ubuntu-latest`, invoked
  `uv run --python <ver> pytest -v` — **no xdist, no rerun plugin**, serial. The load is the
  constrained hosted runner plus each integration test's own full-cluster concurrency.

**Consequence for GOAL R4:** its antecedent ("IF diagnosis determines task stdout is genuinely
dropped by a product defect") is **not met**. The correct, contract-honoring resolution is
test-hardening (R1/R2/R3/R5/R6). The user's "fix the product bug too" decision stands — there is
simply no product bug to fix, and we say so transparently rather than silently doing test-only.

## The fix: assert on authoritative task state, not scraped stdout

Replace the fragile `assert sorted(stdout.strip().splitlines()) == ['0','1','2']`
(`test_groups.py:143`) with database assertions via `hs list` / `hs info`. Keep the robust
stderr/log assertions (the log stream is single-writer and passed in CI).

**Filter semantics verified** (`task.py:515-524`) — plain `exit_status` predicates, **no `retried`
exclusion**, so superseded rows are counted:

| `-F/--failed` | `-S/--succeeded` | `-C/--completed` | `-R/--remaining` | `-X/--cancelled` |
|---|---|---|---|---|
| `exit_status != 0` | `exit_status == 0` | `exit_status != null` | `exit_status == null` | `== CANCEL_STATUS` |

Retry rows copy the parent's `tag`/`group`/`fingerprint`/`source` (`model.py:620-627`), so `-t n:1`
matches the whole 3-row chain. The authoritative assertion menu:

| Assertion | Proves | Value |
|---|---|---|
| `hs list -c` | total rows | `5` (n:0=1, n:1=3, n:2=1) |
| `hs list -c -t n:1` | chain length | `3` (attempts 1,2,3) |
| `hs list -c -t n:1 -F` | failures | `2` (attempts 1,2 exit 1) — matches "Non-zero exit status" ×2 |
| `hs list exit_status -t n:1 -S` | terminal success | `['0']` |
| `hs list attempt -t n:1 -S` | **succeeded on 3rd attempt** | `['3']` — key oracle |
| `hs list -c -R` | nothing incomplete | `0` |
| `hs list exit_status -t n:2 -S` + `hs list group -t n:2` | n:2 ran in group 1 | `['0']`, `['1']` |

**Content proof is unnecessary** (`--capture` not needed): the command
`[ $TASK_ATTEMPT -eq 3 ] && echo 1` can only exit `0` on attempt 3 — the `&&` chain means
`exit_status == 0` for that task *is* proof `echo 1` ran. So `exit_status==0 ∧ attempt==3` proves
n:1 produced its output, without touching stdout. This also preserves R5 (behavioral coverage is
stronger, not weaker, than the old scrape).

**Ordering (R3):** keep the existing `group_idx < n2_idx` check — it reads **stderr** (the
single-writer log stream), which is robust and passed in CI; the fragility was stdout, not stderr.
Group-gating semantics already guarantee group 1 cannot start until group 0 completes, so the
presence of the transition message + n:2's success is itself an ordering proof.

## Scope guards (from GOAL non-goals)

The same stdout-scraping assumption exists across ~15 other assertions
(`test_groups.py:39,61,82,167,186,224,287`, `test_cluster.py:248,257`,
`test_restart.py:35,54,102,118,157`, `test_submit_json.py:275,358`, `test_cancel.py:68,93`) and the
wall-clock `elapsed > 10` at `test_groups.py:292`. These are **out of scope** here (GOAL non-goals) —
this fix is the pilot; the pattern is a follow-up.

## Residual open question

The precise CI byte-loss trigger (why a single sequential-writer pipe drops one mid-stream line on a
loaded hosted runner) could not be determined from code. It does **not** need to be: the rewritten
test does not depend on that stream at all, so it is immune regardless of the trigger. Final
acceptance is a green CI run on the PR (the flake is not locally reproducible).
