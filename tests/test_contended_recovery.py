# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Recovering a task-row write that a row lock made miss, by fault injection on SQLite.

On a TimescaleDB 2.28.1 through 2.31.0 hypertable, an UPDATE that waited on another transaction's
row lock can match 0 rows although the row exists and still qualifies. SQLite never does that, so
these tests inject it: a wrapper around the session's ``execute`` makes the next *k* UPDATEs of a
chosen task report 0 matched rows without running, optionally committing another writer's change to
the row first, as the transaction it waited on would. Every recovery path is driven in-process, as
the server drives it (``Confirm.update_info``, ``Receiver.update_tasks``, ``Task.revert_orphaned``,
``Task.revert_staged``), and the wrapper's record of executed statements pins what an uncontended
write costs. The Scheduler's ``retried`` mark and ``Task.cancel_all`` are made to miss at the
driver instead (``DriverContention``), below the ORM, so a bulk update is reached as well as a
per-row one. ``hs update`` runs in-process (``TaskUpdateApp.main``) on the same engine, with its
writes by id made to miss at the driver too: the legacy single-task form, ``--limit`` and
``--delete --limit``. The real race, on a server database, is ``tests/test_contended_write.py``.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from pathlib import Path

# Standard libs
import io
import re
import json
import logging
from types import SimpleNamespace
from datetime import datetime, timedelta
from queue import Empty as QueueEmpty

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status as cli_status
from sqlalchemy import create_engine, event, text, update, delete
from sqlalchemy.engine import Engine
from sqlalchemy.orm.exc import StaleDataError

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.core.queue import make_sentinel
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data import model
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, ResultOutcome, CANCEL_STATUS, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, ConfirmState, Receiver, ReceiverState
from hypershell.task import TaskUpdateApp


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

    def __init__(self, execute: Any, keys: Tuple[str, ...] = ('r_id', )) -> None:
        self.execute = execute
        self.keys = keys
        self.losing: Dict[str, int] = {}
        self.meanwhile: Dict[str, Callable[[], None]] = {}
        self.executed: List[Tuple[str, Optional[str], bool]] = []

    def lose(self, task_id: str, count: int, meanwhile: Optional[Callable[[], None]] = None) -> None:
        """Make the next `count` UPDATEs of task `task_id` match no row, without running them.

        A `meanwhile` callable runs once, at the first of them: the other transaction's change,
        committed while the write waited on its row lock.
        """
        self.losing[task_id] = count
        if meanwhile is not None:
            self.meanwhile[task_id] = meanwhile

    def __call__(self, statement: Any, params: Any = None, *args: Any, **kwargs: Any) -> Any:
        kind = 'UPDATE' if getattr(statement, 'is_update', False) else (
               'SELECT' if getattr(statement, 'is_select', False) else 'OTHER')
        task_id = next((params[key] for key in self.keys if key in params), None) if isinstance(params, dict) else None
        lost = kind == 'UPDATE' and self.losing.get(task_id, 0) > 0
        self.executed.append((kind, task_id, lost))
        if lost:
            self.losing[task_id] -= 1
            if (change := self.meanwhile.pop(task_id, None)) is not None:
                change()
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


@fixture
def stamp_contention(task_db, monkeypatch) -> Contention:
    """As `contention`, also keyed on the dispatch stamp's id bind, so the stamp can be made to miss."""
    wrapper = Contention(Session.execute, keys=('r_id', 'd_id'))
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


def returned(copies: List[Task]) -> List[Task]:
    """Finished copies as the server receives them, after the bundle's JSON."""
    return deserialize_tasks(serialize_tasks(copies))


class NothingReturned:
    """A queue whose completed-task side is always empty, as when no bundle arrives in time."""

    class Completed:
        @staticmethod
        def get(timeout: float = None) -> bytes:
            raise QueueEmpty()

    completed = Completed()


def receiver(failures: Optional[io.StringIO] = None, in_memory: bool = False) -> Receiver:
    """A Receiver whose queue never delivers a bundle."""
    return Receiver(queue=NothingReturned(), in_memory=in_memory, redirect_failures=failures)


def cycle(machine: Receiver) -> ReceiverState:
    """One Receiver cycle with no new bundle: UNLOAD, and UPDATE if it moves there; return the state after UNLOAD."""
    state = machine.unload_bundle()
    if state is ReceiverState.UPDATE:
        assert machine.update_tasks() is ReceiverState.UNLOAD
    return state


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


@mark.unit
def test_late_confirm_on_complete_row_is_not_contention(contention, logs) -> None:
    """A confirmation that finds its row already complete is settled: one UPDATE, one SELECT, no warning."""
    task_id = submit()
    copy = dispatch_all([task_id])[task_id]
    leaf = (uuid(), 'leaf-a')
    machine = receiver()
    machine.tasks = returned([finish(copy, client=leaf)])
    machine.update_tasks()
    before = row(task_id)
    if (before['client_id'], before['client_host']) != leaf or before['completion_time'] is None:
        pytest.fail(f'The result for task ({task_id}) was not applied')
    contention.clear()
    logs.clear()
    contention.lose(task_id, 1)

    assert confirm([task_id]) is ConfirmState.UNLOAD

    assert row(task_id) == before
    assert contention.kinds(task_id) == ['UPDATE', 'SELECT']
    assert reruns(logs, 'confirm', task_id) == []
    settled = [message for message in messages(logs, logging.DEBUG, 'already complete') if f'({task_id})' in message]
    assert len(settled) == 1, settled
    assert messages(logs, logging.WARNING) == []


def dispatched_and_confirmed(count: int = 1) -> List[Task]:
    """Dispatch and confirm `count` new tasks to the client; return their copies in submission order."""
    ids = [submit(f'echo {n}') for n in range(count)]
    copies = dispatch_all(ids)
    confirm(ids)
    return [copies[task_id] for task_id in ids]


@mark.unit
def test_contended_result_applies_on_rerun(contention, logs) -> None:
    """A result that missed its row the first time re-runs and is applied, with the usual logging."""
    copy, = dispatched_and_confirmed()
    finished = finish(copy, exit_status=3)
    failures = io.StringIO()
    contention.lose(copy.id, 1)

    machine = receiver(failures)
    machine.tasks = returned([finished])
    machine.update_tasks()

    data = row(copy.id)
    assert (data['command'], data['duration'], data['exit_status']) == (copy.args, 2, 3)
    assert data['completion_time'] is not None
    assert failures.getvalue().splitlines() == [copy.args]
    assert machine.held == []
    assert len(reruns(logs, 'result', copy.id)) == 1
    assert messages(logs, logging.DEBUG, f'Completed task ({copy.id})')
    assert messages(logs, logging.WARNING) == [f'Non-zero exit status (3) for task ({copy.id})']


@mark.unit
def test_contended_result_exhausted_is_deferred(contention) -> None:
    """At the bound, a result whose row still qualifies is DEFERRED and writes nothing."""
    copy, = dispatched_and_confirmed()
    before = row(copy.id)
    contention.clear()
    contention.lose(copy.id, ALWAYS)

    assert Task.apply_results(returned([finish(copy)])) == [ResultOutcome.DEFERRED]

    assert row(copy.id) == before
    assert contention.kinds(copy.id) == ['UPDATE', 'SELECT'] * attempts()


@mark.unit
def test_held_result_lands_on_a_later_cycle(contention, logs) -> None:
    """A deferred result is held, warned about once, and applied on a later cycle with no new bundle."""
    copy, = dispatched_and_confirmed()
    finished = finish(copy, exit_status=3)
    failures = io.StringIO()
    contention.lose(copy.id, 2 * attempts())
    machine = receiver(failures)
    machine.tasks = returned([finished])

    machine.update_tasks()
    assert [task.id for task in machine.held] == [copy.id]
    assert row(copy.id)['completion_time'] is None
    assert cycle(machine) is ReceiverState.UPDATE
    assert [task.id for task in machine.held] == [copy.id]
    assert cycle(machine) is ReceiverState.UPDATE

    assert machine.held == []
    data = row(copy.id)
    assert (data['command'], data['duration'], data['exit_status']) == (copy.args, 2, 3)
    assert data['completion_time'] is not None
    assert failures.getvalue().splitlines() == [copy.args]
    assert len(messages(logs, logging.DEBUG, f'Completed task ({copy.id})')) == 1
    assert len(messages(logs, logging.DEBUG, f'Retrying held result for task ({copy.id})')) == 2
    held = messages(logs, logging.WARNING, 'Contended result write did not land')
    assert len(held) == 1 and f'({copy.id})' in held[0] and 'held for retry' in held[0]
    assert cycle(machine) is ReceiverState.UNLOAD


@mark.unit
def test_held_result_refused_once_row_is_reverted(contention, logs) -> None:
    """A held result whose row is reverted meanwhile is refused as stale and released."""
    copy, = dispatched_and_confirmed()
    contention.lose(copy.id, attempts())
    machine = receiver()
    machine.tasks = returned([finish(copy)])
    machine.update_tasks()
    assert [task.id for task in machine.held] == [copy.id]

    Task.revert_orphaned(CLIENT_A[0])
    assert cycle(machine) is ReceiverState.UPDATE

    assert machine.held == []
    assert row(copy.id)['schedule_time'] is None
    stale = messages(logs, logging.WARNING, 'Refused stale result')
    assert len(stale) == 1 and f'({copy.id})' in stale[0]


@mark.unit
def test_held_result_at_shutdown_is_reported(contention, logs) -> None:
    """The shutdown sentinel with a result still held logs it and finishes."""
    copy, = dispatched_and_confirmed()
    contention.lose(copy.id, ALWAYS)
    machine = receiver()
    machine.tasks = returned([finish(copy)])
    machine.update_tasks()

    machine.bundle = make_sentinel()
    assert machine.unpack_bundle() is ReceiverState.FINAL

    shutdown = messages(logs, logging.WARNING, 'not written at shutdown')
    assert shutdown == [f'Result for task ({copy.id}) not written at shutdown - task left interrupted']
    assert in_flight(row(copy.id))


@mark.unit
def test_uncontended_results_cost_what_they_did(contention, logs) -> None:
    """Applied is one UPDATE; stale and duplicate are one UPDATE and one SELECT; nothing is held."""
    first, second = dispatched_and_confirmed(2)
    machine = receiver()

    contention.clear()
    machine.tasks = returned([finish(first)])
    machine.update_tasks()
    assert contention.kinds() == ['UPDATE']

    contention.clear()
    machine.tasks = returned([finish(first)])
    machine.update_tasks()
    assert contention.kinds() == ['UPDATE', 'SELECT']
    assert len(messages(logs, logging.WARNING, 'Ignored duplicate result')) == 1

    Task.revert_orphaned(CLIENT_A[0])
    contention.clear()
    machine.tasks = returned([finish(second)])
    machine.update_tasks()
    assert contention.kinds() == ['UPDATE', 'SELECT']
    assert len(messages(logs, logging.WARNING, 'Refused stale result')) == 1

    assert machine.held == []
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert cycle(machine) is ReceiverState.UNLOAD


@mark.unit
def test_in_memory_receiver_never_holds(contention, logs) -> None:
    """In memory, results are not written, so none is ever held."""
    copy, = dispatched_and_confirmed()
    contention.lose(copy.id, ALWAYS)
    machine = receiver(in_memory=True)

    contention.clear()
    machine.tasks = returned([finish(copy)])
    machine.update_tasks()

    assert contention.kinds() == []
    assert machine.held == []
    assert messages(logs, logging.DEBUG, f'Completed task ({copy.id})')
    assert cycle(machine) is ReceiverState.UNLOAD


def staged(count: int = 1) -> List[str]:
    """Stamp `count` new tasks as the Scheduler does when it stages them for a zone; return their ids."""
    ids = [submit(f'echo {n}') for n in range(count)]
    dispatch_all(ids)
    return ids


def committed(engine: Engine, statement: Any) -> Callable[[], None]:
    """A change to the task table committed by another connection, as another writer's transaction."""
    def change() -> None:
        with engine.begin() as conn:
            if conn.execute(statement).rowcount != 1:
                pytest.fail(f'The other writer matched no row: {statement}')
    return change


def cancelled(engine: Engine, task_id: str) -> Callable[[], None]:
    """Cancel the task from another connection, as `hs update --cancel` does."""
    now = datetime.now().astimezone()
    table = Task.__table__
    return committed(engine, update(table).where(table.c.id == task_id)
                     .values(schedule_time=now, completion_time=now, exit_status=CANCEL_STATUS))


def deleted(engine: Engine, task_id: str) -> Callable[[], None]:
    """Delete the task's row from another connection."""
    table = Task.__table__
    return committed(engine, delete(table).where(table.c.id == task_id))


@mark.unit
def test_contended_staged_revert_lands_on_rerun(contention, logs) -> None:
    """A staged revert that missed a row the first time re-runs and reverts it, in the same pass."""
    ids = staged(3)
    contention.lose(ids[1], 1)

    assert Task.revert_staged(ids) == 3

    assert all(row(task_id)['schedule_time'] is None for task_id in ids)
    assert len(reruns(logs, 'staged revert', ids[1])) == 1
    assert reruns(logs, 'staged revert', ids[1])[0].endswith(f'(attempt 2 of {attempts()})')
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_staged_revert_exhausted_leaves_row_staged(contention, logs) -> None:
    """At the bound, the row stays stamped and is reported, and the rest of the pass is reverted."""
    ids = staged(3)
    contention.clear()
    contention.lose(ids[1], ALWAYS)

    assert Task.revert_staged(ids) == 2

    assert in_flight(row(ids[1]))
    assert row(ids[0])['schedule_time'] is None and row(ids[2])['schedule_time'] is None
    assert contention.kinds(ids[1]) == ['UPDATE', 'SELECT'] * attempts()
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({ids[1]})' in warnings[0] and 'staged revert' in warnings[0] and 'left in flight' in warnings[0]


@mark.unit
def test_staged_revert_keeps_row_cancelled_while_contended(task_db, contention, logs) -> None:
    """A row cancelled by the transaction the revert waited on is not re-run and stays cancelled."""
    ids = staged(2)
    contention.clear()
    contention.lose(ids[0], 1, meanwhile=cancelled(task_db, ids[0]))

    assert Task.revert_staged(ids) == 1

    data = row(ids[0])
    assert data['exit_status'] == CANCEL_STATUS and data['completion_time'] is not None
    assert row(ids[1])['schedule_time'] is None
    assert contention.kinds(ids[0]) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_staged_revert_skips_row_deleted_while_contended(task_db, contention, logs) -> None:
    """A row deleted by the transaction the revert waited on is skipped without an error."""
    ids = staged(2)
    contention.clear()
    contention.lose(ids[0], 1, meanwhile=deleted(task_db, ids[0]))

    assert Task.revert_staged(ids) == 1

    with task_db.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM task')).scalar() == 1
    assert row(ids[1])['schedule_time'] is None
    assert contention.kinds(ids[0]) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_uncontended_staged_revert_costs_one_update_per_row(contention, logs) -> None:
    """A staged revert that matches first time is one UPDATE per row; a terminal row adds one SELECT."""
    ids = staged(3)
    Task.cancel(ids[2])

    contention.clear()
    assert Task.revert_staged(ids) == 2

    assert contention.kinds() == ['UPDATE', 'UPDATE', 'UPDATE', 'SELECT']
    assert [task_id for _, task_id, _ in contention.executed] == [ids[0], ids[1], ids[2], ids[2]]
    assert row(ids[2])['exit_status'] == CANCEL_STATUS
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []


# --- The eviction revert: a row that changes while the revert waits ---------------------------------


CLIENT_B = (uuid(), 'client-b')


def completed_as(engine: Engine, task_id: str, client: Tuple[str, str] = CLIENT_A) -> Callable[[], None]:
    """The Receiver's write of a result for the task, committed from another connection."""
    now = datetime.now().astimezone()
    table = Task.__table__
    return committed(engine, update(table).where(table.c.id == task_id, table.c.completion_time.is_(None))
                     .values(client_id=client[0], client_host=client[1], command='echo contended',
                             start_time=now, completion_time=now, exit_status=0, waited=0, duration=0))


def confirmed_to(engine: Engine, task_id: str, client: Tuple[str, str]) -> Callable[[], None]:
    """A confirmation of the task to another client, committed from another connection."""
    table = Task.__table__
    return committed(engine, update(table).where(table.c.id == task_id)
                     .values(client_id=client[0], client_host=client[1]))


@mark.unit
def test_contended_revert_keeps_a_result_landed_meanwhile(task_db, contention, logs) -> None:
    """An eviction revert that waited on the result of its row keeps the result and settles the row."""
    first, second = (copy.id for copy in dispatched_and_confirmed(2))
    contention.clear()
    contention.lose(first, 1, meanwhile=completed_as(task_db, first))

    Task.revert_orphaned(CLIENT_A[0])

    data = row(first)
    assert data['completion_time'] is not None and data['exit_status'] == 0
    assert data['client_id'] == CLIENT_A[0]
    assert row(second)['schedule_time'] is None
    assert contention.kinds(first) == ['UPDATE', 'SELECT']
    settled = [message for message in messages(logs, logging.DEBUG, 'already complete') if f'({first})' in message]
    assert len(settled) == 1, settled
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_revert_leaves_a_row_confirmed_to_another_client(task_db, contention, logs) -> None:
    """An eviction revert that waited on a confirmation of its row to another client leaves it with that client."""
    first, second = (copy.id for copy in dispatched_and_confirmed(2))
    contention.clear()
    contention.lose(first, 1, meanwhile=confirmed_to(task_db, first, CLIENT_B))

    Task.revert_orphaned(CLIENT_A[0])

    data = row(first)
    assert in_flight(data) and (data['client_id'], data['client_host']) == CLIENT_B
    assert row(second)['schedule_time'] is None
    assert contention.kinds(first) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_revert_skips_a_row_deleted_meanwhile(task_db, contention, logs) -> None:
    """An eviction revert that waited on a delete of its row skips it, without an error, and reverts the rest."""
    first, second = (copy.id for copy in dispatched_and_confirmed(2))
    contention.clear()
    contention.lose(first, 1, meanwhile=deleted(task_db, first))

    Task.revert_orphaned(CLIENT_A[0])

    with task_db.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM task')).scalar() == 1
    assert row(second)['schedule_time'] is None
    assert contention.kinds(first) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.WARNING) == []


# --- The dispatch stamp ------------------------------------------------------------------------------


def stamped(data: Dict[str, Any]) -> bool:
    """The row carries this server's dispatch stamp."""
    return data['schedule_time'] is not None and data['server_id'] is not None


@mark.unit
def test_contended_dispatch_lands_on_rerun(stamp_contention, logs) -> None:
    """A dispatch stamp that missed a row the first time re-runs and stamps it, in the same chunk."""
    ids = [submit(f'echo {n}') for n in range(3)]
    stamp_contention.lose(ids[1], 1)

    assert [task.id for task in Task.next(limit=3)] == ids

    assert all(stamped(row(task_id)) for task_id in ids)
    assert stamp_contention.kinds(ids[1]) == ['UPDATE', 'SELECT', 'UPDATE']
    assert len(reruns(logs, 'dispatch', ids[1])) == 1
    assert reruns(logs, 'dispatch', ids[1])[0].endswith(f'(attempt 2 of {attempts()})')
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_dispatch_exhausted_leaves_row_unscheduled(stamp_contention, logs) -> None:
    """At the bound, the row is left unscheduled and reported, the rest is dispatched, and the next round takes it."""
    ids = [submit(f'echo {n}') for n in range(3)]
    stamp_contention.lose(ids[1], ALWAYS)

    assert [task.id for task in Task.next(limit=3)] == [ids[0], ids[2]]

    assert row(ids[1])['schedule_time'] is None and row(ids[1])['server_id'] is None
    assert stamp_contention.kinds(ids[1]) == ['UPDATE', 'SELECT'] * attempts()
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({ids[1]})' in warnings[0] and 'dispatch' in warnings[0] and 'left unscheduled' in warnings[0]

    stamp_contention.losing.clear()
    assert [task.id for task in Task.next(limit=3)] == [ids[1]]
    assert stamped(row(ids[1]))


@mark.unit
def test_contended_dispatch_skips_a_row_cancelled_meanwhile(task_db, stamp_contention, logs) -> None:
    """A dispatch stamp that waited on a cancel of its row skips it, and the row stays cancelled."""
    ids = [submit(f'echo {n}') for n in range(2)]
    stamp_contention.lose(ids[0], 1, meanwhile=cancelled(task_db, ids[0]))

    assert [task.id for task in Task.next(limit=2)] == [ids[1]]

    data = row(ids[0])
    assert data['exit_status'] == CANCEL_STATUS and data['completion_time'] is not None
    assert data['server_id'] is None
    assert stamp_contention.kinds(ids[0]) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_dispatch_skips_a_row_deleted_meanwhile(task_db, stamp_contention, logs) -> None:
    """A dispatch stamp that waited on a delete of its row skips it without an error."""
    ids = [submit(f'echo {n}') for n in range(2)]
    stamp_contention.lose(ids[0], 1, meanwhile=deleted(task_db, ids[0]))

    assert [task.id for task in Task.next(limit=2)] == [ids[1]]

    with task_db.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM task')).scalar() == 1
    assert stamped(row(ids[1]))
    assert stamp_contention.kinds(ids[0]) == ['UPDATE', 'SELECT']
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_uncontended_dispatch_costs_one_update_per_row(stamp_contention, logs) -> None:
    """A dispatch stamp that matches first time is one UPDATE per row, with no re-check and no re-run."""
    ids = [submit(f'echo {n}') for n in range(3)]

    assert [task.id for task in Task.next(limit=3)] == ids

    assert stamp_contention.kinds() == ['UPDATE'] * len(ids)
    assert [task_id for _, task_id, _ in stamp_contention.executed] == ids
    assert all(stamped(row(task_id)) for task_id in ids)
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []


# --- The retry mark and the cancel, lost at the driver ----------------------------------------------


# A column named in an UPDATE's SET clause, as SQLite's compiled statements write it.
SET_COLUMN = re.compile(r'"?(\w+)"?\s*=\s*\?')

# The columns a cancel writes, and nothing else.
CANCEL_COLUMNS = frozenset({'schedule_time', 'completion_time', 'exit_status'})

# What a lost write runs in its place: an UPDATE of `task` that matches no row.
LOST_UPDATE = 'UPDATE task SET id = id WHERE 0'


class DriverContention:
    """Makes chosen writes of chosen tasks match no row, at the driver; records statements on `task`.

    Installed on the engine by the dialect's `do_execute` and `do_executemany` events, below the
    ORM, so a bulk update or delete (one executemany) is reached as well as a per-row one. A lost
    write runs a 0-row UPDATE in its place; in an executemany, the lost task's parameter set is
    dropped and the rest run. Only writes of the `kinds` given are ever lost or made to wait. The
    default is the retry mark (an UPDATE whose SET names `retried`) and the cancel (an UPDATE whose
    SET is exactly the columns a cancel writes), so the dispatch stamp, the result write and the
    reverts always land; `hs update`'s tests add any other UPDATE ('UPDATE') and the DELETE by id
    ('DELETE').

    Each statement on `task` is recorded once per parameter set as (kind, task id): the kind is
    'mark', 'cancel', 'UPDATE', 'DELETE', 'SELECT' or 'INSERT'; the task id is the parameter the
    WHERE clause opens on (`task.id = ?`), else None. Another writer's change, run by `lose`, is
    neither recorded nor lost. A write made to `wait` on another writer's change (`waits`) is not
    lost: the change commits first and the write then runs as written, on the row as the change
    left it, as PostgreSQL re-checks a row it waited on.
    """

    def __init__(self, engine: Engine, kinds: Tuple[str, ...] = ('mark', 'cancel')) -> None:
        self.engine = engine
        self.lost_kinds = kinds
        self.losing: Dict[str, int] = {}
        self.meanwhile: Dict[str, Callable[[], None]] = {}
        self.executed: List[Tuple[str, Optional[str]]] = []
        self.passing = False
        event.listen(engine, 'do_execute', self.do_execute)
        event.listen(engine, 'do_executemany', self.do_executemany)

    def remove(self) -> None:
        """Uninstall from the engine."""
        event.remove(self.engine, 'do_execute', self.do_execute)
        event.remove(self.engine, 'do_executemany', self.do_executemany)

    def lose(self, task_id: str, count: int, meanwhile: Optional[Callable[[], None]] = None) -> None:
        """Make the next `count` writes (of the kinds given) of task `task_id` match no row.

        A `meanwhile` callable runs once, at the first of them, before the 0-row UPDATE: the other
        transaction's change, committed while the write waited on its row lock.
        """
        self.losing[task_id] = count
        if meanwhile is not None:
            self.meanwhile[task_id] = meanwhile

    def waits(self, task_id: str, meanwhile: Callable[[], None]) -> None:
        """Make the next write (of the kinds given) of task `task_id` wait on another writer's change, then run it.

        `meanwhile` runs once, before that write: the other transaction's change, committed while the
        write waited on its row lock. The write itself is not lost, so its own row condition decides
        whether it matches the row as the change left it.
        """
        self.meanwhile[task_id] = meanwhile

    @staticmethod
    def classify(statement: str) -> Optional[Tuple[str, Optional[int]]]:
        """The kind of a statement on `task` and the position of its task-id parameter; None for other tables."""
        sql = ' '.join(statement.split())
        where = sql.find(' WHERE ')
        position = None
        if where >= 0 and sql.startswith('task.id = ?', where + len(' WHERE ')):
            position = sql.count('?', 0, where)
        if sql.startswith('UPDATE task SET '):
            names = set(SET_COLUMN.findall(sql[len('UPDATE task SET '):where if where >= 0 else None]))
            kind = 'mark' if 'retried' in names else 'cancel' if names == CANCEL_COLUMNS else 'UPDATE'
        elif sql.startswith('DELETE FROM task '):
            kind = 'DELETE'
        elif sql.startswith('INSERT INTO task '):
            kind = 'INSERT'
        elif sql.startswith('SELECT ') and ' FROM task ' in f'{sql} ':
            kind = 'SELECT'
        else:
            return None
        return kind, position

    @staticmethod
    def task_of(params: Any, position: Optional[int]) -> Optional[str]:
        """The task id in one positional parameter set, if the statement names one."""
        if position is None or not isinstance(params, (tuple, list)) or position >= len(params):
            return None
        return str(params[position])

    def take(self, kind: str, task_id: Optional[str]) -> bool:
        """Whether this write is lost; runs the other writer's change at the task's first write (of the kinds given)."""
        if kind not in self.lost_kinds:
            return False
        if (change := self.meanwhile.pop(task_id, None)) is not None:
            self.passing = True
            try:
                change()
            finally:
                self.passing = False
        if self.losing.get(task_id, 0) <= 0:
            return False
        self.losing[task_id] -= 1
        return True

    def do_execute(self, cursor: Any, statement: str, params: Any, context: Any) -> bool:
        if self.passing or (shape := self.classify(statement)) is None:
            return False
        kind, position = shape
        task_id = self.task_of(params, position)
        self.executed.append((kind, task_id))
        if not self.take(kind, task_id):
            return False
        cursor.execute(LOST_UPDATE)
        return True

    def do_executemany(self, cursor: Any, statement: str, params: Any, context: Any) -> bool:
        if self.passing or (shape := self.classify(statement)) is None:
            return False
        kind, position = shape
        kept = []
        for each in params:
            task_id = self.task_of(each, position)
            self.executed.append((kind, task_id))
            if not self.take(kind, task_id):
                kept.append(each)
        if len(kept) == len(params):
            return False
        if kept:
            cursor.executemany(statement, kept)
        else:
            cursor.execute(LOST_UPDATE)
        return True

    def kinds(self, task_id: str = None) -> List[str]:
        """The kinds of statement executed (for one task, if given), in order."""
        return [kind for kind, of in self.executed if task_id is None or of == task_id]

    def clear(self) -> None:
        self.executed.clear()


@fixture
def driver(task_db) -> Iterator[DriverContention]:
    """A `DriverContention` on the per-test engine."""
    injector = DriverContention(task_db)
    try:
        yield injector
    finally:
        injector.remove()


def from_mark(kinds: List[str]) -> List[str]:
    """The statements from the first retry mark on (none if there was no mark)."""
    return kinds[kinds.index('mark'):] if 'mark' in kinds else []


def failed(count: int = 1) -> List[str]:
    """Dispatch `count` new tasks and write back a failure for each, in that order; return their ids."""
    copies = dispatched_and_confirmed(count)
    finished = [finish(copy, exit_status=1) for copy in copies]
    for n, copy in enumerate(finished):
        copy.completion_time += timedelta(seconds=n)  # NOTE: the retry selection orders by completion time.
    machine = receiver(io.StringIO())
    machine.tasks = returned(finished)
    machine.update_tasks()
    ids = [copy.id for copy in copies]
    if [row(task_id)['exit_status'] for task_id in ids] != [1] * count:
        pytest.fail(f'The failures of {ids} were not written back')
    return ids


def retry(limit: int) -> List[Tuple[str, str]]:
    """One Scheduler round with retries allowed; return (id, previous id) of each task it dispatched."""
    return [(task.id, task.previous_id) for task in Task.next(limit=limit, attempts=2)]


def retries_of(engine: Engine, task_id: str) -> List[str]:
    """Ids of the committed rows that name the task as their previous attempt."""
    with engine.connect() as conn:
        return list(conn.execute(text('SELECT id FROM task WHERE previous_id = :id'), {'id': task_id}).scalars())


def cancelled_ids(engine: Engine) -> List[str]:
    """Ids of the committed rows that are cancelled."""
    with engine.connect() as conn:
        return sorted(conn.execute(text('SELECT id FROM task WHERE exit_status = :status'),
                                   {'status': CANCEL_STATUS}).scalars())


def marked_by_another(engine: Engine, task_id: str, next_id: str) -> Callable[[], None]:
    """The task marked retried, naming `next_id`, from another connection."""
    table = Task.__table__
    return committed(engine, update(table).where(table.c.id == task_id).values(retried=True, next_id=next_id))


@mark.unit
def test_contended_retry_mark_lands_on_rerun(task_db, driver, logs) -> None:
    """A retry mark that missed its row the first time re-runs and lands, with the one retry row it names."""
    failed_id, = failed()
    logs.clear()
    driver.lose(failed_id, 1)

    dispatched = retry(limit=1)

    retry_ids = retries_of(task_db, failed_id)
    assert len(retry_ids) == 1, retry_ids
    assert dispatched == [(retry_ids[0], failed_id)]
    data = row(failed_id)
    assert data['retried'] and data['next_id'] == retry_ids[0]
    assert stamped(row(retry_ids[0]))
    assert len(reruns(logs, 'retry mark', failed_id)) == 1
    assert reruns(logs, 'retry mark', failed_id)[0].endswith(f'(attempt 2 of {attempts()})')
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_retry_mark_exhausted_leaves_row_retryable(task_db, driver, logs) -> None:
    """At the bound, the row gets no retry row and stays retryable, the rest of the batch is retried,
    and the next round retries it once."""
    first, second = failed(2)
    logs.clear()
    driver.lose(first, ALWAYS)

    dispatched = retry(limit=2)

    assert driver.kinds(first).count('mark') == attempts()
    assert retries_of(task_db, first) == []
    assert not row(first)['retried'] and row(first)['next_id'] is None
    assert [previous for _, previous in dispatched] == [second]
    assert retries_of(task_db, second) == [dispatched[0][0]]
    assert row(second)['retried'] and row(second)['next_id'] == dispatched[0][0]
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({first})' in warnings[0] and 'retry mark' in warnings[0]

    driver.losing.clear()
    assert [previous for _, previous in retry(limit=2)] == [first]
    assert len(retries_of(task_db, first)) == 1
    assert row(first)['retried']


@mark.unit
def test_retry_mark_skips_row_cancelled_meanwhile(task_db, driver, logs) -> None:
    """A retry mark that waited on a cancel of its row gives it no retry row, and the row stays cancelled."""
    first, second = failed(2)
    logs.clear()
    driver.clear()
    driver.lose(first, 1, meanwhile=cancelled(task_db, first))

    dispatched = retry(limit=2)

    assert from_mark(driver.kinds(first)) == ['mark', 'SELECT']
    assert retries_of(task_db, first) == []
    data = row(first)
    assert data['exit_status'] == CANCEL_STATUS and not data['retried'] and data['next_id'] is None
    assert [previous for _, previous in dispatched] == [second]
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_retry_mark_skips_row_deleted_meanwhile(task_db, driver, logs) -> None:
    """A retry mark that waited on a delete of its row gives it no retry row, without an error."""
    first, second = failed(2)
    logs.clear()
    driver.clear()
    driver.lose(first, 1, meanwhile=deleted(task_db, first))

    dispatched = retry(limit=2)

    assert from_mark(driver.kinds(first)) == ['mark', 'SELECT']
    assert retries_of(task_db, first) == []
    with task_db.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM task WHERE id = :id'), {'id': first}).scalar() == 0
    assert [previous for _, previous in dispatched] == [second]
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_retry_mark_skips_row_retried_meanwhile(task_db, driver, logs) -> None:
    """A retry mark that waited on another writer marking its row retried adds no second retry row."""
    first, second = failed(2)
    other = uuid()
    logs.clear()
    driver.clear()
    driver.lose(first, 1, meanwhile=marked_by_another(task_db, first, other))

    dispatched = retry(limit=2)

    assert from_mark(driver.kinds(first)) == ['mark', 'SELECT']
    assert retries_of(task_db, first) == []
    data = row(first)
    assert data['retried'] and data['next_id'] == other
    assert [previous for _, previous in dispatched] == [second]
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
@mark.parametrize('change', ['cancelled', 'retried'])
def test_retry_mark_that_waited_matches_only_a_row_awaiting_retry(task_db, driver, logs, change: str) -> None:
    """A retry mark that waited on a cancel of its row, or on another writer marking it retried, and then
    ran on the row as that left it, matches nothing: the row gets no retry row and keeps the other change."""
    first, second = failed(2)
    other = uuid()
    logs.clear()
    driver.clear()
    driver.waits(first, cancelled(task_db, first) if change == 'cancelled' else marked_by_another(task_db, first, other))

    dispatched = retry(limit=2)

    assert from_mark(driver.kinds(first)) == ['mark', 'SELECT']
    assert retries_of(task_db, first) == []
    data = row(first)
    if change == 'cancelled':
        assert data['exit_status'] == CANCEL_STATUS and not data['retried'] and data['next_id'] is None
    else:
        assert data['retried'] and data['next_id'] == other
    assert [previous for _, previous in dispatched] == [second]
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []
    assert messages(logs, logging.WARNING) == []


class Stopped(BaseException):
    """The process stopping mid-call (never caught by `except Exception`)."""


@mark.unit
def test_stop_between_retry_insert_and_mark_leaves_no_retry(task_db, logs) -> None:
    """A stop between the retry row's insert and the retry mark leaves no retry row, and the next round
    retries the row once."""
    failed_id, = failed()
    seen: List[str] = []

    def stop(conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool) -> None:
        kind = (DriverContention.classify(statement) or (None, None))[0]
        if kind in ('INSERT', 'mark') and kind not in seen:
            seen.append(kind)
            if len(seen) == 2:
                raise Stopped()

    event.listen(task_db, 'before_cursor_execute', stop)
    try:
        with pytest.raises(Stopped):
            retry(limit=1)
    finally:
        event.remove(task_db, 'before_cursor_execute', stop)
        Session.remove()  # NOTE: the connection goes away with the process.

    assert retries_of(task_db, failed_id) == []
    assert not row(failed_id)['retried'] and row(failed_id)['next_id'] is None
    assert [previous for _, previous in retry(limit=5)] == [failed_id]
    assert len(retries_of(task_db, failed_id)) == 1


@mark.unit
def test_contended_cancel_lands_on_rerun(task_db, driver, logs) -> None:
    """A cancel that missed a row the first time re-runs and cancels it, with the rest of the call."""
    ids = [submit(f'echo {n}') for n in range(3)]
    driver.lose(ids[1], 1)

    assert Task.cancel_all(ids) == ids

    assert cancelled_ids(task_db) == sorted(ids)
    assert len(reruns(logs, 'cancel', ids[1])) == 1
    assert reruns(logs, 'cancel', ids[1])[0].endswith(f'(attempt 2 of {attempts()})')
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_contended_cancel_exhausted_reports_rows_not_cancelled(task_db, driver, logs) -> None:
    """At the bound, the row is left as it was and left out of what `cancel_all` returns; the rest is
    cancelled and committed, and `cancel` of that row says it did not cancel it."""
    ids = [submit(f'echo {n}') for n in range(3)]
    before = row(ids[1])
    driver.lose(ids[1], ALWAYS)

    assert Task.cancel_all(ids) == [ids[0], ids[2]]

    assert cancelled_ids(task_db) == sorted([ids[0], ids[2]])
    assert row(ids[1]) == before
    assert driver.kinds(ids[1]).count('cancel') == attempts()
    warnings = messages(logs, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert f'({ids[1]})' in warnings[0] and 'cancel' in warnings[0]

    assert Task.cancel(ids[1]) is False
    assert row(ids[1]) == before


@mark.unit
def test_uncontended_retry_mark_costs_one_update_per_row(task_db, driver, logs) -> None:
    """A retry mark that matches first time is one UPDATE per row, with no re-check and no re-run."""
    ids = failed(2)
    driver.clear()

    dispatched = retry(limit=2)

    assert {task_id: from_mark(driver.kinds(task_id)) for task_id in ids} == {task_id: ['mark'] for task_id in ids}
    assert driver.kinds().count('mark') == len(ids)
    assert sorted(previous for _, previous in dispatched) == sorted(ids)
    assert all(row(task_id)['retried'] for task_id in ids)
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []


@mark.unit
def test_uncontended_cancel_costs_one_update_per_row(task_db, driver, logs) -> None:
    """A cancel that matches first time is one UPDATE per row, with no re-check and no re-run."""
    ids = [submit(f'echo {n}') for n in range(3)]
    driver.clear()

    Task.cancel_all(ids)

    assert driver.kinds() == ['cancel'] * len(ids)
    assert sorted(task_id for _, task_id in driver.executed) == sorted(ids)
    assert cancelled_ids(task_db) == sorted(ids)
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []


@mark.unit
def test_cancel_of_gone_row_raises_and_cancels_nothing(task_db) -> None:
    """A cancel naming a row that no longer exists raises, and cancels none of the rows it names."""
    ids = [submit(f'echo {n}') for n in range(3)]
    Task.delete(Task.from_id(ids[1]))

    with pytest.raises(StaleDataError):
        Task.cancel_all(ids)

    assert cancelled_ids(task_db) == []


# --- hs update, its writes by id lost at the driver ------------------------------------------------


# The writes of `hs update` that can be lost: a field or tag change, a cancel, and a delete by id.
UPDATE_KINDS = ('UPDATE', 'cancel', 'DELETE')


@fixture
def update_driver(task_db) -> Iterator[DriverContention]:
    """A `DriverContention` on the per-test engine that can lose any write `hs update` makes by id."""
    injector = DriverContention(task_db, kinds=UPDATE_KINDS)
    try:
        yield injector
    finally:
        injector.remove()


@fixture
def hs_update(task_db, monkeypatch, tmp_path) -> Callable[..., int]:
    """Run `hs update ARGS... -f` in-process on the per-test engine; return its exit status.

    The database is already initialized, so `ensuredb` is skipped; a traceback file, if one is
    written, lands under the test's own directory. Each run starts from the parser's own defaults
    (a filter alias appends to the `--where` list, which would otherwise be shared between runs).
    """
    monkeypatch.setattr('hypershell.task.ensuredb', lambda: None)
    log_dir = tmp_path / 'log'
    log_dir.mkdir()
    monkeypatch.setattr('hypershell.core.exceptions.default_path', SimpleNamespace(log=str(log_dir)))
    where = next(action for action in TaskUpdateApp.interface._actions  # noqa: SLF001
                 if action.dest == 'where_clauses')

    def run(*argv: str) -> int:
        monkeypatch.setattr(where, 'default', [])
        try:
            return TaskUpdateApp.main([*argv, '-f'])
        finally:
            Session.remove()  # NOTE: the connection goes away with the process.

    return run


def submitted(count: int, tag: Optional[Dict[str, Any]] = None) -> List[str]:
    """Add `count` tasks (args ordered as their position, zero-padded); return their ids in that order."""
    tasks = [Task.new(args=f'echo {n:03d}', tag=dict(tag or {})) for n in range(count)]
    Task.add_all(tasks)
    return [task.id for task in tasks]


def rows(engine: Engine) -> Dict[str, Dict[str, Any]]:
    """Every committed task row by id: group, exit status, completion time and tag."""
    with engine.connect() as conn:
        found = conn.execute(text('SELECT id, "group", exit_status, completion_time, tag FROM task')).mappings().all()
    return {data['id']: {'group': data['group'], 'exit_status': data['exit_status'],
                         'completion_time': data['completion_time'],
                         'tag': json.loads(data['tag']) if isinstance(data['tag'], str) else data['tag']}
            for data in found}


def severe(logs: pytest.LogCaptureFixture) -> List[str]:
    """Messages logged at ERROR or above."""
    return [record.getMessage() for record in logs.records if record.levelno >= logging.ERROR]


def contended_error(logs: pytest.LogCaptureFixture, task_id: str) -> List[str]:
    """Messages at ERROR or above that name the task and say the update was contended and nothing was written."""
    return [message for message in severe(logs)
            if task_id in message and 'contended' in message.lower() and 'nothing was written' in message]


def traceback_written(logs: pytest.LogCaptureFixture, tmp_path: Path) -> bool:
    """A traceback file was written, or a line said so."""
    return (any('traceback written' in record.getMessage() for record in logs.records)
            or any((tmp_path / 'log').iterdir()))


def any_rerun(logs: pytest.LogCaptureFixture, task_id: str) -> List[str]:
    """The DEBUG lines for extra attempts of one task's write, on any path."""
    return [message for message in messages(logs, logging.DEBUG, 'Re-running contended') if f'({task_id})' in message]


NOT_MATCHED = '1 of the 4 tasks counted no longer matched and were left as they are'


@mark.unit
def test_update_legacy_contended_lands_on_rerun(task_db, update_driver, hs_update, logs) -> None:
    """`hs update ID FIELD VALUE` on a task whose write missed the first time re-runs it and writes it, exit 0."""
    task_id, other = submitted(2)
    update_driver.lose(task_id, 1)

    assert hs_update(task_id, 'group', '3') == cli_status.success

    assert rows(task_db)[task_id]['group'] == 3
    assert rows(task_db)[other]['group'] == 0
    assert len(any_rerun(logs, task_id)) == 1
    assert severe(logs) == []
    assert messages(logs, logging.WARNING) == []


@mark.unit
def test_update_legacy_contended_exhausted_writes_nothing(task_db, update_driver, hs_update, logs, tmp_path) -> None:
    """At the bound, the legacy form writes nothing, says the update was contended, and exits 5 without a traceback."""
    task_id, = submitted(1)
    before = rows(task_db)
    update_driver.lose(task_id, ALWAYS)

    assert hs_update(task_id, 'group', '3') == cli_status.runtime_error

    assert rows(task_db) == before
    assert contended_error(logs, task_id), severe(logs)
    assert not any('not found' in message.lower() or 'NotFound' in message for message in severe(logs))
    assert not traceback_written(logs, tmp_path)


@mark.unit
def test_update_legacy_missing_task_not_found(task_db, update_driver, hs_update, logs, tmp_path) -> None:
    """The legacy form on an id with no row reports it not found and exits 5, without a traceback."""
    submitted(1)
    before = rows(task_db)
    missing = uuid()

    assert hs_update(missing, 'group', '3') == cli_status.runtime_error

    assert rows(task_db) == before
    assert any('NotFound' in message for message in severe(logs)), severe(logs)
    assert not any('contended' in message.lower() for message in severe(logs))
    assert not traceback_written(logs, tmp_path)


@mark.unit
def test_update_limit_contended_lands_on_rerun(task_db, update_driver, hs_update, logs) -> None:
    """`--limit` with a field and a tag change: a row whose write missed once is re-run, and every row gets both."""
    ids = submitted(4)
    update_driver.lose(ids[1], 1)

    assert hs_update('--limit', '4', '-s', 'args', '-R', 'group=7', 'color:red') == cli_status.success

    after = rows(task_db)
    assert {task_id: (after[task_id]['group'], after[task_id]['tag']) for task_id in ids} == \
        {task_id: (7, {'color': 'red'}) for task_id in ids}
    assert len(any_rerun(logs, ids[1])) == 1
    assert messages(logs, logging.INFO, 'Updated 4 tasks')
    assert severe(logs) == []


@mark.unit
def test_update_limit_contended_exhausted_writes_nothing(task_db, update_driver, hs_update, logs, tmp_path) -> None:
    """At the bound, `--limit` over 150 rows writes none of them (not even those before the contended one),
    says the update was contended, and exits 5 without a traceback."""
    ids = submitted(150)
    before = rows(task_db)
    lost = ids[119]
    update_driver.lose(lost, ALWAYS)

    assert hs_update('--limit', '150', '-s', 'args', '-R', 'group=7') == cli_status.runtime_error

    after = rows(task_db)
    assert [task_id for task_id in ids if after[task_id] != before[task_id]] == []
    assert contended_error(logs, lost), severe(logs)
    assert not traceback_written(logs, tmp_path)


@mark.unit
def test_update_limit_skips_task_changed_out_of_filter(task_db, update_driver, hs_update, logs) -> None:
    """A selected task that completed before its write no longer matches `-R`: `--limit` leaves it with its
    result, does not count it, and says so; exit 0."""
    ids = submitted(4)
    update_driver.waits(ids[0], completed_as(task_db, ids[0]))

    assert hs_update('--limit', '4', '-s', 'args', '-R', 'group=7') == cli_status.success

    after = rows(task_db)
    assert (after[ids[0]]['group'], after[ids[0]]['exit_status']) == (0, 0)
    assert after[ids[0]]['completion_time'] is not None
    assert [after[task_id]['group'] for task_id in ids[1:]] == [7, 7, 7]
    assert messages(logs, logging.WARNING, NOT_MATCHED)
    assert messages(logs, logging.INFO, 'Updated 3 tasks')
    assert severe(logs) == []


@mark.unit
def test_update_limit_cancel_keeps_result_landed_meanwhile(task_db, update_driver, hs_update, logs) -> None:
    """`--limit -R --cancel` leaves a selected task that completed before its write with its result."""
    ids = submitted(4)
    update_driver.waits(ids[0], completed_as(task_db, ids[0]))

    assert hs_update('--limit', '4', '-s', 'args', '-R', '--cancel') == cli_status.success

    after = rows(task_db)
    assert after[ids[0]]['exit_status'] == 0
    assert [after[task_id]['exit_status'] for task_id in ids[1:]] == [CANCEL_STATUS] * 3
    assert messages(logs, logging.WARNING, NOT_MATCHED)
    assert messages(logs, logging.INFO, 'Updated 3 tasks')
    assert severe(logs) == []


@mark.unit
def test_update_limit_uncontended_costs_one_update_per_row(task_db, update_driver, hs_update, logs) -> None:
    """An uncontended `--limit` update is one UPDATE per selected row, with nothing read back after them."""
    ids = submitted(4)
    update_driver.clear()

    assert hs_update('--limit', '3', '-s', 'args', '-R', 'group=7') == cli_status.success

    kinds = update_driver.kinds()
    first = kinds.index('UPDATE')
    assert kinds[first:] == ['UPDATE'] * 3
    assert [task_id for _, task_id in update_driver.executed[first:]] == ids[:3]
    assert [rows(task_db)[task_id]['group'] for task_id in ids] == [7, 7, 7, 0]
    assert messages(logs, logging.DEBUG, 'Re-running contended') == []


@mark.unit
def test_update_delete_limit_contended_lands_on_rerun(task_db, update_driver, hs_update, logs) -> None:
    """`--delete --limit`: a row whose delete missed once is re-run, and every selected row is deleted."""
    ids = submitted(4)
    update_driver.lose(ids[1], 1)

    assert hs_update('--limit', '4', '-s', 'args', '-R', '--delete') == cli_status.success

    assert rows(task_db) == {}
    assert len(any_rerun(logs, ids[1])) == 1
    assert messages(logs, logging.INFO, 'Deleted 4 tasks')
    assert severe(logs) == []


@mark.unit
def test_update_delete_limit_contended_exhausted_deletes_nothing(task_db, update_driver, hs_update, logs,
                                                                  tmp_path) -> None:
    """At the bound, `--delete --limit` deletes nothing, says the update was contended, and exits 5."""
    ids = submitted(4)
    before = rows(task_db)
    update_driver.lose(ids[2], ALWAYS)

    assert hs_update('--limit', '4', '-s', 'args', '-R', '--delete') == cli_status.runtime_error

    assert rows(task_db) == before
    assert contended_error(logs, ids[2]), severe(logs)
    assert not traceback_written(logs, tmp_path)


@mark.unit
def test_update_filtered_uncontended_is_one_statement(task_db, update_driver, hs_update, logs) -> None:
    """Without `--limit`, a field change, a tag set and a tag removal are one UPDATE of `task`, for 5 rows
    and for 30, and every row gets all three."""
    for count in (5, 30):
        ids = submitted(count, tag={'size': 'big'})
        update_driver.clear()

        assert hs_update('group=7', 'color:red', '--remove-tag', 'size', '-w', 'group==0') == cli_status.success

        assert update_driver.kinds().count('UPDATE') == 1, update_driver.kinds()
        after = rows(task_db)
        assert {task_id: (after[task_id]['group'], after[task_id]['tag']) for task_id in ids} == \
            {task_id: (7, {'color': 'red'}) for task_id in ids}
