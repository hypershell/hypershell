# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Deterministic replays of the order in which the scheduler runs task groups.

These tests drive ``Scheduler.load_bundle`` in-process against a throwaway SQLite file.
Rows written by another process (a submitter, a later ``hs submit``, or the Receiver thread)
are committed through a separate session, injected at an exact point, so each interleaving is
replayed without sleeps, threads or timing.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Tuple
from pathlib import Path

# Standard libs
import os
import sys
import json
import logging
from datetime import datetime, timedelta
from subprocess import run, PIPE, TimeoutExpired

# External libs
import pytest
from pytest import mark, fixture
from cmdkit.app import exit_status
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Internal libs
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task
from hypershell.server import Scheduler, SchedulerState, is_file_input
from tests import main, create_taskfile, create_taskfile_echo


LATE_GROUP_MESSAGE = 'after task group'
"""Substring of the warning logged when a group starts after a higher one already has."""


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


def write_back(task_id: str, exit_status: int) -> None:
    """Commit a task result through a separate session, as the Receiver thread does."""
    session = Session.session_factory()
    try:
        session.bulk_update_mappings(Task, [{'id': task_id,
                                             'exit_status': exit_status,
                                             'completion_time': datetime.now().astimezone()}])
        session.commit()
    finally:
        session.close()


def fail(task_id: str, exit_status: int = 1) -> None:
    """Record a failed result for the task."""
    write_back(task_id, exit_status)


def succeed(task_id: str) -> None:
    """Record a successful result for the task."""
    write_back(task_id, 0)


def commit_rows(tasks: List[Task]) -> List[str]:
    """Commit new task rows through a separate session, as another writer does; return their ids."""
    ids = [task.id for task in tasks]
    session = Session.session_factory()
    try:
        session.add_all(tasks)
        session.commit()
    finally:
        session.close()
    return ids


def commit_tasks(*groups: int) -> List[str]:
    """Commit one new task per entry in `groups`, each in that group; return their ids."""
    return commit_rows([Task.new(args=f'echo {i}', group=group) for i, group in enumerate(groups)])


def make_scheduler(**options) -> Scheduler:
    """Started scheduler with no queue, zero back-off and one attempt unless given."""
    options.setdefault('attempts', 1)
    scheduler = Scheduler(queue=None, poll=0, **options)
    if scheduler.start() is not SchedulerState.LOAD:
        pytest.fail('Scheduler did not start into LOAD')
    return scheduler


def first_bundle(scheduler: Scheduler, limit: int = 4) -> List[Tuple[int, str]]:
    """Drive LOAD until a bundle is packed and return its (group, id) pairs; fail otherwise."""
    for _ in range(limit):
        state = scheduler.load_bundle()
        if state is SchedulerState.PACK:
            return [(task.group, task.id) for task in scheduler.tasks]
        if state is not SchedulerState.LOAD:
            pytest.fail(f'Expected a bundle, scheduler moved to {state}')
    pytest.fail(f'No bundle after {limit} loads')


def run_until_final(scheduler: Scheduler, limit: int = 40) -> Tuple[List[int], bool]:
    """Drive LOAD for at most `limit` passes, completing every bundle with success.

    Returns the group of each scheduled task in order, and whether the scheduler reached FINAL.
    """
    order = []
    for _ in range(limit):
        state = scheduler.load_bundle()
        if state is SchedulerState.FINAL:
            return order, True
        if state is SchedulerState.PACK:
            bundle = [(task.group, task.id) for task in scheduler.tasks]
            for group, task_id in bundle:
                order.append(group)
                succeed(task_id)
        elif state is not SchedulerState.LOAD:
            pytest.fail(f'Unexpected scheduler state {state}')
    return order, False


def late_group_warnings(caplog: pytest.LogCaptureFixture) -> List[str]:
    """Captured warnings that a group started after a higher one."""
    return [record.getMessage() for record in caplog.records
            if record.levelno == logging.WARNING and LATE_GROUP_MESSAGE in record.getMessage()]


def scheduled_in_group(group: int) -> int:
    """Count of rows in `group` that have been scheduled."""
    return (Task.query(caching=False)
            .filter(Task.group == group)
            .filter(Task.schedule_time.isnot(None))
            .count())


# --- Rows committed while the scheduler chooses its next group ------------------------------------

@mark.unit
def test_next_group_sees_rows_committed_during_check(task_db: Engine, monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    """Rows committed in the current group while the next group is chosen are run before moving on."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_tasks(0, 2)
    scheduler = make_scheduler()
    bundle = first_bundle(scheduler)
    if [group for group, _ in bundle] != [0]:
        pytest.fail(f'Expected the group-0 task first, found {bundle}')
    succeed(bundle[0][1])

    # The injection is armed only while the next group is chosen, so the stop branch never fires it.
    real_increment_group = Task.increment_group
    real_count_retryable = Task.count_retryable
    choosing = [False]
    injected: List[str] = []

    def increment_group(*args, **kwargs):
        choosing[0] = True
        try:
            return real_increment_group(*args, **kwargs)
        finally:
            choosing[0] = False

    def count_retryable(*args, **kwargs):
        if choosing[0] and not injected:
            injected.extend(commit_tasks(0, 1))
        return real_count_retryable(*args, **kwargs)

    monkeypatch.setattr(Task, 'increment_group', increment_group)
    monkeypatch.setattr(Task, 'count_retryable', count_retryable)
    scheduler.load_bundle()
    if not injected:
        pytest.fail('Injected rows were not committed')
    monkeypatch.setattr(Task, 'increment_group', real_increment_group)
    monkeypatch.setattr(Task, 'count_retryable', real_count_retryable)

    assert scheduler.current_group == 0
    late_row = first_bundle(scheduler)
    assert late_row == [(0, injected[0])]
    succeed(late_row[0][1])

    order, reached_final = run_until_final(scheduler)
    assert [0] + order == [0, 1, 2]
    assert reached_final
    assert late_group_warnings(caplog) == []


# --- A lower group arriving after a higher group has started ---------------------------------------

@mark.unit
@mark.parametrize('forever_mode', [False, True])
def test_late_lower_group_runs_after_current(task_db: Engine, caplog: pytest.LogCaptureFixture,
                                             forever_mode: bool) -> None:
    """A lower group committed after a higher group started runs once that group completes."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_tasks(1)
    scheduler = make_scheduler(forever_mode=forever_mode)
    bundle = first_bundle(scheduler)
    if [group for group, _ in bundle] != [1]:
        pytest.fail(f'Expected the group-1 task first, found {bundle}')
    late_id, = commit_tasks(0)
    succeed(bundle[0][1])

    scheduled = []
    for _ in range(10):
        state = scheduler.load_bundle()
        if state is SchedulerState.PACK:
            scheduled = [(task.group, task.id) for task in scheduler.tasks]
            break
        if state is not SchedulerState.LOAD:
            break
    assert scheduled == [(0, late_id)]

    warnings = late_group_warnings(caplog)
    assert len(warnings) == 1
    assert 'task group 0' in warnings[0] and 'task group 1' in warnings[0]

    succeed(late_id)
    order, reached_final = run_until_final(scheduler, limit=10)
    assert order == []
    assert reached_final is not forever_mode


# --- Input in ascending order that begins below the default group ----------------------------------

@mark.unit
def test_ascending_input_below_zero_runs_in_order(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """Groups committed after an empty-database start run in ascending order, with no warning."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    scheduler = make_scheduler()
    commit_tasks(-1, -1, 0)

    order, reached_final = run_until_final(scheduler)
    assert order == [-1, -1, 0]
    assert reached_final
    assert late_group_warnings(caplog) == []


# --- Restarting a database left with a lower group unscheduled -------------------------------------

@mark.unit
def test_restart_runs_stranded_lower_group(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A restart runs a lower group left unscheduled behind a completed higher group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    now = datetime.now().astimezone()
    commit_rows([
        Task.new(args='echo 1', group=1, attempt=1, exit_status=0, schedule_time=now, completion_time=now),
        Task.new(args='echo 0', group=0),
    ])
    scheduler = make_scheduler(restart_mode=True)

    order, reached_final = run_until_final(scheduler)
    assert order == [0]
    assert reached_final
    assert late_group_warnings(caplog) == []


@mark.unit
def test_restart_on_halted_group_still_halts(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """A restart on a group whose failure has used its retries stops without running the next group."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    now = datetime.now().astimezone()
    commit_rows([
        Task.new(args='false', group=0, attempt=1, exit_status=1, schedule_time=now, completion_time=now),
        Task.new(args='echo 1', group=1),
    ])
    scheduler = make_scheduler(restart_mode=True, attempts=1)

    caplog.clear()
    assert scheduler.load_bundle() is SchedulerState.FINAL
    assert any(record.getMessage().startswith('Failed task group 0') for record in caplog.records)
    assert scheduled_in_group(1) == 0


# --- A database whose earlier work finished at a higher group --------------------------------------

def commit_finished(*groups: int) -> None:
    """Commit one successful task per entry in `groups`, scheduled in that order an hour ago."""
    then = datetime.now().astimezone() - timedelta(hours=1)
    commit_rows([Task.new(args=f'echo done {i}', group=group, attempt=1, exit_status=0,
                          schedule_time=then + timedelta(seconds=i),
                          completion_time=then + timedelta(seconds=i, milliseconds=500))
                 for i, group in enumerate(groups)])


@mark.unit
@mark.parametrize('restart_mode', [False, True])
def test_new_file_after_finished_run_runs_in_order(task_db: Engine, caplog: pytest.LogCaptureFixture,
                                                   restart_mode: bool) -> None:
    """A new file on a database whose earlier run finished at a higher group runs in ascending order."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_finished(0, 1, 2)
    commit_tasks(0, 1, 2)
    scheduler = make_scheduler(restart_mode=restart_mode)

    order, reached_final = run_until_final(scheduler)
    assert order == [0, 1, 2]
    assert reached_final
    assert late_group_warnings(caplog) == []


@mark.unit
def test_tasks_after_finished_run_start_at_lowest_group(task_db: Engine, caplog: pytest.LogCaptureFixture) -> None:
    """Tasks committed after a start on finished work run from their lowest group, with no warning."""
    caplog.set_level(logging.DEBUG, logger='hypershell')
    commit_finished(0, 1, 2)
    scheduler = make_scheduler(forever_mode=True)
    commit_tasks(0, 1, 2)

    order, reached_final = run_until_final(scheduler, limit=20)
    assert order == [0, 1, 2]
    assert not reached_final
    assert late_group_warnings(caplog) == []


# --- File input is submitted whole before scheduling --------------------------------------------

class Wrapper:
    """A source that carries the stream it wraps as `iterable`."""

    def __init__(self, iterable: Any) -> None:
        self.iterable = iterable

    def __iter__(self) -> Iterator[str]:
        return iter(self.iterable)


@mark.unit
@mark.parametrize('kind, expected', [
    ('file', True), ('list', True), ('tuple', True),
    ('stdin', False), ('pipe', False), ('generator', False),
    ('wrapped-file', True), ('wrapped-pipe', False),
])
def test_file_input_classification(tmp_path: Path, kind: str, expected: bool) -> None:
    """A seekable file, a list or a tuple is file input; standard input, a pipe or a generator is not."""
    taskfile = tmp_path / 'task.in'
    taskfile.write_text('echo 0\n')
    handles = []
    try:
        if kind in ('file', 'wrapped-file'):
            handles.append(open(taskfile, mode='r'))
            source = handles[-1]
        elif kind in ('pipe', 'wrapped-pipe'):
            read_fd, write_fd = os.pipe()
            handles.append(os.fdopen(write_fd, mode='w'))
            handles.append(os.fdopen(read_fd, mode='r'))
            source = handles[-1]
        elif kind == 'list':
            source = ['echo 0']
        elif kind == 'tuple':
            source = ('echo 0', )
        elif kind == 'stdin':
            source = sys.stdin
        else:
            source = (line for line in ['echo 0'])
        if kind.startswith('wrapped-'):
            source = Wrapper(source)
        assert is_file_input(source) is expected
    finally:
        for handle in handles:
            handle.close()


def task_times(groups: List[int]) -> Dict[int, Tuple[List[datetime], List[datetime]]]:
    """Schedule and completion times of every task, by group, from `hs list --json`."""
    rc, stdout, _ = main(['hs', 'list', '--json'])
    if rc != exit_status.success:
        pytest.fail(f'hs list --json exited {rc}')
    times = {group: ([], []) for group in groups}
    for record in json.loads(stdout):
        if record['schedule_time'] is None or record['completion_time'] is None:
            pytest.fail(f'Task not completed: {record["id"]}')
        times[record['group']][0].append(datetime.fromisoformat(record['schedule_time']))
        times[record['group']][1].append(datetime.fromisoformat(record['completion_time']))
    return times


@mark.integration
@mark.parametrize('layout', [[1, 0, 1, 0], [0, -1, 0, -1]], ids=['1-0', '0-(-1)'])
def test_cluster_runs_unsorted_file_in_group_order(temp_site: Path, layout: List[int]) -> None:
    """A task file that lists a higher group first still runs its groups in ascending order."""
    tasks = [f'echo {n}  #HYPERSHELL: n:{n} group:{group}' for n, group in enumerate(layout)]
    taskfile = create_taskfile(temp_site, tasks)
    rc, _, stderr = main(['hs', 'cluster', str(taskfile), '-N2'])
    assert rc == exit_status.success

    # Results are read from the database, which is authoritative; task stdout is best effort.
    rc, stdout, _ = main(['hs', 'list', '-c', '-S'])
    assert rc == exit_status.success and stdout == str(len(tasks))

    groups = sorted(set(layout))
    times = task_times(groups)
    for lower, higher in zip(groups, groups[1:]):
        assert max(times[lower][1]) <= min(times[higher][0])
        assert stderr.count(f'Completed task group {lower} - starting task group {higher}') == 1
    assert stderr.count('Completed task group') == len(groups) - 1
    assert LATE_GROUP_MESSAGE not in stderr


CLIENT_TIMEOUT_TASKS = 6000
"""Tasks in a file that takes several times the client timeout to read (bundles of one)."""


@mark.integration
def test_cluster_clients_with_timeout_wait_for_file(temp_site: Path) -> None:
    """Clients with an idle timeout shorter than the file read still run every task, then exit."""
    taskfile = create_taskfile_echo(temp_site, CLIENT_TIMEOUT_TASKS)
    env = {**os.environ, 'HYPERSHELL_LOGGING_LEVEL': 'INFO'}
    try:
        proc = run(['hs', 'cluster', str(taskfile), '-N2', '-T1', '--no-tls', '-o', os.devnull],
                   stdout=PIPE, stderr=PIPE, env=env, timeout=180)
    except TimeoutExpired:
        pytest.fail('hs cluster did not finish within 180 seconds')
    assert proc.returncode == exit_status.success

    rc, stdout, _ = main(['hs', 'list', '-c', '-S'])
    assert rc == exit_status.success and stdout == str(CLIENT_TIMEOUT_TASKS)
