#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge the relay tail runs on the head against the bound, and table them beside a baseline.

Usage: tail_bound.py HEAD_DIR [--baseline DIR] [--table PATH] [--limit 3] [--min-runs 3] [--budget 10]

Reads the `relay-hardening-tail` records (`*.json`, as `spec/relay-hardening/drive/tail_run.py`
writes them) in HEAD_DIR and exits 1 when any of these holds:

- more records than the budget (every started run counts);
- a record not at 0.2 s tasks, 4 site clients of 8 threads, with 2000 or 2004 tasks;
- fewer than `--min-runs` complete runs at either size;
- a relay tail missing or over `--limit` seconds;
- a record with `src_changed`, or records from more than one commit.

It gates on the tail only: `tail_run.py` attributes a held site bundle to s2 and s4, not s1, at a
task count whose last bundle is partial. With `--table` it writes a markdown table: the baseline's
rows, then the head's, then the least and greatest tail at each size. Exits 64 on bad usage.
Standard library only.
"""

import sys
import json
import argparse
from pathlib import Path

SIZES = (2000, 2004)
SHAPE = {'sleep': '0.2', 'clients': 4, 'threads': 8}
SEGMENTS = [f's{n}' for n in range(1, 10)]


def load(folder: Path) -> list:
    records = []
    for path in sorted(folder.glob('*.json')):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise SystemExit(f'tail_bound: cannot read {path}: {error}')
        if data.get('kind') == 'relay-hardening-tail':
            records.append(data)
    return records


def tail(record: dict):
    value = (record.get('relay') or {}).get('tail')
    return value if isinstance(value, (int, float)) else None


def problems(records: list, limit: float, min_runs: int, budget: int) -> list:
    found = []
    if len(records) > budget:
        found.append(f'{len(records)} records, over the budget of {budget}')
    complete = {size: 0 for size in SIZES}
    for record in records:
        name, sizes = record.get('run_id', '?'), record.get('sizes') or {}
        if any(str(sizes.get(key)) != str(value) for key, value in SHAPE.items()) or sizes.get('tasks') not in SIZES:
            found.append(f'{name}: sizes {sizes} are not the drive\'s')
        if record.get('src_changed'):
            found.append(f'{name}: ran with uncommitted source changes')
        value = tail(record)
        if value is None:
            found.append(f'{name}: no relay tail')
        elif value > limit:
            found.append(f'{name}: tail {value:.3f} s over {limit:g} s')
        if record.get('complete') and sizes.get('tasks') in complete:
            complete[sizes['tasks']] += 1
    for size, count in complete.items():
        if count < min_runs:
            found.append(f'{count} complete runs at {size} tasks, fewer than {min_runs}')
    commits = {record.get('commit') for record in records}
    if len(commits) > 1:
        found.append(f'records from {len(commits)} commits: {sorted(map(str, commits))}')
    return found


def cell(value) -> str:
    return '-' if value is None else f'{value:.2f}'


def rows(records: list) -> list:
    lines = []
    for record in records:
        relay, direct = record.get('relay') or {}, record.get('direct') or {}
        segments = relay.get('segments') or {}
        lines.append('| ' + ' | '.join([
            record.get('run_id', '?'), record.get('label', ''), str(record.get('commit', '')),
            str((record.get('sizes') or {}).get('tasks', '')), cell(tail(record)), cell(direct.get('tail')),
            *[cell(segments.get(name)) for name in SEGMENTS],
            f"{cell(relay.get('wall'))} / {cell(direct.get('wall'))}"]) + ' |')
    return lines


def summary(label: str, records: list) -> list:
    lines = []
    for size in SIZES:
        tails = [value for record in records if (record.get('sizes') or {}).get('tasks') == size
                 for value in [tail(record)] if value is not None]
        if tails:
            lines.append(f'| {label} | {size} | {len(tails)} | {min(tails):.2f} | {max(tails):.2f} |')
    return lines


def table(baseline: list, head: list) -> str:
    header = ['Run', 'Label', 'Commit', 'Tasks', 'Tail (s)', 'Direct tail (s)', *SEGMENTS, 'Wall relay / direct (s)']
    lines = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    lines += rows(baseline) + rows(head)
    lines += ['', '| Records | Tasks | Runs | Least tail (s) | Greatest tail (s) |', '|---|---|---|---|---|']
    lines += summary('baseline', baseline) + summary('head', head)
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(prog='tail_bound.py', add_help=True)
    parser.add_argument('head', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--table', type=Path)
    parser.add_argument('--limit', type=float, default=3.0)
    parser.add_argument('--min-runs', type=int, default=3)
    parser.add_argument('--budget', type=int, default=10)
    try:
        args = parser.parse_args()
    except SystemExit as exit:
        return 0 if exit.code == 0 else 64
    if not args.head.is_dir() or (args.baseline is not None and not args.baseline.is_dir()):
        print('tail_bound: HEAD_DIR and --baseline must be directories', file=sys.stderr)
        return 64
    head = load(args.head)
    baseline = load(args.baseline) if args.baseline is not None else []
    if args.table is not None:
        args.table.write_text(table(baseline, head))
    found = problems(head, args.limit, args.min_runs, args.budget)
    for line in found:
        print(f'tail_bound: {line}', file=sys.stderr)
    tails = ', '.join(f"{record.get('run_id')} {cell(tail(record))}" for record in head) or 'none'
    print(f"tail_bound: {'FAIL' if found else 'PASS'} - {len(head)} records; tails {tails}")
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
