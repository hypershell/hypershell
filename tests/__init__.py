# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for HyperShell."""


# Type annotations
from __future__ import annotations
from typing import Final, List, Tuple, Dict, Any

# Standard libs
import os
import re
import json
import shutil
import sqlite3
from pathlib import Path
from subprocess import run, PIPE

# External libs
import pytest

# Public interface
__all__ = ['NO_OUTPUT', 'main', 'main_lines', 'assert_output',
           'create_taskfile', 'create_taskfile_echo', 'create_json_taskfile',
           'UUID_PATTERN', 'FIXTURE_283', 'ORIGINAL_TASK_COLUMNS', 'HALF_UPGRADE_DDL',
           'copy_fixture', 'make_half_upgraded', 'snapshot', 'task_columns', 'sqlite_schema']


# Flag for empty output from `main_lines` function
NO_OUTPUT: Final[List[str]] = ['', ]


def main(argv: List[str]) -> Tuple[int, str, str]:
    """Return stdout, stderr, and exit code of command-line interface."""
    proc = run(argv, stdout=PIPE, stderr=PIPE, env=os.environ)
    return (
        proc.returncode,
        proc.stdout.decode('utf-8').strip(),
        proc.stderr.decode('utf-8').strip()
    )


def main_lines(argv: List[str]) -> Tuple[int, List[str], List[str]]:
    """Return stdout, stderr, and exit code of command-line interface with output as List[str]."""
    rc, stdout, stderr = main(argv)
    return (
        rc,
        [line.strip() for line in stdout.split('\n')],
        [line.strip() for line in stderr.split('\n')]
    )


def assert_output(pattern: str, output: str, count: int = 1, groups: Dict[str, str | re.Pattern] = None) -> None:
    """Assert some count of lines in output match pattern, optionally with capture groups pattern matching."""
    n = 0
    for line in output.strip().splitlines():
        if match := re.search(pattern, line):
            n += 1
            for key, value in (groups or {}).items():
                assert re.match(value, match.group(key))
    assert n == count


def create_taskfile(temp_site: Path, lines: List[str], filename: str = 'task.in') -> Path:
    """Produce task input file for test, return path."""
    taskfile = temp_site / filename
    with open(taskfile, mode='w', encoding='utf-8') as stream:
        for line in lines:
            print(line, file=stream)
    return taskfile


def create_taskfile_echo(temp_site: Path, count: int, tags: Dict[str, Any] = None) -> Path:
    """Produce task input file with 'echo {}' lines, return path."""
    tagline = ' '.join([f'{k}:{v}' for k, v in (tags or {}).items()])
    return create_taskfile(temp_site, lines=[f'echo {n}  # HYPERSHELL: {tagline} n:{n}' for n in range(count)])


def create_json_taskfile(temp_site: Path, data: Any, filename: str = 'plan.json') -> Path:
    """Produce a JSON task plan file for test, return path."""
    taskfile = temp_site / filename
    with open(taskfile, mode='w', encoding='utf-8') as stream:
        json.dump(data, stream)
    return taskfile


UUID_PATTERN: re.Pattern = re.compile(
    r'^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$'
)


FIXTURE_283: Final[Path] = Path(__file__).parent / 'fixtures' / 'hypershell-2.8.3.db'
"""SQLite database created by hypershell 2.8.3 (see tests/fixtures/README.md). Never opened read-write."""


ORIGINAL_TASK_COLUMNS: Final[Tuple[str, ...]] = (
    'id', 'group', 'args', 'submit_id', 'submit_time', 'submit_host', 'cores', 'memory',
    'cores_max', 'memory_max', 'timeout', 'server_id', 'server_host', 'schedule_time', 'client_id',
    'client_host', 'command', 'start_time', 'completion_time', 'exit_status', 'outpath', 'errpath',
    'csvpath', 'attempt', 'retried', 'waited', 'duration', 'previous_id', 'next_id', 'tag',
)
"""The `task` columns of a hypershell 2.8.3 database, in table order."""


HALF_UPGRADE_DDL: Final[Tuple[str, ...]] = (
    'CREATE TABLE source (\n\tid TEXT NOT NULL, \n\tpath TEXT NOT NULL, \n\tfingerprint TEXT, '
    '\n\ttask_count INTEGER, \n\tcreated DATETIME NOT NULL, \n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX index_source_lookup ON source (path, fingerprint)',
)
"""The statements a failed hypershell 2.9.0a1 run leaves behind on a 2.8 database (frozen)."""


def copy_fixture(temp_site: Path, name: str = None) -> Path:
    """Copy the 2.8.3 fixture to the site database path (or `name` in the site), return path."""
    if not FIXTURE_283.is_file():
        pytest.fail(f'Missing fixture: {FIXTURE_283}')
    target = Path(temp_site) / name if name else Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    shutil.copyfile(FIXTURE_283, target)
    return target


def make_half_upgraded(path: Path) -> Path:
    """Apply the frozen 2.9.0a1 statements to a copied 2.8.3 database, return path."""
    conn = sqlite3.connect(str(path))
    try:
        for statement in HALF_UPGRADE_DDL:
            conn.execute(statement)
        conn.commit()
    except sqlite3.Error as error:
        pytest.fail(f'Could not prepare half-upgraded database: {error}')
    finally:
        conn.close()
    return path


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Read-only raw connection to a SQLite file."""
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def snapshot(path: Path) -> Dict[str, Dict[str, tuple]]:
    """Raw rows as stored: the original `task` columns keyed by id, and every `client` row."""
    columns = ', '.join(f'"{name}"' for name in ORIGINAL_TASK_COLUMNS)
    conn = _connect_ro(path)
    try:
        return {
            'task': {row[0]: row for row in conn.execute(f'SELECT {columns} FROM task')},
            'client': {row[0]: row for row in conn.execute('SELECT * FROM client')},
        }
    except sqlite3.Error as error:
        pytest.fail(f'Could not snapshot {path}: {error}')
    finally:
        conn.close()


def task_columns(path: Path, table: str = 'task') -> List[str]:
    """Column names of a table, in table order."""
    conn = _connect_ro(path)
    try:
        return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    finally:
        conn.close()


def sqlite_schema(path: Path) -> List[Tuple[str, str, str, str]]:
    """Every `sqlite_master` entry (type, name, table, sql), sorted."""
    conn = _connect_ro(path)
    try:
        return sorted(conn.execute('SELECT type, name, tbl_name, coalesce(sql, \'\') FROM sqlite_master'))
    finally:
        conn.close()
