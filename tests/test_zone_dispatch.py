# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Zones in the dispatcher and the scheduler: demand, per-zone pools, unstaging, the held-group line.

Scheduler tests drive its actions one step at a time against a throwaway SQLite database.
"""


# Type annotations
from __future__ import annotations
from typing import Iterator, List, Optional

# Standard libs
import os
import json
import sqlite3
import logging
from pathlib import Path
from types import SimpleNamespace

# External libs
from pytest import mark, fixture
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# External libs (command line)
from cmdkit.app import exit_status

# Internal libs
import hypershell.server
from hypershell.core.dispatch import Dispatcher, PROTOCOL
from hypershell.core.heartbeat import Heartbeat, ClientState
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task, Zone, zone_id, DEFAULT_ZONE_ID
from hypershell.server import Scheduler, SchedulerState, HeartMonitor, HeartbeatState
from tests import main


GPU, CPU = zone_id('gpu'), zone_id('cpu')


@fixture(autouse=True)
def short_timeout(monkeypatch) -> None:
    """Shorten the scheduler's bounded waits."""
    monkeypatch.setattr(hypershell.server, 'DEFAULT_REMOTE_TIMEOUT', 0.05)


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


@fixture
def records() -> Iterator[List[logging.LogRecord]]:
    """Every record the server logs, at any level."""
    captured: List[logging.LogRecord] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(hypershell.server.__name__)
    handler, level = ListHandler(level=1), logger.level
    logger.addHandler(handler)
    logger.setLevel(1)
    yield captured
    logger.removeHandler(handler)
    logger.setLevel(level)


def ask(dispatcher: Dispatcher, client: str, zone: Optional[str] = None, size: Optional[int] = None,
        wait: float = 0.0) -> dict:
    """One work request, decoded."""
    return json.loads(dispatcher.request(json.dumps({'protocol': PROTOCOL, 'client': client, 'host': 'h',
                                                     'zone': zone, 'size': size, 'wait': wait})))


def ids(reply: dict) -> List[str]:
    """Ids of the tasks in a reply."""
    return [task['id'] for task in reply['tasks']]


def add_tasks(count: int, zone: Optional[str] = None, group: int = 0) -> List[str]:
    """Submit `count` tasks in `zone` and `group`, return their ids in submit order."""
    tasks = [Task.new(args=f'echo {zone} {group} {n}', zone=zone, group=group) for n in range(count)]
    Task.add_all(tasks)
    return [task.id for task in tasks]


def scheduler_for(dispatcher: Dispatcher, bundlesize: int = 2) -> Scheduler:
    """A scheduler on `dispatcher`, past its start state."""
    scheduler = Scheduler(queue=SimpleNamespace(dispatcher=dispatcher), bundlesize=bundlesize, forever_mode=True,
                          poll=0)
    scheduler.current_group = 0
    return scheduler


@mark.unit
def test_request_registers_demand() -> None:
    """A zoned request registers its client's zone and size; the zone then needs filling."""
    dispatcher = Dispatcher(bundlesize=2)
    assert dispatcher.refills()[1] == [(DEFAULT_ZONE_ID, 2)]
    ask(dispatcher, 'g1', zone='gpu', size=5)
    assert dispatcher.clients(GPU) == 1
    assert sorted(dispatcher.refills()[1]) == sorted([(DEFAULT_ZONE_ID, 2), (GPU, 5)])
    ask(dispatcher, 'g2', zone='gpu', size=None)
    assert dispatcher.clients(GPU) == 2
    assert dict(dispatcher.refills()[1])[GPU] == 5


@mark.unit
def test_new_zone_wakes_the_scheduler() -> None:
    """The first request for a zone wakes a waiting scheduler."""
    dispatcher = Dispatcher()
    generation, _ = dispatcher.refills()
    ask(dispatcher, 'g1', zone='gpu')
    assert dispatcher.refills()[0] != generation


@mark.unit
def test_zoned_request_gets_only_its_zone() -> None:
    """A zoned request never gets another zone's task; a request without a zone gets default-zone tasks only."""
    dispatcher = Dispatcher()
    dispatcher.stage(DEFAULT_ZONE_ID, [('d1', '{"id": "d1"}'), ('d2', '{"id": "d2"}')])
    dispatcher.stage(GPU, [('g1', '{"id": "g1"}')])
    assert ids(ask(dispatcher, 'c1', zone='gpu', size=10)) == ['g1']
    assert ids(ask(dispatcher, 'c1', zone='gpu', size=10)) == []
    assert ids(ask(dispatcher, 'c2', zone='cpu', size=10)) == []
    assert ids(ask(dispatcher, 'c3', size=10)) == ['d1', 'd2']
    dispatcher.stage(DEFAULT_ZONE_ID, [('d3', '{"id": "d3"}')])
    assert ids(ask(dispatcher, 'c4', zone='default', size=10)) == ['d3']


@mark.unit
def test_scheduler_fills_demanded_zones_only(task_db: Engine) -> None:
    """The scheduler loads the default zone always and other zones only while a client serves them."""
    default_ids, gpu_ids, cpu_ids = add_tasks(3), add_tasks(3, GPU), add_tasks(3, CPU)
    dispatcher = Dispatcher(bundlesize=2)
    ask(dispatcher, 'g1', zone='gpu', size=1)
    scheduler = scheduler_for(dispatcher)
    assert scheduler.load_bundle() is SchedulerState.PACK
    assert sorted(zone for zone, _ in scheduler.loaded) == sorted([DEFAULT_ZONE_ID, GPU])
    assert scheduler.pack_bundle() is SchedulerState.LOAD
    assert dispatcher.staged(DEFAULT_ZONE_ID) == 2 and dispatcher.staged(GPU) == 1
    assert dispatcher.staged(CPU) == 0
    assert ids(ask(dispatcher, 'g1', zone='gpu', size=1)) == gpu_ids[:1]
    assert ids(ask(dispatcher, 'd1', size=5)) == default_ids[:2]
    unscheduled = {t.id for t in Task.query().filter(Task.schedule_time.is_(None)).all()}
    assert set(cpu_ids) <= unscheduled


@mark.unit
def test_losing_the_last_client_unstages_by_id(task_db: Engine, records: List[logging.LogRecord]) -> None:
    """When a zone's last client finishes, its staged rows are reverted by id and read unscheduled."""
    gpu_ids = add_tasks(4, GPU)
    dispatcher = Dispatcher(bundlesize=2)
    ask(dispatcher, 'g1', zone='gpu', size=2)
    scheduler = scheduler_for(dispatcher)
    for _ in range(2):
        assert scheduler.load_bundle() is SchedulerState.PACK
        assert scheduler.pack_bundle() is SchedulerState.LOAD
    assert dispatcher.staged(GPU) == 4
    assert all(Task.from_id(i, caching=False).schedule_time is not None for i in gpu_ids)

    monitor = HeartMonitor(queue=SimpleNamespace(dispatcher=dispatcher), evict_after=10, in_memory=True)
    monitor.beats['g1'] = Heartbeat.new(uuid='g1', host='h')
    monitor.latest_heartbeat = Heartbeat.new(uuid='g1', host='h', state=ClientState.FINISHED)
    assert monitor.update_client() is HeartbeatState.SWITCH
    assert dispatcher.clients(GPU) == 0

    scheduler.load_bundle()
    Session.expire_all()
    assert dispatcher.staged(GPU) == 0
    assert all(Task.from_id(i, caching=False).schedule_time is None for i in gpu_ids)
    assert any('Reverted 4 staged tasks' in r.getMessage() for r in records)


@mark.unit
def test_unstaging_skips_a_zone_that_regained_a_client() -> None:
    """A zone whose client comes back before the scheduler reverts keeps its staged rows."""
    dispatcher = Dispatcher()
    ask(dispatcher, 'g1', zone='gpu')
    dispatcher.stage(GPU, [('g1', '{"id": "g1"}')])
    dispatcher.forget('g1')
    dispatcher.demand['g2'] = (GPU, 1)  # a new client registered between forget and the revert
    assert dispatcher.take_unstaged() == []
    assert dispatcher.staged(GPU) == 1


@mark.unit
def test_held_group_logs_each_zone_once(task_db: Engine, records: List[logging.LogRecord]) -> None:
    """A group held by tasks in a zone no client serves is logged once per group and zone."""
    Zone.ensure('cpu')
    default_ids = add_tasks(2, group=0)
    cpu_ids = add_tasks(1, CPU, group=0)
    add_tasks(1, group=1)
    dispatcher = Dispatcher(bundlesize=2)
    scheduler = scheduler_for(dispatcher)
    assert scheduler.load_bundle() is SchedulerState.PACK
    scheduler.pack_bundle()
    assert ids(ask(dispatcher, 'd1', size=5)) == default_ids
    for task_id in default_ids:
        Task.update(task_id, exit_status=0, completion_time=Task.from_id(task_id).schedule_time)
    for _ in range(3):
        assert scheduler.load_bundle() is SchedulerState.LOAD
    held = [r.getMessage() for r in records if r.getMessage().startswith('Task group 0 waiting on zone')]
    assert held == ['Task group 0 waiting on zone cpu: 1 tasks, no client serving it']
    assert [r.levelno for r in records if r.getMessage().startswith('Task group')] == [logging.INFO]
    assert scheduler.current_group == 0
    assert len(cpu_ids) == 1


@mark.integration
@mark.parametrize('argv, message', [
    (['hs', 'submit', '--zone', 'gpu', '-q', 'echo', 'a'], 'Cannot use --zone with --queue'),
    (['hs', 'cluster', '-', '--zone', 'gpu', '--no-db'], 'Cannot use --zone without database'),
    (['hs', 'submit', '--zone', '  ', 'echo', 'a'], 'Zone name must not be empty'),
    (['hs', 'submit', '--zone', 'a\x07b', 'echo', 'a'], 'Zone name must not contain control characters'),
    (['hs', 'task', 'submit', '--zone', '', '--', 'echo', 'a'], 'Zone name must not be empty'),
    (['hs', 'cluster', '-', '--zone', ''], 'Zone name must not be empty'),
    (['hs', 'client', '--zone', '\t'], 'Zone name must not be empty'),
    (['hs', 'client', '--zone', 'x\ny'], 'Zone name must not contain control characters'),
])
def test_zone_usage_errors(temp_site: Path, argv: List[str], message: str) -> None:
    """--zone needs a database, and a zone name must be non-empty printable text."""
    rc, stdout, stderr = main(argv)
    assert rc == exit_status.bad_argument, stderr
    assert message in stderr


@mark.integration
def test_submit_stamps_zone(temp_site: Path) -> None:
    """`hs submit --zone` and `hs task submit --zone` stamp the zone; `--zone default` is the default zone."""
    assert main(['hs', 'submit', '--zone', ' gpu ', 'echo', 'a'])[0] == exit_status.success
    assert main(['hs', 'task', 'submit', '--zone', 'gpu', '--', 'echo', 'b'])[0] == exit_status.success
    assert main(['hs', 'submit', '--zone', 'default', 'echo', 'c'])[0] == exit_status.success
    assert main(['hs', 'submit', 'echo', 'd'])[0] == exit_status.success
    with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
        rows = dict(db.execute('SELECT args, zone FROM task').fetchall())
        names = dict(db.execute('SELECT id, name FROM zone').fetchall())
    assert rows == {'echo a': GPU, 'echo b': GPU, 'echo c': None, 'echo d': None}
    assert names[GPU] == 'gpu'
