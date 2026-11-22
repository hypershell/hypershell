# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The reached group, and `hs submit` refusing a group below it.

The reached group is the highest group the current pass has scheduled, read from task columns.
The current pass is the run of work since the database last held no incomplete task; there is no
reached group once every task has finished. Cancelled rows do not count, a retry row counts once
it exists, and on SQLite a group stored as text by an earlier release is ignored. Every row counted
was scheduled in the current pass, so the value never exceeds the group the schedule has truly
reached. With `in_flight=False` (for `hs cluster FILE` and `hs server FILE`, which revert in-flight
tasks when they start) a task still in flight does not count.

`hs submit` with a database refuses, all or nothing with exit status 2, a submission holding a task
below the reached group, naming both groups. Without `-g` it takes the reached group when the most
recently scheduled group is below it, so a submission that names no group is never refused.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, Optional, Tuple
from pathlib import Path

# Standard libs
import re
import uuid
from datetime import datetime, timedelta

# External libs
import pytest
from pytest import mark, fixture
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

# Internal libs
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, CANCEL_STATUS
from hypershell.data.upgrade import upgrade_schema
from tests import create_taskfile
from tests.input_checks import db_site, stored_args, stored_groups, set_task, finish, submit, submit_ok  # noqa: F401
from tests.test_timescale import admin, ts_database, ts_site, needs_server, ADMIN_URL  # noqa: F401


# --- Task.reached_group, in-process on both dialects ---------------------------------------------

@fixture(params=['sqlite', pytest.param('postgres', marks=needs_server)])
def model_db(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """Bind the global session to a fresh database on SQLite or PostgreSQL; yield the dialect name."""
    admin_engine, name = None, None
    if request.param == 'sqlite':
        engine = create_engine(f'sqlite:///{tmp_path / "tasks.db"}', connect_args={'check_same_thread': False})
        Entity.metadata.create_all(engine)
    else:
        admin_engine = create_engine(ADMIN_URL, isolation_level='AUTOCOMMIT')
        name = f't_{uuid.uuid4().hex[:16]}'
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE {name}'))
        engine = create_engine(make_url(ADMIN_URL).set(database=name))
        upgrade_schema(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield request.param
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()
        if admin_engine is not None:
            with admin_engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
            admin_engine.dispose()


T0 = datetime.now().astimezone().replace(microsecond=0) - timedelta(days=1)
"""A fixed origin for the scenarios; each time is a whole number of minutes after it."""


def at(minutes: float) -> datetime:
    """The time `minutes` after the origin."""
    return T0 + timedelta(minutes=minutes)


def row(group: Any, submitted: float, scheduled: Optional[float] = None, completed: Optional[float] = None,
        status: Optional[int] = None, previous: Optional[str] = None) -> str:
    """Add one task row with the given group and times (in minutes after the origin); return its id."""
    task = Task.new(args=f'echo {uuid.uuid4().hex[:8]}', group=group)
    task.submit_time = at(submitted)
    task.schedule_time = None if scheduled is None else at(scheduled)
    task.completion_time = None if completed is None else at(completed)
    task.exit_status = status
    task.previous_id = previous
    Task.add(task)
    return task.id


def scenario(name: str) -> None:
    """Write the rows of one scenario."""
    if name == 'S0':  # Empty.
        return
    if name == 'S1':  # Group 0 done; group 5 with one task in flight and one queued.
        row(0, 0, 1, 2, 0)
        row(5, 0, 3)
        row(5, 0)
    elif name == 'S2':  # A first pass ran to group 9 and finished; a second pass runs group 1.
        for group in range(10):
            row(group, 0, 1 + group, 2 + group, 0)
        row(1, 20, 21)
    elif name == 'S3':  # Every task finished.
        for group in (0, 5, 9):
            row(group, 0, 1, 2, 0)
    elif name == 'S4':  # Group 0 done and group 2 in flight; a queued group 9 was cancelled.
        row(0, 0, 1, 2, 0)
        row(2, 0, 3)
        row(9, 0, 4, 4, CANCEL_STATUS)
    elif name == 'S5':  # Group 5 finished after a late group 1 was submitted; group 1 now runs.
        row(5, 0, 1, 3, 0)
        row(1, 2, 4)
    elif name == 'S6':  # Every task finished; one in group 9 failed with no retry left.
        row(0, 0, 1, 2, 0)
        row(9, 0, 3, 4, 1)
    elif name == 'S7':  # Only a failure awaiting its retry row.
        row(9, 0, 1, 2, 1)
    elif name == 'S8':  # The retry row of a group 9 failure is queued; the failed row finished before it.
        failed = row(9, 0, 1, 2, 1)
        row(9, 3, previous=failed)
    elif name == 'S9':  # Group 9 finished before the only open task arrived: an under-estimate.
        row(1, 0, 1, 6, 0)
        row(9, 0, 2, 5, 0)
        row(1, 5.5)
    elif name == 'S10':  # A run left stranded: group 0 done, group 5 only in flight.
        row(0, 0, 1, 2, 0)
        row(5, 0, 3)
    else:
        raise ValueError(name)


EXPECTED: Dict[str, Tuple[Optional[int], Optional[int]]] = {
    'S0': (None, None),
    'S1': (5, 0),
    'S2': (1, None),
    'S3': (None, None),
    'S4': (2, 0),
    'S5': (5, 5),
    'S6': (None, None),
    'S7': (None, None),
    'S8': (9, 9),
    'S9': (1, 1),
    'S10': (5, 0),
}
"""The reached group of each scenario, with tasks in flight counted and not."""


@mark.unit
@mark.parametrize('name', list(EXPECTED))
def test_reached_group_scenarios(model_db: str, name: str) -> None:
    """The reached group of each scenario, the same on SQLite and PostgreSQL."""
    scenario(name)
    reached, reached_without_in_flight = EXPECTED[name]
    assert Task.reached_group() == reached
    assert Task.reached_group(in_flight=True) == reached
    assert Task.reached_group(in_flight=False) == reached_without_in_flight


@mark.unit
def test_reached_group_ignores_text_groups(model_db: str) -> None:
    """On SQLite, groups an earlier release stored as text or real are ignored; the value is always an integer."""
    if model_db != 'sqlite':
        pytest.skip('PostgreSQL stores only integer groups')
    ids = [row(5, 0, 1), row(5, 0, 1), row(5, 0, 1)]
    Session.execute(text('UPDATE task SET "group" = \'x\' WHERE id = :id'), {'id': ids[0]})
    Session.execute(text('UPDATE task SET "group" = 1.5 WHERE id = :id'), {'id': ids[1]})
    Session.commit()
    reached = Task.reached_group()
    assert reached == 5 and isinstance(reached, int)
    assert Task.reached_group(in_flight=False) is None


# --- hs submit -----------------------------------------------------------------------------------

def advance(engine: Engine, finished: bool = False) -> None:
    """Submit tasks in groups 0 and 5 and advance them: group 0 done, group 5 with one task in flight, one queued.

    With `finished`, every task is done instead, so there is no reached group.
    """
    for group, command in ((0, 'echo a'), (5, 'echo b'), (5, 'echo c')):
        submit_ok('-g', str(group), '--', *command.split())
    now = datetime.now().astimezone()
    if finished:
        for n, command in enumerate(('echo a', 'echo b', 'echo c')):
            finish(engine, command, now - timedelta(minutes=10), now - timedelta(minutes=9 - n),
                   now - timedelta(minutes=8 - n))
        return
    finish(engine, 'echo a', now - timedelta(minutes=10), now - timedelta(minutes=9), now - timedelta(minutes=8))
    set_task(engine, 'echo b', submit_time=now - timedelta(minutes=10), schedule_time=now - timedelta(minutes=7))
    set_task(engine, 'echo c', submit_time=now - timedelta(minutes=10))


def assert_reached_refusal(stderr: str, lowest: int, reached: int) -> None:
    """The refusal names the lowest offending group and the reached group."""
    lines = [line for line in stderr.splitlines() if 'reached group' in line]
    assert any(re.search(rf'\b{lowest}\b.*reached group {reached}\b', line) for line in lines), stderr[-4000:]


@mark.integration
def test_submit_below_reached_group_refused(db_site: Tuple[Path, Engine]) -> None:
    """A task below the reached group is refused (exit 2), naming both groups; nothing is inserted."""
    site, engine = db_site
    advance(engine)
    rc, _, stderr = submit('-g', '0', '--', 'echo', 'late', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert_reached_refusal(stderr, 0, 5)
    assert 'echo late' not in stored_args(engine)


@mark.integration
@mark.parametrize('case, group', [('at', 5), ('above', 7)], ids=['at', 'above'])
def test_submit_at_or_above_reached_group(db_site: Tuple[Path, Engine], case: str, group: int) -> None:
    """A task in the reached group or above it is accepted."""
    site, engine = db_site
    advance(engine)
    rc, _, stderr = submit('-g', str(group), '--', 'echo', 'new', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(engine)['echo new'] == group


@mark.integration
@mark.parametrize('case', ['empty', 'finished'])
def test_submit_any_group_without_reached_group(db_site: Tuple[Path, Engine], case: str) -> None:
    """With no reached group (an empty database, or every task finished), any group is accepted."""
    site, engine = db_site
    if case == 'finished':
        advance(engine, finished=True)
    rc, _, stderr = submit('-g', '0', '--', 'echo', 'new', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(engine)['echo new'] == 0


LOWER_FILE = ['echo x # HYPERSHELL: group:5', 'echo y # HYPERSHELL: group:1', 'echo z']
"""A submission with one task in the reached group, one below it, and one that names no group."""


@mark.integration
def test_submit_file_below_reached_group_refused_whole(db_site: Tuple[Path, Engine]) -> None:
    """A task file holding one task below the reached group is refused whole: nothing inserted."""
    site, engine = db_site
    advance(engine)
    path = create_taskfile(site, LOWER_FILE)
    rc, _, stderr = submit('-b', '1', '-w', '1', str(path), cwd=site)
    assert rc == 2, stderr[-4000:]
    assert_reached_refusal(stderr, 1, 5)
    assert sorted(stored_args(engine)) == ['echo a', 'echo b', 'echo c']
    assert rows_for_path(engine, str(path)) == 0


def rows_for_path(engine: Engine, path: str) -> int:
    """Number of Source rows recorded for `path`."""
    with engine.connect() as conn:
        return conn.execute(text('SELECT count(*) FROM source WHERE path = :path'), {'path': path}).scalar()


@mark.integration
def test_submit_stdin_below_reached_group_refused_whole(db_site: Tuple[Path, Engine]) -> None:
    """Standard input is read in full and refused whole when one task is below the reached group."""
    site, engine = db_site
    advance(engine)
    rc, _, stderr = submit('-b', '1', '-w', '1', stdin='\n'.join(LOWER_FILE) + '\n', cwd=site)
    assert rc == 2, stderr[-4000:]
    assert_reached_refusal(stderr, 1, 5)
    assert sorted(stored_args(engine)) == ['echo a', 'echo b', 'echo c']


@mark.integration
def test_submit_default_group_is_reached_group(db_site: Tuple[Path, Engine]) -> None:
    """With no `-g`, a submission takes the reached group when the most recently scheduled group is below it."""
    site, engine = db_site
    advance(engine)
    rc, _, stderr = submit('-g', '5', '--', 'echo', 'late', cwd=site)
    assert rc == 0, stderr[-4000:]
    # That task becomes a late lower group now running: group 1, the most recently scheduled.
    now = datetime.now().astimezone()
    set_task(engine, 'echo late', group=1, submit_time=now - timedelta(minutes=6),
             schedule_time=now - timedelta(minutes=1))
    rc, _, stderr = submit('--', 'echo', 'plain', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(engine)['echo plain'] == 5
    rc, _, stderr = submit('-f', '-', stdin='echo piped\n', cwd=site)
    assert rc == 0, stderr[-4000:]
    assert stored_groups(engine)['echo piped'] == 5
