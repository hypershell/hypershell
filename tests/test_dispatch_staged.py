# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Rows changed by hand while staged: cancel, delete and revert, and live submission to a database server.

A staged row is stamped in the database and held in its zone's pool, not yet delivered. When the
zone's last client leaves, or the server ends, the rows still staged go back to unscheduled; a
row cancelled meanwhile must stay cancelled, a deleted row must be skipped, and a row reverted by
hand and staged again must be delivered with its current stamp.

Every process runs in its own process group with a bound, and the group is killed when the test ends.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Dict, List, Optional, Any

# Standard libs
import os
import json
import time
import signal
import secrets
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

# External libs
from pytest import mark

# Internal libs
from hypershell.core.dispatch import Dispatcher
from hypershell.data.model import Task, DEFAULT_ZONE_ID
from tests import create_taskfile
from tests.test_dispatch_workload import Group, group, wait_listening, task_rows, received, RUNNING  # noqa: F401
from tests.test_zone_model import task_db  # noqa: F401


DEADLINE = 90
CANCEL_STATUS = -1


def hs(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    """Run a short `hs` command in its own process group, killed if it outlives `timeout`."""
    proc = subprocess.Popen(['hs', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        raise AssertionError(f'hs {" ".join(args)} did not finish within {timeout} s: {stderr}')
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def finished(proc: subprocess.Popen, deadline: float) -> Optional[int]:
    """Exit status of `proc`, or None if it is still running at `deadline`."""
    try:
        return proc.wait(timeout=max(1.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return None


def rows_by_id() -> Dict[str, Dict[str, Any]]:
    """Every task row, by id."""
    return {row['id']: row for row in task_rows()}


def staged_ids(where: str = 'TRUE') -> List[str]:
    """Rows stamped but not confirmed by any client and not complete: staged, not delivered."""
    with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
        return [row[0] for row in db.execute('SELECT id FROM task WHERE schedule_time IS NOT NULL '
                                             'AND client_id IS NULL AND completion_time IS NULL '
                                             f'AND {where} ORDER BY submit_time')]


def until(check: Callable[[], Any], what: str, timeout: float = 30) -> Any:
    """Poll `check` until it returns something true; fail with `what` after `timeout`."""
    deadline = time.monotonic() + timeout
    while not (value := check()):
        assert time.monotonic() < deadline, f'timed out waiting for {what}'
        time.sleep(0.1)
    return value


def submit_one(*args: str) -> str:
    """Submit one task with `hs task submit`, return its id."""
    result = hs('task', 'submit', *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def endpoint() -> List[str]:
    """Address for one server and its clients; a fresh key goes in the environment they inherit."""
    os.environ['HYPERSHELL_SERVER_AUTH'] = secrets.token_hex(16)
    return ['-H', 'localhost', '-p', os.environ['HYPERSHELL_SERVER_PORT']]


@mark.unit
def test_stage_replaces_a_staged_record() -> None:
    """Staging an id already in the pool replaces its record in place; the pool keeps one copy, in order."""
    dispatcher = Dispatcher(bundlesize=1)
    assert dispatcher.stage(DEFAULT_ZONE_ID, [('t1', '{"v": "old"}'), ('t2', '{"v": "t2"}')]) == 2
    dispatcher.stage(DEFAULT_ZONE_ID, [('t1', '{"v": "new"}')])
    assert dispatcher.staged(DEFAULT_ZONE_ID) == 2
    reply = json.loads(dispatcher.request(json.dumps({'protocol': 1, 'client': 'c1', 'size': 5, 'wait': 0})))
    assert reply['tasks'] == [{'v': 'new'}, {'v': 't2'}]


@mark.unit
def test_revert_staged_leaves_terminal_and_missing_rows(task_db) -> None:  # noqa: F811 (fixture)
    """Only staged rows still incomplete go back to new; cancelled, completed and deleted rows are left alone.

    More ids are given than one statement binds, so the reverts span several statements.
    """
    tasks = [Task.new(args=f'echo {n}') for n in range(1203)]
    Task.add_all(tasks)
    ids = [task.id for task in tasks]
    now = datetime.now().astimezone()
    Task.update_all([{'id': task_id, 'schedule_time': now, 'server_id': 'server'} for task_id in ids])
    cancelled, completed, deleted = ids[0], ids[700], ids[1202]
    Task.cancel(cancelled)
    Task.update(completed, exit_status=0, completion_time=now)
    Task.delete(Task.from_id(deleted, caching=False))
    assert Task.revert_staged(ids) == len(ids) - 3
    rows = {task.id: task for task in Task.query(caching=False).all()}
    assert deleted not in rows and len(rows) == len(ids) - 1
    assert rows[cancelled].exit_status == -1 and rows[cancelled].completion_time is not None
    assert rows[completed].exit_status == 0 and rows[completed].completion_time is not None
    reverted = [task for task_id, task in rows.items() if task_id not in (cancelled, completed)]
    assert all(task.schedule_time is None and task.server_id is None for task in reverted)
    assert Task.revert_staged([]) == 0


@mark.integration
@mark.parametrize('action', ['cancel', 'delete'])
def test_staged_rows_changed_before_shutdown(group: Group, action: str) -> None:
    """Default-zone rows staged with no default client are cancelled or deleted; then the server ends.

    The server reverts what is still staged once its clients are gone. A cancelled row keeps its
    cancel and a deleted row is skipped; the server exits 0 and the zoned task completes.
    """
    address = endpoint()
    gpu = submit_one('--zone', 'gpu', '--', 'sleep', '8')
    defaults = [submit_one('--', 'echo', f'd{n}') for n in range(3)]
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', *address)
    wait_listening(server, int(address[3]), group, 'server')
    client = group.start('client', 'client', '--zone', 'gpu', '-N1', *address)
    staged = until(lambda: staged_ids('zone IS NULL'), 'default-zone rows to be staged')
    result = hs('update', '--remaining', f'--{action}', '--no-confirm', '-w', 'zone == null')
    assert result.returncode == 0, result.stderr
    assert server.poll() is None and rows_by_id()[gpu]['completion_time'] is None, 'run ended before the change'
    assert finished(server, deadline) == 0, group.text('server')
    assert finished(client, deadline) == 0, group.text('client')
    assert 'staged tasks never delivered' in group.text('server')
    assert 'StaleDataError' not in group.text('server')
    rows = rows_by_id()
    assert rows[gpu]['exit_status'] == 0
    if action == 'cancel':
        for task_id in defaults:
            assert rows[task_id]['exit_status'] == CANCEL_STATUS, (task_id in staged, rows[task_id])
            assert rows[task_id]['completion_time'] is not None
    else:
        assert not set(defaults) & set(rows)
    assert not set(defaults) & set(RUNNING.findall(group.text('client')))


@mark.integration
@mark.parametrize('action', ['cancel', 'delete'])
def test_staged_rows_changed_before_zone_empties(group: Group, action: str) -> None:
    """Rows staged for a zone are cancelled or deleted; then the zone's last client leaves.

    The scheduler reverts the zone's staged rows when its last client leaves. A cancelled row keeps
    its cancel and never runs, even when another client of the zone arrives; a deleted row is
    skipped, the scheduler keeps running, and every other task completes.
    """
    address = endpoint()
    taskfile = create_taskfile(group.site, ['sleep 15'] + [f'echo g{n}' for n in range(1, 9)])
    submit = group.start('submit', 'submit', '--zone', 'gpu', str(taskfile))
    assert finished(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    first, = [row['id'] for row in task_rows() if row['args'] == 'sleep 15']
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', *address)
    wait_listening(server, int(address[3]), group, 'server')
    leaving = group.start('client-a', 'client', '--zone', 'gpu', '-b', '1', '-N1', *address)
    until(lambda: first in RUNNING.findall(group.text('client-a')), 'the first client to start its long task')
    time.sleep(2)  # it takes what its local queue holds, then waits on the long task
    delivered = sum(received(group.text('client-a')))
    until(lambda: sum(1 for row in task_rows() if row['client_id']) == delivered, 'confirmations to land')
    staged = staged_ids()
    assert staged and sum(received(group.text('client-a'))) == delivered
    result = hs('update', '--remaining', f'--{action}', '--no-confirm',
                '-w', 'zone != null', 'schedule_time != null', 'client_id == null')
    assert result.returncode == 0, result.stderr
    rows = rows_by_id()
    changed = [task_id for task_id in staged if task_id not in rows or rows[task_id]['exit_status'] == CANCEL_STATUS]
    assert changed == staged, (changed, staged)
    leaving.send_signal(signal.SIGUSR1)  # finish what it holds, then leave
    assert rows_by_id()[first]['completion_time'] is None, 'the long task ended before the client was told to leave'
    assert finished(leaving, deadline) == 0, group.text('client-a')
    until(lambda: 'staged tasks (zone has no client' in group.text('server') or server.poll() is not None,
          'the zone\'s staged rows to be released')
    assert server.poll() is None, group.text('server')
    arriving = group.start('client-b', 'client', '--zone', 'gpu', '-N1', *address)
    assert finished(server, deadline) == 0, group.text('server')
    assert finished(arriving, deadline) == 0, group.text('client-b')
    assert 'StaleDataError' not in group.text('server')
    rows = rows_by_id()
    ran = set(RUNNING.findall(group.text('client-a'))) | set(RUNNING.findall(group.text('client-b')))
    assert not set(staged) & ran
    for task_id in staged:
        if action == 'cancel':
            assert rows[task_id]['exit_status'] == CANCEL_STATUS and rows[task_id]['completion_time'] is not None
        else:
            assert task_id not in rows
    others = [row for task_id, row in rows.items() if task_id not in staged]
    assert len(others) == 9 - len(staged)
    assert all(row['exit_status'] == 0 and row['completion_time'] is not None for row in others), others


@mark.integration
def test_reverted_staged_row_runs_with_its_new_stamp(group: Group) -> None:
    """A staged row reverted by hand is staged again with its new stamp, delivered once, and completes.

    Every row is staged before any client connects (the pool is deep enough), so the scheduler
    selects the reverted row again while the pool still holds its earlier record.
    """
    address = endpoint()
    taskfile = create_taskfile(group.site, [f'echo d{n}' for n in range(4)])
    submit = group.start('submit', 'submit', str(taskfile))
    assert finished(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', '-Q', '1', *address,
                         env={'HYPERSHELL_SERVER_QUEUESIZE': '10'})
    wait_listening(server, int(address[3]), group, 'server')
    staged = until(lambda: len(ids := staged_ids()) == 4 and ids, 'every row to be staged')
    target = staged[1]
    before = rows_by_id()[target]['schedule_time']
    result = hs('update', '-w', f'id == {target}', '--revert', '--no-confirm')
    assert result.returncode == 0, result.stderr
    until(lambda: (stamp := rows_by_id()[target]['schedule_time']) is not None and stamp != before,
          'the reverted row to be staged again')
    client = group.start('client', 'client', '-N1', *address)
    status = finished(server, deadline)
    assert status == 0, (status, group.text('server')[-3000:])
    assert finished(client, deadline) == 0, group.text('client')
    assert 'Refused stale result' not in group.text('server')
    rows = rows_by_id()
    assert len(rows) == 4
    assert all(row['exit_status'] == 0 and row['completion_time'] is not None for row in rows.values()), rows
    assert RUNNING.findall(group.text('client')).count(target) == 1


@mark.integration
def test_queue_submit_refused_by_database_server(group: Group) -> None:
    """`hs submit -q` against a server with a database exits non-zero at once, naming the reason.

    Nothing drains the live queue of a server with a database, so a submission there would wait
    forever. The server keeps serving.
    """
    address = endpoint()
    server = group.start('server', 'server', '--forever', '--initdb', *address)
    wait_listening(server, int(address[3]), group, 'server')
    taskfile = create_taskfile(group.site, [f'echo q{n}' for n in range(5)])
    start = time.monotonic()
    submit = group.start('submit', 'submit', '-q', str(taskfile), '-b', '1', '-w', '1', *address)
    status = finished(submit, start + 30)
    assert status is not None, 'hs submit -q hung against a server with a database'
    assert status != 0
    assert time.monotonic() - start < 20
    text = group.text('submit')
    assert 'database' in text and '--no-db' in text, text
    assert server.poll() is None, group.text('server')
    assert task_rows() == []
