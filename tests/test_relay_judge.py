# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""The relay drive's judge (`spec/relay/drive/relay_judge.py`) on synthetic run records.

A run is one directory of records: `upstream.json` from the node that ran the upstream server,
`relay-A.json` and `relay-B.json` from the two relays, and one `leaf-<site>-<n>.json` per site node.
A good run of each failover variant (kill, pause, none) passes every check; each bad run below breaks
exactly one thing the multi-node run must show, and the judge names the check it fails.
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


JUDGE = Path(__file__).parent.parent / 'spec' / 'relay' / 'drive' / 'relay_judge.py'
VARIANTS = ('kill', 'pause', 'none')


@fixture(scope='module')
def judge():
    """The judge module, loaded from its path (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location('relay_judge', JUDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASKS, FAILING, GROUPS = 300, 3, 3
ROWS = TASKS + FAILING


def upstream(variant: str) -> Dict[str, Any]:
    """A clean upstream record for `variant`."""
    snapshot = {'kill': 6, 'pause': 5, 'none': 0}[variant]
    triggered = variant != 'none'
    dups = snapshot if variant == 'kill' else 0
    return {
        'schema': 2, 'role': 'upstream', 'run_id': f'smoke-r1-{variant}', 'label': 'feature',
        'mode': 'slurm' if variant == 'kill' else 'local',
        'variant': variant, 'hypershell': '2.9.0a4', 'python': '3.12.13', 'tls': True,
        'nodes': {'total': 3, 'A': 1, 'B': 1},
        'workload': {'tasks': TASKS, 'failing': FAILING, 'groups': GROUPS, 'sleep': 0.05},
        'settings': {'evict': 10, 'heartrate': 1, 'threads': 2, 'clients_per_node': 2, 'kill_at': 0.4},
        'server_rc': 0, 'completed': True, 'wall_s': 60.0,
        'a': {'rows': ROWS, 'incomplete': 0, 'succeeded': TASKS, 'failed_first': FAILING, 'retry_rows': FAILING,
              'retry_rows_ok': FAILING, 'max_attempt': 2, 'group_order': True, 'executed_ids': ROWS,
              'executions': ROWS + dups, 'never_executed': 0, 'dup_ids': dups, 'dup_outside_snapshot': 0},
        'b': {'completed_not_leaf': 0, 'completed_by_relay': 0, 'completed_site_mismatch': 0,
              'client_host_outside_site': 0, 'completed_by_site': {'A': 200, 'B': ROWS - 200},
              'sites': {'A': {'leaves_launched': 2, 'leaves_upstream': 2, 'leaves_other_relay': 0, 'relay_rows': 1},
                        'B': {'leaves_launched': 2, 'leaves_upstream': 2, 'leaves_other_relay': 0, 'relay_rows': 1}}},
        'c': {'samples': 55, 'conn_peak': 6, 'conn_series': [0, 3, 6, 6, 3], 'peers_seen': 2,
              'peers_outside_relays': 0, 'address_check': variant == 'kill'},
        'd': {'triggered': triggered, 'attempts': 1 if triggered else 0, 'snapshot': snapshot,
              'snapshot_completed': snapshot, 'snapshot_completed_at_A': snapshot if variant == 'kill' else 3,
              'snapshot_completed_at_B': 0 if variant != 'pause' else snapshot - 3,
              'evictions': {'A': 0, 'B': 1 if triggered else 0}, 'reverts': {'A': 0, 'B': 1 if triggered else 0},
              'stale': 4 if variant == 'pause' else 0, 'duplicate': 0,
              'completed_at_trigger': 130 if triggered else 0},
        'key_in_server_log': 0,
    }


def relay(variant: str, site: str) -> Dict[str, Any]:
    """A clean relay record for `site` under `variant`."""
    action = variant if site == 'B' else 'none'
    return {
        'schema': 2, 'role': 'relay', 'run_id': f'smoke-r1-{variant}', 'site': site,
        'rc': -9 if action == 'kill' else 0, 'identities': 1, 'leaves_launched': 2, 'action': action,
        'local_rows_left': None if action == 'kill' else 0, 'results_pending': None if action == 'kill' else 0,
        'results_returned': 150, 'peak_backlog': 4, 'peak_bound': 4, 'backlog_samples': 40,
        'upstream_conns_peak': 3, 'primary_db_created': False, 'key_in_logs': 0,
    }


def leaf(variant: str, site: str, node: int) -> Dict[str, Any]:
    """A clean leaf-node record."""
    return {
        'schema': 2, 'role': 'leaf', 'run_id': f'smoke-r1-{variant}', 'site': site, 'node': node,
        'hosts_relay': True, 'samples': 50, 'procs_scanned': 30, 'env_unreadable': 0, 'clients_scanned': 2,
        'tasks_scanned': 25, 'launchers_scanned': 2, 'key_hits': 0, 'fenced_names_seen': 0, 'key_probe_seen': 0,
        'untokened_clients': 0, 'upstream_conns_max': 0, 'executions': 150,
        'site_key_clients': 2, 'site_key_argv': 0, 'site_key_tasks': 0,
    }


def good_run(variant: str) -> Dict[str, Dict[str, Any]]:
    """Every record of a clean run of `variant`, by name."""
    return {'upstream': upstream(variant), 'relay-A': relay(variant, 'A'), 'relay-B': relay(variant, 'B'),
            'leaf-A-1': leaf(variant, 'A', 1), 'leaf-B-2': leaf(variant, 'B', 2)}


def failed(judge, records: Dict[str, Dict[str, Any]]) -> List[str]:
    """Names of the checks the judge fails on `records`."""
    return [name for name, ok, _ in judge.judge_run(records) if not ok]


@mark.unit
@mark.parametrize('variant', VARIANTS)
def test_good_run_passes(judge, variant: str) -> None:
    """A clean run of each failover variant passes every check."""
    checks = judge.judge_run(good_run(variant))
    assert checks and all(ok for _, ok, _ in checks), [c for c in checks if not c[1]]


def edit(path: str, value: Any) -> Callable[[Dict[str, Dict[str, Any]]], None]:
    """A mutation setting the dotted `path` (record.key.key...) to `value`."""
    def mutate(records: Dict[str, Dict[str, Any]]) -> None:
        name, *keys = path.split('.')
        target = records[name]
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return mutate


def both(*mutations: Callable[[Dict[str, Dict[str, Any]]], None]) -> Callable[[Dict[str, Dict[str, Any]]], None]:
    """A mutation applying each of `mutations` in turn."""
    def mutate(records: Dict[str, Dict[str, Any]]) -> None:
        for each in mutations:
            each(records)
    return mutate


def drop(name: str) -> Callable[[Dict[str, Dict[str, Any]]], None]:
    """A mutation removing a whole record."""
    return lambda records: records.pop(name)


# (variant, mutation, the check it must fail): each a way the multi-node run can fail R21.
BAD = [
    # Records: the run is complete and scrubbed.
    ('kill', drop('relay-B'), 'records.present'),
    ('kill', drop('leaf-B-2'), 'records.present'),
    ('none', edit('upstream.schema', 1), 'records.schema'),
    ('none', lambda records: [record.update(schema=3) for record in records.values()], 'records.schema'),
    ('none', edit('leaf-A-1.note', 'ran on /some/path'), 'records.scrubbed'),
    ('none', edit('upstream.completed', False), 'run.completed'),
    ('none', edit('upstream.server_rc', 1), 'run.server'),
    # (a) every task reaches its final state once upstream, with group order kept.
    ('kill', edit('upstream.a.rows', ROWS + 1), 'a.rows'),
    ('kill', edit('upstream.a.incomplete', 2), 'a.incomplete'),
    ('kill', edit('upstream.a.succeeded', TASKS - 1), 'a.succeeded'),
    ('kill', edit('upstream.a.retry_rows_ok', FAILING - 1), 'a.retries'),
    ('kill', edit('upstream.a.max_attempt', 3), 'a.retries'),
    ('kill', edit('upstream.a.group_order', False), 'a.group_order'),
    ('kill', edit('upstream.a.never_executed', 1), 'a.executed'),
    ('kill', edit('upstream.a.dup_outside_snapshot', 1), 'a.duplicates'),
    ('none', edit('upstream.a.dup_ids', 1), 'a.duplicates'),
    # (b) completed rows name a leaf of the site that ran them; leaves listed upstream by site.
    ('kill', edit('upstream.b.completed_not_leaf', 1), 'b.attribution'),
    ('kill', edit('upstream.b.completed_by_relay', 1), 'b.attribution'),
    ('kill', edit('upstream.b.completed_site_mismatch', 1), 'b.site'),
    ('kill', edit('upstream.b.client_host_outside_site', 1), 'b.site'),
    ('none', edit('upstream.b.completed_by_site.B', 0), 'b.both_sites'),
    ('kill', edit('upstream.b.sites.A.leaves_upstream', 1), 'b.roster.A'),
    ('kill', edit('upstream.b.sites.B.leaves_other_relay', 1), 'b.roster.B'),
    ('kill', edit('upstream.b.sites.A.relay_rows', 0), 'b.roster.A'),
    ('kill', edit('upstream.b.sites.B.leaves_launched', 0), 'b.roster.B'),
    # (c) upstream holds connections for the relays only.
    ('none', edit('leaf-A-1.upstream_conns_max', 1), 'c.leaves'),
    ('none', edit('upstream.c.samples', 0), 'c.sampled'),
    ('none', edit('upstream.c.conn_peak', 0), 'c.sampled'),
    ('none', edit('relay-A.upstream_conns_peak', 0), 'c.relays'),
    ('none', edit('upstream.c.conn_peak', 7), 'c.bounded'),
    ('kill', edit('upstream.c.peers_outside_relays', 1), 'c.peers'),
    ('none', both(edit('upstream.c.address_check', True), edit('upstream.c.peers_outside_relays', 1)), 'c.peers'),
    # (d) failover: evicted, reverted, completed at the surviving site, nothing lost.
    ('kill', edit('upstream.d.triggered', False), 'd.triggered'),
    ('kill', edit('upstream.d.snapshot', 0), 'd.triggered'),
    ('kill', edit('upstream.d.evictions.B', 0), 'd.evicted'),
    ('kill', edit('upstream.d.reverts.B', 0), 'd.evicted'),
    ('kill', edit('upstream.d.evictions.A', 1), 'd.survivor'),
    ('kill', edit('upstream.d.snapshot_completed_at_A', 5), 'd.completed'),
    ('kill', edit('upstream.d.stale', 1), 'd.results'),
    ('kill', edit('relay-B.rc', 0), 'd.relays'),
    ('kill', edit('relay-A.rc', 1), 'd.relays'),
    ('pause', edit('upstream.d.stale', 0), 'd.results'),
    ('pause', edit('upstream.d.snapshot_completed', 4), 'd.completed'),
    ('pause', edit('relay-B.rc', -9), 'd.relays'),
    ('none', edit('upstream.d.triggered', True), 'd.triggered'),
    ('none', edit('upstream.d.evictions.B', 1), 'd.evicted'),
    ('none', edit('upstream.d.stale', 1), 'd.results'),
    ('none', edit('relay-B.rc', 2), 'd.relays'),
    # (e) the upstream key in no site client's argv or environment, and the scan not vacuous.
    ('none', edit('leaf-B-2.key_hits', 1), 'e.key'),
    ('none', edit('leaf-A-1.fenced_names_seen', 1), 'e.fenced'),
    ('none', edit('leaf-A-1.key_probe_seen', 1), 'e.fenced'),
    ('none', edit('leaf-A-1.clients_scanned', 0), 'e.scanned.A'),
    ('none', edit('leaf-B-2.tasks_scanned', 0), 'e.scanned.B'),
    ('none', edit('leaf-B-2.untokened_clients', 1), 'e.forwarded'),
    ('none', edit('relay-A.key_in_logs', 1), 'e.logs'),
    ('none', edit('upstream.key_in_server_log', 1), 'e.logs'),
    # The site key: in each site client's environment, on no command line, in no task.
    ('none', edit('leaf-A-1.site_key_clients', 1), 'e.site_key.handoff'),
    ('kill', edit('leaf-B-2.site_key_argv', 2), 'e.site_key.argv'),
    ('pause', edit('leaf-A-1.site_key_tasks', 1), 'e.site_key.tasks'),
    ('none', lambda records: records['leaf-B-2'].pop('site_key_tasks'), 'e.site_key.tasks'),
    # The relay database never touches the primary; the backlog stays under its bound.
    ('none', edit('relay-B.primary_db_created', True), 'relay.primary'),
    ('none', edit('relay-A.peak_backlog', 5), 'relay.backlog'),
    ('none', edit('relay-A.backlog_samples', 0), 'relay.backlog'),
    ('none', edit('relay-A.identities', 2), 'relay.identity'),
]


@mark.unit
@mark.parametrize('variant, mutate, check', BAD, ids=[f'{v}-{c}-{i}' for i, (v, _, c) in enumerate(BAD)])
def test_bad_run_fails_its_check(judge, variant: str, mutate, check: str) -> None:
    """Each way a run can fail R21 fails the check that names it."""
    records = copy.deepcopy(good_run(variant))
    mutate(records)
    assert check in failed(judge, records)


@mark.unit
@mark.parametrize('variant', VARIANTS)
def test_schema_1_run_passes_without_the_site_key_checks(judge, variant: str) -> None:
    """Records from the drive before the site-key fields are judged on everything else."""
    records = good_run(variant)
    for record in records.values():
        record['schema'] = 1
        for field in ('site_key_clients', 'site_key_argv', 'site_key_tasks'):
            record.pop(field, None)
    checks = judge.judge_run(records)
    assert all(ok for _, ok, _ in checks), [c for c in checks if not c[1]]
    assert not any(name.startswith('e.site_key') for name, _, _ in checks)


@mark.unit
def test_unknown_variant_fails(judge) -> None:
    """A run of no known variant cannot pass."""
    records = good_run('none')
    records['upstream']['variant'] = 'other'
    assert 'run.variant' in failed(judge, records)


def write_run(root: Path, name: str, records: Dict[str, Dict[str, Any]]) -> Path:
    """Write a run's records as JSON files under `root/name`."""
    run = root / name
    run.mkdir(parents=True)
    for stem, record in records.items():
        (run / f'{stem}.json').write_text(json.dumps(record))
    return run


@mark.unit
def test_main_judges_every_run_under_a_directory(judge, tmp_path: Path, capsys) -> None:
    """Given a directory of runs, every run is judged and the exit status is 0 only if all pass."""
    for variant in VARIANTS:
        write_run(tmp_path, f'r-{variant}', good_run(variant))
    assert judge.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count('PASS') >= 3 and 'FAIL' not in out

    bad = good_run('kill')
    bad['upstream']['a']['incomplete'] = 1
    write_run(tmp_path, 'r-bad', bad)
    assert judge.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert 'r-bad' in out and 'a.incomplete' in out and 'FAIL' in out


@mark.unit
def test_main_judges_one_run_directory(judge, tmp_path: Path) -> None:
    """Given a run's own directory, that run is judged."""
    run = write_run(tmp_path, 'only', good_run('pause'))
    assert judge.main([str(run)]) == 0


@mark.unit
def test_main_fails_with_no_runs(judge, tmp_path: Path, capsys) -> None:
    """A directory with no run records is a failure, never a vacuous pass."""
    assert judge.main([str(tmp_path)]) == 1
    assert 'no run' in capsys.readouterr().out.lower()


@mark.unit
def test_main_requires_named_variants(judge, tmp_path: Path, capsys) -> None:
    """With --require, a variant the matrix needs but no run shows is a failure."""
    write_run(tmp_path, 'r-kill', good_run('kill'))
    assert judge.main([str(tmp_path), '--require', 'kill']) == 0
    assert judge.main([str(tmp_path), '--require', 'kill,none']) == 1
    assert 'none' in capsys.readouterr().out


PROBE = Path(__file__).parent.parent / 'spec' / 'relay' / 'drive' / 'relay_probe.py'


@fixture(scope='module')
def probe():
    """The drive's roles module, loaded from its path with its default settings (nothing runs on load)."""
    spec = importlib.util.spec_from_file_location('relay_probe', PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@mark.unit
@mark.parametrize('connected', [0, 1, 2, 4])
def test_probe_records_the_bound_the_relay_enforces(probe, connected: int) -> None:
    """The bound a relay record carries is the relay's own: two site bundles per connected site client."""
    from hypershell.relay import site_bundlesize
    from hypershell.relay.upstream import BACKLOG_FACTOR
    bundlesize = site_bundlesize(probe.RELAY_BUNDLESIZE, probe.THREADS)
    assert probe.BACKLOG_FACTOR == BACKLOG_FACTOR
    assert probe.relay_bound(connected) == connected * bundlesize * BACKLOG_FACTOR


@mark.unit
def test_relay_holding_two_bundles_per_client_passes(judge, probe) -> None:
    """A relay at its full bound (two bundles per client, as on the drive's defaults) passes; above it fails."""
    records = good_run('none')
    bound = probe.relay_bound(2)
    for site in ('A', 'B'):
        records[f'relay-{site}'].update(peak_backlog=bound, peak_bound=bound)
    assert 'relay.backlog' not in failed(judge, records)
    records['relay-A']['peak_backlog'] = bound + 1
    assert 'relay.backlog' in failed(judge, records)
