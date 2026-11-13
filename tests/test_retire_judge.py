# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay-retire judge's cluster mode (`spec/relay-retire/drive/retire_judge.py`) on synthetic records.

A record is one run of `retire_cluster.sh`, as `retire_collect.py` writes it: the settings, task
counts and per-task run counts, one entry per site client launched (relay A or A2, node, launch and
exit times, exit status), what each relay's log and role record say (A's first "Superseded" line,
its hand-backs, final heartbeats, exit status), the server's registrations, retirement, requeues,
departures and evictions, each task's transitions between owners in the upstream samples, and the
drive's events, all in seconds from the run's start. A clean record passes every check; each broken
record below breaks one thing the multi-node run must show, and the judge names the check it fails;
a record in which A held nothing unstarted and handed back nothing is inconclusive.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional

# Standard libs
import json
import importlib.util
from pathlib import Path

# External libs
from pytest import mark, fixture


JUDGE = Path(__file__).parent.parent / 'spec' / 'relay-retire' / 'drive' / 'retire_judge.py'
Record = Dict[str, Any]

MARK = 22.0          # A's first "Superseded" line
REQUEUE = 23.5       # the server's requeue of what A handed back (and the samples' sight of it)
A_DONE = 48.0        # A's clients finish the tasks they started
A_UNSTARTED = (3, 4, 5)


@fixture(scope='module')
def judge():
    """The judge module, loaded from its path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('retire_judge', JUDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def launch(relay: str, node: int, t_launch: float, t_end: Optional[float], status: int = 0,
           killed: bool = False) -> Dict[str, Any]:
    """One site client's entry."""
    return {'relay': relay, 'word': 'cpu', 'env': 'cpu', 'client': 'cpu', 'node': node, 'key': False,
            't_launch': t_launch, 't_start': t_launch + 1.0, 't_kill': t_end if killed else None,
            't_end': t_end, 'status': status, 'killed': killed, 'signal': None}


def transitions(requeue: float = REQUEUE) -> Dict[str, List[List[Any]]]:
    """Each task's owners over the run: A's clients run 0-2; A holds 3-5 unstarted and hands them back
    at `requeue`; A2's two clients run the rest, two at a time, 40 s each."""
    steps: Dict[int, List[List[Any]]] = {task: [[4.0, None]] for task in range(12)}
    for task in (0, 1, 2):
        steps[task] += [[7.0, 'A'], [A_DONE, 'done']]
    for task in A_UNSTARTED:
        steps[task] += [[13.0, 'A'], [requeue, None]]
    for i, task in enumerate([6, 7, 3, 4, 5, 8, 9, 10, 11]):
        began = 22.0 + 40.0 * (i // 2)
        steps[task] += [[began, 'A2'], [began + 40.0, 'done']]
    return {str(task): items for task, items in steps.items()}


def good() -> Record:
    """A clean retire run: A retires at 22 s, hands back 3 tasks, exits 0 at 50 s; A2 does the rest."""
    a = [launch('A', 1 + i, 5.0 + 0.5 * i, 49.0) for i in range(3)]
    a2 = [launch('A2', 1 + i, 21.0, 228.0) for i in range(2)]
    return {
        'schema': 1, 'scenario': 'retire', 'run_id': 'test-retire', 'label': 'feature',
        'hypershell': '2.9.0a8', 'python': '3.12.13', 'local': False, 'nodes': 4,
        'bounds': {'A': {'cpu': {'min': 0, 'init': 3, 'max': 3}}, 'A2': {'cpu': {'min': 0, 'init': 2, 'max': 2}}},
        'workload': [{'group': 0, 'zone': 'cpu', 'tasks': 12, 'sleep': 40}],
        'settings': {'period': 1, 'evict': 1200, 'heartrate': 2, 'timeout': 5, 'relay_period': 1, 'hold': 2,
                     'sample': 0.25, 'quiet': 5, 'run_timeout': 900},
        'tasks': {'total': 12, 'rows': 12, 'completed': 12, 'succeeded': 12, 'failed': 0, 'max_attempt': 1,
                  'runs': 12, 'ran': 12, 'unknown_runs': 0, 'repeated': []},
        'launches': a + a2,
        'relays': {
            'A': {'started': True, 'rc': 0, 'self': True, 'started_at': 2.0, 'exited_at': 50.0,
                  'drained_at': None, 'stopped': False, 'stopped_at': None, 'identities': 1,
                  'superseded': 1, 'mark': MARK, 'handed_back': [{'count': 3, 't': 23.0}],
                  'handback_failed': 0, 'final_heartbeats': [{'relay': 'A', 't': 49.8}],
                  'launching': [{'count': 3, 't': 4.5}], 'terminating': 0, 'topology_errors': 0,
                  'drain_requested': None, 'errors': 0},
            'A2': {'started': True, 'rc': 0, 'self': False, 'started_at': 15.0, 'exited_at': 243.0,
                   'drained_at': 240.0, 'stopped': False, 'stopped_at': None, 'identities': 1,
                   'superseded': 0, 'mark': None, 'handed_back': [], 'handback_failed': 0,
                   'final_heartbeats': [{'relay': 'A2', 't': 242.0}], 'launching': [{'count': 2, 't': 20.5}],
                   'terminating': 0, 'topology_errors': 0, 'drain_requested': 240.1, 'errors': 0},
        },
        'server': {'registered': [{'relay': 'A', 'site': 'A', 'zone': 'cpu', 't': 3.0},
                                  {'relay': 'A2', 'site': 'A', 'zone': 'cpu', 't': 20.0}],
                   'evicted': [], 'reverted': [],
                   'left': [{'relay': 'A', 't': 49.9}, {'relay': 'A2', 't': 242.1}],
                   'retired': [{'relay': 'A', 'site': 'A', 't': 20.3}],
                   'requeued': [{'relay': 'A', 'count': 3, 't': REQUEUE}],
                   'duplicate_warnings': 1, 'refused_handbacks': 0, 'errors': 0, 'rc': 0, 'exported': True},
        'trigger': {'t': 15.0, 'undelivered': 3},
        'samples': {'count': 960, 'first': 4.0, 'last': 244.0, 'max_gap': 0.3},
        'transitions': transitions(),
        'events': {'registered': 3.0, 'submitted': 4.0, 'held': 15.0, 'a2': 15.0, 'a2-registered': 20.0,
                   'a-exited': 50.0, 'completed': 223.0, 'quiet': 230.0, 'observed': 235.0, 'drain': 235.0,
                   'end': 250.0},
        'wall_s': 250.0,
        'scrub': {'hosts': 0, 'users': 0, 'paths': 0, 'key': 0, 'words': 0, 'uuids': 0},
    }


def results(judge, record: Record) -> Dict[str, Optional[bool]]:
    """Each check's result by name (True, False, or None for inconclusive)."""
    return {name: ok for name, ok, _ in judge.judge_record(record)}


def failed(judge, record: Record) -> List[str]:
    """Names of the checks the judge fails on `record`."""
    return [name for name, ok in results(judge, record).items() if ok is False]


@mark.unit
def test_good_run_passes(judge) -> None:
    """A clean record passes every check, the R7 bound and its log cross-check among them."""
    checks = results(judge, good())
    assert all(ok is True for ok in checks.values()), {name: ok for name, ok in checks.items() if ok is not True}
    assert {'R7.available', 'R7.bound', 'R7.requeue_logged', 'R7.non_vacuous', 'retire.no_strand'} <= set(checks)


def mutate(record: Record, change: Callable[[Record], None]) -> Record:
    change(record)
    return record


def stranded(record: Record) -> None:
    """A never hands back task 3: it stays on A until the samples end (the defect, as on develop)."""
    record['transitions']['3'] = [[4.0, None], [13.0, 'A']]
    record['relays']['A']['handed_back'] = [{'count': 2, 't': 23.0}]
    record['server']['requeued'] = [{'relay': 'A', 'count': 2, 't': REQUEUE}]


def late(record: Record) -> None:
    """A's unstarted tasks reach the server 8 s after the mark: past 3 heartbeats (6 s) plus skew (1 s)."""
    record['transitions'] = transitions(requeue=MARK + 8.0)
    record['server']['requeued'] = [{'relay': 'A', 'count': 3, 't': MARK + 8.0}]


BROKEN = [
    ('A exits with status 1', lambda r: r['relays']['A'].update(rc=1), {'retire.exit'}),
    ('A is stopped, not exiting by itself', lambda r: r['relays']['A'].update(self=False, stopped=True),
     {'retire.exit'}),
    ('the server evicts A', lambda r: r['server']['evicted'].append({'relay': 'A', 't': 60.0}), {'retire.no_evict'}),
    ('a task runs twice', lambda r: r['tasks'].update(runs=13, repeated=[3]), {'tasks.once'}),
    ('a task fails', lambda r: r['tasks'].update(succeeded=11, failed=1), {'tasks.complete', 'tasks.failed'}),
    ('A launches after the mark', lambda r: r['launches'].append(launch('A', 3, 30.0, 70.0)),
     {'retire.no_launch_after_mark'}),
    ('A terminates a site client', lambda r: r['relays']['A'].update(terminating=1), {'relay.no_terminate'}),
    ('a site client of A is killed', lambda r: r['launches'][0].update(status=-9, killed=True), {'exit.clean'}),
    ('A logs the mark twice', lambda r: r['relays']['A'].update(superseded=2), {'retire.mark'}),
    ('A never reads the mark', lambda r: r['relays']['A'].update(superseded=0, mark=None),
     {'retire.mark', 'R7.mark_read'}),
    ('A sends no final heartbeat', lambda r: r['relays']['A'].update(final_heartbeats=[]), {'retire.final'}),
    ('A2 leaves before it is drained', lambda r: r['server']['left'].insert(1, {'relay': 'A2', 't': 100.0}),
     {'a2.stayed'}),
    ('A2 never starts', lambda r: (r['server']['registered'].pop(), r['relays']['A2'].update(started=False)),
     {'a2.started'}),
    ('a task strands at A', stranded, {'R7.available', 'retire.no_strand'}),
    ('the hand-back is late', late, {'R7.bound', 'R7.requeue_logged'}),
    ('the run hit its deadline', lambda r: r['events'].update(deadline=600.0), {'run.deadline'}),
    ('eviction shorter than the run', lambda r: r['settings'].update(evict=300), {'run.eviction_longer'}),
    ('the samples are too coarse', lambda r: r['samples'].update(max_gap=5.0), {'samples.resolution'}),
    ('a path in the record', lambda r: r.update(label='/home/someone'), {'record.scrubbed'}),
    ('a host in the record', lambda r: r['scrub'].update(hosts=1), {'record.scrubbed'}),
]


@mark.unit
@mark.parametrize('name, change, expected', BROKEN, ids=[item[0] for item in BROKEN])
def test_broken_record_fails(judge, name: str, change: Callable[[Record], None], expected: set) -> None:
    """Each broken record fails exactly the checks that name what broke."""
    assert set(failed(judge, mutate(good(), change))) == expected


@mark.unit
def test_skew_allowance(judge) -> None:
    """R7's bound is 3 heartbeats plus the clock-skew allowance: 6.5 s after the mark passes, 7.5 s fails."""
    for delay, ok in ((6.5, True), (7.5, False)):
        record = good()
        record['transitions'] = transitions(requeue=MARK + delay)
        record['server']['requeued'] = [{'relay': 'A', 'count': 3, 't': MARK + delay}]
        assert results(judge, record)['R7.bound'] is ok


@mark.unit
def test_follow_starts_before_the_mark_by_the_allowance(judge) -> None:
    """A task requeued just before A's mark (the server's clock a little ahead) is still followed."""
    record = good()
    record['transitions'] = transitions(requeue=MARK - 0.5)
    record['server']['requeued'] = [{'relay': 'A', 'count': 3, 't': MARK - 0.5}]
    checks = results(judge, record)
    assert checks['R7.available'] is True and checks['R7.bound'] is True and checks['R7.non_vacuous'] is True


def vacuous(record: Record) -> None:
    """A held nothing unstarted when it read the mark, and handed back nothing."""
    record['transitions'] = {task: [step for step in steps if not (int(task) in A_UNSTARTED and step[1] == 'A')]
                             for task, steps in record['transitions'].items()}
    record['relays']['A']['handed_back'] = []
    record['server']['requeued'] = []


@mark.unit
def test_vacuous_run_is_inconclusive(judge) -> None:
    """A run in which A held nothing unstarted and handed back nothing proves nothing."""
    checks = results(judge, mutate(good(), vacuous))
    assert checks['R7.non_vacuous'] is None
    assert not [name for name, ok in checks.items() if ok is False]


def write(directory: Path, *items: Record) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for i, record in enumerate(items):
        (directory / f'record-{i}.json').write_text(json.dumps(record))
    return directory


@mark.unit
def test_cluster_exit_status(judge, tmp_path: Path, capsys) -> None:
    """The command's verdict: PASS 0; any failed run, or none at all, 1; an inconclusive run 2."""
    assert judge.cluster([str(write(tmp_path / 'good', good())), '--require', 'retire']) == 0
    assert capsys.readouterr().out.strip().endswith('retire_judge: PASS (1 runs, 0 failed, 0 inconclusive)')
    broken = mutate(good(), lambda r: r['relays']['A'].update(rc=1))
    assert judge.cluster([str(write(tmp_path / 'broken', good(), broken)), '--require', 'retire']) == 1
    assert 'FAIL retire.exit' in capsys.readouterr().out
    assert judge.cluster([str(write(tmp_path / 'vacuous', mutate(good(), vacuous))), '--require', 'retire']) == 2
    assert 'INCONCLUSIVE R7.non_vacuous' in capsys.readouterr().out
    assert judge.cluster([str(tmp_path / 'none'), '--require', 'retire']) == 1
    assert judge.cluster([str(write(tmp_path / 'one', good())), '--require', 'nothing']) == 1


@mark.unit
def test_record_samples_rebuild(judge) -> None:
    """The samples rebuilt from transitions hold each task's owner as of each time, and drop it once done."""
    found = dict(judge.record_samples(good()))
    assert found[13.0][3] == 'A' and found[13.0][0] == 'A' and found[13.0][6] is None
    assert 0 not in found[A_DONE] and found[REQUEUE][3] is None
    assert all(t2 - t1 <= 1.0 for t1, t2 in zip(sorted(found), sorted(found)[1:]))
