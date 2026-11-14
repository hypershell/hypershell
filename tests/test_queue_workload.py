# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A grouped workload with a retried failure, run by a server and two clients over loopback."""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Any

# Standard libs
import os
import time
import secrets
import sqlite3
from pathlib import Path
from subprocess import Popen, run, DEVNULL, TimeoutExpired

# External libs
import psutil
from pytest import mark

# Internal libs
from tests import create_taskfile


DEADLINE = 60


def hs(*args: str) -> List[str]:
    """Command line for the installed CLI."""
    return ['hs', *args]


def listening(pid: int, port: int) -> bool:
    """True once a process in the tree rooted at `pid` is listening on `port` (no probe connection)."""
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


def workload(site: Path) -> List[str]:
    """Three groups; one task in the first group fails on its first attempt only."""
    marker = site / 'retry.marker'
    return [
        'echo a0  #HYPERSHELL: group:0',
        'echo a1  #HYPERSHELL: group:0',
        'echo a2  #HYPERSHELL: group:0',
        f'test -f {marker} || {{ touch {marker}; exit 3; }}  #HYPERSHELL: group:0',
        'echo b0  #HYPERSHELL: group:1',
        'echo b1  #HYPERSHELL: group:1',
        'echo b2  #HYPERSHELL: group:1',
        'echo c0  #HYPERSHELL: group:2',
        'echo c1  #HYPERSHELL: group:2',
    ]


def task_rows(database: str) -> List[Dict[str, Any]]:
    """Every task row with the columns that define its final state."""
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            'SELECT id, args, "group", attempt, exit_status, retried, previous_id, next_id, '
            'schedule_time, completion_time FROM task')]


@mark.integration
def test_grouped_workload_with_retry(temp_site: Path) -> None:
    """Every task reaches its final state, no result is lost, and every process exits cleanly."""
    port = os.environ['HYPERSHELL_SERVER_PORT']
    os.environ['HYPERSHELL_SERVER_AUTH'] = secrets.token_hex(16)
    lines = workload(temp_site)
    taskfile = create_taskfile(temp_site, lines)
    assert run(hs('submit', str(taskfile)), cwd=temp_site, stdout=DEVNULL, stderr=DEVNULL).returncode == 0

    with open(temp_site / 'server.log', 'w') as log:
        server = Popen(hs('server', '--restart', '-r', '1', '-H', 'localhost', '-p', port),
                       cwd=temp_site, stdout=log, stderr=log)
    clients = []
    try:
        deadline = time.monotonic() + DEADLINE
        while not listening(server.pid, int(port)):
            assert server.poll() is None, (temp_site / 'server.log').read_text()
            assert time.monotonic() < deadline, 'server never listened'
            time.sleep(0.05)
        for index in range(2):
            with open(temp_site / f'client-{index}.log', 'w') as log:
                clients.append(Popen(hs('client', '-H', 'localhost', '-p', port, '-T', '30'),
                                     cwd=temp_site, stdout=log, stderr=log))
        start = time.monotonic()
        for proc in (server, *clients):
            assert proc.wait(timeout=max(1.0, DEADLINE - (time.monotonic() - start))) == 0
    except TimeoutExpired:
        raise AssertionError('a process did not exit within the deadline')
    finally:
        for proc in (server, *clients):
            if proc.poll() is None:
                proc.kill()

    rows = task_rows(os.environ['HYPERSHELL_DATABASE_FILE'])
    assert len(rows) == len(lines) + 1
    assert all(row['completion_time'] is not None for row in rows)

    retried = [row for row in rows if row['args'].startswith('test -f')]
    first, second = sorted(retried, key=lambda row: row['attempt'])
    assert (first['attempt'], first['exit_status'], first['retried']) == (1, 3, 1)
    assert (second['attempt'], second['exit_status'], second['retried']) == (2, 0, 0)
    assert first['next_id'] == second['id'] and second['previous_id'] == first['id']

    others = [row for row in rows if row not in retried]
    assert sorted(row['args'] for row in others) == sorted(line.split('  #')[0] for line in lines[:3] + lines[4:])
    assert all((row['attempt'], row['exit_status'], row['retried']) == (1, 0, 0) for row in others)

    by_group = {group: [row for row in rows if row['group'] == group] for group in (0, 1, 2)}
    assert max(row['completion_time'] for row in by_group[0]) <= min(row['schedule_time'] for row in by_group[1])
    assert max(row['completion_time'] for row in by_group[1]) <= min(row['schedule_time'] for row in by_group[2])
