# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Check the evidence recorded in this cycle's EVIDENCE.md against the GOAL's bars.

Run from the root of this cycle's worktree:

    python spec/writeback-prebuilt-update/check_evidence.py r1 [ci] [r6]

Each check reads one fenced JSON block in EVIDENCE.md, the one that follows a marker line
``<!-- evidence:r1 -->``, ``<!-- evidence:ci -->`` or ``<!-- evidence:r6 -->``. "The fix" is the last
commit that touched ``src/``; a build or CI run counts as the fix when its ``src/`` and ``tests/``
match that commit's.

- r1: the summary ``cpu_bench.py`` prints. N at least 5,000, at least three recorded rounds, the
  three builds at the right commits, and the fix's mean CPU per result at or below 2.8's.
- ci: ``{"run_id", "head_sha", "jobs": {name: conclusion}, "reruns": [...]}`` from the GitHub
  Actions run on the draft PR. Every job listed succeeded, and the four Python entries and the five
  PostgreSQL entries (two plain servers, three TimescaleDB) are all there. ``jobs`` holds each job's
  final conclusion. ``reruns`` (optional) records a rerun the foreman allowed at the plan gate:
  at most one, of ``tests (3.13)``, whose first attempt failed only in the documented restart-update
  flake, with exactly its signature (``FLAKE``). Any other rerun fails the check.
- r6: ``{"config", "runs": [{"label", "sha", "order", "tasks_per_s"}], "ruling"}`` from the
  foreman's cluster runs. The configuration is the GOAL's, each build has at least three runs, the
  runs are interleaved (each consecutive triple holds all three builds), and the fix's mean
  throughput is at or above 2.8.3's. A shortfall smaller than the spread (the wider of the two
  builds' max-minus-min ranges) passes only with a non-empty ``ruling`` from the maintainer.
"""


# Standard libs
import re
import sys
import json
import statistics
import subprocess
from pathlib import Path


EVIDENCE = Path(__file__).with_name('EVIDENCE.md')
BASELINE = 'b819de6'
DEVELOP = 'bc6547a'
RELEASE = '2.8.3'
CI_JOBS = [f'tests ({version})' for version in ('3.11', '3.12', '3.13', '3.14')] + [
    f'PostgreSQL ({name})' for name in ('pg13', 'pg18', 'timescale', 'timescale-oss', 'timescale-floor')]
# The one rerun the foreman's plan-gate ruling allows (2026-11-04): the Python 3.13 job, failing
# only in the documented restart-update flake, with exactly this signature.
FLAKE_JOB = 'tests (3.13)'
FLAKE = {'test': 'tests/test_restart.py::test_restart_update_adds_novel_and_runs',
         'signature': "assert [''] == ['4', '5']", 'location': 'test_restart.py:102'}
CLUSTER = {'clients': 256, 'executors_per_client': 1, 'bundlesize': 1, 'tls': True, 'database': 'sqlite',
           'server_cpus': 16}


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def commit(rev: str) -> str:
    return git('rev-parse', '--verify', f'{rev}^{{commit}}')


def same_code(rev: str, fix: str) -> bool:
    return subprocess.run(['git', 'diff', '--quiet', rev, fix, '--', 'src', 'tests'], timeout=60).returncode == 0


def block(text: str, name: str) -> dict:
    match = re.search(rf'<!-- evidence:{name} -->\s*```json\n(.*?)\n```', text, re.S)
    if not match:
        raise SystemExit(f'check_evidence: no "{name}" block in {EVIDENCE.name}')
    return json.loads(match.group(1))


def check_r1(data: dict, fix: str) -> str:
    host, summary = data['host'], data['summary']
    assert host['n'] >= 5000 and host['rounds'] >= 3, f'n={host["n"]}, rounds={host["rounds"]}'
    assert summary['2.8']['sha'] == commit(BASELINE), '2.8 is not measured at b819de6'
    assert summary['develop']['sha'] == commit(DEVELOP), 'develop is not measured at bc6547a'
    assert same_code(summary['fix']['sha'], fix), 'the measured fix is not the final fix'
    for label in ('2.8', 'develop', 'fix'):
        values = summary[label]['values']
        assert len(values) >= 3 and abs(statistics.mean(values) - summary[label]['mean']) < 1e-3, label
    fix_mean, base_mean = summary['fix']['mean'], summary['2.8']['mean']
    assert fix_mean <= base_mean, f'fix {fix_mean} ms/result is above 2.8 {base_mean} ms/result'
    return f'r1: fix {fix_mean} ms/result <= 2.8 {base_mean} ms/result (develop {summary["develop"]["mean"]})'


def check_ci(data: dict, fix: str) -> str:
    assert same_code(data['head_sha'], fix), 'the CI run is not on the final fix'
    jobs = data['jobs']
    missing = [name for name in CI_JOBS if name not in jobs]
    assert not missing, f'missing CI jobs: {missing}'
    failed = {name: result for name, result in jobs.items() if result != 'success'}
    assert not failed, f'CI jobs not successful: {failed}'
    reruns = data.get('reruns', [])
    assert isinstance(reruns, list) and len(reruns) <= 1, f'more than the one allowed rerun: {reruns}'
    for rerun in reruns:
        assert rerun.get('job') == FLAKE_JOB, f'rerun of {rerun.get("job")!r}; only {FLAKE_JOB} may be rerun'
        assert {key: rerun.get(key) for key in FLAKE} == FLAKE, f'rerun is not the documented flake: {rerun}'
        assert rerun.get('first_attempt') == 'failure', f'rerun without a failed first attempt: {rerun}'
    note = f', {FLAKE_JOB} rerun once for the documented flake' if reruns else ''
    return f'ci: run {data["run_id"]} at {data["head_sha"][:7]}, {len(jobs)} jobs successful{note}'


def check_r6(data: dict, fix: str) -> str:
    assert data['config'] == CLUSTER, f'configuration {data["config"]} is not {CLUSTER}'
    builds = {RELEASE: commit(RELEASE), 'develop': commit(DEVELOP)}
    runs = sorted(data['runs'], key=lambda run: run['order'])
    for run in runs:
        if run['label'] == 'fix':
            assert same_code(run['sha'], fix), f'run {run["order"]} is not the final fix'
        else:
            assert run['sha'] == builds[run['label']], f'run {run["order"]} has the wrong commit'
    rounds = [runs[i:i + 3] for i in range(0, len(runs), 3)]
    assert len(rounds) >= 3 and all(sorted(run['label'] for run in r) == sorted([RELEASE, 'develop', 'fix'])
                                    for r in rounds), 'runs are not three or more interleaved rounds'
    rates = {label: [run['tasks_per_s'] for run in runs if run['label'] == label] for label in (RELEASE, 'fix')}
    fix_mean, base_mean = statistics.mean(rates['fix']), statistics.mean(rates[RELEASE])
    note = f'fix {fix_mean:.1f} tasks/s vs 2.8.3 {base_mean:.1f} tasks/s over {len(rounds)} rounds'
    if fix_mean >= base_mean:
        return f'r6: {note}'
    spread = max(max(values) - min(values) for values in rates.values())
    assert base_mean - fix_mean < spread, f'{note}: short by more than the spread ({spread:.1f})'
    assert data.get('ruling'), f'{note}: short within the spread ({spread:.1f}); needs the maintainer\'s ruling'
    return f'r6: {note}; short within the spread, ruled: {data["ruling"]}'


def main(names: list) -> int:
    checks = {'r1': check_r1, 'ci': check_ci, 'r6': check_r6}
    if not names or any(name not in checks for name in names):
        print(f'usage: check_evidence.py {{{"|".join(checks)}}}...', file=sys.stderr)
        return 2
    fix = git('log', '-1', '--format=%H', '--', 'src')
    text = EVIDENCE.read_text()
    for name in names:
        try:
            print(checks[name](block(text, name), fix))
        except (AssertionError, KeyError, TypeError, ValueError) as error:
            print(f'check_evidence: {name} failed: {error!r}', file=sys.stderr)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
