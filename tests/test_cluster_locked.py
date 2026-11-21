# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
A locked SQLite database delays a cluster's own decision; it never ends the run.

The autoscaler's check and the every-client-exited watch read the database. When a read fails
because another connection holds the database locked, the check is skipped with one warning and
decided again at the next pass; launched clients that exited meanwhile still count. Any other
database error ends the run as before.

Each locked case holds a real `BEGIN EXCLUSIVE` from a second, raw connection on the test's own
database file. In-process tests bind the session to an engine of their own whose connections give
up after half a second (the product's busy wait is untouched); command-line tests keep the
product's wait. Every subprocess, thread and lock holder here is bounded.

The new names are reached through their module, so each test here fails on its own (not the whole
module at import) on a tree without them.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Tuple, Type, Final

# Standard libs
import os
import re
import sys
import time
import logging
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
from subprocess import Popen, TimeoutExpired, DEVNULL

# External libs
import pytest
from pytest import mark, MonkeyPatch
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, IntegrityError
from cmdkit.app import exit_status

# Internal libs
from tests.conftest import free_port
from tests.test_autoscale_failed_launches import ScriptedLaunch
from tests.test_zone_autoscale import FakeProc, run_checks
from tests.test_cluster_exit import StandInServer, kill_group, sh
from hypershell.data import core as data_core
from hypershell.data.core import Session
from hypershell.data.model import Entity, Task
from hypershell.cluster import remote, watch
from hypershell.cluster.remote import AutoScaler, AutoScalerState
from hypershell.cluster.watch import ClusterLaunchError, watch_launched


BUSY_TIMEOUT: Final[float] = 0.5
"""Seconds an in-process test's connections wait on a lock before `database is locked`."""

JOIN_BOUND: Final[float] = 15
"""Seconds within which a watch run in a thread must have ended."""

ADDRESS: Final[str] = '127.0.0.1'
"""Bind address of every cluster here, so nothing depends on resolving this host's name."""

HS: Final[str] = os.path.join(os.path.dirname(sys.executable), 'hs')
"""This tree's `hs`."""

TASKS: Final[int] = 5
"""Tasks in each command-line cluster's input."""

LOCKED: Final[str] = 'database was locked'
"""The phrase every warning about a skipped check or read carries."""


# --- A locked database ---------------------------------------------------------------------------------------


@contextmanager
def locked_db(path: Path, busy_timeout: float = BUSY_TIMEOUT) -> Iterator[Engine]:
    """Bind the global session to a fresh SQLite file at `path` whose reads give up after `busy_timeout`."""
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False, 'timeout': busy_timeout})
    Entity.metadata.create_all(engine)
    Session.remove()
    Session.configure(bind=engine)
    try:
        yield engine
    finally:
        Session.remove()
        Session.configure(bind=data_core.engine)
        engine.dispose()


@contextmanager
def exclusive(path: Path) -> Iterator[sqlite3.Connection]:
    """Hold `BEGIN EXCLUSIVE` on `path` from a second, raw connection for the body of the block."""
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.execute('BEGIN EXCLUSIVE')
    try:
        yield conn
    finally:
        conn.execute('COMMIT')
        conn.close()


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    """A per-test database of four tasks bound to the global session; its path."""
    path = tmp_path / 'tasks.db'
    with locked_db(path):
        Task.add_all([Task.new(args=f'echo {n}') for n in range(4)])
        yield path


def sqlite_error(message: str, code: Optional[int]) -> sqlite3.OperationalError:
    """A driver error with `message`, carrying `code` as SQLite's own errors do (none if None)."""
    error = sqlite3.OperationalError(message)
    if code is not None:
        error.sqlite_errorcode = code
    return error


def wrapped(orig: BaseException) -> OperationalError:
    """The error SQLAlchemy raises for a statement that failed with `orig`."""
    return OperationalError('SELECT 1', {}, orig)


def disk_error() -> OperationalError:
    """A database error that is not a lock."""
    return wrapped(sqlite_error('disk I/O error', sqlite3.SQLITE_IOERR))


def messages(caplog: pytest.LogCaptureFixture, level: int) -> List[str]:
    """Captured messages at exactly `level`."""
    return [record.getMessage() for record in caplog.records if record.levelno == level]


# --- Telling a locked database apart -------------------------------------------------------------------------


@mark.unit
@mark.parametrize('error, locked', [
    pytest.param(wrapped(sqlite_error('database is locked', sqlite3.SQLITE_BUSY)), True, id='busy'),
    pytest.param(wrapped(sqlite_error('database table is locked', sqlite3.SQLITE_LOCKED)), True, id='table-locked'),
    pytest.param(wrapped(sqlite_error('database table is locked', 262)), True, id='table-locked-extended'),
    pytest.param(wrapped(sqlite_error('database is locked', None)), True, id='no-code'),
    pytest.param(disk_error(), False, id='disk-io'),
    pytest.param(wrapped(RuntimeError('database is locked')), False, id='not-sqlite'),
    pytest.param(IntegrityError('INSERT', {}, sqlite3.IntegrityError('database is locked')), False,
                 id='not-operational'),
])
def test_database_locked(error: BaseException, locked: bool) -> None:
    """Only SQLite saying the database or a table in it is locked counts, by its code or, without one, its message."""
    assert watch.database_locked(error) is locked


@mark.unit
def test_database_locked_by_a_held_lock(db: Path) -> None:
    """The error a read raises under a lock held by another connection counts as locked."""
    with exclusive(db):
        with pytest.raises(OperationalError, match='database is locked') as caught:
            Task.count_remaining()
    assert watch.database_locked(caught.value)


@mark.unit
def test_session_still_reads_after_a_locked_read(db: Path) -> None:
    """The session reads again once the lock is released, with no rollback in between."""
    with exclusive(db):
        with pytest.raises(OperationalError, match='database is locked'):
            Task.count_remaining()
    assert Task.count_remaining() == 4


# --- The autoscaler's check ------------------------------------------------------------------------------------


def scaler_of(policy: str = 'fixed', **options: Any) -> Tuple[AutoScaler, List[FakeProc]]:
    """A fleet of one in the default zone whose clients exit 1 at once, under `policy`, started up to steady state."""
    started: List[FakeProc] = []
    scaler = AutoScaler(ScriptedLaunch('default', started, []), policy=policy, period=3600, init_size=1,
                        min_size=0, max_size=1, **options)
    while scaler.init() is AutoScalerState.SCALE:
        scaler.scale()
    return scaler, started


def one_check(scaler: AutoScaler) -> AutoScalerState:
    """One check as the machine runs it: CHECK, then the policy's zone sizing unless the check ended it."""
    state = scaler.check()
    if state in (AutoScalerState.CHECK_FIXED, AutoScalerState.CHECK_DYNAMIC):
        state = scaler.actions[state]()
    return state


READS: Final[Dict[str, Dict[str, Any]]] = {
    # The client registrations behind the failed-launch stop.
    'registrations': {'failed_launches': 3, 'registrations': remote.client_registrations},
    # The task counts that size each zone, with the failed-launch count on.
    'sizing-at-limit': {'failed_launches': 3, 'registrations': lambda: (0, 0)},
    # The task counts that size each zone, with the failed-launch count off.
    'sizing-off': {'failed_launches': 0},
}


@mark.unit
@mark.parametrize('read', list(READS))
@mark.parametrize('policy', ['fixed', 'dynamic'])
def test_locked_check_is_skipped_with_one_warning(db: Path, caplog: pytest.LogCaptureFixture,
                                                  policy: str, read: str) -> None:
    """A check whose read meets a lock waits for the next one, with one warning and no stop."""
    scaler, started = scaler_of(policy, **READS[read])
    assert started[0].poll() == 1  # The first launch has already failed
    scaler.last_check = datetime.now() - timedelta(hours=1)
    with caplog.at_level(logging.WARNING, logger='hypershell'):
        with exclusive(db):
            state = one_check(scaler)
    assert state is AutoScalerState.WAIT
    assert not scaler.exhausted
    assert scaler.last_check > datetime.now() - timedelta(minutes=1)
    locked = [message for message in messages(caplog, logging.WARNING) if LOCKED in message]
    assert len(locked) == 1, messages(caplog, logging.WARNING)
    assert not messages(caplog, logging.CRITICAL)


@mark.unit
def test_exits_during_a_skipped_check_are_counted(db: Path) -> None:
    """The launch that exited while its check was skipped is the first of the three that stop the machine."""
    scaler, started = scaler_of(failed_launches=3, registrations=remote.client_registrations)
    with exclusive(db):
        assert one_check(scaler) is AutoScalerState.WAIT
    states = run_checks(scaler, 8)
    assert states[-1] is AutoScalerState.FINAL, states
    assert scaler.exhausted and len(scaler.failures) == 3
    assert scaler.failures[0][0] == started[0].pid


@mark.unit
def test_limit_off_relaunches_after_a_skipped_check(db: Path) -> None:
    """With the limit at 0, the check after a skipped one launches again, and nothing ends."""
    scaler, started = scaler_of(failed_launches=0)
    with exclusive(db):
        assert one_check(scaler) is AutoScalerState.WAIT
    states = run_checks(scaler, 4)
    assert AutoScalerState.FINAL not in states and AutoScalerState.SCALE in states
    assert not scaler.exhausted and len(started) >= 3


@mark.unit
@mark.parametrize('read', ['registrations', 'sizing'])
def test_other_operational_errors_still_end_the_check(db: Path, monkeypatch: MonkeyPatch, read: str) -> None:
    """A database error that is not a lock propagates from the check, as before."""
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise disk_error()
    if read == 'registrations':
        scaler, _ = scaler_of(failed_launches=3, registrations=broken)
    else:
        scaler, _ = scaler_of(failed_launches=0)
        monkeypatch.setattr(Task, 'current_group', broken)
    with pytest.raises(OperationalError, match='disk I/O error'):
        one_check(scaler)


# --- The every-client-exited watch -----------------------------------------------------------------------------


class ScheduledStandIn(StandInServer):
    """A stand-in server whose scheduler runs (so work is read from the database) and whose receiver has ended."""

    class Part:
        """A server thread that is running or not, with the scheduler's retry settings."""

        def __init__(self, alive: bool) -> None:
            self.alive = alive
            self.machine = type('Machine', (), {'current_group': None, 'attempts': 1, 'eager': False, 'held': []})()

        def is_alive(self) -> bool:
            return self.alive

    def __init__(self) -> None:
        super().__init__()
        self.scheduler = self.Part(True)
        self.receiver = self.Part(False)
        self.submitter = None


def watch_in_thread(server: StandInServer, launched: list) -> Tuple[threading.Thread, Dict[str, Any]]:
    """Run the watch in a thread; the returned dict gets whatever it raised and when it ended."""
    outcome: Dict[str, Any] = {'error': None, 'at': None}

    def target() -> None:
        try:
            watch_launched(server, launched, (ADDRESS, 50123), interval=0.1)
        except BaseException as error:  # Anything, so an unexpected error is seen, not lost
            outcome['error'] = error
        outcome['at'] = time.monotonic()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, outcome


@mark.unit
def test_watch_reads_again_after_a_locked_read(db: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Every client gone, one failed, and the read of remaining tasks locked: a warning, then the launch error."""
    server = ScheduledStandIn()
    with caplog.at_level(logging.WARNING, logger='hypershell'):
        with exclusive(db):
            thread, outcome = watch_in_thread(server, [('node-a', sh('exit 1'))])
            time.sleep(1.5)  # At least one read meets the lock
        released = time.monotonic()
        thread.join(timeout=JOIN_BOUND)
    server.ended.set()
    assert not thread.is_alive(), 'watch did not end'
    assert isinstance(outcome['error'], ClusterLaunchError), repr(outcome['error'])
    assert server.stopped
    assert outcome['at'] - released <= 10
    assert any(LOCKED in message for message in messages(caplog, logging.WARNING)), messages(caplog, logging.WARNING)
    assert not messages(caplog, logging.CRITICAL)


@mark.unit
def test_other_operational_errors_still_end_the_watch(monkeypatch: MonkeyPatch) -> None:
    """A database error that is not a lock propagates from the watch, as before."""
    def broken(*args: Any, **kwargs: Any) -> int:
        raise disk_error()
    monkeypatch.setattr(Task, 'count_remaining', broken)
    server = ScheduledStandIn()
    thread, outcome = watch_in_thread(server, [('node-a', sh('exit 1'))])
    thread.join(timeout=JOIN_BOUND)
    server.ended.set()
    assert not thread.is_alive(), 'watch did not end'
    assert isinstance(outcome['error'], OperationalError), repr(outcome['error'])
    assert 'disk I/O error' in str(outcome['error'])


# --- Command-line clusters with a lock held ------------------------------------------------------------------


def count_rows(path: Path, query: str) -> int:
    """The single number `query` reads through a short-lived connection; -1 while the file is missing or busy."""
    if not path.exists():
        return -1
    try:
        conn = sqlite3.connect(str(path), timeout=0.2)
        try:
            return conn.execute(query).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return -1


class LockHolder(threading.Thread):
    """
    Hold `BEGIN EXCLUSIVE` on `path` for `hold` seconds once the cluster has launched and staged a task.

    It waits (bounded) until `sync` is in the cluster's log at `log_path` and a task has been
    scheduled: with no client asking, the scheduler then waits on demand and reads nothing, so the
    lock meets only the cluster's own reads. It then waits `delay` seconds more, takes the lock and
    touches `flag`, the gated launcher's signal. The monotonic times it took and released the lock
    are kept in `times`, with any error. `abort` ends a wait early.
    """

    delay: float = 0.5
    """Seconds between the cluster being ready and the lock being taken."""

    condition: str = 'a scheduled task'
    """What `ready` waits for in the database, as the error says it."""

    def __init__(self, path: Path, hold: float, flag: Path, log_path: Path, sync: str,
                 ready_timeout: float = 30) -> None:
        super().__init__(daemon=True)
        self.path, self.hold, self.flag, self.log_path, self.sync = path, hold, flag, log_path, sync
        self.ready_timeout = ready_timeout
        self.abort = threading.Event()
        self.times: Dict[str, Any] = {'locked_at': None, 'released_at': None, 'error': None}

    def ready(self) -> bool:
        return (self.sync in self.log_path.read_text() and
                count_rows(self.path, 'SELECT count(*) FROM task WHERE schedule_time IS NOT NULL') > 0)

    def run(self) -> None:
        try:
            deadline = time.monotonic() + self.ready_timeout
            while not self.ready():
                if self.abort.is_set() or time.monotonic() > deadline:
                    self.times['error'] = f'never saw {self.sync!r} with {self.condition}'
                    return
                time.sleep(0.02)
            time.sleep(self.delay)
            with exclusive(self.path):
                self.times['locked_at'] = time.monotonic()
                self.flag.touch()
                self.abort.wait(self.hold)
            self.times['released_at'] = time.monotonic()
        except Exception as error:
            self.times['error'] = repr(error)


def gated_launcher(flag: Path) -> str:
    """A launcher that waits up to 30 s for `flag`, then exits 1 (its client arguments are ignored)."""
    return f"sh -c 'n=0; while [ ! -e {flag} ] && [ $n -lt 300 ]; do sleep 0.1; n=$((n+1)); done; exit 1' sh"


def start_cluster(site: Path, args: List[str], env: Dict[str, str] = None,
                  out_path: Optional[Path] = None) -> Tuple[Popen, Path]:
    """
    Start `hs cluster` on a task file of `TASKS` lines in `site`, in its own session; its log is returned.

    Its standard output goes to `out_path` if given, else nowhere.
    """
    (site / 'tasks.txt').write_text(''.join(f'echo {n}\n' for n in range(TASKS)))
    log_path = site / f'cluster-{time.monotonic_ns()}.log'
    with open(log_path, mode='w') as log, open(out_path or os.devnull, mode='w') as out:
        proc = Popen([HS, 'cluster', 'tasks.txt', '--no-tls', '-H', ADDRESS, '-p', str(free_port()), *args],
                     cwd=site, env={**os.environ, **(env or {})}, stdin=DEVNULL, stdout=out, stderr=log,
                     start_new_session=True)
    return proc, log_path


def wait_for(proc: Popen, timeout: float) -> Optional[float]:
    """The monotonic time `proc` was seen to exit, or None if it is still running after `timeout`."""
    try:
        proc.wait(timeout=timeout)
        return time.monotonic()
    except TimeoutExpired:
        return None


def tracebacks(site: Path) -> List[str]:
    """Exception traceback files written anywhere under `site`."""
    return [str(path) for path in site.rglob('exception-*.log')]


def locked_warnings(text: str) -> List[str]:
    """Log lines warning that the database was locked."""
    return [line for line in text.splitlines() if 'WARNING' in line and LOCKED in line]


AUTOSCALE_SYNC: Final[str] = 'Autoscale adding client'
"""The autoscaling cluster's log line once its server is up and its fleet checked."""

LAUNCH_SYNC: Final[str] = 'Launching clients'
"""The launcher-mode cluster's log line once its server is up."""

AUTOSCALE_HOLD: Final[float] = 8.5
"""Seconds the lock is held over an autoscaling cluster: a check starts within 2 s and waits 5 s on the lock."""

LAUNCH_HOLD: Final[float] = 7.5
"""Seconds the lock is held over a launcher-mode cluster: the watch sees the exit within 1 s and waits 5 s."""


IDLE_SYNC: Final[str] = r'backing off (1[6-9]|[2-9][0-9]) seconds'
"""The scheduler's trace line once its back-off has grown to 16 s or more, longer than any lock held here."""

IDLE_READY: Final[float] = 45
"""Seconds within which a working cluster must have finished its tasks and backed off that far."""


class IdleLockHolder(LockHolder):
    """
    A lock holder that takes the lock at once when the cluster is idle.

    Idle is every task complete and a line matching `sync` (a regular expression: the scheduler's
    back-off) in the cluster's log. The scheduler then sleeps through the hold, so the lock meets
    only the autoscaler's reads; the server's own threads meeting a locked database are outside what
    the cluster promises.
    """

    delay = 0
    condition = 'every task complete'

    def ready(self) -> bool:
        return (re.search(self.sync, self.log_path.read_text()) is not None and
                count_rows(self.path, 'SELECT count(*) > 0 AND count(completion_time) = count(*) FROM task') == 1)


def hold_over(site: Path, log_path: Path, hold: float, sync: str,
              kind: Type[LockHolder] = LockHolder, ready_timeout: float = 30) -> LockHolder:
    """Start a lock holder on the site's database, signalling the gated launcher through `site/locked.flag`."""
    holder = kind(Path(os.environ['HYPERSHELL_DATABASE_FILE']), hold, site / 'locked.flag', log_path, sync,
                  ready_timeout=ready_timeout)
    holder.start()
    return holder


@mark.integration
def test_autoscaling_cluster_with_a_locked_database_reaches_the_failed_launch_stop(temp_site: Path) -> None:
    """A lock held over a check: a warning, no traceback, then the failed-launch stop and exit 5 after the release."""
    site = Path(temp_site)
    proc, log_path = start_cluster(site, ['--autoscaling', 'fixed', '-P', '1s',
                                          '--launcher', gated_launcher(site / 'locked.flag')])
    holder = hold_over(site, log_path, AUTOSCALE_HOLD, AUTOSCALE_SYNC)
    try:
        ended = wait_for(proc, timeout=45)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    assert holder.times['error'] is None, holder.times
    assert ended is not None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert proc.returncode == exit_status.runtime_error, text[-4000:]
    assert '3 launched clients in a row exited non-zero' in text, text[-4000:]
    assert locked_warnings(text), text[-4000:]
    assert not [line for line in text.splitlines() if 'CRITICAL' in line and 'locked' in line], text[-4000:]
    assert ended > holder.times['released_at']


@mark.integration
def test_autoscaling_cluster_with_the_limit_off_keeps_relaunching_after_a_lock(temp_site: Path) -> None:
    """With `autoscale.failed_launches` at 0, the cluster is running 6 s after the release and relaunching."""
    site = Path(temp_site)
    proc, log_path = start_cluster(site, ['--autoscaling', 'fixed', '-P', '1s',
                                          '--launcher', gated_launcher(site / 'locked.flag')],
                                   env={'HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES': '0'})
    holder = hold_over(site, log_path, AUTOSCALE_HOLD, AUTOSCALE_SYNC)
    try:
        holder.join(timeout=45)
        ended = wait_for(proc, timeout=6)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    assert holder.times['error'] is None, holder.times
    assert holder.times['released_at'] is not None, holder.times
    assert ended is None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert locked_warnings(text), text[-4000:]
    exits = [line for line in text.splitlines() if 'exited with status 1' in line]
    assert len(exits) >= 3, text[-4000:]


@mark.integration
def test_launcher_cluster_with_a_locked_database_exits_with_the_launch_error(temp_site: Path) -> None:
    """Every client gone with one failure and the read of remaining tasks locked: the launch error within 10 s."""
    site = Path(temp_site)
    proc, log_path = start_cluster(site, ['--launcher', gated_launcher(site / 'locked.flag')])
    holder = hold_over(site, log_path, LAUNCH_HOLD, LAUNCH_SYNC)
    try:
        ended = wait_for(proc, timeout=40)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    assert holder.times['error'] is None, holder.times
    assert ended is not None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert proc.returncode == exit_status.runtime_error, text[-4000:]
    assert 'every launched client has exited with tasks remaining' in text, text[-4000:]
    assert locked_warnings(text), text[-4000:]
    assert ended - holder.times['released_at'] <= 10


@mark.integration
def test_autoscaling_cluster_with_working_clients_keeps_serving_after_a_lock(temp_site: Path) -> None:
    """A lock held once the work is done: every task complete with its output, one warning, and still serving."""
    site = Path(temp_site)
    out_path = site / 'cluster.out'
    proc, log_path = start_cluster(site, ['-Q', '60s', '-T', '1s', '--autoscaling', 'fixed', '-P', '1s',
                                          '--launcher', 'env', '--remote-exe', HS],
                                   env={'HYPERSHELL_LOGGING_LEVEL': 'TRACE'}, out_path=out_path)
    holder = hold_over(site, log_path, AUTOSCALE_HOLD, IDLE_SYNC, kind=IdleLockHolder, ready_timeout=IDLE_READY)
    try:
        holder.join(timeout=IDLE_READY + AUTOSCALE_HOLD + 5)
        ended = wait_for(proc, timeout=6)
    finally:
        holder.abort.set()
        holder.join(timeout=15)
        kill_group(proc)
    text = log_path.read_text()
    assert holder.times['error'] is None, holder.times
    assert holder.times['released_at'] is not None, holder.times
    assert ended is None, text[-4000:]
    assert not tracebacks(site), text[-4000:]
    assert not [line for line in text.splitlines() if 'CRITICAL' in line], text[-4000:]
    assert len(locked_warnings(text)) == 1, locked_warnings(text)
    assert sorted(out_path.read_text().split()) == [str(n) for n in range(TASKS)]
    done = 'SELECT count(*) FROM task WHERE completion_time IS NOT NULL AND exit_status = 0'
    assert count_rows(Path(os.environ['HYPERSHELL_DATABASE_FILE']), done) == TASKS
