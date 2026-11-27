# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A `ValueError` or `OverflowError` that escapes while a command reads its arguments and configuration ends it cleanly.

The backstop is for a numeric case the table misses: a child patches an argument's parser, or a
start check, to raise, and the command exits 2 (reading its command line) or 3 (otherwise) with one
line naming the error, and no traceback. An error raised once work has started keeps today's path.
"""


# Type annotations
from __future__ import annotations
from typing import Final

# Standard libs
import sys
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests.bounded_checks import hs_cli


CHILD: Final[str] = '''
import sys
from hypershell import main
from hypershell.submit import SubmitApp
def boom(*args, **kwargs):
    raise {error}('injected {error}')
{patch}
sys.exit(main(['submit', '-b', '1', 'echo', 'a']))
'''
"""A child that patches one step of `hs submit`, then runs it."""

PATCHES: Final[dict] = {
    'arguments': ('[action for action in SubmitApp.interface._actions if action.dest == "bundlesize"][0].type = boom',
                  exit_status.bad_argument),
    'start': ('SubmitApp.check_arguments = boom', exit_status.bad_config),
}
"""Where the error is injected, and the exit status it then gives."""


def run_injected(site: Path, stage: str, error: str) -> None:
    """Inject `error` at `stage` of `hs submit`: it exits 2 or 3 with one line naming the error, and no traceback."""
    patch, status = PATCHES[stage]
    rc, _, stderr = hs_cli(site, [sys.executable, '-c', CHILD.format(error=error, patch=patch)])
    assert rc == status, stderr[-4000:]
    assert 'Traceback' not in stderr and 'traceback written' not in stderr, stderr[-4000:]
    lines = [line for line in stderr.splitlines() if f'injected {error}' in line]
    assert len(lines) == 1 and error in lines[0], stderr[-4000:]


@mark.integration
@mark.parametrize('stage', sorted(PATCHES))
def test_injected_error_before_work(temp_site: Path, stage: str) -> None:
    """An `OverflowError` escaping an argument's parser exits 2, and one escaping a start check exits 3."""
    run_injected(temp_site, stage, 'OverflowError')


@mark.integration
def test_injected_value_error_at_start(temp_site: Path) -> None:
    """A `ValueError` escaping a start check exits 3 the same way (argparse already reports one from a parser)."""
    run_injected(temp_site, 'start', 'ValueError')


@mark.integration
def test_error_after_work_keeps_path(temp_site: Path) -> None:
    """A `ValueError` once work has started is not the backstop's: it keeps today's path (exit 5, a traceback file)."""
    patch = 'SubmitApp.read_source = boom'
    rc, _, stderr = hs_cli(temp_site, [sys.executable, '-c', CHILD.format(error='ValueError', patch=patch)])
    assert rc == exit_status.runtime_error, stderr[-4000:]
    assert 'traceback written' in stderr, stderr[-4000:]
