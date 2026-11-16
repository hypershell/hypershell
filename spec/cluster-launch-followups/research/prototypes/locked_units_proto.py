"""Prototype unit tests: the autoscaler's check and the every-client-exited watch with a real held SQLite lock.

R1/R2 (AutoScaler.check + check_fixed), R3 (watch_launched -> work_remains), R4 guard. Each locked case
holds BEGIN EXCLUSIVE from a second raw connection on the very file the global session is bound to; the
session's engine waits 0.5 s on the lock, so a locked read fails in 0.5 s, not 5 s.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest
from pytest import mark
from sqlalchemy.exc import OperationalError

from hypershell.data.model import Task
from hypershell.cluster import remote, watch
from hypershell.cluster.remote import AutoScalerState
from hypershell.cluster.watch import ClusterLaunchError, watch_launched
from tests.test_autoscale_failed_launches import failing_scaler
from tests.test_zone_autoscale import run_checks
from tests.test_cluster_exit import StandInServer, sh

from locking import locked_db, exclusive

JOIN_BOUND = 15.0


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / 'tasks.db'
    with locked_db(path):
        Task.add_all([Task.new(args=f'echo {n}') for n in range(4)])
        yield path


def records(caplog, level: int) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def one_check(scaler) -> AutoScalerState:
    """One check as the machine runs it: CHECK, then CHECK_FIXED's zone sizing unless the check ended it."""
    state = scaler.check()
    if state in (AutoScalerState.CHECK_FIXED, AutoScalerState.CHECK_DYNAMIC):
        state = scaler.actions[state]()
    return state


@mark.unit
@mark.parametrize('failed_launches', [3, 0])
def test_locked_check_is_skipped_with_one_warning(db: Path, caplog, failed_launches: int) -> None:
    """R1: a check under a held lock neither raises nor halts; one WARNING says the database was locked."""
    scaler, started = failing_scaler(failed_launches=failed_launches, registrations=remote.client_registrations)
    assert started[0].poll() == 1  # The first launch has already failed
    with caplog.at_level(logging.WARNING, logger='hypershell'):
        with exclusive(db):
            state = one_check(scaler)
    assert state is not AutoScalerState.FINAL and state is not AutoScalerState.HALT
    locked = [m for m in records(caplog, logging.WARNING) if 'locked' in m.lower()]
    assert len(locked) == 1, records(caplog, logging.WARNING)
    assert not records(caplog, logging.CRITICAL)


@mark.unit
def test_exits_during_a_skipped_check_still_count(db: Path) -> None:
    """R2: the launch that exited while its check was skipped is the first of the three that stop the machine."""
    scaler, started = failing_scaler(failed_launches=3, registrations=remote.client_registrations)
    with exclusive(db):
        one_check(scaler)
    states = run_checks(scaler, 8)
    assert states[-1] is AutoScalerState.FINAL, states
    assert scaler.exhausted and len(scaler.failures) == 3
    assert scaler.failures[0][0] == started[0].pid


@mark.unit
def test_limit_off_relaunches_after_a_skipped_check(db: Path) -> None:
    """R1/R2 with the limit at 0: after the lock the next check scales again; nothing ends."""
    scaler, started = failing_scaler(failed_launches=0, registrations=remote.client_registrations)
    with exclusive(db):
        one_check(scaler)
    states = run_checks(scaler, 4)
    assert AutoScalerState.FINAL not in states and AutoScalerState.SCALE in states
    assert not scaler.exhausted and len(started) >= 3


@mark.unit
def test_other_operational_errors_still_end_the_check(db: Path) -> None:
    """R4 guard: an OperationalError that is not a lock still propagates (passes on the base too)."""
    def broken():
        raise OperationalError('SELECT 1', {}, sqlite3.OperationalError('disk I/O error'))
    scaler, _ = failing_scaler(failed_launches=3, registrations=broken)
    with pytest.raises(OperationalError, match='disk I/O error'):
        one_check(scaler)


class ScheduledStandIn(StandInServer):
    """A stand-in server whose scheduler runs (so work is read from the database) and whose receiver has ended."""

    class _Alive:
        def __init__(self, alive: bool) -> None:
            self.alive = alive
            self.machine = type('M', (), {'current_group': None, 'attempts': 1, 'eager': False})()

        def is_alive(self) -> bool:
            return self.alive

    def __init__(self) -> None:
        super().__init__()
        self.scheduler = self._Alive(True)
        self.receiver = self._Alive(False)
        self.submitter = None


@mark.unit
def test_watch_reads_again_after_a_locked_read(db: Path, caplog) -> None:
    """R3: every client gone, one failed, the tasks-remain read locked: WARNING, read again, R1 error after release."""
    server = ScheduledStandIn()
    outcome: Dict[str, Any] = {'error': None, 'at': None}

    def target() -> None:
        try:
            watch_launched(server, [('node-a', sh('exit 1'))], ('127.0.0.1', 50123), interval=0.1)
        except BaseException as error:  # anything, so the base's OperationalError is seen, not lost
            outcome['error'] = error
        outcome['at'] = time.monotonic()

    with caplog.at_level(logging.WARNING, logger='hypershell'):
        with exclusive(db):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            time.sleep(1.5)  # At least one read meets the lock (0.5 s busy wait)
        released = time.monotonic()
        thread.join(timeout=JOIN_BOUND)
    assert not thread.is_alive(), 'watch did not end'
    server.ended.set()
    assert isinstance(outcome['error'], ClusterLaunchError), repr(outcome['error'])
    assert server.stopped
    assert outcome['at'] - released <= 10
    assert any('locked' in m.lower() for m in records(caplog, logging.WARNING)), records(caplog, logging.WARNING)
    assert not records(caplog, logging.CRITICAL)


@mark.unit
def test_probe_session_after_a_locked_read(db: Path) -> None:
    """PROBE (not for the suite): does the scoped session read again after a locked read, without a rollback?"""
    with exclusive(db):
        with pytest.raises(OperationalError, match='database is locked'):
            Task.count_remaining()
    assert Task.count_remaining() == 4
