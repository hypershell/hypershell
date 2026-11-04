# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Recovering a task-row write that a row lock made miss, by fault injection on SQLite.

On a TimescaleDB 2.29+ hypertable, an UPDATE that waited on another transaction's row lock can
match 0 rows although the row exists and still qualifies. SQLite never does that, so these tests
inject it: a wrapper around the session's ``execute`` makes the next *k* UPDATEs of a chosen task
report 0 matched rows without running. Every recovery path is driven in-process, as the server
drives it (``Confirm.update_info``, ``Receiver.update_tasks``, ``Task.revert_orphaned``), and the
wrapper's record of executed statements pins what an uncontended write costs. The real race, on a
server database, is ``tests/test_contended_write.py``.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple
from pathlib import Path

# Standard libs
import io
import logging
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm.exc import StaleDataError

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data import model
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, ConfirmState


CLIENT_A = (uuid(), 'client-a')

# Enough lost attempts that a loop which kept selecting the same row would show it.
ALWAYS = 400


def attempts() -> int:
    """Attempts a contended write makes in all."""
    return 1 + model.CONTENDED_RERUNS


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


@fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture HyperShell's log records at DEBUG."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    return caplog


class Lost:
    """The result of an UPDATE that matched no row."""
    rowcount = 0


class Contention:
    """Wraps the session's `execute`: records each statement and makes chosen UPDATEs match nothing."""

    def __init__(self, execute: Any) -> None:
        self.execute = execute
        self.losing: Dict[str, int] = {}
        self.executed: List[Tuple[str, Optional[str], bool]] = []

    def lose(self, task_id: str, count: int) -> None:
        """Make the next `count` UPDATEs of task `task_id` match no row, without running them."""
        self.losing[task_id] = count

    def __call__(self, statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> Any:
        kind = 'UPDATE' if getattr(statement, 'is_update', False) else (
               'SELECT' if getattr(statement, 'is_select', False) else 'OTHER')
        task_id = params.get('r_id') if isinstance(params, dict) else None
        lost = kind == 'UPDATE' and self.losing.get(task_id, 0) > 0
        self.executed.append((kind, task_id, lost))
        if lost:
            self.losing[task_id] -= 1
            return Lost()
        return self.execute(statement, params, *args, **kwargs)

    def kinds(self, task_id: str = None) -> List[str]:
        """The kinds of statement executed (for one task, if given), in order."""
        return [kind for kind, of, _ in self.executed if task_id is None or of == task_id]

    def clear(self) -> None:
        self.executed.clear()


@fixture
def contention(task_db, monkeypatch) -> Contention:
    """Route the session's direct `execute` calls through a `Contention`."""
    wrapper = Contention(Session.execute)
    monkeypatch.setitem(vars(Session), 'execute', wrapper)
    return wrapper


def submit(args: str = 'echo contended') -> str:
    """Add one task and return its id."""
    task = Task.new(args=args)
    Task.add(task)
    return task.id


def dispatch(limit: int = 1) -> List[Task]:
    """Schedule tasks and return the copies a client receives, through the real bundle codec."""
    return deserialize_tasks(serialize_tasks(Task.next(limit=limit)))


def dispatch_all(ids: List[str]) -> Dict[str, Task]:
    """Schedule exactly the tasks `ids`; return their copies by id."""
    copies = {copy.id: copy for copy in dispatch(limit=len(ids))}
    if set(copies) != set(ids):
        pytest.fail(f'Expected to dispatch {ids}, got {list(copies)}')
    return copies


def confirm(ids: List[str], client: Tuple[str, str] = CLIENT_A) -> ConfirmState:
    """Confirm task ids to a client, as the server's Confirm thread does; return its next state."""
    machine = Confirm(queue=None)
    machine.client_info = ClientInfo(client_id=client[0], client_host=client[1], task_ids=ids)
    return machine.update_info()


def finish(copy: Task, exit_status: int = 0, client: Tuple[str, str] = CLIENT_A) -> Task:
    """Stamp the result columns on a copy, as a client does when it runs the task."""
    start_time = datetime.now().astimezone()
    copy.client_id, copy.client_host = client
    copy.command = copy.args
    copy.start_time = start_time
    copy.completion_time = start_time + timedelta(seconds=2)
    copy.waited = 1
    copy.duration = 2
    copy.exit_status = exit_status
    copy.outpath = f'task-{copy.id}.out'
    copy.errpath = f'task-{copy.id}.err'
    return copy


def row(task_id: str) -> Dict[str, Any]:
    """The task's current row, freshly read."""
    return Task.from_id(task_id, caching=False).to_dict()


def messages(logs: pytest.LogCaptureFixture, level: int, phrase: str = '') -> List[str]:
    """Messages logged at exactly `level` that contain `phrase`."""
    return [record.getMessage() for record in logs.records
            if record.levelno == level and phrase in record.getMessage()]


def reruns(logs: pytest.LogCaptureFixture, path: str, task_id: str) -> List[str]:
    """The DEBUG lines for extra attempts of one task's write on one path."""
    return [message for message in messages(logs, logging.DEBUG, f'Re-running contended {path} write')
            if f'({task_id})' in message]


def in_flight(data: Dict[str, Any]) -> bool:
    """The row is scheduled and not complete."""
    return data['schedule_time'] is not None and data['completion_time'] is None


@mark.unit
def test_contended_confirm_lands_on_rerun(contention, logs) -> None:
    """A confirmation that missed a row the first time re-runs and stamps it."""
    ids = [submit(f'echo {n}') for n in range(3)]
    dispatch_all(ids)
    contention.lose(ids[1], 1)

    assert confirm(ids) is ConfirmState.UNLOAD

    for task_id in ids:
        assert (row(task_id)['client_id'], row(task_id)['client_host']) == CLIENT_A
    assert len(reruns(logs, 'confirm', ids[1])) == 1
    assert reruns(logs, 'confirm', ids[1])[0].endswith(f'(attempt 2 of {attempts()})')
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_confirm_exhausted_keeps_running(contention, logs) -> None:
    """At the bound, the row is left without its client, the rest of the bundle is stamped, and Confirm goes on."""
    ids = [submit(f'echo {n}') for n in range(3)]
    dispatch_all(ids)
    contention.lose(ids[1], ALWAYS)

    assert confirm(ids) is ConfirmState.UNLOAD

    assert (row(ids[1])['client_id'], row(ids[1])['client_host']) == (None, None)
    for task_id in (ids[0], ids[2]):
        assert (row(task_id)['client_id'], row(task_id)['client_host']) == CLIENT_A
    assert contention.kinds(ids[1]).count('UPDATE') == attempts()
    assert len(reruns(logs, 'confirm', ids[1])) == attempts() - 1
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({ids[1]})' in warnings[0] and 'confirm' in warnings[0] and 'client not recorded' in warnings[0]


@mark.unit
def test_contended_revert_lands_on_rerun(contention, logs) -> None:
    """An eviction revert that missed its row the first time re-runs and reverts it."""
    task_id = submit()
    dispatch_all([task_id])
    confirm([task_id])
    contention.lose(task_id, 1)

    Task.revert_orphaned(CLIENT_A[0])

    data = row(task_id)
    assert (data['schedule_time'], data['client_id']) == (None, None)
    assert len(reruns(logs, 'revert', task_id)) == 1
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_revert_exhausted_leaves_row_in_flight(contention, logs) -> None:
    """At the bound, eviction warns and returns after one pass, and the row stays in flight."""
    task_id = submit()
    dispatch_all([task_id])
    confirm([task_id])
    contention.clear()
    contention.lose(task_id, ALWAYS)

    Task.revert_orphaned(CLIENT_A[0])

    data = row(task_id)
    assert in_flight(data) and data['client_id'] == CLIENT_A[0]
    assert contention.kinds(task_id).count('UPDATE') == attempts()
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({task_id})' in warnings[0] and 'revert' in warnings[0] and 'left in flight' in warnings[0]


@mark.unit
def test_contended_interrupted_revert_exhausted_returns(contention, logs) -> None:
    """At the bound, the start-up revert of interrupted tasks returns too, reverting the rest."""
    ids = [submit(f'echo {n}') for n in range(2)]
    dispatch_all(ids)
    contention.lose(ids[0], ALWAYS)

    Task.revert_interrupted()

    assert in_flight(row(ids[0]))
    assert row(ids[1])['schedule_time'] is None
    assert contention.kinds(ids[0]).count('UPDATE') == 2 * attempts()
    assert len(messages(logs, logging.WARNING, f'({ids[0]})')) == 2


@mark.unit
def test_confirm_of_missing_row_raises_and_writes_nothing(task_db, contention) -> None:
    """A confirmation naming a row that no longer exists raises, and stamps none of its bundle."""
    ids = [submit(f'echo {n}') for n in range(3)]
    dispatch_all(ids)
    Task.delete(Task.from_id(ids[1]))

    with pytest.raises(StaleDataError):
        confirm(ids)

    with task_db.connect() as conn:
        stamped = conn.execute(text('SELECT count(*) FROM task WHERE client_id IS NOT NULL')).scalar()
    assert stamped == 0


@mark.unit
def test_uncontended_confirm_and_revert_cost_one_update_per_row(contention, logs) -> None:
    """A write that matches first time is one UPDATE per row, with no re-check and no re-run."""
    ids = [submit(f'echo {n}') for n in range(3)]
    dispatch_all(ids)

    contention.clear()
    confirm(ids)
    assert contention.kinds() == ['UPDATE'] * len(ids)
    assert [task_id for _, task_id, _ in contention.executed] == ids

    contention.clear()
    Task.revert_orphaned(CLIENT_A[0])
    assert contention.kinds() == ['UPDATE'] * len(ids)
    assert all(row(task_id)['schedule_time'] is None for task_id in ids)
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
