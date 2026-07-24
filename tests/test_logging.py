# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for file-based logging path resolution and slot claiming."""


# Type annotations
from __future__ import annotations

# Standard libs
import os
import sys
import time
import errno
import signal
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
    """Release and clear process-global slot-lock state after each test.

    Slot locks are held for the life of the owner and the finalizer runs once; without this a
    claimed handle (or a tripped `_FINALIZED` flag) would leak across tests and skew later
    contention/liveness/finalize checks.
    """
    yield
    for handle in log._slot_locks:
        try:
            handle.close()
        except OSError:
            pass
    log._slot_locks.clear()
    log._FINALIZED = False


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


# A program that claims a slot then exits cleanly, so its atexit finalizer drops the sidecar.
_CLEAN_EXITER = (
    'import sys;'
    'from hypershell.core.logging import claim_file_slot;'
    'print(claim_file_slot(sys.argv[1]), flush=True)'
)


@mark.unit
def test_finalize_logging_drops_own_sidecar(tmp_path: Path) -> None:
    """On clean shutdown a process removes its own held lock sidecar (best-effort, R3)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    claimed = claim_file_slot(base)
    sidecar = claimed + '.lock'
    assert os.path.exists(sidecar)
    log.finalize_logging()
    assert not os.path.exists(sidecar)  # own sidecar removed while its lock was still held
    assert not log._slot_locks


@mark.integration
def test_clean_exit_removes_own_sidecar_via_atexit(tmp_path: Path) -> None:
    """A cleanly-exiting subprocess sweeps its own sidecar through the registered atexit hook."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    proc = Popen([sys.executable, '-c', _CLEAN_EXITER, base], stdout=PIPE, env=os.environ)
    claimed = proc.stdout.readline().decode().strip()
    proc.wait()
    assert proc.returncode == 0
    assert os.path.basename(claimed) == 'client.log'
    assert not os.path.exists(claimed + '.lock')  # atexit finalizer dropped it on clean exit


@mark.unit
def test_prune_removes_stale_unlocked_sidecar(tmp_path: Path) -> None:
    """The canonical winner prunes a sibling '-N' sidecar that no live process holds (R4)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    root, ext = os.path.splitext(base)
    stale = f'{root}-2{ext}.lock'
    open(stale, 'w').close()  # unlocked, legacy 0-byte sidecar: no live holder
    assert os.path.exists(stale)
    claim_file_slot(base)  # win the canonical slot (holds client.log.lock)
    log.prune_stale_sidecars(base)
    assert not os.path.exists(stale)


@mark.unit
def test_prune_keeps_live_held_sibling_sidecar(tmp_path: Path) -> None:
    """A sibling sidecar a live process holds is never pruned (flock-acquire is the safety gate)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    root, ext = os.path.splitext(base)
    claim_file_slot(base)      # holds client.log.lock (canonical)
    claim_file_slot(base)      # holds client-2.log.lock (a live sibling)
    held = f'{root}-2{ext}.lock'
    assert os.path.exists(held)
    log.prune_stale_sidecars(base)
    assert os.path.exists(held)  # live holder -> retained


@mark.unit
def test_prune_sidecar_never_touches_data_or_rotated_files(tmp_path: Path) -> None:
    """Pruning removes only '.lock' sidecars, never '-N' data, its rotated lineage, or 'main' (R5)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    base = str(tmp_path / 'client.log')
    root, ext = os.path.splitext(base)
    stale = f'{root}-2{ext}.lock'
    data = f'{root}-2{ext}'                          # client-2.log        (rank data)
    rotated = f'{root}-2.20260723'                   # client-2.20260723   (rotated lineage)
    partial = f'{root}-2.20260723.gz.partial'        # mid-compression child
    main_side = str(tmp_path / 'main.log.lock')      # the 'main' role is never pruned
    open(stale, 'w').close()
    for survivor in (data, rotated, partial, main_side):
        Path(survivor).write_text('keep me')
    claim_file_slot(base)
    log.prune_stale_sidecars(base)
    assert not os.path.exists(stale)  # only the sidecar goes
    for survivor in (data, rotated, partial, main_side):
        assert os.path.exists(survivor), survivor
        assert Path(survivor).read_text() == 'keep me'


def _fork_lock_probe(tmp_path: Path, child_closes: bool) -> bool:
    """Claim a slot, `os.fork()`, and (optionally) close the child's inherited copy.

    Models the queue-manager fork: the child shares the parent's slot-lock open-file-description.
    After the child has done its close-or-not and the parent releases its own handle, probe
    whether the slot lock is re-acquirable. Returns True if released, False if a child that kept
    the inherited descriptor is ghost-holding it. Isolates the fd mechanism (no multiprocessing).
    """
    base = str(tmp_path / 'server.log')
    sidecar = claim_file_slot(base) + '.lock'
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # Child: shares the parent's slot-lock OFD across the fork.
        os.close(r)
        if child_closes:
            log.close_inherited_slot_locks()  # The P3 fix: drop the inherited copy.
        os.write(w, b'x')   # Signal readiness (still holding the OFD if we did not close).
        time.sleep(10)      # Linger so the parent can probe while we are alive.
        os._exit(0)
    os.close(w)
    os.read(r, 1)           # Wait until the child has closed-or-not.
    os.close(r)
    for handle in list(log._slot_locks):  # Parent releases its own handle (as on a clean exit).
        handle.close()
    log._slot_locks.clear()
    probe = log._try_lock(sidecar)        # Acquirable iff no live descriptor holds the OFD.
    acquired = probe is not None
    if probe is not None:
        probe.close()
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    return acquired


@mark.unit
@mark.skipif(os.name == 'nt', reason='POSIX fork semantics')
def test_forked_child_keeping_inherited_lock_ghost_locks_parent_slot(tmp_path: Path) -> None:
    """The latent leak: a forked child that keeps the inherited descriptor holds the slot lock
    alive after the parent releases it (a killed parent would ghost-lock its slot)."""
    if not _LOCKING:
        skip('requires advisory file locking')
    assert _fork_lock_probe(tmp_path, child_closes=False) is False


@mark.unit
@mark.skipif(os.name == 'nt', reason='POSIX fork semantics')
def test_forked_child_closing_inherited_lock_releases_parent_slot(tmp_path: Path) -> None:
    """P3's fix (R6): the child closing its inherited descriptor lets the slot lock release
    exactly when the true owner (the parent) exits — no ghost lock."""
    if not _LOCKING:
        skip('requires advisory file locking')
    assert _fork_lock_probe(tmp_path, child_closes=True) is True
