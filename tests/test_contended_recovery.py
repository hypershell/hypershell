# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Recovering a task-row write that a row lock made miss, by fault injection on SQLite.

On a TimescaleDB 2.29+ hypertable, an UPDATE that waited on another transaction's row lock can
match 0 rows although the row exists and still qualifies. SQLite never does that, so these tests
inject it: a wrapper around the session's ``execute`` makes the next *k* UPDATEs of a chosen task
report 0 matched rows without running, optionally committing another writer's change to the row
first, as the transaction it waited on would. Every recovery path is driven in-process, as the
server drives it (``Confirm.update_info``, ``Receiver.update_tasks``, ``Task.revert_orphaned``,
``Task.revert_staged``), and the wrapper's record of executed statements pins what an uncontended
write costs. The real race, on a
server database, is ``tests/test_contended_write.py``.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from pathlib import Path

# Standard libs
import io
import logging
from datetime import datetime, timedelta
from queue import Empty as QueueEmpty

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text, update, delete
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
        task_id = params.get('r_id') if isinstance(params, dict) else None
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
