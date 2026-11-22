# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A retry keeps its task's declared cores, memory and walltime, as it keeps its command, tags, group and zone.

The PostgreSQL case reuses the `pg_engine` fixture of `tests/test_zone_postgres.py` (set `HS_TEST_POSTGRES`).
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Tuple

# Standard libs
import os
import sqlite3
from pathlib import Path

# External libs
from pytest import mark
from sqlalchemy.engine import Engine
from cmdkit.app import exit_status as cli_status

# Internal libs
from hypershell.data.model import Task
from tests import main
from tests.test_retry_gate import task_db, fail
from tests.test_zone_postgres import ADMIN_URL, pg_engine


GIB: int = 2 ** 30


def declarations(task_id: str) -> Tuple:
    """The stored cores, memory and timeout of one row, read fresh."""
    task = Task.query(caching=False).filter(Task.id == task_id).one()
    return task.cores, task.memory, task.timeout


def check_retry_keeps_declarations(eager: bool) -> None:
    """Shared body for SQLite and PostgreSQL."""
    declared = Task.new(args='sleep 6', cores=2, memory=GIB, timeout=2)
    plain = Task.new(args='echo plain')
    Task.add_all([declared, plain])
    first = {task.id for task in Task.next(limit=10, attempts=2, eager=eager)}
    assert first == {declared.id, plain.id}
    for task_id in first:
        fail(task_id, exit_status=-2)
    # Eager selection can list a new retry row twice, so retries are taken by identity.
    retries: Dict[str, Task] = {task.previous_id: task for task in Task.next(limit=10, attempts=2, eager=eager)}
    assert set(retries) == {declared.id, plain.id}
    assert declarations(retries[declared.id].id) == (2, GIB, 2)
    assert declarations(retries[plain.id].id) == (None, None, None)


@mark.unit
@mark.parametrize('eager', [False, True])
def test_retry_keeps_declarations(task_db: Engine, eager: bool) -> None:
    """The retry row carries cores, memory and timeout; a task that declared none gets none."""
    check_retry_keeps_declarations(eager)


@mark.integration
@mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests')
@mark.parametrize('eager', [False, True])
def test_retry_keeps_declarations_postgres(pg_engine: Engine, eager: bool) -> None:
    """The same on PostgreSQL."""
    check_retry_keeps_declarations(eager)


@mark.unit
def test_retry_reads_columns_not_tags(task_db: Engine) -> None:
    """A tag named like a declaration (written by `hs update KEY:VALUE`) stays a tag; the retry reads the columns."""
    task = Task.new(args='echo tagged', cores=1)
    task.tag = {'cores': '8', 'memory': '2GB', 'timeout': '60', 'k': 'v'}
    Task.add(task)
    (scheduled, ) = Task.next(limit=10, attempts=2)
    fail(scheduled.id, exit_status=1)
    (retry, ) = Task.next(limit=10, attempts=2)
    assert declarations(retry.id) == (1, None, None)
    stored = Task.query(caching=False).filter(Task.id == retry.id).one()
    assert stored.tag == {'cores': '8', 'memory': '2GB', 'timeout': '60', 'k': 'v'}


@mark.integration
@mark.skipif(not ADMIN_URL, reason='set HS_TEST_POSTGRES to a PostgreSQL admin URL to run these tests')
def test_retry_text_cores_tag_postgres(pg_engine: Engine) -> None:
    """On PostgreSQL, a `cores:x` tag (as `hs update cores:x` writes it) never reaches the retry's integer column.

    The retry row is inserted with the stored columns and the tag kept whole; a scheduler that read
    the tag into the column failed the insert, and the run.
    """
    task = Task.new(args='echo tagged', cores=1)
    task.tag = {'cores': 'x', 'k': 'v'}
    Task.add(task)
    (scheduled, ) = Task.next(limit=10, attempts=2)
    fail(scheduled.id, exit_status=1)
    (retry, ) = Task.next(limit=10, attempts=2)
    assert declarations(retry.id) == (1, None, None)
    stored = Task.query(caching=False).filter(Task.id == retry.id).one()
    assert stored.tag == {'cores': 'x', 'k': 'v'} and stored.previous_id == task.id


def stored_rows() -> List[tuple]:
    """Attempt, exit status, duration, cores, memory, timeout and previous id of every row, by attempt."""
    conn = sqlite3.connect(f'file:{os.environ["HYPERSHELL_DATABASE_FILE"]}?mode=ro', uri=True)
    try:
        return conn.execute('SELECT attempt, exit_status, duration, cores, memory, timeout, previous_id, id '
                            'FROM task ORDER BY attempt').fetchall()
    finally:
        conn.close()


@mark.integration
def test_cluster_retry_keeps_walltime(temp_site: Path) -> None:
    """A task submitted with -c, -m and -W is killed at its walltime on its retry too (the client sets none)."""
    rc, _, stderr = main(['hs', 'submit', '-c', '1', '-m', '1MB', '-W', '2', 'sleep', '6'])
    assert rc == cli_status.success, stderr
    rc, _, stderr = main(['hs', 'cluster', '--restart', '-r', '1', '-N1'])
    assert rc == cli_status.success, stderr
    first, retry = stored_rows()
    assert retry[6] == first[7]
    assert first[1] < 0 and first[1] not in (-1001, -1002)
    assert retry[1] < 0 and retry[1] not in (-1001, -1002)
    assert retry[2] < 6
    assert retry[3:6] == first[3:6] and first[3] == 1 and first[5] == 2


@mark.unit
def test_retry_columns_are_fixed() -> None:
    """Every column is copied to a retry, written by a result, or fresh on the retry: exactly one of them."""
    fresh = {'id', 'submit_id', 'submit_host', 'submit_time', 'attempt', 'retried', 'previous_id', 'next_id',
             'server_id', 'server_host', 'schedule_time', 'part'}
    retry, result = set(Task.retry_columns), set(Task.result_columns)
    assert len(retry) == len(Task.retry_columns)
    assert not retry & result and not retry & fresh and not result & fresh
    assert retry | result | fresh == set(Task.columns)
    assert {'cores', 'memory', 'timeout'} <= retry
