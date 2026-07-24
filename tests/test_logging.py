# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for file-based logging path resolution and slot claiming."""


# Type annotations
from __future__ import annotations

# Standard libs
import os
import sys
import errno
from pathlib import Path
from subprocess import Popen, PIPE

# External libs
from pytest import mark, skip, fixture
from cmdkit.app import exit_status

# Internal libs
from tests import main
import hypershell.core.logging as log
from hypershell.core.logging import (
    role_from_command, default_file_for, claim_file_slot, resolve_log_path,
    read_lock_record, HOSTNAME_SHORT, INSTANCE, _LOCKING,
)


@fixture(autouse=True)
def _clean_slot_locks():
    """Release and clear any held slot-lock handles after each test.

    Slot locks are process-global and held for the life of the owner; without this a
    claimed handle would leak across tests and skew later contention/liveness checks.
    """
    yield
    for handle in log._slot_locks:
        try:
            handle.close()
        except OSError:
            pass
    log._slot_locks.clear()


# A tiny program that claims a slot, announces it, and holds the lock while it sleeps.
# Used to prove cross-process contention and crash-driven slot reclamation.
_HOLDER = (
    'import sys, time;'
    'from hypershell.core.logging import claim_file_slot;'
    'print(claim_file_slot(sys.argv[1]), flush=True);'
    'time.sleep(60)'
)


@mark.unit
@mark.parametrize('command,role', [
    ('server', 'server'),
    ('cluster', 'cluster'),
    ('client', 'client'),
    ('submit', 'submit'),
    ('list', 'main'),
    ('info', 'main'),
    ('config', 'main'),
    ('initdb', 'main'),
    ('-h', 'main'),
    (None, 'main'),
])
def test_role_from_command(command: str, role: str) -> None:
    """Only distributed commands get their own role; everything else is 'main'."""
    assert role_from_command(command) == role


@mark.unit
def test_default_file_is_host_scoped_except_main() -> None:
    """Distributed roles embed the host in the filename; 'main' does not."""
    assert os.path.basename(default_file_for('main')) == 'main.log'
    assert os.path.basename(default_file_for('server')) == f'server-{HOSTNAME_SHORT}.log'
    assert os.path.basename(default_file_for('client')) == f'client-{HOSTNAME_SHORT}.log'
    assert os.path.basename(default_file_for('submit')) == f'submit-{HOSTNAME_SHORT}.log'


@mark.unit
def test_resolve_log_path_decorates_only_explicit_client_paths(tmp_path: Path) -> None:
    """An explicit client path is host-scoped; the (already host-scoped) default is not."""
    if not _LOCKING:
        skip('requires advisory file locking')
    explicit = resolve_log_path(str(tmp_path / 'shared.log'), role='client', is_default=False)
    assert os.path.basename(explicit) == f'shared-client-{HOSTNAME_SHORT}.log'
    default = resolve_log_path(str(tmp_path / f'client-{HOSTNAME_SHORT}.log'), role='client', is_default=True)
    assert os.path.basename(default) == f'client-{HOSTNAME_SHORT}.log'
    # Non-client roles are never decorated, explicit or not.
    server = resolve_log_path(str(tmp_path / 'shared.log'), role='server', is_default=False)
    assert os.path.basename(server) == 'shared.log'


@mark.unit
def test_claim_file_slot_disambiguates_within_process(tmp_path: Path) -> None:
    """A second live claim on the same path falls through to the next slot."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'app.log')
    assert os.path.basename(claim_file_slot(base)) == 'app.log'
    assert os.path.basename(claim_file_slot(base)) == 'app-2.log'
    assert os.path.basename(claim_file_slot(base)) == 'app-3.log'


@mark.integration
def test_slot_reclaimed_after_owner_dies(tmp_path: Path) -> None:
    """A crashed owner's slot is immediately reclaimable (OS releases the lock)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    holder = Popen([sys.executable, '-c', _HOLDER, base], stdout=PIPE, env=os.environ)
    try:
        held = holder.stdout.readline().decode().strip()
        assert os.path.basename(held) == 'client.log'
        # While the owner lives, we are pushed to the next slot.
        assert os.path.basename(claim_file_slot(base)) == 'client-2.log'
    finally:
        holder.terminate()
        holder.wait()
    # Once the owner dies, slot 1 is free again.
    assert os.path.basename(claim_file_slot(base)) == 'client.log'


@mark.integration
def test_file_logging_writes_role_named_file(temp_site: Path) -> None:
    """With file logging enabled, a command writes to its role/host-scoped file."""
    os.environ['HYPERSHELL_LOGGING_FILE'] = 'enabled'
    try:
        rc, _, _ = main(['hs', 'initdb', '--yes'])
        assert rc == exit_status.success
        matches = list(Path(temp_site).rglob('main.log'))
        assert matches, f'expected main.log somewhere under {temp_site}'
    finally:
        os.environ.pop('HYPERSHELL_LOGGING_FILE', None)


def _raise_errno(code: int):
    """Return a fake `flock` that always raises `OSError(code, ...)`."""
    def _flock(_fd, _flags):
        raise OSError(code, os.strerror(code))
    return _flock


@mark.unit
@mark.parametrize('bad_errno', [errno.ENOSYS, errno.ENOLCK, errno.EPERM])
def test_unsupported_lock_errno_degrades_to_canonical(tmp_path: Path, monkeypatch, bad_errno: int) -> None:
    """A non-contention lock error (lockless FS) appends to the canonical path, never a PID file."""
    if not _LOCKING:
        skip('requires advisory file locking')
    monkeypatch.setattr(log.fcntl, 'flock', _raise_errno(bad_errno))
    base = str(tmp_path / 'client.log')
    claimed = claim_file_slot(base)
    assert claimed == base  # canonical path, not '<root>-<pid>.log'
    assert not log._slot_locks  # nothing held: we degraded rather than acquired


@mark.unit
def test_no_locking_support_uses_canonical(tmp_path: Path, monkeypatch) -> None:
    """With no advisory-locking support at all, the canonical path is reused (not per-PID)."""
    monkeypatch.setattr(log, '_LOCKING', False)
    base = str(tmp_path / 'client.log')
    assert claim_file_slot(base) == base


@mark.unit
def test_contention_advances_to_next_slot(tmp_path: Path, monkeypatch) -> None:
    """Genuine contention (EAGAIN) on slot 1 advances to the '-2' slot."""
    if not _LOCKING:
        skip('requires advisory file locking')
    real_flock = log.fcntl.flock
    calls = {'n': 0}
    def _flock(fd, flags):
        calls['n'] += 1
        if calls['n'] == 1:
            raise OSError(errno.EAGAIN, 'temporarily unavailable')  # slot 1 contended
        return real_flock(fd, flags)  # slot 2 acquires for real
    monkeypatch.setattr(log.fcntl, 'flock', _flock)
    base = str(tmp_path / 'app.log')
    assert os.path.basename(claim_file_slot(base)) == 'app-2.log'


@mark.unit
def test_lock_record_round_trips(tmp_path: Path) -> None:
    """A won lock stamps the sidecar with this process's owner record, readable back."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    claimed = claim_file_slot(base)
    record = read_lock_record(claimed + '.lock')
    assert record is not None
    assert record['v'] == 1
    assert record['pid'] == os.getpid()
    assert record['host'] == HOSTNAME_SHORT
    assert record['instance'] == INSTANCE
    assert log._owner_alive(record) is True


@mark.unit
def test_losing_probe_does_not_blank_the_record(tmp_path: Path) -> None:
    """A second (losing) claim opens the sidecar non-truncating, preserving the winner's record."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    claim_file_slot(base)                 # winner writes its record on slot 1
    claim_file_slot(base)                 # loser probes slot 1 (fails), advances to slot 2
    record = read_lock_record(base + '.lock')
    assert record is not None and record['pid'] == os.getpid()


@mark.unit
def test_legacy_empty_lock_record_reads_as_no_holder(tmp_path: Path) -> None:
    """A legacy 0-byte sidecar carries no record and reads as 'no live holder'."""
    sidecar = tmp_path / 'client.log.lock'
    sidecar.write_bytes(b'')
    assert read_lock_record(str(sidecar)) is None
    assert log._owner_alive(read_lock_record(str(sidecar))) is False


@mark.unit
def test_torn_lock_record_reads_as_no_holder(tmp_path: Path) -> None:
    """An unparseable (torn) record reads as 'no live holder' after the retry."""
    sidecar = tmp_path / 'client.log.lock'
    sidecar.write_text('{not valid json')
    assert read_lock_record(str(sidecar)) is None


@mark.unit
def test_owner_alive_lock_record_false_on_create_time_mismatch() -> None:
    """Our own live pid with a wrong start-time reads as dead (pid-reuse defense)."""
    record = {'v': 1, 'pid': os.getpid(), 'create_time': 1.0, 'host': HOSTNAME_SHORT, 'instance': 'x'}
    assert log._owner_alive(record) is False


@mark.unit
def test_owner_alive_lock_record_false_for_other_host() -> None:
    """A record stamped by another host is not a checkable local holder."""
    record = {'v': 1, 'pid': os.getpid(), 'create_time': 0.0, 'host': 'some-other-host', 'instance': 'x'}
    assert log._owner_alive(record) is False
