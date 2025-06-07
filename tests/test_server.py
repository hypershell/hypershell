# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test server operations."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile_echo, assert_output, UUID_PATTERN
from hypershell.server import ServerApp


@mark.integration
def test_server_usage() -> None:
    """The 'hs server' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'server', ])
    assert rc == exit_status.usage
    assert stdout == ServerApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_server_help(opt: str) -> None:
    """The 'hs server' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'server', opt, ])
    assert rc == exit_status.success
    assert stdout == ServerApp.interface.help_text
    assert stderr == ''


@mark.integration
def test_server_mixed_modes() -> None:
    """Cannot use multiple start methods."""
    assert main_lines(['hs', 'server', '--forever', '--restart']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            'CRITICAL [hypershell] Using --forever with --restart is invalid',
        ]
    )


@mark.integration
@mark.parametrize('opt', ['--foo', '--bar', '--baz=7'])
def test_server_bad_options(opt: str) -> None:
    """Error on invalid options."""
    assert main_lines(['hs', 'server', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: {opt}',
        ]
    )


@mark.integration
def test_server_missing_file(temp_site: Path) -> None:
    """Error on missing file."""
    missing_file = str(temp_site / 'missing_file.txt')
    rc, stdout, stderr = main(['hs', 'server', missing_file])
    assert rc == exit_status.runtime_error
    assert stdout == ''
    assert_output(r'CRITICAL .* FileNotFoundError: .* No such file .*', stderr, 1)


@mark.integration
def test_server_multiple_arguments() -> None:
    """Error on multiple positional arguments."""
    assert main_lines(['hs', 'server', 'file_a', 'file_b']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: file_b',
        ]
    )


OPTION_PAIR: Final[Dict[str, str]] = {
    '-H': '-H/--bind', '--bind': '-H/--bind',
    '-p': '-p/--port', '--port': '-p/--port',
    '-k': '-k/--auth', '--auth': '-k/--auth',
    '-b': '-b/--bundlesize', '--bundlesize': '-b/--bundlesize',
    '-w': '-w/--bundlewait', '--bundlewait': '-w/--bundlewait',
    '-r': '-r/--max-retries', '--max-retries': '-r/--max-retries',
    '-f': '-f/--failures', '--failures': '-f/--failures',
}


@mark.integration
@mark.parametrize('opt', list(OPTION_PAIR))
def test_submit_missing_option_value(opt: str) -> None:
    """Error on missing option value."""
    assert main_lines(['hs', 'server', '-', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] argument {OPTION_PAIR[opt]}: expected one argument',
        ]
    )
