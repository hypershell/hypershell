# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Judge a recorded CI run of the `Tests` workflow for this cycle: red at the tests-first commit, or green.

Run from the repository root:

    uv run python spec/contended-writes/drive/ci_check.py red   EVIDENCE_DIR --code-rev REV
    uv run python spec/contended-writes/drive/ci_check.py green EVIDENCE_DIR --code-rev REV

EVIDENCE_DIR holds what the foreman recorded from the run (no network is used here):

    run.json     gh run view RUN --json databaseId,headSha,conclusion,jobs
    failed.log   gh run view RUN --log-failed          (red only)

The run's head must carry the code of REV: `git diff REV HEADSHA -- . ':!spec'` is empty, so only
spec/ may differ.

red: every job's failed tests, read from failed.log, are exactly the ones expected for it, and a job
concluded `failure` exactly when it has any. Expected: the `tests (3.x)` jobs fail the SQLite lines
of spec/contended-writes/red.txt; every `PostgreSQL (…)` job fails its `pg:` lines; and the jobs
named in spec/contended-writes/ci-red.txt (`JOB[,JOB…] NODEID` per line) fail those tests too. All
other jobs (install, metadata, docs) succeed.

green: the run and every job concluded `success`.

Exit status 0 when the run is as expected; 1 with one line per difference otherwise; 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set

SPEC = Path(__file__).resolve().parent.parent
FAILED = re.compile(r'(?:FAILED (tests/\S+::\S+))|(?:(tests/\S+::\S+) FAILED)')
PYTHON_JOB = re.compile(r'^tests \(3\.\d+\)$')
PG_JOB = re.compile(r'^PostgreSQL \((?P<entry>[^)]+)\)$')


def listed(path: Path) -> Dict[str, Set[str]]:
    """The red list's SQLite and server-database node ids."""
    nodes: Dict[str, Set[str]] = {'sqlite': set(), 'pg': set()}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line = line.split(' raises=')[0]
        if line.startswith('pg:'):
            nodes['pg'].add(line[3:])
        else:
            nodes['sqlite'].add(line)
    return nodes


def ci_only(path: Path) -> Dict[str, Set[str]]:
    """Tests expected to fail only on the named PostgreSQL matrix entries."""
    expected: Dict[str, Set[str]] = {}
    if not path.exists():
        return expected
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        jobs, node = line.split(None, 1)
        for job in jobs.split(','):
            expected.setdefault(job, set()).add(node.strip())
    return expected


def failed_by_job(log: str) -> Dict[str, Set[str]]:
    """Failed test node ids per job name, from `gh run view --log-failed` (job TAB step TAB line)."""
    failed: Dict[str, Set[str]] = {}
    for line in log.splitlines():
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        for match in FAILED.finditer(parts[-1]):
            failed.setdefault(parts[0], set()).add(match.group(1) or match.group(2))
    return failed


def same_code(rev: str, head: str) -> bool:
    """Head carries the code of `rev` (only spec/ may differ)."""
    return subprocess.run(['git', 'diff', '--quiet', rev, head, '--', '.', ':!spec'], check=False).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('mode', choices=['red', 'green'])
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--code-rev', required=True)
    args = parser.parse_args()

    try:
        run = json.loads((args.evidence / 'run.json').read_text())
    except (OSError, ValueError) as error:
        print(f'ci_check: cannot read {args.evidence / "run.json"}: {error}')
        return 2
    problems = []
    head = run.get('headSha', '')
    if not head or not same_code(args.code_rev, head):
        problems.append(f'run {run.get("databaseId")} head {head or "?"} does not carry the code of {args.code_rev}')
    jobs = {job['name']: job.get('conclusion') for job in run.get('jobs', [])}
    if not jobs:
        problems.append('the run lists no jobs')

    if args.mode == 'green':
        if run.get('conclusion') != 'success':
            problems.append(f'run concluded {run.get("conclusion")}')
        problems.extend(f'job {name} concluded {conclusion}' for name, conclusion in sorted(jobs.items())
                        if conclusion != 'success')
    else:
        try:
            log = (args.evidence / 'failed.log').read_text(errors='replace')
        except OSError as error:
            print(f'ci_check: cannot read {args.evidence / "failed.log"}: {error}')
            return 2
        red = listed(SPEC / 'red.txt')
        extra = ci_only(SPEC / 'ci-red.txt')
        failed = failed_by_job(log)
        for name, conclusion in sorted(jobs.items()):
            if PYTHON_JOB.match(name):
                expected = red['sqlite']
            elif (pg := PG_JOB.match(name)) is not None:
                expected = red['pg'] | extra.get(pg.group('entry'), set())
            else:
                expected = set()
            got = failed.get(name, set())
            if got != expected:
                for node in sorted(expected - got):
                    problems.append(f'{name}: expected to fail, did not: {node}')
                for node in sorted(got - expected):
                    problems.append(f'{name}: failed, not expected: {node}')
            want = 'failure' if expected else 'success'
            if conclusion != want:
                problems.append(f'{name}: concluded {conclusion}, expected {want}')
        unknown = set(failed) - set(jobs)
        problems.extend(f'failed.log names a job the run does not list: {name}' for name in sorted(unknown))

    for line in problems:
        print(line)
    print(f'ci_check: {args.mode}, {len(jobs)} jobs, {len(problems)} difference(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
