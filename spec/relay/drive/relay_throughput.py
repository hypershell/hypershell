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
one on PATH); its directory goes first on PATH for every process started, so the clients the relay
launches (`hyper-shell client ...`) are that installation's too, as in `relay_drive.sh`. Scratch
sites go under TMPDIR and are kept (their paths are printed).

It never waits without end: a run fails, with a message naming its logs, when the relay's site has no
live client within LIVE_WAIT seconds (`--live-wait`), when the relay exits before the upstream server
does, or past TIMEOUT. Progress lines are printed as it goes.
"""


# Standard libs
import os
import sys
import time
import socket
import secrets
import shutil
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

PROGRESS = 30.0  # Seconds between progress lines while a run goes on.
POLL = 0.5  # Seconds between looks at a run.


class RunFailed(RuntimeError):
    """A run that cannot finish: the reason, for the report."""


def say(text: str) -> None:
    """One progress line, at once."""
    print(f'relay_throughput: {text}', flush=True)


def hs_bin() -> str:
    """The directory of the `hs` under test (resolved on PATH if `HS` is a bare name)."""
    found = HS if os.sep in HS else shutil.which(HS)
    if not found:
        raise RunFailed(f'{HS!r} not found on PATH (set HS to the hs of the installation under test)')
    return os.path.dirname(os.path.abspath(found))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def prepare(name: str, args: argparse.Namespace) -> tuple:
    """A scratch site, its environment (only its own settings) and the task file."""
    site = Path(tempfile.mkdtemp(prefix=f'relay-throughput-{name}-'))
    env = {k: v for k, v in os.environ.items() if not k.startswith('HYPERSHELL_')}
    env['PATH'] = os.pathsep.join([hs_bin(), env.get('PATH', os.defpath)])
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
    try:
        with sqlite3.connect(f'file:{site / "task.db"}?mode=ro', uri=True, timeout=5) as db:
            return db.execute('SELECT count(*) FROM task WHERE exit_status = 0').fetchone()[0]
    except sqlite3.Error:
        return 0


def live_clients(path: Path) -> int:
    """Clients connected to the server whose database is `path` (none if it cannot be read yet)."""
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as db:
            return db.execute('SELECT count(*) FROM client WHERE disconnected_at IS NULL').fetchone()[0]
    except sqlite3.Error:
        return 0


def watch(name: str, site: Path, proc: subprocess.Popen, args: argparse.Namespace, begin: float,
          live: Path, helper: subprocess.Popen = None) -> int:
    """Wait for `proc` to exit and return its status, failing the run if it cannot finish.

    It fails when no client is live in the database `live` within the live wait, when `helper` (the
    relay) exits first, or past the timeout; it prints progress meanwhile.
    """
    seen, report = False, begin + PROGRESS
    while (status := proc.poll()) is None:
        now = time.monotonic()
        if not seen:
            if live_clients(live):
                seen = True
                say(f'{name}: first client live after {now - begin:.1f} s')
            elif now - begin > args.live_wait:
                raise RunFailed(f'{name}: no live client after {args.live_wait:g} s (see the logs in {site})')
        if helper is not None and helper.poll() is not None:
            raise RunFailed(f'{name}: relay exited ({helper.returncode}) before the upstream server '
                            f'(see {site / "relay.log"})')
        if now - begin > args.timeout:
            raise RunFailed(f'{name}: not finished after {args.timeout:g} s (see the logs in {site})')
        if now >= report:
            report = now + PROGRESS
            say(f'{name}: {completed(site)} of {args.tasks} succeeded after {now - begin:.0f} s')
        time.sleep(POLL)
    return status


def direct(args: argparse.Namespace) -> tuple:
    site, env, tasks = prepare('direct', args)
    say(f'direct: starting ({site})')
    begin = time.monotonic()
    proc = start(site, env, 'cluster', 'cluster', str(tasks), '-N', str(args.clients * args.threads))
    try:
        status = watch('direct', site, proc, args, begin, live=site / 'task.db')
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, 9)
    return time.monotonic() - begin, status, completed(site), site


def relay(args: argparse.Namespace) -> tuple:
    site, env, tasks = prepare('relay', args)
    launcher = site / 'launcher'
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    subprocess.run([HS, 'submit', str(tasks)], cwd=site, env=env, check=True, capture_output=True)
    port, key = free_port(), secrets.token_hex(16)
    say(f'relay: starting ({site})')
    begin = time.monotonic()
    server = start(site, env, 'server', 'server', '--restart', '-H', 'localhost', '-p', str(port), '-k', key)
    relay_proc = start(site, env, 'relay', 'relay', '-H', 'localhost', '-p', str(port), '--bind', '127.0.0.1',
                       '--port', str(free_port()), '-N', str(args.clients), '--launcher', str(launcher),
                       HYPERSHELL_RELAY_UPSTREAM_AUTH=key, HYPERSHELL_RELAY_DATABASE=str(site / 'relay.db'))
    try:
        status = watch('relay', site, server, args, begin, live=site / 'relay.db', helper=relay_proc)
        wall = time.monotonic() - begin
        say(f'relay: upstream server exited ({status}) after {wall:.1f} s; waiting for the relay')
        try:
            status = status or relay_proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise RunFailed(f'relay: still running 60 s after the upstream server (see {site / "relay.log"})')
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
    parser.add_argument('--live-wait', type=float, default=120)
    args = parser.parse_args()
    ideal = args.tasks * float(args.sleep) / (args.clients * args.threads)
    results = {}
    for name, run in (('direct', direct), ('relay', relay)):
        try:
            wall, status, done, site = run(args)
        except RunFailed as failure:
            print(f'{failure}\nFAIL', flush=True)
            return 1
        results[name] = wall
        print(f'{name}: {wall:.1f} s, exit {status}, {done} of {args.tasks} succeeded ({site})', flush=True)
        if status != 0 or done != args.tasks:
            print('FAIL', flush=True)
            return 1
    ratio = results['relay'] / results['direct']
    verdict = 'PASS' if ratio <= args.ratio else 'FAIL'
    print(f'ideal {ideal:.1f} s; relay/direct {ratio:.2f} (limit {args.ratio}): {verdict}', flush=True)
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
