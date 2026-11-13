#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Collect one run of the multi-node topology drive into its record.

    topology_collect.py RUN_DIR OUT_FILE

RUN_DIR is the run's transport directory (plan, server and relay logs, launch records, the task
export, the drive's events); OUT_FILE is the record `topology_judge.py` judges. The record holds
counts, booleans, zone and site names, node indices and times in seconds from the run's start:
no host, user, path or key. It is scanned for each of those before it is written, and what is
found is counted under `scrub` (a clean record has every count 0). Standard library only.

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
STAMP = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?)\s')
REGISTERED = re.compile(r'Relay site registered \(site: ([^,]+), zone: ([^,]+), .*\) \(.*: ([0-9a-f-]{36})\)\s*$')
EVICTED = re.compile(r'Evicting client \(.*: ([0-9a-f-]{36})\)')
REVERTED = re.compile(r'Reverting orphaned tasks \(.*: ([0-9a-f-]{36})\)')
LEFT = re.compile(r'Relay site left \(site: ([^,]+), zone: ([^)]+)\) \(.*: ([0-9a-f-]{36})\)')
DROPPED = re.compile(r'Site (\S+) left the topology')
PUBLISHED = re.compile(r'Topology \S+ published')
DUPLICATE = re.compile(r'advertised by two relays')


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
    rel = lambda t: None if t is None else round(t - t0, 2)
    nodes = os.environ.get('HSD_NODES', '').split()
    index = {name.split('.')[0]: i for i, name in enumerate(nodes)}

    # Server log: registrations (identity -> site, zone), evictions, reverts, departures.
    identity: Dict[str, Dict[str, str]] = {}
    sites: Dict[str, Dict[str, Any]] = {
        site: {'registered': [], 'evicted': [], 'reverted': [], 'left': []} for site in ('A', 'B')}
    dropped: List[Dict[str, Any]] = []
    published = duplicates = 0
    for line in read_lines(run / 'server.log'):
        t = rel(log_time(line))
        if match := REGISTERED.search(line):
            site, zone, uuid = match.groups()
            identity[uuid] = {'site': site, 'zone': zone}
            sites.setdefault(site, {'registered': [], 'evicted': [], 'reverted': [], 'left': []})
            sites[site]['registered'].append({'zone': zone, 't': t})
        elif match := EVICTED.search(line):
            if who := identity.get(match.group(1)):
                sites[who['site']]['evicted'].append({'zone': who['zone'], 't': t})
            else:
                sites.setdefault('other', {'registered': [], 'evicted': [], 'reverted': [], 'left': []})
                sites['other']['evicted'].append({'zone': None, 't': t})
        elif match := REVERTED.search(line):
            if who := identity.get(match.group(1)):
                sites[who['site']]['reverted'].append({'zone': who['zone'], 't': t})
        elif match := LEFT.search(line):
            site, zone, _ = match.groups()
            sites.setdefault(site, {'registered': [], 'evicted': [], 'reverted': [], 'left': []})
            sites[site]['left'].append({'zone': zone, 't': t})
        elif match := DROPPED.search(line):
            dropped.append({'site': match.group(1), 't': t})
        elif PUBLISHED.search(line):
            published += 1
        elif DUPLICATE.search(line):
            duplicates += 1

    # Relays: how each ended, and what its log says of its site clients.
    relays: Dict[str, Any] = {}
    for site in ('A', 'B'):
        ended = read_json(run / f'relay-{site}.exit') or {}
        log = read_lines(run / f'relay-{site}.log')
        relays[site] = {
            'started': bool(ended.get('started')), 'rc': ended.get('rc'),
            'killed_at': rel(ended.get('killed_at')), 'drained_at': rel(ended.get('drained_at')),
            'stopped': bool(ended.get('stopped')),
            'idle_exits': sum('Autoscale client disconnected' in line for line in log),
            'nonzero_exits': sum(bool(re.search(r'Autoscale client \(\d+\) exited with status', line)) for line in log),
            'terminating': sum('Terminating site client' in line for line in log),
            'errors': sum(bool(re.search(r'\b(ERROR|CRITICAL)\b', line)) for line in log),
            **sites.get(site, {}),
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
            'site': start.get('site'), 'word': start.get('word'), 'env': start.get('env'),
            'client': start.get('client'), 'node': index.get(start.get('node', '').split('.')[0], -1),
            'key': start.get('key') == '1',
            't_launch': rel(number(start.get('t_launch'))), 't_start': rel(number(start.get('t_start'))),
            't_kill': rel(number(kill.get('t'))), 't_end': rel(number(end.get('t_end'))),
            'status': int(end['status']) if end.get('status', '').lstrip('-').isdigit() else None,
            'killed': end.get('killed') == '1', 'signal': None if end.get('signal', '-') == '-' else end['signal'],
        })
    launches.sort(key=lambda item: (item['t_launch'] is None, item['t_launch'] or 0))

    # Tasks, from the server's export.
    tasks = read_json(run / 'tasks.json') or []
    groups: Dict[int, Dict[str, Any]] = {}
    for task in tasks:
        group = groups.setdefault(task['group'], {'group': task['group'], 'tasks': 0, 'completed': 0,
                                                   'succeeded': 0, 'failed': 0, 'zones': {},
                                                   'first_schedule': None, 'last_completion': None})
        group['tasks'] += 1
        group['zones'][task.get('zone') or '-'] = group['zones'].get(task.get('zone') or '-', 0) + 1
        done = iso_time(task.get('completion_time'))
        scheduled = iso_time(task.get('schedule_time'))
        if done is not None:
            group['completed'] += 1
            last = group['last_completion']
            group['last_completion'] = rel(done) if last is None else max(last, rel(done))
        if task.get('exit_status') == 0:
            group['succeeded'] += 1
        elif task.get('exit_status') is not None:
            group['failed'] += 1
        if scheduled is not None:
            first = group['first_schedule']
            group['first_schedule'] = rel(scheduled) if first is None else min(first, rel(scheduled))
    completions = [g['last_completion'] for g in groups.values() if g['last_completion'] is not None]

    server_exit = read_json(run / 'server.exit') or {}
    try:
        version = subprocess.run([os.environ.get('HS', 'hs'), '--version'], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=60).stdout.split()
        hypershell = next((word.lstrip('v') for word in version if word[:2] in ('v2', 'v3')), None)
    except (OSError, subprocess.SubprocessError):
        hypershell = None

    return {
        'schema': SCHEMA, 'scenario': plan.get('scenario'), 'run_id': run.name,
        'label': os.environ.get('HSD_LABEL', 'feature'), 'hypershell': hypershell,
        'python': '.'.join(map(str, sys.version_info[:3])),
        'local': os.environ.get('HSD_LOCAL', '') == '1', 'nodes': len(nodes),
        'distribution': plan.get('distribution'), 'sites': plan.get('sites', []),
        'bounds': plan.get('bounds', {}), 'settings': plan.get('settings', {}),
        'workload': [{'group': g, 'zone': z, 'tasks': n, 'sleep': s} for g, z, n, s in plan.get('workload', [])],
        'tasks': {'total': sum(n for _, _, n, _ in plan.get('workload', [])), 'rows': len(tasks),
                  'completed': sum(g['completed'] for g in groups.values()),
                  'succeeded': sum(g['succeeded'] for g in groups.values()),
                  'failed': sum(g['failed'] for g in groups.values()),
                  'max_attempt': max((task.get('attempt') or 1 for task in tasks), default=0)},
        'groups': [groups[g] for g in sorted(groups)],
        'last_completion': max(completions) if completions else None,
        'launches': launches, 'relays': relays, 'dropped': dropped,
        'topology_versions': published, 'duplicate_warnings': duplicates,
        'server': {'rc': server_exit.get('rc'), 'exported': bool(server_exit.get('exported'))},
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
    count = lambda found: sum(text.count(item) for item in found if item and len(item) > 1)
    return {'hosts': count(hosts - {'A', 'B'}), 'users': count(users), 'paths': count(paths) + text.count('/'),
            'key': count({key}), 'words': count(words)}


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
