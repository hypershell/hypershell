# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test `hs update` filtering, including the --cancelled and --signal aliases (parity with list)."""


# Type annotations
from __future__ import annotations

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status as cli_status

# Internal libs
from tests import main, main_lines, create_taskfile


def submit_with_statuses(temp_site: Path, statuses: dict) -> None:
    """Submit one task per tag in `statuses` and set each task's exit_status."""
    taskfile = create_taskfile(temp_site, [f'echo {tag}  # HYPERSHELL: n:{tag}' for tag in statuses])
    assert main(['hs', 'submit', str(taskfile)])[0] == cli_status.success
    for tag, status in statuses.items():
        assert main(['hs', 'update', f'exit_status={status}', '-t', f'n:{tag}', '--no-confirm'])[0] == \
            cli_status.success


@mark.integration
def test_update_cancelled_filter(temp_site: Path) -> None:
    """`hs update --cancelled` matches only cancelled tasks (exit_status == -1)."""
    submit_with_statuses(temp_site, {0: -1, 1: -15, 2: 0})

    # Revert only the cancelled task.
    assert main(['hs', 'update', '--revert', '--cancelled', '--no-confirm'])[0] == cli_status.success

    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['null']   # reverted
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:1'])[1] == ['-15']    # untouched
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:2'])[1] == ['0']      # untouched


@mark.integration
def test_update_signal_filter(temp_site: Path) -> None:
    """`hs update --signal NAME` matches only tasks killed by that signal (exit_status == -N)."""
    submit_with_statuses(temp_site, {0: -15, 1: -9})   # SIGTERM, SIGKILL

    # Revert only the SIGTERM task.
    assert main(['hs', 'update', '--revert', '--signal', 'TERM', '--no-confirm'])[0] == cli_status.success

    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['null']   # reverted
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:1'])[1] == ['-9']     # SIGKILL untouched


@mark.integration
def test_update_signal_unknown_errors(temp_site: Path) -> None:
    """An unknown --signal name is a usage error (before any change is applied)."""
    submit_with_statuses(temp_site, {0: -15})
    rc, _, _ = main(['hs', 'update', '--revert', '--signal', 'BOGUS', '--no-confirm'])
    assert rc != cli_status.success
    # The task was not touched.
    assert main_lines(['hs', 'list', 'exit_status', '-t', 'n:0'])[1] == ['-15']
