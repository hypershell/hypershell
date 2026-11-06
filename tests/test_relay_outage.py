# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A relay rides through an upstream outage, and a restart, without losing a result.

An upstream server and a relay run as separate processes, and the relay dials the upstream server
through a TCP forwarder in this process. The forwarder passes traffic, closes every connection (and
each new one at once), or blackholes it (connections stay open and nothing moves, as with a stalled
host). The upstream server keeps its default eviction period (600 s), so an outage of a few seconds
never evicts the relay: results completed at the site during the outage must land upstream, as the
results of the assignments the relay confirmed, once the forwarder passes traffic again.

Tasks wait on a barrier file, so the relay holds them, confirmed upstream, before the outage begins,
and they complete during it.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Tuple

# Standard libs
import os
import time
import signal
import socket
import secrets
import threading
from pathlib import Path

# External libs
from pytest import mark

# Internal libs
from tests.conftest import free_port
from tests.test_dispatch_workload import Group, group, wait_listening  # noqa: fixture
from tests.test_relay_workload import (IDENTITY, STALE_OR_DUPLICATE, rows, until, waiting_tasks, running_tasks,
                                       held_upstream, alive, site_processes, loopback_launcher)
from tests import create_taskfile


DEADLINE = 180
TASKS = 4
CALL_DEADLINE = 3  # Seconds an upstream call may take before the relay takes its link as down.


class Forwarder:
    """A TCP forwarder on loopback in front of `target` with three modes: pass, close and blackhole."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.mode = 'pass'
        self.lock = threading.Lock()
        self.pairs: List[Tuple[socket.socket, socket.socket, threading.Event]] = []
        self.held: List[socket.socket] = []
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.closing = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        while not self.closing.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                mode = self.mode
                if mode == 'close':
                    conn.close()
                elif mode == 'blackhole':
                    self.held.append(conn)
                else:
                    try:
                        upstream = socket.create_connection(('127.0.0.1', self.target))
                    except OSError:
                        conn.close()
                        continue
                    stalled = threading.Event()
                    self.pairs.append((conn, upstream, stalled))
                    for src, dst in ((conn, upstream), (upstream, conn)):
                        threading.Thread(target=self.pump, args=(src, dst, stalled), daemon=True).start()

    @staticmethod
    def pump(src: socket.socket, dst: socket.socket, stalled: threading.Event) -> None:
        src.settimeout(0.1)
        while True:
            if stalled.is_set():
                if src.fileno() < 0:
                    return
                time.sleep(0.05)
                continue
            try:
                data = src.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                data = b''
            if not data:
                for sock in (src, dst):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
                return
            try:
                dst.sendall(data)
            except OSError:
                return

    def set_mode(self, mode: str) -> None:
        """Pass traffic, close every connection, or blackhole it; leaving a blackhole ends what it held."""
        with self.lock:
            self.mode = mode
            pairs = self.pairs
            if mode == 'blackhole':
                for _, _, stalled in pairs:
                    stalled.set()
                return
            self.pairs = [] if mode == 'close' else [pair for pair in pairs if not pair[2].is_set()]
            ended = [pair for pair in pairs if mode == 'close' or pair[2].is_set()]
            held, self.held = self.held, []
        for a, b, stalled in ended:
            stalled.set()
            for sock in (a, b):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        for sock in held:
            sock.close()

    def close(self) -> None:
        self.set_mode('close')
        self.closing.set()
        self.listener.close()


def start(group: Group) -> Tuple[Any, Forwarder, str]:
    """Submit the waiting tasks, start the upstream server (default eviction) and a relay behind a forwarder."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    barrier = group.site / 'barrier'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, TASKS))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    server = group.start('server', 'server', '--restart', '-b', '1', '-Q', '1',
                         '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    return server, Forwarder(port), key


def start_relay(group: Group, forwarder: Forwarder, key: str, name: str = 'relay') -> Any:
    """`hs relay` dialing upstream through the forwarder, with short call deadlines and backoff."""
    env = {'HYPERSHELL_RELAY_UPSTREAM_AUTH': key,
           'HYPERSHELL_RELAY_DATABASE': str(group.site / 'relay.db'),
           'HYPERSHELL_RELAY_BUNDLEWAIT': '1',
           'HYPERSHELL_RELAY_UPSTREAM_HEARTRATE': '1',
           'HYPERSHELL_RELAY_UPSTREAM_DEADLINE': str(CALL_DEADLINE),
           'HYPERSHELL_RELAY_UPSTREAM_BACKOFF': '2'}
    return group.start(name, 'relay', '-H', 'localhost', '-p', str(forwarder.port),
                       '--bind', '127.0.0.1', '--port', str(free_port()),
                       '-N', '2', '--launcher', str(loopback_launcher(group.site)), env=env)


def outbox(relay_db: Path) -> List[str]:
    """States of the results the relay holds."""
    return [row['state'] for row in rows(relay_db, 'SELECT state FROM relay_result')]


def hold_everything(group: Group, relay: Any, upstream: Path, deadline: float, name: str = 'relay') -> str:
    """Wait until the relay holds every task, confirmed upstream, with tasks running at its site."""
    identity = until(lambda: IDENTITY.search(group.text(name)), deadline, 'relay identity').group(1)
    barrier = group.site / 'barrier'
    until(lambda: running_tasks(relay, barrier) and len(held_upstream(upstream, identity)) == TASKS,
          deadline, 'every task confirmed to the relay and running at its site')
    return identity


def check_landed(group: Group, upstream: Path, relay_db: Path) -> None:
    """Every task completed upstream once, by a site client of the relay, and nothing stale or duplicate."""
    tasks = rows(upstream, 'SELECT * FROM task')
    leaves = {row['id'] for row in rows(relay_db, 'SELECT id FROM client')}
    assert len(tasks) == TASKS
    assert all(task['exit_status'] == 0 and task['completion_time'] is not None for task in tasks), tasks
    assert {task['client_id'] for task in tasks} <= leaves
    server_log = group.text('server')
    assert not any(text in server_log for text in STALE_OR_DUPLICATE)
    assert 'Evicting client' not in server_log


@mark.integration
@mark.parametrize('mode', ['close', 'blackhole'])
def test_partition_holds_results_and_they_land_after_restore(group: Group, mode: str) -> None:
    """Through a partition the site finishes its tasks into the outbox; once traffic passes again they land."""
    deadline = time.monotonic() + DEADLINE
    server, forwarder, key = start(group)
    upstream, relay_db = Path(os.environ['HYPERSHELL_DATABASE_FILE']), group.site / 'relay.db'
    try:
        relay = start_relay(group, forwarder, key)
        hold_everything(group, relay, upstream, deadline)

        cut = time.monotonic()
        forwarder.set_mode(mode)
        until(lambda: 'Lost connection to upstream server' in group.text('relay'), deadline, 'the link down')
        detected = time.monotonic() - cut
        assert detected < CALL_DEADLINE + 15, f'outage detected after {detected:.1f} s'

        (group.site / 'barrier').touch()
        until(lambda: outbox(relay_db) == ['pending'] * TASKS, deadline, 'every result held in the outbox')
        assert alive(relay.pid) and alive(server.pid)
        assert rows(upstream, 'SELECT count(*) AS n FROM task WHERE completion_time IS NULL') == [{'n': TASKS}]

        forwarder.set_mode('pass')
        assert group.wait(server, deadline) == 0, group.text('server')
        assert group.wait(relay, deadline) == 0, group.text('relay')
    finally:
        forwarder.close()
        (group.site / 'barrier').touch()

    check_landed(group, upstream, relay_db)
    relay_log = group.text('relay')
    assert 'Reconnected to upstream server' in relay_log
    assert 'Upstream heartbeat - final' in relay_log and 'not finishing upstream' not in relay_log
    assert outbox(relay_db) == ['returned'] * TASKS


@mark.integration
def test_relay_killed_during_a_partition_hands_over_its_results_after_a_restart(group: Group) -> None:
    """A relay SIGKILLed with results held in a partition, started again on its database, delivers them."""
    deadline = time.monotonic() + DEADLINE
    server, forwarder, key = start(group)
    upstream, relay_db = Path(os.environ['HYPERSHELL_DATABASE_FILE']), group.site / 'relay.db'
    try:
        first = start_relay(group, forwarder, key)
        hold_everything(group, first, upstream, deadline)
        forwarder.set_mode('close')
        until(lambda: 'Lost connection to upstream server' in group.text('relay'), deadline, 'the link down')
        (group.site / 'barrier').touch()
        until(lambda: outbox(relay_db) == ['pending'] * TASKS, deadline, 'every result held in the outbox')

        site = [proc.pid for proc in site_processes(first)]
        os.killpg(first.pid, signal.SIGKILL)
        assert first.wait(timeout=30) == -signal.SIGKILL
        until(lambda: not any(alive(pid) for pid in site), deadline, 'the first site gone')

        # Started again while the partition holds: it waits for the upstream server, then delivers.
        second = start_relay(group, forwarder, key, name='relay-again')
        until(lambda: 'Cannot reach upstream server' in group.text('relay-again'), deadline, 'a refused start')
        assert alive(second.pid) and outbox(relay_db) == ['pending'] * TASKS
        forwarder.set_mode('pass')
        until(lambda: rows(upstream, 'SELECT count(*) AS n FROM task WHERE completion_time IS NOT NULL')
              == [{'n': TASKS}], deadline, 'every result landed upstream')
        assert alive(server.pid)  # It waits on the first relay's registration (default eviction).
    finally:
        forwarder.close()
        (group.site / 'barrier').touch()

    check_landed(group, upstream, relay_db)
    second_log = group.text('relay-again')
    assert 'Connected to upstream server' in second_log
    until(lambda: outbox(relay_db) == ['returned'] * TASKS, deadline, 'the outbox marked returned')
