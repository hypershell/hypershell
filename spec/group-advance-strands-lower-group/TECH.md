---
slug: group-advance-strands-lower-group
title: Run task groups in order, and never strand a late one
kind: fix
appetite: small
status: in_progress
branch: fix/group-advance-strands-lower-group
base: develop
current_phase: P5
last_updated: '2026-11-04'
phases:
- id: P1
  name: Red replays and the halted-restart guard (tests only)
  status: done
  satisfies:
  - R8
  - R1
  - R4
  - R5
  - R6
  - R7
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: kt() { uv run python -c 'import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)' "$@"; }; git diff --quiet b31c66e -- src && kt 300 pytest -m unit
    -rxX tests/test_group_order.py
- id: P2
  name: Atomic next-group choice, late-lower warning, provisional first group
  status: done
  satisfies:
  - R1
  - R2
  - R4
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'kt() { uv run python -c ''import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)'' "$@"; }; git diff --quiet b31c66e -- tests/test_groups.py tests/test_retry_gate.py
    && ! grep -q xfail tests/test_group_order.py && kt 600 pytest -m unit tests/test_group_order.py
    tests/test_retry_gate.py && kt 300 .agents/factory/bin/temp_site.sh sh -c "printf
    ''%s\n'' ''echo 1 #HYPERSHELL: n:1 group:1'' > a.in && uv run hs submit -f a.in
    && uv run hsx --restart -N1 --no-tls && printf ''%s\n'' ''echo 0 #HYPERSHELL:
    n:0 group:0'' > b.in && uv run hs submit -f b.in && uv run hsx --restart -N1 --no-tls
    2> err.log && test \$(uv run hs list -c -R) -eq 0 && test \$(uv run hs list -c
    -S -t n:0) -eq 1 && ! grep -q ''after task group'' err.log" && kt 300 pytest -q
    tests/test_group_order.py::test_cluster_runs_group_stored_as_text'
- id: P3
  name: File input is ingested before the scheduler starts
  status: done
  satisfies:
  - R3
  - R8
  - R7
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'kt() { uv run python -c ''import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)'' "$@"; }; { kt 1200 pytest -q -l tests/test_group_order.py tests/test_groups.py
    > "${TMPDIR:-/tmp}/hs-p3.log" 2>&1 && tail -n 3 "${TMPDIR:-/tmp}/hs-p3.log" ||
    { tail -n 40 "${TMPDIR:-/tmp}/hs-p3.log"; grep -E ''^(FAILED|ERROR)'' "${TMPDIR:-/tmp}/hs-p3.log";
    echo ''One rerun of the failed tests (known TLS localhost flake): record both
    runs''; kt 1200 pytest -q -l --lf tests/test_group_order.py tests/test_groups.py;
    }; } && kt 300 .agents/factory/bin/temp_site.sh sh -c "printf ''%s\n'' ''echo
    a #HYPERSHELL: group:1'' ''echo b #HYPERSHELL: group:0'' ''echo c #HYPERSHELL:
    group:-1'' ''echo d #HYPERSHELL: group:1'' ''echo e #HYPERSHELL: group:0'' ''echo
    f #HYPERSHELL: group:-1'' > t.in && uv run hsx t.in -N2 --no-tls 2> err.log &&
    test \"\$(uv run hs list group -s schedule_time | uniq | tr ''\n'' '' '')\" =
    ''-1 0 1 '' && test \$(uv run hs list -c -R) -eq 0 && ! grep -q ''after task group''
    err.log"'
- id: P4
  name: Group-order docs, AGENTS.md and invariants
  status: done
  satisfies:
  - R10
  depends_on:
  - P2
  - P3
  parallel: true
  hammerable: false
  hill: uphill
  verify: ( for f in docs/_include/task_groups_alt.rst AGENTS.md .agents/factory/invariants.md;
    do grep -qiE 'best[- ]effort' "$f" && grep -qi 'warning' "$f" && grep -qiE 'whole
    file|entire file|in full' "$f" || { echo "R10 statement missing in $f"; exit 1;
    }; done ) && git diff --quiet b31c66e -- share src/hypershell/__init__.py src/hypershell/cluster/__init__.py
    docs/_include/cluster_help.rst docs/_include/cluster_usage.rst docs/_include/server_help.rst
    docs/_include/server_usage.rst docs/_include/submit_help.rst docs/_include/submit_usage.rst
- id: P5
  name: 2.8.4 port trial and final gate
  status: in_progress
  satisfies:
  - R9
  - R7
  - R2
  depends_on:
  - P1
  - P2
  - P3
  - P4
  parallel: false
  hammerable: false
  hill: uphill
  verify: 'kt() { uv run python -c ''import os,signal,subprocess,sys,threading;p=subprocess.Popen(sys.argv[2:],start_new_session=True);t=threading.Timer(float(sys.argv[1]),os.killpg,(p.pid,signal.SIGKILL));t.start();r=p.wait();t.cancel();sys.exit(124
    if r<0 else r)'' "$@"; }; test "$(git diff --name-only b31c66e -- src | tr ''\n''
    '' '')" = ''src/hypershell/client.py src/hypershell/cluster/local.py src/hypershell/cluster/remote.py
    src/hypershell/cluster/ssh.py src/hypershell/data/model.py src/hypershell/server.py
    '' && ! git diff b31c66e -- src | grep -E ''^[-+].*(mapped_column|__tablename__|add_argument|_HELP|_USAGE)''
    && kt 2400 sh spec/group-advance-strands-lower-group/port-trial.sh && { kt 2400
    pytest -q -l > "${TMPDIR:-/tmp}/hs-p5.log" 2>&1 && tail -n 3 "${TMPDIR:-/tmp}/hs-p5.log"
    || { tail -n 40 "${TMPDIR:-/tmp}/hs-p5.log"; grep -E ''^(FAILED|ERROR)'' "${TMPDIR:-/tmp}/hs-p5.log";
    echo ''One rerun of the failed tests (known TLS localhost flake): record both
    runs''; kt 2400 pytest -q -l --lf ; }; }'
review:
  last_reviewed_commit: eca86c8
  verdict: changes-requested
  blocked_reason: 'review 3 (human ruling 2026-11-04, narrow fix then scoped review):
    O1 text group first hides the late-group warning (server.py:274); O2 seed prose'
  cycle: 3
---
# TECH.md — Run task groups in order, and never strand a late one

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/group-advance-strands-lower-group/TECH.md`);
the per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–03.

## Why five phases

- **P1** commits the replays red against `b31c66e`'s source. R8 requires (a) and (b) to fail
  there.
- **P2** is the coupled-core fix in `data/model.py` and `Scheduler`, made as one coherent edit,
  and it turns P1 green.
- **P3** is the `ServerThread` slice for file input (R3), with its integration test. That test is
  never run red, because on the base it can hang.
- **P4** is docs only.
- **P5** proves the 2.8.4 port and runs the final gate.

Nothing needs cluster jobs. Cross-matrix evidence comes from hosted CI after publish.

## Conventions (apply to every phase)

- **Rules.**
  - Commit conventions, style and invariants come from [`AGENTS.md`](../../AGENTS.md). The
    curated footguns are [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md)
    §1, §3–§6 and §12.
  - Make one atomic, signed commit per phase, holding the change and this file's state change.
    Subject: `[fix] Build group-advance-strands-lower-group P<n>: …`. **No `Co-Authored-By` or
    other trailer.** Stage by explicit path.
  - There is no CLI change, so `docs/_include/*_help.rst`, `*_usage.rst` and `share/` stay
    untouched. Never build Sphinx or man pages.
  - Comments are declarative statements. **No R-IDs or phase ids in source or tests.**
- **Kill timers.** Every `verify:` defines `kt SECONDS CMD…`. It runs `CMD` in a new session
  under `uv run` (so `pytest` resolves to the project's), kills the whole process group when the
  time runs out, and exits 124.
  - Run any ad-hoc `pytest`, `hs` or `hsx` command the same way. The integration suite has no
    per-test timeout, and on the base the bug hangs forever.
  - Before reporting, scan for and kill any stray `hs`, `hsx` or `pytest` process you started.
- **Known flake.** A TLS client dialing `localhost` can reach a foreign IPv6 listener and exit
  with rc 5 and `ConnectionResetError`. That flake has its own fix cycle. A suite step in a
  `verify:` reruns only the failed tests, once (`--lf`).
  - Record both runs in the commit body: the failed node ids and the signature, which `-l` shows
    in the traceback's `stderr`.
  - A second failure is red.
  - A first failure with a different signature is a finding even if the rerun passes. Log it in
    the commit body and the build report.
  - CLI drives pass `--no-tls`, so they cannot hit it.
- **Port placement.** Every `src/` edit must sit where the three lines on each side are the same
  on 2.8.3 (`origin/master`), because P5 applies this cycle's diff with `git apply -3`, and a
  change next to a line that differs on 2.8.3 conflicts. See PLAN §2.2 and §2.3 for the placements
  (`load_bundle`: only the `Task.next` assignment and the line after `if self.tasks:`;
  `Scheduler.__init__`: before `self.current_group = None`; `ServerThread`: `start_threads` and
  `stop`, not `__init__`). To pre-check a `src/` edit, run `git merge-tree` against
  `origin/master` (read-only).
- **Run location.** `verify:` commands run from the repo root. The CLI drives and integration
  tests bind a loopback port, so run them where local binding is allowed. A sandbox that forbids
  it fails `hsx` in `find_available_ports`, which is an environment fault, not a red verify. The
  worktree may start with no `.venv`; the first `uv run` creates it.
- **Stream drives.** Never drive stream input (a pipe) in a verify. A pre-existing early FINAL
  (PLAN §5 risk 1) can drop piped tasks, so such a drive would flake for a reason outside this
  cycle.

---

## Phase P1 — Red replays and the halted-restart guard (tests only)
**Satisfies:** R8 (a, b red half), R1, R4, R5, R6 (red replays), R7 (guard) · **Depends on:** —
**Goal:** commit deterministic replays that fail **by assertion** on `b31c66e`'s source, plus a
guard that is green before and after. **No `src/` edit in this phase.**

- [x] Create `tests/test_group_order.py`, with every test in this phase `@mark.unit`. Its module
      docstring says the tests drive `Scheduler.load_bundle` in-process against a throwaway
      SQLite file and commit other writers' rows through a separate session.
- [x] Copy `tests/test_retry_gate.py`'s `task_db` fixture and its separate-session write
      helpers: `succeed(task_id)`, `fail(task_id, exit_status=1)`, and
      `commit_tasks(*groups) -> List[str]`, which adds `Task.new` rows through
      `Session.session_factory()` and returns their ids. The file must not import from another
      test module.
- [x] Add `make_scheduler(**options)`. It builds `Scheduler(queue=None, poll=0, attempts=1, …)`,
      requires `start()` to return LOAD, and otherwise calls `pytest.fail`.
- [x] Add `run_until_final(scheduler, limit=40)`. It drives `load_bundle()` and records each
      bundle's groups in order. It completes each bundle with `succeed` and stops on FINAL. It
      returns `(order, reached_final)` and never loops unbounded.
- [x] Preconditions use `pytest.fail(...)`, never `assert`, so a broken replay cannot pass as the
      expected failure. Assertions about the fixed behaviour use `assert`.
- [x] Capture the late-lower-group warning with
      `caplog.set_level(logging.DEBUG, logger='hypershell')` and match the records whose message
      contains `after task group`.
- [x] `test_next_group_sees_rows_committed_during_check`. Add `strict=True`,
      `raises=AssertionError` and a declarative reason as `xfail` options.
  - Setup: `commit_tasks(0, 2)`, then start, so the scheduler is in group 0. The first bundle
    must be the group-0 row (precondition). Succeed it.
  - Arm the injection: a wrapper on `Task.increment_group` sets a flag for the length of the
    call. A one-shot wrapper on `Task.count_retryable` runs `commit_tasks(0, 1)` only while that
    flag is set. Install both with `monkeypatch`.
  - Run one `load_bundle()`. The injection must have fired (precondition).
  - Assert `scheduler.current_group == 0`, then that the next bundle is the new group-0 row.
  - `run_until_final` then gives order `[0, 1, 2]` and FINAL, with no late-lower warning.
- [x] `test_late_lower_group_runs_after_current`: strict `xfail` as above, and
      `@mark.parametrize('forever_mode', [False, True])`.
  - Setup: `commit_tasks(1)`, then start, so the scheduler is in group 1. The first bundle is
    group 1 (precondition). Then `commit_tasks(0)`, and succeed the group-1 row.
  - Drive at most 10 passes. Assert the group-0 row is scheduled, and exactly one late-lower
    record that names groups 0 and 1.
  - Not `forever`: succeed it and assert FINAL. `forever`: assert no FINAL in those passes.
- [x] `test_ascending_input_below_zero_runs_in_order`: strict `xfail`.
  - Setup: an empty database, then start, so the start group is provisional. Then
    `commit_tasks(-1, -1, 0)` before any `load_bundle`.
  - `run_until_final` gives order `[-1, -1, 0]` and FINAL, with no late-lower warning.
- [x] `test_restart_runs_stranded_lower_group`: strict `xfail`.
  - Setup: build the hung database. Add a group-1 row with `schedule_time` and a successful
    completion, and an unscheduled group-0 row.
  - `make_scheduler(restart_mode=True)`, then `run_until_final` gives `[0]` and FINAL, with no
    late-lower warning (nothing started this run before group 0).
- [x] `test_restart_on_halted_group_still_halts`: a guard, with no `xfail`.
  - Setup: a group-0 row scheduled and failed (`exit_status=1`, attempt 1, `attempts=1`), and an
    unscheduled group-1 row. `make_scheduler(restart_mode=True)`.
  - The next `load_bundle()` returns FINAL with a `Failed task group 0` record, and the group-1
    row has no `schedule_time`.
- [x] Red record. Run `kt 300 pytest -m unit --runxfail tests/test_group_order.py` (with `kt`
      defined as in `verify:`). It **must fail**, on the four replays only, and every case must
      fail by `AssertionError`. Paste its short summary into the commit body.
- **Verify:** the frontmatter `verify:`. It checks `git diff --quiet b31c66e -- src`, then runs
  `pytest -m unit -rxX tests/test_group_order.py`, which exits 0 only if every replay XFAILs by
  assertion and the guard passes. Strict mode means an XPASS fails the run.
- **Touches:** `tests/test_group_order.py`.

## Phase P2 — Atomic next-group choice, late-lower warning, provisional first group
**Satisfies:** R1, R2, R4, R5, R6, R7, R8 (green half) · **Depends on:** P1
**Goal:** the P1 replays go green, with #68's gates and everything else unchanged. The change is
coupled core and is made as one edit.

- [x] `data/model.py`: add `Task.lowest_incomplete_group() -> Optional[int]`, which is
      `Session.query(func.min(Task.group)).filter(Task.completion_time.is_(None)).scalar()`.
      Its docstring says it is the smallest group holding a task that has not completed, or None.
- [x] `Task.increment_group`: keep the three gates and their order verbatim. Replace the last
      query with `lowest_incomplete_group()`:
  - `None` returns `'no task groups remain'`, as today.
  - `== previous_group` returns `TaskGroupInfo(previous_group, '<n> new tasks submitted')`, or a
    similar declarative reason.
  - Anything else returns `TaskGroupInfo(value)`.

  Update the docstring: the method may now move to a lower group. Add a declarative comment that
  the next group is chosen in one query, so rows committed during the checks are not skipped,
  and that it may be lower when tasks arrive late.
- [x] `server.py` `Scheduler`:
  - Add the annotations and the `__init__` attributes `first_group_pending = False`,
    `highest_started_group = None` and `last_scheduled_group = None`. Place them before
    `current_group` (port placement).
  - `start()`: in the empty-database `else:` branch, set `self.first_group_pending = True`.
  - `load_bundle()`: replace the `self.tasks = Task.next(...)` assignment. While
    `first_group_pending`, resolve `group = Task.lowest_incomplete_group()`. If it is `None`, set
    `self.tasks = []` without calling `Task.next`. Otherwise set `self.current_group = group` and
    call `Task.next` as before. Add a declarative comment: on an empty-database start, no task is
    scheduled into a group chosen before any task existed.
  - Add `mark_group_started()`, called as the first line inside `if self.tasks:`. It logs the
    late-lower WARNING when `current_group != last_scheduled_group` and `current_group` is below
    `highest_started_group`, then updates both attributes and clears `first_group_pending`. The
    message names both groups and contains `after task group` (PLAN §2.2).
  - **Do not touch the stop branch**, `increment_group`'s call site, or the back-off.
- [x] `tests/test_group_order.py`: remove the four `xfail` markers.
- [x] Pre-check the port. Run `git diff b31c66e -- src > "$TMPDIR/cyc.diff"`, then
      `git merge-tree` to confirm `server.py`'s hunks don't overlap or touch 2.8.3's differing
      lines. The P5 trial is the authority. This is only an early warning.
- [x] Do **not** touch `tests/test_groups.py`, `tests/test_retry_gate.py` or any integration
      test.
- [x] Review-1 remediation, F1 (triage in `REVIEW.md`). `Scheduler.start` keeps its line that
      reports `Task.current_group()` and takes its start from `starting_group(group)`:
  - the most recently scheduled group, while it holds a failure awaiting retry or one that has used
    its retries (`increment_group` holds or halts it), so the halted-restart path is unchanged;
  - otherwise the lowest group with incomplete tasks;
  - with none, the provisional first group (`first_group_pending`), as on an empty database.

  `starting_group` sits after `mark_group_started` (port placement). Red first, on `31d8771`'s
  `src`: `test_new_file_after_finished_run_runs_in_order[False|True]` (the reviewer's replay: an
  ascending file on a database whose earlier run finished groups 0-2) and
  `test_tasks_after_finished_run_start_at_lowest_group` (`forever`, the tasks arrive after the
  start).
- [x] Review-2 remediation, N1 (narrow ruling; triage in `REVIEW.md`). SQLite stores a
      non-integer group such as `group:x` as text, which has no order against an integer group.
      `mark_group_started` catches the `TypeError` from its late-group comparison: it logs no
      warning and keeps `highest_started_group` unchanged, and still records
      `last_scheduled_group`. There is no coercion or rejection at ingest, so what base accepts is
      unchanged. Audit every comparison or arithmetic on group values this cycle added, in
      `server.py` and `data/model.py`, and record it in the build record.
- [x] Red first, on `5c7f381`'s `src`: `test_cluster_runs_group_stored_as_text` (`integration`):
      `echo 0  # HYPERSHELL: group:0` and `echo x  # HYPERSHELL: group:x` through
      `hs cluster FILE -N1 --no-tls`, under a 120 s timer; after the fix rc 0 and both tasks
      succeeded.
- [x] Review-3 remediation, O1 (maintainer's ruling, narrow fix then a scoped review; triage in
      `REVIEW.md`). `mark_group_started` tracks only a group that orders against an integer group
      (`numbers.Real`) as `highest_started_group`, so a text group started first never becomes
      the tracked value and the late-group warning still fires for later integer groups. A text
      group is never compared, so it still never raises (the N1 tolerance), and
      `last_scheduled_group` is still recorded for every group.
- [x] Red first, at `eca86c8`'s `src`: `test_late_lower_group_warns_after_text_group_started_first`
      (`unit`, the reviewer's scenario replayed in-process on SQLite under `forever_mode`): group
      `x` is scheduled first, then 5, then a late 0 committed while 5 runs; the one late-group
      warning names groups 0 and 5.
- **Verify:** the frontmatter `verify:`.
  - `tests/test_groups.py` and `tests/test_retry_gate.py` are identical to `b31c66e`, and there
    is no `xfail` in the new file.
  - The unit tests of the new file and of `test_retry_gate.py` pass.
  - **R6/R1 CLI drive** in a throwaway site:
    - `hs submit` a group-1 task, then `hsx --restart -N1` runs it.
    - `hs submit` a group-0 task, then `hsx --restart -N1` again.
    - Expect exit 0, 0 remaining, the group-0 task succeeded, and no `after task group` in
      stderr.
    - On `b31c66e` the second run hangs, so the drive is under `kt 300`.
  - The text-group integration test passes (rc 0, both tasks succeeded). It uses `--no-tls`, so
    the TLS flake cannot reach it.
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/server.py`,
  `tests/test_group_order.py`.

## Phase P3 — File input is ingested before the scheduler starts
**Satisfies:** R3, R8 (c), R7 · **Depends on:** P2
**Goal:** `hs cluster FILE` and `hs server FILE` run an unsorted file's groups in ascending order.
Stream input, `--no-db`, `--forever` and bare `--restart` are unchanged.

- [x] `server.py`: add the module function `is_file_input(source) -> bool`, with a docstring
      (PLAN §2.3).
  - Unwrap with `inner = getattr(source, 'iterable', source)`, with a declarative comment that a
    gated source carries the stream it wraps as `iterable`. Do not import `GatedSource`.
  - Return true for a `list` or `tuple`, and for an `io.IOBase` that is not `sys.stdin` and
    whose `seekable()` is true. Return false otherwise.
  - Export it if `server.py` keeps an `__all__`.
- [x] `ServerThread.start_threads`: start the submitter, then confirm, heartmonitor and receiver.
  Then, if `self.scheduler is not None`:
  - if `self.submitter is not None and is_file_input(self.submitter.source)`, log INFO
    (`Waiting for task file to be submitted before scheduling`, or similar) and call
    `self.wait_submitter()`;
  - then `self.scheduler.start()`.

  `__init__` is not touched (port placement).
- [x] `ServerThread.stop`: `self.scheduler.stop(wait=wait and self.scheduler.ident is not None,
      timeout=timeout)`. Add a declarative comment that a scheduler not yet started, because a
      file is still being ingested, has nothing to join.
- [x] `tests/test_group_order.py`, two additions:
  - `test_file_input_classification` (`unit`, parametrized). A seekable temp file opened `'r'`,
    a list and a tuple give True. `sys.stdin`, an `os.pipe()` read end (via `os.fdopen`) and a
    generator give False. A tiny local wrapper with `.iterable` set to the temp file gives True,
    and one set to the pipe gives False. Close every handle.
  - `test_cluster_runs_unsorted_file_in_group_order` (`integration`, takes `temp_site`),
    parametrized over layouts `[1, 0, 1, 0]` and `[0, -1, 0, -1]`, written as
    `echo N  #HYPERSHELL: n:N group:G`. Run `main(['hs', 'cluster', str(taskfile), '-N2'])`.
    - rc is success, and every task printed.
    - From `main(['hs', 'list', '--json'])`: for each pair of adjacent groups, the lower group's
      max `completion_time` is no later than the higher group's min `schedule_time`.
    - `Completed task group L - starting task group H` appears exactly once, for the ascending
      pair, and there is no `after task group`.
    - It must not depend on timing: no sleeps and no repetition.
- [x] Never run the integration test against `b31c66e` without `kt`. It can hang there.
- [x] Review-1 remediation, F2 (narrow ruling). `ServerThread.wait_scheduling()` returns once the
      scheduler has started, and raises the server's error if it stopped first. `LocalCluster`,
      `RemoteCluster`, `AutoScalingCluster` and `SSHCluster` call it between `server.start()` and
      launching their clients, so for file input no client they manage starts before the file is
      in. `ClientScheduler.start` restarts the idle clock, since `LocalCluster` builds its client
      before the file is read. `RemoteCluster.stop` and `SSHCluster.stop` handle no client having
      been launched. Every edit sits where 2.8.3 has the same lines.
- [x] Red first, on `31d8771`'s `src`: `test_cluster_clients_with_timeout_wait_for_file`
      (`integration`): `hs cluster FILE -N2 -T1 --no-tls` on 6,000 tasks (bundles of one, so the
      read takes several times the client timeout), under a 180 s kill timer; after the fix rc 0
      and every task succeeded.
- **Verify:** the frontmatter `verify:`.
  - `tests/test_group_order.py` and `tests/test_groups.py` pass, with the one-rerun flake rule.
  - **Unsorted-file CLI drive:** groups listed `1, 0, -1, 1, 0, -1` through `hsx t.in -N2`. By
    `schedule_time`, the distinct group sequence is exactly `-1 0 1`, 0 remain, and no
    `after task group` appears in stderr.
- **Touches:** `src/hypershell/server.py`, `tests/test_group_order.py`.

## Phase P4 — Group-order docs, AGENTS.md and invariants
**Satisfies:** R10 · **Depends on:** P2, P3
**Goal:** the three texts state how file input and stream input order their groups. The CLI help
is unchanged.

- [x] `docs/_include/task_groups_alt.rst`: add a short paragraph after "The server advances
  through groups automatically…", in the file's voice (first-person plural, `*HyperShell*`
  italics):
  - A task file given to `hs cluster`/`hs server` is read in full before scheduling begins, so
    its groups run in ascending order whatever order the file lists them.
  - Input on standard input or a pipe is scheduled as it arrives, which is best effort. A lower
    group that arrives after a higher group has started runs once the current group finishes,
    with a warning.
  - Optionally, one sentence on `-T/--timeout` versus a long file read (PLAN §5 risk 2, if the
    foreman keeps it).
- [x] `AGENTS.md` § Task lifecycle contract, the "Group-gating can stall by design" paragraph:
  - the next group is the smallest one with incomplete tasks, and may be lower when tasks arrive
    late;
  - file input is ingested whole first, so its groups run in order;
  - stream input is best effort, and a late lower group runs after the current one with a
    warning.

  Keep it terse.
- [x] `.agents/factory/invariants.md` §3, the group-gating bullet: the same three facts, in
  lockstep with `AGENTS.md`.
- [x] No change to `docs/_include/*_help.rst`, `*_usage.rst` or `share/`. No Sphinx or man build.
- [x] Review-1 remediation. F2: the `-T`/`--timeout` sentence says that `hs cluster` starts its
      clients once the file is read, and that a `hs server FILE` client with a shorter timeout exits
      before any task is scheduled while the server waits for new clients. F3: `ROADMAP.md` and
      `issues/group-order-unsorted-inline-file.md` state the ingest-first resolution. The empty
      task file hang is seeded as `issues/empty-task-file-hangs.md` with its `ROADMAP.md` entry. The
      verify's unchanged list names `src/hypershell/cluster/__init__.py` (the CLI) in place of the
      whole `cluster` package, which F2 edits.
- [x] Review-2 remediation. N2: `issues/empty-task-file-hangs.md` and its `ROADMAP.md` entry say
      the hang is on an empty database. The confirmed idle-clock reading: the `-T` paragraph of
      `docs/_include/client_help.rst`, which already documents `-d`/`--delay-start`, says the
      timeout counts from when the client starts, after any delay. That file is prose only and
      is not in this phase's unchanged list; the CLI help strings are untouched. The deferred
      "database is locked" hang is seeded as `issues/database-locked-during-ingest-hangs.md`
      (unshaped, fix, small, public) with its `ROADMAP.md` entry.
- [x] Review-3 remediation. O2: `issues/integration-test-hang-timeout.md` and its `ROADMAP.md`
      entry say the integration tests run each command as a subprocess (`subprocess.run` in
      `tests.main`, `tests/__init__.py`) with no timeout, not in process, and point the fix at a
      subprocess or per-test timeout, not thread handling. The maintainer's seed: a non-integer
      group is accepted at submit time, seeded as `issues/group-tag-integer-validation.md`
      (unshaped, fix, small, public) with its `ROADMAP.md` entry.
- **Verify:** the frontmatter `verify:`.
  - Each of the three files mentions the whole file being read first (`whole file`,
    `entire file` or `in full`), `best effort`, and `warning`.
  - `share/`, the CLI wiring and the cluster, server and submit help and usage snippets are
    identical to `b31c66e`.
- **Touches:** `docs/_include/task_groups_alt.rst`, `AGENTS.md`, `.agents/factory/invariants.md`.

## Phase P5 — 2.8.4 port trial and final gate
**Satisfies:** R9, R7, R2 · **Depends on:** P1, P2, P3, P4
**Goal:** prove the `src/` and `tests/` change ports to 2.8.3 after #68 with no hand edits, and
pass the whole `develop` suite. **No `src/` or `tests/` edit in this phase.** If the trial shows a
conflict, fix the placement in P2's or P3's files under a P5 amendment note, and rerun.

- [x] R9 static checks (in `verify:`):
  - the only `src/` files changed since `b31c66e` are `data/model.py` and `server.py`;
  - no added or removed line touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP`
    or `_USAGE`.
- [x] Run [`port-trial.sh`](port-trial.sh) under `kt 2400`. It works in a detached `origin/master`
      worktree under the main checkout's `.worktrees/`, resolved from
      `git rev-parse --git-common-dir`.
  - It applies #68's `src/` and `tests/` diff and resolves its one known conflict, the stop
    branch of `load_bundle`, by script: it keeps 2.8.3's `not self.startup_phase` and adds #68's
    `count_retryable` conjunct and comment.
  - It applies this cycle's `git diff b31c66e HEAD -- src tests`, which must be conflict-free.
  - It runs `uv sync --frozen`, then runs `tests/test_group_order.py`,
    `tests/test_retry_gate.py` and `tests/test_groups.py` under its own `kt 1200`, with the
    one-rerun flake rule.
  - It removes the worktree on exit and checks that `master` did not move.
- [x] If `port-trial.sh` is killed (exit 124), its exit trap does not run. Remove the leftover
      worktree with `git worktree remove --force <main>/.worktrees/port-2.8.4-trial` and
      `git worktree prune` before retrying. Never touch the `master` branch, never cherry-pick
      onto it, and never push.
- [x] If `uv sync --frozen` cannot build the 2.8.3 environment here (no network or cache), stop
      and report an environment fault. Do not mark the phase done.
- [x] Full `develop` suite under `kt 2400`, with the one-rerun flake rule.
- [x] Build record: paste the trial's applied-file list, its pytest summary, and the full
      suite's summary (plus any rerun) into the commit body and below.
- [x] Review-1 remediation: the static check's `src/` list adds `client.py` and the three cluster
      launchers (F2). Rerun the port trial and the full suite on 3.11, 3.12, 3.13 and 3.14.
- [x] Review-2 remediation: rerun the port trial at the N1 commit, and the full suite once on each
      of 3.11, 3.12, 3.13 and 3.14. The known flakes (the TLS localhost `ConnectionResetError`,
      and `test_upgrade_cli.py::test_waits_for_slow_peer_upgrade`) may each be rerun once, and
      every rerun is recorded.
- [ ] Review-3 remediation: rerun the port trial at the O1 commit, and the full suite once on
      each of 3.12 and 3.14 (the maintainer's gate), with the same flake rule.
- **Verify:** the frontmatter `verify:`.
- **Touches:** `spec/group-advance-strands-lower-group/TECH.md` (state and build record) only.


---

## Build record

**Environment substitution (every phase).** The build host has no network, so `uv run` cannot
resolve the project environment. Each verify ran with `uv run ` removed and a frozen, offline
environment's interpreter first on `PATH` instead. Each environment was built from this
worktree's `uv.lock` with `uv sync --frozen --offline` against a local uv cache and a local
CPython (3.11, 3.12, 3.13, 3.14), into a directory outside the repo
(`UV_PROJECT_ENVIRONMENT`), so the project is installed editable from this worktree. The verify
strings themselves are unchanged. `HYPERSHELL_*` variables were cleared and `TMPDIR` pointed at a
scratch directory for each run.

**P1 (red first, on `b31c66e`'s `src`).** `pytest -m unit --runxfail tests/test_group_order.py`
(3.12): `5 failed, 1 passed`. Every failure is an `AssertionError`:
- `test_next_group_sees_rows_committed_during_check`: `assert scheduler.current_group == 0`
  gave `1 == 0`.
- `test_late_lower_group_runs_after_current[False]` and `[True]`: the group-0 row was never
  scheduled (`[] == [(0, …)]`).
- `test_ascending_input_below_zero_runs_in_order`: order `[0]`, expected `[-1, -1, 0]`.
- `test_restart_runs_stranded_lower_group`: order `[]`, expected `[0]`.
- The guard `test_restart_on_halted_group_still_halts` passed.

Verify (3.12): `1 passed, 5 xfailed`, rc 0.

**P2.** The empty-database start and the late-lower warning live in two new `Scheduler`
methods, `next_tasks()` and `mark_group_started()`, placed after `load_bundle`, where both
branches are identical. `load_bundle` changes only the `Task.next` assignment (now
`self.tasks = self.next_tasks()`) and the first line inside `if self.tasks:`. The same-group
reason from `increment_group` is `new tasks submitted`. A `git merge-file` pre-check of each
`src/` file onto `origin/master` (with #68's `model.py`) is conflict-free. Verify (3.12):
unit tests `28 passed`; the restart drive exits 0 with `Completed task group 1 - starting task
group 0`, 0 remaining, the group-0 task succeeded, and no late warning.

**P3.** `is_file_input` sits before `serve_from`, and `import io` and the `__all__` entry sit where
both branches match; the `git merge-file` pre-check of `server.py` onto `origin/master` stays
conflict-free. The integration test checks success from `hs list -c -S` rather than task stdout,
which the suite treats as best effort; the plan's "every task printed" is met through the
database. Verify (3.12): `tests/test_group_order.py tests/test_groups.py` `27 passed` on the first
run (no rerun); the unsorted-file drive ran groups `-1 0 1` by `schedule_time`, 0 remaining, no
late warning. A separate file drive logged `Waiting for task file to be submitted before
scheduling`.

**P4.** The three texts carry the file-order and stream-best-effort statements, and the docs carry
the one `-T/--timeout` sentence the foreman's ruling asked for (accepted risk). Verify passed. The
foreman's plan-gate ruling on PLAN §5 risk 1 is recorded as a seed,
`issues/stream-run-stops-before-input-ends.md`, with its `ROADMAP.md` entry.

**P5 (port trial and final gate).**
- R9 static checks passed: `src/` changes are exactly `data/model.py` and `server.py`, and no added
  or removed line touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP` or `_USAGE`.
- `port-trial.sh` on `origin/master` at `4d5771b` (2.8.3), unedited:
  - #68 conflicted only in `src/hypershell/server.py` (`model.py` applied cleanly). The script's
    resolution replaced the one conflict block in `load_bundle`'s stop branch with 2.8.3's
    `... and not self.startup_phase` plus #68's
    `Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0`
    conjunct and its two-line comment. No markers remained.
  - This cycle applied with no conflict: `M src/hypershell/data/model.py`,
    `M src/hypershell/server.py`, `A tests/test_group_order.py` (with #68's
    `A tests/test_retry_gate.py`).
  - Tests (`test_group_order.py`, `test_retry_gate.py`, `test_groups.py`): `49 passed`, no rerun.
  - The worktree was removed, and `master` did not move.
- **Environment substitution for the trial.** With no network, `uv sync --frozen` first failed on
  `jinja2` (from the default `docs` group), which was not in the offline cache. The trial then ran
  with `UV_NO_GROUP=docs`, `UV_OFFLINE=1`, a local uv cache and a local CPython 3.12, and the
  environment placed outside the repo through `UV_PROJECT_ENVIRONMENT`. The script was not edited,
  and it still ran `uv sync --frozen` and `uv run --frozen pytest` against 2.8.3's lock.
- Full `develop` suite, first run, no rerun: 3.12 `647 passed, 64 skipped`; 3.14 `647 passed, 64
  skipped`; 3.11 `647 passed, 64 skipped`.
- 3.13: `1 failed, 646 passed, 64 skipped`. The failure was
  `tests/test_upgrade_cli.py::test_waits_for_slow_peer_upgrade` (`assert len(waits) >= 2`, with
  no wait line in the child's stderr). That is not the TLS signature, so it is a finding. The test
  holds the lock for a fixed 2 s after starting a child interpreter, and on that slow run (13 min)
  the child had not started waiting before the lock was released. The rerun (`--lf`) gave `1
  passed`. This cycle touches no upgrade code.

**Review-1 remediation (triage in `REVIEW.md`), P2–P4.** Same environment substitution as above.
- **F1, how the ruling is read.** Taken literally, "incomplete rows" in the last scheduled group
  would keep the reviewer's replay at group 2: the new file's group-2 row is incomplete, and
  `Task.revert_interrupted` has already cleared any in-flight mark before the group is chosen.
  That replay could then never go green, and R10's "groups run in ascending order" would stay
  false. So the hold that matters for a restart, failures to retry or halt on, keeps the last
  group; incomplete rows lead to the lowest group with incomplete tasks, which is the last group
  itself unless a lower one also has some. The halted-restart guard is unchanged and green.
- **F1 red** (3.12, `src` at `31d8771`): `test_new_file_after_finished_run_runs_in_order[False]`,
  `[True]` and `test_tasks_after_finished_run_start_at_lowest_group` failed, each with
  `assert [2, 0, 1] == [0, 1, 2]`. **Green:** `-m unit tests/test_group_order.py
  tests/test_retry_gate.py` `39 passed`.
- **F2 red** (3.12, `src` at `31d8771`): `test_cluster_clients_with_timeout_wait_for_file` failed,
  `hs cluster did not finish within 180 seconds` (killed by its timer). **Green:** passed in 51.9 s.
- `tests/test_groups.py` is unmodified and passes. Its
  `test_group_starts_based_on_previous_scheduled_tasks` matches `Starting with task group 5` on the
  line that reports `Task.current_group()`, which `Scheduler.start` keeps; a separate debug line
  names a different start group.
- Verify (3.12): P2 rc 0 (`39 passed`; the restart drive ran group 0 with no late warning); P3
  rc 0 (`31 passed`, no rerun; the unsorted drive ran `-1 0 1`); P4 rc 0.
- CLI drives (3.12, `--no-tls`, throwaway sites): two ascending files in turn, `--repeat` of the
  same file, `hs submit` then `hsx --restart`, and an ascending pipe after a finished run all ran
  `0 1 2` after the earlier `0 1 2`, with no late warning. A halted group still halts under
  `--restart` (CRITICAL, the higher group not run). A bad tag exits rc 5 in 0.3 s. `--launcher=env
  -H 127.0.0.1 -T1` on 3,000 tasks: rc 0, all succeeded. `--no-db` and a pipe run.

**Review-1 remediation, P5 (port trial and final gate).**
- R9 static checks passed: `src/` changes are exactly `client.py`, `cluster/local.py`,
  `cluster/remote.py`, `cluster/ssh.py`, `data/model.py` and `server.py`, and no added or removed
  line touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP` or `_USAGE`.
- `port-trial.sh` on `origin/master` at `4d5771b` (2.8.3), unedited, at `fccb67f`: #68 conflicted
  only in `server.py`, resolved by the script as before. This cycle applied with no conflict:
  `M` `client.py`, `cluster/local.py`, `cluster/remote.py`, `cluster/ssh.py`, `data/model.py`,
  `server.py`, `A tests/test_group_order.py` (with #68's `A tests/test_retry_gate.py`). Tests
  (`test_group_order.py`, `test_retry_gate.py`, `test_groups.py`): `53 passed`, no rerun. The
  trial worktree was removed, the worktree list is unchanged, and `master` stayed at `dd0264c`.
- **Environment substitution for the trial**, as before: `UV_NO_GROUP=docs`, `UV_OFFLINE=1`,
  `UV_PYTHON_DOWNLOADS=never`, a local CPython 3.12, `UV_PROJECT_ENVIRONMENT` outside the repo, and
  `UV_CACHE_DIR` pointed at a private copy of a local uv cache that holds `hatchling` (the default
  cache is not writable from the build sandbox). The script was not edited.
- Full `develop` suite, first run, no rerun: 3.12.16 `651 passed, 64 skipped`; 3.11.18 `651
  passed, 64 skipped`; 3.13.18 `651 passed, 64 skipped`; 3.14.10 `651 passed, 64 skipped`. Neither
  known flake appeared.

**Review-2 remediation (triage in `REVIEW.md`), P2 and P4.** Same environment substitution as above.
- **Readings the foreman confirmed.**
  - **The F1 hold.** `starting_group` keeps the last scheduled group only while every task in it
    is complete and a failure there still awaits retry or has used its retries
    (`Task.count_remaining(group=group) == 0`). If that group holds a failure and also gets new
    rows, the start is the lowest group with incomplete tasks. This keeps R3's ascending order
    and the R7 gate (nothing above the failed group runs, and the retry still runs before the run
    stops), and matches the cycle-1 ratification.
  - **The idle clock.** `ClientScheduler.start` restarts the idle clock, so a standalone client's
    `-d`/`--delay-start` no longer counts toward `-T`/`--timeout`: `hs client -d 4 -T 3` lives
    about 8.5 s, against 6.4 s on base. No command that succeeds today fails, and plain idle `-T`
    timings are unchanged. `docs/_include/client_help.rst` (which documents `-d`) now says so
    under `-T`.
- **N1 red** (3.12, `src` identical to `5c7f381`): `test_cluster_runs_group_stored_as_text`
  failed, `assert 5 == 0`. The same file through the CLI gave rc 5 with
  `TypeError: '<' not supported between instances of 'str' and 'int'` from the Scheduler, after
  `Completed task group 0 - starting task group x`; the `x` row (`typeof(group)='text'`) was left
  scheduled with no completion. **Green:** the test passes; the CLI drive gives rc 0, both rows
  completed with exit 0, and `x` runs after group 0, as on base.
- **N1 audit:** every comparison or arithmetic on a group value that this cycle added. SQLite
  orders an integer before text, and Python equality between a `str` and an `int` is false, not
  an error; only ordering raises.

  | Site | Operation on group values | Safe with a text group? | Change |
  |------|---------------------------|-------------------------|--------|
  | `Task.lowest_incomplete_group` (`data/model.py`) | SQL `min("group")` over incomplete rows | Yes: SQLite orders it, with integers before text; no Python comparison | None |
  | `Task.increment_group` (`data/model.py`) | `next_group is None`, `next_group == previous_group` | Yes: identity and equality only | None |
  | `Scheduler.next_tasks` (`server.py`) | `first_group is None`, then assignment | Yes | None |
  | `Scheduler.mark_group_started` (`server.py`) | `group != last_scheduled_group` | Yes: equality | None |
  | `Scheduler.mark_group_started` (`server.py`) | `group < highest`, `max(group, highest)` (the only Python-side `min`/`max`) | **No:** `TypeError` (N1) | Fixed: the `TypeError` is caught; no warning, `highest_started_group` kept, `last_scheduled_group` still recorded |
  | `Scheduler.starting_group` (`server.py`) | `count_remaining(group=…) == 0`, `count_retryable(group=…) > 0`, `lowest is None`, `lowest != group` | Yes: the group is an SQL filter, and Python compares integer counts or uses identity and equality | None |
  | `Scheduler.start` (`server.py`) | Assignment from `starting_group(…)` | Yes | None |
  | Log messages naming groups (`server.py`, `data/model.py`) | f-string formatting | Yes | None |
  | `client.py`, `cluster/local.py`, `cluster/remote.py`, `cluster/ssh.py` | No group values | Not applicable | None |

  No group arithmetic was added. Pre-existing SQL group filters (`Task.next`, `Task.current_group`)
  are unchanged.
- **Audit probes** (3.12, `--no-tls`, fresh sites, after the fix), each rc 0 with no `TypeError`:
  a file listing `x`, `0`, `1` ran `0 1 x`; a file of only `x` and `y` ran both; a finished `x`
  run followed by a file of groups 0 and 1 ran both; `hs submit` of groups 1 and `x` with
  `hsx --restart`, then a late group 0 with `hsx --restart`, ran all three; and a `--forever`
  cluster that had started `x` then ran a late `hs submit` group 0 (`Completed task group x -
  starting task group 0`), which exercises the caught comparison mid-run, with no warning.
- Named gates (3.12): `tests/test_group_order.py tests/test_groups.py tests/test_retry_gate.py`
  `54 passed`, no rerun. Verify (3.12, `uv run` stripped, frozen offline venv): P2 rc 0 (`39
  passed` unit; the restart drive ran group 0 with no late warning; the text-group test `1
  passed`); P4 rc 0.
- **TECH amendment:** P2's verify adds the text-group integration test, through
  `set_phase.py --verify`.

**Review-2 remediation, P5 (port trial and final gate).**
- R9 static checks passed: `src/` changes are exactly `client.py`, `cluster/local.py`,
  `cluster/remote.py`, `cluster/ssh.py`, `data/model.py` and `server.py`, and no added or removed
  line touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP` or `_USAGE`.
- `port-trial.sh` on `origin/master` at `4d5771b` (2.8.3), unedited, at `dbfa759`: #68 conflicted
  only in `server.py`, resolved by the script as before. This cycle applied with no conflict:
  `M` `client.py`, `cluster/local.py`, `cluster/remote.py`, `cluster/ssh.py`, `data/model.py`,
  `server.py`, `A tests/test_group_order.py` (with #68's `A tests/test_retry_gate.py`). Tests
  (`test_group_order.py`, `test_retry_gate.py`, `test_groups.py`): `54 passed`, no rerun. The
  trial worktree was removed, the worktree list is unchanged, and `master` stayed at `dd0264c`.
- **Environment substitution for the trial**, as before: `UV_NO_GROUP=docs`, `UV_OFFLINE=1`,
  `UV_PYTHON_DOWNLOADS=never`, a local CPython 3.12.16, `UV_PROJECT_ENVIRONMENT` outside the repo,
  and `UV_CACHE_DIR` pointed at a private copy of a local uv cache that holds `hatchling`. The
  script was not edited.
- Full `develop` suite, one run each: 3.12.16 `652 passed, 64 skipped`; 3.13.18 `652 passed, 64
  skipped`; 3.14.10 `652 passed, 64 skipped`, none needing a rerun.
- 3.11.18: `1 failed, 651 passed, 64 skipped`. The failure was the known flake
  `tests/test_upgrade_cli.py::test_waits_for_slow_peer_upgrade`, and its rerun (`--lf`) gave `1
  passed`. **The signature differs from the one recorded in P5 above, so it is a finding:**
  `sqlite3.OperationalError: database is locked` from the lock holder's own `COMMIT`
  (`tests/test_upgrade_cli.py:322`), after the test's fixed 2 s sleep, instead of
  `assert len(waits) >= 2`. The likely cause is that the child process held a read lock at that
  moment, so the holder could not take the exclusive lock its commit needs: the same fixed-sleep
  timing race, which fails in a different place. The test and `data/upgrade.py` are identical to
  `b31c66e`, and this cycle touches no upgrade code. Neither TLS flake appeared.

**Review-3 remediation (maintainer's ruling, triage in `REVIEW.md`), P2 and P4.** Same environment
substitution as above.
- **O1 red** (3.12, `src` identical to `eca86c8`; the tree's `src` had no change since):
  `test_late_lower_group_warns_after_text_group_started_first` failed 2 of 2 on `assert 0 == 1`
  (no late-group warning) after the scheduler ran `x`, then 5, then the late 0. **Green:** 2 of 2
  passed after the fix.
- **The fix.** `mark_group_started` gates its comparison on `isinstance(group, numbers.Real)`, in
  place of the N1 `try`/`except TypeError`. Every value SQLite or PostgreSQL returns for the
  column is an `int`, a `float`, a `str`, `bytes` or `None`, so a `Real` group is always ordered
  against the tracked one, which is itself always `Real`; a text group is never compared, never
  tracked, and never raises. `last_scheduled_group` is still recorded for every group. The N1
  audit table above is otherwise unchanged.
- **CLI drive** (3.12, `--no-tls`, fresh sites): a `--forever` cluster fed one-line files by
  `hs submit -f`, 6 s apart. `x, 5, 2` ran `x 5 2` and warned "Starting task group 2 after task
  group 5" (no warning before the fix, per review cycle 3); `5, 2` and `5, x, 2` warned the same.
  No `TypeError` anywhere. N1's two-line file (`group:0`, then `group:x`) gave rc 0, both rows
  done. Pipes with pauses exited early on the known stream-stop item, so they were not used.
- **O2** and **the maintainer's seed:** prose only; every `issues/` link in `ROADMAP.md` resolves.
- Named gates (3.12): `tests/test_group_order.py tests/test_groups.py tests/test_retry_gate.py`
  `55 passed`, no rerun. Verify (3.12, `uv run` stripped, frozen offline venv): P2 rc 0 (`40
  passed` unit; the restart drive ran group 0 with no late warning; the text-group test `1
  passed`); P4 rc 0.

---

## How `hs-build` drives this

1. `next_phase.py` prints the next actionable phase (statuses are authoritative; the
   `current_phase` pointer is reconciled against them).
2. Pre-flight: clean tree, on `branch`, `base` reachable.
3. Execute every `[ ]` in the phase (consult `PLAN.md` / `research/` for detail).
4. Run the phase's `verify:` command — never advance on a checkbox alone.
5. Amend this file freely if reality diverges (regenerate frontmatter with `set_phase.py`; note the
   amendment in the commit body). STOP and escalate only on a **`GOAL.md` contradiction**.
6. Mark the phase `done`, advance `current_phase`, `--touch`; one `[fix]` commit; stop and report.
