#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Judge multi-node drive results (drive.sh): identical results, throughput, and footprint by label.

    compare.py RESULTS --baseline LABEL --candidate LABEL [--control LABEL] [--results-only]
               [--min-ratio R] [--zoned LABEL]

RESULTS holds one directory per run with ``server.json`` and ``client-<index>.json``. Runs are
grouped by configuration (TLS, clients N, threads T, tasks M, server and client bundling, zones,
groups, retries, cap, and whether clients advertise a size), so modes never pool. For each
homogeneous group (no zones):

- **Results:** every run of every label has the same exit-status histogram, rows = M, no incomplete
  task, server and every client exiting 0, and as many client records as expected clients.
- **Throughput:** the candidate's median p10-p90 tasks/s is at least the baseline's median minus
  max(baseline spread, candidate spread, 5% of the baseline median); each label needs at least
  three runs.
- **Footprint:** the candidate's peak child processes and open file descriptors, summed over the
  server's process tree, are no higher than the baseline's.

With ``--min-ratio R`` the throughput rule is instead: the candidate's median is at least R times
the baseline's, judged only on groups whose clients advertise no size (``HSD_CLIENT_ADVERTISE=0``);
groups where clients advertise are held to results and footprint alone.

The control label, when given, is held to the same results check and reported against the
baseline for attribution; it does not change the verdict. ``--results-only`` checks results alone.

A zoned group (``HSD_ZONES`` set) is judged for ``--zoned LABEL`` only, run by run: every task
completed (rows = M + retried rows), server and clients exit 0, no task ran in another zone's
client (no exit 7, none recorded by a client), no retry changed zone, the clients' received bundles
sum to the rows (no task delivered twice per attempt), no client's largest bundle exceeds
min(its advertised size or the server's, the cap), and task groups ran in order. Without
``--zoned``, zoned groups are skipped.

Exit status: 0 pass, 1 fail, 2 insufficient runs (fewer than three for a label) or no results.
Standard library only.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Standard libs
import os
import sys
import json
import glob
import argparse
import statistics


MIN_RUNS = 3
FLOOR = 0.05


def load(results: str) -> List[Dict[str, Any]]:
    """Every run's server record, with its client records attached."""
    runs = []
    for path in sorted(glob.glob(os.path.join(results, '*', 'server.json'))):
        with open(path) as stream:
            run = json.load(stream)
        clients = []
        for client_path in sorted(glob.glob(os.path.join(os.path.dirname(path), 'client-*.json'))):
            with open(client_path) as stream:
                clients.append(json.load(stream))
        run['clients'] = clients
        runs.append(run)
    return runs


DEFAULT_CAP = 1000


def advertise(run: Dict[str, Any]) -> Optional[bool]:
    """Whether the run's clients advertise a size (None when the records predate the knob)."""
    flags = {client['advertised'] is not None for client in run['clients'] if 'advertised' in client}
    return None if not flags else (True if flags == {True} else False if flags == {False} else None)


def config_key(run: Dict[str, Any]) -> Tuple:
    return (run['tls'], run['clients_expected'], run['threads'], run['tasks'],
            run['bundlesize'], run.get('client_bundlesize'), run.get('client_bundlewait'),
            run.get('zones'), run.get('groups', 1), run.get('retries', 0), run.get('cap'), advertise(run))


def describe(key: Tuple) -> str:
    tls, clients, threads, tasks, bundle, client_bundle, client_wait, zones, groups, retries, cap, adv = key
    extra = ''
    if zones:
        extra += f' zones={zones}'
    if groups != 1 or retries:
        extra += f' G={groups} r={retries}'
    if cap is not None:
        extra += f' cap={cap}'
    if adv is not None:
        extra += f' advertise={int(adv)}'
    return (f'tls={"on" if tls else "off"} N={clients} T={threads} M={tasks} '
            f'b={bundle} cb={client_bundle} cw={client_wait:g}{extra}')


def zoned_problems(run: Dict[str, Any]) -> List[str]:
    """Why a zoned run fails its checks (empty when it passes)."""
    found = []
    expected_rows = run['tasks'] + run['retried']
    if run['server_rc'] != 0:
        found.append(f'server rc {run["server_rc"]}')
    if run['rows'] != expected_rows or run['incomplete']:
        found.append(f'rows {run["rows"]} (expected {expected_rows}), {run["incomplete"]} incomplete')
    if len(run['clients']) != run['clients_expected']:
        found.append(f'{len(run["clients"])} client records for {run["clients_expected"]} clients')
    failed = [client['index'] for client in run['clients'] if client['rc'] != 0]
    if failed:
        found.append(f'client rc != 0: {failed}')
    wrong = run['exit_hist'].get('7', 0) + sum(client.get('exit7', 0) for client in run['clients'])
    if wrong:
        found.append(f'{wrong} tasks ran on a client of another zone (exit 7)')
    if run.get('zone_mismatched_retries'):
        found.append(f'{run["zone_mismatched_retries"]} retries changed zone')
    received = [client.get('received') for client in run['clients']]
    if None in received:
        found.append('a client did not record its bundles (run clients with HSD_CLIENT_LOG=DEBUG)')
    elif sum(received) != run['rows']:
        found.append(f'clients received {sum(received)} tasks for {run["rows"]} rows')
    cap = max(run.get('cap') or DEFAULT_CAP, run['bundlesize'])
    for client in run['clients']:
        bound = min(client.get('advertised') or run['bundlesize'], cap)
        if client.get('largest') is not None and client['largest'] > bound:
            found.append(f'client {client["index"]} got a bundle of {client["largest"]} (bound {bound})')
    if run.get('group_order') is not True:
        found.append('task groups out of order')
    return found


def problems(run: Dict[str, Any]) -> List[str]:
    """Why this run's results are not clean (empty when clean)."""
    found = []
    if run['server_rc'] != 0:
        found.append(f'server rc {run["server_rc"]}')
    expected_rows = run['tasks'] + (run['retried'] if run.get('retries') else 0)
    if run['rows'] != expected_rows:
        found.append(f'rows {run["rows"]} != {expected_rows} (M {run["tasks"]})')
    if run['incomplete']:
        found.append(f'{run["incomplete"]} incomplete')
    if len(run['clients']) != run['clients_expected']:
        found.append(f'{len(run["clients"])} client records for {run["clients_expected"]} clients')
    failed = [client['index'] for client in run['clients'] if client['rc'] != 0]
    if failed:
        found.append(f'client rc != 0: {failed}')
    if run.get('p10_p90_tasks_per_s') is None:
        found.append('no throughput')
    return found


def summary(runs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rates = [run['p10_p90_tasks_per_s'] for run in runs if run.get('p10_p90_tasks_per_s') is not None]
    peaks = [run['peak'] for run in runs if run.get('peak')]
    return dict(n=len(runs), median=statistics.median(rates) if rates else None,
                low=min(rates, default=None), high=max(rates, default=None),
                spread=(max(rates) - min(rates)) if rates else None,
                children=max((peak['children'] for peak in peaks), default=None),
                fds=max((peak['tree_fds'] for peak in peaks), default=None),
                threads=max((peak['tree_threads'] for peak in peaks), default=None),
                rss=max((peak['tree_rss_mb'] for peak in peaks), default=None))


def num(value: Optional[float], spec: str = 'g') -> str:
    return '-' if value is None else format(value, spec)


def fmt(stats: Dict[str, Any]) -> str:
    if stats['median'] is None:
        return f'n={stats["n"]} (no throughput)'
    return (f'n={stats["n"]} median {stats["median"]:.1f} [{stats["low"]:.1f}-{stats["high"]:.1f}] '
            f'spread {stats["spread"]:.1f}; peak tree children {num(stats["children"])} fds {num(stats["fds"])} '
            f'threads {num(stats["threads"])} rss {num(stats["rss"], ".0f")} MB')


def compare(runs: List[Dict[str, Any]], baseline: str, candidate: str, control: Optional[str],
            results_only: bool, min_ratio: Optional[float] = None, zoned: Optional[str] = None) -> int:
    labels = list(dict.fromkeys(label for label in (baseline, candidate, control, zoned) if label))
    groups: Dict[Tuple, Dict[str, List[Dict[str, Any]]]] = {}
    for run in runs:
        if run['label'] in labels:
            groups.setdefault(config_key(run), {}).setdefault(run['label'], []).append(run)
    if not groups:
        print('no results for the given labels')
        return 2
    status = 0
    insufficient = False
    for key, by_label in sorted(groups.items(), key=lambda item: repr(item[0])):
        print(describe(key))
        if key[7]:
            if not zoned:
                print('  zoned group: skipped (no --zoned)')
                continue
            zoned_runs = by_label.get(zoned, [])
            if not zoned_runs:
                print(f'  {zoned}: no zoned runs -> FAIL')
                status = 1
            for run in zoned_runs:
                found = zoned_problems(run)
                for problem in found:
                    print(f'  {zoned} {run["run_id"]}: {problem} -> FAIL')
                print(f'  {zoned} {run["run_id"]}: zones {run.get("zone_tasks")}, '
                      f'{run["rows"]} rows, {run["retried"]} retried -> {"FAIL" if found else "PASS"}')
                status = status or (1 if found else 0)
            continue
        labels_here = [label for label in labels if label != zoned or label in (baseline, candidate, control)]
        histograms = set()
        for label in labels_here:
            label_runs = by_label.get(label, [])
            if not label_runs:
                print(f'  {label}: no runs -> FAIL')
                status = 1
                continue
            for run in label_runs:
                histograms.add(json.dumps(run['exit_hist'], sort_keys=True))
                for problem in problems(run):
                    print(f'  {label} {run["run_id"]}: {problem} -> FAIL')
                    status = 1
            print(f'  {label}: {fmt(summary(label_runs))}')
        same = len(histograms) == 1
        print(f'  results: {"identical" if same else "DIFFER"} across runs and labels '
              f'({" | ".join(sorted(histograms))}) -> {"PASS" if same else "FAIL"}')
        status = status or (0 if same else 1)
        if results_only or baseline not in by_label or candidate not in by_label:
            continue
        base, cand = summary(by_label[baseline]), summary(by_label[candidate])
        if min(base['n'], cand['n'], summary(by_label.get(control, []))['n'] if control else MIN_RUNS) < MIN_RUNS:
            print(f'  throughput: insufficient runs (need {MIN_RUNS} per label)')
            insufficient = True
            continue
        if min_ratio is None:
            tolerance = max(base['spread'], cand['spread'], FLOOR * base['median'])
            fast = cand['median'] >= base['median'] - tolerance
            print(f'  throughput {candidate} vs {baseline}: {cand["median"] - base["median"]:+.1f} tasks/s, '
                  f'tolerance {tolerance:.1f} -> {"PASS" if fast else "FAIL"}')
        elif key[11] is False:
            ratio = cand['median'] / base['median']
            fast = ratio >= min_ratio
            print(f'  throughput {candidate} vs {baseline}: ratio {ratio:.3f} of the baseline median '
                  f'(min {min_ratio:g}) -> {"PASS" if fast else "FAIL"}')
        else:
            fast = True
            print(f'  throughput: not judged (clients advertise a size; --min-ratio judges advertise=0 groups)')
        if control and control in by_label:
            ctrl = summary(by_label[control])
            print(f'  attribution {control} vs {baseline}: {ctrl["median"] - base["median"]:+.1f} tasks/s; '
                  f'{candidate} vs {control}: {cand["median"] - ctrl["median"]:+.1f} tasks/s')
        measured = None not in (cand['children'], base['children'], cand['fds'], base['fds'])
        small = measured and cand['children'] <= base['children'] and cand['fds'] <= base['fds']
        print(f'  footprint {candidate} vs {baseline}: tree children {num(cand["children"])} vs '
              f'{num(base["children"])}, tree fds {num(cand["fds"])} vs {num(base["fds"])} '
              f'-> {"PASS" if small else "FAIL"}')
        if not (fast and small):
            status = 1
    if status == 0 and insufficient:
        status = 2
    print(f'verdict: {"PASS" if status == 0 else "FAIL" if status == 1 else "INSUFFICIENT RUNS"}')
    return status


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='compare.py', description=__doc__.split('\n\n')[0].strip())
    parser.add_argument('results')
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--control')
    parser.add_argument('--results-only', action='store_true')
    parser.add_argument('--min-ratio', type=float, default=None)
    parser.add_argument('--zoned')
    args = parser.parse_args(argv)
    return compare(load(args.results), args.baseline, args.candidate, args.control, args.results_only,
                   args.min_ratio, args.zoned)


if __name__ == '__main__':
    sys.exit(main())
