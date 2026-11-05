# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Check the evidence recorded in this cycle's EVIDENCE.md against GOAL R7.

Run from the root of this cycle's worktree:

    python spec/hypertable-update-retry/check_evidence.py r7 [ci]

Each check reads one fenced JSON block in EVIDENCE.md, the one that follows a marker line
``<!-- evidence:r7 -->`` or ``<!-- evidence:ci -->``. "The fix" is the last commit that touched
``src/``; a run counts as the fix when its ``src/`` matches that commit's.

- r7: ``{"runs": [<contention_run.py summary>, ...]}`` from the foreman's runs on a test cluster.
  There is a run of ``develop`` (0e8275e) and of the fix on TimescaleDB 2.31.0 and on 2.24.0, each
  with the final test module (its ``tests`` commit holds the same ``tests/test_contended_write.py``
  as ``HEAD``) and every test of ``TESTS`` reported. On 2.31.0, ``develop`` fails exactly the tests
  in ``RED``, by assertion (``failed``, never ``error``), and passes the rest; everywhere else every
  test passes.
- ci: ``{"run_id", "head_sha", "jobs": {name: conclusion}, "contended": {job: {"passed", "skipped"}},
  "reruns": [...]}`` from the GitHub Actions run on the draft PR. Every job listed succeeded; the four
  Python entries and the five PostgreSQL entries are all there; each PostgreSQL entry passed every
  test of ``TESTS`` and skipped none. ``reruns`` (optional) is the one rerun the documented
  restart-update flake allows, as in ``writeback-prebuilt-update``.
"""


# Standard libs
import re
import sys
import json
import subprocess
from pathlib import Path


EVIDENCE = Path(__file__).with_name('EVIDENCE.md')
MODULE = 'tests/test_contended_write.py'
DEVELOP = '0e8275e'
# The tests whose contended write is lost on develop under TimescaleDB 2.29 and later.
RED = {'test_contended_confirm_records_client', 'test_contended_result_applies',
       'test_contended_orphan_revert_lands'}
TESTS = RED | {'test_rerun_after_contended_update_matches', 'test_contended_result_refused_when_reverted'}
SERVERS = ('2.31.0', '2.24.0')
EXPOSED = '2.31.0'
POSTGRES_JOBS = [f'PostgreSQL ({name})' for name in ('pg13', 'pg18', 'timescale', 'timescale-oss', 'timescale-floor')]
CI_JOBS = [f'tests ({version})' for version in ('3.11', '3.12', '3.13', '3.14')] + POSTGRES_JOBS
FLAKE_JOB = 'tests (3.13)'
FLAKE = {'test': 'tests/test_restart.py::test_restart_update_adds_novel_and_runs',
         'signature': "assert [''] == ['4', '5']", 'location': 'test_restart.py:102'}


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def commit(rev: str) -> str:
    return git('rev-parse', '--verify', f'{rev}^{{commit}}')


def same(rev: str, other: str, *paths: str) -> bool:
    return subprocess.run(['git', 'diff', '--quiet', rev, other, '--', *paths], timeout=60).returncode == 0


def block(text: str, name: str) -> dict:
    match = re.search(rf'<!-- evidence:{name} -->\s*```json\n(.*?)\n```', text, re.S)
    if not match:
        raise SystemExit(f'check_evidence: no "{name}" block in {EVIDENCE.name}')
    return json.loads(match.group(1))


def check_r7(data: dict, fix: str) -> str:
    develop = commit(DEVELOP)
    seen = set()
    for run in data['runs']:
        label = run['label']
        version = run['server']['timescaledb']
        assert version in SERVERS, f'{label}: TimescaleDB {version} is not one of {SERVERS}'
        assert same(run['tests'], 'HEAD', MODULE), f'{label}: not run with the final {MODULE}'
        if run['commit'] == develop:
            build = 'develop'
        else:
            assert same(run['commit'], fix, 'src'), f'{label}: {run["commit"][:7]} is neither develop nor the fix'
            build = 'fix'
        outcomes = run['outcomes']
        assert set(outcomes) == TESTS, f'{label}: tests {sorted(outcomes)} are not {sorted(TESTS)}'
        red = RED if (build, version) == ('develop', EXPOSED) else set()
        expected = {name: 'failed' if name in red else 'passed' for name in TESTS}
        assert outcomes == expected, f'{label}: {outcomes} != expected {expected}'
        assert run['exit'] == (1 if red else 0), f'{label}: pytest exit {run["exit"]}'
        seen.add((build, version))
    missing = {(build, version) for build in ('develop', 'fix') for version in SERVERS} - seen
    assert not missing, f'missing runs: {sorted(missing)}'
    return f'r7: {len(data["runs"])} runs; develop red on {EXPOSED} in exactly {sorted(RED)}; the fix green on {SERVERS}'


def check_ci(data: dict, fix: str) -> str:
    assert same(data['head_sha'], fix, 'src', 'tests'), 'the CI run is not on the final fix'
    jobs = data['jobs']
    missing = [name for name in CI_JOBS if name not in jobs]
    assert not missing, f'missing CI jobs: {missing}'
    failed = {name: result for name, result in jobs.items() if result != 'success'}
    assert not failed, f'CI jobs not successful: {failed}'
    for name in POSTGRES_JOBS:
        counts = data['contended'][name]
        assert counts == {'passed': len(TESTS), 'skipped': 0}, f'{name}: contended tests {counts}'
    reruns = data.get('reruns', [])
    assert isinstance(reruns, list) and len(reruns) <= 1, f'more than the one allowed rerun: {reruns}'
    for rerun in reruns:
        assert rerun.get('job') == FLAKE_JOB, f'rerun of {rerun.get("job")!r}; only {FLAKE_JOB} may be rerun'
        assert {key: rerun.get(key) for key in FLAKE} == FLAKE, f'rerun is not the documented flake: {rerun}'
        assert rerun.get('first_attempt') == 'failure', f'rerun without a failed first attempt: {rerun}'
    note = f', {FLAKE_JOB} rerun once for the documented flake' if reruns else ''
    return f'ci: run {data["run_id"]} at {data["head_sha"][:7]}, {len(jobs)} jobs successful{note}'


def main(names: list) -> int:
    checks = {'r7': check_r7, 'ci': check_ci}
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
