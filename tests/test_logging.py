# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for file-based logging path resolution and slot claiming."""


# Type annotations
from __future__ import annotations

# Standard libs
import os
import sys
from pathlib import Path
from subprocess import Popen, PIPE

# External libs
from pytest import mark, skip
from cmdkit.app import exit_status

# Internal libs
from tests import main
from hypershell.core.logging import (
    role_from_command, default_file_for, claim_file_slot, resolve_log_path,
    HOSTNAME_SHORT, _LOCKING,
)


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
