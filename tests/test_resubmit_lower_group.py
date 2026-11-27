# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs cluster FILE` and `hs server FILE` refuse a group below the reached group of an advanced database.

A named file given to either command on a database whose schedule has a reached group is refused
(exit 2) before anything is inserted or dispatched when it would insert a task in a lower group.
Tasks the re-submission gate skips as already present are not being submitted and never trigger
the refusal, so a same-file `--restart` onto a database a hang left stranded runs as before. A
stream is not refused: its late lower group runs, with a warning.
"""


# Type annotations
from __future__ import annotations
from typing import List, Tuple
from pathlib import Path

# Standard libs
import os
from datetime import datetime, timedelta

# External libs
from pytest import mark
from sqlalchemy.engine import Engine

# Internal libs
from tests import create_taskfile
from tests.input_checks import (bounded, db_site, sqlite_engine, stored_args, stored_groups, set_task, finish,  # noqa: F401
                                submit_ok, rows, ADDRESS)
from tests.test_timescale import admin, ts_database, ts_site  # noqa: F401 (fixtures db_site asks for)
from tests.test_cluster_exit import run_cli, cluster_command, HS


ADVANCED_FILE: List[str] = ['echo a # HYPERSHELL: group:0',
                            'echo b # HYPERSHELL: group:5',
                            'echo c # HYPERSHELL: group:5']
"""A task file whose run has reached group 5."""

LATE_GROUP_MESSAGE = 'after task group'
"""Substring of the warning logged when a group starts after a higher one already has."""


def advance(site: Path, engine: Engine) -> Path:
    """Submit `ADVANCED_FILE` and advance it: groups 0 and 5 each have a finished task and group 5 one queued.

    Its reached group is 5 whether or not tasks in flight count. Returns the file's path.
    """
    path = create_taskfile(site, ADVANCED_FILE, filename='tasks.in')
    submit_ok(str(path), cwd=site)
    now = datetime.now().astimezone()
    finish(engine, 'echo a', now - timedelta(minutes=10), now - timedelta(minutes=9), now - timedelta(minutes=8))
    finish(engine, 'echo b', now - timedelta(minutes=10), now - timedelta(minutes=7), now - timedelta(minutes=6))
    set_task(engine, 'echo c', submit_time=now - timedelta(minutes=10))
    return path


def completed(engine: Engine) -> List[str]:
    """Commands of every completed task, sorted."""
    return sorted(args for args, in rows(engine, 'SELECT args FROM task WHERE completion_time IS NOT NULL'))


@mark.integration
def test_cluster_update_below_reached_group_refused(db_site: Tuple[Path, Engine]) -> None:
    """`hs cluster FILE --restart --update` of a changed file adding a lower group is refused; nothing runs."""
    site, engine = db_site
    path = advance(site, engine)
    create_taskfile(site, [*ADVANCED_FILE, 'echo d # HYPERSHELL: group:1'], filename='tasks.in')
    status, _, log = run_cli(site, cluster_command(str(path), '--restart', '--update', '-N1'), timeout=60)
    assert status == 2, log[-4000:]
    assert 'reached group 5' in log, log[-4000:]
    assert sorted(stored_args(engine)) == ['echo a', 'echo b', 'echo c']
    assert completed(engine) == ['echo a', 'echo b']


@mark.integration
def test_server_file_below_reached_group_refused(db_site: Tuple[Path, Engine]) -> None:
    """`hs server FILE` holding a group below the reached group is refused before anything is inserted."""
    site, engine = db_site
    advance(site, engine)
    other = create_taskfile(site, ['echo d # HYPERSHELL: group:1', 'echo e # HYPERSHELL: group:6'], filename='more.in')
    rc, _, stderr = bounded([HS, 'server', str(other), '--no-tls', '-H', ADDRESS], cwd=site,
                            env={**os.environ, 'HYPERSHELL_SERVER_AUTH': 'Xk3v9QpL2mN7bR4tW8yZ'})
    assert rc == 2, stderr[-4000:]
    assert 'reached group 5' in stderr, stderr[-4000:]
    assert sorted(stored_args(engine)) == ['echo a', 'echo b', 'echo c']


@mark.integration
def test_same_file_restart_onto_stranded_database(db_site: Tuple[Path, Engine]) -> None:
    """A same-file `--restart` onto a database a hang left stranded inserts nothing new, is not refused, and runs."""
    site, engine = db_site
    path = create_taskfile(site, ['echo one # HYPERSHELL: group:1', 'echo zero # HYPERSHELL: group:0',
                                  'echo two # HYPERSHELL: group:1'], filename='tasks.in')
    submit_ok(str(path), cwd=site)
    # The run reached group 1: one task done, one left in flight; group 0 never ran.
    now = datetime.now().astimezone()
    finish(engine, 'echo one', now - timedelta(minutes=10), now - timedelta(minutes=9), now - timedelta(minutes=8))
    set_task(engine, 'echo two', submit_time=now - timedelta(minutes=10), schedule_time=now - timedelta(minutes=7))
    set_task(engine, 'echo zero', submit_time=now - timedelta(minutes=10))
    status, _, log = run_cli(site, f'{cluster_command(str(path), "--restart", "-N1")} > out.txt', timeout=90)
    assert status == 0, log[-4000:]
    assert sorted(stored_args(engine)) == ['echo one', 'echo two', 'echo zero']
    assert completed(engine) == ['echo one', 'echo two', 'echo zero']
    assert sorted((site / 'out.txt').read_text().split()) == ['two', 'zero']


@mark.integration
def test_stream_late_lower_group_runs_with_warning(temp_site: Path) -> None:
    """A stream into `hs cluster` is not refused: its late lower group runs after the reached group, with a warning."""
    site = Path(str(temp_site))
    feed = "{ echo 'echo high # HYPERSHELL: group:5'; sleep 6; echo 'echo late # HYPERSHELL: group:1'; }"
    status, _, log = run_cli(site, f'{feed} | {cluster_command("-N1")} > out.txt', timeout=90)
    assert status == 0, log[-4000:]
    assert sorted((site / 'out.txt').read_text().split()) == ['high', 'late']
    assert stored_groups(sqlite_engine()) == {'echo high': 5, 'echo late': 1}
    assert LATE_GROUP_MESSAGE in log
