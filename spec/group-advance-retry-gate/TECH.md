---
slug: group-advance-retry-gate
title: Don't advance a task group past a pending retry
kind: fix
appetite: small
status: done
branch: fix/group-advance-retry-gate
base: develop
current_phase: done
last_updated: '2026-11-01'
phases:
- id: P1
  name: Red replays and unchanged-behaviour guards (tests only)
  status: done
  satisfies:
  - R4
  - R8
  - R5
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: git diff --quiet c1d4470 -- src && uv run pytest -m unit -rxX tests/test_retry_gate.py
- id: P2
  name: Shared retryable predicate gates group advance and stop
  status: done
  satisfies:
  - R1
  - R2
  - R3
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
  verify: 'git diff --quiet c1d4470 -- tests/test_groups.py && ! grep -q xfail tests/test_retry_gate.py
    && uv run pytest && .agents/factory/bin/temp_site.sh sh -c "printf ''%s\n'' ''echo
    0 #HYPERSHELL: n:0 group:0'' ''[ \$TASK_ATTEMPT -eq 3 ] && echo 1 #HYPERSHELL:
    n:1 group:0'' ''echo 2 #HYPERSHELL: n:2 group:1'' > t.in && uv run hsx t.in -r
    3 -N2 && test \$(uv run hs list -c) -eq 5 && test \$(uv run hs list attempt -t
    n:1 -S) -eq 3 && test \$(uv run hs list group -t n:2 -S) -eq 1 && test \$(uv run
    hs list -c -R) -eq 0" && .agents/factory/bin/temp_site.sh sh -c "printf ''%s\n''
    ''echo 0 #HYPERSHELL: n:0'' ''[ \$TASK_ATTEMPT -eq 3 ] && echo 1 #HYPERSHELL:
    n:1'' > t.in && uv run hsx t.in -r 3 -N2 && test \$(uv run hs list -c) -eq 4 &&
    test \$(uv run hs list attempt -t n:1 -S) -eq 3 && test \$(uv run hs list -c -R)
    -eq 0"'
review:
  last_reviewed_commit: 1c51ebbc1b9ffd31816e16f84dacebd3b46efe9b
  verdict: approved
  blocked_reason: ''
  cycle: 1
---
# TECH.md — Don't advance a task group past a pending retry

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above
is the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/group-advance-retry-gate/TECH.md`); the
per-phase checklists below are the work. `hs-build` executes the next actionable phase, runs its
`verify:` command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes
one atomic code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs 01–02.

## Why two phases

This plan has two phases because R4 and R8 require the replays to be **shown red on `c1d4470`
before the fix**. P1 commits the tests alone, against unchanged source, and verifies them red by
assertion. P2 lands the fix, which is small and coupled-core (`data/model.py` + `server.py`, so it
is one coherent edit), and verifies them green alongside the full suite and two CLI drives. No
phase needs cluster jobs. R6's cross-matrix evidence is hosted CI after publish, and no phase waits
on it.

## Conventions (apply to every phase)

- Commit conventions, style, and invariants come from [`AGENTS.md`](../../AGENTS.md); curated
  footguns in [`.agents/factory/invariants.md`](../../.agents/factory/invariants.md) §1–§5.
- One atomic commit per phase containing **both** the change and this file's state change.
  Subject: `[fix] Build group-advance-retry-gate P<n>: …`. **No `Co-Authored-By` trailer.**
- No CLI change, so **no** `docs/_include/*.rst` or `share/` updates (§12 does not trigger).
- Comments are declarative statements; **no R-IDs or phase ids in source or tests** (AGENTS.md).
- `verify:` commands run from the repo root. The CLI drives and the integration tests bind a
  loopback port: run them where local port binding is allowed (a sandbox that forbids it fails
  `hsx` with `StopIteration` in `find_available_ports` — an environment fault, not a red verify).

---

## Phase P1 — Red replays and unchanged-behaviour guards (tests only)
**Satisfies:** R4, R8 (red half), R5 (guards) · **Depends on:** —
**Goal:** commit deterministic replays of both interleavings that fail **by assertion** on
`c1d4470`'s source, plus guards that pin today's behaviour of the paths that must not change.
**No `src/` edit in this phase.**

- [x] Create `tests/test_retry_gate.py`; every test `@mark.unit`.
- [x] Fixture `task_db(tmp_path)`: file SQLite engine (`check_same_thread=False`),
      `Entity.metadata.create_all`; `Session.remove()` **then** `Session.configure(bind=engine)`;
      teardown `Session.remove()`, rebind to `hypershell.data.core.engine`, `engine.dispose()`.
      This keeps in-process queries off the developer's per-user database
      ([research 01](research/01-test-harness.md)).
- [x] Helper `fail(task_id, exit_status=1)` / `succeed(task_id)`: commit `exit_status` and
      `completion_time` through a separate session (`Session.session_factory()`), as the Receiver
      thread would.
- [x] Helper `inject_after_next(monkeypatch, action)`: one-shot wrapper on `Task.next` — call the
      real method; if it returned `[]`, run `action`; restore the original; return the result.
      (`load_bundle` calls `Task.next` with keywords only.)
- [x] Preconditions (e.g. "this LOAD scheduled attempt 2") use `pytest.fail(...)`, **never
      `assert`**, so a broken replay can't masquerade as the expected failure.
- [x] Drive `Scheduler(queue=None, attempts=3, eager=eager, poll=0)` via `start()` then
      `load_bundle()`; `poll=0` makes the hold's back-off `sleep(0)`. No sleeps, threads or timing.
- [x] `test_group_advance_waits_for_pending_retry`, `@mark.parametrize('eager', [False, True])`,
      `@mark.xfail(strict=True, raises=AssertionError, reason=<declarative: group advance skips a
      failure committed after Task.next>)`: failing task in group 0 + a group-1 task → attempt 2 in
      flight → inject `fail(attempt 2)` → `load_bundle()` returns LOAD, `current_group == 0`, no
      `Completed task group` / `Failed task group` record (`caplog.set_level(logging.DEBUG,
      logger='hypershell')`) → next `load_bundle()` returns PACK with one task (group 0, attempt 3,
      `previous_id` = attempt 2) → no group-1 row has `schedule_time`.
- [x] `test_stop_waits_for_pending_retry`, parametrized `eager` × `layout ∈ {no-groups,
      last-group}` (last-group: a succeeding task in group 0, the failing task in group 1), same
      strict `xfail`: attempt 2 in flight → inject `fail` → `load_bundle()` is not FINAL → next
      returns PACK with attempt 3 → `fail(attempt 3)` → `load_bundle()` returns FINAL.
- [x] Guards (no `xfail`; green now and after the fix): exhausted failure in group 0 with a group 1,
      `attempts ∈ {1, 3}` — non-`forever` → FINAL + `Failed task group 0` record + group 1
      unscheduled; `forever_mode=True` → LOAD, `current_group == 0`. Cancelled (`CANCEL_STATUS`)
      row in group 0 with a group 1, `attempts=3` → advances to group 1. Group-0 task fails
      attempt 1, attempt 2 succeeds → advances to group 1. Single succeeding group → FINAL.
- [x] Red record: run `uv run pytest -m unit --runxfail tests/test_retry_gate.py`; it **must
      fail**, on the replay cases only. Paste its short failure summary into the commit body.
- **Verify:** `git diff --quiet c1d4470 -- src && uv run pytest -m unit -rxX tests/test_retry_gate.py`
  — exits 0 only if `src/` is `c1d4470`'s, every replay case fails by `AssertionError` (strict:
  an XPASS or any other exception fails the run), and every guard passes.
- **Touches:** `tests/test_retry_gate.py`.

## Phase P2 — Shared retryable predicate gates group advance and stop
**Satisfies:** R1, R2, R3, R4, R5, R6, R7, R8 · **Depends on:** P1
**Goal:** one predicate decides "awaiting retry" for selection, group advance and stop; the P1
replays go green with everything else unchanged.

- [x] `data/model.py`: add `Task.query_retryable(attempts, group=None) -> Query` holding
      `select_failed`'s filter chain verbatim (`exit_status IS NOT NULL`, `!= 0`,
      `!= CANCEL_STATUS`, `attempt < attempts`, `retried IS FALSE`, optional `group`).
- [x] Rewrite `Task.select_failed` on it: `query_retryable(...).order_by(completion_time)
      .limit(limit).all()` — same signature, same rows, same order.
- [x] Add `Task.count_retryable(attempts, group, eager=False) -> int` — `group` required; returns
      `0` when `attempts <= 1 and not eager` (mirrors `Task.next`: non-eager selects failures only
      when `attempts > 1`), else `query_retryable(attempts, group).count()`. Docstring: the count is
      exactly the failures `Task.next` retries for the same `group`, `attempts` and `eager`.
- [x] `Task.increment_group(previous_group, attempts, eager=False)`: after the incomplete-rows hold
      and **before** the exhausted-failure check, hold with
      `TaskGroupInfo(previous_group, f'waiting on {n} failed tasks to be retried')`. Declarative
      comment: incomplete first so a result committed between checks is seen; retryable before
      exhausted so a group holding both is held, not halted.
- [x] `server.py` `Scheduler.load_bundle`: stop branch gains
      `Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0`
      **after** `Task.count_remaining() == 0`; pass `eager=self.eager` to `increment_group`.
      Declarative comment on the stop conjunct. No other change to the branch, logging or back-off.
      `count_remaining` and `Scheduler.start` stay as they are.
- [x] `tests/test_retry_gate.py`: remove the two `xfail` markers. Add
      `test_hold_matches_retry_selection`, parametrized `attempts ∈ {1, 2, 3, 4}` × `eager`: one
      group of only complete rows (success; `CANCEL_STATUS`; failure at attempt 1; failure at
      attempt 3; retried failure + successful successor; `-9`; `-1001`; user-set `attempt=0`
      failure) plus a retryable failure in another group; assert
      `Task.count_retryable(attempts, 0, eager)` equals the number of **distinct** rows
      `Task.next(limit=100, group=0, attempts=attempts, eager=eager)` returns, and every returned
      row has `previous_id` set. *(Amended in build: eager `Task.next` lists each new retry row
      twice when the bundle limit has room, because `select_new` re-selects the retry rows just
      committed with `schedule_time` NULL; the raw `len` read 10 against 5 retries. The test
      compares by row identity. The duplication is pre-existing and out of scope here.)*
- [x] `AGENTS.md`: "Group-gating can stall by design" — `increment_group` won't advance while the
      group has incomplete tasks **or failures still awaiting retry**; and name
      `Task.query_retryable`/`count_retryable` alongside `select_failed` in the
      `attempts == max_retries + 1` sentence. (`invariants.md` stays accurate; no harness edit.)
- [x] Do **not** touch `tests/test_groups.py` or any integration test.
- **Verify:** the frontmatter `verify:` — `tests/test_groups.py` byte-identical to `c1d4470`; no
  `xfail` left in `tests/test_retry_gate.py`; full `uv run pytest`; then two CLI drives in a
  throwaway site: a **grouped retry** (n:1 in group 0 succeeds only on attempt 3, n:2 in group 1 →
  5 rows, n:1's successful attempt is 3, n:2 succeeded in group 1, 0 remaining) and an
  **ungrouped retry** (same, no groups → 4 rows, attempt 3 succeeded, 0 remaining).
- **Touches:** `src/hypershell/data/model.py`, `src/hypershell/server.py`,
  `tests/test_retry_gate.py`, `AGENTS.md`.

**Build record (P2 verify, 2026-11-01).** Frontmatter `verify:` exited 0: `tests/test_groups.py`
identical to `c1d4470`, no `xfail` in `tests/test_retry_gate.py`, full `uv run pytest` 483 passed.
CLI drives, re-run with their post-conditions printed (`hs list group attempt exit_status args`):

```
grouped retry:   rows=5 n1_ok_attempt=3 n2_ok_group=1 remaining=0
0	1	0	echo 0
0	1	1	[ $TASK_ATTEMPT -eq 3 ] && echo 1
1	1	0	echo 2
0	2	1	[ $TASK_ATTEMPT -eq 3 ] && echo 1
0	3	0	[ $TASK_ATTEMPT -eq 3 ] && echo 1
ungrouped retry: rows=4 n1_ok_attempt=3 remaining=0
0	1	0	echo 0
0	1	1	[ $TASK_ATTEMPT -eq 3 ] && echo 1
0	2	1	[ $TASK_ATTEMPT -eq 3 ] && echo 1
0	3	0	[ $TASK_ATTEMPT -eq 3 ] && echo 1
```

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
