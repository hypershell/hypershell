# REVIEW — A relay run ends within seconds of its last result

> Adversarial QA by `hs-review`, run in an isolated/clean context. The correctness pass grades the
> branch diff against [`GOAL.md`](GOAL.md) + the AGENTS.md invariants **only** — it does not see
> `PLAN.md`/`TECH.md` (avoids grading-its-own-homework / plan-sycophancy). Every finding cites an
> **executed** command, not an assertion.

- **Reviewed commit:** `8690deaf`  ·  **Base:** `origin/develop` (`d9901cf0`; merge base `f3fb4dd2`)
  ·  **Date:** 2026-11-21
- **Verdict:** changes-requested. There is one CONFIRMED finding, F1 (LOW): the `hs cluster` one-line
  `-w` help says more than the code does. Separately, the branch does not merge cleanly with
  `develop` (#114, see *Merge status*), so it has to be brought up to date before it can be
  published. The code changes for R1 to R7 hold up under every check run.
- **Cycle:** 1 of ≤3 — mirrors `review.cycle` in `TECH.md`.

## How this cycle was run

- **Blind correctness pass:** one fresh reviewer. It was given:
  - `GOAL.md`, with its GOAL-gate block;
  - the diff `git diff origin/develop...HEAD -- . ':(exclude)spec/'`;
  - the base copies of `invariants.md`, `review-rubric.md` and `AGENTS.md`;
  - the binding readings from the gates:
    - R1's flush when idle reaches every `hs client` with `-b` above 1, mid-run included;
    - the GOAL's cut was not taken: the receiver reads the stop predicate's two counts about once
      per run, and the back-off is unchanged while work remains;
    - with `hs update` during the final back-off, the run ends when that back-off ends, as today;
    - `share/man` is regenerated only at release, and no completion change is needed;
    - R7 is test-only and its red proof stands at `94a45f6c`; P7 re-proves R1 and R3 only.

  It did not read `PLAN.md`, `TECH.md`, `META.md` or `research/`, and its searches excluded `spec/`.
  It was allowed to read only `evidence/` and `drive/` from this cycle's `spec/` folder, plus the
  measuring tools the GOAL names in `spec/relay-hardening/drive/` and `spec/relay/drive/`, because R5
  is graded from them. It was told not to start `tail_run.py`, since every started run counts
  against R5's budget.
- **Orchestrator:**
  - checked contract drift and merge status;
  - re-graded every finding;
  - ran the new tests, the docs build in its own environment, and the help output;
  - checked that R5's records were made on a commit whose code is HEAD's.
- **Contract drift:** `GOAL.md` changed only in the shaping commit (`d1a0fea8`) and at the GOAL gate
  (`6f4e809f`), which only turned **Proposed** into **Confirmed** and added the ruling block.
- **Environments:** CPython 3.14.6 for tests and drives, run serially. The docs were built with
  CPython 3.12. Every CLI drive used `.agents/factory/bin/temp_site.sh`, bound to 127.0.0.1, with
  keys passed only through the environment.

## Verification run

- **New and changed tests at HEAD:**
  - `test_finish_wake.py`, `test_client_return.py`, `test_client_request.py` and
    `test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused` → 27 passed in 8 s
    (orchestrator).
  - The reviewer's run of the first three files → 24 passed.
  - The R7 unit tests plus `test_site_key_reaches_site_clients_in_their_environment_only` and
    `test_site_clients_keep_the_relay_config_file_without_relay_settings` → 8 passed.
- **Scheduler and receiver modules at HEAD** (`contended*`, `group_order`, `retry_gate`,
  `server_fsm`, `stream_input`, `writeback*`, `zone_dispatch`) → 181 passed, 45 skipped (the
  PostgreSQL cases; PostgreSQL was not run).
- **Whole suite:** not re-run by this review. No source has changed since the build's last whole-suite
  run, and CI runs on this head.
- **Red on the base** (scratch export of `f3fb4dd2` with HEAD's tests):
  - R1: `test_idle_client_returns_what_it_holds` → `AssertionError: 0 of 3 results returned within
    10 s (bundle wait 60 s)`.
  - R3: `test_finishing_result_ends_a_long_back_off` → `run still waiting out its back-off after
    15.0 s`.
  - The new unit tests fail on the base only because the names they use are missing (`TaskCount`,
    the receiver's `scheduler=`). The integration tests are the red proofs.
  - R7 at `94a45f6c` (scratch export): `test_scans_skip_a_process_whose_read_is_refused[AccessDenied]`
    raised `psutil.AccessDenied`, which is the right reason.
- **R3 race probes** (reviewer, scratch only). Each probe landed the finishing result through the
  receiver, in each window of a pass:
  - after `submission_complete`, after `Task.count`, after `count_remaining`, and after
    `increment_group`;
  - also a result that was first DEFERRED and then APPLIED when presented again.

  Each pass ended in under 1 s and the next pass was FINAL (5 passed).
- **R3 read count** (reviewer). In an in-process 500-task run the receiver called `results_landed`
  63 times. It read `count_remaining` twice and `count_retryable` once, which matches the gate's
  "about once per run".
- **Queries while work remains:** `load_bundle` issues `count() > 0` → `count_remaining` →
  `count_retryable` with the same short-circuits as on `f3fb4dd2`, and a pass that loads anything
  disarms (reviewer, by comparing the code).
- **CLI drives, head against base** (reviewer):

  | Drive | Head | Base | Notes |
  |---|---|---|---|
  | `hs cluster -N2 -b4 -w30 -Q30`, 5 × `sleep 0.3` | tail 1.05 s, wall 2.51 s | tail 60.49 s, wall 62.06 s | same rows |
  | `hs server -b4 -Q30` + `hs client -N2 -b4 -w30` | tail 1.12 s | — | 5 of 5 rows exit 0 |
  | `--no-db`, same shape | wall 2.55 s | — | — |
  | one `sleep 12`, `-Q30` (long back-off) | tail 0.20 s | tail 3.22 s | — |
  | `-r1`, last task fails | tail 1.17 s | tail 5.13 s | identical rows; time to first retry unchanged |
  | groups, `--restart` | tail 1.04 s | tail 2.03 s | identical rows |
  | failed group, nothing elsewhere | tail 1.13 s | tail 2.18 s | identical rows |
  | failed group with a task waiting elsewhere (halt) | tail 2.14 s | tail 2.17 s | the halt is the same; the waiting task never runs on either |
  | `--eager` | tail 1.11 s | — | 3 attempts, correct |
  | stream input, second line after 6 s | tail 0.53 s | — | the late line still ran |
  | `--forever` | still running 8 s after the last result | same | stopped by SIGTERM to its own process group |

- **Docs:** `sphinx-build -W --keep-going -q -E -b html docs …` exited 0 at HEAD (orchestrator, own
  environment; reviewer, separately) and on the merged tree (reviewer).
- **Help output** (orchestrator, throwaway site). `hs cluster --help` and `hs client --help` both
  print `-w, --bundlewait … Wait before flushing tasks, none once idle; …` (see F1).
- **R5 integrity:**
  - The six head records name `dd0b227d` with `src_changed: false`, and
    `git diff dd0b227d HEAD -- src/ tests/ docs/` is empty (orchestrator).
  - `tail_bound.py` re-run against `evidence/head/` with the baseline → PASS, and the table it writes
    is byte-identical to [`evidence/tails.md`](evidence/tails.md) (reviewer).
  - Run times leave no room for an unkept run, and every run is complete: 3 at each size, 6 of the
    10 allowed.

## Requirement → evidence matrix

| R-ID | Implemented by | Verified how | Status |
|------|----------------|--------------|--------|
| R1 | `client.py`:<br>• `TaskCount` (:160)<br>• raised when a reply brings tasks (:259)<br>• lowered as the collector takes each (:433)<br>• idle branch in `check_bundle` (:447)<br>• wired in `ClientThread` (:1347–1353), the only place these are built, so site clients and local mode get it | • `test_client_return.py` passes<br>• red on `f3fb4dd2`<br>• `hs cluster` / `hs client` drives (tail 1.05 s against 60.49 s) | ✅ |
| R2 | `check_bundle` keeps size first and wait last; `finalize` unchanged. Every executor path reaches the outbound queue (template error, resource error, signal, walltime), and the count is raised before tasks are unpacked, so it cannot reach 0 while a received task is unreturned. | Unit tests for held, full, wait passed, no count, finalize | ✅ |
| R3 | `server.py`:<br>• arm before the counts in `load_bundle` (:270–281)<br>• disarm on load (:265)<br>• `Scheduler.results_landed` (:352–368)<br>• receiver call (:732–734)<br>• wiring (:1254; no scheduler with `--no-db`) | • `test_finish_wake.py` passes<br>• red on `f3fb4dd2`<br>• race probes in every window<br>• read count<br>• back-off drive (0.20 s against 3.22 s) | ✅ |
| R4 | Unchanged queries and order while work remains. `--forever` and open input never arm. | • forever / open-input / retry-left / work-left unit tests<br>• scheduler-module tests<br>• retry, groups, failed-group, halt, `--eager`, stream and `--forever` drives | ✅ |
| R5 | `evidence/head/` (6 runs at `dd0b227d`), `evidence/tails.md`, `drive/tail_bound.py` | • `tail_bound.py` PASS<br>• tails: 2000 tasks 0.72–0.79 s; 2004 tasks 0.69–1.65 s (baseline 1.58–1.78 s and 6.75/7.45 s)<br>• source identical to HEAD | ✅ |
| R6 | `client_help`, `cluster_help`, `server_help`; `config_param_ref` (`client.bundlewait`, `relay.bundlewait`, `server.poll`) with `versionchanged`; `docs/cli/client\|cluster\|server.rst`; the `-w` one-liners in `client.py` and `cluster/__init__.py` | • Read against the code<br>• `-W` build exits 0 | ⚠️ F1 |
| R7 | `tests/test_relay_workload.py`: `read_process` and `UNREADABLE` (AccessDenied, NoSuchProcess, ZombieProcess) in `scan_site`, `scan_site_key` and `client_environments`; same counts and "no key seen" checks | • 8 passed at HEAD<br>• red at `94a45f6c` (P1) | ✅ |

Unmapped changes (possible scope creep): none.
- The `ROADMAP.md` and `issues/*` status changes are the adoptions the GOAL's first clarification
  records.
- The §3 edits to `AGENTS.md` and `invariants.md` keep them in step with R3. Read against the code,
  the new text is accurate: arm before the counts once the input has ended; the receiver reads the
  same counts and wakes; the next pass stops.

## Findings

Severity: **CRITICAL** (any AGENTS.md invariant violation is auto-CRITICAL) · **HIGH** · **MEDIUM** ·
**LOW**. Verdict: **CONFIRMED** (reproduced) vs **PLAUSIBLE** (suspected, needs human triage). Only
CONFIRMED findings auto-loop to `hs-build`.

### F1 [LOW/CONFIRMED] `hs cluster -w` one-line help says nothing waits once idle, but its submit thread still waits
- **Where:** `src/hypershell/cluster/__init__.py:87`
- **The problem:**
  - In `hs cluster`, `-w` defaults to `submit.bundlewait` (`:153–154`) and sets the submit thread's
    bundle wait as well as each client's.
  - The new one-liner says "Wait before flushing tasks, none once idle". The "none once idle" part
    holds only for the clients.
  - A quiet input stream still has its lines held by the submit thread, and `hs submit`'s bundling
    is a Non-goal.
- **Failure scenario:** `hs cluster -N 1 -b 10 -w 30 -Q 30` reading stdin, with one line written at
  t=0 and stdin closed at t=8 s. The line was submitted at 0.27 s but scheduled at 8.02 s: it waited
  for the end of input, not "none".
- **What is correct:**
  - The long help (`docs/_include/cluster_help.rst`) is precise: "A client with nothing left to run
    returns what it holds at once".
  - `hs client`'s one-liner (`client.py:1598`) is accurate, because there `-w` is the client's
    alone.
- **Evidence:**
  - reviewer drive on head: `{"submitted_after": 0.27, "scheduled_after": 8.02, "stdin_closed_at": 8.0}`;
  - orchestrator: `temp_site.sh sh -c 'uv run hs cluster --help | grep -- --bundlewait'` prints the
    line above.
- **Touches:** R6 (the `hs cluster` help must say that *a client* with nothing left to run returns
  what it holds at once). The fix is wording only: credit the idle return to the clients, as the long
  help does. No source logic changes.

**Considered and dropped:**
- **The receiver dies if its new count read raises.** The receiver already carries the same exposure
  through `Task.apply_results`, so this is not new.
- **A PostgreSQL session left idle in a transaction after the receiver's counts.** The scheduler's own
  back-off already does this, so this is not new.
- **A cancel or delete during the final back-off gives no wake.** Ruled at the plan gate (D2).
- **A flush when idle mid-run.** Ruled at the GOAL gate.

**Coverage note (not a finding):**
- The tests cover a finishing result that lands after the counts (`report_held`), but none covers
  one landing between `count_remaining` and the assignment of `finishing`.
- The reviewer's scratch probe of that window passes, and the code handles it: the receiver either
  sees the `(mark, 0)` arm and reads the counts, or sees the full arm, with its own increment counted
  after the mark.

## Merge status

- **Against `origin/develop` `d9901cf0`:**
  `git merge-tree --write-tree --name-only origin/develop HEAD` reports content conflicts in
  `src/hypershell/server.py` and `ROADMAP.md`. The branch is based on `f3fb4dd2` and lacks #114
  (`52828325`).
- **server.py:** neighbouring lines in `Receiver`. Develop's `written` counter (+1 per applied
  result, read by `cluster/watch.py`) sits beside the branch's `scheduler` attribute and its
  `results_landed` call. Keeping both is sound:
  - both count only APPLIED results;
  - `results_landed` runs inside `update_tasks`, before UNLOAD, so #114's one-bundle-at-a-time
    barrier holds;
  - #114's empty barrier bundle applies 0 results and makes no call.
  - On the keep-both tree (reviewer, scratch): `test_finish_wake`, `test_client_return`,
    `test_client_request` and `test_cluster_locked` → 49 passed; `test_cluster_exit` and
    `test_cluster_nodb_finish` → 48 passed; docs `-W` → exit 0.
- **ROADMAP.md:** prose only. Keep develop's `*Target: …*` lines together with the branch's
  `status: adopted:relay-shutdown-tail` values. The queue-limits seed stays `unshaped`.
- **Against the sibling `feature/relay-advertise`:** not on `origin`, so not checked as a pushed
  branch. Its local head (based on `develop` `08d867df`) gives the same two conflicts and no others:
  `AGENTS.md`, `invariants.md` and `config_param_ref.rst` auto-merge.
- **Consequence:** the conflict has to be resolved before publish. The resolution touches
  `server.py`, so it falls after this review's SHA and `/hs-publish`'s staleness gate would stop on
  it. It belongs in the `hs-build` remediation, and cycle 2 grades it.

## Human-gate triggers

- **Not triggered by the rubric's rule.** The one CONFIRMED finding (F1) is in
  `cluster/__init__.py`, which is not on the high-blast-radius list. No CONFIRMED finding touches
  `server.py`, `client.py` or a security or DB-lifecycle invariant.
- The diff itself changes `server.py` (the scheduler's stop and wake, the Receiver) and `client.py`
  (bundling), and the remediation's merge resolution touches `server.py` again. A CONFIRMED finding
  there in cycle 2 would trigger the gate.

## Remediation for `hs-build`

1. Bring the branch up to date with `origin/develop`, resolving `server.py` and `ROADMAP.md` by
   keeping both sides as above. Re-run the scheduler and receiver modules, the new tests and #114's
   `test_cluster_locked`, `test_cluster_exit` and `test_cluster_nodb_finish` on the result.
2. F1: reword the `hs cluster` `-w` one-liner so that the idle return is credited to the clients.
   Leave the `hs client` line as it is.

Cycle 2 may be scoped to F1 and the merge resolution. If it is, it should still re-read the resolved
`Receiver.update_tasks` against §3 and §6.

## Review cycle 2 — changes-requested (2026-11-21)

- **Reviewed commit:** `c9b41f95`  ·  **Base:** `origin/develop` `d9901cf0`, which is also the merge
  base now that develop is merged in (`84768297`).
- **Verdict:** changes-requested.
  - The review-cycle-1 remediation holds on all three counts: F1's one-liner, the `server.py` merge
    resolution, and the new coverage test.
  - The remediation's new sentence in the `hs cluster` long help is wrong about the submit thread:
    F2, LOW, CONFIRMED. The fix is wording only.
- **Cycle:** 2 of ≤3. It mirrors `review.cycle` in `TECH.md`. This is the second cycle without
  convergence, so a third cycle should be the last before the human is asked.

### How this cycle was run

- **Mode:** scoped, as the review-cycle-1 rulings set. It covered three things:
  - F1;
  - the merge resolution of `origin/develop` (#114) in `server.py`;
  - the new test `test_result_landing_during_the_count_is_not_lost`.

  It also took a fresh blind pass over the delta `8690deaf..c9b41f95`, and over every place where the
  merge meets this cycle's code.
- **Blind correctness pass:** one fresh reviewer. It was given:
  - `GOAL.md` with its GOAL-gate block;
  - the base copies of `invariants.md`, `review-rubric.md` and `AGENTS.md`;
  - the diff with `spec/` excluded, both the whole branch against `origin/develop` and the delta
    since `8690deaf`;
  - cycle 1's F1 and its coverage note;
  - the binding readings: the plan-gate rulings D1–D6, the P7 gate ruling, and the review-cycle-1
    rulings.

  It did not read `PLAN.md`, `TECH.md`, `META.md`, `REVIEW.md` or `research/`, and its searches
  excluded `spec/` except this cycle's `evidence/` and `drive/`. It started no `tail_run.py` run.
- **Orchestrator:**
  - checked contract drift and merge status;
  - checked the R5 claim;
  - built the docs and read the help output;
  - ran the new tests and #114's tests;
  - drove F2 on its own, independently of the reviewer;
  - re-graded every finding.
- **Contract drift:** none. `GOAL.md` still changes only in `d1a0fea8` and `6f4e809f`.
- **Environments:** CPython 3.14.6 for the tests and drives, run serially. Docs used CPython 3.12.
  Every CLI drive ran in `.agents/factory/bin/temp_site.sh`, bound to 127.0.0.1, with the key in
  the environment only.

### Verification run

- **Whole suite on HEAD, serial** (reviewer): 3545 passed, 138 skipped, exit 0.
- **Targeted tests on HEAD** (orchestrator): `test_finish_wake`, `test_client_return`,
  `test_client_request`, `test_cluster_nodb_finish`, `test_cluster_locked` and
  `test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused` → 60 passed. #114's
  tests and this cycle's tests both pass on the merged tree.
- **Merge resolution in `server.py`:**
  - `Receiver` on HEAD is develop's class plus this branch's additions and nothing else: the
    `scheduler` attribute, its keyword argument, and the `results_landed` call.
  - The branch's own `server.py` hunks are byte-identical before and after the merge (reviewer,
    `cmp` of the two hunk sets).
  - `written += 1` runs on the APPLIED path inside the loop (`server.py:734`). In-memory mode marks
    every result APPLIED, so the `--no-db` finish in `cluster/watch.py:189` still counts.
  - `results_landed(outcomes.count(APPLIED))` runs once per bundle, after the loop and before
    UNLOAD (`:735–737`).
  - The two counters are separate: nothing is counted twice, reordered or lost.
  - The empty barrier bundle applies 0 results and makes no call.
  - Barrier probe (reviewer, scratch). A stubbed `results_landed` that sleeps 1 s, followed by
    #114's real `results_written()`, gives `results_written: True written: 3 landed calls: [3]`.
    The barrier waits for the report.
- **Coverage test (`tests/test_finish_wake.py:225`):**
  - **Deterministic.** The result lands synchronously inside the pass's own `count_remaining` call,
    with no sleeps; 8 of 8 repeated runs passed (reviewer).
  - **Mutation.** In a scratch copy, `self.finishing = (mark, 0)` was removed, so the pass arms only
    after its count. `test_finish_wake.py` then gives 1 failed, 8 passed. Only the new test fails
    ("Pass still waiting after 15.0 s"); the older `test_result_landing_before_the_wait_is_not_lost`
    does not catch this mutation. The test adds real coverage.
- **F1 one-liner:**
  - `hs cluster --help` prints "Wait before flushing tasks, none for an idle client". The idle
    return is credited to the clients alone, as ruled.
  - `hs client --help` is unchanged and accurate.
  - Drive (reviewer): `hs cluster t.in -N2 -b 10 -w 30`, 3 tasks of `sleep 0.2`, ended in 1.81 s
    with a database and 1.57 s with `--no-db`.
- **R5 claim (orchestrator and reviewer):**
  - `client.py` is the same blob at `dd0b227d` and HEAD, and `server.py` differs only by the three
    `written` lines.
  - The other source changes since `dd0b227d`:
    - `cluster/__init__.py`: the help string;
    - `cluster/watch.py`: used only by `RemoteCluster` and `SSHCluster`, not by a relay;
    - `cluster/remote.py`: #114's `AutoScaler` branch for a locked SQLite read, an error path only.
  - The records still stand for HEAD's code. `tail_bound.py` on `evidence/head/` → PASS (reviewer).
- **Docs:** `sphinx-build -W --keep-going -q -E -b html` exited 0 at HEAD (orchestrator; reviewer
  separately).

### Requirement → evidence (the delta)

| R-ID | Verified how | Status |
|------|--------------|--------|
| R1, R2 | `client.py` unchanged since cycle 1 (same blob); `test_client_return` passes; `-w 30` idle drive | ✅ |
| R3 | `test_finish_wake` passes, including the new test; mutation red; merge leaves `results_landed` on APPLIED results only | ✅ |
| R4 | Work-left, retry-left, forever and input-open tests pass; whole suite green | ✅ |
| R5 | Records on `dd0b227d`; relay and client paths unchanged since; `tail_bound.py` PASS | ✅ |
| R6 | `hs cluster` one-liner accurate; `-W` build clean | ⚠️ F2 |
| R7 | Unchanged since cycle 1; injection test passes | ✅ |

Unmapped changes: none. Outside the merge, the delta is the F1 help string and prose, the coverage
test, and `spec/` commits.

### Findings

#### F2 [LOW/CONFIRMED] The `hs cluster` long help says the submit thread lets go of tasks once `-w` passes; on a quiet stream it does not

- **Where:** `docs/_include/cluster_help.rst:112–113`, new in `dd450d86`. It reads: "The `submit`
  thread does not: it holds tasks until a bundle is full, this period passes or the input ends."
- **The problem:**
  - Both committers check the bundle wait only when a task arrives: `DatabaseCommitter.get_task`
    (`src/hypershell/submit.py:529–543`, test at `:538`) and `QueueCommitter.get_task` (`:771–785`).
  - On an empty queue they loop without looking at the clock. So once a stream pauses, the period
    passing frees nothing: a line is held until the next line arrives or the input ends.
  - The behaviour is old and already seeded (`issues/stream-bundlewait-commit-wait.md`). The new
    sentence misstates it.
- **Failure scenario:** a user streams lines into `hs cluster -b 10 -w 2` and expects a line to be
  committed about 2 s after it is read. It is not committed until the stream moves on or ends.
- **Evidence:**
  - Orchestrator drive: `hs cluster -N 1 -b 10 -w 2 -Q 30 -H 127.0.0.1`, one line written at 0.31 s,
    stdin closed at 8.02 s. The row was committed and scheduled at 8.12 s, about 6 s past the stated
    period.
  - Reviewer drive: `-w 1`, a 7 s pause. 0 rows after 4 s; "Submitted 2 tasks" logged only when the
    second line arrived at +7 s.
- **Touches:** R6's surrounding prose, not the client behaviour R6 requires. No source logic is
  involved, and the submit side's bundling stays a Non-goal.
- **Fix:** wording only. Drop "this period passes", or say that the period is checked when a task
  arrives. The `hs cluster` one-liner and the client sentence stay as they are.

**Considered and dropped:**
- **The receiver's new count reads can raise on a locked SQLite database.** `Task.apply_results`
  and the scheduler's own reads carry the same exposure, so this is not new to the branch. Cycle 1
  dropped it for the same reason.

### Merge status

- **Against `origin/develop` `d9901cf0`:** develop has not moved since the merge.
  `git merge-tree --write-tree origin/develop HEAD` is clean, because develop is the merge's second
  parent.
- **Against `feature/relay-advertise` `e7bc209f`** (now on `origin`, not yet merged):
  - `git merge-tree --write-tree e7bc209f HEAD` is clean.
  - Both branches touch `AGENTS.md`, `invariants.md`, `config_param_ref.rst` and `ROADMAP.md`, but
    in different places: §3 against §11, `relay.bundlewait` against the new `relay.advertise`, and
    different roadmap lines.
  - The merged tree reads coherently around `client.py`, `server.py` and the relay docs.

### Human-gate triggers

- **Not triggered.** The one CONFIRMED finding (F2) is in `docs/_include/cluster_help.rst`.
- No CONFIRMED finding touches `server.py`, `client.py`, another high-blast-radius file, or a
  security or DB-lifecycle invariant. The `server.py` merge resolution was graded and holds.

### Remediation for `hs-build`

1. F2: reword the submit-thread sentence in `docs/_include/cluster_help.rst` so that it matches
   `submit.py`. Then rebuild the docs with `-W`.

Cycle 3 can be scoped to F2.
