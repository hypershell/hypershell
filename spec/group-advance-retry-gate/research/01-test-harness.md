# 01 — Test harness for the deterministic replays

> Read-only research brief for `hs-plan`. Question: how can `unit`-tagged tests drive `Task`
> classmethods and `Scheduler.load_bundle()` in-process, against a real database, with the
> Receiver's writeback injected at an exact point and no sleeps?

## How `Session` binds in-process

- `data/core.py` builds `engine`, `sessionmaker(bind=engine)` and `Session = scoped_session(...)`
  once, at import, from the config singleton.
- `core/config.py` always preloads `database.file` to the default per-user site database. The
  `tests/conftest.py` isolation removes every `HYPERSHELL_*` variable at import, and `temp_site`
  sets `HYPERSHELL_DATABASE_FILE` only later, per test. **An in-process `Task` query therefore hits
  the developer's real per-user SQLite file, not an in-memory database and not the temp site.**
  No schema is created in-process (only `initdb`/`ensuredb` call `create_all`).
- That is why no existing test touches the database in-process: `tests/__init__.py::main` is a
  subprocess, and `test_autoscale.py`/`test_submit.py` say so explicitly. `test_source.py` builds a
  throwaway engine with `Entity.metadata.create_all`, but uses raw connections, not `Task`
  classmethods. No test constructs `Scheduler`; none calls `Task.add_all`, `next`, `update` or
  `increment_group` against a database.
- Every `Task` classmethod goes through the module-global `Session`, so rebinding it redirects all
  of them: `Session.remove()` **then** `Session.configure(bind=test_engine)` (`configure` does not
  affect a session that already exists). Teardown: `Session.remove()`, rebind to
  `hypershell.data.core.engine`, `engine.dispose()`.

## Driving `Scheduler` without a queue

- `Scheduler.__init__` only stores `queue`; `start()` and `load_bundle()` use `Task.*` and
  `check_signal()` only (`post_bundle` is the sole queue user). `Scheduler(queue=None, ...)` works.
- `check_signal()` returns the module flag, `None` unless a signal arrived.
- `poll=0` sets `poll_max = 0`, so the hold branch computes `min(0, 2 * poll) = 0` and calls
  `time.sleep(0)`. No timing enters the test.
- Call `start()` **before** anything is in flight: with remaining rows it runs
  `Task.revert_interrupted()`. Keep `restart_mode=False` and `submitter=None`, so the stop check
  behaves as in production (`submission_complete()` is true).

## Fixture and writeback

- Engine: a file SQLite database under `tmp_path`, `check_same_thread=False`, then
  `Entity.metadata.create_all(engine)`. A file database (rather than `sqlite://`) lets a second
  session see committed data and avoids single-connection pooling.
- Receiver writeback: `Receiver.update_tasks` calls `Task.update_all([task.to_dict() ...])` from its
  own thread, hence its own scoped session. A faithful replay commits the failure
  (`exit_status`, `completion_time`) through a separate session from `Session.session_factory()`;
  `Task.update(id, exit_status=..., completion_time=...)` on the shared session also reproduces
  both defects.
- Injection: keep `real_next = Task.next`, `monkeypatch.setattr(Task, 'next', one_shot)`; the
  one-shot wrapper calls `real_next(**kw)`, asserts it returned `[]`, commits the failure, restores
  `real_next` and returns `[]`. This works because `load_bundle` calls `Task.next` with keywords only.
- Caching: `bulk_update_mappings` does not refresh identity-map objects, but commits expire them and
  the gating queries (`count()`, `.first()`) always reach SQL. Re-read held rows with
  `Task.from_id(id, caching=False)`.
- Logs: the `hypershell` logger propagates, so `caplog.set_level(logging.DEBUG, logger='hypershell')`
  captures `Completed task group …` / `Failed task group …` for the R2 assertion.

## Prototype result (scratch, outside the repo)

A prototype of both replays, run against this worktree's source (identical to `c1d4470`), failed as
GOAL.md predicts for `eager=False` and `eager=True`: the group replay advanced to group 1, logged
`Completed task group 0 - starting task group 1` and next scheduled group 1's task; the stop replay
returned `FINAL`. Neither needs timing.

## Existing coverage of "unchanged otherwise" (all `integration`, via subprocess)

| Behaviour | Covered by | Gap |
|-----------|------------|-----|
| Exhausted failures stop a non-`forever` run | `test_groups.py::test_group_hard_failure_halts` | forever-mode wait: none |
| Cancelled task neither holds nor halts | `test_cancel.py::test_cancelled_task_not_retried`, `::test_cancelled_task_not_reverted_on_restart` | cancelled row in group N with a group N+1: none |
| `attempts == 1` | every cluster run without `-r` (`test_groups.py` basic/sequential/default/non-sequential/restart-group cases, `test_restart.py`) | failing task at `attempts == 1` under groups: none (the new hold is vacuous there) |
| Retried task succeeds, group advances | `test_groups.py::test_group_failed_task_with_retries` (non-eager) | eager mode: none |
| No failures reach FINAL | the `attempts == 1` list, plus `test_restart.py::test_cluster_new_file_runs_against_completed_db` | — |

## Callers

- `Task.select_failed`: `model.py` `__schedule_next_failed_tasks` only; no tests.
- `Task.increment_group`: `server.py` `load_bundle` only; no tests.
- `Task.count_remaining`: `server.py` `start` and the stop check, `cluster/remote.py` (autoscaler,
  four sites), `model.py` `time_to_completion`; `test_autoscale.py` monkeypatches it. **Its meaning
  must not change**; the stop path gets a separate retryable-failure check.
