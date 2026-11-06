# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Local throughput check: one relay against a direct `hs cluster` with the same executors.

Usage:
    python relay_throughput.py [--tasks 2000] [--sleep 0.2] [--clients 4] [--threads 8] [--ratio 1.5]

Both runs execute the same task file (TASKS tasks of `sleep SLEEP`) on CLIENTS x THREADS executors on
this machine, with every setting at its default:

- direct: `hs cluster FILE -N CLIENTS*THREADS` (one in-process client);
- relay: `hs submit FILE`, then `hs server --restart` and `hs relay -N CLIENTS` with `[relay]
  num_threads = THREADS`, its site clients launched through a wrapper around `env` that dials the
  relay on loopback (a host's own name need not resolve to an address it accepts).

Each wall time runs from the start of the run to the exit of the process that finishes it (the
cluster, or the upstream server). It prints both, their ratio, and PASS when the relay is within
RATIO of the direct time (exit 0), FAIL otherwise (exit 1). `HS` names the `hs` to run (default: the
one on PATH). Scratch sites go under TMPDIR and are kept (their paths are printed).
"""


# Standard libs
import os
import sys
import time
import socket
import secrets
import sqlite3
import argparse
import tempfile
import subprocess
from pathlib import Path


LAUNCHER = """#!/bin/sh
previous=
for arg; do
    shift
    if [ "$previous" = "-H" ]; then set -- "$@" 127.0.0.1; else set -- "$@" "$arg"; fi
    previous=$arg
done
exec env "$@"
"""

HS = os.environ.get('HS', 'hs')


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def prepare(name: str, args: argparse.Namespace) -> tuple:
    """A scratch site, its environment (only its own settings) and the task file."""
    site = Path(tempfile.mkdtemp(prefix=f'relay-throughput-{name}-'))
    env = {k: v for k, v in os.environ.items() if not k.startswith('HYPERSHELL_')}
    config = site / 'config.toml'
    config.write_text(f'[relay]\nnum_threads = {args.threads}\n')
    env.update(HYPERSHELL_SITE=str(site), HYPERSHELL_DATABASE_FILE=str(site / 'task.db'),
               HYPERSHELL_CONFIG_FILE=str(config), HYPERSHELL_LOGGING_LEVEL='INFO')
    tasks = site / 'tasks.txt'
    tasks.write_text(''.join(f'sleep {args.sleep}\n' for _ in range(args.tasks)))
    return site, env, tasks


def start(site: Path, env: dict, name: str, *argv: str, **extra: str) -> subprocess.Popen:
    with open(site / f'{name}.log', 'w') as stderr, open(site / f'{name}.out', 'w') as stdout:
        return subprocess.Popen([HS, *argv], cwd=site, env={**env, **extra}, stdout=stdout, stderr=stderr,
                                start_new_session=True)


def completed(site: Path) -> int:
    with sqlite3.connect(site / 'task.db') as db:
        return db.execute('SELECT count(*) FROM task WHERE exit_status = 0').fetchone()[0]


def direct(args: argparse.Namespace) -> tuple:
    site, env, tasks = prepare('direct', args)
    begin = time.monotonic()
    proc = start(site, env, 'cluster', 'cluster', str(tasks), '-N', str(args.clients * args.threads))
    status = proc.wait(timeout=args.timeout)
    return time.monotonic() - begin, status, completed(site), site


def relay(args: argparse.Namespace) -> tuple:
    site, env, tasks = prepare('relay', args)
    launcher = site / 'launcher'
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    subprocess.run([HS, 'submit', str(tasks)], cwd=site, env=env, check=True, capture_output=True)
    port, key = free_port(), secrets.token_hex(16)
    begin = time.monotonic()
    server = start(site, env, 'server', 'server', '--restart', '-H', 'localhost', '-p', str(port), '-k', key)
    relay_proc = start(site, env, 'relay', 'relay', '-H', 'localhost', '-p', str(port), '--bind', '127.0.0.1',
                       '--port', str(free_port()), '-N', str(args.clients), '--launcher', str(launcher),
                       HYPERSHELL_RELAY_UPSTREAM_AUTH=key, HYPERSHELL_RELAY_DATABASE=str(site / 'relay.db'))
    try:
        status = server.wait(timeout=args.timeout)
        wall = time.monotonic() - begin
        status = status or relay_proc.wait(timeout=60)
    finally:
        for proc in (server, relay_proc):
            if proc.poll() is None:
                os.killpg(proc.pid, 9)
    return wall, status, completed(site), site


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--tasks', type=int, default=2000)
    parser.add_argument('--sleep', default='0.2')
    parser.add_argument('--clients', type=int, default=4)
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--ratio', type=float, default=1.5)
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    ideal = args.tasks * float(args.sleep) / (args.clients * args.threads)
    results = {}
    for name, run in (('direct', direct), ('relay', relay)):
        wall, status, done, site = run(args)
        results[name] = wall
        print(f'{name}: {wall:.1f} s, exit {status}, {done} of {args.tasks} succeeded ({site})')
        if status != 0 or done != args.tasks:
            print('FAIL')
            return 1
    ratio = results['relay'] / results['direct']
    verdict = 'PASS' if ratio <= args.ratio else 'FAIL'
    print(f'ideal {ideal:.1f} s; relay/direct {ratio:.2f} (limit {args.ratio}): {verdict}')
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
