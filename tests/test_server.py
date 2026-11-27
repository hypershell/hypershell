# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
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
    '-b': '-b/--bundlesize', '--bundlesize': '-b/--bundlesize',
    '-w': '-w/--bundlewait', '--bundlewait': '-w/--bundlewait',
    '-Q': '-Q/--poll', '--poll': '-Q/--poll',
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


@mark.integration
@mark.parametrize('argv, message', [
    (['--autoscaling', '--restart'], 'Using --autoscaling with --restart is invalid'),
    (['tasks.in', '--autoscaling'], 'Cannot specify both FILE and --autoscaling'),
    (['--autoscaling', '--no-db'], 'Cannot use --autoscaling with --no-db'),
    (['--autoscaling', '--no-confirm'], 'Cannot use --autoscaling with --no-confirm'),
    (['--autoscaling=sometimes'], "Autoscaling mode must be 'fixed' or 'dynamic' (given 'sometimes')"),
])
def test_server_autoscaling_refusals(argv: list, message: str) -> None:
    """Autoscaling with relay sites runs forever on the database with confirmation; anything else is refused."""
    assert main_lines(['hs', 'server', *argv]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] {message}',
        ]
    )


@mark.integration
@mark.parametrize('opt, pair', [('-F', '-F/--factor'), ('--factor', '-F/--factor'),
                                ('-P', '-P/--period'), ('--period', '-P/--period')])
def test_server_autoscaling_missing_option_value(opt: str, pair: str) -> None:
    """Error on a missing value for the autoscaling options."""
    assert main_lines(['hs', 'server', '--autoscaling', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] argument {pair}: expected one argument',
        ]
    )
