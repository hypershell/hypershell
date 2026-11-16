"""Prototype integration tests: `hs cluster` with a second connection holding BEGIN EXCLUSIVE on the site database.

The launched client is gated: it waits (bounded) for a flag the lock holder touches once it holds the lock, then
exits 1, so the first failed exit and every read after it happen under the lock, whatever the machine's speed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from subprocess import Popen, TimeoutExpired, DEVNULL
from typing import Dict, List, Optional, Tuple

import pytest
from pytest import mark
from cmdkit.app import exit_status

from tests.conftest import free_port
from tests.test_cluster_exit import kill_group

from locking import LockHolder

HS = os.path.join(os.path.dirname(sys.executable), 'hs')
ADDRESS = '127.0.0.1'
TASKS = 5
STANDIN_SITE = str(Path(__file__).parent / 'standin_site')


def gated_launcher(flag: Path) -> str:
    """A launcher that waits up to 30 s for `flag`, then exits 1 (its client arguments are ignored)."""
    return f"sh -c 'n=0; while [ ! -e {flag} ] && [ $n -lt 300 ]; do sleep 0.1; n=$((n+1)); done; exit 1' sh"


def start_cluster(site: Path, args: List[str], env: Dict[str, str]) -> Tuple[Popen, Path, float]:
    (site / 'tasks.txt').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    log_path = site / f'cluster-{time.monotonic_ns()}.log'
    environ = {**os.environ, 'HYPERSHELL_LOGGING_LEVEL': 'DEBUG', **env}
    if os.environ.get('CLF_STANDIN') == '1':
        environ['PYTHONPATH'] = os.pathsep.join([STANDIN_SITE, environ.get('PYTHONPATH', '')])
    with open(log_path, mode='w') as log:
        started = time.monotonic()
        proc = Popen([HS, 'cluster', 'tasks.txt', '--no-tls', '-H', ADDRESS, '-p', str(free_port()), *args],
                     cwd=site, env=environ, stdin=DEVNULL, stdout=DEVNULL, stderr=log, start_new_session=True)
    return proc, log_path, started


def wait_for(proc: Popen, timeout: float) -> Optional[float]:
    """Seconds monotonic at exit, or None if `proc` is still running after `timeout`."""
    try:
        proc.wait(timeout=timeout)
        return time.monotonic()
    except TimeoutExpired:
        return None


def report(holder, started, ended, proc) -> None:
    rel = lambda t: None if t is None else round(t - started, 1)
    print(f'TIMES locked={rel(holder.times["locked_at"])} released={rel(holder.times["released_at"])} '
          f'ended={rel(ended)} rc={proc.returncode}')


def tracebacks(site: Path) -> List[str]:
    return [str(p) for p in site.rglob('exception-*.log')]


def locked_warnings(text: str) -> List[str]:
    return [line for line in text.splitlines() if 'WARNING' in line and 'locked' in line.lower()]


SYNC = 'Launching clients'
AUTOSCALE_SYNC = 'Autoscale adding client'
"""The cluster's DEBUG line once its server is up; with staged tasks, the scheduler then reads nothing until a client."""

AUTOSCALE_HOLD = 8.5
"""Seconds the lock is held: a 1 s period's check starts within ~2 s of the flag and fails after 5 s busy wait."""


@mark.integration
def test_autoscaling_cluster_with_a_locked_database_reaches_the_failed_launch_stop(temp_site: Path) -> None:
    """R1+R2: lock held over a check; no traceback, a 'locked' WARNING, then the failed-launch stop and exit 5."""
    site = Path(temp_site)
    flag = site / 'locked.flag'
    proc, log_path, started = start_cluster(site, ['--autoscaling', 'fixed', '-P', '1s',
                                                   '--launcher', gated_launcher(flag)], env={})
    holder = LockHolder(Path(os.environ['HYPERSHELL_DATABASE_FILE']), TASKS, AUTOSCALE_HOLD, flag=flag,
                        log_path=log_path, sync=AUTOSCALE_SYNC)
    holder.start()
    try:
        ended = wait_for(proc, timeout=45)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    report(holder, started, ended, proc)
    assert holder.times['error'] is None, holder.times
    assert ended is not None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert proc.returncode == exit_status.runtime_error, text[-4000:]
    assert '3 launched clients in a row exited non-zero' in text, text[-4000:]
    assert locked_warnings(text), text[-4000:]
    assert ended > holder.times['released_at']


@mark.integration
def test_autoscaling_cluster_with_the_limit_off_keeps_relaunching_after_a_lock(temp_site: Path) -> None:
    """R1 with autoscale.failed_launches=0: still running 6 s after the release, relaunching, no traceback."""
    site = Path(temp_site)
    flag = site / 'locked.flag'
    proc, log_path, started = start_cluster(site, ['--autoscaling', 'fixed', '-P', '1s',
                                                   '--launcher', gated_launcher(flag)],
                                            env={'HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES': '0'})
    holder = LockHolder(Path(os.environ['HYPERSHELL_DATABASE_FILE']), TASKS, AUTOSCALE_HOLD, flag=flag,
                        log_path=log_path, sync=AUTOSCALE_SYNC)
    holder.start()
    try:
        holder.join(timeout=40)
        ended = wait_for(proc, timeout=6)
        alive = proc.poll() is None
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    report(holder, started, ended, proc)
    assert holder.times['error'] is None, holder.times
    assert alive, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert locked_warnings(text), text[-4000:]
    exits = [line for line in text.splitlines() if 'exited with status 1' in line]
    assert len(exits) >= 3, text[-4000:]


LAUNCHER_HOLD = 7.5
"""Seconds the lock is held: the watch sees the exit within ~1 s of the flag and its read fails after 5 s."""


@mark.integration
def test_launcher_cluster_with_a_locked_database_stops_with_the_r1_error(temp_site: Path) -> None:
    """R3: every client gone with one failure, the tasks-remain read locked: WARNING, then R1's error, exit 5 in 10 s."""
    site = Path(temp_site)
    flag = site / 'locked.flag'
    proc, log_path, started = start_cluster(site, ['--launcher', gated_launcher(flag)], env={})
    holder = LockHolder(Path(os.environ['HYPERSHELL_DATABASE_FILE']), TASKS, LAUNCHER_HOLD, flag=flag,
                        log_path=log_path, sync=SYNC)
    holder.start()
    try:
        ended = wait_for(proc, timeout=40)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    report(holder, started, ended, proc)
    assert holder.times['error'] is None, holder.times
    assert ended is not None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert proc.returncode == exit_status.runtime_error, text[-4000:]
    assert 'every launched client has exited with tasks remaining' in text, text[-4000:]
    assert locked_warnings(text), text[-4000:]
    assert ended - holder.times['released_at'] <= 10
