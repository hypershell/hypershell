#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge the records of multi-node topology drive runs.

A record is one JSON file written by `topology_collect.py` for one run of `topology_cluster.sh`:
counts, booleans, site and zone names, node indices and times in seconds from the run's start.

    topology_judge.py PATH [--require spread-kill,fill]

PATH is one record or a directory holding records (searched recursively for `*.json`). Each run is
printed with every check as PASS or FAIL; the last line is the verdict. The exit status is 0 only if
at least one run was found, every run passed, and every scenario named by --require passed in some
run. Standard library only.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# Standard libs
import re
import sys
import json
import argparse
from pathlib import Path


SCHEMA = 1
SCENARIOS = ('spread-kill', 'fill')
SERVES = {'A': ('cpu', 'highmem'), 'B': ('cpu', 'gpu')}
SAFE = re.compile(r'^[A-Za-z0-9 ._:,+-]*$')
SKEW = 1.0  # seconds: tolerance between node clocks where an order across nodes is judged

Check = Tuple[str, bool, str]
Record = Dict[str, Any]
INF = float('inf')


def strings(value: Any) -> Iterator[str]:
    """Every string in a record, keys included."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def of(record: Record, site: str, zone: Optional[str] = None) -> List[Dict[str, Any]]:
    """The launches of a site (and zone)."""
    return [launch for launch in record.get('launches', [])
            if launch.get('site') == site and (zone is None or launch.get('word') == zone)]


def start_of(launch: Dict[str, Any]) -> float:
    """When a launch was made (never, if unrecorded)."""
    return INF if launch.get('t_launch') is None else launch['t_launch']


def end_of(launch: Dict[str, Any]) -> float:
    """When a launch ended (never, if it has no exit)."""
    return INF if launch.get('t_end') is None else launch['t_end']


def running(launches: Sequence[Dict[str, Any]], t: float) -> int:
    """How many of the launches run at `t`."""
    return sum(1 for item in launches if item.get('t_launch') is not None and item['t_launch'] <= t < end_of(item))


def peak(launches: Sequence[Dict[str, Any]], begin: float = -INF, until: float = INF) -> int:
    """The most of the launches running at once within [begin, until)."""
    points = [begin] if begin > -INF else []
    points += [item['t_launch'] for item in launches
               if item.get('t_launch') is not None and begin <= item['t_launch'] < until]
    return max((running(launches, t) for t in points), default=0)


def first_time(entries: Sequence[Dict[str, Any]], **match: Any) -> Optional[float]:
    """The earliest time of the log entries that match."""
    times = [entry['t'] for entry in entries
             if entry.get('t') is not None and all(entry.get(k) == v for k, v in match.items())]
    return min(times) if times else None


def common(record: Record) -> List[Check]:
    """Checks every scenario must pass."""
    checks: List[Check] = []
    add = lambda name, ok, detail: checks.append((name, bool(ok), detail))
    tasks = record.get('tasks', {})
    events = record.get('events', {})
    bounds = record.get('bounds', {})
    launches = record.get('launches', [])
    relays = record.get('relays', {})
    killed_site = 'B' if record.get('scenario') == 'spread-kill' else None
    kill_t = events.get('kill')

    add('record.schema', record.get('schema') == SCHEMA and record.get('scenario') in SCENARIOS,
        f'schema {record.get("schema")}, scenario {record.get("scenario")}')
    add('run.deadline', 'deadline' not in events and 'open' not in events,
        'finished within its deadline' if 'deadline' not in events else 'tasks remained at the deadline')
    total = tasks.get('total', 0)
    add('tasks.complete', total > 0 and tasks.get('rows') == total and tasks.get('completed') == total
        and tasks.get('succeeded') == total,
        f'{tasks.get("succeeded")} of {total} succeeded ({tasks.get("rows")} rows, {tasks.get("completed")} completed)')
    add('tasks.failed', tasks.get('failed') == 0, f'{tasks.get("failed")} failed')
    groups = record.get('groups', [])
    order = len(groups) >= 2
    for earlier, later in zip(groups, groups[1:]):
        if (earlier.get('last_completion') is None or later.get('first_schedule') is None
                or earlier['last_completion'] > later['first_schedule']):
            order = False
    add('tasks.group_order', order,
        ', '.join(f'group {g.get("group")}: scheduled from {g.get("first_schedule")} s, '
                  f'last completed {g.get("last_completion")} s' for g in groups))

    add('launch.some', len(launches) > 0, f'{len(launches)} site clients launched')
    unnamed = [item for item in launches
               if not (item.get('word') == item.get('env') == item.get('client') and item.get('word') not in (None, '-'))]
    add('launch.zone_named', launches and not unnamed,
        f'{len(unnamed)} launches whose {{zone}}, HYPERSHELL_ZONE and --zone differ or are unset')
    misplaced = [item for item in launches if item.get('word') not in bounds.get(item.get('site'), {})]
    side = {zone: {site for site in SERVES if of(record, site, zone)} for zone in ('highmem', 'gpu')}
    add('launch.placement', not misplaced and side['highmem'] == {'A'} and side['gpu'] == {'B'},
        f'{len(misplaced)} launches in a zone their site does not serve; highmem at '
        f'{sorted(side["highmem"]) or "none"}, gpu at {sorted(side["gpu"]) or "none"}')
    nodes = sorted({item.get('node') for item in launches}, key=str)
    add('launch.nodes', launches and all(isinstance(n, int) and n >= 1 for n in nodes),
        f'site clients on nodes {nodes} (node 0 is the server)')
    over = []
    for site, zones in bounds.items():
        for zone, bound in zones.items():
            top = peak(of(record, site, zone))
            if top > bound.get('max', 0):
                over.append(f'{site} {zone} {top} > {bound.get("max")}')
    peaks = ', '.join(f'{s} {z} {peak(of(record, s, z))}/{b.get("max")}' for s, zs in bounds.items() for z, b in zs.items())
    add('launch.peak', not over, f'peak/max: {peaks}' + (f'; over: {over}' if over else ''))
    keyed = sum(1 for item in launches if item.get('key'))
    add('launch.key', not keyed, f'{keyed} site clients with the upstream key in their environment')

    open_ = [item for item in launches if item.get('t_end') is None]
    add('exit.all', launches and not open_, f'{len(open_)} site clients with no recorded exit')
    by_kill = lambda item: (item.get('site') == killed_site and kill_t is not None
                            and end_of(item) >= kill_t - SKEW)
    bad = [item for item in launches if not by_kill(item)
           and not (item.get('status') == 0 and not item.get('killed') and item.get('signal') is None)]
    killed = sum(1 for item in launches if by_kill(item))
    add('exit.idle', launches and not bad,
        f'{len(launches) - killed - len(bad)} idle exits (status 0), {killed} by the kill, {len(bad)} otherwise')
    terminating = {site: relay.get('terminating') for site, relay in relays.items()}
    add('relay.no_terminate', all(v == 0 for v in terminating.values()) and len(terminating) == 2,
        f'"Terminating site client" lines: {terminating}')
    seen = {}
    for site, relay in relays.items():
        if site != killed_site:
            seen[site] = (relay.get('idle_exits'), relay.get('nonzero_exits'), len(of(record, site)))
    add('relay.idle_seen', seen and all(idle == n > 0 and nonzero == 0 for idle, nonzero, n in seen.values()),
        ', '.join(f'{s}: {i} idle exits and {z} non-zero of {n} launches' for s, (i, z, n) in seen.items()))
    quiet = events.get('quiet')
    late = [item for item in launches if quiet is not None and (item.get('t_launch') or 0) > quiet]
    add('launch.none_after_idle', quiet is not None and not late and events.get('observed', 0) - quiet >= 5,
        f'{len(late)} launches after every client had left at {quiet} s '
        f'(observed until {events.get("observed")} s)')
    stayed, drained = [], []
    for site, relay in relays.items():
        if site == killed_site:
            continue
        early = [entry for entry in relay.get('left', [])
                 if relay.get('drained_at') is None or entry.get('t') is None or entry['t'] < relay['drained_at']]
        stayed.append(f'{site}: {len(relay.get("evicted", []))} evictions, {len(early)} departures before the drain')
        if relay.get('evicted') or early or not relay.get('registered'):
            stayed.append('bad')
        drained.append((site, relay.get('drained_at') is not None and relay.get('rc') == 0, relay.get('rc')))
    add('relay.stayed', 'bad' not in stayed, '; '.join(s for s in stayed if s != 'bad'))
    add('relay.drained', drained and all(ok for _, ok, _ in drained),
        ', '.join(f'{site} drained, status {rc}' for site, _, rc in drained))
    scrub = record.get('scrub', {})
    unsafe = [text for text in strings(record) if not SAFE.match(text)]
    add('record.scrubbed', scrub and not any(scrub.values()) and not unsafe,
        f'found: {scrub}; {len(unsafe)} strings with a path or other unsafe character')
    return checks


def spread_kill(record: Record) -> List[Check]:
    """Spread across both sites, then relay B killed: eviction, revert and regrowth at A."""
    checks: List[Check] = []
    add = lambda name, ok, detail: checks.append((name, bool(ok), detail))
    events = record.get('events', {})
    relays = record.get('relays', {})
    kill_t = events.get('kill')
    b = relays.get('B', {})
    add('spread.distribution', record.get('distribution') == 'spread', f'distribution {record.get("distribution")}')
    before = {site: sum(1 for item in of(record, site, 'cpu') if kill_t is not None and start_of(item) < kill_t)
              for site in SERVES}
    add('spread.cpu_both', all(before.values()), f'cpu launches before the kill: {before}')
    gpu_done = next((g.get('last_completion') for g in record.get('groups', []) if g.get('group') == 0), None)
    add('spread.side_done_first', kill_t is not None and gpu_done is not None and gpu_done <= kill_t,
        f'group 0 (highmem, gpu) last completed {gpu_done} s; kill {kill_t} s')
    add('kill.done', kill_t is not None and b.get('killed_at') is not None,
        f'kill requested {kill_t} s, relay B killed {b.get("killed_at")} s')
    zones = sorted({entry.get('zone') for entry in b.get('registered', [])})
    evicted = {zone: first_time(b.get('evicted', []), zone=zone) for zone in zones}
    reverted = {zone: first_time(b.get('reverted', []), zone=zone) for zone in zones}
    add('kill.evicted', zones and kill_t is not None
        and all(t is not None and t >= kill_t for t in evicted.values())
        and all(t is not None and t >= kill_t for t in reverted.values()),
        f'B evicted {evicted}, orphans reverted {reverted}')
    dropped = [entry.get('t') for entry in record.get('dropped', []) if entry.get('site') == 'B']
    add('kill.dropped', dropped and kill_t is not None and min(dropped) >= kill_t,
        f'site B left the topology at {dropped} s')
    evict_t = evicted.get('cpu')
    a_cpu = of(record, 'A', 'cpu')
    after = [item for item in a_cpu if evict_t is not None and evict_t <= start_of(item) < INF]
    held = peak(a_cpu, kill_t, evict_t) if kill_t is not None and evict_t is not None else None
    grown = peak(a_cpu, evict_t) if evict_t is not None else None
    add('kill.regrow', after and held is not None and grown is not None and grown > held,
        f'A cpu: {len(after)} launches after B\'s eviction; peak {held} between the kill and the eviction, '
        f'{grown} after it')
    return checks


def fill(record: Record) -> List[Check]:
    """Fill in the listed order: the preferred site saturates cpu before the other launches any."""
    checks: List[Check] = []
    add = lambda name, ok, detail: checks.append((name, bool(ok), detail))
    relays = record.get('relays', {})
    order = record.get('sites') or []
    add('fill.distribution', record.get('distribution') == 'fill' and len(order) == 2,
        f'distribution {record.get("distribution")}, sites {order}')
    if len(order) != 2:
        return checks
    preferred, other = order
    registered = {site: first_time(relays.get(site, {}).get('registered', []), zone='cpu') for site in order}
    add('fill.listed_over_rank', None not in registered.values() and registered[other] < registered[preferred],
        f'cpu registered: {registered} (the preferred site registered last)')
    later = [item['t_launch'] for item in of(record, other, 'cpu') if item.get('t_launch') is not None]
    first_other = min(later) if later else None
    ahead = sum(1 for item in of(record, preferred, 'cpu')
                if first_other is not None and start_of(item) < first_other)
    top = record.get('bounds', {}).get(preferred, {}).get('cpu', {}).get('max')
    add('fill.saturate', first_other is not None and top is not None and ahead >= top,
        f'{preferred} launched {ahead} cpu clients (max {top}) before {other}\'s first at {first_other} s')
    add('fill.no_kill', 'kill' not in record.get('events', {})
        and not any(relay.get('evicted') for relay in relays.values()),
        'no relay killed or evicted')
    return checks


def judge(record: Record) -> List[Check]:
    """Every check of one run."""
    checks = common(record)
    if record.get('scenario') == 'spread-kill':
        checks += spread_kill(record)
    elif record.get('scenario') == 'fill':
        checks += fill(record)
    return checks


def records(path: Path) -> Iterator[Tuple[Path, Record]]:
    """The records under a path."""
    files = [path] if path.is_file() else sorted(path.rglob('*.json'))
    for file in files:
        try:
            value = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and 'scenario' in value and 'launches' in value:
            yield file, value


def main(argv: Optional[List[str]] = None) -> int:
    """Judge every run found; print each check and the verdict."""
    parser = argparse.ArgumentParser(prog='topology_judge.py', description=__doc__.splitlines()[0])
    parser.add_argument('path', type=Path)
    parser.add_argument('--require', default='', help='scenarios that must have passed (comma separated)')
    args = parser.parse_args(argv)
    required = [name for name in args.require.split(',') if name]
    unknown = [name for name in required if name not in SCENARIOS]
    if unknown:
        print(f'topology_judge: unknown scenarios: {", ".join(unknown)}', file=sys.stderr)
        return 2
    passed, failed = set(), 0
    found = 0
    for file, record in records(args.path):
        found += 1
        checks = judge(record)
        ok = all(result for _, result, _ in checks)
        print(f'{record.get("run_id", file.stem)} ({record.get("scenario")}): {"PASS" if ok else "FAIL"}')
        for name, result, detail in checks:
            print(f'  {"PASS" if result else "FAIL"} {name}: {detail}')
        if ok:
            passed.add(record.get('scenario'))
        else:
            failed += 1
    missing = [name for name in required if name not in passed]
    verdict = found > 0 and failed == 0 and not missing
    detail = f'{found} runs, {failed} failed' + (f'; required but not passed: {", ".join(missing)}' if missing else '')
    print(f'topology_judge: {"PASS" if verdict else "FAIL"} ({detail})')
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())
