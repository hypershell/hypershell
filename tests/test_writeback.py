# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replays of result writeback.

Apart from one command-line run, these tests drive ``Receiver.update_tasks`` in-process against a throwaway SQLite database, with
no threads, queue or sleeps. A client's copy of a task is produced by ``Task.next`` and the real
bundle codec; confirmation runs through ``Confirm.update_info``; eviction is
``Task.revert_orphaned``, exactly what the heartbeat monitor calls. Each replay places a returned
result at an exact point in a dispatch, revert, reschedule, retry or cancel sequence.
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
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from cmdkit.app import exit_status

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.client import ClientInfo
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, ResultOutcome, CANCEL_STATUS, serialize_tasks, deserialize_tasks
from hypershell.server import Confirm, Receiver
from tests import main, create_taskfile_echo, assert_output


# The columns a client produces when it runs a task, and every other column of the task table.
RESULT_COLUMNS = ('client_id', 'client_host', 'command', 'start_time', 'completion_time', 'waited', 'duration',
                  'exit_status', 'outpath', 'errpath', 'csvpath', 'cores_max', 'memory_max')
OTHER_COLUMNS = tuple(name for name in Task.columns if name not in RESULT_COLUMNS)

CLIENT_A = (uuid(), 'client-a')
CLIENT_B = (uuid(), 'client-b')
CLIENT_C = (uuid(), 'client-c')


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
    """Capture every hypershell record, debug included."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    return caplog


def submit(args: str = 'echo writeback') -> str:
    """Add one task and return its id."""
    task = Task.new(args=args)
    Task.add(task)
    return task.id


def dispatch(limit: int = 1, attempts: int = 1) -> List[Task]:
    """Schedule tasks and return the copies a client receives, through the real bundle codec."""
    return deserialize_tasks(serialize_tasks(Task.next(limit=limit, attempts=attempts)))


def dispatch_one(task_id: str, attempts: int = 1) -> Task:
    """Schedule exactly the one task `task_id`; fail the test otherwise."""
    copies = dispatch(attempts=attempts)
    if [copy.id for copy in copies] != [task_id]:
        pytest.fail(f'Expected to dispatch task ({task_id}), got {[copy.id for copy in copies]}')
    return copies[0]


def confirm(client: Tuple[str, str], ids: List[str]) -> None:
    """Confirm task ids to a client, as the server's Confirm thread does."""
    machine = Confirm(queue=None)
    machine.client_info = ClientInfo(client_id=client[0], client_host=client[1], task_ids=ids)
    machine.update_info()


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
    copy.csvpath = f'task-{copy.id}.csv'
    copy.cores_max = 1.5
    copy.memory_max = 2048.0
    return copy


def receive(copies: List[Task], failures: Optional[io.StringIO] = None) -> None:
    """Return a bundle of finished copies through the wire and the server's Receiver."""
    machine = Receiver(queue=None, redirect_failures=failures)
    machine.tasks = deserialize_tasks(serialize_tasks(copies))
    machine.update_tasks()


def row(task_id: str) -> Dict[str, Any]:
    """The task's current row, freshly read."""
    return Task.from_id(task_id, caching=False).to_dict()


def plain(value: Any) -> Any:
    """A value as SQLite returns it: datetimes as naive local wall-clock time."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def results_of(copy: Task) -> Dict[str, Any]:
    """The result columns a copy carries."""
    return {name: plain(getattr(copy, name)) for name in RESULT_COLUMNS}


def results_in(data: Dict[str, Any]) -> Dict[str, Any]:
    """The result columns of a row."""
    return {name: plain(data[name]) for name in RESULT_COLUMNS}


def others_in(data: Dict[str, Any]) -> Dict[str, Any]:
    """Every column of a row that is not a result column."""
    return {name: data[name] for name in OTHER_COLUMNS}


def messages(logs: pytest.LogCaptureFixture, level: int = None) -> List[str]:
    """Captured hypershell messages, optionally at one level."""
    return [record.getMessage() for record in logs.records
            if record.name.startswith('hypershell') and (level is None or record.levelno == level)]


def completions(logs: pytest.LogCaptureFixture, task_id: str) -> List[str]:
    """Records announcing the task as completed by the server."""
    return [message for message in messages(logs) if message == f'Completed task ({task_id})']


def nonzero(logs: pytest.LogCaptureFixture, task_id: str) -> List[str]:
    """Records reporting a non-zero exit status for the task."""
    return [message for message in messages(logs)
            if message.startswith('Non-zero exit status') and task_id in message]


def warnings_with(logs: pytest.LogCaptureFixture, word: str, *names: str) -> List[str]:
    """WARNING records containing `word` (case-insensitive) and every one of `names`."""
    return [message for message in messages(logs, logging.WARNING)
            if word in message.lower() and all(name in message for name in names)]


def retries_of(task_id: str) -> int:
    """Count of rows whose retry chain points back to `task_id`."""
    return Task.query(caching=False).filter(Task.previous_id == task_id).count()


# --- Late, duplicate and foreign results ------------------------------------------------------------

@mark.unit
def test_late_result_after_retry_keeps_chain(task_db, logs) -> None:
    """An evicted client's late failure leaves the retried row and its single retry alone."""
    task_id = submit('false')
    copy_a = dispatch_one(task_id, attempts=3)
    confirm(CLIENT_A, [task_id])
    Task.revert_orphaned(CLIENT_A[0])
    copy_b = dispatch_one(task_id, attempts=3)
    confirm(CLIENT_B, [task_id])
    receive([finish(copy_b, 1, CLIENT_B)])
    retries = dispatch(attempts=3)
    if len(retries) != 1 or retries[0].previous_id != task_id:
        pytest.fail(f'Expected one retry of task ({task_id}), got {[t.previous_id for t in retries]}')
    before = row(task_id)
    if before['retried'] is not True or before['next_id'] != retries[0].id:
        pytest.fail(f'Expected task ({task_id}) marked retried, got {before}')
    logs.clear()

    receive([finish(copy_a, 1, CLIENT_A)])

    after = row(task_id)
    for name in ('retried', 'next_id', 'previous_id', 'attempt'):
        assert after[name] == before[name], name
    assert results_in(after) == results_in(before)
    assert len(warnings_with(logs, 'stale', task_id, CLIENT_A[0])) == 1
    assert not [task for task in Task.next(limit=10, attempts=3) if task.previous_id == task_id]
    assert retries_of(task_id) == 1


@mark.unit
@mark.parametrize('case', ['reverted', 'same-client', 'other-client', 'completed-elsewhere'])
def test_stale_result_is_refused(task_db, logs, case: str) -> None:
    """A result for a reverted, rescheduled or re-completed row changes nothing and is logged."""
    task_id = submit('echo stale')
    copy_a = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    Task.revert_orphaned(CLIENT_A[0])
    if case != 'reverted':
        copy_next = dispatch_one(task_id)
        if copy_next.schedule_time == copy_a.schedule_time:
            pytest.fail('Re-dispatch reused the first schedule_time')
        confirm(CLIENT_A if case == 'same-client' else CLIENT_B, [task_id])
        if case == 'completed-elsewhere':
            receive([finish(copy_next, 0, CLIENT_B)])
            if row(task_id)['exit_status'] != 0:
                pytest.fail('Client B result was not applied')
    before = row(task_id)
    failures = io.StringIO()
    logs.clear()

    receive([finish(copy_a, 1, CLIENT_A)], failures=failures)

    assert row(task_id) == before
    assert failures.getvalue() == ''
    assert completions(logs, task_id) == []
    assert nonzero(logs, task_id) == []
    assert len(warnings_with(logs, 'stale', task_id, CLIENT_A[0])) == 1


@mark.unit
def test_cancelled_row_keeps_cancel_status(task_db, logs) -> None:
    """A result for a cancelled row keeps the cancel status and its completion time."""
    task_id = submit('echo cancel')
    copy = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    Task.cancel(task_id)
    before = row(task_id)
    if before['exit_status'] != CANCEL_STATUS:
        pytest.fail(f'Task ({task_id}) was not cancelled')

    receive([finish(copy, 0, CLIENT_A)])

    after = row(task_id)
    assert after['exit_status'] == CANCEL_STATUS
    assert after['completion_time'] == before['completion_time']
    assert len(warnings_with(logs, 'stale', task_id, CLIENT_A[0])) == 1


@mark.unit
@mark.parametrize('how', ['same-bundle', 'redelivered'])
def test_duplicate_result_is_ignored(task_db, logs, how: str) -> None:
    """A result already applied by its own assignment is ignored, distinctly from a stale one."""
    task_id = submit('echo duplicate')
    copy = finish(dispatch_one(task_id), 0, CLIENT_A)
    confirm(CLIENT_A, [task_id])
    if how == 'same-bundle':
        receive([copy, copy])
        first = results_of(copy)
        assert results_in(row(task_id)) == first
    else:
        receive([copy])
        first = row(task_id)
        receive([copy])
        assert row(task_id) == first
    assert len(completions(logs, task_id)) == 1
    assert len(warnings_with(logs, 'duplicate', task_id)) == 1
    assert warnings_with(logs, 'stale') == []


@mark.unit
@mark.parametrize('how', ['same-bundle', 'redelivered'])
def test_duplicate_result_with_cancel_status_value_is_ignored(task_db, logs, how: str) -> None:
    """A repeated result whose own run exited with the cancel status value is a duplicate, not stale.

    A task terminated by a hang-up signal completes with the same exit status a cancel stamps. A
    cancel always restamps the row's dispatch time, so a result for a cancelled row is still stale.
    """
    task_id = submit('echo hangup')
    copy = finish(dispatch_one(task_id), CANCEL_STATUS, CLIENT_A)
    confirm(CLIENT_A, [task_id])
    if how == 'same-bundle':
        receive([copy, copy])
    else:
        receive([copy])
        first = row(task_id)
        receive([copy])
        assert row(task_id) == first
    assert row(task_id)['exit_status'] == CANCEL_STATUS
    assert len(completions(logs, task_id)) == 1
    assert messages(logs, logging.WARNING).count(
        f'Ignored duplicate result for task ({task_id}) from client ({CLIENT_A[1]}: {CLIENT_A[0]})') == 1
    assert warnings_with(logs, 'stale') == []

    cancelled_id = submit('echo cancelled')
    cancelled_copy = dispatch_one(cancelled_id)
    confirm(CLIENT_B, [cancelled_id])
    Task.cancel(cancelled_id)
    before = row(cancelled_id)
    if before['schedule_time'] == plain(cancelled_copy.schedule_time):
        pytest.fail('Cancel reused the dispatched schedule_time')
    logs.clear()

    receive([finish(cancelled_copy, CANCEL_STATUS, CLIENT_B)])

    assert row(cancelled_id) == before
    assert messages(logs, logging.WARNING) == [
        f'Refused stale result for task ({cancelled_id}) from client ({CLIENT_B[1]}: {CLIENT_B[0]})']


@mark.unit
def test_non_result_columns_are_never_written(task_db, logs) -> None:
    """A returned copy differing in every non-result column changes none of them."""
    task_id = submit('echo columns')
    copy = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    before = row(task_id)
    copy.group = before['group'] + 7
    copy.args = 'echo mutated'
    copy.submit_id = uuid()
    copy.submit_time = before['submit_time'] - timedelta(days=1)
    copy.submit_host = 'other-submit-host'
    copy.cores = 2
    copy.memory = 1024
    copy.timeout = 60
    copy.server_id = uuid()
    copy.server_host = 'other-server-host'
    copy.attempt = before['attempt'] + 4
    copy.retried = not before['retried']
    copy.previous_id = uuid()
    copy.next_id = uuid()
    copy.source = uuid()
    copy.fingerprint = 'f' * 64
    copy.part = before['part'] + 3
    copy.tag = {'mutated': 'yes'}
    for name in OTHER_COLUMNS:
        if name not in ('id', 'schedule_time') and getattr(copy, name) == before[name]:
            pytest.fail(f'Copy column {name} was not mutated')

    receive([finish(copy, 0, CLIENT_A)])

    after = row(task_id)
    assert others_in(after) == others_in(before)
    assert results_in(after) == results_of(copy)


@mark.unit
def test_foreign_attribution_is_applied(task_db, logs) -> None:
    """A result returned under another client's name applies, and keeps the server's stamps."""
    task_id = submit('echo foreign')
    copy = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    before = row(task_id)
    copy.server_id = uuid()
    copy.server_host = 'relay-host'

    receive([finish(copy, 0, CLIENT_C)])

    after = row(task_id)
    assert (after['client_id'], after['client_host']) == CLIENT_C
    assert results_in(after) == results_of(copy)
    for name in ('server_id', 'server_host', 'schedule_time'):
        assert after[name] == before[name], name
    assert len(completions(logs, task_id)) == 1


# --- Behaviour that does not change -----------------------------------------------------------------

@mark.unit
@mark.parametrize('exit_status', [0, 1])
def test_result_applies_as_today(task_db, logs, exit_status: int) -> None:
    """A result for the current assignment applies every result column, with today's logging."""
    task_id = submit('echo today')
    copy = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    before = row(task_id)
    failures = io.StringIO()

    receive([finish(copy, exit_status, CLIENT_A)], failures=failures)

    after = row(task_id)
    assert results_in(after) == results_of(copy)
    assert others_in(after) == others_in(before)
    assert len(completions(logs, task_id)) == 1
    if exit_status == 0:
        assert nonzero(logs, task_id) == []
        assert failures.getvalue() == ''
    else:
        assert nonzero(logs, task_id) == [f'Non-zero exit status ({exit_status}) for task ({task_id})']
        assert failures.getvalue() == before['args'] + '\n'
    assert warnings_with(logs, 'stale') == []
    assert warnings_with(logs, 'duplicate') == []


@mark.unit
def test_no_confirm_result_applies(task_db, logs) -> None:
    """Without confirmation the server never stamped the client, and the result still applies."""
    task_id = submit('echo unconfirmed')
    copy = dispatch_one(task_id)
    if row(task_id)['client_id'] is not None:
        pytest.fail('Dispatch stamped a client before any confirmation')

    receive([finish(copy, 0, CLIENT_A)])

    after = row(task_id)
    assert results_in(after) == results_of(copy)
    assert (after['client_id'], after['client_host']) == CLIENT_A
    assert len(completions(logs, task_id)) == 1


@mark.unit
def test_in_memory_receiver_writes_nothing(task_db, logs) -> None:
    """An in-memory server logs the completion and writes nothing, even for an unknown task."""
    copy = finish(Task.new(args='echo memory'), 0, CLIENT_A)
    machine = Receiver(queue=None, in_memory=True)
    machine.tasks = [copy]
    machine.update_tasks()
    assert len(completions(logs, copy.id)) == 1
    assert Task.count() == 0


# --- The fence itself -------------------------------------------------------------------------------

@mark.unit
def test_result_columns_are_fixed() -> None:
    """Exactly the client-produced columns are result columns; every other column is not."""
    assert Task.result_columns == RESULT_COLUMNS
    assert set(Task.columns) - set(Task.result_columns) == {
        'id', 'group', 'args', 'submit_id', 'submit_time', 'submit_host', 'cores', 'memory', 'timeout',
        'server_id', 'server_host', 'schedule_time', 'attempt', 'retried', 'previous_id', 'next_id',
        'source', 'fingerprint', 'part', 'tag',
    }


@mark.unit
def test_missing_row_is_refused(task_db, logs) -> None:
    """A result for a task that is not in the database is refused and logged, not raised."""
    copy = Task.new(args='echo missing')
    copy.schedule_time = datetime.now().astimezone()

    receive([finish(copy, 0, CLIENT_A)])

    assert Task.count() == 0
    assert completions(logs, copy.id) == []
    assert len(warnings_with(logs, 'stale', copy.id, CLIENT_A[0])) == 1


@mark.unit
def test_unscheduled_token_is_refused(task_db, logs) -> None:
    """A result carrying no schedule_time never matches a reverted row."""
    task_id = submit('echo unscheduled')
    copy = dispatch_one(task_id)
    confirm(CLIENT_A, [task_id])
    Task.revert_orphaned(CLIENT_A[0])
    before = row(task_id)
    if before['schedule_time'] is not None:
        pytest.fail(f'Task ({task_id}) was not reverted')
    copy.schedule_time = None

    receive([finish(copy, 0, CLIENT_A)])

    assert row(task_id) == before
    assert completions(logs, task_id) == []
    assert len(warnings_with(logs, 'stale', task_id, CLIENT_A[0])) == 1


@mark.unit
def test_echoed_token_matches_after_round_trip(task_db) -> None:
    """The schedule_time a client echoes through the wire matches exactly; one microsecond off does not."""
    task_id = submit('echo token')
    copy = dispatch_one(task_id)
    before = row(task_id)
    shifted = deserialize_tasks(serialize_tasks([finish(copy, 0, CLIENT_A)]))[0]
    shifted.schedule_time = shifted.schedule_time + timedelta(microseconds=1)

    assert Task.apply_results([shifted]) == [ResultOutcome.STALE]
    assert row(task_id) == before

    echoed = deserialize_tasks(serialize_tasks([copy]))[0]
    assert Task.apply_results([echoed]) == [ResultOutcome.APPLIED]
    assert results_in(row(task_id)) == results_of(copy)


@mark.integration
def test_cluster_no_confirm_with_db(temp_site: Path) -> None:
    """A database-backed cluster without confirmation applies and attributes every result."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'cluster', taskfile, '--no-confirm'])
    assert rc == exit_status.success
    assert stdout == '\n'.join(map(str, range(4)))
    assert_output(r'\[hypershell.server\] Completed task', stderr, 4)
    assert 'stale result' not in stderr
    assert 'duplicate result' not in stderr
    assert main(['hs', 'list', '-c']) == (exit_status.success, '4', '')
    assert main(['hs', 'list', '-c', '-S']) == (exit_status.success, '4', '')
    assert main(['hs', 'list', '-c', '-w', 'client_id == null']) == (exit_status.success, '0', '')
