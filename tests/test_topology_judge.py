# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The multi-node topology drive's judge (`spec/relay-topology/drive/topology_judge.py`) on synthetic records.

A record is one run of `topology_cluster.sh`, as `topology_collect.py` writes it: the plan (bounds,
distribution, workload), the task counts per group, one entry per site client launched (its site,
zone words, node, launch and exit times, exit status), each relay's registrations, evictions and
how it ended, and the drive's events, all in seconds from the run's start. A good record of each
scenario passes every check; each bad record below breaks one thing the multi-node run must show,
and the judge names the check it fails.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List

# Standard libs
import copy
import json
import importlib.util
from pathlib import Path

# External libs
from pytest import mark, fixture


JUDGE = Path(__file__).parent.parent / 'spec' / 'relay-topology' / 'drive' / 'topology_judge.py'
SCENARIOS = ('spread-kill', 'fill')
Record = Dict[str, Any]


@fixture(scope='module')
def judge():
    """The judge module, loaded from its path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('topology_judge', JUDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def launch(site: str, zone: str, node: int, t_launch: float, t_end: float, status: int = 0,
           killed: bool = False) -> Dict[str, Any]:
    """One site client's entry."""
    return {'site': site, 'word': zone, 'env': zone, 'client': zone, 'node': node, 'key': False,
            't_launch': t_launch, 't_start': t_launch + 0.5, 't_kill': t_end if killed else None,
            't_end': t_end, 'status': status, 'killed': killed, 'signal': None}


def relay(zones: List[str], registered: float, **fields: Any) -> Dict[str, Any]:
    """One relay's entry: registered at `registered`, drained at 230 s with status 0 unless given."""
    entry = {'started': True, 'rc': 0, 'killed_at': None, 'drained_at': 230.0, 'stopped': False,
             'idle_exits': 0, 'nonzero_exits': 0, 'terminating': 0, 'errors': 0,
             'registered': [{'zone': zone, 't': registered} for zone in zones],
             'evicted': [], 'reverted': [], 'left': [{'zone': zone, 't': 231.0} for zone in zones]}
    entry.update(fields)
    return entry


def base(scenario: str, bounds: Dict[str, Any], workload: List[Dict[str, Any]],
         groups: List[Dict[str, Any]]) -> Record:
    """The fields every record has."""
    total = sum(item['tasks'] for item in workload)
    return {
        'schema': 1, 'scenario': scenario, 'run_id': f'test-{scenario}', 'label': 'feature',
        'hypershell': '2.9.0a7', 'python': '3.12.13', 'local': False, 'nodes': 4,
        'bounds': bounds, 'workload': workload,
        'settings': {'period': 5, 'factor': 1.0, 'evict': 20, 'heartrate': 2, 'timeout': 10, 'threads': 2},
        'tasks': {'total': total, 'rows': total, 'completed': total, 'succeeded': total, 'failed': 0,
                  'max_attempt': 1},
        'groups': groups, 'last_completion': groups[-1]['last_completion'],
        'topology_versions': 40, 'duplicate_warnings': 0, 'dropped': [],
        'server': {'rc': 0, 'exported': True}, 'wall_s': 240.0,
        'scrub': {'hosts': 0, 'users': 0, 'paths': 0, 'key': 0, 'words': 0},
    }


def group(number: int, tasks: int, first: float, last: float) -> Dict[str, Any]:
    """One group's entry."""
    return {'group': number, 'tasks': tasks, 'completed': tasks, 'succeeded': tasks, 'failed': 0,
            'zones': {}, 'first_schedule': first, 'last_completion': last}


def spread_kill() -> Record:
    """A clean spread-kill run: both sites grow cpu, B is killed at 40 s and evicted at 62 s, A regrows."""
    bounds = {'A': {'cpu': {'min': 0, 'max': 6}, 'highmem': {'min': 0, 'max': 2}},
              'B': {'cpu': {'min': 0, 'max': 6}, 'gpu': {'min': 0, 'max': 2}}}
    workload = [{'group': 0, 'zone': 'highmem', 'tasks': 16, 'sleep': 2},
                {'group': 0, 'zone': 'gpu', 'tasks': 16, 'sleep': 2},
                {'group': 1, 'zone': 'cpu', 'tasks': 600, 'sleep': 2}]
    record = base('spread-kill', bounds, workload, [group(0, 32, 5.0, 25.0), group(1, 600, 26.0, 195.0)])
    a = [launch('A', 'highmem', 1, 5.0, 30.0), launch('A', 'highmem', 3, 6.0, 31.0),
         launch('A', 'cpu', 1, 26.0, 205.0), launch('A', 'cpu', 3, 36.0, 205.0)]
    a += [launch('A', 'cpu', 1 + i % 2 * 2, 66.0 + 5 * i, 205.0) for i in range(4)]
    b = [launch('B', 'gpu', 2, 5.5, 30.0), launch('B', 'gpu', 3, 6.5, 31.0),
         launch('B', 'cpu', 2, 31.0, 41.0, status=-9, killed=True),
         launch('B', 'cpu', 3, 38.0, 41.2, status=-9, killed=True)]
    record.update(distribution='spread', sites=[], launches=sorted(a + b, key=lambda item: item['t_launch']))
    record['relays'] = {
        'A': relay(['cpu', 'highmem'], 2.0, idle_exits=len(a)),
        'B': relay(['cpu', 'gpu'], 1.0, rc=-9, killed_at=40.2, drained_at=None, idle_exits=2,
                   evicted=[{'zone': 'cpu', 't': 62.0}, {'zone': 'gpu', 't': 62.0}],
                   reverted=[{'zone': 'cpu', 't': 62.0}, {'zone': 'gpu', 't': 62.0}],
                   left=[{'zone': 'cpu', 't': 62.0}, {'zone': 'gpu', 't': 62.0}]),
    }
    record['dropped'] = [{'site': 'B', 't': 63.0}]
    record['events'] = {'registered': 3.0, 'kill': 40.0, 'completed': 196.0, 'quiet': 207.0,
                        'observed': 227.0, 'drain': 227.0, 'end': 240.0}
    return record


def fill() -> Record:
    """A clean fill run: sites A,B; B registers first; A launches its 4 cpu clients before B's first."""
    bounds = {'A': {'cpu': {'min': 0, 'max': 4}, 'highmem': {'min': 0, 'max': 2}},
              'B': {'cpu': {'min': 0, 'max': 4}, 'gpu': {'min': 0, 'max': 2}}}
    workload = [{'group': 0, 'zone': 'cpu', 'tasks': 300, 'sleep': 2},
                {'group': 1, 'zone': 'highmem', 'tasks': 8, 'sleep': 2},
                {'group': 1, 'zone': 'gpu', 'tasks': 8, 'sleep': 2}]
    record = base('fill', bounds, workload, [group(0, 300, 5.0, 90.0), group(1, 16, 91.0, 110.0)])
    a = [launch('A', 'cpu', 1 + i % 2 * 2, 5.0 + 5 * i, 100.0) for i in range(4)]
    a += [launch('A', 'highmem', 1, 92.0, 120.0)]
    b = [launch('B', 'cpu', 2, 26.0, 100.0), launch('B', 'cpu', 3, 31.0, 100.0), launch('B', 'gpu', 2, 92.0, 120.0)]
    record.update(distribution='fill', sites=['A', 'B'], launches=sorted(a + b, key=lambda item: item['t_launch']))
    record['relays'] = {'A': relay(['cpu', 'highmem'], 2.0, idle_exits=len(a)),
                        'B': relay(['cpu', 'gpu'], 1.0, idle_exits=len(b))}
    record['events'] = {'registered': 3.0, 'completed': 111.0, 'quiet': 121.0, 'observed': 141.0,
                        'drain': 141.0, 'end': 150.0}
    return record


GOOD = {'spread-kill': spread_kill, 'fill': fill}


def failed(judge, record: Record) -> List[str]:
    """Names of the checks the judge fails on `record`."""
    return [name for name, ok, _ in judge.judge(record) if not ok]


@mark.unit
@mark.parametrize('scenario', SCENARIOS)
def test_good_run_passes(judge, scenario: str) -> None:
    """A clean run of each scenario passes every check."""
    checks = judge.judge(GOOD[scenario]())
    assert checks and all(ok for _, ok, _ in checks), [c for c in checks if not c[1]]


def edit(path: str, value: Any) -> Callable[[Record], None]:
    """A mutation setting the dotted `path` (key.key... with list indices) to `value`."""
    def mutate(record: Record) -> None:
        *keys, last = path.split('.')
        target: Any = record
        for key in keys:
            target = target[int(key)] if isinstance(target, list) else target[key]
        if isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value
    return mutate


def both(*mutations: Callable[[Record], None]) -> Callable[[Record], None]:
    """A mutation applying each of `mutations` in turn."""
    def mutate(record: Record) -> None:
        for each in mutations:
            each(record)
    return mutate


def launches_where(predicate: Callable[[Dict[str, Any]], bool],
                   change: Callable[[Dict[str, Any]], None]) -> Callable[[Record], None]:
    """A mutation changing every launch that matches."""
    def mutate(record: Record) -> None:
        for item in record['launches']:
            if predicate(item):
                change(item)
    return mutate


def add_launch(*args: Any, **kwargs: Any) -> Callable[[Record], None]:
    """A mutation adding one launch (and counting its idle exit at its relay)."""
    def mutate(record: Record) -> None:
        item = launch(*args, **kwargs)
        record['launches'].append(item)
        record['relays'][item['site']]['idle_exits'] += 1
    return mutate


def first(site: str, zone: str) -> Callable[[Dict[str, Any]], bool]:
    """Matches the first launch of a site-zone only."""
    seen: List[int] = []
    def match(item: Dict[str, Any]) -> bool:
        if item['site'] == site and item['word'] == zone and not seen:
            seen.append(1)
            return True
        return False
    return match


BAD = [
    # Every scenario.
    ('spread-kill', edit('schema', 2), 'record.schema'),
    ('fill', edit('events.deadline', 400.0), 'run.deadline'),
    ('fill', edit('tasks.completed', 315), 'tasks.complete'),
    ('spread-kill', edit('tasks.succeeded', 631), 'tasks.complete'),
    ('fill', both(edit('tasks.failed', 1), edit('groups.0.failed', 1)), 'tasks.failed'),
    ('spread-kill', edit('groups.1.first_schedule', 20.0), 'tasks.group_order'),
    ('fill', edit('groups.0.last_completion', None), 'tasks.group_order'),
    ('fill', edit('launches', []), 'launch.some'),
    ('spread-kill', launches_where(first('A', 'cpu'), lambda item: item.update(env='-')), 'launch.zone_named'),
    ('fill', launches_where(first('B', 'cpu'), lambda item: item.update(client='gpu')), 'launch.zone_named'),
    ('spread-kill', add_launch('B', 'highmem', 2, 7.0, 30.0), 'launch.placement'),
    ('fill', add_launch('A', 'gpu', 1, 93.0, 120.0), 'launch.placement'),
    ('spread-kill', launches_where(lambda item: item['word'] == 'gpu', lambda item: item.update(site='A')),
     'launch.placement'),
    ('fill', launches_where(first('A', 'cpu'), lambda item: item.update(node=0)), 'launch.nodes'),
    ('spread-kill', launches_where(first('B', 'gpu'), lambda item: item.update(node=-1)), 'launch.nodes'),
    ('spread-kill', add_launch('A', 'highmem', 3, 7.0, 30.0), 'launch.peak'),
    ('fill', add_launch('A', 'cpu', 3, 22.0, 60.0), 'launch.peak'),
    ('spread-kill', launches_where(first('A', 'highmem'), lambda item: item.update(key=True)), 'launch.key'),
    ('fill', launches_where(first('B', 'gpu'), lambda item: item.update(t_end=None, status=None)), 'exit.all'),
    ('fill', launches_where(first('A', 'cpu'), lambda item: item.update(status=1)), 'exit.idle'),
    ('spread-kill', launches_where(first('A', 'cpu'), lambda item: item.update(signal='TERM', status=143)),
     'exit.idle'),
    ('spread-kill', launches_where(first('B', 'gpu'), lambda item: item.update(killed=True)), 'exit.idle'),
    ('fill', edit('relays.A.terminating', 1), 'relay.no_terminate'),
    ('spread-kill', edit('relays.B.terminating', 2), 'relay.no_terminate'),
    ('fill', edit('relays.B.idle_exits', 1), 'relay.idle_seen'),
    ('spread-kill', edit('relays.A.nonzero_exits', 1), 'relay.idle_seen'),
    ('fill', add_launch('B', 'cpu', 3, 125.0, 130.0), 'launch.none_after_idle'),
    ('spread-kill', edit('events.observed', 208.0), 'launch.none_after_idle'),
    ('fill', edit('relays.A.evicted', [{'zone': 'cpu', 't': 50.0}]), 'relay.stayed'),
    ('spread-kill', edit('relays.A.left', [{'zone': 'cpu', 't': 100.0}]), 'relay.stayed'),
    ('fill', edit('relays.B.rc', 1), 'relay.drained'),
    ('spread-kill', edit('relays.A.drained_at', None), 'relay.drained'),
    ('fill', edit('scrub.hosts', 1), 'record.scrubbed'),
    ('spread-kill', edit('label', 'run/at/a/path'), 'record.scrubbed'),
    # spread-kill.
    ('spread-kill', edit('distribution', 'fill'), 'spread.distribution'),
    ('spread-kill', launches_where(lambda item: item['site'] == 'B' and item['word'] == 'cpu',
                                   lambda item: item.update(t_launch=item['t_launch'] + 60)), 'spread.cpu_both'),
    ('spread-kill', edit('groups.0.last_completion', 45.0), 'spread.side_done_first'),
    ('spread-kill', edit('relays.B.killed_at', None), 'kill.done'),
    ('spread-kill', edit('relays.B.evicted', [{'zone': 'gpu', 't': 62.0}]), 'kill.evicted'),
    ('spread-kill', edit('relays.B.reverted', []), 'kill.evicted'),
    ('spread-kill', edit('relays.B.evicted', [{'zone': 'cpu', 't': 30.0}, {'zone': 'gpu', 't': 30.0}]),
     'kill.evicted'),
    ('spread-kill', edit('dropped', []), 'kill.dropped'),
    ('spread-kill', launches_where(lambda item: item['site'] == 'A' and item['word'] == 'cpu' and item['t_launch'] > 60,
                                   lambda item: item.update(t_launch=item['t_launch'] - 25)), 'kill.regrow'),
    # A's clients after the eviction only replace the ones it had: launched one at a time, as each left.
    ('spread-kill', both(launches_where(lambda item: item['site'] == 'A' and item['word'] == 'cpu' and item['t_launch'] < 60,
                                        lambda item: item.update(t_end=63.0)),
                         launches_where(lambda item: item['site'] == 'A' and item['word'] == 'cpu' and item['t_launch'] > 60,
                                        lambda item: item.update(t_end=item['t_launch'] + 4))),
     'kill.regrow'),
    ('spread-kill', lambda record: record.update(launches=[item for item in record['launches']
                                                           if not (item['site'] == 'A' and item['t_launch'] > 60)]),
     'kill.regrow'),
    # fill.
    ('fill', edit('sites', ['B', 'A']), 'fill.listed_over_rank'),
    ('fill', edit('relays.A.registered', [{'zone': 'cpu', 't': 0.5}]), 'fill.listed_over_rank'),
    ('fill', launches_where(first('B', 'cpu'), lambda item: item.update(t_launch=12.0)), 'fill.saturate'),
    ('fill', launches_where(lambda item: item['site'] == 'B' and item['word'] == 'cpu',
                            lambda item: item.update(site='A', node=1)), 'fill.saturate'),
    ('fill', edit('events.kill', 50.0), 'fill.no_kill'),
    ('fill', edit('distribution', 'spread'), 'fill.distribution'),
]


@mark.unit
@mark.parametrize('scenario, mutate, check', BAD, ids=[f'{s}-{c}-{i}' for i, (s, _, c) in enumerate(BAD)])
def test_bad_run_fails_its_check(judge, scenario: str, mutate, check: str) -> None:
    """Each bad run fails the check that names what it broke."""
    record = copy.deepcopy(GOOD[scenario]())
    mutate(record)
    assert check in failed(judge, record)


@mark.unit
def test_unknown_scenario_fails(judge) -> None:
    """A record of no known scenario fails its schema check."""
    record = spread_kill()
    record['scenario'] = 'other'
    assert 'record.schema' in failed(judge, record)


def write(path: Path, record: Record) -> Path:
    """Write a record to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return path


@mark.unit
def test_main_judges_every_run_under_a_directory(judge, tmp_path: Path, capsys) -> None:
    """Every record under the directory is judged, and both scenarios pass."""
    write(tmp_path / 'one' / 'r-spread-kill.json', spread_kill())
    write(tmp_path / 'two' / 'r-fill.json', fill())
    (tmp_path / 'notes.json').write_text('[1, 2]')
    assert judge.main([str(tmp_path), '--require', 'spread-kill,fill']) == 0
    out = capsys.readouterr().out
    assert 'test-spread-kill (spread-kill): PASS' in out and 'test-fill (fill): PASS' in out
    assert out.strip().splitlines()[-1] == 'topology_judge: PASS (2 runs, 0 failed)'


@mark.unit
def test_main_judges_one_record(judge, tmp_path: Path) -> None:
    """A record given by its path is judged on its own."""
    assert judge.main([str(write(tmp_path / 'r.json', fill()))]) == 0


@mark.unit
def test_main_fails_a_failing_run(judge, tmp_path: Path, capsys) -> None:
    """One failing run fails the verdict, naming its failed check."""
    record = fill()
    record['relays']['A']['terminating'] = 1
    write(tmp_path / 'a.json', spread_kill())
    write(tmp_path / 'b.json', record)
    assert judge.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert '  FAIL relay.no_terminate' in out
    assert out.strip().splitlines()[-1] == 'topology_judge: FAIL (2 runs, 1 failed)'


@mark.unit
def test_main_fails_with_no_runs(judge, tmp_path: Path, capsys) -> None:
    """No record found is a failure, not a vacuous pass."""
    assert judge.main([str(tmp_path)]) == 1
    assert 'topology_judge: FAIL (0 runs, 0 failed)' in capsys.readouterr().out


@mark.unit
def test_main_requires_named_scenarios(judge, tmp_path: Path, capsys) -> None:
    """A required scenario with no passing run fails the verdict; an unknown one is a usage error."""
    write(tmp_path / 'r.json', fill())
    assert judge.main([str(tmp_path), '--require', 'fill']) == 0
    assert judge.main([str(tmp_path), '--require', 'spread-kill,fill']) == 1
    assert 'required but not passed: spread-kill' in capsys.readouterr().out
    assert judge.main([str(tmp_path), '--require', 'kill']) == 2
