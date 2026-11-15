---
slug: task-input-fidelity
title: 'Every task the input gives runs as given: date-like lines run as text, and
  a streamed run waits for its input to end'
kind: fix
appetite: small
status: blocked
branch: fix/task-input-fidelity
base: develop
current_phase: done
last_updated: '2026-11-15'
phases:
- id: P1
  name: 'Decode the task wire by declared type: date-like text stays text, times stay
    times, bytes unchanged'
  status: done
  satisfies:
  - R1
  - R2
  - R3
  - R4
  - R8
  depends_on: []
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_task_wire.py tests/test_writeback.py tests/test_writeback_statement.py
    tests/test_relay_return.py tests/test_topology_server.py tests/test_contended_write.py
    && for m in dates dates-default dates-stdin dates-nodb; do .agents/factory/bin/temp_site.sh
    uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh" $m || exit 1;
    done
- id: P2
  name: The scheduler waits for the end of stream input before it stops, without reaching
    the failed-group halt
  status: done
  satisfies:
  - R5
  - R6
  - R7
  - R8
  depends_on:
  - P1
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q tests/test_stream_input.py tests/test_group_order.py tests/test_server_fsm.py
    tests/test_restart.py tests/test_cluster_exit.py tests/test_autoscale_failed_launches.py
    && for m in pause fail-pause burst server fifo nodb-stream file launch-false;
    do .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh"
    $m || exit 1; done
- id: P3
  name: 'Optional: wake the scheduler at the end of input so a stream run stops promptly'
  status: done
  satisfies:
  - R6
  depends_on:
  - P2
  parallel: false
  hammerable: true
  hill: uphill
  verify: uv run pytest -q tests/test_stream_input.py tests/test_dispatch.py tests/test_dispatch_staged.py
    tests/test_dispatch_version.py tests/test_signal_wake.py tests/test_server_fsm.py
    && FD_POLL=600 FD_BOUND=10 .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh"
    pause && .agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh"
    fail-pause
- id: P4
  name: Docs, the invariant record and seeds; cross-version, both Pythons, every provider
  status: done
  satisfies:
  - R4
  - R8
  - R9
  depends_on:
  - P2
  parallel: false
  hammerable: false
  hill: uphill
  verify: uv run pytest -q && mkdir -p $TMPDIR/hs-2.9.0a8 && git archive 2.9.0a8 src
    | tar -x -C $TMPDIR/hs-2.9.0a8 && for m in cross-old-client cross-old-server;
    do FD_OLD_SRC=$TMPDIR/hs-2.9.0a8/src .agents/factory/bin/temp_site.sh uv run sh
    "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh" $m || exit 1; done &&
    UV_PROJECT_ENVIRONMENT=$TMPDIR/docs-venv uv run --python 3.12 --group docs sphinx-build
    -W --keep-going -q -E -b html docs $TMPDIR/docs-html
review:
  last_reviewed_commit: 0b84a745b0fa63ac1450ed7d67f28eb10c1b175d
  verdict: changes-requested
  blocked_reason: 'F1 (LOW, prose): invariants.md §3 and AGENTS.md overstate the scheduler
    stop rule; human gate'
  cycle: 1
---
# TECH.md — Every task the input gives runs as given

The **context engine and finite-state machine** for building this fix. The YAML frontmatter above is
the resume ground-truth (read it with
`uv run python .agents/factory/bin/next_phase.py spec/task-input-fidelity/TECH.md`); the per-phase
checklists below are the work. `hs-build` executes the next actionable phase, runs its `verify:`
command, updates state via `uv run python .agents/factory/bin/set_phase.py …`, and makes one atomic
code+state commit.

- **Vision / requirements (locked):** [`GOAL.md`](GOAL.md) — R-IDs are the contract.
- **Authoritative design:** [`PLAN.md`](PLAN.md).
- **Backing research:** [`research/00-digest.md`](research/00-digest.md) + briefs `01`, `02`.
- **Drive:** [`drive/fidelity_drive.sh`](drive/fidelity_drive.sh) — run in a throwaway site from the
  repository root: `.agents/factory/bin/temp_site.sh uv run sh "$PWD/spec/task-input-fidelity/drive/fidelity_drive.sh" MODE`.

## Frontmatter field reference

As in [`.agents/factory/templates/TECH.md`](../../.agents/factory/templates/TECH.md).

## Conventions (apply to every phase)

- [`AGENTS.md`](../../AGENTS.md) is the constitution; the touched invariants are walked in
  [`PLAN.md`](PLAN.md) § 3. `data/model.py` and `server.py` are high-blast-radius: serialize the edits.
- One phase per `hs-build` invocation; one atomic commit with the code and the `TECH.md` state change,
  subject `[fix] Build task-input-fidelity P<n>: …`. **No `Co-Authored-By` trailer.**
- Comments are declarative statements; no R-ids in source. Tag every new test `unit` or `integration`.
- No CLI change in this cycle: no help snippet or completion changes. The user-visible stream change
  gets its `.. versionchanged:: 2.9.0` in P4.
- Environment notes: `UV_CACHE_DIR` under `$TMPDIR`. PostgreSQL runs need the sandbox off and
  `HS_TEST_POSTGRES=postgresql+psycopg://postgres:postgres@localhost:5432/postgres`
  (`HS_TEST_TIMESCALE=community` for the `timescale` provider); create and drop your own databases
  (the drive does, under `FD_PROVIDER`). Python 3.11 runs use their own environment:
  `UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run --python 3.11 …`, never re-syncing `.venv`.
- Overlap with #109 (`fix/contended-writes`, unmerged): keep this cycle's `model.py` change to
  `Entity.from_json`; do not touch `Task` write paths, `test_contended_*.py` or `test_source.py`
  (PLAN § 5).

### Dry-run on `develop` (plan time, 2026-11-14)

Every drive mode was run against `develop`'s code (this branch's `src/`, unchanged) and against a
scratch prototype outside the tree. **Expected to fail on `develop`:** `dates`, `dates-default`,
`dates-stdin`, `dates-nodb` (date lines `-1001`, only `hello` runs) and `pause`, `fail-pause` (the
late line never runs); `burst`, `server` and `fifo` fail on `develop` the same way (shaping's
reproductions; not re-run on `develop` at plan time). **Pass on `develop` (guards that must stay
green):** `file`, `nodb-stream`, `launch-false`, `cross-old-client`, `cross-old-server`. The new test
files do not exist yet; P1 and P2 write them tests-first, so their `pytest` targets fail on `develop`
until the code lands.

---

## Phase P1 — Decode the task wire by declared type
**Satisfies:** R1, R2, R3, R4, R8 · **Depends on:** —
**Goal:** a date-like line reaches the client as text and a date-like result is stored as text; every
timestamp still decodes as a time; the encoded bytes equal 2.9.0a8's.

- [x] Tests first, `tests/test_task_wire.py` (`@mark.unit`), per PLAN § 6 and research/01's list:
      every R1 form (`20261102`, `2026-11-02`, `2026W45`, `2026-W45-1`, `20261102T1200`,
      `2026-11-02 12:00`) in `args`, `command`, `outpath`, `errpath`, `csvpath`, `submit_host`,
      `server_host`, `client_host`, through `deserialize_tasks(serialize_tasks(…))` and
      `Task.from_json(json.loads(json.dumps(task.to_json())))`; the four time fields, naive and aware,
      with and without microseconds (equal value and `utcoffset`); a date-like `tag` value; `None`, an
      int and `'not-a-time'` in a time field pass through; an unknown key still raises `TypeError`;
      every SQLAlchemy `DateTime` column of `Task`, `Client`, `Source`, `Zone` is `datetime` in
      `columns`; template expansion of each form (`'echo {}'` and the default template) gives the exact
      text; `Task.apply_results` stores `command='2026-11-02'` verbatim (SQLite `task_db` fixture) and
      the row matches only its dispatch.
- [x] R4 fixture: `tests/fixtures/make-task-wire-2.9.0a8.sh` extracts `git archive 2.9.0a8 src` under
      `$TMPDIR`, encodes a fixed task (fixed UUIDs, aware times with and without microseconds, one naive
      time, `args='2026-11-02'`, `tag={'day': '2026-11-02'}`) with that code, and prints the dispatch
      text, `serialize_tasks([task])` and the result bundle (`command='2026-11-02'`, client fields,
      start/completion times, `waited`, `duration`, `exit_status=0`). Paste the three outputs as
      `bytes` literals into `test_task_wire.py`; add a provenance row to `tests/fixtures/README.md`.
      Tests: today's encoding equals the literals; the literals decode with text and times intact.
- [x] PostgreSQL twin of the `apply_results` test in `tests/test_writeback_postgres.py` (skipped
      without `HS_TEST_POSTGRES`; runs under both providers as that file's others do).
- [x] Confirm the new tests fail on the unchanged code (the text-field and template cases; the R4 and
      time cases pass already).
- [x] `src/hypershell/data/model.py` `Entity.from_json`: decode a value with `from_json_type` only
      when `cls.columns.get(key) is datetime`; docstring says so. Nothing else in the file.
- [x] Evidence (record in the commit body): the verify command on 3.14; `UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run --python 3.11 pytest -q tests/test_task_wire.py`;
      with the sandbox off, `HS_TEST_POSTGRES=… uv run pytest -q tests/test_writeback_postgres.py` and
      again with `HS_TEST_TIMESCALE=community`; the `dates` modes with `FD_PROVIDER=postgres` and
      `FD_PROVIDER=timescale`.
- **Verify:** `uv run pytest -q tests/test_task_wire.py tests/test_writeback.py tests/test_writeback_statement.py tests/test_relay_return.py tests/test_topology_server.py tests/test_contended_write.py` and the four `dates*` drive modes.
- **Touches:** `src/hypershell/data/model.py`, `tests/test_task_wire.py`, `tests/test_writeback_postgres.py`, `tests/fixtures/`.

## Phase P2 — The scheduler waits for the end of stream input
**Satisfies:** R5, R6, R7, R8 · **Depends on:** P1
**Goal:** a non-`forever` scheduler with a database stops only once its submitter has ended and every
task is terminal; nothing else about when a run ends changes.

- [x] Tests first, `tests/test_stream_input.py` (reuse `tests/test_group_order.py`'s `task_db`,
      `commit_tasks`, `succeed`, `fail`, `make_scheduler`; a fake submitter whose `is_alive()` the test
      flips), `@mark.unit`:
      `test_open_input_holds_the_stop[restart_mode]` (first task done → passes return LOAD; a late
      line is the next bundle; flip → FINAL);
      `test_input_ending_with_nothing_left_stops`;
      `test_submitter_ending_between_the_counts_is_seen` (a patched count commits a row and ends the
      submitter mid-pass: that pass is not FINAL and the row is scheduled next);
      `test_failure_with_open_input_waits_for_late_line` (attempts 1: no `Failed task group` while the
      input is open; the late line runs; flip → FINAL, no critical);
      `test_failed_group_still_halts_late_higher_group` (R7 guard);
      `test_scheduled_work_ignores_open_input` (`cluster/watch.py::scheduled_work` False with all done
      and input open; True once a row is committed).
      `@mark.integration`: `test_local_cluster_runs_input_after_a_pause` — a `LocalCluster` (one
      in-process client, bundlesize 1, small poll) fed by a generator that yields `echo a`, blocks on
      an event that a wrapped `Scheduler.load_bundle` sets on the first pass after `a` completed, then
      yields `echo b` and ends; assert that pass returned LOAD, both rows exit 0, and the cluster ends.
      It needs `monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)` with the `task_db`
      binding (else `ServerThread` falls back to `in_memory`); fall back to the child-script pattern of
      `tests/test_cluster_exit.py` under `temp_site` if the in-process binding fights the cluster.
- [x] Confirm they fail on the unchanged scheduler (each R5 case reaches FINAL early).
- [x] `src/hypershell/server.py` `Scheduler.load_bundle`: read `submission_complete()` before the
      counts; when every task is terminal and nothing is retryable, return FINAL if the input has
      ended, else back off without `increment_group`/`report_held`. Move the back-off tail into a
      `back_off(generation, reason)` method used by both branches. Remove `Scheduler.startup_phase`
      (annotation, init, clear, read); keep `restart_mode`. Update `submission_complete`'s docstring.
- [x] `src/hypershell/cluster/watch.py` `scheduled_work`: docstring only — committed work counts,
      input still being read does not.
- [x] Evidence (commit body): the verify command; the stream modes with `FD_PROVIDER=postgres` and
      `FD_PROVIDER=timescale` (`pause`, `burst`, `fail-pause`; sandbox off); `pause` and `burst` on 3.11.
- **Verify:** `uv run pytest -q tests/test_stream_input.py tests/test_group_order.py tests/test_server_fsm.py tests/test_restart.py tests/test_cluster_exit.py tests/test_autoscale_failed_launches.py` and the drive modes `pause fail-pause burst server fifo nodb-stream file launch-false`.
- **Touches:** `src/hypershell/server.py`, `src/hypershell/cluster/watch.py`, `tests/test_stream_input.py`.

## Phase P3 — Optional: a prompt stop at the end of input
**Satisfies:** R6 · **Depends on:** P2 · **hammerable** (the foreman may cut it at the plan gate;
without it R6 holds within `server.poll`)
**Goal:** a stream run ends within a second or two of its input ending and its last task finishing,
whatever `server.poll` is.

- [x] Tests first: a unit test that `Scheduler.wake()` ends a back-off at once, including a wake that
      lands before the wait starts (lossless); the P2 integration test with `poll=600` still ends well
      inside its join timeout.
- [x] `src/hypershell/core/dispatch.py`: public `Dispatcher.wake()` — bump the need generation under the
      dispatcher's lock and notify (as its own `_signal_need` callers do). Not on `DISPATCH_EXPOSED`.
- [x] `src/hypershell/server.py`: `Scheduler.wake()` calls it; `ServerThread.run_with_exceptions` calls
      `self.scheduler.machine.wake()` (when there is a scheduler) between `wait_submitter()` and
      `wait_scheduler()`. No other change to the shutdown sequence.
- **Verify:** `uv run pytest -q tests/test_stream_input.py tests/test_dispatch.py tests/test_dispatch_staged.py tests/test_dispatch_version.py tests/test_signal_wake.py tests/test_server_fsm.py`, then `pause` with `FD_POLL=600 FD_BOUND=10`, then `fail-pause`.
- **Touches:** `src/hypershell/core/dispatch.py`, `src/hypershell/server.py`, `tests/test_stream_input.py`.

## Phase P4 — Docs, the record, seeds, and full evidence
**Satisfies:** R4, R8, R9 · **Depends on:** P2 (and P3 when built)
**Goal:** the change is documented, the invariant record matches the code, the deferred findings are
seeded, and the whole evidence plan of `GOAL.md` passes.

- [x] `docs/cli/cluster.rst`, `docs/cli/server.rst`: `.. versionchanged:: 2.9.0` beside the existing
      2.9.0 markers — a run fed by a stream (standard input, a pipe, a named pipe) ends once that input
      ends and every task is done. `docs/_include/task_groups_alt.rst`: one sentence that a stream with
      long pauses wants no `-T` (or autoscaling). No prose for the date-like fix (PLAN § 2.4).
- [x] `AGENTS.md` and `.agents/factory/invariants.md` in lockstep: payload fields decode by their
      declared column type, only `datetime` columns as times (§6/§9 and "Shutdown/sentinel ordering");
      a non-`forever` scheduler stops only once its submitter has ended (read before the counts) and
      every task is terminal (§3 group-gating paragraph). Keep clear of #109's paragraphs.
- [x] Seeds from [`templates/ISSUE.md`](../../.agents/factory/templates/ISSUE.md), `status: unshaped`,
      each with a `ROADMAP.md` entry: a client idle past `-T` during a pause leaves a late line with no
      one to run it; with `-b N>1` a line read before a pause waits for the next line to be committed.
- [x] Evidence (commit body): the full suite on 3.14 and on 3.11
      (`UV_PROJECT_ENVIRONMENT=$TMPDIR/venv311 uv run --python 3.11 pytest -q`); with the sandbox off,
      `HS_TEST_POSTGRES=… uv run pytest -q tests/test_writeback_postgres.py tests/test_timescale.py tests/test_upgrade_postgres.py`
      and again with `HS_TEST_TIMESCALE=community`; every drive mode on 3.14, `dates` and `pause` on
      3.11, and `dates`, `pause`, `burst` under `FD_PROVIDER=postgres` and `timescale`; the two
      cross-version modes; the Sphinx build.
- **Verify:** `uv run pytest -q`, the cross-version drives against `git archive 2.9.0a8 src`, and the Sphinx `-W` build.
- **Touches:** `docs/cli/cluster.rst`, `docs/cli/server.rst`, `docs/_include/task_groups_alt.rst`, `AGENTS.md`, `.agents/factory/invariants.md`, `issues/`, `ROADMAP.md`.

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
