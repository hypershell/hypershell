# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A task can declare 2 GiB of memory or more on every database (issues/memory-column-overflows-postgres.md).

`task.memory` is a byte count. SQLite's `INTEGER` holds 64 bits; on PostgreSQL the column is
`BIGINT`, and the in-place schema upgrade widens a 32-bit column left by an earlier release. On a
TimescaleDB hypertable it widens where TimescaleDB allows it and otherwise refuses, schema
unchanged. The PostgreSQL and TimescaleDB cases live in `tests/test_upgrade_postgres.py` and
`tests/test_timescale.py`, which reuse `check_large_memory` from here.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

# Standard libs
import os
import re
from pathlib import Path
from subprocess import run, PIPE

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from sqlalchemy.dialects import sqlite, postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable
from sqlalchemy.types import BIGINT, INTEGER, NUMERIC, SMALLINT

# Internal libs
from hypershell.core.exceptions import SchemaUpgradeError
from hypershell.data import upgrade
from hypershell.data.model import Task
from tests import create_taskfile


FOUR_GB: str = str(4 * 2 ** 30)
COMMAND_TIMEOUT: float = 180  # A refused insert can hang a submit; fail instead of waiting forever.

WIDEN_SQL: str = 'ALTER TABLE task ALTER COLUMN memory TYPE BIGINT'
NOTICE: str = 'Rewriting table task to widen task.memory to BIGINT (this can take a while on a large table)'
MESSAGE: str = 'Widened column task.memory to BIGINT'


def command(argv: List[str]) -> Tuple[int, List[str], List[str]]:
    """Exit status, stdout lines, and stderr lines of the command line, bounded by `COMMAND_TIMEOUT`."""
    proc = run(argv, stdout=PIPE, stderr=PIPE, env=os.environ, timeout=COMMAND_TIMEOUT)
    return (proc.returncode,
            [line.strip() for line in proc.stdout.decode('utf-8').strip().split('\n')],
            [line.strip() for line in proc.stderr.decode('utf-8').strip().split('\n')])


def listed(*fields: str) -> List[List[str]]:
    """Every task's `fields`, as `hs list` prints them (tab-separated)."""
    rc, stdout, stderr = command(['hs', 'list', *fields, '--all'])
    assert rc == exit_status.success, stderr
    return sorted(line.split('\t') for line in stdout if line)


def check_large_memory(site: Path) -> None:
    """
    Tasks declaring 4 GB, on the command line and inline in a task file, are stored, shown, run,
    and retried with that byte count, on whichever database the environment points at.
    """
    rc, _, stderr = command(['hs', 'submit', '-m', '4GB', '--', 'echo', 'big'])
    assert rc == exit_status.success, stderr
    taskfile = create_taskfile(site, ['echo inline  # HYPERSHELL: memory:4GB'], filename='memory.in')
    rc, _, stderr = command(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.success, stderr
    rc, _, stderr = command(['hs', 'submit', '-m', '4GB', '--', 'sh', '-c', 'exit 3'])
    assert rc == exit_status.success, stderr

    ids: Dict[str, str] = {args: id for args, id in listed('args', 'id')}
    assert set(ids) == {'echo big', 'echo inline', 'sh -c "exit 3"'}
    assert command(['hs', 'list', 'memory', '--all'])[:2] == (exit_status.success, [FOUR_GB] * 3)
    for args in ('echo big', 'echo inline'):
        assert command(['hs', 'info', ids[args], '-x', 'memory'])[:2] == (exit_status.success, [FOUR_GB])

    # The client is given 8 GB, so a 4 GB task fits wherever the suite runs.
    rc, stdout, stderr = command(['hs', 'cluster', '--restart', '-N1', '-M', '8GB', '-r', '2', '--no-tls'])
    assert rc == exit_status.success, stderr
    assert sorted(stdout) == ['big', 'inline']
    assert listed('args', 'attempt', 'exit_status', 'memory') == [
        ['echo big', '1', '0', FOUR_GB],
        ['echo inline', '1', '0', FOUR_GB],
        ['sh -c "exit 3"', '1', '3', FOUR_GB],
        ['sh -c "exit 3"', '2', '3', FOUR_GB],
        ['sh -c "exit 3"', '3', '3', FOUR_GB],
    ]


# --------------------------------------------------------------------------------------------------
# The column as the models declare it


def task_ddl(dialect: object) -> str:
    """The CREATE TABLE statement for `task` in one dialect."""
    return str(CreateTable(Task.__table__).compile(dialect=dialect))


@mark.unit
def test_sqlite_column_unchanged() -> None:
    """SQLite creates `memory INTEGER`, as before; its INTEGER already holds 64 bits."""
    assert re.search(r'\bmemory INTEGER\b', task_ddl(sqlite.dialect()))


@mark.unit
def test_postgres_column_bigint() -> None:
    """PostgreSQL creates `memory BIGINT`."""
    assert re.search(r'\bmemory BIGINT\b', task_ddl(postgresql.dialect()))


# --------------------------------------------------------------------------------------------------
# The widening decision, by reflected type and by the hypertable's columnstore


@mark.unit
@mark.parametrize('reflected, due', [
    pytest.param(INTEGER(), True, id='integer'),
    pytest.param(SMALLINT(), True, id='smallint'),
    pytest.param(BIGINT(), False, id='bigint'),
    pytest.param(NUMERIC(), False, id='numeric'),
])
def test_widening_due(reflected: object, due: bool) -> None:
    """Only a narrower integer is widened; BIGINT, or a type changed by hand, is left as it is."""
    assert upgrade._is_narrow_integer(reflected) is due


def widen(state: Optional[tuple]) -> List[Tuple[str, str, Optional[str], str]]:
    """The steps planned for `task.memory` under one hypertable state, as plain tuples."""
    table = Task.__table__
    return [(step.table, step.sql, step.notice, step.message) for step in upgrade._widen(table, table.c.memory, state)]


@mark.unit
@mark.parametrize('state', [
    pytest.param(None, id='plain'),
    pytest.param(((2, 31, 0), False, 0), id='columnstore-off'),
    pytest.param(((2, 22, 0), False, 0), id='columnstore-off-old-release'),
    pytest.param(((2, 24, 0), True, 0), id='columnstore-on-2.24.0'),
    pytest.param(((2, 31, 0), True, 0), id='columnstore-on-2.31.0'),
])
def test_widened_directly(state: Optional[tuple]) -> None:
    """One ALTER, announced before it runs and logged after; never a TimescaleDB statement."""
    steps = widen(state)
    assert steps == [('task', WIDEN_SQL, NOTICE, MESSAGE)]
    assert not any('timescaledb' in sql.lower() or 'columnstore' in sql.lower() for _, sql, _, _ in steps)


@mark.unit
@mark.parametrize('state, version', [
    pytest.param(((2, 22, 0), True, 0), '2.22.0', id='2.22.0'),
    pytest.param(((2, 23, 1), True, 0), '2.23.1', id='2.23.1'),
])
def test_old_release_refused(state: tuple, version: str) -> None:
    """With the columnstore on before 2.24.0, the step refuses, naming the update and the manual steps."""
    with pytest.raises(SchemaUpgradeError) as caught:
        widen(state)
    message = str(caught.value)
    for token in ('task.memory', f'TimescaleDB {version}', '2.24.0', 'ALTER EXTENSION timescaledb UPDATE',
                  'turn the columnstore off on task, widen memory to BIGINT', 'columnstore back on', 'hs initdb',
                  'TimescaleDB section of the database documentation', 'The schema is unchanged.'):
        assert token in message, message


@mark.unit
@mark.parametrize('state, count', [
    pytest.param(((2, 31, 0), True, 3), 3, id='2.31.0'),
    pytest.param(((2, 23, 0), True, 1), 1, id='2.23.0'),
])
def test_compressed_refused(state: tuple, count: int) -> None:
    """Any compressed chunk is refused first, on any release, naming the chunks and how to convert them back."""
    with pytest.raises(SchemaUpgradeError) as caught:
        widen(state)
    message = str(caught.value)
    assert 'TimescaleDB 2.24.0' not in message, message
    for token in ('task.memory', f'{count} compressed chunk', 'decompress_chunk', 'show_chunks',
                  'The schema is unchanged.'):
        assert token in message, message


class CompressedChunks(Exception):
    """TimescaleDB's refusal of a type change on a hypertable with compressed chunks, as the driver raises it."""
    sqlstate = '0A000'

    def __str__(self) -> str:
        return 'operation not supported on hypertables with compressed chunks'


@mark.unit
def test_compressed_during_upgrade() -> None:
    """A chunk compressed between the plan and the statement gives the same actionable refusal."""
    message = str(upgrade._wrap(DBAPIError('ALTER TABLE task ALTER COLUMN memory TYPE BIGINT', None,
                                           CompressedChunks())))
    for token in ('task.memory', 'compressed', 'decompress_chunk', 'show_chunks', 'The schema is unchanged.'):
        assert token in message, message


# --------------------------------------------------------------------------------------------------
# A 4 GB task on SQLite


@mark.integration
def test_large_memory_sqlite(temp_site: Path) -> None:
    """On SQLite a 4 GB task is stored, shown, run, and retried with its byte count."""
    check_large_memory(temp_site)
