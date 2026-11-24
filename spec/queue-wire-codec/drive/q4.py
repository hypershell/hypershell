#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Read a recorded throughput gate against the cycle's threshold (GOAL Q4), per config.

Usage: q4.py GATE_JSONL

GATE_JSONL holds the runs `tests/bench/bench_queue.py gate --out` appends, one JSON object per line
with its `config`, its `label` (base or feature) and its steady-state throughput `p10_p90`. For each
config the threshold is: the feature's median is at least the base's median less the larger of 5% of
it and the base's own min-max spread. (The bench's own verdict also admits the feature's spread; this
does not.) Every config needs at least five runs of each build. Prints a table and exits 1 if any
config misses, or if a run did not complete cleanly; 0 otherwise.
"""

import sys
import json
import statistics

REPEATS = 5


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    runs = {}
    with open(sys.argv[1], encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                run = json.loads(line)
                runs.setdefault(run['config'], {}).setdefault(run['label'], []).append(run)
    if not runs:
        print('q4: no runs recorded', file=sys.stderr)
        return 1
    ok = True
    print('| config | base median [min-max] | feature median [min-max] | delta | allowed drop | runs b/f | Q4 |')
    print('|---|---|---|---|---|---|---|')
    for config, labels in runs.items():
        base, feature = labels.get('base', []), labels.get('feature', [])
        tb, tf = [r['p10_p90'] for r in base], [r['p10_p90'] for r in feature]
        if len(tb) < REPEATS or len(tf) < REPEATS:
            print(f'| {config} | | | | | {len(tb)}/{len(tf)} | FAIL (fewer than {REPEATS} runs) |')
            ok = False
            continue
        mb, mf = statistics.median(tb), statistics.median(tf)
        allowed = max(0.05 * mb, max(tb) - min(tb))
        complete = all(r.get('complete') and not r.get('incomplete') for r in base + feature)
        passed = mf >= mb - allowed and complete
        ok &= passed
        print(f'| {config} | {mb:.1f} [{min(tb):.1f}-{max(tb):.1f}] | {mf:.1f} [{min(tf):.1f}-{max(tf):.1f}] '
              f'| {mf - mb:+.1f} ({100 * (mf - mb) / mb:+.1f}%) | {allowed:.1f} | {len(tb)}/{len(tf)} '
              f'| {"PASS" if passed else "FAIL"}{"" if complete else " (incomplete run)"} |')
    print(f'# Q4: {"PASS" if ok else "FAIL"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
