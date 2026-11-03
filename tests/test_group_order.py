# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replays of the order in which the scheduler runs task groups.

These tests drive ``Scheduler.load_bundle`` in-process against a throwaway SQLite file.
Rows written by another process (a submitter, a later ``hs submit``, or the Receiver thread)
are committed through a separate session, injected at an exact point, so each interleaving is
replayed without sleeps, threads or timing.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Tuple
from pathlib import Path

# Standard libs
import logging
from datetime import datetime

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task
from hypershell.server import Scheduler, SchedulerState


LATE_GROUP_MESSAGE = 'after task group'
"""Substring of the warning logged when a group starts after a higher one already has."""


@fixture
def task_db(tmp_path: Path) -> Iterator[Engine]:
    """Bind the global session to a fresh per-test SQLite file, then restore it."""
    engine = create_engine(f'sqlite:///{tmp_path / "tasks.db"}', connect_args={'check_same_thread': False})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


def write_back(task_id: str, exit_status: int) -> None:
    """Commit a task result through a separate session, as the Receiver thread does."""
    session = Session.session_factory()
    try:
        session.bulk_update_mappings(Task, [{'id': task_id,
                                             'exit_status': exit_status,
                                             'completion_time': datetime.now().astimezone()}])
        session.commit()
    finally:
        session.close()


def fail(task_id: str, exit_status: int = 1) -> None:
    """Record a failed result for the task."""
    write_back(task_id, exit_status)


def succeed(task_id: str) -> None:
    """Record a successful result for the task."""
    write_back(task_id, 0)


def commit_rows(tasks: List[Task]) -> List[str]:
    """Commit new task rows through a separate session, as another writer does; return their ids."""
    ids = [task.id for task in tasks]
    session = Session.session_factory()
    try:
        session.add_all(tasks)
        session.commit()
    finally:
        session.close()
    return ids


def commit_tasks(*groups: int) -> List[str]:
    """Commit one new task per entry in `groups`, each in that group; return their ids."""
    return commit_rows([Task.new(args=f'echo {i}', group=group) for i, group in enumerate(groups)])


def make_scheduler(**options) -> Scheduler:
    """Started scheduler with no queue, zero back-off and one attempt unless given."""
    options.setdefault('attempts', 1)
    scheduler = Scheduler(queue=None, poll=0, **options)
    if scheduler.start() is not SchedulerState.LOAD:
        pytest.fail('Scheduler did not start into LOAD')
    return scheduler


def first_bundle(scheduler: Scheduler, limit: int = 4) -> List[Tuple[int, str]]:
    """Drive LOAD until a bundle is packed and return its (group, id) pairs; fail otherwise."""
    for _ in range(limit):
        state = scheduler.load_bundle()
        if state is SchedulerState.PACK:
            return [(task.group, task.id) for task in scheduler.tasks]
        if state is not SchedulerState.LOAD:
            pytest.fail(f'Expected a bundle, scheduler moved to {state}')
    pytest.fail(f'No bundle after {limit} loads')


def run_until_final(scheduler: Scheduler, limit: int = 40) -> Tuple[List[int], bool]:
    """Drive LOAD for at most `limit` passes, completing every bundle with success.

    Returns the group of each scheduled task in order, and whether the scheduler reached FINAL.
    """
    order = []
    for _ in range(limit):
        state = scheduler.load_bundle()
        if state is SchedulerState.FINAL:
            return order, True
        if state is SchedulerState.PACK:
            bundle = [(task.group, task.id) for task in scheduler.tasks]
            for group, task_id in bundle:
                order.append(group)
                succeed(task_id)
        elif state is not SchedulerState.LOAD:
            pytest.fail(f'Unexpected scheduler state {state}')
    return order, False


def late_group_warnings(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Captured warnings that a group started after a higher one."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and LATE_GROUP_MESSAGE in record.getMessage()]


def scheduled_in_group(group: int) -> int:
    """Count of rows in `group` that have been scheduled."""
    return (Task.query(caching=False)
            .filter(Task.group == group)
            .filter(Task.schedule_time.isnot(None))
            .count())


# --- Rows committed while the scheduler chooses its next group ------------------------------------

@mark.unit
@mark.xfail(strict=True, raises=AssertionError,
            reason='The next group is chosen past rows committed while the current group is checked')
def test_next_group_sees_rows_committed_during_check(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    """Rows committed in the current group while the next group is chosen are run before moving on."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_tasks(0, 2)
    scheduler = make_scheduler()
    bundle = first_bundle(scheduler)
    if [group for group, _ in bundle] != [0]:
        pytest.fail(f'Expected the group-0 task first, found {bundle}')
    succeed(bundle[0][1])

    # The injection is armed only while the next group is chosen, so the stop branch never fires it.
    real_increment_group = Task.increment_group
    real_count_retryable = Task.count_retryable
    choosing = [False]
    injected: List[str] = []

    def increment_group(*args, **kwargs):
        choosing[0] = True
        try:
            return real_increment_group(*args, **kwargs)
        finally:
            choosing[0] = False

    def count_retryable(*args, **kwargs):
        if choosing[0] and not injected:
            injected.extend(commit_tasks(0, 1))
        return real_count_retryable(*args, **kwargs)

    monkeypatch.setattr(Task, 'increment_group', increment_group)
    monkeypatch.setattr(Task, 'count_retryable', count_retryable)
    scheduler.load_bundle()
    if not injected:
        pytest.fail('Injected rows were not committed')
    monkeypatch.setattr(Task, 'increment_group', real_increment_group)
    monkeypatch.setattr(Task, 'count_retryable', real_count_retryable)

    assert scheduler.current_group == 0
    late_row = first_bundle(scheduler)
    assert late_row == [(0, injected[0])]
    succeed(late_row[0][1])

    order, reached_final = run_until_final(scheduler)
    assert [0] + order == [0, 1, 2]
    assert reached_final
    assert late_group_warnings(caplog) == []


# --- A lower group arriving after a higher group has started ---------------------------------------

@mark.unit
@mark.xfail(strict=True, raises=AssertionError,
            reason='The scheduler only moves to higher groups, so a late lower group is never run')
@mark.parametrize('forever_mode', [False, True])
def test_late_lower_group_runs_after_current(task_db: Engine, caplog: pytest.LogCaptureFixture,
                                             forever_mode: bool) -> None:
    """A lower group committed after a higher group started runs once that group completes."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_tasks(1)
    scheduler = make_scheduler(forever_mode=forever_mode)
    bundle = first_bundle(scheduler)
    if [group for group, _ in bundle] != [1]:
        pytest.fail(f'Expected the group-1 task first, found {bundle}')
    late_id, = commit_tasks(0)
    succeed(bundle[0][1])

    scheduled = []
    for _ in range(10):
        state = scheduler.load_bundle()
        if state is SchedulerState.PACK:
            scheduled = [(task.group, task.id) for task in scheduler.tasks]
            break
        if state is not SchedulerState.LOAD:
            break
    assert scheduled == [(0, late_id)]

    warnings = late_group_warnings(caplog)
    assert len(warnings) == 1
    assert 'task group 0' in warnings[0] and 'task group 1' in warnings[0]

    succeed(late_id)
    order, reached_final = run_until_final(scheduler, limit=10)
    assert order == []
    assert reached_final is not forever_mode


# --- Input in ascending order that begins below the default group ----------------------------------

@mark.unit
@mark.xfail(strict=True, raises=AssertionError,
            reason='An empty-database start schedules into group 0 before lower groups arrive')
def test_ascending_input_below_zero_runs_in_order(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """Groups committed after an empty-database start run in ascending order, with no warning."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    scheduler = make_scheduler()
    commit_tasks(-1, -1, 0)

    order, reached_final = run_until_final(scheduler)
    assert order == [-1, -1, 0]
    assert reached_final
    assert late_group_warnings(caplog) == []


# --- Restarting a database left with a lower group unscheduled -------------------------------------

@mark.unit
@mark.xfail(strict=True, raises=AssertionError,
            reason='A restart starts in the higher group and never moves down to the lower one')
def test_restart_runs_stranded_lower_group(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A restart runs a lower group left unscheduled behind a completed higher group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    now = datetime.now().astimezone()
    commit_rows([
        Task.new(args='echo 1', group=1, attempt=1, exit_status=0, schedule_time=now, completion_time=now),
        Task.new(args='echo 0', group=0),
    ])
    scheduler = make_scheduler(restart_mode=True)

    order, reached_final = run_until_final(scheduler)
    assert order == [0]
    assert reached_final
    assert late_group_warnings(caplog) == []


@mark.unit
def test_restart_on_halted_group_still_halts(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A restart on a group whose failure has used its retries stops without running the next group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    now = datetime.now().astimezone()
    commit_rows([
        Task.new(args='false', group=0, attempt=1, exit_status=1, schedule_time=now, completion_time=now),
        Task.new(args='echo 1', group=1),
    ])
    scheduler = make_scheduler(restart_mode=True, attempts=1)

    caplog.clear()
    assert scheduler.load_bundle() is SchedulerState.FINAL
    assert any(record.getMessage().startswith('Failed task group 0') for record in caplog.records)
    assert scheduled_in_group(1) == 0
