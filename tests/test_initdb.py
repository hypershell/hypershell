# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test initdb operations."""


# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile_echo, assert_output


@mark.integration
def test_which_database(temp_site: Path) -> None:
    """Confirm ephemeral database configuration."""
    assert main_lines(['hs', 'config', 'which', 'database.file']) == (
        exit_status.success,
        [f'{temp_site}/local.db (env: HYPERSHELL_DATABASE_FILE | default: null)', ],
        NO_OUTPUT
    )


@mark.integration
def test_list_empty_list(temp_site: Path) -> None:
    """New database is empty."""
    assert main_lines(['hs', 'list', '--limit', '1']) == (
        exit_status.success, NO_OUTPUT, NO_OUTPUT
    )


@mark.integration
def test_list_empty_count(temp_site: Path) -> None:
    """New database is empty."""
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )


@mark.integration
def test_vacuum(temp_site: Path) -> None:
    """Vacuum database with prompt."""
    assert main_lines(['hs', 'initdb', '--yes']) == (
        exit_status.success, NO_OUTPUT, [
        f'INFO [hypershell.data] SQLite database initialized automatically',
        f'INFO [hypershell.data] Optimizing database {temp_site / "local.db"}'
    ])
    assert main_lines(['hs', 'initdb', '--vacuum']) == (
        exit_status.runtime_error, NO_OUTPUT,
        ['CRITICAL [hypershell.data] RuntimeError: Non-interactive prompt cannot confirm (see --yes).', ]
    )
    assert main_lines(['hs', 'initdb', '--vacuum', '--yes']) == (
        exit_status.success, NO_OUTPUT, [
        f'INFO [hypershell.data] Vacuuming database {temp_site / "local.db"}',
        f'INFO [hypershell.data] Cleaned 0.00B from {temp_site / "local.db"}'
    ])


@mark.integration
def test_rotate(temp_site: Path) -> None:
    """Rotate database."""
    assert main_lines(['hs', 'initdb', '--yes']) == (
        exit_status.success, NO_OUTPUT, [
        f'INFO [hypershell.data] SQLite database initialized automatically',
        f'INFO [hypershell.data] Optimizing database {temp_site / "local.db"}'
    ])
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', taskfile])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(implicit - not executable\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 1 tasks$', stderr, 4)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)
