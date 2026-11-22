# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""When a client returns its finished tasks.

A client returns a bundle of results when it is full, or when the bundle wait has passed since its
previous return, and at shutdown everything it holds. A client with nothing left to run (no task
running and none waiting to start) returns what it holds at once, whatever its bundle size and wait.

The unit tests drive the collector one action at a time on plain queues, with a count of the tasks
in flight that the test sets. The integration test runs a local cluster whose one client holds a
bundle larger than its work and waits a minute between returns.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List

# Standard libs
import time
from queue import Queue
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy.engine import Engine

# Internal libs
import hypershell.client
import hypershell.server
from hypershell.client import ClientCollector, CollectorState
from hypershell.data.model import Task, deserialize_tasks
from hypershell.cluster.local import LocalCluster
from tests.conftest import free_port
from tests.test_group_order import task_db  # noqa: F401 (fixture)


RETURN_BOUND = 1.0
"""Seconds from a task's completion to its result landing, for a client with nothing left to run."""


@fixture
def short_local_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the collector's wait on its local queue so an empty poll returns at once."""
    monkeypatch.setattr(hypershell.client, 'DEFAULT_LOCAL_TIMEOUT', 0.01)


def task_count(value: int) -> Any:
    """A count of tasks in flight, at `value`."""
    from hypershell.client import TaskCount  # Looked up here: the name is new with the idle return
    count = TaskCount()
    count.add(value)
    return count


def collector(bundlesize: int, bundlewait: int, **options: Any) -> ClientCollector:
    """A started collector on plain queues."""
    machine = ClientCollector(queue=SimpleNamespace(completed=Queue()), local=Queue(),
                              bundlesize=bundlesize, bundlewait=bundlewait, **options)
    assert machine.start() is CollectorState.GET_LOCAL
    return machine


def take(machine: ClientCollector, *args: str) -> List[CollectorState]:
    """Hand the collector a finished task for each of `args`; the state after checking each one."""
    states = []
    for arg in args:
        machine.local.put(Task.new(args=arg))
        assert machine.get_local() is CollectorState.CHECK_BUNDLE
        states.append(machine.check_bundle())
    return states


def returned(machine: ClientCollector) -> List[List[str]]:
    """The bundles the collector has put out, as lists of task arguments."""
    bundles = []
    while not machine.queue.completed.empty():
        bundles.append([task.args for task in deserialize_tasks(machine.queue.completed.get_nowait())])
    return bundles


def send(machine: ClientCollector) -> None:
    """Pack and put out the held bundle."""
    assert machine.pack_bundle() is CollectorState.PUT_REMOTE
    assert machine.put_remote() is CollectorState.GET_LOCAL


@mark.unit
def test_held_while_tasks_are_in_flight(short_local_timeout: None) -> None:
    """With a task still in flight, a partial bundle is held until the wait passes."""
    count = task_count(2)
    machine = collector(bundlesize=4, bundlewait=3600, inflight=count)
    assert take(machine, 'echo a') == [CollectorState.GET_LOCAL]
    assert count.value == 1
    assert machine.get_local() is CollectorState.CHECK_BUNDLE  # Nothing came: an empty poll
    assert machine.check_bundle() is CollectorState.GET_LOCAL
    assert returned(machine) == []


@mark.unit
def test_returned_when_the_bundle_is_full(short_local_timeout: None) -> None:
    """A full bundle is returned while tasks are still in flight."""
    machine = collector(bundlesize=2, bundlewait=3600, inflight=task_count(5))
    assert take(machine, 'echo a', 'echo b') == [CollectorState.GET_LOCAL, CollectorState.PACK_BUNDLE]
    send(machine)
    assert returned(machine) == [['echo a', 'echo b']]


@mark.unit
def test_returned_when_the_wait_has_passed(short_local_timeout: None) -> None:
    """A partial bundle is returned once the bundle wait has passed, while tasks are still in flight."""
    machine = collector(bundlesize=8, bundlewait=0, inflight=task_count(3))
    assert take(machine, 'echo a') == [CollectorState.PACK_BUNDLE]
    send(machine)
    assert returned(machine) == [['echo a']]


@mark.unit
def test_returned_at_once_with_nothing_left_to_run(short_local_timeout: None) -> None:
    """Taking the last task in flight returns the partial bundle at once; an idle client sends no empty bundle."""
    count = task_count(2)
    machine = collector(bundlesize=8, bundlewait=3600, inflight=count)
    assert take(machine, 'echo a', 'echo b') == [CollectorState.GET_LOCAL, CollectorState.PACK_BUNDLE]
    assert count.value == 0
    send(machine)
    assert returned(machine) == [['echo a', 'echo b']]
    for _ in range(3):
        assert machine.get_local() is CollectorState.CHECK_BUNDLE
        assert machine.check_bundle() is CollectorState.GET_LOCAL
    assert returned(machine) == []


@mark.unit
def test_without_a_count_bundles_as_before(short_local_timeout: None) -> None:
    """A collector given no count holds a partial bundle until the wait passes."""
    for options in ({}, {'inflight': None}):
        machine = collector(bundlesize=8, bundlewait=3600, **options)
        assert take(machine, 'echo a') == [CollectorState.GET_LOCAL]
        assert machine.get_local() is CollectorState.CHECK_BUNDLE
        assert machine.check_bundle() is CollectorState.GET_LOCAL
        assert returned(machine) == []


@mark.unit
def test_finalize_returns_everything_held(short_local_timeout: None) -> None:
    """At shutdown the collector returns every task it holds, however few."""
    machine = collector(bundlesize=8, bundlewait=3600)
    assert take(machine, 'echo a', 'echo b') == [CollectorState.GET_LOCAL, CollectorState.GET_LOCAL]
    machine.local.put(None)
    assert machine.get_local() is CollectorState.FINAL
    assert machine.finalize() is CollectorState.HALT
    assert returned(machine) == [['echo a', 'echo b']]


@mark.integration
def test_idle_client_returns_what_it_holds(task_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A client with a bundle of 8 and a 60 s wait returns its 3 results within a second of each finishing."""
    monkeypatch.setattr(hypershell.server, 'DATABASE_ENABLED', True)
    # A local cluster sets the client's module-wide mode; it is restored for the tests that follow.
    monkeypatch.setattr(hypershell.client, 'CLIENT_STANDALONE_MODE', hypershell.client.CLIENT_STANDALONE_MODE)
    apply_results = Task.apply_results
    landed: Dict[str, datetime] = {}

    def stamp_landing(results: List[Task]) -> Any:
        outcomes = apply_results(results)
        for task in results:
            landed[task.id] = datetime.now().astimezone()
        return outcomes

    monkeypatch.setattr(Task, 'apply_results', staticmethod(stamp_landing))
    with open(tmp_path / 'out.txt', mode='w') as output:
        cluster = LocalCluster(source=['true'] * 3, num_threads=2, bundlesize=8, bundlewait=60, poll=1,
                               max_retries=0, port=free_port(), redirect_output=output)
        cluster.start()
        try:
            deadline = time.monotonic() + 10
            while len(landed) < 3 and time.monotonic() < deadline:
                time.sleep(0.1)
            assert len(landed) == 3, f'{len(landed)} of 3 results returned within 10 s (bundle wait 60 s)'
        finally:
            if cluster.is_alive():
                cluster.server.scheduler.stop()
                cluster.join(timeout=90)
    rows = {task.id: task for task in Task.query(caching=False).all()}
    lags = {task_id: (when - rows[task_id].completion_time.astimezone()).total_seconds()
            for task_id, when in landed.items()}
    assert max(lags.values()) <= RETURN_BOUND, f'completion to landing: {lags}'
