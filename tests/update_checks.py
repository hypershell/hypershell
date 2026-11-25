# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the tests of `hs update` changes read by each column's kind.

Not collected (no `test_` prefix). A site with three tasks tagged n:0, n:1 and n:2; both forms of a
change on the task tagged n:1 (`COLUMN=VALUE -t n:1` and `ID COLUMN VALUE`); a snapshot of every
column of every task; the refusal every kind gives; and the value a column holds, read back through
the private engine on SQLite or PostgreSQL. Every name used here exists on the base build, so a
module importing this one collects there; names this cycle adds are imported inside the tests.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Final, List, Optional, Tuple
from pathlib import Path

# Standard libs
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# External libs
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.core.config_schema import shown
from tests import create_taskfile
from tests.input_checks import bounded, rows


LOCAL_ZONE: Final[str] = 'America/Indiana/Indianapolis'
"""The local time zone every command runs in here: not UTC, so a value read in the wrong zone shows."""

LOCAL: Final[ZoneInfo] = ZoneInfo(LOCAL_ZONE)
"""The local time zone, for reading back a wall time SQLite holds."""

ZONED: Final[Dict[str, str]] = {'TZ': LOCAL_ZONE, 'PGTZ': 'UTC'}
"""Variables for a command: local time is not UTC, and a PostgreSQL session reads a naive time as UTC."""

FORMS: Final[List[str]] = ['filtered', 'single']
"""`COLUMN=VALUE -t n:1` (filtered) and `ID COLUMN VALUE` (single-task), on the task tagged n:1."""

REFUSED: Final[object] = object()
"""The expected outcome of a value the column's kind refuses."""


def command_env(**variables: str) -> Dict[str, str]:
    """The current environment with `variables` set (and the local zone unless given)."""
    return {**os.environ, **ZONED, **variables}


def hs(site: Path, argv: List[str], stdin: Optional[str] = None, **variables: str) -> Tuple[int, str, str]:
    """Run `argv` in `site` under the local zone, bounded; its exit status, stdout and stderr."""
    return bounded(argv, stdin=stdin, cwd=site, env=command_env(**variables))


def submit_three(site: Path) -> None:
    """Three tasks tagged n:0, n:1 and n:2, never run."""
    taskfile = create_taskfile(site, [f'echo {n}  # HYPERSHELL: n:{n}' for n in range(3)])
    rc, _, stderr = hs(site, ['hs', 'submit', str(taskfile)])
    assert rc == 0, stderr


def tag_of(tag: Any) -> Dict[str, Any]:
    """A stored tag as a dictionary (text on SQLite, JSON on PostgreSQL)."""
    return (json.loads(tag) if isinstance(tag, str) else tag) or {}


def all_rows(engine: Engine) -> List[Dict[str, Any]]:
    """Every column of every task, by column name, the tag as a dictionary and ids as text."""
    result = []
    with engine.connect() as conn:
        from sqlalchemy import text
        for row in conn.execute(text('SELECT * FROM task')):
            record = dict(row._mapping)
            record['tag'] = tag_of(record['tag'])
            for name, value in record.items():
                if type(value).__name__ == 'UUID':
                    record[name] = str(value)
            result.append(record)
    return result


def snapshot(engine: Engine) -> List[str]:
    """Every column of every task, comparable before and after a command."""
    return sorted(json.dumps(record, sort_keys=True, default=str) for record in all_rows(engine))


def task_id(engine: Engine, n: int) -> str:
    """The id of the task tagged n:`n`, as text."""
    found = [str(record['id']) for record in all_rows(engine) if record['tag'].get('n') == n]
    assert len(found) == 1, found
    return found[0]


def task_row(engine: Engine, n: int) -> Dict[str, Any]:
    """Every column of the task tagged n:`n`."""
    found = [record for record in all_rows(engine) if record['tag'].get('n') == n]
    assert len(found) == 1, found
    return found[0]


def stored_type(engine: Engine, column: str, ident: str) -> Optional[str]:
    """SQLite's storage class of `column` on the task `ident` (None on PostgreSQL)."""
    if engine.dialect.name != 'sqlite':
        return None
    return rows(engine, f'SELECT typeof("{column}") FROM task WHERE id = :id', id=ident)[0][0]


def update_argv(engine: Engine, form: str, column: str, value: str, *extra: str) -> List[str]:
    """The `hs update` command changing `column` to `value` on the task tagged n:1, in `form`."""
    if form == 'filtered':
        return ['hs', 'update', f'{column}={value}', *extra, '-t', 'n:1']
    return ['hs', 'update', task_id(engine, 1), column, value, *extra]


def unwritten(stdout: str, stderr: str) -> None:
    """Nothing counted, asked, written or traced back."""
    for output in (stdout, stderr):
        assert 'Traceback' not in output, output[-4000:]
        assert 'Searching database' not in output, output[-4000:]
        assert 'affects' not in output and 'continue?' not in output, output[-4000:]
        assert 'Updated' not in output, output[-4000:]


def assert_refused(rc: int, stdout: str, stderr: str, column: str, value: str) -> None:
    """Exit status 2, one line `<column>: expected …, given <value>`, and nothing counted, asked or written."""
    assert rc == 2, f'{column}={value!r}: exit {rc}\n{stderr[-4000:]}'
    lines = [line for line in stderr.splitlines() if f'{column}: expected ' in line and f'given {shown(value)}' in line]
    assert len(lines) == 1, f'{column}={value!r}:\n{stderr[-4000:]}'
    unwritten(stdout, stderr)


def instant(value: Any) -> Optional[float]:
    """The POSIX time of a stored datetime: SQLite holds the local wall time as text, PostgreSQL the instant."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=LOCAL)
    return value.timestamp()


Check = Callable[[Engine, str, Any, Any], None]
"""How a column's stored value is checked against the value expected: (engine, column, stored, expected)."""


def same_int(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """A whole number stored as an integer, or NULL."""
    assert stored == expected and (expected is None or type(stored) is int), (column, stored, expected)


def same_float(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """A number stored as a float (never an integer), or NULL."""
    assert stored == expected and (expected is None or type(stored) is float), (column, stored, expected)
    if expected is not None:
        assert str(stored) != '-0.0', (column, stored)


def same_bool(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """A boolean (SQLite holds 1 or 0)."""
    assert stored in (0, 1) and bool(stored) is expected, (column, stored, expected)


def same_text(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """Text (a uuid as lower-case text), or NULL."""
    assert (None if stored is None else str(stored)) == expected, (column, stored, expected)


def same_instant(engine: Engine, column: str, stored: Any, expected: Any) -> None:
    """The same instant, or NULL."""
    assert instant(stored) == (None if expected is None else expected.timestamp()), (column, stored, expected)


SQLITE_TYPES: Final[Dict[Check, str]] = {same_int: 'integer', same_float: 'real', same_bool: 'integer', same_text: 'text'}
"""The storage class SQLite holds for each check's kind of value, when not NULL."""


def drive(site: Path, engine: Engine, column: str, form: str, cases: List[Tuple[str, Any]], check: Check) -> None:
    """Change `column` on the task tagged n:1 to each value of `cases` in `form`, checking each outcome.

    A refused value exits 2 naming the column and the value, and leaves every task as it was; an
    accepted one exits 0 and stores the value expected, on that task only.
    """
    target = task_id(engine, 1)
    for value, expected in cases:
        before = snapshot(engine)
        others = [record for record in all_rows(engine) if record['id'] != target]
        rc, stdout, stderr = hs(site, update_argv(engine, form, column, value, '-f'))
        if expected is REFUSED:
            assert_refused(rc, stdout, stderr, column, value)
            assert snapshot(engine) == before, f'{column}={value!r} wrote'
            continue
        assert rc == 0, f'{column}={value!r}: exit {rc}\n{stderr[-4000:]}'
        assert 'Traceback' not in stderr, stderr[-4000:]
        check(engine, column, task_row(engine, 1)[column], expected)
        if expected is not None and check in SQLITE_TYPES and engine.dialect.name == 'sqlite':
            assert stored_type(engine, column, target) == SQLITE_TYPES[check], (column, value)
        assert [record for record in all_rows(engine) if record['id'] != target] == others, f'{column}={value!r}'
