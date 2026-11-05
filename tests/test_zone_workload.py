# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A grouped, mixed-zone workload with retries, run by zoned and unzoned clients over loopback.

Each client exports a ZONE_MARK naming the zone it serves; every task prints that mark next to the
zone it was submitted to, so a task run by a client of another zone shows in the output.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, List, Any

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
from hypershell.data.model import zone_id
from tests.test_dispatch_workload import Group, group, wait_listening, received, RUNNING  # noqa: fixture
from tests import create_taskfile


DEADLINE = 180
ZONES = ('default', 'cpu', 'gpu')
GROUPS = 3
PER_GROUP = 8

# (name, zone or None, advertised -b or None)
CLIENTS = (
    ('cpu-a', 'cpu', 3), ('cpu-b', 'cpu', None),
    ('gpu-a', 'gpu', 5), ('gpu-b', 'gpu', None),
    ('any-a', None, None), ('any-b', None, 2),
)


def lines_for(zone: str) -> List[str]:
    """Tasks of one zone across the groups; one task in group 0 fails on its first attempt only."""
    lines = [f'echo "$ZONE_MARK {zone} {g}-{n}"  #HYPERSHELL: group:{g}'
             for g in range(GROUPS) for n in range(PER_GROUP)]
    lines.append(f'echo "$ZONE_MARK {zone} retry"; test "$TASK_ATTEMPT" -gt 1 || exit 3  #HYPERSHELL: group:0')
    return lines


def task_rows() -> List[Dict[str, Any]]:
    """Every task row."""
    with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM task')]


@mark.integration
def test_mixed_zone_workload(group: Group) -> None:
    """Every task completes, only on a client of its zone; retries keep their zone; groups run in order."""
    port, key = int(os.environ['HYPERSHELL_SERVER_PORT']), secrets.token_hex(16)
    deadline = time.monotonic() + DEADLINE
    for zone in ZONES:
        taskfile = create_taskfile(group.site, lines_for(zone), filename=f'{zone}.in')
        options = [] if zone == 'default' else ['--zone', zone]
        submit = group.start(f'submit-{zone}', 'submit', str(taskfile), *options)
        assert group.wait(submit, deadline) == 0, group.text(f'submit-{zone}')

    server = group.start('server', 'server', '--restart', '-r', '1', '-b', '4',
                         '-H', 'localhost', '-p', str(port), '-k', key)
    wait_listening(server, port, group, 'server')
    clients = {}
    for name, zone, size in CLIENTS:
        options = (['--zone', zone] if zone else []) + (['-b', str(size)] if size else [])
        clients[name] = group.start(name, 'client', '-N2', '-H', 'localhost', '-p', str(port), '-k', key,
                                    '-T', '60', *options, env={'ZONE_MARK': zone or 'default'})
    assert group.wait(server, deadline) == 0, group.text('server')
    for name, proc in clients.items():
        assert group.wait(proc, deadline) == 0, group.text(name)

    rows = task_rows()
    expected = len(ZONES) * (GROUPS * PER_GROUP + 1)
    assert len(rows) == expected + len(ZONES)  # one retry row per zone
    assert all(row['completion_time'] is not None for row in rows)
    names = {None: 'default', zone_id('cpu'): 'cpu', zone_id('gpu'): 'gpu'}
    for row in rows:
        assert names[row['zone']] in row['args'], row

    retries = [row for row in rows if row['attempt'] == 2]
    parents = {row['id']: row for row in rows}
    assert len(retries) == len(ZONES)
    for retry in retries:
        assert retry['exit_status'] == 0
        assert retry['zone'] == parents[retry['previous_id']]['zone']

    output = [line.split() for name, _, _ in CLIENTS for line in group.text(name, 'out').splitlines()]
    assert len(output) == len(rows)
    assert all(mark_ == zone for mark_, zone, _ in output), [line for line in output if line[0] != line[1]]
    for name, zone, size in CLIENTS:
        sizes = received(group.text(name))
        assert sizes, name
        assert max(sizes) <= (size or 4), (name, sizes)

    runs = Counter(task_id for name, _, _ in CLIENTS for task_id in RUNNING.findall(group.text(name)))
    assert set(runs) == {row['id'] for row in rows} and set(runs.values()) == {1}

    by_group = {g: [row for row in rows if row['group'] == g] for g in range(GROUPS)}
    for g in range(GROUPS - 1):
        assert max(r['completion_time'] for r in by_group[g]) <= min(r['schedule_time'] for r in by_group[g + 1])
