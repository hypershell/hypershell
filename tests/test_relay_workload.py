# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Relays end to end: an upstream server and relays as separate processes over loopback.

The upstream server schedules a grouped workload from its database, with one retry allowed. A relay
connects to it as an ordinary client, launches its own site clients on this host and runs the tasks
there. Site clients dial the relay by host name, as they would from another node; here the launcher
(a wrapper around `env`) points them at loopback instead, since a host's own name need not resolve
to an address its firewall lets through. Every task must complete upstream exactly once per attempt,
attributed to the site client that ran it, never to the relay; the relay must hand every result back
and end its upstream registration cleanly, and both processes must exit on their own.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Set

# Standard libs
import os
import re
import time
import secrets
import sqlite3
from pathlib import Path
from collections import Counter

# External libs
from pytest import mark

# Internal libs
from hypershell.core.logging import HOSTNAME
from tests.conftest import free_port
from tests.test_dispatch_workload import Group, group, wait_listening, RUNNING  # noqa: fixture
from tests import create_taskfile


DEADLINE = 240
GROUPS = 3
PER_GROUP = 6
IDENTITY = re.compile(r'Upstream identity ([0-9a-f-]+) for zone (\S+)')
STALE_OR_DUPLICATE = ('Refused stale result', 'Ignored duplicate result')


def workload() -> List[str]:
    """Grouped tasks; one task in group 0 fails on its first attempt only."""
    lines = [f'echo {g}-{n}  #HYPERSHELL: group:{g}' for g in range(GROUPS) for n in range(PER_GROUP)]
    lines.append('echo retry; test "$TASK_ATTEMPT" -gt 1 || exit 3  #HYPERSHELL: group:0')
    return lines


def rows(path: Path, sql: str) -> List[Dict[str, Any]]:
    """Rows of a SQLite file, as dictionaries."""
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql)]


LOOPBACK_LAUNCHER = """\
#!/bin/sh
# Launch a site client with env, dialing the relay on loopback rather than by host name.
previous=
for arg; do
    shift
    if [ "$previous" = "-H" ]; then set -- "$@" 127.0.0.1; else set -- "$@" "$arg"; fi
    previous=$arg
done
exec env "$@"
"""


def loopback_launcher(site: Path) -> Path:
    """The site client launcher for these tests (see the module notes)."""
    path = site / 'loopback-launcher'
    if not path.exists():
        path.write_text(LOOPBACK_LAUNCHER)
        path.chmod(0o755)
    return path


def start_relay(group: Group, name: str, upstream_port: int, key: str, size: int) -> Any:
    """Start `hs relay` on the site with its own database, launching `size` site clients."""
    env = {'HYPERSHELL_RELAY_UPSTREAM_AUTH': key,
           'HYPERSHELL_RELAY_DATABASE': str(group.site / f'{name}.db'),
           'HYPERSHELL_RELAY_BUNDLEWAIT': '1'}
    return group.start(name, 'relay', '-H', 'localhost', '-p', str(upstream_port),
                       '--bind', '127.0.0.1', '--port', str(free_port()),
                       '-N', str(size), '--launcher', str(loopback_launcher(group.site)), env=env)


@mark.integration
def test_one_relay_end_to_end(group: Group) -> None:
    """A grouped workload with a retry runs wholly at one relay's site and lands upstream, attributed to leaves."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    taskfile = create_taskfile(group.site, workload())
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')

    server = group.start('server', 'server', '--restart', '-r', '1', '-b', '4', '-Q', '1',
                         '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, size=2)

    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(relay, deadline) == 0, group.text('relay')

    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    relay_log, server_log = group.text('relay'), group.text('server')
    identity, zone = IDENTITY.search(relay_log).groups()
    assert zone == 'default'

    # Every task completed upstream, once per attempt, and the one failure retried once.
    tasks = rows(upstream, 'SELECT * FROM task')
    assert len(tasks) == GROUPS * PER_GROUP + 2
    assert all(task['completion_time'] is not None for task in tasks)
    failed = [task for task in tasks if task['exit_status'] != 0]
    assert [(task['exit_status'], task['attempt'], task['retried']) for task in failed] == [(3, 1, 1)]
    retry, = [task for task in tasks if task['attempt'] == 2]
    assert retry['previous_id'] == failed[0]['id'] and retry['exit_status'] == 0
    assert max(task['attempt'] for task in tasks) == 2

    # Attributed to the site clients that ran them, never to the relay.
    leaves: Set[str] = {row['id'] for row in rows(group.site / 'relay.db', 'SELECT id FROM client')}
    assert 2 <= len(leaves)
    assert identity not in leaves
    assert {task['client_id'] for task in tasks} <= leaves
    assert {task['client_host'] for task in tasks} == {HOSTNAME}
    runs = Counter(RUNNING.findall(relay_log))
    assert set(runs) == {task['id'] for task in tasks} and set(runs.values()) == {1}

    # Groups ran in order upstream.
    by_group = {g: [task for task in tasks if task['group'] == g] for g in range(GROUPS)}
    for g in range(GROUPS - 1):
        assert max(t['completion_time'] for t in by_group[g]) <= min(t['schedule_time'] for t in by_group[g + 1])

    # The relay identity is the only upstream client; it ended with a FINISHED beat, not an eviction.
    clients = rows(upstream, 'SELECT id, evicted, disconnected_at FROM client')
    assert [(c['id'], c['evicted']) for c in clients] == [(identity, 0)]
    assert clients[0]['disconnected_at'] is not None
    assert 'Upstream heartbeat - final' in relay_log
    assert 'not finishing upstream' not in relay_log
    assert not any(text in server_log for text in STALE_OR_DUPLICATE)
    assert 'Evicting client' not in server_log

    # Nothing is left held at the relay.
    assert rows(group.site / 'relay.db', 'SELECT count(*) AS n FROM task') == [{'n': 0}]
    states = rows(group.site / 'relay.db', 'SELECT state, count(*) AS n FROM relay_result GROUP BY state')
    assert states == [{'state': 'returned', 'n': len(tasks)}]
