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

With two relays sharing a zone, the first group's tasks wait on a barrier file, so each site holds
work at once before anything completes: both sites run work, and the processes they launched (site
clients and their tasks) can be scanned for the upstream key while they run. Killing one relay
(with its site) while it holds confirmed tasks leaves them to the upstream server's eviction, which
puts them back in the queue for the other relay.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List, Set, Tuple

# Standard libs
import os
import re
import sys
import time
import signal
import secrets
import sqlite3
from pathlib import Path
from collections import Counter

# External libs
import psutil
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


# --- Two relays ---------------------------------------------------------------------------------


ZONE = 'shared'
UNTIL = 0.1  # Seconds between looks at a condition.


def until(condition: Callable[[], Any], deadline: float, what: str) -> Any:
    """Poll `condition` until it returns something true; fail at the deadline."""
    while not (value := condition()):
        assert time.monotonic() < deadline, f'timed out waiting for {what}'
        time.sleep(UNTIL)
    return value


WAITER = """\
import os, sys, time
barrier, label = sys.argv[1:3]
while not os.path.exists(barrier):
    time.sleep(0.1)
print(os.environ.get('SITE_MARK'), label, flush=True)
"""


def gated_workload(site: Path, barrier: Path) -> List[str]:
    """The grouped workload with a retry, in one zone; group 0 waits for the barrier file to exist.

    The waiting tasks are Python processes, whose environment can be read on every platform (some
    platforms withhold the environment of their own system programs, such as the shell).
    """
    waiter = site / 'waiter.py'
    waiter.write_text(WAITER)
    wait = f"'{sys.executable}' '{waiter}' '{barrier}'"
    lines = [f'{wait} 0-{n}  #HYPERSHELL: group:0' for n in range(PER_GROUP)]
    for g in range(1, GROUPS):
        lines.extend(f'echo "$SITE_MARK {g}-{n}"  #HYPERSHELL: group:{g}' for n in range(PER_GROUP))
    lines.append(f'{wait} retry; test "$TASK_ATTEMPT" -gt 1 || exit 3  #HYPERSHELL: group:0')
    return lines


def start_site_relay(group: Group, name: str, upstream_port: int, key: str, size: int = 2,
                     **env: str) -> Any:
    """Start `hs relay` for zone `shared` with its own database and a primary database file that must never appear.

    The upstream key is placed in the relay's environment under a second name on purpose; the site must
    never see it. `SITE_MARK` is an ordinary variable that the site's tasks print.
    """
    home = group.site / name
    home.mkdir()
    env = {'HYPERSHELL_RELAY_UPSTREAM_AUTH': key,
           'HYPERSHELL_RELAY_DATABASE': str(home / 'relay.db'),
           'HYPERSHELL_DATABASE_FILE': str(home / 'primary-sentinel.db'),
           'HYPERSHELL_RELAY_BUNDLEWAIT': '1',
           'FENCE_PROBE': f'probe:{key}',
           'SITE_MARK': name, **env}
    return group.start(name, 'relay', '-H', 'localhost', '-p', str(upstream_port),
                       '--bind', '127.0.0.1', '--port', str(free_port()), '--zone', ZONE,
                       '-N', str(size), '--launcher', str(loopback_launcher(group.site)), env=env)


def site_processes(relay: Any) -> List[psutil.Process]:
    """Every process a relay launched (site clients, their launchers and tasks), not the relay itself."""
    try:
        return psutil.Process(relay.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def running_tasks(relay: Any, barrier: Path) -> int:
    """Number of the relay's site tasks now waiting on the barrier."""
    count = 0
    for proc in site_processes(relay):
        try:
            if any(str(barrier) in arg for arg in proc.cmdline()):
                count += 1
        except psutil.Error:
            pass
    return count


def scan_site(relay: Any, key: str, barrier: Path) -> Dict[str, Any]:
    """Scan the command line and environment of every process a relay launched for the upstream key.

    Counts only processes whose environment could be read: `clients` and `tasks` of them, `hits` among
    all of them, and the variable `names` they hold.
    """
    found = {'read': 0, 'clients': 0, 'tasks': 0, 'hits': 0, 'names': set()}
    for proc in site_processes(relay):
        try:
            cmdline, environ = proc.cmdline(), proc.environ()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue  # Exited between the listing and the read.
        if not environ:
            continue  # Withheld by the platform.
        found['read'] += 1
        found['clients'] += 'client' in cmdline
        found['tasks'] += any(str(barrier) in arg for arg in cmdline)
        found['names'].update(environ)
        if any(key in arg for arg in cmdline) or any(key in n or key in v for n, v in environ.items()):
            found['hits'] += 1
    return found


def alive(pid: int) -> bool:
    """True while a process runs (a zombie has ended)."""
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def identity_of(group: Group, name: str, deadline: float) -> str:
    """The upstream identity a relay logged when it started."""
    match = until(lambda: IDENTITY.search(group.text(name)), deadline, f'{name} identity')
    assert match.group(2) == ZONE
    return match.group(1)


def held_upstream(upstream: Path, identity: str) -> List[str]:
    """Ids of upstream rows confirmed to `identity` and not completed."""
    return [row['id'] for row in rows(upstream, f"SELECT id FROM task WHERE client_id = '{identity}' "
                                                f"AND completion_time IS NULL")]


def leaves_of(group: Group, name: str) -> Set[str]:
    """Ids of the site clients a relay registered."""
    return {row['id'] for row in rows(group.site / name / 'relay.db', 'SELECT id FROM client')}


def check_upstream_rows(tasks: List[Dict[str, Any]]) -> None:
    """Every task completed upstream, the one failure retried once, and groups ran in order."""
    assert len(tasks) == GROUPS * PER_GROUP + 2
    assert all(task['completion_time'] is not None for task in tasks)
    assert sum(1 for task in tasks if task['exit_status'] == 0) == GROUPS * PER_GROUP + 1
    failed = [task for task in tasks if task['exit_status'] != 0]
    assert [(task['exit_status'], task['attempt'], task['retried']) for task in failed] == [(3, 1, 1)]
    retry, = [task for task in tasks if task['attempt'] == 2]
    assert retry['previous_id'] == failed[0]['id'] and retry['exit_status'] == 0
    assert {task['zone'] for task in tasks} == {tasks[0]['zone']} and tasks[0]['zone'] is not None
    by_group = {g: [task for task in tasks if task['group'] == g] for g in range(GROUPS)}
    for g in range(GROUPS - 1):
        assert max(t['completion_time'] for t in by_group[g]) <= min(t['schedule_time'] for t in by_group[g + 1])


def start_upstream(group: Group, port: int, key: str, deadline: float, barrier: Path, **env: str) -> Any:
    """Submit the gated workload to zone `shared` and start the upstream server (one retry, bundles of 4)."""
    taskfile = create_taskfile(group.site, gated_workload(group.site, barrier))
    submit = group.start('submit', 'submit', '--zone', ZONE, str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--restart', '-r', '1', '-b', '4', '-Q', '1',
                         '-H', 'localhost', '-p', str(port), '-k', key, env=env)
    wait_listening(server, port, group, 'server')
    return server


@mark.integration
def test_two_relays_grouped_workload_with_retry(group: Group) -> None:
    """Two relays share a zone's grouped workload: both sites run work, and no site process sees the upstream key."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    server = start_upstream(group, port, key, deadline, barrier)
    relays = {name: start_site_relay(group, name, port, key) for name in ('relay-a', 'relay-b')}

    # Both sites hold work at once, waiting on the barrier: scan everything they launched.
    for name, relay in relays.items():
        until(lambda: running_tasks(relay, barrier), deadline, f'a task running at {name}')
    for name, relay in relays.items():
        found = scan_site(relay, key, barrier)
        assert found['clients'] >= 2 and found['tasks'] >= 1, f'{name}: site processes read: {found}'
        assert found['hits'] == 0, f'{name}: upstream key seen in {found["hits"]} site processes'
        assert 'SITE_MARK' in found['names'] and 'FENCE_PROBE' not in found['names']
        assert not any(n.startswith('HYPERSHELL_RELAY_') for n in found['names'])
    barrier.touch()

    assert group.wait(server, deadline) == 0, group.text('server')
    for name, relay in relays.items():
        assert group.wait(relay, deadline) == 0, group.text(name)

    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    tasks = rows(upstream, 'SELECT * FROM task')
    check_upstream_rows(tasks)

    # Each task ran once, at one site or the other, and is attributed to the site client that ran it.
    identities = {name: identity_of(group, name, deadline) for name in relays}
    leaves = {name: leaves_of(group, name) for name in relays}
    assert all(len(ids) >= 2 for ids in leaves.values()) and leaves['relay-a'].isdisjoint(leaves['relay-b'])
    assert set(identities.values()).isdisjoint(leaves['relay-a'] | leaves['relay-b'])
    assert {task['client_id'] for task in tasks} <= leaves['relay-a'] | leaves['relay-b']
    for name in relays:
        ran = [task for task in tasks if task['client_id'] in leaves[name]]
        assert ran, f'no task ran at {name}'
        printed = group.text(name, 'out').split()
        assert printed and set(printed[::2]) == {name}, f'{name} printed {printed}'
    runs = Counter(RUNNING.findall(group.text('relay-a')) + RUNNING.findall(group.text('relay-b')))
    assert set(runs) == {task['id'] for task in tasks} and set(runs.values()) == {1}

    # Both relays ended their registrations with a final beat; nothing stale, duplicate or evicted.
    clients = rows(upstream, 'SELECT id, evicted, disconnected_at FROM client')
    assert sorted((c['id'], c['evicted']) for c in clients) == sorted((i, 0) for i in identities.values())
    assert all(c['disconnected_at'] is not None for c in clients)
    server_log = group.text('server')
    assert not any(text in server_log for text in STALE_OR_DUPLICATE)
    assert 'Evicting client' not in server_log
    for name in relays:
        text = group.text(name) + group.text(name, 'out')
        assert 'Upstream heartbeat - final' in text and 'not finishing upstream' not in text
        assert key not in text, f'upstream key in {name} output'
        assert rows(group.site / name / 'relay.db', 'SELECT count(*) AS n FROM task') == [{'n': 0}]
        assert not (group.site / name / 'primary-sentinel.db').exists()


@mark.integration
def test_failover_reverts_and_completes_elsewhere(group: Group) -> None:
    """A relay killed with its site while holding confirmed tasks is evicted; its tasks complete at the other site."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    server = start_upstream(group, port, key, deadline, barrier, HYPERSHELL_SERVER_EVICT='10')
    relays = {name: start_site_relay(group, name, port, key, HYPERSHELL_RELAY_UPSTREAM_HEARTRATE='1')
              for name in ('relay-a', 'relay-b')}
    identities = {name: identity_of(group, name, deadline) for name in relays}
    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])

    # Relay B holds confirmed tasks, already running at its site; relay A holds some too.
    for name, relay in relays.items():
        until(lambda: running_tasks(relay, barrier) and held_upstream(upstream, identities[name]),
              deadline, f'tasks held at {name}')
    snapshot = held_upstream(upstream, identities['relay-b'])
    site_b = [proc.pid for proc in site_processes(relays['relay-b'])]
    os.killpg(relays['relay-b'].pid, signal.SIGKILL)
    assert relays['relay-b'].wait(timeout=30) == -signal.SIGKILL
    until(lambda: not any(alive(pid) for pid in site_b), deadline, 'site B gone')
    barrier.touch()

    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(relays['relay-a'], deadline) == 0, group.text('relay-a')

    tasks = rows(upstream, 'SELECT * FROM task')
    check_upstream_rows(tasks)

    # Relay B was evicted and its tasks put back; each completed at site A.
    leaves_a = leaves_of(group, 'relay-a')
    by_id = {task['id']: task for task in tasks}
    assert snapshot
    assert {task['client_id'] for task in tasks} <= leaves_a
    clients = {c['id']: c for c in rows(upstream, 'SELECT id, evicted, disconnected_at FROM client')}
    assert set(clients) == set(identities.values())
    assert clients[identities['relay-b']]['evicted'] == 1
    assert clients[identities['relay-a']]['evicted'] == 0
    assert clients[identities['relay-a']]['disconnected_at'] is not None
    server_log = group.text('server')
    assert server_log.count('Evicting client') == 1 and identities['relay-b'] in server_log
    assert server_log.count('Reverting orphaned tasks') == 1
    assert not any(text in server_log for text in STALE_OR_DUPLICATE)

    # Site A ran each task at most once; only the tasks relay B held when it died ran twice in all.
    runs_a = Counter(RUNNING.findall(group.text('relay-a')))
    runs = runs_a + Counter(RUNNING.findall(group.text('relay-b')))
    assert set(runs_a.values()) == {1}
    assert set(runs) == set(by_id)
    assert {id for id, count in runs.items() if count > 1} <= set(snapshot)
    text = group.text('relay-a') + group.text('relay-a', 'out')
    assert 'Upstream heartbeat - final' in text and key not in text
    assert not (group.site / 'relay-b' / 'primary-sentinel.db').exists()
    assert not (group.site / 'relay-a' / 'primary-sentinel.db').exists()
