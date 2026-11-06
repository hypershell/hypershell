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

A relay also works against an upstream server running without a database, rides through its upstream
server dying and coming back (its held results then refused as stale, and the tasks run again), ends
(non-zero) with its site when its upstream server comes back refusing it or its own relay-local receiver dies, finishes everything it holds
when told to drain (what it has not yet staged too), returns the times its site clients report as the
instants they were from another time zone, and hands its site clients the relay's configuration file
without the relay settings in it, a copy it removes when SIGINT or SIGTERM ends it.

A relay serving two zones takes the tasks of those zones only, each under its own identity, and leaves
a third zone's alone; killed while it holds tasks in both, it is evicted under each identity, and every
task of both zones goes back unscheduled.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, Iterator, List, Set, Tuple

# Standard libs
import os
import re
import sys
import time
import stat
import signal
import secrets
import sqlite3
import tomllib
import subprocess
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta
from collections import Counter

# External libs
import psutil
import pytest
from pytest import mark

# Internal libs
from hypershell.core.config import RELAY_MARKER
from hypershell.cluster.launch import AUTH_ENV
from hypershell.core.logging import HOSTNAME
from hypershell.core.tls import ensure_default_materials
from hypershell.relay.upstream import BACKLOG_FACTOR
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


def start_relay(group: Group, name: str, upstream_port: int, key: str, size: int, *args: str, **env: str) -> Any:
    """Start `hs relay` on the site with its own database, launching `size` site clients."""
    env = {'HYPERSHELL_RELAY_UPSTREAM_AUTH': key,
           'HYPERSHELL_RELAY_DATABASE': str(group.site / f'{name}.db'),
           'HYPERSHELL_RELAY_BUNDLEWAIT': '1', **env}
    return group.start(name, 'relay', '-H', 'localhost', '-p', str(upstream_port),
                       '--bind', '127.0.0.1', '--port', str(free_port()),
                       '-N', str(size), '--launcher', str(loopback_launcher(group.site)), *args, env=env)


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

    # Upstream the relay identity is listed with its site, and its site clients under it; it ended
    # with a FINISHED beat, not an eviction, which disconnected them all.
    clients = {c['id']: c for c in rows(upstream, 'SELECT id, evicted, disconnected_at, site, relay FROM client')}
    assert set(clients) == {identity} | leaves
    assert all((c['site'], c['relay'], c['evicted']) == (HOSTNAME, identity, 0) for c in clients.values())
    assert all(c['disconnected_at'] is not None for c in clients.values())
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
    env.setdefault('HYPERSHELL_RELAY_SITE', name)
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
    """Number of the relay's site tasks now waiting on the barrier, each counted once.

    A task is the waiting Python process, whose arguments include the barrier path itself. A site
    client runs each task with `/bin/sh -c`, and where that shell does not exec a lone command but
    waits on it (Debian and Ubuntu patch dash so), the shell stays alive as the task's parent with the
    whole command line as one argument: it holds the barrier path only within that argument and is
    not counted, so the count is the same whichever shell `/bin/sh` is.
    """
    count = 0
    for proc in site_processes(relay):
        try:
            if str(barrier) in proc.cmdline():
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


def settled(group: Group, name: str, upstream: Path, identity: str, size: int = 2) -> bool:
    """Relay `name` can be cut no more work: all `size` site clients are connected (and listed upstream),
    everything dispatched to it is confirmed and held in its database, and either it holds more than its
    low-water mark (so it asks for nothing) or no task upstream is left unclaimed (so a request finds nothing)."""
    relay_db = group.site / name / 'relay.db'
    connected = rows(relay_db, 'SELECT count(*) AS n FROM client WHERE disconnected_at IS NULL')[0]['n']
    local = {row['id'] for row in rows(relay_db, 'SELECT id FROM task WHERE completion_time IS NULL')}
    held = set(held_upstream(upstream, identity))
    dispatched = sum(int(n) for n in re.findall(rf'Dispatched (\d+) tasks to client {identity}', group.text('server')))
    unclaimed = rows(upstream, 'SELECT count(*) AS n FROM task WHERE client_id IS NULL AND completion_time IS NULL '
                               'AND "group" = 0')[0]['n']
    low_water = connected * BACKLOG_FACTOR // 2  # Local bundle size 1.
    forwarded = rows(upstream, f"SELECT count(*) AS n FROM client WHERE relay = '{identity}' "
                               f"AND id <> '{identity}'")[0]['n']  # Its roster reached upstream.
    return (connected == size and forwarded == size and bool(local) and local == held
            and dispatched == len(held) and (len(local) > low_water or unclaimed == 0))


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

    # Upstream each site lists its relay and exactly the site clients that relay launched.
    clients = rows(upstream, 'SELECT id, evicted, disconnected_at, site, relay FROM client')
    for name, identity in identities.items():
        at_site = {c['id']: c for c in clients if c['site'] == name}
        assert set(at_site) == {identity} | leaves[name]
        assert all(c['relay'] == identity for c in at_site.values())
    assert len(clients) == len(identities) + sum(len(ids) for ids in leaves.values())

    # Both relays ended their registrations with a final beat, which disconnected their site clients;
    # nothing stale, duplicate or evicted.
    assert all(c['evicted'] == 0 and c['disconnected_at'] is not None for c in clients)
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

    # Relay B holds confirmed tasks, already running at its site; relay A holds some too. B is killed
    # only once it can be cut nothing more: otherwise a reply cut for it but not yet confirmed would
    # stay in flight upstream with no client (the cut-to-confirm window), and the run would never end.
    for name, relay in relays.items():
        until(lambda: running_tasks(relay, barrier) and held_upstream(upstream, identities[name]),
              deadline, f'tasks held at {name}')
    until(lambda: settled(group, 'relay-b', upstream, identities['relay-b']), deadline, 'relay B settled')
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
    clients = {c['id']: c for c in rows(upstream, 'SELECT id, evicted, disconnected_at, site, relay FROM client')}
    leaves_b = leaves_of(group, 'relay-b')
    assert set(clients) == set(identities.values()) | leaves_a | leaves_b
    assert clients[identities['relay-b']]['evicted'] == 1
    assert clients[identities['relay-a']]['evicted'] == 0
    assert all(c['disconnected_at'] is not None for c in clients.values())

    # Relay B's eviction disconnected its site clients (never evicted on their own); A's ended with A.
    for name, ids in (('relay-a', leaves_a), ('relay-b', leaves_b)):
        assert all((clients[id]['site'], clients[id]['relay'], clients[id]['evicted']) == (name, identities[name], 0)
                   for id in ids)
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


KEY_PROBE_TEMPLATE = ': {% printenv ' + AUTH_ENV + ' || echo unset %}; {}'
"""Site template whose shell expansion records whether the expanding command saw the site key."""


def scan_site_key(relay: Any, barrier: Path) -> Dict[str, Any]:
    """Where the site key is among the processes a relay launched (environments that could be read).

    `keys` is the set of keys the site clients hold in their environment; `argv` counts processes with
    one of them on their command line, `tasks` the running tasks scanned, and `task_hits` those holding
    the key variable or a key in their environment.
    """
    procs = []
    for proc in site_processes(relay):
        try:
            procs.append((proc.cmdline(), proc.environ()))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    procs = [(cmdline, environ) for cmdline, environ in procs if environ]
    clients = [environ for cmdline, environ in procs if 'client' in cmdline]
    keys = {environ[AUTH_ENV] for environ in clients if environ.get(AUTH_ENV)}
    tasks = [environ for cmdline, environ in procs if any(str(barrier) in arg for arg in cmdline)]
    return {'clients': len(clients), 'keyed_clients': sum(1 for environ in clients if environ.get(AUTH_ENV)),
            'keys': keys, 'tasks': len(tasks),
            'argv': sum(1 for cmdline, _ in procs if any(k in arg for k in keys for arg in cmdline)),
            'task_hits': sum(1 for environ in tasks
                             if AUTH_ENV in environ or any(k in v for k in keys for v in environ.values()))}


@mark.integration
def test_site_key_reaches_site_clients_in_their_environment_only(group: Group) -> None:
    """The relay's site clients get the site key in their environment; no command line and no task gets it.

    The site template expands a shell command (``{% %}``) that prints the key variable, and every
    command recorded upstream shows it unset there too.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    server = start_upstream(group, port, key, deadline, barrier)
    relay = start_site_relay(group, 'relay-a', port, key, HYPERSHELL_RELAY_TEMPLATE=KEY_PROBE_TEMPLATE)

    # Both clients, and a task whose environment can be read (a task's shell may withhold its own).
    found = until(lambda: (scan := scan_site_key(relay, barrier))['tasks'] >= 1 and scan['clients'] >= 2 and scan,
                  deadline, 'a readable task running at the site')
    assert found['keyed_clients'] == found['clients'], f'site clients without the site key: {found}'
    site_key, = found['keys']
    assert len(site_key) == 128 and key not in site_key
    assert found['argv'] == 0, f'site key on {found["argv"]} command lines'
    assert found['task_hits'] == 0, f'site key in {found["task_hits"]} task environments'
    barrier.touch()

    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(relay, deadline) == 0, group.text('relay-a')
    tasks = rows(Path(os.environ['HYPERSHELL_DATABASE_FILE']), 'SELECT * FROM task')
    check_upstream_rows(tasks)
    assert all(task['command'].startswith(': unset; ') for task in tasks), \
        [task['command'][:24] for task in tasks if not task['command'].startswith(': unset; ')]
    text = group.text('relay-a') + group.text('relay-a', 'out')
    assert site_key not in text and key not in text


# --- Lifecycle and configuration --------------------------------------------------------------


EXIT_BOUND = 30  # Seconds a relay may take to exit once it has failed.


def waiting_tasks(site: Path, barrier: Path, count: int) -> List[str]:
    """Tasks that wait for the barrier file (Python processes, whose environment can be read)."""
    waiter = site / 'waiter.py'
    waiter.write_text(WAITER)
    return [f"'{sys.executable}' '{waiter}' '{barrier}' wait-{n}" for n in range(count)]


def site_clients(relay: Any) -> List[psutil.Process]:
    """The site clients a relay launched (not their tasks)."""
    found = []
    for proc in site_processes(relay):
        try:
            if 'client' in proc.cmdline():
                found.append(proc)
        except psutil.Error:
            pass
    return found


def release(group: Group, relay: Any, barrier: Path) -> None:
    """Let any task still waiting finish, and kill whatever is left of the relay's process group."""
    barrier.touch()
    try:
        os.killpg(relay.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


ONE_AT_A_TIME_LAUNCHER = """\
#!/bin/sh
# The loopback launcher, with each site client asking for one task at a time (-b 1).
previous=
for arg; do
    shift
    if [ "$previous" = "-b" ]; then set -- "$@" 1; else set -- "$@" "$arg"; fi
    previous=$arg
done
exec '{loopback}' "$@"
"""

ZONED_LAUNCHER = """\
#!/bin/sh
# The loopback launcher, with each site client in time zone {zone}.
TZ='{zone}'
export TZ
exec '{loopback}' "$@"
"""


def wrapped_launcher(site: Path, name: str, text: str, **fields: str) -> Path:
    """A site client launcher that adjusts each launch, then runs the loopback launcher."""
    path = site / name
    path.write_text(text.format(loopback=loopback_launcher(site), **fields))
    path.chmod(0o755)
    return path


FAULTY_RECEIVER_RELAY = """\
import sys
from hypershell.data import model


original = model.Task.apply_results


def apply_results(cls, results):
    # Only a write of real results fails: the receiver may also flush an empty batch on its own.
    if results:
        raise RuntimeError('injected failure in the relay-local receiver')
    return original(results)


# Only the relay-local server's receiver writes results back in a relay process.
model.Task.apply_results = classmethod(apply_results)

from hypershell import main
sys.exit(main(['relay', *sys.argv[1:]]))
"""


def start_faulty_relay(group: Group, upstream_port: int, key: str, size: int) -> Any:
    """Start a relay process whose relay-local receiver fails on the first results it writes (never on none).

    The process is marked as a relay before it imports anything, so it binds the relay database
    at import and runs the relay without re-executing (which would drop the fault).
    """
    script = group.site / 'faulty_relay.py'
    script.write_text(FAULTY_RECEIVER_RELAY)
    env = {**os.environ, RELAY_MARKER: '1',
           'HYPERSHELL_RELAY_UPSTREAM_AUTH': key,
           'HYPERSHELL_RELAY_DATABASE': str(group.site / 'relay.db'),
           'HYPERSHELL_RELAY_BUNDLEWAIT': '1'}
    argv = [sys.executable, str(script), '-H', 'localhost', '-p', str(upstream_port),
            '--bind', '127.0.0.1', '--port', str(free_port()),
            '-N', str(size), '--launcher', str(loopback_launcher(group.site))]
    with open(group.site / 'relay.out', 'w') as stdout, open(group.site / 'relay.log', 'w') as stderr:
        proc = subprocess.Popen(argv, cwd=group.site, stdout=stdout, stderr=stderr, env=env,
                                start_new_session=True)
    group.procs.append(proc)
    return proc


def unstaged(relay_db: Path) -> int:
    """Tasks the relay holds that its relay-local scheduler has not yet loaded (nor handed to a site client)."""
    return rows(relay_db, 'SELECT count(*) AS n FROM task WHERE schedule_time IS NULL '
                          'AND completion_time IS NULL')[0]['n']


@mark.integration
def test_relay_against_a_server_without_a_database(group: Group) -> None:
    """Against `hs server --no-db` (no stamps) every task runs once at the site and goes back as a client returns it."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    names = [f'nodb-{n}' for n in range(8)]
    taskfile = create_taskfile(group.site, [f'echo {name}' for name in names])
    server = group.start('server', 'server', '--no-db', '-H', 'localhost', '-p', str(port), '-k', key, str(taskfile))
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, size=2)

    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(relay, deadline) == 0, group.text('relay')

    relay_log = group.text('relay')
    runs = Counter(RUNNING.findall(relay_log))
    assert len(runs) == len(names) and set(runs.values()) == {1}
    assert sorted(group.text('relay', 'out').split()) == sorted(names)
    stamps = rows(group.site / 'relay.db', 'SELECT schedule_time, server_id FROM relay_task')
    assert stamps == [{'schedule_time': None, 'server_id': None}] * len(names)
    states = rows(group.site / 'relay.db', 'SELECT state, count(*) AS n FROM relay_result GROUP BY state')
    assert states == [{'state': 'returned', 'n': len(names)}]
    assert 'RelayInsertError' not in relay_log and 'Failed to insert' not in relay_log
    assert 'Upstream heartbeat - final' in relay_log and 'not finishing upstream' not in relay_log
    assert 'running without a database' in group.text('server')  # Upstream says a relay has no failover here.


@mark.integration
def test_relay_rides_through_its_upstream_server_dying_and_restarting(group: Group) -> None:
    """With its upstream server killed mid-run the relay keeps its site running and holds the results; once a
    new server answers on the same port it hands them over (refused as stale: the restarted server put every
    unfinished task back), drops nothing it still needs, runs the tasks again under their new assignments, and
    every task completes once upstream."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 4))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, size=2, HYPERSHELL_RELAY_UPSTREAM_HEARTRATE='1',
                        HYPERSHELL_RELAY_UPSTREAM_DEADLINE='3', HYPERSHELL_RELAY_UPSTREAM_BACKOFF='2')
    upstream, relay_db = Path(os.environ['HYPERSHELL_DATABASE_FILE']), group.site / 'relay.db'
    try:
        identity = until(lambda: IDENTITY.search(group.text('relay')), deadline, 'relay identity').group(1)
        until(lambda: running_tasks(relay, barrier) >= 2 and len(held_upstream(upstream, identity)) == 4,
              deadline, 'every task held, two running')
        clients = [proc.pid for proc in site_clients(relay)]
        first = {row['id']: row['schedule_time'] for row in rows(upstream, 'SELECT id, schedule_time FROM task')}

        os.killpg(server.pid, signal.SIGKILL)
        server.wait(timeout=EXIT_BOUND)
        until(lambda: 'Lost connection to upstream server' in group.text('relay'), deadline, 'the link down')
        barrier.touch()
        until(lambda: rows(relay_db, "SELECT count(*) AS n FROM relay_result WHERE state = 'pending'") == [{'n': 4}],
              deadline, 'every result held in the outbox')
        assert alive(relay.pid) and all(alive(pid) for pid in clients), 'the site did not keep running'

        restarted = group.start('server-again', 'server', '--restart', '-b', '1',
                                '-H', 'localhost', '-p', str(port), '-k', key)
        assert group.wait(restarted, deadline) == 0, group.text('server-again')
        assert group.wait(relay, deadline) == 0, group.text('relay')
    finally:
        release(group, relay, barrier)

    tasks = rows(upstream, 'SELECT * FROM task')
    assert len(tasks) == 4 and all(task['exit_status'] == 0 for task in tasks)
    assert all(task['schedule_time'] != first[task['id']] for task in tasks)  # Each landed on its new assignment.
    server_log, relay_log = group.text('server-again'), group.text('relay')
    assert server_log.count('Refused stale result') == 4  # The held results answered assignments put back.
    assert 'Reconnected to upstream server' in relay_log
    assert 'Upstream heartbeat - final' in relay_log


@mark.integration
def test_relay_exits_and_ends_its_site_when_its_upstream_server_comes_back_unusable(group: Group) -> None:
    """An upstream server that answers again but refuses the relay for good (another key) is a failure: the relay
    exits non-zero within the bound, with the error, and leaves no site client."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 4))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, size=2, HYPERSHELL_RELAY_UPSTREAM_HEARTRATE='1',
                        HYPERSHELL_RELAY_UPSTREAM_DEADLINE='3', HYPERSHELL_RELAY_UPSTREAM_BACKOFF='2')
    try:
        until(lambda: running_tasks(relay, barrier) >= 2, deadline, 'two site tasks running')
        clients = [proc.pid for proc in site_clients(relay)]
        assert len(clients) == 2
        os.killpg(server.pid, signal.SIGKILL)
        server.wait(timeout=EXIT_BOUND)
        until(lambda: 'Lost connection to upstream server' in group.text('relay'), deadline, 'the link down')
        assert alive(relay.pid)

        other = group.start('server-other', 'server', '--forever', '-H', 'localhost', '-p', str(port),
                            '-k', secrets.token_hex(16))
        wait_listening(other, port, group, 'server-other')
        try:
            status = relay.wait(timeout=EXIT_BOUND)
        except subprocess.TimeoutExpired:
            pytest.fail(f'relay still running {EXIT_BOUND} seconds after its upstream server refused it:\n'
                        f'{group.text("relay")}')
        relay_log = group.text('relay')
        assert status not in (0, None), relay_log
        assert 'AuthenticationError' in relay_log
        assert not any(alive(pid) for pid in clients), 'a site client outlived the relay'
        assert 'Ended 2 site clients' in relay_log
    finally:
        release(group, relay, barrier)


@mark.integration
def test_relay_exits_and_ends_its_site_when_its_receiver_dies(group: Group) -> None:
    """With its relay-local receiver dead, the relay exits non-zero within the bound, ends its site and sends no FINISHED.

    Upstream then evicts the relay and puts back every task it held.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 4))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key,
                         env={'HYPERSHELL_SERVER_EVICT': '10'})
    wait_listening(server, port, group, 'server')
    relay = start_faulty_relay(group, port, key, size=2)
    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    try:
        identity = until(lambda: IDENTITY.search(group.text('relay')), deadline, 'relay identity').group(1)
        until(lambda: running_tasks(relay, barrier) >= 2, deadline, 'two site tasks running')
        clients = [proc.pid for proc in site_clients(relay)]
        assert len(clients) == 2
        held = held_upstream(upstream, identity)
        assert held

        barrier.touch()  # The first result to reach the relay-local receiver ends it.
        try:
            status = relay.wait(timeout=EXIT_BOUND)
        except subprocess.TimeoutExpired:
            pytest.fail(f'relay still running {EXIT_BOUND} seconds after its site returned results:\n'
                        f'{group.text("relay")}')
        relay_log = group.text('relay')
        assert status not in (0, None), relay_log
        assert 'injected failure in the relay-local receiver' in relay_log
        assert not any(alive(pid) for pid in clients), 'a site client outlived the relay'
        assert 'Upstream heartbeat - final' not in relay_log

        # Upstream evicts the relay, which never finished, and puts back what it held (marking the
        # eviction and reverting the tasks are two statements, so both are waited for).
        until(lambda: (rows(upstream, f"SELECT evicted FROM client WHERE id = '{identity}'") == [{'evicted': 1}]
                       and not held_upstream(upstream, identity)),
              deadline, 'the relay evicted upstream and its tasks put back')
        # Put back: no client and not complete (the upstream scheduler may have staged them again at once).
        reverted = rows(upstream, 'SELECT id FROM task WHERE client_id IS NULL AND completion_time IS NULL')
        assert set(held) <= {row['id'] for row in reverted}
    finally:
        release(group, relay, barrier)


@mark.integration
def test_drain_signal_finishes_everything_the_relay_holds(group: Group) -> None:
    """SIGUSR1 ends pulling, but the site finishes every task the relay holds, staged or not, and it ends cleanly.

    Site clients take one task at a time in a named zone, so the relay-local server stages one or
    two tasks ahead of them, and the relay holds tasks it has not yet staged when the signal comes.
    A drain that stopped the relay-local scheduler would leave those behind.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    relay_db = group.site / 'relay.db'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 24))
    submit = group.start('submit', 'submit', '--zone', ZONE, str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--forever', '-b', '10', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    launcher = wrapped_launcher(group.site, 'one-at-a-time-launcher', ONE_AT_A_TIME_LAUNCHER)
    relay = start_relay(group, 'relay', port, key, 2, '--zone', ZONE, '--launcher', str(launcher),
                        HYPERSHELL_RELAY_BUNDLESIZE='6')
    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    try:
        identity = until(lambda: IDENTITY.search(group.text('relay')), deadline, 'relay identity').group(1)
        # Both site clients are busy and the relay holds tasks its relay-local server has not staged.
        until(lambda: running_tasks(relay, barrier) == 2 and unstaged(relay_db) > 0,
              deadline, 'an unstaged backlog at the relay')
        relay.send_signal(signal.SIGUSR1)
        until(lambda: 'Drain requested' in group.text('relay'), deadline, 'the drain to be seen')
        barrier.touch()
        assert group.wait(relay, deadline) == 0, group.text('relay')
    finally:
        release(group, relay, barrier)

    received = {row['id'] for row in rows(relay_db, 'SELECT id FROM relay_task')}
    tasks = {task['id']: task for task in rows(upstream, 'SELECT * FROM task')}
    assert received and all(tasks[id]['completion_time'] is not None and tasks[id]['exit_status'] == 0
                            for id in received)
    assert all(task['client_id'] is None for id, task in tasks.items() if id not in received)  # Never pulled
    states = rows(relay_db, 'SELECT state, count(*) AS n FROM relay_result GROUP BY state')
    assert states == [{'state': 'returned', 'n': len(received)}]
    relay_log = group.text('relay')
    assert 'Upstream heartbeat - final' in relay_log and 'not finishing upstream' not in relay_log
    client, = rows(upstream, f"SELECT evicted, disconnected_at FROM client WHERE id = '{identity}'")
    assert client['evicted'] == 0 and client['disconnected_at'] is not None


@mark.integration
def test_leaf_times_land_upstream_as_the_instants_they_were(group: Group) -> None:
    """Site clients in another time zone than their relay: every time they report lands upstream as that instant.

    The upstream server and the relay run in one zone, the site clients 13.5 hours behind it. Each
    task's start and completion, as the upstream server stores them, fall after its schedule time
    there, and within minutes of it.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    relay_zone, site_zone = {'TZ': 'Asia/Kolkata'}, 'America/Los_Angeles'
    taskfile = create_taskfile(group.site, [f'echo zone-{n}' for n in range(6)])
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--restart', '-b', '2', '-H', 'localhost', '-p', str(port), '-k', key,
                         env=relay_zone)
    wait_listening(server, port, group, 'server')
    launcher = wrapped_launcher(group.site, 'zoned-launcher', ZONED_LAUNCHER, zone=site_zone)
    relay = start_relay(group, 'relay', port, key, 2, '--launcher', str(launcher), **relay_zone)

    assert group.wait(server, deadline) == 0, group.text('server')
    assert group.wait(relay, deadline) == 0, group.text('relay')

    tasks = rows(Path(os.environ['HYPERSHELL_DATABASE_FILE']),
                 'SELECT schedule_time, start_time, completion_time, exit_status FROM task')
    assert len(tasks) == 6 and all(task['exit_status'] == 0 for task in tasks)
    for task in tasks:
        scheduled, started, completed = (datetime.fromisoformat(task[name])
                                         for name in ('schedule_time', 'start_time', 'completion_time'))
        assert scheduled <= started <= completed <= scheduled + timedelta(minutes=5), task


SIGNALLED_STATUS = {signal.SIGTERM: -signal.SIGTERM, signal.SIGINT: 4}  # 4 is the keyboard-interrupt status


@contextmanager
def interruptible() -> Iterator[None]:
    """Processes started within get SIGINT at its default action, whatever this process inherited.

    A background job of a non-interactive shell starts with SIGINT ignored, and an ignored signal stays
    ignored across exec, so a relay started from it could not be interrupted. A signal with a handler
    is reset to its default action across exec instead, so a handler is installed while the process
    starts. Off the main thread no handler can be installed: with SIGINT ignored the test is skipped.
    """
    try:
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:
        if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
            pytest.skip('SIGINT is ignored here and cannot be reset off the main thread')
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


@mark.integration
@mark.parametrize('signum', [signal.SIGTERM, signal.SIGINT], ids=['SIGTERM', 'SIGINT'])
def test_relay_ended_by_a_signal_removes_the_site_config_copy(group: Group, signum: int) -> None:
    """SIGTERM or SIGINT end the relay without a drain: it ends its site clients and removes the site's config copy.

    SIGTERM still ends the process by that signal.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    config = group.site / 'relay.toml'
    config.write_text(f'[relay.upstream]\nauth = "{key}"\n')
    copy = group.site / 'relay.db.site.toml'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 4))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    with interruptible():
        relay = start_relay(group, 'relay', port, key, 2, HYPERSHELL_CONFIG_FILE=str(config))
    try:
        until(lambda: running_tasks(relay, barrier) >= 2, deadline, 'two site tasks running')
        clients = [proc.pid for proc in site_clients(relay)]
        assert len(clients) == 2 and copy.exists()
        relay.send_signal(signum)
        try:
            status = relay.wait(timeout=EXIT_BOUND)
        except subprocess.TimeoutExpired:
            pytest.fail(f'relay still running {EXIT_BOUND} seconds after {signal.Signals(signum).name}:\n'
                        f'{group.text("relay")}')
        assert status == SIGNALLED_STATUS[signum], group.text('relay')
        assert not copy.exists(), 'the site configuration copy outlived the relay'
        assert not any(alive(pid) for pid in clients), 'a site client outlived the relay'
    finally:
        release(group, relay, barrier)


RELAY_CONFIG = """\
[relay.upstream]
auth = "{key}"

[server.tls]
cert = "{cert}"
key = "{cert_key}"
cafile = "{cert}"
"""


@mark.integration
def test_site_clients_keep_the_relay_config_file_without_relay_settings(group: Group) -> None:
    """Site clients get a private copy of the relay's configuration file, so its explicit [server.tls] still works."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    barrier = group.site / 'barrier'
    certs = group.site / 'certs'
    certs.mkdir()
    cert, cert_key = ensure_default_materials(str(certs))
    config = group.site / 'relay.toml'
    config.write_text(RELAY_CONFIG.format(key=key, cert=cert, cert_key=cert_key))
    copy = group.site / 'relay.db.site.toml'
    taskfile = create_taskfile(group.site, waiting_tasks(group.site, barrier, 4))
    submit = group.start('submit', 'submit', str(taskfile))
    assert group.wait(submit, deadline) == 0, group.text('submit')
    server = group.start('server', 'server', '--restart', '--no-tls', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, 1, '--no-tls', HYPERSHELL_CONFIG_FILE=str(config))
    try:
        until(lambda: running_tasks(relay, barrier), time.monotonic() + 60, 'a site task running')
        environ = [proc.environ() for proc in site_clients(relay)]
        assert environ and all(env.get('HYPERSHELL_CONFIG_FILE') == str(copy) for env in environ)
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
        text = copy.read_text()
        assert key not in text and 'relay' not in tomllib.loads(text)
        assert tomllib.loads(text)['server']['tls'] == {'cert': cert, 'key': cert_key, 'cafile': cert}
        barrier.touch()
        assert group.wait(server, deadline) == 0, group.text('server')
        assert group.wait(relay, deadline) == 0, group.text('relay')
    finally:
        release(group, relay, barrier)

    tasks = rows(Path(os.environ['HYPERSHELL_DATABASE_FILE']), 'SELECT exit_status FROM task')
    assert [task['exit_status'] for task in tasks] == [0] * 4
    relay_log = group.text('relay')
    assert 'CERTIFICATE_VERIFY_FAILED' not in relay_log and 'exited with status' not in relay_log
    assert not copy.exists()



# --- A relay serving two zones -----------------------------------------------------------------------


SERVED = ('cpu', 'gpu')
PER_ZONE = 4


def submit_zones(group: Group, deadline: float, lines: Callable[[str], List[str]], zones: Tuple[str, ...]) -> None:
    """Submit `lines(zone)` to each of `zones`."""
    for zone in zones:
        taskfile = create_taskfile(group.site, lines(zone), filename=f'{zone}.in')
        submit = group.start(f'submit-{zone}', 'submit', '--zone', zone, str(taskfile))
        assert group.wait(submit, deadline) == 0, group.text(f'submit-{zone}')


def zone_identities(group: Group, name: str, deadline: float) -> Dict[str, str]:
    """The upstream identity a relay logged for each zone it serves."""
    until(lambda: len(IDENTITY.findall(group.text(name))) == len(SERVED), deadline, f'{name} identities')
    return {zone: identity for identity, zone in IDENTITY.findall(group.text(name))}


def zone_rows(upstream: Path, zone: str) -> List[Dict[str, Any]]:
    """Upstream task rows of `zone`."""
    return rows(upstream, f"SELECT t.* FROM task t JOIN zone z ON z.id = t.zone WHERE z.name = '{zone}'")


@mark.integration
def test_multizone_relay_takes_only_its_zones(group: Group) -> None:
    """A relay serving two zones runs both zones' tasks, each pulled under its own identity; a third zone is left alone.

    Each zone has its own fleet of one site client at the relay's site. Upstream, the relay is two
    clients, both attributed to its site; its site clients are listed under the first identity.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    submit_zones(group, deadline, lambda zone: [f'echo {zone}-{n}' for n in range(PER_ZONE)], (*SERVED, 'other'))
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, 1, '--zone', SERVED[0], '--zone', SERVED[1],
                        HYPERSHELL_RELAY_SITE='site-z')
    try:
        identities = zone_identities(group, 'relay', deadline)
        assert set(identities) == set(SERVED) and len(set(identities.values())) == 2
        until(lambda: all(len([t for t in zone_rows(upstream, zone) if t['exit_status'] == 0]) == PER_ZONE
                          for zone in SERVED), deadline, 'both zones complete')
        relay.send_signal(signal.SIGUSR1)
        assert group.wait(relay, deadline) == 0, group.text('relay')
    finally:
        release(group, relay, group.site / 'barrier')

    # The third zone was never pulled: no row of it was handed to anyone.
    assert all(t['client_id'] is None and t['completion_time'] is None for t in zone_rows(upstream, 'other'))

    # Each zone was requested under its own identity, and only for its own tasks.
    server_log = group.text('server')
    for zone, identity in identities.items():
        assert re.search(rf'Dispatched \d+ tasks to client {identity} \(zone {zone}\)', server_log), zone
        assert not re.search(rf'Dispatched \d+ tasks to client {identity} \(zone (?!{zone}\))', server_log), zone

    # Results are the site clients'; upstream lists both identities at the site, the leaves under the first.
    leaves = {row['id'] for row in rows(group.site / 'relay.db', 'SELECT id FROM client')}
    assert len(leaves) >= len(SERVED)
    done = [t for zone in SERVED for t in zone_rows(upstream, zone)]
    assert {t['client_id'] for t in done} <= leaves
    clients = {c['id']: c for c in rows(upstream, 'SELECT id, evicted, disconnected_at, site, relay FROM client')}
    holder = identities[SERVED[0]]
    for identity in identities.values():
        assert (clients[identity]['site'], clients[identity]['relay']) == ('site-z', identity)
    assert all(clients[id]['relay'] == holder and clients[id]['site'] == 'site-z' for id in leaves)
    assert all(c['evicted'] == 0 and c['disconnected_at'] is not None for c in clients.values())
    relay_log = group.text('relay')
    assert 'Upstream heartbeat - final' in relay_log and 'not finishing upstream' not in relay_log


def zone_settled(group: Group, upstream: Path, identity: str) -> bool:
    """A zone identity holds confirmed tasks above its low-water mark, and everything dispatched to it is confirmed.

    With its site's tasks blocked, it then asks for nothing more, so no reply can be cut for it unconfirmed.
    """
    held = held_upstream(upstream, identity)
    dispatched = sum(int(n) for n in re.findall(rf'Dispatched (\d+) tasks to client {identity}', group.text('server')))
    return len(held) >= BACKLOG_FACTOR and dispatched == len(held)


@mark.integration
def test_multizone_relay_failover_reverts_and_unstages_both_zones(group: Group) -> None:
    """A two-zone relay killed while it holds tasks in both zones is evicted under each identity; both zones go back.

    Every task the relay held is put back unscheduled, and what the upstream server had staged for
    either zone is unstaged once its only client is gone: no row of either zone is left stamped.
    """
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    upstream = Path(os.environ['HYPERSHELL_DATABASE_FILE'])
    barrier = group.site / 'barrier'
    submit_zones(group, deadline, lambda zone: waiting_tasks(group.site, barrier, 2 * PER_ZONE), SERVED)
    server = group.start('server', 'server', '--forever', '-b', '1', '-H', 'localhost', '-p', str(port), '-k', key,
                         env={'HYPERSHELL_SERVER_EVICT': '10'})
    wait_listening(server, port, group, 'server')
    relay = start_relay(group, 'relay', port, key, 1, '--zone', SERVED[0], '--zone', SERVED[1],
                        HYPERSHELL_RELAY_UPSTREAM_HEARTRATE='1')
    try:
        identities = zone_identities(group, 'relay', deadline)
        until(lambda: running_tasks(relay, barrier) == len(SERVED)
              and all(zone_settled(group, upstream, identity) for identity in identities.values()),
              deadline, 'tasks held in both zones')
        held = {zone: held_upstream(upstream, identity) for zone, identity in identities.items()}
        site = [proc.pid for proc in site_processes(relay)]
        os.killpg(relay.pid, signal.SIGKILL)
        assert relay.wait(timeout=30) == -signal.SIGKILL
        until(lambda: not any(alive(pid) for pid in site), deadline, 'site gone')
        until(lambda: group.text('server').count('Reverting orphaned tasks') == len(SERVED), deadline,
              'both identities evicted')
        until(lambda: all(t['schedule_time'] is None for zone in SERVED for t in zone_rows(upstream, zone)),
              deadline, 'both zones back unscheduled')
    finally:
        release(group, relay, barrier)

    for zone, ids in held.items():
        assert ids, zone
        rows_ = {t['id']: t for t in zone_rows(upstream, zone)}
        assert all(rows_[id]['client_id'] is None and rows_[id]['completion_time'] is None for id in ids)
    clients = {c['id']: c for c in rows(upstream, 'SELECT id, evicted FROM client')}
    assert all(clients[identity]['evicted'] == 1 for identity in identities.values())
    server_log = group.text('server')
    assert server_log.count('Evicting client') == len(SERVED)
    assert all(identity in server_log for identity in identities.values())
