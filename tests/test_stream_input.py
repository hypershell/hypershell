# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replays of a run fed by a stream whose input pauses.

A scheduler that shares its process with a submitter stops only once that submitter has ended and
every task is terminal. The unit tests drive ``Scheduler.load_bundle`` in-process against a throwaway
SQLite file, with a stand-in submitter whose end the test chooses, so a pause in the input is replayed
without sleeps. The integration test runs a whole local cluster on a source that holds its second
line until the scheduler has seen the first one finish.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List
from pathlib import Path

# Standard libs
import time
import logging
import threading
from types import SimpleNamespace

# External libs
import pytest
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
import hypershell.server
from hypershell.data.model import Task
from hypershell.server import Scheduler, SchedulerState
from hypershell.cluster.local import LocalCluster
from hypershell.cluster.watch import scheduled_work
from tests.conftest import free_port
from tests.test_group_order import (task_db, commit_tasks, commit_finished, succeed, fail,  # noqa: F401 (fixture)
                                    make_scheduler, first_bundle)


FAILED_GROUP_MESSAGE = 'Failed task group'
"""Substring of the critical message logged when a failed group halts the run."""


class OpenInput:
    """Stands in for a submitter still reading its input, until the test ends it."""

    def __init__(self: OpenInput) -> None:
        self.alive = True

    def is_alive(self: OpenInput) -> bool:
        return self.alive


def critical(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Captured critical messages."""
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.CRITICAL]


def run_bundle(scheduler: Scheduler, outcome=succeed) -> List[str]:
    """Load the next bundle and record `outcome` for each of its tasks; return their ids."""
    ids = [task_id for _, task_id in first_bundle(scheduler)]
    for task_id in ids:
        outcome(task_id)
    return ids


@mark.unit
@mark.parametrize('restart_mode', [False, True])
def test_open_input_holds_the_stop(task_db: Engine, restart_mode: bool) -> None:
    """With every task done and the input still open, the scheduler waits; a late line is then run."""
    submitter = OpenInput()
    scheduler = make_scheduler(submitter=submitter, restart_mode=restart_mode)
    first = commit_tasks(0)
    assert run_bundle(scheduler) == first
    for _ in range(5):
        assert scheduler.load_bundle() is SchedulerState.LOAD
    late = commit_tasks(0)
    assert run_bundle(scheduler) == late
    assert scheduler.load_bundle() is SchedulerState.LOAD
    submitter.alive = False
    assert scheduler.load_bundle() is SchedulerState.FINAL
    assert Task.count_remaining() == 0


@mark.unit
def test_input_ending_with_nothing_left_stops(task_db: Engine) -> None:
    """Earlier work all done and nothing new read: the scheduler waits on the input, then stops at its end."""
    commit_finished(0)
    submitter = OpenInput()
    scheduler = make_scheduler(submitter=submitter)
    assert scheduler.load_bundle() is SchedulerState.LOAD
    submitter.alive = False
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_submitter_ending_between_the_counts_is_seen(task_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A submitter that commits its last row and ends while the scheduler counts does not lose the row."""
    commit_finished(0)
    submitter = OpenInput()
    scheduler = make_scheduler(submitter=submitter)
    count_retryable = Task.count_retryable
    late = []

    def commit_then_count(*args: Any, **kwargs: Any) -> int:
        if not late:
            late.extend(commit_tasks(0))
            submitter.alive = False
        return count_retryable(*args, **kwargs)

    monkeypatch.setattr(Task, 'count_retryable', commit_then_count)
    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert late and run_bundle(scheduler) == late
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_failure_with_open_input_waits_for_late_line(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A failed task with no retry left does not halt the run while the input is open."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    submitter = OpenInput()
    scheduler = make_scheduler(submitter=submitter, attempts=1)
    commit_tasks(0)
    run_bundle(scheduler, outcome=fail)
    for _ in range(3):
        assert scheduler.load_bundle() is SchedulerState.LOAD
    assert not [message for message in critical(caplog) if FAILED_GROUP_MESSAGE in message]
    late = commit_tasks(0)
    assert run_bundle(scheduler) == late
    assert scheduler.load_bundle() is SchedulerState.LOAD
    submitter.alive = False
    assert scheduler.load_bundle() is SchedulerState.FINAL
    assert critical(caplog) == []


@mark.unit
def test_failed_group_still_halts_late_higher_group(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A late line in a higher group still meets the failed-group halt, input open or not."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    scheduler = make_scheduler(submitter=OpenInput(), attempts=1)
    commit_tasks(0)
    run_bundle(scheduler, outcome=fail)
    commit_tasks(1)
    assert scheduler.load_bundle() is SchedulerState.FINAL
    assert [message for message in critical(caplog) if f'{FAILED_GROUP_MESSAGE} 0' in message]


@mark.unit
def test_scheduled_work_ignores_open_input(task_db: Engine) -> None:
    """Input still being read is not work a client could run; a committed line is."""
    scheduler = make_scheduler(submitter=OpenInput())
    commit_tasks(0)
    run_bundle(scheduler)
    server = SimpleNamespace(scheduler=SimpleNamespace(is_alive=lambda: True, machine=scheduler))
    assert scheduled_work(server) is False
    commit_tasks(0)
    assert scheduled_work(server) is True


@mark.integration
def test_local_cluster_runs_input_after_a_pause(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    """A line that arrives after every earlier task finished is still run, and the run then ends."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    released = threading.Event()
    after_done = []
    final_input_ended = []
    load_bundle = Scheduler.load_bundle

    def observed_load_bundle(self: Scheduler) -> SchedulerState:
        # Only a pass that starts with the first task already finished is the one the pause is about;
        # nothing new can land before the source is released, so that pass sees the same counts.
        first_done = not released.is_set() and Task.count() > 0 and Task.count_remaining() == 0
        state = load_bundle(self)
        if first_done:
            after_done.append(state)
            released.set()
        if state is SchedulerState.FINAL:
            final_input_ended.append(self.submission_complete())
        return state

    monkeypatch.setattr(Scheduler, 'load_bundle', observed_load_bundle)

    def source():
        yield 'echo a'
        if not released.wait(60):
            raise RuntimeError('The scheduler never saw the first task finish')
        yield 'echo b'

    with open(tmp_path / 'out.txt', mode='w') as output:
        cluster = LocalCluster(source=source(), num_threads=1, bundlesize=1, bundlewait=1, poll=1,
                               max_retries=0, port=free_port(), redirect_output=output)
        cluster.start()
        cluster.join(timeout=60)
        assert not cluster.is_alive()

    assert after_done == [SchedulerState.LOAD]
    rows = {task.args: task.exit_status for task in Task.query(caching=False).all()}
    assert rows == {'echo a': 0, 'echo b': 0}
    assert final_input_ended == [True]
    assert sorted((tmp_path / 'out.txt').read_text().split()) == ['a', 'b']


WAKE_BOUND = 10
"""Seconds a woken back-off may take to end; the back-off itself is a long poll, far beyond this."""


def open_input_scheduler(submitter: OpenInput, poll: int = 600) -> Scheduler:
    """Started scheduler on a database whose one task is done, its input open, backing off for `poll` seconds."""
    commit_finished(0)
    scheduler = Scheduler(queue=None, poll=poll, attempts=1, submitter=submitter)
    assert scheduler.start() is SchedulerState.LOAD
    scheduler.poll = poll  # Already backed off to its full interval, as after a long pause
    scheduler.woken = True
    return scheduler


@mark.unit
def test_wake_ends_a_back_off(task_db: Engine) -> None:
    """A wake ends the scheduler's wait for demand at once, and the next pass sees the input ended."""
    submitter = OpenInput()
    scheduler = open_input_scheduler(submitter)
    waiting = threading.Event()
    wait_need = scheduler.wait_need

    def entered_wait(generation: int, timeout: float) -> bool:
        waiting.set()
        return wait_need(generation, timeout)

    scheduler.wait_need = entered_wait
    states = []
    thread = threading.Thread(target=lambda: states.append(scheduler.load_bundle()), daemon=True)
    start = time.monotonic()
    thread.start()
    assert waiting.wait(WAKE_BOUND)
    submitter.alive = False
    scheduler.wake()
    thread.join(WAKE_BOUND)
    assert not thread.is_alive()
    assert time.monotonic() - start < WAKE_BOUND
    assert states == [SchedulerState.LOAD]
    assert scheduler.load_bundle() is SchedulerState.FINAL


@mark.unit
def test_wake_before_the_wait_is_not_lost(task_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wake that lands during a pass, before its back-off starts, still ends that back-off."""
    submitter = OpenInput()
    scheduler = open_input_scheduler(submitter)
    count_retryable = Task.count_retryable

    def wake_then_count(*args: Any, **kwargs: Any) -> int:
        scheduler.wake()
        return count_retryable(*args, **kwargs)

    monkeypatch.setattr(Task, 'count_retryable', wake_then_count)
    start = time.monotonic()
    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert time.monotonic() - start < WAKE_BOUND


@mark.integration
def test_local_cluster_stops_promptly_at_end_of_input(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    """Input that ends during a long back-off ends the run at once, not after the poll interval."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    released = threading.Event()
    back_off = Scheduler.back_off

    def long_back_off(self: Scheduler, generation: int, reason: str) -> SchedulerState:
        if reason.startswith('All tasks done'):
            self.poll, self.woken = self.poll_max, True  # A long pause: the full interval, every time
            released.set()
        return back_off(self, generation, reason)

    monkeypatch.setattr(Scheduler, 'back_off', long_back_off)

    def source():
        yield 'echo a'
        if not released.wait(60):
            raise RuntimeError('The scheduler never waited on the open input')

    with open(tmp_path / 'out.txt', mode='w') as output:
        cluster = LocalCluster(source=source(), num_threads=1, bundlesize=1, bundlewait=1, poll=600,
                               max_retries=0, port=free_port(), redirect_output=output)
        cluster.start()
        try:
            cluster.join(timeout=60)
            assert not cluster.is_alive()
        finally:
            if cluster.is_alive():
                cluster.server.scheduler.stop()  # Ends the back-off; the run then shuts down in order
                cluster.join(timeout=30)

    assert released.is_set()
    rows = {task.args: task.exit_status for task in Task.query(caching=False).all()}
    assert rows == {'echo a': 0}
