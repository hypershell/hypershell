# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Run this cycle's contended-write tests against one commit's ``src/`` on a given server (GOAL R7).

Run from the root of a checkout of this cycle's branch, with ``git`` and ``uv`` on ``PATH``:

    python3 spec/hypertable-update-retry/contention_run.py --label ts2.31-develop --commit 0e8275e \\
        --admin-url postgresql+psycopg://postgres:postgres@localhost:5432/postgres --timescale community

The tests exist only on this branch, so the runner takes them from ``--tests`` (default ``HEAD``) and
the code under test from ``--commit``: it adds a detached worktree at ``--tests``, replaces its
``src/`` with ``--commit``'s (``git archive``), syncs that tree's environment from the lock
(``uv sync --frozen --extra postgres``), and runs ``tests/test_contended_write.py`` there, so the
``hs`` the tests call is the build under test. The two commits must share ``pyproject.toml`` and
``uv.lock``. ``--admin-url`` is what CI passes as ``HS_TEST_POSTGRES`` (an admin URL on the
maintenance database); ``--timescale`` is ``HS_TEST_TIMESCALE`` (``community``, ``apache``, or empty
for a server without TimescaleDB).

Writes ``<out>/<label>.log`` (pytest's output), ``<out>/<label>.xml`` (JUnit) and
``<out>/<label>.json`` (the summary, also printed last): label, both commits, the server's version
and TimescaleDB version, each test's outcome (``passed``, ``failed``, ``error`` or ``skipped``) and
pytest's exit status. The password in ``--admin-url`` is never written. The worktree is removed
afterwards unless ``--keep``. ``--dry-run`` resolves and checks everything that needs no server or
network and prints the plan.

Exit status: 0 every test passed, 1 a test failed, 2 harness error, 124 the run hit its kill timer.
"""


# Standard libs
import os
import sys
import json
import shutil
import signal
import argparse
import subprocess
from pathlib import Path
from xml.etree import ElementTree


TEST_MODULE = 'tests/test_contended_write.py'
SERVER_FACTS = '''
import json, os, sqlalchemy
engine = sqlalchemy.create_engine(os.environ["HS_TEST_POSTGRES"])
with engine.connect() as conn:
    version = conn.execute(sqlalchemy.text("SHOW server_version")).scalar()
    ts = conn.execute(sqlalchemy.text(
        "SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'")).scalar()
print(json.dumps({"postgres": version, "timescaledb": ts}))
'''


class HarnessError(Exception):
    """The run could not be set up or read; not a test result."""


def git(*args: str, timeout: float = 120) -> str:
    """Run git in the current repository and return its stripped stdout."""
    proc = subprocess.run(['git', *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise HarnessError(f'git {" ".join(args)}: {proc.stderr.strip()}')
    return proc.stdout.strip()


def commit(rev: str) -> str:
    return git('rev-parse', '--verify', f'{rev}^{{commit}}')


def masked(url: str) -> str:
    """The URL without its password."""
    scheme, sep, rest = url.partition('://')
    creds, at, host = rest.rpartition('@')
    if not at or ':' not in creds:
        return url
    return f'{scheme}{sep}{creds.split(":", 1)[0]}:***@{host}'


def run(argv: list, cwd: Path, env: dict, timeout: float, log=None) -> int:
    """Run a command in its own session; kill its process group at the deadline (exit 124)."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log or subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return 124
    if proc.returncode != 0 and out:
        print(out[-4000:], file=sys.stderr)
    return proc.returncode


def outcomes(xml: Path) -> dict:
    """Each test function's outcome from the JUnit file."""
    if not xml.exists():
        raise HarnessError(f'no JUnit file at {xml}')
    found = {}
    for case in ElementTree.parse(xml).getroot().iter('testcase'):
        kinds = {child.tag for child in case}
        found[case.get('name')] = ('failed' if 'failure' in kinds else 'error' if 'error' in kinds
                                   else 'skipped' if 'skipped' in kinds else 'passed')
    return found


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--label', required=True, help='name for this run (files are <out>/<label>.*)')
    parser.add_argument('--commit', required=True, help='commit whose src/ is under test')
    parser.add_argument('--tests', default='HEAD', help='commit the tests come from (default: HEAD)')
    parser.add_argument('--admin-url', default=os.getenv('HS_TEST_POSTGRES', ''), help='HS_TEST_POSTGRES')
    parser.add_argument('--timescale', default=os.getenv('HS_TEST_TIMESCALE', ''),
                        choices=['', 'community', 'apache'], help='HS_TEST_TIMESCALE')
    parser.add_argument('--out', default='contention-out', help='directory for logs (default: contention-out)')
    parser.add_argument('--python', default='3.14', help='interpreter request passed to uv (default: 3.14)')
    parser.add_argument('--timeout', type=float, default=1200, help='seconds for each of sync and pytest')
    parser.add_argument('--keep', action='store_true', help='keep the worktree afterwards')
    parser.add_argument('--dry-run', action='store_true', help='check and print the plan; run nothing')
    return parser.parse_args(argv)


def main(argv: list) -> int:
    args = parse_args(argv)
    out = Path(args.out).resolve()
    tree = out / f'tree-{args.label}'
    summary = {'label': args.label}
    try:
        summary.update(commit=commit(args.commit), tests=commit(args.tests), timescale=args.timescale,
                       admin_url=masked(args.admin_url))
        for name in ('pyproject.toml', 'uv.lock'):
            if subprocess.run(['git', 'diff', '--quiet', summary['commit'], summary['tests'], '--', name]).returncode:
                raise HarnessError(f'{name} differs between {args.commit} and {args.tests}')
        if args.dry_run:
            # The tests may not be committed yet; check the working tree's copy.
            if not Path(TEST_MODULE).is_file():
                raise HarnessError(f'no {TEST_MODULE} in the working tree')
            print(json.dumps({**summary, 'out': str(out), 'module': TEST_MODULE, 'dry_run': True}, indent=2))
            return 0
        git('cat-file', '-e', f'{summary["tests"]}:{TEST_MODULE}')
        if not args.admin_url:
            raise HarnessError('--admin-url (or HS_TEST_POSTGRES) is required')
        if tree.exists():
            raise HarnessError(f'{tree} exists; remove it or pick another --label')
        out.mkdir(parents=True, exist_ok=True)
        git('worktree', 'add', '--detach', str(tree), summary['tests'])
        try:
            shutil.rmtree(tree / 'src')
            archive = out / f'{args.label}-src.tar'
            git('archive', '--format=tar', '-o', str(archive), summary['commit'], 'src')
            subprocess.run(['tar', '-xf', str(archive), '-C', str(tree)], check=True, timeout=120)
            archive.unlink()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith('HYPERSHELL_') and key != 'VIRTUAL_ENV'}
            env.update(HS_TEST_POSTGRES=args.admin_url, HS_TEST_TIMESCALE=args.timescale)
            if run(['uv', 'sync', '--frozen', '--extra', 'postgres', '--python', args.python],
                   tree, env, args.timeout) != 0:
                raise HarnessError('uv sync failed')
            facts = subprocess.run(['uv', 'run', '--frozen', '--extra', 'postgres', 'python', '-c', SERVER_FACTS],
                                   cwd=tree, env=env, capture_output=True, text=True, timeout=300)
            if facts.returncode != 0:
                raise HarnessError(f'could not read the server: {facts.stderr.strip()[-2000:]}')
            summary['server'] = json.loads(facts.stdout.strip().splitlines()[-1])
            xml = out / f'{args.label}.xml'
            with open(out / f'{args.label}.log', 'w') as log:
                status = run(['uv', 'run', '--frozen', '--extra', 'postgres', 'pytest', '-v', '-rs',
                              '-p', 'no:cacheprovider', f'--junitxml={xml}', TEST_MODULE],
                             tree, env, args.timeout, log=log)
            summary['exit'] = status
            if status == 124:
                return 124
            if status not in (0, 1):
                raise HarnessError(f'pytest exited {status}; see {args.label}.log')
            summary['outcomes'] = outcomes(xml)
        finally:
            if not args.keep:
                git('worktree', 'remove', '--force', str(tree))
        (out / f'{args.label}.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
        return 0 if all(value == 'passed' for value in summary['outcomes'].values()) else 1
    except (HarnessError, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f'contention_run: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
