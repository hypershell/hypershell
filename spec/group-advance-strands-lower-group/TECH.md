---
slug: group-advance-strands-lower-group
title: Run task groups in order, and never strand a late one
kind: fix
appetite: small
status: in_progress
branch: fix/group-advance-strands-lower-group
base: develop
current_phase: P4
last_updated: '2026-11-03'
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
    -S -t n:0) -eq 1 && ! grep -q ''after task group'' err.log"'
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
  status: pending
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
    }; done ) && git diff --quiet b31c66e -- share src/hypershell/__init__.py src/hypershell/cluster
    docs/_include/cluster_help.rst docs/_include/cluster_usage.rst docs/_include/server_help.rst
    docs/_include/server_usage.rst docs/_include/submit_help.rst docs/_include/submit_usage.rst
- id: P5
  name: 2.8.4 port trial and final gate
  status: pending
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
    '' '')" = ''src/hypershell/data/model.py src/hypershell/server.py '' && ! git
    diff b31c66e -- src | grep -E ''^[-+].*(mapped_column|__tablename__|add_argument|_HELP|_USAGE)''
    && kt 2400 sh spec/group-advance-strands-lower-group/port-trial.sh && { kt 2400
    pytest -q -l > "${TMPDIR:-/tmp}/hs-p5.log" 2>&1 && tail -n 3 "${TMPDIR:-/tmp}/hs-p5.log"
    || { tail -n 40 "${TMPDIR:-/tmp}/hs-p5.log"; grep -E ''^(FAILED|ERROR)'' "${TMPDIR:-/tmp}/hs-p5.log";
    echo ''One rerun of the failed tests (known TLS localhost flake): record both
    runs''; kt 2400 pytest -q -l --lf ; }; }'
review:
  last_reviewed_commit: ''
  verdict: none
  blocked_reason: ''
  cycle: 0
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

- [ ] `docs/_include/task_groups_alt.rst`: add a short paragraph after "The server advances
  through groups automatically…", in the file's voice (first-person plural, `*HyperShell*`
  italics):
  - A task file given to `hs cluster`/`hs server` is read in full before scheduling begins, so
    its groups run in ascending order whatever order the file lists them.
  - Input on standard input or a pipe is scheduled as it arrives, which is best effort. A lower
    group that arrives after a higher group has started runs once the current group finishes,
    with a warning.
  - Optionally, one sentence on `-T/--timeout` versus a long file read (PLAN §5 risk 2, if the
    foreman keeps it).
- [ ] `AGENTS.md` § Task lifecycle contract, the "Group-gating can stall by design" paragraph:
  - the next group is the smallest one with incomplete tasks, and may be lower when tasks arrive
    late;
  - file input is ingested whole first, so its groups run in order;
  - stream input is best effort, and a late lower group runs after the current one with a
    warning.

  Keep it terse.
- [ ] `.agents/factory/invariants.md` §3, the group-gating bullet: the same three facts, in
  lockstep with `AGENTS.md`.
- [ ] No change to `docs/_include/*_help.rst`, `*_usage.rst` or `share/`. No Sphinx or man build.
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

- [ ] R9 static checks (in `verify:`):
  - the only `src/` files changed since `b31c66e` are `data/model.py` and `server.py`;
  - no added or removed line touches `mapped_column`, `__tablename__`, `add_argument`, `_HELP`
    or `_USAGE`.
- [ ] Run [`port-trial.sh`](port-trial.sh) under `kt 2400`. It works in a detached `origin/master`
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
- [ ] If `port-trial.sh` is killed (exit 124), its exit trap does not run. Remove the leftover
      worktree with `git worktree remove --force <main>/.worktrees/port-2.8.4-trial` and
      `git worktree prune` before retrying. Never touch the `master` branch, never cherry-pick
      onto it, and never push.
- [ ] If `uv sync --frozen` cannot build the 2.8.3 environment here (no network or cache), stop
      and report an environment fault. Do not mark the phase done.
- [ ] Full `develop` suite under `kt 2400`, with the one-rerun flake rule.
- [ ] Build record: paste the trial's applied-file list, its pytest summary, and the full
      suite's summary (plus any rerun) into the commit body and below.
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
