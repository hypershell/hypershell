# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""CPU per result of the server's result writeback, 2.8 against develop against this fix (GOAL R1).

Run from the root of this cycle's worktree, after the fix is committed, with any interpreter whose
environment holds the locked dependencies (the three builds share one lock):

    uv run --frozen python spec/writeback-prebuilt-update/cpu_bench.py

Each build's ``src/`` is extracted from its commit with ``git archive``; nothing is installed and
nothing is fetched. Every measurement is a fresh child process, in its own session under a kill
timer, that imports ``hypershell`` from one extracted tree. The child fills a throwaway SQLite
database with N tasks, dispatches them with ``Task.next``, finishes each copy as a client does and
passes it through the bundle codec, then times only the server's write: ``Receiver.update_tasks``
on N one-result bundles (bundle size 1, as in the cluster runs). That is ``Task.update_all`` of the
full row on 2.8 and ``Task.apply_results`` on develop and on the fix. One result in a hundred
carries a non-zero exit status. The metric is CPU time (``time.process_time``) per result.

The builds run interleaved: a warm-up pass that is not recorded, then rounds that rotate the order.
The bar is the fix's mean at or below 2.8's mean. Exit status: 0 bar met, 1 bar missed, 2 harness
error, 124 a child hit its kill timer. Raw measurements go to ``<out>/runs.jsonl``; the summary is
printed as JSON and as a Markdown table for ``EVIDENCE.md``.
"""


# Standard libs
import os
import sys
import json
import time
import shutil
import signal
import argparse
import platform
import statistics
import subprocess
import tempfile


# The 2.8 writeback (2.8.3's ``Task.update_all`` of the full row); b819de6 carries the same path.
BASELINE = 'b819de6'
# Develop at shaping, with #70's per-result statement.
DEVELOP = 'bc6547a'


def git(*args: str, timeout: float = 60) -> str:
    """Run git in the current repository and return its stripped stdout."""
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def extract(sha: str, dest: str, timeout: float) -> None:
    """Extract the commit's ``src/`` tree into `dest`."""
    os.makedirs(dest)
    archive = os.path.join(dest, 'src.tar')
    git('archive', '--format=tar', '-o', archive, sha, 'src', timeout=timeout)
    subprocess.run(['tar', '-xf', archive, '-C', dest], check=True, timeout=timeout)
    os.remove(archive)


def run_child(tree: str, site: str, n: int, timeout: float) -> dict:
    """One measurement in a fresh process group, killed after `timeout` seconds."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('HYPERSHELL_')}
    env.update(HYPERSHELL_CONFIG_FILE='', HYPERSHELL_SITE=site,
               HYPERSHELL_DATABASE_FILE=os.path.join(site, 'task.db'), HYPERSHELL_LOGGING_LEVEL='ERROR')
    proc = subprocess.Popen([sys.executable, '-I', os.path.abspath(__file__), '--child', tree, str(n)],
                            env=env, cwd=site, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise TimeoutError(f'child for {tree} exceeded {timeout}s')
    if proc.returncode != 0:
        raise RuntimeError(f'child for {tree} exited {proc.returncode}:\n{err[-2000:]}')
    return json.loads(out.strip().splitlines()[-1])


def child(tree: str, n: int) -> None:
    """Measure the writeback of `n` results with ``hypershell`` imported from `tree`."""
    sys.path.insert(0, os.path.join(tree, 'src'))
    import gc
    import hypershell
    if not os.path.realpath(hypershell.__file__).startswith(os.path.realpath(tree)):
        raise SystemExit(f'imported {hypershell.__file__}, not the tree under {tree}')
    from datetime import datetime, timedelta
    import sqlalchemy
    from sqlalchemy import event, func, select
    from sqlalchemy.engine import Engine
    from hypershell.core.uuid import uuid
    from hypershell.data import initdb
    from hypershell.data.core import Session
    from hypershell.data.model import Task, serialize_tasks, deserialize_tasks
    from hypershell.server import Receiver

    initdb()
    Task.add_all([Task.new(args=f'echo {i}') for i in range(n)])
    results = []
    while batch := Task.next(limit=500):
        for copy in deserialize_tasks(serialize_tasks(batch)):
            start = datetime.now().astimezone()
            copy.client_id, copy.client_host, copy.command = uuid(), 'node', copy.args
            copy.start_time, copy.completion_time = start, start + timedelta(milliseconds=5)
            copy.waited, copy.duration = 0, 5
            copy.exit_status = 3 if len(results) % 100 == 99 else 0
            results.append(copy)
    if len(results) != n:
        raise SystemExit(f'dispatched {len(results)} of {n} tasks')
    bundles = [deserialize_tasks(serialize_tasks([copy])) for copy in results]
    Session.remove()

    statements = []
    event.listen(Engine, 'before_cursor_execute',
                 lambda conn, cursor, sql, params, context, many: statements.append(sql.split(None, 1)[0].upper()))
    receiver = Receiver(queue=None)
    gc.collect()
    wall, cpu = time.perf_counter(), time.process_time()
    for bundle in bundles:
        receiver.tasks = bundle
        receiver.update_tasks()
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall

    done = Session.execute(select(func.count()).select_from(Task).where(Task.completion_time.isnot(None))).scalar()
    failed = Session.execute(select(func.count()).select_from(Task).where(Task.exit_status != 0)).scalar()
    if done != n or failed != n // 100:
        raise SystemExit(f'expected {n} completed and {n // 100} failed rows, found {done} and {failed}')
    print(json.dumps({
        'n': n, 'cpu_ms_per_result': round(1e3 * cpu / n, 4), 'wall_ms_per_result': round(1e3 * wall / n, 4),
        'updates_per_result': round(statements.count('UPDATE') / n, 3),
        'statements_per_result': round(len(statements) / n, 3),
        'python': platform.python_version(), 'sqlalchemy': sqlalchemy.__version__,
    }))


def summarize(builds: list, runs: list) -> dict:
    """Mean, median and spread of CPU per result for each build, and the bar."""
    summary = {}
    for label, sha in builds:
        values = [run['cpu_ms_per_result'] for run in runs if run['label'] == label]
        summary[label] = {'sha': sha, 'runs': len(values), 'mean': round(statistics.mean(values), 4),
                          'median': round(statistics.median(values), 4),
                          'min': min(values), 'max': max(values), 'values': values}
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--n', type=int, default=5000, help='results per measurement (default 5000)')
    parser.add_argument('--rounds', type=int, default=6, help='recorded interleaved rounds (default 6)')
    parser.add_argument('--fix', default='HEAD', help='the fix commit (default HEAD; src must be committed)')
    parser.add_argument('--out', default=None, help='directory for runs.jsonl (default a new one under TMPDIR)')
    parser.add_argument('--timeout', type=float, default=300, help='kill timer per measurement, seconds')
    parser.add_argument('--keep', action='store_true', help='keep the extracted trees and databases')
    parser.add_argument('--child', nargs=2, metavar=('TREE', 'N'), help=argparse.SUPPRESS)
    opts = parser.parse_args()
    if opts.child:
        child(opts.child[0], int(opts.child[1]))
        return 0
    if opts.n < 5000 or opts.rounds < 3:
        parser.error('the evidence needs --n 5000 or more and --rounds 3 or more')

    if git('status', '--porcelain', '--', 'src'):
        print('cpu_bench: src/ has uncommitted changes; commit the fix first', file=sys.stderr)
        return 2
    builds = [('2.8', git('rev-parse', '--verify', f'{BASELINE}^{{commit}}')),
              ('develop', git('rev-parse', '--verify', f'{DEVELOP}^{{commit}}')),
              ('fix', git('rev-parse', '--verify', f'{opts.fix}^{{commit}}'))]
    out = opts.out or tempfile.mkdtemp(prefix='wb-cpu-bench.', dir=os.environ.get('TMPDIR'))
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix='work.', dir=out)
    runs = []
    try:
        for label, sha in builds:
            extract(sha, os.path.join(work, label), timeout=opts.timeout)
        with open(os.path.join(out, 'runs.jsonl'), 'a') as log:
            for round_ in range(opts.rounds + 1):  # Round 0 is the unrecorded warm-up.
                order = builds[round_ % 3:] + builds[:round_ % 3]
                for label, sha in order:
                    site = tempfile.mkdtemp(prefix=f'site.{label}.', dir=work)
                    run = run_child(os.path.join(work, label), site, opts.n, opts.timeout)
                    run.update(label=label, sha=sha, round=round_, warmup=round_ == 0)
                    print(json.dumps(run), file=log, flush=True)
                    print(f'cpu_bench: round {round_} {label:<7} {run["cpu_ms_per_result"]:.4f} ms/result',
                          file=sys.stderr, flush=True)
                    if round_ > 0:
                        runs.append(run)
                    shutil.rmtree(site, ignore_errors=True)
    except TimeoutError as error:
        print(f'cpu_bench: {error}', file=sys.stderr)
        return 124
    except (RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f'cpu_bench: {error}', file=sys.stderr)
        return 2
    finally:
        if not opts.keep:
            shutil.rmtree(work, ignore_errors=True)

    summary = summarize(builds, runs)
    bar = summary['fix']['mean'] <= summary['2.8']['mean']
    host = {'machine': platform.machine(), 'system': platform.system(), 'python': runs[0]['python'],
            'sqlalchemy': runs[0]['sqlalchemy'], 'n': opts.n, 'rounds': opts.rounds, 'cpus': os.cpu_count()}
    print(json.dumps({'host': host, 'summary': summary, 'bar_met': bar, 'runs_jsonl': os.path.join(out, 'runs.jsonl')},
                     indent=2))
    print('\n| build | commit | runs | mean ms | median ms | min | max |\n|---|---|---|---|---|---|---|')
    for label, stats in summary.items():
        print(f'| {label} | `{stats["sha"][:7]}` | {stats["runs"]} | {stats["mean"]:.4f} | {stats["median"]:.4f} '
              f'| {stats["min"]:.4f} | {stats["max"]:.4f} |')
    print(f'\nBar (fix mean at or below 2.8 mean): {"met" if bar else "MISSED"}')
    return 0 if bar else 1


if __name__ == '__main__':
    sys.exit(main())
