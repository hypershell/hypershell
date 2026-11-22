# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The result that finishes a run ends the scheduler's back-off.

A scheduler with nothing left to load backs off for a wait that grows toward the server's poll
interval. When the result that finishes the run (input ended, no task left incomplete or retryable,
not ``--forever``) lands during that wait, the server stops waiting and begins finishing at once.
While work remains, a landed result changes nothing about the back-off.

The unit tests drive ``Scheduler.load_bundle`` in a thread against a throwaway SQLite file, with the
back-off forced to a long poll, and report results through a receiver driven in the test's own
thread. The integration test runs a whole local cluster whose last result lands in a 600 s back-off.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Standard libs
import time
import threading
from queue import Queue
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
import hypershell.client
import hypershell.server
from hypershell.data.core import Session
from hypershell.data.model import Task
from hypershell.server import Scheduler, SchedulerState, Receiver, ServerThread
from hypershell.cluster.local import LocalCluster
from tests.conftest import free_port
from tests.test_group_order import task_db, commit_tasks  # noqa: F401 (fixture)
from tests.test_stream_input import OpenInput


BACK_OFF = 600
"""Seconds of the forced back-off: the full poll interval, far beyond any bound below."""

WAKE_BOUND = 1.0
"""Seconds the finishing result may take to end the back-off."""

SHORT_POLL = 1.0
"""Seconds of a back-off that must run out (no wake) in the tests where work remains."""

JOIN_BOUND = 15.0
"""Seconds any pass is waited on before the test gives up and halts it."""


def mark_scheduled(*task_ids: str) -> None:
    """Stamp each task as dispatched to a client, through a separate session, as the dispatcher's write does."""
    session = Session.session_factory()
    try:
        now = datetime.now().astimezone()
        session.bulk_update_mappings(Task, [{'id': task_id, 'schedule_time': now, 'client_id': 'test-client',
                                             'client_host': 'localhost'} for task_id in task_ids])
        session.commit()
    finally:
        session.close()


def started_scheduler(*task_ids: str, poll: float = BACK_OFF, **options: Any) -> Scheduler:
    """Started scheduler whose tasks are all dispatched and running, so a pass finds nothing to load.

    Its back-off is already grown to `poll` and stays there.
    """
    options.setdefault('attempts', 1)
    scheduler = Scheduler(queue=None, poll=BACK_OFF, **options)
    assert scheduler.start() is SchedulerState.LOAD
    mark_scheduled(*task_ids)
    scheduler.poll = poll
    scheduler.woken = True
    return scheduler


def result(task_id: str, exit_status: int = 0) -> Task:
    """A client's result for the task's current dispatch."""
    row = Task.query(caching=False).filter(Task.id == task_id).one()
    task = Task.from_json(row.to_json())
    Session.expunge_all()
    task.exit_status = exit_status
    task.start_time = task.completion_time = datetime.now().astimezone()
    return task


def receiver(scheduler: Optional[Scheduler]) -> Receiver:
    """A receiver that reports what it writes back to `scheduler`."""
    return Receiver(queue=SimpleNamespace(completed=Queue()), scheduler=scheduler)


def land(machine: Receiver, *results: Task) -> None:
    """Write back a bundle of results through the receiver."""
    machine.tasks = list(results)
    assert machine.update_tasks() is hypershell.server.ReceiverState.UNLOAD


class Pass:
    """One scheduler pass run in its own thread, with a mark when it starts to wait."""

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler
        self.waiting = threading.Event()
        self.states: List[SchedulerState] = []
        wait_need = scheduler.wait_need

        def entered_wait(generation: int, timeout: float) -> bool:
            if timeout == scheduler.poll:
                self.waiting.set()
            return wait_need(generation, timeout)

        scheduler.wait_need = entered_wait
        self.thread = threading.Thread(target=lambda: self.states.append(scheduler.load_bundle()), daemon=True)

    def start(self) -> Pass:
        self.thread.start()
        return self

    def join(self, timeout: float) -> float:
        """Wait at most `timeout` for the pass to end; the seconds it took. A pass still waiting is halted."""
        start = time.monotonic()
        self.thread.join(timeout)
        took = time.monotonic() - start
        if self.thread.is_alive():
            self.scheduler.halt()
            self.thread.join(JOIN_BOUND)
            pytest.fail(f'Pass still waiting after {timeout} s')
        return took


@mark.unit
def test_finishing_result_ends_the_back_off(task_db: Engine) -> None:
    """The result that finishes the run, written back through the receiver, ends a 600 s back-off at once."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id)
    run = Pass(scheduler).start()
    assert run.waiting.wait(JOIN_BOUND)
    land(receiver(scheduler), result(task_id))
    assert run.join(JOIN_BOUND) < WAKE_BOUND
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_result_with_work_left_does_not_end_the_back_off(task_db: Engine) -> None:
    """A result that leaves a task still running does not cut the back-off."""
    first, second = commit_tasks(0, 0)
    scheduler = started_scheduler(first, second, poll=SHORT_POLL)
    run = Pass(scheduler).start()
    assert run.waiting.wait(JOIN_BOUND)
    land(receiver(scheduler), result(first))
    run.join(JOIN_BOUND)
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.woken is False  # The wait ran out


@mark.unit
def test_failure_with_a_retry_left_does_not_end_the_back_off(task_db: Engine) -> None:
    """A failed last result with a retry left does not cut the back-off: the next pass loads the retry."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id, poll=SHORT_POLL, attempts=2)
    run = Pass(scheduler).start()
    assert run.waiting.wait(JOIN_BOUND)
    land(receiver(scheduler), result(task_id, exit_status=1))
    run.join(JOIN_BOUND)
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.woken is False


@mark.unit
def test_forever_scheduler_is_not_woken_by_a_result(task_db: Engine) -> None:
    """A scheduler in forever mode keeps waiting after the last result lands."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id, poll=SHORT_POLL, forever_mode=True)
    run = Pass(scheduler).start()
    assert run.waiting.wait(JOIN_BOUND)
    land(receiver(scheduler), result(task_id))
    run.join(JOIN_BOUND)
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.woken is False


@mark.unit
def test_input_still_open_is_not_woken_by_a_result(task_db: Engine) -> None:
    """With its input still open, a scheduler keeps waiting after the last result so far lands."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id, poll=SHORT_POLL, submitter=OpenInput())
    run = Pass(scheduler).start()
    assert run.waiting.wait(JOIN_BOUND)
    land(receiver(scheduler), result(task_id))
    run.join(JOIN_BOUND)
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.woken is False


@mark.unit
def test_result_landing_before_the_wait_is_not_lost(task_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A finishing result that lands after the pass's counts and before its wait still ends that wait."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id)
    machine = receiver(scheduler)
    finishing = result(task_id)
    report_held = scheduler.report_held

    def land_then_report(*args: Any, **kwargs: Any) -> Any:
        land(machine, finishing)
        return report_held(*args, **kwargs)

    monkeypatch.setattr(scheduler, 'report_held', land_then_report)
    run = Pass(scheduler).start()
    assert run.join(JOIN_BOUND) < WAKE_BOUND
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_result_landing_during_the_count_is_not_lost(task_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A finishing result that lands after the pass reads its remaining count, before that count arms the
    pass, still ends the pass's wait: the pass is armed for any landing before it counts."""
    task_id, = commit_tasks(0)
    scheduler = started_scheduler(task_id)
    machine = receiver(scheduler)
    finishing = result(task_id)
    count_remaining = Task.count_remaining
    seen: Dict[str, Any] = {}

    def count_then_land(*args: Any, **kwargs: Any) -> int:
        remaining = count_remaining(*args, **kwargs)
        if not seen:
            # The pass's own count: the result lands once it is read and before the pass arms with it.
            seen.update(remaining=remaining, armed=scheduler.finishing)
            land(machine, finishing)
        return remaining

    monkeypatch.setattr(Task, 'count_remaining', staticmethod(count_then_land))
    run = Pass(scheduler).start()
    assert run.join(JOIN_BOUND) < WAKE_BOUND
    assert seen == {'remaining': 1, 'armed': (0, 0)}
    assert scheduler.landed == 1 and scheduler.finishing == (0, 1)
    assert run.states == [SchedulerState.LOAD]
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_receiver_reports_to_the_server_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server with a database hands its scheduler to its receiver; an in-memory one has none to hand."""
    in_memory = ServerThread(in_memory=True, address=('127.0.0.1', free_port()), auth='test')
    assert in_memory.scheduler is None
    assert in_memory.receiver.machine.scheduler is None
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    with_db = ServerThread(address=('127.0.0.1', free_port()), auth='test')
    assert with_db.receiver.machine.scheduler is with_db.scheduler.machine


@mark.integration
def test_finishing_result_ends_a_long_back_off(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    """A run whose last result lands in a 600 s back-off finishes within a second of that result."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    # A local cluster sets the client's module-wide mode; it is restored for the tests that follow.
    monkeypatch.setattr(hypershell.client, 'CLIENT_STANDALONE_MODE', hypershell.client.CLIENT_STANDALONE_MODE)
    backing_off = threading.Event()
    marks: Dict[str, float] = {}
    back_off = Scheduler.back_off

    def long_back_off(self: Scheduler, generation: int, reason: str) -> SchedulerState:
        if reason.startswith('No tasks available'):
            self.poll, self.woken = self.poll_max, True  # Grown to the full interval
            backing_off.set()
        return back_off(self, generation, reason)

    apply_results = Task.apply_results

    def stamp_landing(results: List[Task]) -> Any:
        outcomes = apply_results(results)
        marks['landed'] = time.monotonic()
        return outcomes

    finalize = Scheduler.finalize

    def stamp_final() -> SchedulerState:
        marks['final'] = time.monotonic()
        return finalize()

    monkeypatch.setattr(Scheduler, 'back_off', long_back_off)
    monkeypatch.setattr(Task, 'apply_results', staticmethod(stamp_landing))
    monkeypatch.setattr(Scheduler, 'finalize', staticmethod(stamp_final))
    with open(tmp_path / 'out.txt', mode='w') as output:
        cluster = LocalCluster(source=['sleep 1'], num_threads=1, bundlesize=1, bundlewait=1, poll=BACK_OFF,
                               max_retries=0, port=free_port(), redirect_output=output)
        cluster.start()
        try:
            cluster.join(timeout=JOIN_BOUND)
            assert not cluster.is_alive(), f'run still waiting out its back-off after {JOIN_BOUND} s'
        finally:
            if cluster.is_alive():
                cluster.server.scheduler.stop()
                cluster.join(timeout=30)
    assert backing_off.is_set(), 'the run never backed off'
    assert marks['final'] - marks['landed'] < WAKE_BOUND, marks
    assert {task.args: task.exit_status for task in Task.query(caching=False).all()} == {'sleep 1': 0}
