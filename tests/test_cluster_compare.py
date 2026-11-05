# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The multi-node drive's judge (`tests/cluster/compare.py`) on synthetic run records."""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Standard libs
import json
import importlib.util
from pathlib import Path

# External libs
from pytest import mark, fixture


COMPARE = Path(__file__).parent / 'cluster' / 'compare.py'


@fixture(scope='module')
def compare():
    """The compare.py module, loaded from its path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('cluster_compare', COMPARE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def server(label: str, run_id: str, rate: float = 100.0, **fields: Any) -> Dict[str, Any]:
    """A clean homogeneous server record."""
    record = dict(label=label, run_id=run_id, tls=1, clients_expected=2, threads=1, tasks=100, bundlesize=1,
                  client_bundlesize=1, client_bundlewait=1.0, server_rc=0, rows=100, retried=0, incomplete=0,
                  p10_p90_tasks_per_s=rate, exit_hist={'0': 100},
                  peak=dict(children=0, tree_fds=20, tree_threads=10, tree_rss_mb=50.0))
    record.update(fields)
    return record


def client(index: str, label: str, run_id: str, **fields: Any) -> Dict[str, Any]:
    """A clean client record."""
    return dict(label=label, run_id=run_id, role='client', index=index, rc=0, **fields)


def write(root: Path, record: Dict[str, Any], clients: List[Dict[str, Any]]) -> None:
    """Write one run's records under `root`."""
    run = root / record['run_id']
    run.mkdir(parents=True)
    (run / 'server.json').write_text(json.dumps(record))
    for entry in clients:
        (run / f'client-{entry["index"]}.json').write_text(json.dumps(entry))


def homogeneous(root: Path, label: str, rates: List[float], advertised: Optional[int] = None) -> None:
    """Runs of a homogeneous configuration at the given throughputs."""
    for n, rate in enumerate(rates):
        run_id = f'{label}-{n}'
        write(root, server(label, run_id, rate),
              [client(str(i), label, run_id, advertised=advertised) for i in range(2)])


def zoned(root: Path, run_id: str = 'z-0', **changes: Any) -> None:
    """A clean zoned run (with one retry per zone), with optional damage."""
    record = server('feature', run_id, tasks=100, rows=103, retried=3, exit_hist={'0': 100, '3': 3},
                    zones='default:40,cpu:30,gpu:30', groups=3, retries=1, cap=None, bundlesize=4,
                    zone_tasks={'default': 42, 'cpu': 31, 'gpu': 30}, zone_mismatched_retries=0, group_order=True,
                    clients_expected=4)
    clients = [client('cpu', 'feature', run_id, zone='cpu', advertised=3, received=31, largest=3, exit7=0),
               client('gpu', 'feature', run_id, zone='gpu', advertised=None, received=30, largest=4, exit7=0),
               client('any-0', 'feature', run_id, zone=None, advertised=2, received=20, largest=2, exit7=0),
               client('any-1', 'feature', run_id, zone=None, advertised=None, received=22, largest=4, exit7=0)]
    for key, value in changes.pop('server', {}).items():
        record[key] = value
    for index, fields in changes.pop('clients', {}).items():
        next(entry for entry in clients if entry['index'] == index).update(fields)
    write(root, record, clients)


@mark.unit
def test_zoned_run_passes(compare, tmp_path: Path) -> None:
    """A clean zoned run passes; without --zoned its group is skipped."""
    zoned(tmp_path)
    assert compare.main([str(tmp_path), '--baseline', 'feature', '--candidate', 'feature',
                         '--results-only', '--zoned', 'feature']) == 0
    assert compare.main([str(tmp_path), '--baseline', 'feature', '--candidate', 'feature', '--results-only']) == 0


@mark.unit
@mark.parametrize('damage', [
    {'server': {'exit_hist': {'0': 99, '3': 3, '7': 1}}},
    {'clients': {'cpu': {'exit7': 1}}},
    {'server': {'zone_mismatched_retries': 1}},
    {'clients': {'gpu': {'received': 31}}},
    {'clients': {'cpu': {'largest': 4}}},
    {'clients': {'gpu': {'largest': 5}}},
    {'server': {'group_order': False}},
    {'server': {'incomplete': 1}},
    {'clients': {'any-0': {'rc': 5}}},
    {'clients': {'any-1': {'received': None}}},
], ids=['exit-7', 'client-exit-7', 'retry-zone', 'double-delivery', 'over-advertised', 'over-server-size',
        'group-order', 'incomplete', 'client-rc', 'no-bundle-record'])
def test_zoned_damage_fails(compare, tmp_path: Path, damage: Dict[str, Any]) -> None:
    """Each kind of zoned failure fails the verdict."""
    zoned(tmp_path, **damage)
    assert compare.main([str(tmp_path), '--baseline', 'feature', '--candidate', 'feature',
                         '--results-only', '--zoned', 'feature']) == 1


@mark.unit
def test_cap_bounds_the_largest_bundle(compare, tmp_path: Path) -> None:
    """A client advertising more than the cap may get no more than the cap."""
    zoned(tmp_path, server={'cap': 8}, clients={'cpu': {'advertised': 64, 'largest': 8}})
    assert compare.main([str(tmp_path), '--baseline', 'feature', '--candidate', 'feature',
                         '--results-only', '--zoned', 'feature']) == 0
    zoned(tmp_path, run_id='z-1', server={'cap': 8}, clients={'cpu': {'advertised': 64, 'largest': 9}})
    assert compare.main([str(tmp_path), '--baseline', 'feature', '--candidate', 'feature',
                         '--results-only', '--zoned', 'feature']) == 1


@mark.unit
def test_min_ratio_judges_unadvertised_groups(compare, tmp_path: Path) -> None:
    """--min-ratio passes at or above the ratio and fails below it, on groups that advertise nothing."""
    homogeneous(tmp_path / 'pass', 'base', [100, 101, 99])
    homogeneous(tmp_path / 'pass', 'feature', [96, 97, 95])
    assert compare.main([str(tmp_path / 'pass'), '--baseline', 'base', '--candidate', 'feature',
                         '--min-ratio', '0.95']) == 0
    homogeneous(tmp_path / 'fail', 'base', [100, 101, 99])
    homogeneous(tmp_path / 'fail', 'feature', [90, 91, 89])
    assert compare.main([str(tmp_path / 'fail'), '--baseline', 'base', '--candidate', 'feature',
                         '--min-ratio', '0.95']) == 1


@mark.unit
def test_modes_never_pool(compare, tmp_path: Path) -> None:
    """Runs whose clients advertise a size and runs whose clients do not are separate groups."""
    homogeneous(tmp_path, 'base', [100, 100, 100], advertised=None)
    homogeneous(tmp_path, 'feature', [100, 100, 100], advertised=None)
    for n in range(3):
        write(tmp_path, server('feature', f'adv-{n}', 10.0), [client(str(i), 'feature', f'adv-{n}', advertised=1)
                                                               for i in range(2)])
    runs = compare.load(str(tmp_path))
    keys = {compare.config_key(run) for run in runs}
    assert len(keys) == 2
    # The slow advertising runs form their own group, which has no baseline runs and so fails on its own.
    assert compare.main([str(tmp_path), '--baseline', 'base', '--candidate', 'feature', '--min-ratio', '0.95']) == 1
