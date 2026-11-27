#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge the records of relay drive runs against the multi-node checks (a)-(e).

A run is one directory of JSON records written by `relay_drive.sh`: `upstream.json` (the upstream
server's node), `relay-A.json` and `relay-B.json` (one per relay), and `leaf-<site>-<n>.json` (one
per site node). Records hold counts and booleans only: no host, user, path or key.

Schema 2 leaf records also count where the site key was seen: site clients must hold it in their
environment (how `hs relay` hands it over), and no command line and no task may hold it. Schema 1
records, from a drive before that, are judged without those checks.

    relay_judge.py DIR [--require kill,pause,none]

DIR is one run's directory or a directory of runs. Every run found is judged and printed with each
check; the exit status is 0 only if at least one run was found, every run passed, and every variant
named by --require passed in some run. Standard library only.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

# Standard libs
import sys
import json
import argparse
from pathlib import Path


SCHEMA = 2
SCHEMAS = (1, 2)
VARIANTS = ('kill', 'pause', 'none')
SITES = ('A', 'B')

Check = Tuple[str, bool, str]
Records = Dict[str, Dict[str, Any]]


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


def leaves(records: Records, site: str) -> List[Dict[str, Any]]:
    """The leaf-node records of `site`."""
    return [record for name, record in sorted(records.items())
            if name.startswith(f'leaf-{site}-') and record.get('role') == 'leaf']


def total(items: Sequence[Dict[str, Any]], key: str) -> int:
    """Sum of `key` over records (missing counts as zero)."""
    return sum(int(item.get(key) or 0) for item in items)


def judge_run(records: Records) -> List[Check]:
    """Every check of one run, as (name, passed, detail)."""
    checks: List[Check] = []

    def check(name: str, ok: Any, detail: str = '') -> bool:
        checks.append((name, bool(ok), detail))
        return bool(ok)

    up, ra, rb = records.get('upstream'), records.get('relay-A'), records.get('relay-B')
    present = up is not None and ra is not None and rb is not None and all(leaves(records, s) for s in SITES)
    missing = [n for n in ('upstream', 'relay-A', 'relay-B') if n not in records]
    missing += [f'leaf-{s}-*' for s in SITES if not leaves(records, s)]
    if not check('records.present', present, f'missing: {", ".join(missing)}' if missing else ''):
        return checks
    schemas = {r.get('schema') for r in records.values()}
    check('records.schema', len(schemas) == 1 and schemas <= set(SCHEMAS),
          f'expected one schema of {SCHEMAS}, found {sorted(map(str, schemas))}')
    schema = up.get('schema')
    marked = sorted({s for r in records.values() for s in strings(r) if '/' in s or '@' in s})
    check('records.scrubbed', not marked, f'{len(marked)} values look like paths or addresses')

    variant = up.get('variant')
    if not check('run.variant', variant in VARIANTS, f'variant {variant!r}'):
        return checks
    check('run.completed', up.get('completed') is True, 'the run did not finish within its deadline')
    check('run.server', up.get('server_rc') == 0, f'server exit {up.get("server_rc")}')

    # (a) Every task reaches its final state once upstream, with group order kept.
    a, w = up.get('a', {}), up.get('workload', {})
    tasks, failing = int(w.get('tasks', -1)), int(w.get('failing', -1))
    check('a.rows', a.get('rows') == tasks + failing, f'{a.get("rows")} rows for {tasks} tasks + {failing} retries')
    check('a.incomplete', a.get('incomplete') == 0, f'{a.get("incomplete")} rows not completed')
    check('a.succeeded', a.get('succeeded') == tasks, f'{a.get("succeeded")} of {tasks} succeeded')
    check('a.retries', failing > 0 and a.get('failed_first') == failing and a.get('retry_rows') == failing
          and a.get('retry_rows_ok') == failing and a.get('max_attempt') == 2,
          f'failed {a.get("failed_first")}, retried {a.get("retry_rows")} ({a.get("retry_rows_ok")} ok), '
          f'max attempt {a.get("max_attempt")}; expected {failing} each and 2')
    check('a.group_order', a.get('group_order') is True, 'a group started before the one before it completed')
    check('a.executed', a.get('never_executed') == 0 and a.get('executed_ids') == a.get('rows'),
          f'{a.get("never_executed")} rows never executed; {a.get("executed_ids")} ids executed')
    if variant == 'none':
        check('a.duplicates', a.get('dup_ids') == 0 and a.get('dup_outside_snapshot') == 0,
              f'{a.get("dup_ids")} tasks executed more than once with no failover')
    else:
        check('a.duplicates', a.get('dup_outside_snapshot') == 0,
              f'{a.get("dup_outside_snapshot")} tasks executed more than once outside the failover snapshot')

    # (b) Completed rows name a leaf of the site that ran them; leaves appear upstream by site.
    b = up.get('b', {})
    check('b.attribution', b.get('completed_not_leaf') == 0 and b.get('completed_by_relay') == 0,
          f'{b.get("completed_not_leaf")} completed rows name no leaf, {b.get("completed_by_relay")} a relay')
    check('b.site', b.get('completed_site_mismatch') == 0 and b.get('client_host_outside_site') == 0,
          f'{b.get("completed_site_mismatch")} rows name a leaf of a site that did not run them, '
          f'{b.get("client_host_outside_site")} a host outside the site')
    by_site = b.get('completed_by_site', {})
    check('b.both_sites', all(int(by_site.get(s) or 0) > 0 for s in SITES), f'completed by site: {by_site}')
    for site in SITES:
        s = b.get('sites', {}).get(site, {})
        check(f'b.roster.{site}', int(s.get('leaves_launched') or 0) > 0
              and s.get('leaves_upstream') == s.get('leaves_launched')
              and s.get('leaves_other_relay') == 0 and s.get('relay_rows') == 1,
              f'{s.get("leaves_upstream")} leaves upstream at site {site} for {s.get("leaves_launched")} launched, '
              f'{s.get("leaves_other_relay")} under another relay, {s.get("relay_rows")} relay rows')

    # (c) The upstream server holds connections for the relays only.
    c = up.get('c', {})
    every_leaf = leaves(records, 'A') + leaves(records, 'B')
    check('c.leaves', all(leaf.get('upstream_conns_max') == 0 for leaf in every_leaf),
          f'leaf processes held upstream connections: {[l.get("upstream_conns_max") for l in every_leaf]}')
    check('c.sampled', int(c.get('samples') or 0) > 0 and int(c.get('conn_peak') or 0) > 0,
          f'{c.get("samples")} upstream samples, peak {c.get("conn_peak")} connections')
    check('c.relays', all(int(r.get('upstream_conns_peak') or 0) > 0 for r in (ra, rb)),
          'a relay was never seen connected upstream')
    relay_peak = int(ra.get('upstream_conns_peak') or 0) + int(rb.get('upstream_conns_peak') or 0)
    check('c.bounded', int(c.get('conn_peak') or 0) <= relay_peak,
          f'upstream peak {c.get("conn_peak")} exceeds the relays\' own {relay_peak}')
    check('c.peers', not c.get('address_check') or c.get('peers_outside_relays') == 0,
          f'{c.get("peers_outside_relays")} upstream peers outside the relay nodes')

    # (d) Failover: the stopped relay is evicted, its tasks revert and complete elsewhere, none lost.
    d = up.get('d', {})
    evictions, reverts = d.get('evictions', {}), d.get('reverts', {})
    snapshot = int(d.get('snapshot') or 0)
    if variant == 'none':
        check('d.triggered', d.get('triggered') is False, 'a failover was triggered in the control run')
        check('d.evicted', evictions.get('A') == 0 and evictions.get('B') == 0 and reverts.get('B') == 0,
              f'evictions {evictions}, reverts {reverts} in the control run')
        check('d.results', d.get('stale') == 0 and d.get('duplicate') == 0,
              f'{d.get("stale")} stale, {d.get("duplicate")} duplicate results')
        check('d.relays', ra.get('rc') == 0 and rb.get('rc') == 0, f'relay exits {ra.get("rc")}, {rb.get("rc")}')
    else:
        check('d.triggered', d.get('triggered') is True and snapshot > 0,
              f'triggered {d.get("triggered")} with {snapshot} tasks held by relay B')
        check('d.evicted', int(evictions.get('B') or 0) >= 1 and int(reverts.get('B') or 0) >= 1
              and (variant != 'kill' or (evictions.get('B') == 1 and reverts.get('B') == 1)),
              f'relay B evicted {evictions.get("B")} times, reverted {reverts.get("B")} times')
        check('d.survivor', evictions.get('A') == 0, f'relay A evicted {evictions.get("A")} times')
        if variant == 'kill':
            check('d.completed', d.get('snapshot_completed') == snapshot
                  and d.get('snapshot_completed_at_A') == snapshot,
                  f'{d.get("snapshot_completed_at_A")} of {snapshot} held tasks completed at site A')
            check('d.results', d.get('stale') == 0 and d.get('duplicate') == 0,
                  f'{d.get("stale")} stale, {d.get("duplicate")} duplicate results from a dead relay')
            check('d.relays', ra.get('rc') == 0 and rb.get('rc') == -9,
                  f'relay exits {ra.get("rc")}, {rb.get("rc")}; expected 0 and -9')
        else:
            check('d.completed', d.get('snapshot_completed') == snapshot,
                  f'{d.get("snapshot_completed")} of {snapshot} held tasks completed')
            check('d.results', int(d.get('stale') or 0) > 0,
                  'no stale result refused from the paused relay')
            check('d.relays', ra.get('rc') == 0 and rb.get('rc') == 0,
                  f'relay exits {ra.get("rc")}, {rb.get("rc")}; expected 0 and 0')

    # (e) The upstream key appears in no site client's argv or environment, and the scan saw them.
    check('e.key', total(every_leaf, 'key_hits') == 0, f'{total(every_leaf, "key_hits")} site processes held the key')
    check('e.fenced', total(every_leaf, 'fenced_names_seen') == 0 and total(every_leaf, 'key_probe_seen') == 0,
          f'{total(every_leaf, "fenced_names_seen")} site processes held relay settings, '
          f'{total(every_leaf, "key_probe_seen")} the probe variable')
    for site in SITES:
        mine = leaves(records, site)
        check(f'e.scanned.{site}', total(mine, 'clients_scanned') > 0 and total(mine, 'tasks_scanned') > 0,
              f'site {site}: {total(mine, "clients_scanned")} clients and {total(mine, "tasks_scanned")} tasks scanned')
    check('e.forwarded', total(every_leaf, 'untokened_clients') == 0,
          f'{total(every_leaf, "untokened_clients")} site clients without the relay\'s environment (not forwarded)')
    check('e.logs', int(up.get('key_in_server_log') or 0) == 0 and all(r.get('key_in_logs') == 0 for r in (ra, rb)),
          'the key appeared in a log')

    # The site key reaches each site client in its environment, and no command line or task.
    if schema == 2:
        fields = ('site_key_clients', 'site_key_argv', 'site_key_tasks')
        recorded = all(field in leaf for leaf in every_leaf for field in fields)
        check('e.site_key.handoff', recorded and total(every_leaf, 'clients_scanned') > 0
              and total(every_leaf, 'site_key_clients') == total(every_leaf, 'clients_scanned'),
              f'{total(every_leaf, "site_key_clients")} of {total(every_leaf, "clients_scanned")} site clients '
              f'held the site key in their environment' + ('' if recorded else ' (fields missing)'))
        check('e.site_key.argv', recorded and total(every_leaf, 'site_key_argv') == 0,
              f'{total(every_leaf, "site_key_argv")} site processes with a key option or the site key on their '
              f'command line')
        check('e.site_key.tasks', recorded and total(every_leaf, 'site_key_tasks') == 0,
              f'{total(every_leaf, "site_key_tasks")} tasks held the site key in their environment')

    # The relay database never touches the primary; each relay has one identity; R8's bound holds.
    check('relay.primary', all(r.get('primary_db_created') is False for r in (ra, rb)),
          'a relay created the configured primary database')
    check('relay.identity', all(r.get('identities') == 1 for r in (ra, rb)),
          f'identities {ra.get("identities")}, {rb.get("identities")}')
    check('relay.backlog', all(int(r.get('backlog_samples') or 0) > 0
                               and int(r.get('peak_backlog') or 0) <= int(r.get('peak_bound') or 0) for r in (ra, rb)),
          f'peak backlog {ra.get("peak_backlog")}/{ra.get("peak_bound")}, {rb.get("peak_backlog")}/{rb.get("peak_bound")}')
    return checks


def load_run(path: Path) -> Records:
    """The records of one run directory, by file stem."""
    return {item.stem: json.loads(item.read_text()) for item in sorted(path.glob('*.json'))}


def find_runs(path: Path) -> List[Path]:
    """`path` itself if it is a run directory, else the run directories under it."""
    if (path / 'upstream.json').is_file():
        return [path]
    return sorted(item.parent for item in path.glob('*/upstream.json'))


def main(argv: Optional[List[str]] = None) -> int:
    """Judge every run under a directory; print each run's checks and a verdict."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('path', type=Path)
    parser.add_argument('--require', default='', help='comma-separated variants some run must pass')
    args = parser.parse_args(argv)
    runs = find_runs(args.path)
    if not runs:
        print(f'relay_judge: no run records under {args.path}')
        return 1
    passed_variants, status = set(), 0
    for run in runs:
        records = load_run(run)
        checks = judge_run(records)
        ok = all(passed for _, passed, _ in checks)
        variant = records.get('upstream', {}).get('variant')
        print(f'{run.name}: {"PASS" if ok else "FAIL"} (variant {variant}, {len(checks)} checks)')
        for name, passed, detail in checks:
            if not passed:
                print(f'  FAIL {name}: {detail}')
        if ok:
            passed_variants.add(variant)
        else:
            status = 1
    for variant in filter(None, args.require.split(',')):
        if variant not in passed_variants:
            print(f'relay_judge: no passing run of variant {variant}')
            status = 1
    print(f'relay_judge: {"PASS" if status == 0 else "FAIL"} ({len(runs)} runs)')
    return status


if __name__ == '__main__':
    sys.exit(main())
