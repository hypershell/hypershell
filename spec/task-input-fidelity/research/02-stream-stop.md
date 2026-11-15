# 02 — Stream stop (R5–R7, R9): findings for hs-plan

Read-only pass over `ae83e66e`. Line numbers are this worktree's.

## Recommended design (one change, all in `server.py`)

In `Scheduler.load_bundle` (`server.py:246-294`), after `next_tasks` returns nothing:

```python
input_ended = self.submission_complete()  # read BEFORE the counts
if (not self.forever_mode and Task.count() > 0 and Task.count_remaining() == 0
        and Task.count_retryable(self.attempts, group=self.current_group, eager=self.eager) == 0):
    if input_ended:
        return SchedulerState.FINAL
    return self.back_off(generation, 'all tasks done, input still open')  # no increment_group, no report_held
group_info = Task.increment_group(...)   # unchanged from here
```

`back_off` is the existing tail (`:288-294`: poll doubling unless `woken`, trace, `wait_need`) moved
into a helper that both branches use. Keep `Task.count() > 0` so the empty-input hang
(`issues/empty-task-file-hangs.md`, a non-goal) stays as it is. Delete `startup_phase`.

**Do not just swap the condition to `self.submission_complete()`.** Then an all-done run with the input
open falls through to `increment_group`. With the default `attempts == 1`, any non-zero exit in the
current group returns `viable=False` (`model.py:697-708`), which logs `log.critical('Failed task group …')`
and goes to FINAL (`server.py:276-282`) while the stream is still open. So
`(echo false; sleep 8; echo 'echo b') | hsx` would still drop `b`, now with a critical log. On develop
that case is a normal early FINAL. The branch above avoids it: with nothing incomplete anywhere, it
waits. A late line in the same group is scheduled. A late line in a higher group meets the
failed-group halt exactly as develop and a file input do (R7, invariants §3).

**TOCTOU check: holds.** `SubmitThread.run_with_exceptions` (`submit.py:656-664`) runs
`loader.join_unless_left` (all lines queued), then `put_end` (FIFO, after the tasks), then
`committer.join`. The committer's `finalize` → `commit` → `Task.add_all` → `Session.commit`
(`submit.py:554-558`, `model.py:225-238`) happens before the join returns. So `is_alive()` is False
only after every row is committed. Read it first, and the counts that follow see those rows. In
today's order (counts, then `submission_complete`), a submitter that commits and exits between
them is missed. Exceptions: the `leave_input` stop path (a stop anyway), and a loader exception.
In that case `join_unless_left` re-raises before `put_end` (`submit.py:473-474`), the committer
never flushes its buffer, and `wait_submitter` raises. That is pre-existing.

## Q1 — `startup_phase` / `submission_complete` readers

- `Scheduler.startup_phase` is used only in `server.py`: annotated at `:143`, initialized at `:171`
  (`not restart_mode`), cleared at `:261`, read at `:267`. Nothing in tests or docs reads it.
  After the fix it is dead. **Remove it** (all four lines).
- Keep the `restart_mode` parameter and attribute (`:168`). `ServerThread` passes it (`:1186`), and
  `test_group_order.py:287,304,331` and `test_zone_autoscale.py:657` pass it too. Nothing reads it
  after the fix.
- `HeartMonitor.startup_phase` (`:754`, `:805`, `:936`) is a different attribute, read by
  `relay/__init__.py:723` and set by `tests/test_relay_fsm.py:880`. Untouched.
- `submission_complete` is read at `:226` (`start`) and `:267`. `tests/test_restart.py:9` names it
  in a docstring. That text stays valid.
- **`--restart` with a stream** is reachable only from `hs cluster` (`hsx - --restart`, `hsx <(…)
  --restart`): `cluster/__init__.py:381,497`. `hs server` drops the input under `--restart`
  (`server.py:1934`). Today `startup_phase` starts False, so after `start()` logs "waiting for incoming
  tasks" (`:226-229`), the first LOAD pass stops at once on a database whose earlier tasks are all
  done. Stream rows that land later are dropped. After the fix the run waits. This is a fix under R5.
  Put it in the unit test's parametrization.

## Q2 — the non-stop branch while the input is open

With the recommended branch, `increment_group` and `report_held` are skipped while nothing is
incomplete. So nothing is logged repeatedly; only a trace on each back-off. Even through
`increment_group` (the naive fix) and with no failures, it returns `(current, 'no task groups
remain')`, which is viable (`model.py:713-714`). `report_held` finds no unscheduled rows and logs
nothing. The only bad path is the failed-group one above. When a late line lands, the next pass
handles it like this:

- **Same group:** `Task.next(group=current)` loads it.
- **Other group:** `count_remaining > 0` → `increment_group` → `lowest_incomplete_group`. It logs
  "Completed task group X - starting task group Y" once, then `mark_group_started` warns once if Y
  is lower (`:337-350`). That is the stream best-effort rule in §3.
- **Failed current group:** the critical halt, as on develop and with a file.

## Q3 — back-off and a prompt wake

`self.poll` starts at `SERVER_POLL_MIN = 0.5` (`:119`) and doubles up to `poll_max = poll` (server.poll,
30 s by default). Only a wait that runs its full length grows it (`:290-293`). The wait is
`wait_need(generation, poll)` (`:296-307`), and only a pool cut, new demand, a hand-back, `forget`
or `finish` ends it early (`dispatch.py:251,259,278,345,477,501,553`). An idle client's empty request
does not. So the stop comes at most server.poll after the input ends (R6 holds).

**Recommend the cheap wake, done in `ServerThread`, not `SubmitThread`.** `run_with_exceptions`
(`:1197-1206`) already blocks in `wait_submitter()` until the input ends. Add
`self.scheduler.machine.wake()` (guarded by `scheduler is not None`) between `wait_submitter()` and
`wait_scheduler()`. `Scheduler.wake()` mirrors `halt()` (`:309-315`):
`with dispatcher.need: <bump generation>; notify_all()`. A public `Dispatcher.wake()` that calls
`_signal_need()` under its lock keeps `generation` private. Bumping the generation makes the
wake lossless: `wait_need` returns at once if the bump came before it. That is about 8 lines,
with no submit.py change and no reordering under §6. Without it, a paused pipeline lingers up to
30 s after its last line before it exits. The wake is optional under the GOAL; say so at the gate.

## Q4 — #107 failure stops

These read only committed rows or launch counts:
- `watch_launched` / `work_remains` / `scheduled_work` (`cluster/watch.py:106-154`).
- `ServerThread.stop(leave_input=True)` (`server.py:1307-1325`).
- `LoaderThread.leave` (`submit.py:459-466`) and `SubmitThread.put_end` (`submit.py:696-710`).
- The autoscaler's failed launches. `AutoScalingCluster` is forever (`remote.py:984`) and stops at
  `remote.py:1032-1038`.

The `--launcher false` stream tests (`test_cluster_exit.py:546-700`,
`test_autoscale_failed_launches.py:388-420`) always have a committed task left, so they still exit 5.

Case: every launched client exits non-zero, every committed task is done, and the stream is open.
- **Today:** the scheduler has stopped early, `scheduled_work` is False, and the run waits on the
  stream. It exits 0 when the stream ends (late lines dropped), or hangs if the producer stays open.
- **After the fix, `scheduled_work` unchanged (recommended):** the scheduler is alive and waiting on
  input, and `scheduled_work` is False, so the run keeps waiting.
  - A late line that lands → `count_remaining > 0` → exit 5 within about `WATCH_INTERVAL` +
    `SCHEDULE_SETTLE`, with `leave_input`.
  - The stream ends empty → normal exit 0.
  - A producer that stays open and silent still hangs, which is no new hang.
- **Rejected alternative, counting an open submitter as work:** it would exit 5 on a run whose work
  is all done, raced against whether the stream closes inside the 2 s settle. That changes a develop
  exit 0 (R7).

Amend the `scheduled_work` docstring. It should say that it counts committed work only, and that
input still being read is not work no client can run until a line lands.

## Q5 — paths that could now hang

The submitter's thread ends at `StopIteration` for every source:
- `GatedSource.__iter__` delegates (`submit.py:125`).
- JSON input, including `--from-json -`, is read in full into a list. A list counts as file input
  (`server.py:1332-1347`), so the scheduler starts only after the submitter ends.
- `--restart/--repeat/--update` on a stream is stdin-exempt (`cluster/__init__.py:546-553`).

Remaining paths:
- **A `serve_from` generator that never ends** runs forever. That is intended (Clarifications).
- **A committer failure.** `Task.add_all` raises and the committer dies. The loader or `put_end`
  then spins on a full queue (`submit.py:410-417,703-710`), so the submitter never ends. That hangs
  today too, in `wait_submitter`. No change.
- **Unaffected:** `in_memory` (scheduler None, `LiveSubmitThread`); `forever` (`remote.py:984`,
  `topology.py:605`, relay sites); `hs server` with a separate client; LocalCluster
  (`local.py:261-268`: client then server join).

Side finding: with `-b N > 1`, `DatabaseCommitter.get_task` checks `bundlewait` only when a task
arrives (`submit.py:529-543`). So a line read before a pause sits uncommitted until the next line or
the end of input. That is pre-existing, and it also affects `hs submit`. Candidate seed. The tests use `bundlesize=1`.

## Q6 — tests

Nothing existing expects an early stop on a stream. In-process harnesses pass no submitter
(`test_group_order.make_scheduler`, `test_server_fsm.py:355`), and CLI stream tests always end their
input or are failure stops. Reuse from `tests/test_group_order.py:45-152` (`task_db` is duplicated in
`test_server_fsm.py:73`; either is precedent): `write_back`, `fail`, `succeed`, `commit_tasks`,
`commit_finished` (`:314`), `make_scheduler(**options)` (poll=0, so back-offs are instant),
`first_bundle` and `run_until_final`. Also `tests.conftest.free_port`.

New `tests/test_stream_input.py`. The fake submitter is `class OpenInput: alive = True;
is_alive = lambda self: self.alive`.

**(a) Unit tests (`@mark.unit`):**
- `test_open_input_holds_the_stop[restart_mode=False|True]` (R5):
  1. `commit_tasks(0)`, then run and `succeed` the first bundle.
  2. Five passes return LOAD.
  3. A late `commit_tasks(0)` is the next bundle; `succeed` it. Still LOAD.
  4. Set `alive=False`; the next pass is FINAL (R6).
- `test_input_ending_with_nothing_left_stops` (R6): `commit_finished(0)`, input open → LOAD; flip it
  → FINAL.
- `test_submitter_ending_between_the_counts_is_seen`: monkeypatch `Task.count_retryable` so its
  first call commits a row and sets `alive=False`. That pass must not be FINAL, and the next bundle
  holds the row. This test fails if the counts are read first.
- `test_failure_with_open_input_waits_for_late_line` (attempts=1):
  1. `fail` the group-0 task.
  2. Passes return LOAD, with no `Failed task group` in caplog.
  3. A late group-0 line runs.
  4. Flip → FINAL, with no critical.
- `test_failed_group_still_halts_late_higher_group` (R7 guard): `fail` in group 0, input open,
  `commit_tasks(1)` → FINAL with `Failed task group 0`.
- `test_scheduled_work_ignores_open_input` (Q4): call `watch.scheduled_work` on a
  `SimpleNamespace(scheduler=SimpleNamespace(is_alive=lambda: True, machine=scheduler))`. It is
  False when all tasks are done and the input is open, and True after `commit_tasks(0)`.

**(b) `test_local_cluster_runs_input_after_a_pause` (`@mark.integration`; R5/R6, no wall-clock
races):**
- Setup: the `task_db` fixture plus `monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED',
  True)`. The suite's import-time config has no database file, so without this, `ServerThread` forces
  `in_memory` (`server.py:1164`).
- Source generator: yield `'echo a'`; then `released.wait(60)` (raise if it times out); then yield
  `'echo b'`; end.
- Before construction, `monkeypatch.setattr(Scheduler, 'load_bundle', wrapper)`. `actions` is a
  per-instance cached_property, so it binds the wrapper. The wrapper calls the real method. On the
  first pass that returns after `a` is complete (`Task.count_remaining() == 0`), it records that
  pass's state and sets `released`. On FINAL it records `self.submission_complete()`.
- `LocalCluster(source=gen(), num_threads=1, bundlesize=1, poll=1, port=free_port(),
  redirect_output=io.StringIO())`, then start and `join(timeout=60)`.
- Assert:
  - the recorded post-completion state is `[LOAD]`. Develop records `[FINAL]`, and `b` then ends
    up committed but never scheduled;
  - both rows have `exit_status == 0`;
  - FINAL was seen with the input ended;
  - the cluster ended.
- If the wake is built, add `poll=600`: the run still ends well inside the join timeout, because
  only the wake can schedule `b` promptly.
- Fallback if in-process DB binding fights the cluster: the child-script pattern of
  `test_cluster_exit.py:100-130` under `temp_site`.

## Q7 — docs (R9)

- Add one marker in each of `docs/cli/cluster.rst` (after `:50-54`, the existing 2.9.0 list beside
  the `cluster_desc` include) and `docs/cli/server.rst` (after `:30-32`). Suggested:
  `.. versionchanged:: 2.9.0` / "A run fed by a stream (standard input, a pipe or a FIFO) ends once
  that input ends and every task is done, not once the tasks read so far are done."
- No page states the old early stop.
  - `docs/_include/task_groups_alt.rst:19-26` (in `manual.rst:247`) says stream input "is scheduled
    as it arrives". It is consistent and already discusses `-T`, so it is the place for the optional
    "long pauses want no `-T`" sentence.
  - `AGENTS.md:254-260` and invariants §3/§6 do not state the old rule either. Suggest one line in
    invariants §6, or §3: "A non-`forever` scheduler stops only once its submitter has ended (read
    before the counts) and every task is terminal."

## Q8 — `-T` during a pause (out of scope)

A client idle past `-T` returns FINAL with exit 0 (`client.py:239-245`). With the fix, the server
keeps waiting on the input. A late line is then staged with no one to run it, and the run waits
with work left:
- LocalCluster: `server.join` after `client.join`, and nothing watches it.
- Launcher modes: all exits are zero, so `watch_launched` keeps waiting by design.
- Autoscaling relaunches.

Today the same run stops early and drops the line. Record a seed, per Clarifications.

## Risks

- `server.py` is high-blast. The change sits in the FSM's LOAD action, which has no FSM unit tests;
  the new unit tests cover the branch.
- Stream runs now hold until EOF. A wrapper that never closes the input (`tail -f`, a pipeline kept
  open) keeps the run open, as Clarifications intends. Expect user reports; the R9 note covers it.
- The wake bumps `dispatcher.generation`. That is harmless (only `refills` and `wait_need` read it),
  but it is a new writer outside the dispatcher's own methods unless `Dispatcher.wake()` is added.
