---
slug: relay-shutdown-tail
title: A relay run ends within seconds of its last result
kind: fix
appetite: small
status: in_progress
branch: fix/relay-shutdown-tail
base: develop
current_phase: P7
last_updated: '2026-11-21'
phases:
- id: P1
  name: 'Tests first: the red list on f3fb4dd2 (R1, R3, R7) and the new tests'' guards'
  status: done
  satisfies:
  - R1
  - R3
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: RED_LIST=spec/relay-shutdown-tail/drive/red.txt RED_BASE_REV=f3fb4dd2 python3
    spec/relay-shutdown-tail/drive/bounded.py 900 sh spec/relay-hardening/drive/red_check.sh
    && python3 spec/relay-shutdown-tail/drive/bounded.py 300 uv run pytest -q -p no:cacheprovider
    'tests/test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused[NoSuchProcess]'
    'tests/test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused[ZombieProcess]'
    && python3 spec/relay-shutdown-tail/drive/bounded.py 120 uv run pytest -q -p no:cacheprovider
    --collect-only tests/test_client_return.py tests/test_finish_wake.py tests/test_relay_workload.py
    && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    nodb
- id: P2
  name: 'The result that finishes a run wakes the scheduler: a counted arm, the receiver''s
    check'
  status: done
  satisfies:
  - R3
  - R4
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/bounded.py 1500 uv run pytest -q
    -p no:cacheprovider tests/test_finish_wake.py tests/test_stream_input.py tests/test_group_order.py
    tests/test_server_fsm.py tests/test_writeback.py tests/test_writeback_statement.py
    tests/test_retry_gate.py tests/test_dispatch.py tests/test_dispatch_workload.py
    tests/test_restart.py tests/test_cluster_exit.py tests/test_contended_recovery.py
    && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    finish && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    nodb
- id: P3
  name: 'A client with nothing left to run returns what it holds: the in-flight count'
  status: done
  satisfies:
  - R1
  - R2
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/bounded.py 1500 uv run pytest -q
    -p no:cacheprovider tests/test_client_return.py tests/test_client_request.py tests/test_relay_return.py
    tests/test_relay_fsm.py tests/test_signal_wake.py tests/test_stream_input.py tests/test_main.py
    && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    idle && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    nodb && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/relay-shutdown-tail/drive/end_drive.sh"
    finish
- id: P4
  name: 'Rider: the relay workload scans skip a process they cannot read (test-only)'
  status: done
  satisfies:
  - R7
  depends_on:
  - P1
  parallel: true
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/bounded.py 120 uv run pytest -q -p
    no:cacheprovider -m unit tests/test_relay_workload.py && python3 spec/relay-shutdown-tail/drive/bounded.py
    2400 uv run pytest -q -p no:cacheprovider tests/test_relay_workload.py tests/test_relay_outage.py
- id: P5
  name: 'Docs: when results are returned and when a finishing server stops waiting
    (versionchanged 2.9.0); the invariant record'
  status: done
  satisfies:
  - R6
  depends_on:
  - P2
  - P3
  parallel: false
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/bounded.py 300 uv run pytest -q -p
    no:cacheprovider tests/test_main.py tests/test_config_schema.py && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv
    python3 spec/relay-shutdown-tail/drive/bounded.py 900 uv run --python 3.12 --group
    docs sphinx-build -W --keep-going -q -E -b html docs /tmp/claude-501/docs-html-rst
    && UV_PROJECT_ENVIRONMENT=/tmp/claude-501/docs-venv python3 spec/relay-shutdown-tail/drive/bounded.py
    900 uv run --python 3.12 --group docs sphinx-build -W --keep-going -q -E -b man
    docs /tmp/claude-501/docs-man-rst && grep -q 'nothing left to run' docs/_include/client_help.rst
    && grep -q 'nothing left to run' docs/_include/cluster_help.rst && test "$(grep
    -c 'nothing left to run' docs/_include/config_param_ref.rst)" -ge 2 && grep -q
    'finishes a run' docs/_include/config_param_ref.rst && grep -q 'nothing left to
    run' /tmp/claude-501/docs-man-rst/hs.1 && grep -q 'finishes a run' /tmp/claude-501/docs-man-rst/hs.1
    && test "$(git diff f3fb4dd2 -- docs src AGENTS.md .agents | grep -c '^+.*hs task
    ')" -eq 0
- id: P6
  name: 'R5 laptop drive on the committed head: at most 10 runs, at least 3 at 2000
    and at 2004 tasks'
  status: done
  satisfies:
  - R5
  depends_on:
  - P5
  parallel: false
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/tail_bound.py spec/relay-shutdown-tail/evidence/head
    --baseline spec/relay-shutdown-tail/evidence/baseline
- id: P7
  name: Whole suite, serially; the red list re-proved on f3fb4dd2
  status: pending
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R5
  - R6
  - R7
  depends_on:
  - P4
  - P6
  parallel: false
  hammerable: false
  hill: uphill
  verify: python3 spec/relay-shutdown-tail/drive/bounded.py 7200 uv run pytest -q
    -p no:cacheprovider && RED_LIST=spec/relay-shutdown-tail/drive/red.txt RED_BASE_REV=f3fb4dd2
    python3 spec/relay-shutdown-tail/drive/bounded.py 900 sh spec/relay-hardening/drive/red_check.sh
    && python3 spec/relay-shutdown-tail/drive/tail_bound.py spec/relay-shutdown-tail/evidence/head
    --baseline spec/relay-shutdown-tail/evidence/baseline
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
---
# TECH.md — A relay run ends within seconds of its last result

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is the
resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/relay-shutdown-tail/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md) (§ 2 is the build spec for P2–P4).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`–`03`.
- **Drive:** [`drive/`](drive/) — `bounded.py` (wall-clock bound, exit 124), `end_drive.sh`
  (`idle`, `finish`, `nodb`; run in a `temp_site` from the repository root), `red.txt` (for the landed
  `spec/relay-hardening/drive/red_check.sh`, run read-only), `tail_bound.py` (the R5 judge).

## Frontmatter field reference

As in [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the touched invariants are walked in
  [`PLAN.md`](PLAN.md) § 3. `server.py` and `client.py` are high-blast-radius: one phase each, serial;
  the review's human gate applies.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build relay-shutdown-tail P<n>: …`. **No `Co-Authored-By` trailer.** Signed commits.
- Comments are declarative statements; no R-ids in source. Tag every new test `unit` or `integration`.
- No option, default or key changes: no completion changes. The help text and docs change in P5 with
  `.. versionchanged:: 2.9.0`. Docs name top-level commands only.
- **Environment.** Export `UV_PROJECT_ENVIRONMENT=/tmp/claude-501/venv314-rst` and
  `UV_CACHE_DIR=/tmp/claude-501/uv-cache-rst` (Python 3.14) before every verify; P5's docs builds
  override the environment to `/tmp/claude-501/docs-venv` (Python 3.12) and need the sandbox off.
  `pytest -n auto` is broken here: every run is serial. Bind 127.0.0.1 in drives; keys only through
  `HYPERSHELL_SERVER_AUTH` / `HYPERSHELL_RELAY_UPSTREAM_AUTH`, never `-k`.
- **Bounds.** The host has no `timeout(1)`. Every pytest run and drive goes through `drive/bounded.py`
  (or `end_drive.sh`, which uses it), and every new integration test bounds its own waits (joins and
  deadlines, then stops what it started in `finally`), so nothing hangs on the base build. Stop every
  process you start; end each phase with an out-of-sandbox `ps` filtered by your paths.
- **Tests-first.** P1 writes every new test; P2–P4 make them pass. New test files must not import a
  name the base build lacks at module level (`TaskCount`, a `Receiver` keyword, `client_environments`):
  import or look it up inside the test, so the red-list tests collect and fail on `f3fb4dd2` by their
  assertions, not by an import error (the checker calls that NOT RED).

### Dry-run (plan time, 2026-11-21)

Every verify was dry-run against this worktree's `src/` (identical to `f3fb4dd2`) with the existing
files it names, and the new tests were prototyped in a scratch copy of the tree (`git archive`) with
both fixes prototyped from PLAN § 2.

- **Red list** — `red_check.sh` with `RED_BASE_REV=f3fb4dd2` on the R1 and R3 probes (placed in
  `tests/` for the run only): **2 of 2 RED** by assertion (R1: 0 of 3 results returned in 10 s with a
  60 s bundle wait; R3: run still in its 600 s back-off at the 15 s join). The R7 injection test on a
  base copy: `[AccessDenied]` fails by `psutil.AccessDenied` in the call; `[NoSuchProcess]` and
  `[ZombieProcess]` pass (guards).
- **Prototype green** — R1 probe passes 3 of 3 (completion → landed 0.010–0.011 s); R3 probe passes 3
  of 3 (landed → FINAL 0.001–0.005 s); the existing P2 suites passed on the prototype (128 passed).
- **`end_drive.sh`** — base: `idle` killed at its 15 s bound, `finish` killed at 13.5 s (3 of 3),
  `nodb` passes (1.8–2.0 s). Prototype: `idle` 1.7–1.9 s, `finish` 9.7–9.9 s (3 of 3), `nodb` 1.8 s.
- **`tail_bound.py`** — the baseline fails it (2004-task tails 6.75, 7.45 s; 2 runs at 2004), an empty
  `head/` fails it (0 runs), a missing directory or bad usage exits 64. The prototype's 2004-task run:
  tail 1.54 s (`evidence/plan-probe/`).
- **Docs** — the html and man builds pass with `-W` on the base (sandbox off); the base's man page has
  the `-w` paragraph three times and the parameter reference, so the P5 greps look in the right file.
- **Existing suites on the base**, each through `bounded.py` with the files that exist today: P2's
  set 255 passed (5 min 47 s); P3's set 283 passed (47 s); P4's `-m unit` selects nothing yet (17
  deselected, pytest exit 5: P1 adds the unit tests) and the whole relay workload and outage files 20
  passed (2 min 8 s). Through `temp_site.sh uv run sh …`: `idle` FAIL, `finish` FAIL, `nodb` PASS.

### Expected red list on `f3fb4dd2` (P1)

| Test | R | Fails on the base by |
|------|---|----------------------|
| `tests/test_client_return.py::test_idle_client_returns_what_it_holds` | R1 | assertion: fewer than 3 of 3 results returned within 10 s (`LocalCluster`, `-N 2 -b 8 -w 60`, 3 tasks, poll 1) |
| `tests/test_finish_wake.py::test_finishing_result_ends_a_long_back_off` | R3 | assertion: the run is still waiting out a back-off forced to the full 600 s poll at its 15 s join |
| `tests/test_relay_workload.py::test_scans_skip_a_process_whose_read_is_refused[AccessDenied]` | R7 | `psutil.AccessDenied` raised from `scan_site` |

Also written in P1, failing on the base only because the names they test do not exist yet (not on the
red list): the collector and scheduler unit tests for `TaskCount` (R1, R2), the receiver/scheduler unit
tests for `results_landed` (R3, R4) and the `client_environments` test (R7). Guards that pass on the base
and must stay green: `[NoSuchProcess]`, `[ZombieProcess]`, `end_drive.sh nodb`, the collector's
`finalize` flush test.

---

## Phase P1 — Tests first: the red list on f3fb4dd2
**Satisfies:** R1, R3, R7 · **Depends on:** —
**Goal:** every new test written, the three red-list tests failing on `f3fb4dd2` for what they check.

- [x] `tests/test_client_return.py` (new):
  - `test_idle_client_returns_what_it_holds` (`integration`): `LocalCluster(source=['true'] * 3,
    num_threads=2, bundlesize=8, bundlewait=60, poll=1, max_retries=0, port=free_port())` on the
    `task_db` fixture (`tests/test_group_order.py`), `DATABASE_ENABLED` patched on; wrap
    `Task.apply_results` to stamp each result's landing time; wait at most 10 s for all 3; assert all 3
    landed and each landed ≤ 1 s after its `completion_time`; in `finally`, stop the scheduler and join
    (≤ 90 s) if still alive. (The plan-time probe is this test.)
  - Unit tests on `ClientCollector` driven action by action with a `SimpleNamespace(completed=Queue())`
    and `DEFAULT_LOCAL_TIMEOUT` patched small: held while the count is above 0 (`check_bundle` →
    GET_LOCAL); returned by size; returned by wait; returned at once when the count reaches 0 with tasks
    held, never as an empty bundle; `inflight=None` behaves as today; `finalize` returns everything
    held (a guard, green on the base). A `ClientScheduler` unit test in `tests/test_client_request.py`'s
    style: a reply with tasks raises the count by their number.
- [x] `tests/test_finish_wake.py` (new):
  - `test_finishing_result_ends_a_long_back_off` (`integration`): `LocalCluster(source=['sleep 1'],
    num_threads=1, bundlesize=1, bundlewait=1, poll=600, max_retries=0)`; patch `Scheduler.back_off` so a
    `No tasks available` back-off is the full 600 s (`poll, woken = poll_max, True`, as
    `tests/test_stream_input.py:437-445`); stamp `Task.apply_results` and `Scheduler.finalize`; join 15 s;
    assert the run ended, a back-off was entered, and FINAL came < 1 s after the landing.
  - Unit tests (scheduler with `queue=None`, `tests/test_stream_input.py` helpers): a finishing result
    reported through `Receiver(scheduler=…)` ends a 600 s back-off within 1 s; a landed result with a task
    still incomplete does not end it (the wait times out at a short test poll, `woken` stays False); a
    failure with a retry left does not wake; a `--forever` scheduler is never armed; a result landing
    after the pass's counts and before its wait is not lost (patch `Task.count_retryable` to land and
    report the result, as `test_wake_before_the_wait_is_not_lost`); an in-memory receiver has no scheduler.
- [x] `tests/test_relay_workload.py`: `test_scans_skip_a_process_whose_read_is_refused` (`unit`,
  parametrized `AccessDenied` / `NoSuchProcess` / `ZombieProcess`, fake processes through a patched
  `site_processes`; asserts `scan_site` and `scan_site_key` counts over the readable processes, as in
  [`research/03`](research/03-relay-tests-and-drive.md)); `test_client_environments_skip_a_refused_read`
  (`unit`, the `:1058` read; looks `client_environments` up at call time).
- [x] Commit `drive/red.txt` as is (or amend its node ids if a test name changes).
- **Verify:** `red_check.sh` on `f3fb4dd2` (3 of 3 RED), the two guard ids pass, the new files collect,
  `end_drive.sh nodb` passes.
- **Touches:** `tests/test_client_return.py`, `tests/test_finish_wake.py`, `tests/test_relay_workload.py`,
  `tests/test_client_request.py` (optional), `spec/relay-shutdown-tail/drive/red.txt`.

## Phase P2 — The result that finishes a run wakes the scheduler
**Satisfies:** R3, R4 · **Depends on:** P1
**Goal:** a non-`--forever` server whose input has ended stops within 1 s of the result that finishes the
run, however long its back-off; nothing else about scheduling changes.

- [x] `Scheduler`: `landed = 0`, `finishing = None`; in `load_bundle`, on a pass that loaded nothing,
  arm `(landed, 0)` before the counts when not `forever_mode` and the input has ended, read
  `count_remaining()` once (only when `Task.count() > 0`, as today) and arm `(mark, remaining)`; keep the
  FINAL predicate and the failed-group branch as they are; a pass that loads disarms.
- [x] `Scheduler.results_landed(n)` (PLAN § 2): count, check the threshold, read
  `count_remaining() == 0` then `count_retryable(attempts, group=current_group, eager=eager) == 0`,
  `wake()`.
- [x] `Receiver`/`ReceiverThread`: optional `scheduler=`; after a write, report the number of `APPLIED`
  outcomes. `ServerThread` passes `self.scheduler.machine` (or `None` in memory).
- **Verify:** the server suites around the scheduler, receiver, dispatch and shutdown, plus
  `end_drive.sh finish` (≤ 13.5 s) and `nodb`.
- **Touches:** `src/hypershell/server.py`.

## Phase P3 — A client with nothing left to run returns what it holds
**Satisfies:** R1, R2 · **Depends on:** P1
**Goal:** every client returns a partial bundle at once when it has no task running or waiting, and
bundles exactly as today otherwise.

- [x] `TaskCount` in `client.py`; `ClientThread` makes one and passes `inflight=` to the scheduler and
  collector machines (and their threads).
- [x] `ClientScheduler.request_bundle` raises it by a reply's task count; `ClientCollector.get_local`
  lowers it per task taken; `check_bundle` returns a non-empty bundle at 0, after the size test, before
  the wait test, with a TRACE line.
- **Verify:** client, relay-return and stream suites, `tests/test_main.py` (help unchanged here), and
  `end_drive.sh idle` (≤ 15 s), `nodb`, `finish`.
- **Touches:** `src/hypershell/client.py`.

## Phase P4 — Rider: the scans skip a process they cannot read
**Satisfies:** R7 · **Depends on:** P1
**Goal:** a process that exits mid-read is skipped by every scan, with the same counts and checks over
the processes read. Test-only.

- [x] `read_process()`, used by `scan_site` and `scan_site_key`; `client_environments()` read inside
  `until(...)` at the `:1058` site; `Optional`/`Tuple` imports. Leave `running_tasks`, `site_clients` and
  the names `tests/test_relay_outage.py` imports as they are.
- **Verify:** the unit tests of the file, then the whole file and `tests/test_relay_outage.py`
  (integration; real `hs` processes on loopback; each test caps itself at 240 s).
- **Touches:** `tests/test_relay_workload.py`.

## Phase P5 — Docs and the invariant record
**Satisfies:** R6 · **Depends on:** P2, P3
**Goal:** the documentation says when results are returned and when a finishing server stops waiting.

- [x] `-w` help (`src/hypershell/client.py:1556`, `src/hypershell/cluster/__init__.py:87`) and the `-w`
  paragraphs of `docs/_include/client_help.rst` and `docs/_include/cluster_help.rst`: "A client with
  nothing left to run returns what it holds at once." Keep `tests/test_main.py` green.
- [x] `docs/_include/config_param_ref.rst`: `client.bundlewait` (same sentence), `relay.bundlewait` (its
  site clients, too, return what they hold at once when they have nothing left to run), `server.poll`
  ("The result that finishes a run ends the scheduler's wait at once."), each with an
  `.. only:: html` / `.. versionchanged:: 2.9.0` note; the `-Q` paragraphs of
  `docs/_include/server_help.rst` and `cluster_help.rst` get the `server.poll` sentence.
- [x] `.. versionchanged:: 2.9.0` notes in `docs/cli/client.rst`, `docs/cli/cluster.rst`,
  `docs/cli/server.rst`. Top-level commands only.
- [x] `AGENTS.md` and `.agents/factory/invariants.md` §3, in lockstep: the out-of-work stop also takes
  effect when the finishing result lands (the receiver wakes the scheduler), not only on the next pass.
- [x] Man pages: not regenerated (release rebuild); the verify checks the man builder's output.
- **Verify:** help tests, both docs builds with `-W`, the sentence greps in the sources and the man
  output, and no `hs task` added.
- **Touches:** `src/hypershell/client.py`, `src/hypershell/cluster/__init__.py`, `docs/_include/*.rst`,
  `docs/cli/{client,cluster,server}.rst`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P6 — R5: the laptop drive on the committed head
**Satisfies:** R5 · **Depends on:** P5
**Goal:** no relay tail over 3 s at 2000 or 2004 tasks, with every run's record kept beside the baseline.

- [x] On the committed head (clean `src/`), from the repository root, with the venv's `hs`:
  `S=$TMPDIR/rst-head-sites; mkdir -p $S` (a scratch directory outside the tree) then, per run,
  `HS=/tmp/claude-501/venv314-rst/bin/hs TMPDIR=$S PYTHONDONTWRITEBYTECODE=1
  /tmp/claude-501/venv314-rst/bin/python spec/relay-shutdown-tail/drive/bounded.py 300
  /tmp/claude-501/venv314-rst/bin/python spec/relay-hardening/drive/tail_run.py
  spec/relay-shutdown-tail/evidence/head head-2004-NN --label head-2004 --tasks 2004` (or
  `head-2000-NN --label head-2000 --tasks 2000`). The venv's Python directly, so the bound's SIGINT
  reaches `tail_run.py`, whose `finally` stops every process group it started.
- [x] Schedule: 2004, 2000, 2004, 2000, 2004, 2000 — six planned; four spares, spent only to replace a run
  that did not complete, at its size. Every started run counts; stop at 10. A tail over 3 s ends the
  drive: keep the record, do not re-roll, stop and escalate (R5 fails; the plan is wrong).
- [x] `tail_bound.py … --table spec/relay-shutdown-tail/evidence/tails.md`; update `evidence/README.md`'s
  `head/` section (host, date, commit, what the windows show: no `Bundle wait exceeded`, the idle
  returns).
- **Verify:** `tail_bound.py` on `evidence/head` with the baseline (exit 0).
- **Touches:** `spec/relay-shutdown-tail/evidence/`.

## Phase P7 — Whole suite, serially
**Satisfies:** R1–R7 · **Depends on:** P4, P6
**Goal:** the whole suite green on Python 3.14, serially; the red list still red on the base; R5 judged.

- [ ] Run the whole suite once, serially (no `-n`), bounded at 2 h. A failure outside this cycle's files
  that reproduces on `f3fb4dd2` is recorded, not fixed (name it in the phase commit).
- **Verify:** whole suite, `red_check.sh` on `f3fb4dd2` (3 of 3 RED), `tail_bound.py`.
- **Touches:** — (state only, unless a failure in this cycle's files needs a fix).

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the `current_phase`
   pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
