# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge of the end-of-run tail records written by tail_run.py.

    python tail_judge.py [--budget N] [--require-none-over S] [--label L] [--outcome] PATH...

PATH is a record (`*.json` written by tail_run.py) or a directory searched for them (not recursively:
each directory is one budget). One line is printed per run:

    RUN  LABEL  COMMIT  tail  direct-tail  s1 ... s9  FLAGS

with the tail and segments in seconds. FLAGS: `TAIL>5` when the tail is over 5 s; `sN-OVER` when a
segment is over its design bound (BOUNDS, plus SLACK for logging and scheduling); `INCOMPLETE` when
the run did not finish (every task succeeded, both runs exited 0); `SIZE` when the run was not at the
R8 size (2000 tasks of 0.2 s on 4 clients of 8 threads); `MISSING` when a segment could not be
measured. A segment over its bound is unexplained by the designed chain. The last line sums up.

Options:
  --budget N             fail when a directory holds more than N records, or a record that is not
                         at the R8 size or did not complete
  --require-none-over S  fail when a run (of --label, if given) has a tail over S seconds
  --label L              only the runs of this label count for --require-none-over (all are printed)
  --outcome              check the recorded outcome, OUTCOME.md in the directory that holds the
                         PATHs (see `check_outcome`), against the records

Exit status: 0 when every check passed, 1 when one failed or no record was found, 64 on bad usage.
Standard library only.
"""


# Standard libs
import re
import sys
import json
import argparse
from pathlib import Path


# Design bounds of the segments (seconds): research/07-tail-investigation.md, with the site client's
# hold, which that brief's table folded into the relay's (TECH.md P7).
BOUNDS = {
    's1': 5.1,  # last completion -> the relay-local server stores it: the site client's bundle wait (relay.bundlewait)
    's2': 5.1,  # -> the relay returns it: relay.bundlewait 5 s + its poll 0.1 s
    's3': 2.0,  # -> the upstream server stores it (DEFAULT_REMOTE_TIMEOUT)
    's4': 2.0,  # -> the upstream scheduler finishes (its next look, woken by a request)
    's5': 2.0,  # -> the upstream server signals: the heart monitor's 2 s heartbeat get
    's6': 2.1,  # -> the relay hears it: its request wait 2 s and gate 0.1 s
    's7': 3.0,  # -> the relay-local server signals the site: STOP_CHECK 1 s and its own 2 s get
    's8': 2.0,  # -> the relay's final heartbeat: a site client leaves in about 1 s
    's9': 2.5,  # -> the upstream server's last line: its 2 s get and the sentinels
}
SLACK = 0.25
TAIL_LIMIT = 5.0
R8_SIZE = {'tasks': 2000, 'sleep': '0.2', 'clients': 4, 'threads': 8}
OUTCOME = re.compile(r'^(outcome|post-fix-label|seed):\s*(\S+)\s*$', re.MULTILINE)


def load(paths):
    """Every record under `paths`, grouped by the directory that holds it: {dir: [record, ...]}."""
    found = {}
    for path in map(Path, paths):
        files = sorted(path.glob('*.json')) if path.is_dir() else [path]
        group = found.setdefault(path if path.is_dir() else path.parent, [])
        for file in files:
            try:
                record = json.loads(file.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get('kind') == 'relay-hardening-tail':
                group.append(record)
    return found


def flags(record):
    """The flags of one run (see the module notes)."""
    found = []
    relay = record.get('relay') or {}
    tail = relay.get('tail')
    if not record.get('complete'):
        found.append('INCOMPLETE')
    if record.get('sizes') != R8_SIZE:
        found.append('SIZE')
    if tail is not None and tail > TAIL_LIMIT:
        found.append(f'TAIL>{TAIL_LIMIT:g}')
    segments = relay.get('segments') or {}
    for name, bound in BOUNDS.items():
        value = segments.get(name)
        if value is None:
            if 'MISSING' not in found:
                found.append('MISSING')
        elif value > bound + SLACK:
            found.append(f'{name}-OVER')
    return found


def number(value):
    return '-' if value is None else f'{value:.2f}'


def line(record):
    """One run's line."""
    relay, direct = record.get('relay') or {}, record.get('direct') or {}
    segments = relay.get('segments') or {}
    cells = [record.get('run_id', '?'), record.get('label', '?'), str(record.get('commit')),
             number(relay.get('tail')), number(direct.get('tail'))]
    cells += [number(segments.get(name)) for name in BOUNDS]
    return '  '.join(cells + [' '.join(flags(record)) or 'ok'])


def check_outcome(root, records):
    """Check OUTCOME.md in `root` against the records; return the problems found (none: it holds).

    The file names its outcome in a line `outcome: a` or `outcome: b`. Outcome (a), the cause named
    and fixed, needs a line `post-fix-label: LABEL` naming the label of the runs made on the fixed
    head: there must be at least one such run, every one complete, at the R8 size, with no tail over
    5 s. Outcome (b), the runs recorded and the rest left in a narrowed seed, needs a line
    `seed: issues/NAME.md` naming a seed that exists in the repository, with `status: unshaped`.
    """
    path = root / 'OUTCOME.md'
    if not path.exists():
        return [f'no outcome recorded ({path})']
    fields = dict(OUTCOME.findall(path.read_text()))
    outcome = fields.get('outcome')
    problems = []
    if outcome == 'a':
        label = fields.get('post-fix-label')
        runs = [record for record in records if record.get('label') == label]
        if not label or not runs:
            problems.append(f'outcome (a) names no post-fix runs (label {label!r})')
        for record in runs:
            tail = (record.get('relay') or {}).get('tail')
            if not record.get('complete') or record.get('sizes') != R8_SIZE or tail is None or tail > TAIL_LIMIT:
                problems.append(f'post-fix run {record.get("run_id")} is not a complete R8 run with a tail '
                                f'within {TAIL_LIMIT:g} s (tail {tail})')
    elif outcome == 'b':
        seed = fields.get('seed')
        repo = next((parent for parent in [root, *root.parents] if (parent / '.git').exists()), None)
        target = None if not seed or repo is None else repo / seed
        if target is None or not target.exists():
            problems.append(f'outcome (b) names no seed that exists ({seed!r})')
        elif 'status: unshaped' not in target.read_text():
            problems.append(f'seed {seed} is not `status: unshaped`')
    else:
        problems.append(f'no outcome (a) or (b) in {path}')
    return problems


def main(argv=None):
    """Print each run and check the requested gates."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--budget', type=int)
    parser.add_argument('--require-none-over', type=float)
    parser.add_argument('--label')
    parser.add_argument('--outcome', action='store_true')
    given = parser.parse_args(argv)
    groups = load(given.paths)
    records = [record for group in groups.values() for record in group]
    print('  '.join(['run', 'label', 'commit', 'tail', 'direct'] + list(BOUNDS) + ['flags']))
    for record in records:
        print(line(record))
    failed = not records
    if not records:
        print('tail_judge: no records found')
    if given.budget is not None:
        for directory, group in groups.items():
            bad = [record.get('run_id') for record in group
                   if not record.get('complete') or record.get('sizes') != R8_SIZE]
            if len(group) > given.budget:
                print(f'tail_judge: {directory} holds {len(group)} records, over the budget of {given.budget}')
                failed = True
            if bad:
                print(f'tail_judge: {directory}: not complete R8 runs: {", ".join(map(str, bad))}')
                failed = True
    if given.require_none_over is not None:
        over = [record.get('run_id') for record in records
                if (given.label is None or record.get('label') == given.label)
                and ((record.get('relay') or {}).get('tail') is None
                     or record['relay']['tail'] > given.require_none_over)]
        if over:
            print(f'tail_judge: tail over {given.require_none_over:g} s (or not measured): {", ".join(map(str, over))}')
            failed = True
    if given.outcome:
        roots = {Path(path).resolve().parent if Path(path).is_dir() else Path(path).resolve().parent.parent
                 for path in given.paths}
        if len(roots) != 1:
            print('tail_judge: --outcome needs PATHs in one evidence directory')
            failed = True
        else:
            root = roots.pop()
            problems = check_outcome(root, records)
            fields = dict(OUTCOME.findall((root / 'OUTCOME.md').read_text())) if (root / 'OUTCOME.md').exists() else {}
            print(f'tail_judge: outcome {fields.get("outcome", "?")}: '
                  f'{"holds" if not problems else "; ".join(problems)}')
            failed = failed or bool(problems)
    over = sum(1 for record in records if f'TAIL>{TAIL_LIMIT:g}' in flags(record))
    unexplained = sum(1 for record in records if any(flag.endswith('-OVER') for flag in flags(record)))
    print(f'tail_judge: {len(records)} runs, {over} over {TAIL_LIMIT:g} s, {unexplained} unexplained; '
          f'{"FAIL" if failed else "PASS"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
