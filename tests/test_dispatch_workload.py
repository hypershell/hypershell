# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Workloads through the request path on loopback: the cluster, a server with plain clients, queue submit.

Every process runs in its own process group with a bound, and the group is killed when the test ends.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Iterator, List, Optional, Any

# Standard libs
import os
import re
import time
import signal
import secrets
import sqlite3
import subprocess
from pathlib import Path
from collections import Counter

# External libs
import psutil
from pytest import mark, fixture

# Internal libs
from tests import create_taskfile


DEADLINE = 120
RECEIVED = re.compile(r'DEBUG \[hypershell\.client\] Received (\d+) tasks')
RUNNING = re.compile(r'INFO \[hypershell\.client\] Running task \(([0-9a-f-]+)\)')


class Group:
    """Processes started in their own process groups, all killed on exit."""

    def __init__(self, site: Path) -> None:
        self.site = site
        self.procs: List[subprocess.Popen] = []

    def start(self, name: str, *args: str, env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
        stdout = open(self.site / f'{name}.out', 'w')
        stderr = open(self.site / f'{name}.log', 'w')
        proc = subprocess.Popen(['hs', *args], cwd=self.site, stdout=stdout, stderr=stderr,
                                env={**os.environ, **(env or {})}, start_new_session=True)
        stdout.close()
        stderr.close()
        self.procs.append(proc)
        return proc

    def wait(self, proc: subprocess.Popen, deadline: float) -> int:
        return proc.wait(timeout=max(1.0, deadline - time.monotonic()))

    def kill(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()

    def text(self, name: str, suffix: str = 'log') -> str:
        return (self.site / f'{name}.{suffix}').read_text()


@fixture
def group(temp_site: Path) -> Iterator[Group]:
    """A process group manager on the test site."""
    procs = Group(Path(temp_site))
    try:
        yield procs
    finally:
        procs.kill()


def listening(pid: int, port: int) -> bool:
    """True once a process in the tree rooted at `pid` listens on `port` (no probe connection)."""
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return False
    for proc in procs:
        try:
            for conn in proc.net_connections(kind='tcp'):
                if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                    return True
        except psutil.Error:
            pass
    return False


def wait_listening(proc: subprocess.Popen, port: int, group: Group, name: str) -> None:
    """Wait until the server listens; fail with its log if it exits or never does."""
    deadline = time.monotonic() + 30
    while not listening(proc.pid, port):
        assert proc.poll() is None, group.text(name)
        assert time.monotonic() < deadline, 'server never listened'
        time.sleep(0.05)


def task_rows() -> List[Dict[str, Any]]:
    """Every task row."""
    with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM task')]


def received(text: str) -> List[int]:
    """Sizes of the bundles a client log says it received."""
    return [int(size) for size in RECEIVED.findall(text)]


@mark.integration
@mark.parametrize('tls', [True, False])
def test_cluster_default_path(group: Group, tls: bool) -> None:
    """A plain cluster runs every task once to success, TLS on and off."""
    taskfile = create_taskfile(group.site, [f'echo {n}' for n in range(200)])
    proc = group.start('cluster', 'cluster', str(taskfile), '-N4', *([] if tls else ['--no-tls']))
    assert group.wait(proc, time.monotonic() + DEADLINE) == 0, group.text('cluster')
    assert sorted(group.text('cluster', 'out').split()) == sorted(str(n) for n in range(200))
    rows = task_rows()
    assert len(rows) == 200
    assert all(row['exit_status'] == 0 and row['completion_time'] is not None for row in rows)
    assert len(RUNNING.findall(group.text('cluster'))) == 200


@mark.integration
def test_cluster_without_database(group: Group) -> None:
    """An in-memory cluster runs every task once."""
    taskfile = create_taskfile(group.site, [f'echo {n}' for n in range(200)])
    proc = group.start('cluster', 'cluster', str(taskfile), '-N4', '--no-db')
    assert group.wait(proc, time.monotonic() + DEADLINE) == 0, group.text('cluster')
    assert sorted(group.text('cluster', 'out').split()) == sorted(str(n) for n in range(200))
    assert len(RUNNING.findall(group.text('cluster'))) == 200


def grouped(count: int) -> List[str]:
    """Three groups of `count` tasks each."""
    return [f'echo {g}-{n}  #HYPERSHELL: group:{g}' for g in range(3) for n in range(count)]


@mark.integration
def test_server_bundlesize_serves_plain_clients(group: Group) -> None:
    """Clients that advertise nothing get bundles of the server's size; every task runs once, in group order."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    taskfile = create_taskfile(group.site, grouped(200))
    submit = group.start('submit', 'submit', str(taskfile), '-b', '1000')
    assert group.wait(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', '-b', '100', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    clients = [group.start(f'client-{n}', 'client', '-N4', '-H', 'localhost', '-p', str(port), '-k', key, '-T', '30')
               for n in range(2)]
    assert group.wait(server, deadline) == 0, group.text('server')
    for n, client in enumerate(clients):
        assert group.wait(client, deadline) == 0, group.text(f'client-{n}')
    sizes = [size for n in range(2) for size in received(group.text(f'client-{n}'))]
    rows = task_rows()
    assert len(rows) == 600 and all(row['exit_status'] == 0 for row in rows)
    assert sizes and max(sizes) <= 100 and sum(sizes) == len(rows)
    assert max(sizes) > 1
    runs = Counter(task_id for n in range(2) for task_id in RUNNING.findall(group.text(f'client-{n}')))
    assert set(runs) == {row['id'] for row in rows} and set(runs.values()) == {1}
    by_group = {g: [row for row in rows if row['group'] == g] for g in range(3)}
    assert max(row['completion_time'] for row in by_group[0]) <= min(row['schedule_time'] for row in by_group[1])
    assert max(row['completion_time'] for row in by_group[1]) <= min(row['schedule_time'] for row in by_group[2])


@mark.integration
def test_configured_bundlesize_is_advertised(group: Group) -> None:
    """A client whose size comes from the environment advertises it, so the server cuts to that size."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    taskfile = create_taskfile(group.site, [f'echo {n}' for n in range(300)])
    submit = group.start('submit', 'submit', str(taskfile), '-b', '1000')
    assert group.wait(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', '-b', '100', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    client = group.start('client', 'client', '-N4', '-H', 'localhost', '-p', str(port), '-k', key, '-T', '30',
                         env={'HYPERSHELL_SUBMIT_BUNDLESIZE': '3'})
    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(client, deadline) == 0, group.text('client')
    sizes = received(group.text('client'))
    assert sizes and max(sizes) <= 3 and sum(sizes) == 300


@mark.integration
def test_queue_submit_to_in_memory_server(group: Group) -> None:
    """Bundles submitted to an in-memory server's queue are re-cut to the client's size and run once.

    An in-memory server with no source of its own tells its clients to stop once its queue is
    drained, so the queue is deep enough to take every submitted bundle before the client starts.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--no-db', '-b', '5', '-H', 'localhost', '-p', str(port), '-k', key,
                         env={'HYPERSHELL_SERVER_QUEUESIZE': '100'})
    wait_listening(server, port, group, 'server')
    taskfile = create_taskfile(group.site, [f'echo q{n}' for n in range(50)])
    submit = group.start('submit', 'submit', '-q', str(taskfile), '-b', '7', '-w', '1',
                         '-H', 'localhost', '-p', str(port), '-k', key)
    assert group.wait(submit, deadline) == 0, group.text('submit')
    client = group.start('client', 'client', '-N4', '-b', '3', '-H', 'localhost', '-p', str(port), '-k', key,
                         '-T', '20')
    assert group.wait(client, deadline) == 0, group.text('client')
    assert group.wait(server, deadline) == 0, group.text('server')
    assert sorted(group.text('client', 'out').split()) == sorted(f'q{n}' for n in range(50))
    assert len(RUNNING.findall(group.text('client'))) == 50
    sizes = received(group.text('client'))
    assert sizes and max(sizes) <= 3 and sum(sizes) == 50


def stamp(text: str) -> float:
    """Seconds since the epoch of a stored time."""
    from datetime import datetime
    return datetime.fromisoformat(text).timestamp()


@mark.integration
def test_retry_and_group_advance_latency(group: Group) -> None:
    """A retry and each group advance are scheduled within the scheduler's first back-off step.

    The workload is the parity one: three groups, one task failing on its first attempt. 2.8.3
    schedules each next step about 1 s (its first back-off sleep) after the result that unblocks
    it; a wake that finds nothing must not push the scheduler into its next, 2 s, step.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    marker = group.site / 'retry.marker'
    lines = ['echo a0  #HYPERSHELL: group:0', 'echo a1  #HYPERSHELL: group:0', 'echo a2  #HYPERSHELL: group:0',
             f'test -f {marker} || {{ touch {marker}; exit 3; }}  #HYPERSHELL: group:0',
             'echo b0  #HYPERSHELL: group:1', 'echo b1  #HYPERSHELL: group:1', 'echo b2  #HYPERSHELL: group:1',
             'echo c0  #HYPERSHELL: group:2', 'echo c1  #HYPERSHELL: group:2']
    taskfile = create_taskfile(group.site, lines)
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, time.monotonic() + DEADLINE) == 0, group.text('submit')
    deadline = time.monotonic() + DEADLINE
    server = group.start('server', 'server', '--restart', '-r', '1', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    clients = [group.start(f'client-{n}', 'client', '-H', 'localhost', '-p', str(port), '-k', key, '-T', '30')
               for n in range(2)]
    assert group.wait(server, deadline) == 0, group.text('server')
    for n, client in enumerate(clients):
        assert group.wait(client, deadline) == 0, group.text(f'client-{n}')
    rows = task_rows()
    failed, = [row for row in rows if row['attempt'] == 1 and row['exit_status'] == 3]
    retry, = [row for row in rows if row['attempt'] == 2]
    gaps = {'retry': stamp(retry['schedule_time']) - stamp(failed['completion_time'])}
    for g in (0, 1):
        done = max(stamp(row['completion_time']) for row in rows if row['group'] == g)
        start = min(stamp(row['schedule_time']) for row in rows if row['group'] == g + 1)
        gaps[f'group {g + 1}'] = start - done
    assert all(0 <= gap <= 1.5 for gap in gaps.values()), gaps
