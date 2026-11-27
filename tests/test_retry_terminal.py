# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A task that never ran (-1001, the template did not expand; -1002, it needs more than its client has)
is never retried, and its group is failed at once, as a failure with no retries left.

The scheduler replays reuse the deterministic helpers of `tests/test_retry_gate.py`; the PostgreSQL
cases reuse the `pg_engine` fixture of `tests/test_zone_postgres.py` (set `HS_TEST_POSTGRES`).
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List

# Standard libs
import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy.engine import Engine
from cmdkit.app import exit_status as cli_status

# Internal libs
from hypershell.data.model import Task
from hypershell.server import SchedulerState
from tests import main, create_taskfile
from tests.test_retry_gate import (task_db, fail, succeed, submit, make_scheduler, next_bundle, expect_one,
                                   group_messages, scheduled_in_group)
from tests.test_zone_postgres import ADMIN_URL, pg_engine


NEVER_RAN: List[int] = [-1001, -1002]


def rows_in_group(group: int) -> int:
    """Count of every row in `group`, retries included."""
    return Task.query(caching=False).filter(Task.group == group).count()


# --- The scheduler: no retry, and the group halts on the first attempt ----------------------------

@mark.unit
@mark.parametrize('status', NEVER_RAN)
@mark.parametrize('attempts', [2, 3])
@mark.parametrize('eager', [False, True])
@mark.parametrize('forever_mode', [False, True])
def test_never_ran_halts_group(task_db: Engine, caplog: pytest.LogCaptureFixture, status: int, attempts: int,
                               eager: bool, forever_mode: bool) -> None:
    """A never-ran status with retries left is not retried: the run stops, or waits in forever mode."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    submit(0, 1)
    scheduler = make_scheduler(attempts=attempts, eager=eager, forever_mode=forever_mode)
    fail(expect_one(next_bundle(scheduler), group=0, attempt=1).id, exit_status=status)

    caplog.clear()
    state = scheduler.load_bundle()
    if forever_mode:
        assert state is SchedulerState.LOAD
        assert scheduler.current_group == 0
    else:
        assert state is SchedulerState.FINAL
        failed = [message for message in group_messages(caplog) if message.startswith('Failed task group 0')]
        assert failed and 'did not run' in failed[0]
        assert 'exceeding allowed retries' not in failed[0]
    assert rows_in_group(0) == 1
    assert scheduled_in_group(1) == 0


@mark.unit
@mark.parametrize('status', NEVER_RAN)
def test_revert_clears_never_ran_hold(task_db: Engine, status: int) -> None:
    """In forever mode the group waits for the task; reverted, the same row runs and the group advances."""
    submit(0, 1)
    scheduler = make_scheduler(attempts=3, forever_mode=True)
    first = expect_one(next_bundle(scheduler), group=0, attempt=1)
    fail(first.id, exit_status=status)
    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert scheduler.current_group == 0 and rows_in_group(0) == 1

    Task.revert(first.id)
    again = expect_one(next_bundle(scheduler), group=0, attempt=1)
    assert again.id == first.id
    succeed(again.id)
    assert scheduler.load_bundle() is SchedulerState.LOAD
    assert scheduler.current_group == 1


# --- The model: retry selection, the demand counts and the gate agree -----------------------------

def add_finished(*statuses: int, attempt: int = 1, group: int = 0) -> List[str]:
    """Add one finished row per status in `group`; return their ids."""
    now = datetime.now().astimezone()
    tasks = [Task.new(args=f'echo {n}', group=group, attempt=attempt, exit_status=status,
                      schedule_time=now, completion_time=now) for n, status in enumerate(statuses)]
    Task.add_all(tasks)
    return [task.id for task in tasks]


def check_never_ran_not_retryable(eager: bool) -> None:
    """Shared body for SQLite and PostgreSQL: nothing retried, nothing counted, the gate halts."""
    ids = add_finished(*NEVER_RAN)
    assert Task.count_retryable(3, 0, eager=eager) == 0
    assert Task.count_retryable_by_zone(3, 0, eager=eager) == {}
    retries = Task.next(limit=10, group=0, attempts=3, eager=eager)
    assert [task.previous_id for task in retries if task.previous_id in ids] == []
    info = Task.increment_group(0, attempts=3, eager=eager)
    assert info.viable is False
    assert 'did not run' in info.reason
    assert Task.starting_group(0, attempts=3, eager=eager) == 0  # A halted group is where a run starts.


@mark.unit
@mark.parametrize('eager', [False, True])
def test_never_ran_not_retryable(task_db: Engine, eager: bool) -> None:
    """A never-ran row is not retryable, not counted as demand, and fails its group at once."""
    check_never_ran_not_retryable(eager)


@mark.integration
@mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests')
@mark.parametrize('eager', [False, True])
def test_never_ran_not_retryable_postgres(pg_engine: Engine, eager: bool) -> None:
    """The same on PostgreSQL."""
    check_never_ran_not_retryable(eager)


@mark.unit
def test_halt_message_names_both_kinds(task_db: Engine) -> None:
    """A group with an exhausted failure and a never-ran task names both in its halt message."""
    add_finished(1, attempt=3)
    add_finished(-1001)
    info = Task.increment_group(0, attempts=3)
    assert info.viable is False
    assert 'exceeding allowed retries' in info.reason and 'did not run' in info.reason


@mark.unit
def test_exhausted_message_unchanged(task_db: Engine) -> None:
    """An exhausted failure alone keeps today's halt message (guard)."""
    add_finished(1, attempt=3)
    info = Task.increment_group(0, attempts=3)
    assert info.viable is False
    assert info.reason.startswith('at least 1 tasks exceeding allowed retries, example: ')


@mark.unit
@mark.parametrize('status', [1, 2, 127, -2, -9, -15, -1003])
def test_other_failures_still_retried(task_db: Engine, status: int) -> None:
    """Every other non-zero status, signal deaths and other statuses below -1000 included, is retried (guard)."""
    (task_id, ) = add_finished(status)
    assert Task.count_retryable(3, 0) == 1
    assert [task.previous_id for task in Task.next(limit=10, group=0, attempts=3)] == [task_id]


# --- End to end: hs cluster with retries ----------------------------------------------------------

def rows_tagged(n: int) -> List[tuple]:
    """Attempt and exit status of every row tagged n:`n`, read from the database file."""
    conn = sqlite3.connect(f'file:{os.environ["HYPERSHELL_DATABASE_FILE"]}?mode=ro', uri=True)
    try:
        found = conn.execute('SELECT attempt, exit_status, tag FROM task ORDER BY attempt').fetchall()
    finally:
        conn.close()
    return [(attempt, status) for attempt, status, tag in found if f'"n": {n}' in tag or f'"n":{n}' in tag]


@mark.integration
@mark.parametrize('case', ['template', 'resources'])
def test_cluster_does_not_retry_never_ran(temp_site: Path, case: str) -> None:
    """With retries, a task that never ran has one row, its group halts, and the client says it did not run."""
    if case == 'template':
        lines = ['echo 1  # HYPERSHELL: n:1 group:1', 'echo 2  # HYPERSHELL: n:2 group:2']
        options = ['-t', 'echo {nope}']
        expected = -1001
    else:
        lines = ['echo 1  # HYPERSHELL: n:1 group:1 cores:32767', 'echo 2  # HYPERSHELL: n:2 group:2']
        options = []
        expected = -1002
    taskfile = create_taskfile(temp_site, lines)
    rc, stdout, stderr = main(['hs', 'cluster', str(taskfile), '-r', '2', *options])
    assert rc == cli_status.success, stderr
    assert rows_tagged(1) == [(1, expected)]
    assert rows_tagged(2) == [] or all(status is None for _, status in rows_tagged(2))
    assert 'Failed task group 1' in stderr
    assert 'did not run' in stderr
    assert '(cancelled)' not in stderr
