# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replays of the scheduler's group-advance and stop decisions around retries.

These tests drive ``Scheduler.load_bundle`` in-process against a throwaway SQLite database.
A result written back by the Receiver thread is simulated by committing it through a separate
session, injected at an exact point (right after ``Task.next`` finds nothing to schedule),
so the interleaving is replayed without sleeps, threads or timing.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Iterator, List
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
from hypershell.data.model import Entity, Task, CANCEL_STATUS
from hypershell.server import Scheduler, SchedulerState


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


def inject_after_next(monkeypatch: pytest.MonkeyPatch, action: Callable[[], None]) -> List[bool]:
    """Run `action` once, right after the next call to `Task.next` that finds nothing to schedule.

    Returns a one-element flag list that becomes true once the action has run.
    """
    real_next = Task.next
    fired = [False]

    def one_shot(**options) -> List[Task]:
        tasks = real_next(**options)
        if not tasks:
            action()
            fired[0] = True
        monkeypatch.setattr(Task, 'next', real_next)
        return tasks

    monkeypatch.setattr(Task, 'next', one_shot)
    return fired


def submit(*groups: int) -> List[Task]:
    """Add one task per entry in `groups`, each in that group."""
    return Task.add_all([Task.new(args=f'echo {i}', group=group) for i, group in enumerate(groups)])


def make_scheduler(attempts: int = 3, eager: bool = False, forever_mode: bool = False) -> Scheduler:
    """Started scheduler with no queue and zero back-off."""
    scheduler = Scheduler(queue=None, attempts=attempts, eager=eager, forever_mode=forever_mode, poll=0)
    if scheduler.start() is not SchedulerState.LOAD:
        pytest.fail('Scheduler did not start into LOAD')
    return scheduler


def next_bundle(scheduler: Scheduler, limit: int = 4) -> List[Task]:
    """Drive LOAD until a bundle is packed; fail the test if it stops or never packs."""
    for _ in range(limit):
        state = scheduler.load_bundle()
        if state is SchedulerState.PACK:
            return scheduler.tasks
        if state is not SchedulerState.LOAD:
            pytest.fail(f'Expected a bundle, scheduler moved to {state}')
    pytest.fail(f'No bundle after {limit} loads')


def expect_one(tasks: List[Task], group: int, attempt: int) -> Task:
    """The single task of a bundle, with its group and attempt checked as a precondition."""
    if len(tasks) != 1 or tasks[0].group != group or tasks[0].attempt != attempt:
        found = [(task.group, task.attempt) for task in tasks]
        pytest.fail(f'Expected one task (group={group}, attempt={attempt}), found {found}')
    return tasks[0]


def group_messages(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Captured records that announce a group completing or failing."""
    return [record.getMessage() for record in caplog.records
            if record.getMessage().startswith(('Completed task group', 'Failed task group'))]


def scheduled_in_group(group: int) -> int:
    """Count of rows in `group` that have been scheduled."""
    return (Task.query(caching=False)
            .filter(Task.group == group)
            .filter(Task.schedule_time.isnot(None))
            .count())


# --- Replays of a failure written back after Task.next finds nothing ------------------------------

@mark.unit
@mark.parametrize('eager', [False, True])
def test_group_advance_waits_for_pending_retry(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                               caplog: pytest.LogCaptureFixture, eager: bool) -> None:
    """A failure committed between Task.next and the group decision is retried before the next group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    submit(0, 1)
    scheduler = make_scheduler(attempts=3, eager=eager)
    first = expect_one(next_bundle(scheduler), group=0, attempt=1)
    fail(first.id)
    second = expect_one(next_bundle(scheduler), group=0, attempt=2)
    second_id = second.id

    fired = inject_after_next(monkeypatch, lambda: fail(second_id))
    caplog.clear()
    state = scheduler.load_bundle()
    if not fired[0]:
        pytest.fail('Injected failure did not run')

    assert state is SchedulerState.LOAD
    assert scheduler.current_group == 0
    assert group_messages(caplog) == []

    assert scheduler.load_bundle() is SchedulerState.PACK
    assert len(scheduler.tasks) == 1
    retry = scheduler.tasks[0]
    assert retry.group == 0
    assert retry.attempt == 3
    assert retry.previous_id == second_id
    assert scheduled_in_group(1) == 0


@mark.unit
@mark.parametrize('eager', [False, True])
@mark.parametrize('layout', ['no-groups', 'last-group'])
def test_stop_waits_for_pending_retry(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                      layout: str, eager: bool) -> None:
    """A failure committed between Task.next and the stop decision is retried before FINAL."""
    if layout == 'no-groups':
        submit(0)
        group = 0
        scheduler = make_scheduler(attempts=3, eager=eager)
    else:
        submit(0, 1)
        group = 1
        scheduler = make_scheduler(attempts=3, eager=eager)
        succeed(expect_one(next_bundle(scheduler), group=0, attempt=1).id)

    first = expect_one(next_bundle(scheduler), group=group, attempt=1)
    fail(first.id)
    second = expect_one(next_bundle(scheduler), group=group, attempt=2)
    second_id = second.id

    fired = inject_after_next(monkeypatch, lambda: fail(second_id))
    state = scheduler.load_bundle()
    if not fired[0]:
        pytest.fail('Injected failure did not run')

    assert state is not SchedulerState.FINAL
    assert scheduler.load_bundle() is SchedulerState.PACK
    assert len(scheduler.tasks) == 1
    retry = scheduler.tasks[0]
    assert retry.group == group
    assert retry.attempt == 3
    assert retry.previous_id == second_id

    fail(retry.id)
    assert scheduler.load_bundle() is SchedulerState.FINAL


# --- Gating behaviour that must stay as it is -------------------------------------------------------

@mark.unit
@mark.parametrize('attempts', [1, 3])
@mark.parametrize('forever_mode', [False, True])
def test_exhausted_failure_halts_group(task_db: Engine, caplog: pytest.LogCaptureFixture,
                                       attempts: int, forever_mode: bool) -> None:
    """A failure with no retries left stops the run, or waits in forever mode, without advancing."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    submit(0, 1)
    scheduler = make_scheduler(attempts=attempts, forever_mode=forever_mode)
    for attempt in range(1, attempts + 1):
        fail(expect_one(next_bundle(scheduler), group=0, attempt=attempt).id)

    caplog.clear()
    state = scheduler.load_bundle()
    if forever_mode:
        assert state is SchedulerState.LOAD
        assert scheduler.current_group == 0
    else:
        assert state is SchedulerState.FINAL
        assert any(message.startswith('Failed task group 0') for message in group_messages(caplog))
    assert scheduled_in_group(1) == 0


@mark.unit
def test_cancelled_task_does_not_hold_group(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A cancelled task neither holds nor halts its group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    submit(0, 1)
    scheduler = make_scheduler(attempts=3)
    fail(expect_one(next_bundle(scheduler), group=0, attempt=1).id, exit_status=CANCEL_STATUS)

    caplog.clear()
    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert scheduler.current_group == 1
    assert any(message.startswith('Completed task group 0') for message in group_messages(caplog))


@mark.unit
@mark.parametrize('eager', [False, True])
def test_retried_success_advances_group(task_db: Engine, eager: bool) -> None:
    """A group whose failed task succeeds on retry advances to the next group."""
    submit(0, 1)
    scheduler = make_scheduler(attempts=3, eager=eager)
    fail(expect_one(next_bundle(scheduler), group=0, attempt=1).id)
    succeed(expect_one(next_bundle(scheduler), group=0, attempt=2).id)

    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert scheduler.current_group == 1
    expect_one(next_bundle(scheduler), group=1, attempt=1)


@mark.unit
def test_successful_run_reaches_final(task_db: Engine) -> None:
    """A single group that succeeds reaches FINAL."""
    submit(0)
    scheduler = make_scheduler(attempts=3)
    succeed(expect_one(next_bundle(scheduler), group=0, attempt=1).id)
    assert scheduler.load_bundle() is SchedulerState.FINAL


# --- One predicate for retry selection, group hold and stop ----------------------------------------

@mark.unit
@mark.parametrize('attempts', [1, 2, 3, 4])
@mark.parametrize('eager', [False, True])
def test_hold_matches_retry_selection(task_db: Engine, attempts: int, eager: bool) -> None:
    """The failures that hold a group are exactly the failures Task.next retries."""
    now = datetime.now().astimezone()
    done = {'schedule_time': now, 'completion_time': now}
    retried = Task.new(args='echo retried', group=0, attempt=1, retried=True, exit_status=1, **done)
    successor = Task.new(args='echo retried', group=0, attempt=2, exit_status=0,
                         previous_id=retried.id, **done)
    retried.next_id = successor.id
    Task.add_all([
        Task.new(args='echo success', group=0, attempt=1, exit_status=0, **done),
        Task.new(args='echo cancelled', group=0, attempt=1, exit_status=CANCEL_STATUS, **done),
        Task.new(args='echo first', group=0, attempt=1, exit_status=1, **done),
        Task.new(args='echo third', group=0, attempt=3, exit_status=1, **done),
        retried, successor,
        Task.new(args='echo signal', group=0, attempt=1, exit_status=-9, **done),
        Task.new(args='echo never-ran', group=0, attempt=1, exit_status=-1001, **done),
        Task.new(args='echo user-set', group=0, attempt=0, exit_status=1, **done),
        Task.new(args='echo other-group', group=1, attempt=1, exit_status=1, **done),
    ])

    held = Task.count_retryable(attempts, 0, eager=eager)
    # Eager selection can list a new retry row twice, so retries are compared by identity.
    retries = {task.id: task for task in Task.next(limit=100, group=0, attempts=attempts, eager=eager)}
    assert held == len(retries)
    assert all(task.previous_id is not None for task in retries.values())
