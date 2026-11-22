# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the tests of submitted input: bounded commands and a site on SQLite or PostgreSQL.

Every command a test starts runs in its own session under a wall-clock bound. A command still
running at its bound is killed with its whole process group and fails the test, so an input that
hangs a run fails by assertion rather than hanging the suite.

The `db_site` fixture gives a site with an initialized database on SQLite, or on a throwaway
PostgreSQL database when `HS_TEST_POSTGRES` names a server, and a private engine on that database
for reading rows and writing task columns directly. A test module that uses it imports `admin`,
`ts_database` and `ts_site` from `tests.test_timescale` as well, since the PostgreSQL branch asks
for them by name.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Iterator, List, Optional, Tuple

# Standard libs
import os
import json
import signal
from pathlib import Path
from datetime import datetime
from subprocess import Popen, PIPE, DEVNULL, TimeoutExpired

# External libs
import pytest
from pytest import fixture
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data.model import Task
from tests.test_timescale import needs_server


BOUND: Final[float] = 30
"""Seconds a single command may run before the test stops it and fails."""

ADDRESS: Final[str] = '127.0.0.1'
"""Every server a test starts binds the loopback address."""


def kill_group(proc: Popen) -> None:
    """Kill every process left in the session `proc` leads."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # Group already gone


def bounded(argv: List[str], stdin: Optional[str] = None, timeout: float = BOUND,
            cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run `argv` in its own session; return its exit status, stdout and stderr.

    A command still running after `timeout` seconds is killed with its process group and the test
    fails. Anything the command left in its group is killed once it exits.
    """
    proc = Popen(argv, stdin=PIPE if stdin is not None else DEVNULL, stdout=PIPE, stderr=PIPE, text=True,
                 cwd=cwd, env=env if env is not None else os.environ, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(stdin, timeout=timeout)
    except TimeoutExpired:
        kill_group(proc)
        _, stderr = proc.communicate()
        pytest.fail(f'{" ".join(argv)} did not exit within {timeout} s:\n{stderr[-4000:]}')
    finally:
        kill_group(proc)
    return proc.returncode, stdout, stderr


@fixture(params=['sqlite', pytest.param('postgres', marks=needs_server)])
def db_site(request: pytest.FixtureRequest, temp_site: Path) -> Iterator[Tuple[Path, Engine]]:
    """A site with an initialized database on SQLite or PostgreSQL, and a private engine on it."""
    site = Path(str(temp_site))
    if request.param == 'sqlite':
        engine = create_engine(f'sqlite:///{os.environ["HYPERSHELL_DATABASE_FILE"]}')
    else:
        name = request.getfixturevalue('ts_database')()
        engine = request.getfixturevalue('ts_site')(name, provider='postgres')
        os.environ['HYPERSHELL_SITE'] = str(site)
    rc, _, stderr = bounded(['hs', 'initdb', '--yes'], cwd=site)
    assert rc == 0, stderr
    try:
        yield site, engine
    finally:
        engine.dispose()


def sqlite_engine() -> Engine:
    """A private engine on the SQLite database of the current site."""
    return create_engine(f'sqlite:///{os.environ["HYPERSHELL_DATABASE_FILE"]}')


def rows(engine: Engine, query: str, **params: Any) -> List[tuple]:
    """Every row of `query`."""
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(query), params)]


def stored_args(engine: Engine) -> List[str]:
    """Stored commands of every task, in submit order."""
    return [args for args, in rows(engine, 'SELECT args FROM task ORDER BY submit_time')]


def stored_groups(engine: Engine) -> Dict[str, Any]:
    """Stored group of every task, by its command."""
    return dict(rows(engine, 'SELECT args, "group" FROM task'))


def stored_tags(engine: Engine) -> Dict[str, Dict[str, Any]]:
    """Stored tags of every task, by its command."""
    return {args: (json.loads(tag) if isinstance(tag, str) else tag) or {}
            for args, tag in rows(engine, 'SELECT args, tag FROM task')}


def count(engine: Engine, table: str) -> int:
    """Number of rows in `table`."""
    return rows(engine, f'SELECT count(*) FROM {table}')[0][0]


def set_task(engine: Engine, args: str, **values: Any) -> None:
    """Write task columns of the task whose command is `args` (typed by the model, so times compare as stored)."""
    table = Task.__table__
    with engine.begin() as conn:
        result = conn.execute(update(table).where(table.c.args == args).values(**values))
        assert result.rowcount == 1, f'No single task with args {args!r}'


def finish(engine: Engine, args: str, submitted: datetime, scheduled: datetime, completed: datetime,
           status: int = 0) -> None:
    """Make the task `args` one that was submitted, scheduled and completed at the given times."""
    set_task(engine, args, submit_time=submitted, schedule_time=scheduled, completion_time=completed,
             exit_status=status)


def submit(*argv: str, stdin: Optional[str] = None, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """Run `hs submit` with `argv`, bounded."""
    return bounded(['hs', 'submit', *argv], stdin=stdin, cwd=cwd)


def submit_ok(*argv: str, cwd: Optional[Path] = None) -> None:
    """Run `hs submit` with `argv` and assert it succeeds."""
    rc, _, stderr = submit(*argv, cwd=cwd)
    assert rc == 0, stderr
