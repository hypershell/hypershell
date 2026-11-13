#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Collect one run of the multi-node relay-retire drive into its record.

    retire_collect.py RUN_DIR OUT_FILE

RUN_DIR is the run's transport directory (plan, server and relay logs, how each relay ended, launch
records, the upstream task samples, the per-task run files, the task export, the drive's events);
OUT_FILE is the record `retire_judge.py` judges. Built in the shape of the relay-topology drive's
collector (`spec/relay-topology/drive/topology_collect.py`, that cycle's record).

The record holds counts, booleans, relay names (A, A2), node indices, task indices and times in
seconds from the run's start: no host, user, path, key, task id or client id. Each relay identity is
recorded by the relay that logged it (`A`, `A2`; `other` for one no relay of the run logged), each
task by its index in the server's export. The upstream samples are kept as each task's transitions:
`[t, owner]` whenever the client it is assigned to changes (owner a relay name, `other`, null for
unassigned, `done` once it leaves the incomplete set). Times keep the clock of the node that wrote
them: the server's samples and log are node 0's, relay logs and launch times the relays' node's,
client start and end the client's node's; the judge allows for skew where it compares across nodes.
The record is scanned for hosts, users, paths and the key before it is written, and what is found is
counted under `scrub` (a clean record has every count 0). Standard library only.

Scrub inputs from the environment: HSD_NODES (the allocation's node names, in order: a node is
recorded by its index), HSD_SCRUB (further words to look for, space separated), HSD_LABEL, HS.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Standard libs
import os
import re
import sys
import json
import socket
import getpass
import subprocess
from pathlib import Path
from datetime import datetime, timezone


SCHEMA = 1
NAMES = ('A', 'A2')
STAMP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\s')
UUID = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
IDENTITY = re.compile(rf'Upstream identity {UUID} for zone (\S+) at site (\S+)')
REGISTERED = re.compile(rf'Relay site registered \(site: ([^,]+), zone: ([^,]+), .*: {UUID}\)\s*$')
EVICTED = re.compile(rf'Evicting client \(.*: {UUID}\)')
REVERTED = re.compile(rf'Reverting orphaned tasks \(.*: {UUID}\)')
LEFT = re.compile(rf'Relay site left \(site: [^,]+, zone: [^)]+\) \(.*: {UUID}\)')
RETIRED = re.compile(rf'Relay {UUID} on \S+ retired at site (\S+)')
REQUEUED = re.compile(rf'Requeued (\d+) tasks handed back by relay \({UUID}\)')
HANDED = re.compile(rf'Handed back (\d+) tasks no site client started \({UUID}\)')
LAUNCHING = re.compile(r'Topology \S+ \(zone: [^)]*\) - launching ([1-9]\d*)|applying again, launching ([1-9]\d*)')
FINAL = re.compile(rf'Upstream heartbeat - final \({UUID}\)')


def log_time(line: str) -> Optional[float]:
    """Epoch seconds of a system-style log line (the drive runs every process with TZ=UTC)."""
    match = STAMP.match(line)
    if not match:
        return None
    return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc).timestamp()


def iso_time(value: Optional[str]) -> Optional[float]:
    """Epoch seconds of an exported task time (naive times are UTC, as the drive runs)."""
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def read_lines(path: Path) -> List[str]:
    """The lines of a file, or none."""
    try:
        return path.read_text(errors='replace').splitlines()
    except OSError:
        return []


def read_json(path: Path) -> Optional[Any]:
    """A JSON file, or None."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def fields(line: str) -> Dict[str, str]:
    """The key=value words of a record line."""
    return dict(item.split('=', 1) for item in line.split()[1:] if '=' in item)


def number(value: Optional[str]) -> Optional[float]:
    """A recorded number, or None."""
    try:
        return float(value) if value not in (None, '', '-') else None
    except ValueError:
        return None


def collect(run: Path) -> Dict[str, Any]:
    """The record of one run."""
    plan = read_json(run / 'plan.json') or {}
    events: Dict[str, float] = {}
    for line in read_lines(run / 'events'):
        parts = line.split()
        if len(parts) == 2:
            events.setdefault(parts[0], float(parts[1]))
    t0 = events.get('start', 0.0)
    rel = lambda t: None if t is None else round(t - t0, 3)
    nodes = os.environ.get('HSD_NODES', '').split()
    index = {name.split('.')[0]: i for i, name in enumerate(nodes)}

    # Identities: each relay's own log names the upstream identities it holds.
    owner: Dict[str, str] = {}
    for name in NAMES:
        for line in read_lines(run / f'relay-{name}.log'):
            if match := IDENTITY.search(line):
                owner[match.group(1)] = name
    label = lambda uuid: owner.get(uuid, 'other')

    # Server log.
    server: Dict[str, Any] = {'registered': [], 'evicted': [], 'reverted': [], 'left': [], 'retired': [],
                              'requeued': [], 'duplicate_warnings': 0, 'refused_handbacks': 0, 'errors': 0}
    for line in read_lines(run / 'server.log'):
        t = rel(log_time(line))
        if match := REGISTERED.search(line):
            server['registered'].append({'relay': label(match.group(3)), 'site': match.group(1),
                                         'zone': match.group(2), 't': t})
        elif match := EVICTED.search(line):
            server['evicted'].append({'relay': label(match.group(1)), 't': t})
        elif match := REVERTED.search(line):
            server['reverted'].append({'relay': label(match.group(1)), 't': t})
        elif match := LEFT.search(line):
            server['left'].append({'relay': label(match.group(1)), 't': t})
        elif match := RETIRED.search(line):
            server['retired'].append({'relay': label(match.group(1)), 'site': match.group(2), 't': t})
        elif match := REQUEUED.search(line):
            server['requeued'].append({'relay': label(match.group(2)), 'count': int(match.group(1)), 't': t})
        elif 'advertised by two relays' in line:
            server['duplicate_warnings'] += 1
        elif 'Refused hand-back' in line:
            server['refused_handbacks'] += 1
        if re.search(r'\b(ERROR|CRITICAL)\b', line):
            server['errors'] += 1
    server_exit = read_json(run / 'server.exit') or {}
    server.update(rc=server_exit.get('rc'), exported=bool(server_exit.get('exported')))

    # Relays: how each ended, and what its log says.
    relays: Dict[str, Any] = {}
    for name in NAMES:
        ended = read_json(run / f'relay-{name}.exit') or {}
        lines = read_lines(run / f'relay-{name}.log')
        timed = [(rel(log_time(line)), line) for line in lines]
        superseded = [t for t, line in timed if 'Superseded at site' in line]
        relays[name] = {
            'started': bool(ended.get('started')), 'rc': ended.get('rc'), 'self': bool(ended.get('self')),
            'started_at': rel(ended.get('started_at')), 'exited_at': rel(ended.get('exited_at')),
            'drained_at': rel(ended.get('drained_at')), 'stopped': bool(ended.get('stopped')),
            'stopped_at': rel(ended.get('stopped_at')),
            'identities': sum(1 for held_by in owner.values() if held_by == name),
            'superseded': len(superseded), 'mark': superseded[0] if superseded else None,
            'handed_back': [{'count': int(match.group(1)), 't': t} for t, line in timed
                            for match in [HANDED.search(line)] if match],
            'handback_failed': sum('Could not hand back' in line for line in lines),
            'final_heartbeats': [{'relay': label(match.group(1)), 't': t} for t, line in timed
                                 for match in [FINAL.search(line)] if match],
            'launching': [{'count': int(match.group(1) or match.group(2)), 't': t} for t, line in timed
                          for match in [LAUNCHING.search(line)] if match],
            'terminating': sum('Terminating site client' in line for line in lines),
            'topology_errors': sum('Topology not read' in line for line in lines),
            'drain_requested': next((t for t, line in timed if 'Drain requested' in line), None),
            'errors': sum(bool(re.search(r'\b(ERROR|CRITICAL)\b', line)) for line in lines),
        }

    # Launches: one record per site client.
    launches = []
    for path in sorted((run / 'launches').glob('*.rec')):
        lines = read_lines(path)
        if not lines or not lines[0].startswith('start '):
            continue
        start = fields(lines[0])
        kill = next((fields(line) for line in lines[1:] if line.startswith('kill ')), {})
        end = next((fields(line) for line in lines[1:] if line.startswith('exit ')), {})
        launches.append({
            'relay': start.get('site'), 'word': start.get('word'), 'env': start.get('env'),
            'client': start.get('client'), 'node': index.get(start.get('node', '').split('.')[0], -1),
            'key': start.get('key') == '1',
            't_launch': rel(number(start.get('t_launch'))), 't_start': rel(number(start.get('t_start'))),
            't_kill': rel(number(kill.get('t'))), 't_end': rel(number(end.get('t_end'))),
            'status': int(end['status']) if end.get('status', '').lstrip('-').isdigit() else None,
            'killed': end.get('killed') == '1', 'signal': None if end.get('signal', '-') == '-' else end['signal'],
        })
    launches.sort(key=lambda item: (item['t_launch'] is None, item['t_launch'] or 0))

    # Tasks, from the server's export; each by its index there.
    tasks = read_json(run / 'tasks.json') or []
    position = {task['id']: i for i, task in enumerate(tasks)}
    counts = {'total': sum(n for _, _, n, _ in plan.get('workload', [])), 'rows': len(tasks),
              'completed': sum(1 for task in tasks if task.get('completion_time')),
              'succeeded': sum(1 for task in tasks if task.get('exit_status') == 0),
              'failed': sum(1 for task in tasks if task.get('exit_status') not in (None, 0)),
              'max_attempt': max((task.get('attempt') or 1 for task in tasks), default=0)}
    runs: Dict[int, int] = {}
    unknown_runs = 0
    for path in (run / 'runs').glob('*'):
        task = path.name.rsplit('.', 1)[0]
        if task in position:
            runs[position[task]] = runs.get(position[task], 0) + 1
        else:
            unknown_runs += 1
    counts.update(runs=sum(runs.values()) + unknown_runs, ran=len(runs), unknown_runs=unknown_runs,
                  repeated=sorted(i for i, n in runs.items() if n > 1))

    # Upstream samples, as each task's transitions.
    transitions: Dict[int, List[Any]] = {}
    state: Dict[int, Any] = {}
    times: List[float] = []
    for line in read_lines(run / 'held.jsonl'):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        t = rel(data['t'])
        times.append(t)
        rows = {position.get(task, -1): (None if client is None else label(client)) for task, client in data['rows']}
        rows.pop(-1, None)
        for task in sorted(set(state) | set(rows)):
            now = rows[task] if task in rows else 'done'
            if task in state and state[task] == now:
                continue
            transitions.setdefault(task, []).append([t, now])
            state[task] = now
    gaps = [b - a for a, b in zip(times, times[1:])]
    samples = {'count': len(times), 'first': times[0] if times else None, 'last': times[-1] if times else None,
               'max_gap': round(max(gaps), 3) if gaps else None}

    try:
        version = subprocess.run([os.environ.get('HS', 'hs'), '--version'], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=60).stdout.split()
        hypershell = next((word.lstrip('v') for word in version if word[:2] in ('v2', 'v3')), None)
    except (OSError, subprocess.SubprocessError):
        hypershell = None

    trigger = read_json(run / 'held-A') or {}
    return {
        'schema': SCHEMA, 'scenario': plan.get('scenario'), 'run_id': run.name,
        'label': os.environ.get('HSD_LABEL', 'feature'), 'hypershell': hypershell,
        'python': '.'.join(map(str, sys.version_info[:3])),
        'local': os.environ.get('HSD_LOCAL', '') == '1', 'nodes': len(nodes),
        'bounds': plan.get('bounds', {}), 'settings': plan.get('settings', {}),
        'workload': [{'group': g, 'zone': z, 'tasks': n, 'sleep': s} for g, z, n, s in plan.get('workload', [])],
        'tasks': counts, 'launches': launches, 'relays': relays, 'server': server,
        'trigger': {'t': rel(trigger.get('t')), 'undelivered': trigger.get('undelivered')},
        'samples': samples, 'transitions': {str(task): steps for task, steps in sorted(transitions.items())},
        'events': {name: rel(t) for name, t in sorted(events.items()) if name != 'start'},
        'wall_s': rel(events.get('end')),
    }


def scrub(record: Dict[str, Any], run: Path) -> Dict[str, int]:
    """Count each kind of identifying text found in the record."""
    text = json.dumps(record)
    hosts = {name for full in os.environ.get('HSD_NODES', '').split() for name in (full, full.split('.')[0])}
    hosts |= {socket.gethostname(), socket.gethostname().split('.')[0]}
    users = {getpass.getuser(), os.environ.get('USER', '')}
    paths = {str(run), str(run.parent), os.environ.get('HOME', ''), os.environ.get('HSD_WORK', ''),
             os.environ.get('HSD_RESULTS', ''), os.environ.get('HSD_NODE_TMP', ''), str(Path(__file__).parent)}
    words = set(os.environ.get('HSD_SCRUB', '').split())
    try:
        key = (run / 'key').read_text().strip()
    except OSError:
        key = ''
    count = lambda found: sum(text.count(item) for item in found if item and len(item) > 2)
    return {'hosts': count(hosts - set(NAMES)), 'users': count(users), 'paths': count(paths) + text.count('/'),
            'key': count({key}), 'words': count(words),
            'uuids': len(re.findall(UUID, text))}


def main(argv: List[str]) -> int:
    """Write the record of one run."""
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 64
    run, out = Path(argv[0]), Path(argv[1])
    record = collect(run)
    record['scrub'] = scrub(record, run)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
