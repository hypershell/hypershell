# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A superseded relay retires: it hands back what no site client has received, and finishes.

The relay database lists the tasks it holds that no site client has received (no local schedule
time, an upstream stamp) and, once a hand-back is acknowledged, deletes exactly those still unstarted
with their stamps. A relay hands them back over its own upstream connection, by zone identity and in
chunks; the upstream scheduler requeues them for another client. An upstream server without the call,
or one that refuses it, leaves them held (the server's eviction of the relay puts them back, as
before); a hand-back lost with the link stays due and goes through once the link is back.

End to end, an upstream server autoscaling with relay sites and two relays at one site, each a
process: the older relay, holding a task its site client stopped asking for, reads that the newer one
superseded it, hands that task back, lets its client finish, sends its final heartbeat and exits 0 by
itself; every task runs once and the server evicts nothing.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Optional

# Standard libs
import os
import re
import time
import json
import signal
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

# External libs
import pytest
from pytest import mark, MonkeyPatch
from sqlalchemy import update
from sqlalchemy.engine import Engine

# Internal libs
import hypershell.relay as relay_module
from hypershell.core import queue as queue_module
from hypershell.core.queue import QueueServer, QueueClient
from hypershell.core.reconnect import LinkDown, as_link
from hypershell.core.topology import handback_reply, read_handback
from hypershell.core.uuid import uuid
from hypershell.data.model import Task
from hypershell.relay import RelayThread, store
from hypershell.relay.store import RelayTask
from hypershell.server import Scheduler, SchedulerState
from tests.conftest import free_port
from tests.test_zone_model import task_db, add_tasks  # noqa: fixture
from tests.test_relay_connector import relay_db, dispatched  # noqa: fixture
from tests.test_topology_server import loopback, work, row
from tests.test_dispatch_workload import Group, group  # noqa: fixture
from tests.test_relay_workload import loopback_launcher


RELAY = str(uuid())
OTHER_ZONE = str(uuid())
NEWER = str(uuid())


def deliver(engine: Engine, ids: List[str]) -> None:
    """Mark `ids` received by a site client, as the relay-local server does when one takes them."""
    table = Task.__table__
    with engine.begin() as conn:
        conn.execute(update(table).where(table.c.id.in_(ids))
                     .values(schedule_time=datetime.now().astimezone(), client_id=str(uuid())))


def complete(engine: Engine, ids: List[str]) -> None:
    """Mark `ids` completed at the site."""
    table = Task.__table__
    with engine.begin() as conn:
        conn.execute(update(table).where(table.c.id.in_(ids))
                     .values(completion_time=datetime.now().astimezone(), exit_status=0))


def bare_relay(link: Any) -> RelayThread:
    """A relay with only its upstream link: enough for a hand-back."""
    relay = RelayThread.__new__(RelayThread)
    relay.link = link
    relay.handback_due = True
    return relay


# --- What a relay holds unstarted --------------------------------------------------------------------


@mark.unit
def test_unstarted_lists_only_what_no_site_client_received(relay_db: Engine) -> None:
    """Held tasks with no local schedule time and an upstream stamp are listed, with the identity that
    confirmed them and the stamp verbatim; a delivered, a completed or an unstamped task is not."""
    records = dispatched('echo a', 'echo b', 'echo c', 'echo d')
    store.insert_received(relay_db, records[:3], identity=RELAY)
    unstamped = {**records[3], 'schedule_time': None, 'server_id': None, 'server_host': None}
    store.insert_received(relay_db, [unstamped], identity=OTHER_ZONE)
    deliver(relay_db, [records[0]['id']])
    complete(relay_db, [records[1]['id']])
    assert store.unstarted(relay_db) == [(records[2]['id'], RELAY, records[2]['schedule_time'])]


@mark.unit
def test_drop_handed_back_deletes_only_unstarted_tasks_and_their_stamps(relay_db: Engine) -> None:
    """Only a task still unstarted here goes, with its stamp; one a site client took meanwhile, or one
    completed, stays with its stamp, and a task named twice or not held changes nothing."""
    records = dispatched('echo a', 'echo b', 'echo c')
    ids = store.insert_received(relay_db, records, identity=RELAY)
    deliver(relay_db, [ids[1]])
    complete(relay_db, [ids[2]])
    assert store.drop_handed_back(relay_db, [*ids, str(uuid())]) == 1
    assert store.drop_handed_back(relay_db, ids) == 0
    with relay_db.connect() as conn:
        tasks = set(conn.execute(Task.__table__.select().with_only_columns(Task.__table__.c.id)).scalars())
        stamps = set(conn.execute(RelayTask.__table__.select().with_only_columns(RelayTask.__table__.c.id)).scalars())
    assert tasks == stamps == {ids[1], ids[2]}
    assert store.count_held(relay_db) == 2 and store.unstarted(relay_db) == []


# --- The hand-back --------------------------------------------------------------------------------------


@pytest.fixture
def held(task_db: Engine, relay_db: Engine, monkeypatch: MonkeyPatch):
    """Five upstream tasks the relay holds under its identity, two taken by its site clients; an upstream
    queue server with a scheduler, and a connection to it as the relay's link."""
    monkeypatch.setattr(relay_module, 'engine', relay_db)
    ids = add_tasks(5)
    with QueueServer(loopback()) as server:
        server.dispatcher.configure(bundlesize=10)
        scheduler = Scheduler(queue=server, bundlesize=10, forever_mode=True)
        assert scheduler.start() is SchedulerState.LOAD
        assert scheduler.load_bundle() is SchedulerState.PACK
        scheduler.pack_bundle()
        client = QueueClient(loopback(server.address[1]))
        client.connect()
        records = work(client.dispatcher, RELAY, 5)
        assert {record['id'] for record in records} == set(ids)
        Task.confirm_all(ids, RELAY, 'relay-host')
        store.insert_received(relay_db, records, identity=RELAY)
        deliver(relay_db, ids[:2])
        yield SimpleNamespace(ids=ids, server=server, scheduler=scheduler, client=client, relay_db=relay_db,
                              stamps={record['id']: record['schedule_time'] for record in records})


def messages(caplog, start: str) -> List[str]:
    """Logged messages that start with `start`, in order."""
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith(start)]


@mark.integration
def test_hand_back_requeues_upstream_in_chunks_and_drops_what_it_handed(held, monkeypatch, caplog) -> None:
    """The three tasks no site client took go back in chunks of the bound, each named with the stamp it was
    sent under; the upstream scheduler requeues them for another client, and the relay holds them no more.
    The two its site clients took stay held, to run there and go back as results."""
    monkeypatch.setattr(relay_module, 'MAX_HANDBACK_TASKS', 2)
    sent = []
    handback = held.client.dispatcher.handback
    dispatcher = SimpleNamespace(handback=lambda text: sent.append(text) or handback(text))
    link = as_link(SimpleNamespace(dispatcher=dispatcher))
    relay = bare_relay(link)
    with caplog.at_level('INFO'):
        relay.hand_back()
        assert held.scheduler.load_bundle() is SchedulerState.PACK
    held.scheduler.pack_bundle()
    assert not relay.handback_due
    unstarted = held.ids[2:]
    assert [read_handback(text) for text in sent] == [
        (RELAY, [(task_id, held.stamps[task_id]) for task_id in unstarted[:2]]),
        (RELAY, [(task_id, held.stamps[task_id]) for task_id in unstarted[2:]])]
    assert messages(caplog, 'Handed back') == [f'Handed back 2 tasks no site client started ({RELAY})',
                                               f'Handed back 1 tasks no site client started ({RELAY})']
    assert messages(caplog, 'Requeued') == [f'Requeued 2 tasks handed back by relay ({RELAY})',
                                            f'Requeued 1 tasks handed back by relay ({RELAY})']
    assert store.unstarted(held.relay_db) == [] and store.count_held(held.relay_db) == 2
    assert store.count_backlog(held.relay_db) == 2
    assert {record['id'] for record in work(held.client.dispatcher, NEWER, 10)} == set(unstarted)
    assert all(row(task_id).client_id == RELAY for task_id in held.ids[:2])


@mark.integration
def test_hand_back_to_a_server_without_the_call_leaves_the_tasks_to_eviction(held, monkeypatch, caplog) -> None:
    """A server of the earlier build has no hand-back: the relay keeps what it holds, says so once, and
    tries no more; upstream nothing changes."""
    monkeypatch.setattr(queue_module, 'DISPATCH_EXPOSED', ('request', 'takes_submissions', 'topology'))
    with QueueServer(loopback()) as old:
        client = QueueClient(loopback(old.address[1]))
        client.connect()
        assert getattr(client.dispatcher, 'handback', None) is None
        relay = bare_relay(as_link(client))
        with caplog.at_level('WARNING'):
            relay.hand_back()
    assert not relay.handback_due
    assert messages(caplog, 'Could not hand back') == [
        'Could not hand back 3 tasks no site client started (the upstream server takes none) - '
        'the upstream server will put them back when it evicts this relay']
    assert len(store.unstarted(held.relay_db)) == 3 and store.count_held(held.relay_db) == 5
    assert all(row(task_id).client_id == RELAY for task_id in held.ids)


@mark.integration
def test_hand_back_refused_leaves_the_tasks_to_eviction(held, caplog) -> None:
    """A server that answers with an error (here one with no database) refuses the hand-back: the tasks
    stay held and nothing is requeued upstream."""
    held.server.dispatcher.configure(in_memory=True)
    relay = bare_relay(as_link(held.client))
    with caplog.at_level('WARNING'):
        relay.hand_back()
    assert not relay.handback_due
    refused, = messages(caplog, 'Could not hand back')
    assert refused.startswith('Could not hand back 3 tasks no site client started (Hand-back refused (')
    assert len(store.unstarted(held.relay_db)) == 3 and store.count_held(held.relay_db) == 5
    assert held.server.dispatcher.take_handbacks() == []


class Flaky:
    """A link that is down until told otherwise, then makes calls on `client`."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.down = True

    def call(self, role: str, fn: Any, deadline: Optional[float] = None) -> Any:  # noqa: unused
        if self.down:
            raise LinkDown('down')
        return fn(self.client)


@mark.unit
def test_hand_back_lost_with_the_link_stays_due_and_goes_through_later(relay_db: Engine, monkeypatch, caplog) -> None:
    """A hand-back the link loses keeps every task and stays due, with no warning; once the link is back it
    goes through and the tasks are dropped."""
    monkeypatch.setattr(relay_module, 'engine', relay_db)
    ids = store.insert_received(relay_db, dispatched('echo a', 'echo b'), identity=RELAY)
    received = []
    link = Flaky(SimpleNamespace(dispatcher=SimpleNamespace(
        handback=lambda text: received.append(read_handback(text)) or handback_reply(len(read_handback(text)[1])))))
    relay = bare_relay(link)
    with caplog.at_level('DEBUG', logger='hypershell.relay'):
        relay.hand_back()
    assert relay.handback_due and len(store.unstarted(relay_db)) == 2 and received == []
    assert not [record for record in caplog.records if record.levelname == 'WARNING']
    link.down = False
    relay.hand_back()
    assert not relay.handback_due and store.unstarted(relay_db) == [] and store.count_held(relay_db) == 0
    assert [sorted(task_id for task_id, _ in tasks) for _, tasks in received] == [sorted(ids)]


# --- End to end ----------------------------------------------------------------------------------------

KEY = 'relay-retire-upstream-key-0123456789'
DEADLINE = 120.0
IDENTITY = re.compile(r'Upstream identity ([0-9a-f-]+) for zone')


def relay_config(site: Path, name: str, size: int) -> Path:
    """The configuration of relay `name` at site A, with `size` clients and a short idle timeout."""
    path = site / f'{name}.toml'
    path.write_text(f'[relay]\nsite = "A"\ntimeout = 2\nlauncher = "{loopback_launcher(site)}"\n'
                    f'init = {size}\nmax = {size}\n')
    return path


def start_relay(group: Group, name: str, port: int) -> subprocess.Popen:
    """Start relay `name` at site A against the upstream server on `port`, beating every second."""
    return group.start(name, 'relay', '-H', '127.0.0.1', '-p', str(port), '--no-tls',
                       '--bind', '127.0.0.1', '--port', str(free_port()),
                       env={'HYPERSHELL_CONFIG_FILE': str(relay_config(group.site, name, 1)),
                            'HYPERSHELL_RELAY_DATABASE': str(group.site / f'{name}.db'),
                            'HYPERSHELL_RELAY_UPSTREAM_AUTH': KEY,
                            'HYPERSHELL_RELAY_UPSTREAM_HEARTRATE': '1',
                            'HYPERSHELL_SERVER_TLS_ENABLED': 'false',
                            'HYPERSHELL_AUTOSCALE_PERIOD': '1'})


def until(condition: Any, what: str, deadline: float = DEADLINE) -> Any:
    """Poll `condition` until it returns something true; fail naming `what` after `deadline` seconds."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if found := condition():
            return found
        time.sleep(0.2)
    pytest.fail(f'Timed out waiting for {what}')


def undelivered(path: Path) -> int:
    """Tasks held at a relay, incomplete, that no site client has taken (read-only)."""
    if not path.exists():
        return 0
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1) as db:
            return db.execute('SELECT count(*) FROM task WHERE completion_time IS NULL '
                              'AND client_id IS NULL').fetchone()[0]
    except sqlite3.Error:
        return 0


def held_for(path: Path, seconds: float) -> Any:
    """A condition: true once a relay has held a task no site client took for `seconds` running."""
    since: List[Optional[float]] = [None]

    def condition() -> bool:
        if undelivered(path) == 0:
            since[0] = None
            return False
        since[0] = since[0] or time.monotonic()
        return time.monotonic() - since[0] >= seconds
    return condition


@mark.integration
def test_a_superseded_relay_hands_back_finishes_and_exits(group: Group) -> None:
    """Relay A's one site client takes two tasks and passes its idle timeout running them, while A pulls one
    more for it. Relay A2 then registers site A: A reads that it is superseded, hands that task back, lets
    its client finish, sends its final heartbeat and exits 0 by itself. A2 runs the rest; every task runs
    once, completes upstream, and the server evicts neither relay."""
    runs = group.site / 'runs.log'
    tasks = ''.join(f'echo "$TASK_ID" >> {runs}; sleep 4\n' for _ in range(5))
    env = {'HYPERSHELL_SERVER_AUTH': KEY}
    subprocess.run(['hs', 'submit', '-'], input=tasks.encode(), env={**os.environ, **env}, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port = free_port()
    server = group.start('server', 'server', '--autoscaling=fixed', '-P', '1', '--no-tls',
                         '-H', '127.0.0.1', '-p', str(port), env={**env, 'HYPERSHELL_SERVER_EVICT': '120'})
    relay_a = start_relay(group, 'relay-A', port)
    identity = until(lambda: IDENTITY.search(group.text('relay-A')), "relay A's identity").group(1)
    until(held_for(group.site / 'relay-A.db', 1.5), 'a task at A that no site client takes')
    relay_a2 = start_relay(group, 'relay-A2', port)

    assert group.wait(relay_a, time.monotonic() + DEADLINE) == 0
    until(lambda: subprocess.run(['hs', 'list', '--remaining', '--count'], capture_output=True, text=True
                                 ).stdout.strip() == '0', 'every task to complete')
    relay_a2.send_signal(signal.SIGUSR1)
    assert group.wait(relay_a2, time.monotonic() + DEADLINE) == 0
    server.send_signal(signal.SIGTERM)
    server.wait(timeout=30)

    a_log, server_log = group.text('relay-A'), group.text('server')
    assert a_log.count('Superseded at site A by a newer relay') == 1
    handed = sum(int(count) for count in re.findall(rf'Handed back (\d+) tasks no site client started \({identity}\)',
                                                    a_log))
    requeued = sum(int(count) for count in re.findall(rf'Requeued (\d+) tasks handed back by relay \({identity}',
                                                      server_log))
    assert handed >= 1 and requeued == handed
    assert f'Upstream heartbeat - final ({identity})' in a_log
    assert re.search(rf'Relay site left \(site: A, zone: default\) \([^:]*: {identity}\)', server_log)
    assert 'Evicting client' not in server_log
    assert 'Terminating site client' not in a_log
    assert not re.search(r'Topology \S+ \(zone: [^)]*\) - launching [1-9]', a_log.split('Superseded at site A')[1])
    ran = runs.read_text().split()
    assert len(ran) == 5 and len(set(ran)) == 5
    listed = json.loads(subprocess.run(['hs', 'list', '--json'], capture_output=True, text=True, check=True).stdout)
    assert len(listed) == 5 and all(task['exit_status'] == 0 for task in listed)
