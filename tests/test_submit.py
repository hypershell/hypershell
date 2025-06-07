# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test submit operations."""


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
from hypershell.submit import SubmitApp


@mark.integration
def test_submit_usage() -> None:
    """The 'hs submit' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'submit', ])
    assert rc == exit_status.usage
    assert stdout == SubmitApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_submit_help(opt: str) -> None:
    """The 'hs submit' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'submit', opt, ])
    assert rc == exit_status.success
    assert stdout == SubmitApp.interface.help_text
    assert stderr == ''

@mark.integration
def test_submit_mixed_inputs() -> None:
    """Cannot use positional arguments with -f option."""
    assert main_lines(['hs', 'submit', 'a', 'b', '-f', 'some_file']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            'CRITICAL [hypershell] Cannot specify both -f/--task-file and positional arguments',
        ]
    )


@mark.integration
@mark.parametrize('opt', ['--foo', '--bar', '--baz=7'])
def test_submit_bad_options(opt: str) -> None:
    """Error on invalid options."""
    assert main_lines(['hs', 'submit', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: {opt}',
        ]
    )


@mark.integration
def test_submit_missing_file(temp_site: Path) -> None:
    """Error on missing file."""
    missing_file = str(temp_site / 'missing_file.txt')
    rc, stdout, stderr = main(['hs', 'submit', '-f', missing_file])
    assert rc == exit_status.runtime_error
    assert stdout == ''
    assert_output(r'CRITICAL .* FileNotFoundError: .* No such file .*', stderr, 1)


OPTION_PAIR: Final[Dict[str, str]] = {
    '--template': '--template',
    '-b': '-b/--bundlesize', '--bundlesize': '-b/--bundlesize',
    '-w': '-w/--bundlewait', '--bundlewait': '-w/--bundlewait',
    '-t': '-t/--tag', '--tag': '-t/--tag',
}


OPTION_COUNT: Final[Dict[str, str]] = {
    **{k: 'one' for k in OPTION_PAIR},
    **{k: 'at least one' for k in {'-t', '--tag'}}
}


@mark.integration
@mark.parametrize('opt', list(OPTION_PAIR))
def test_submit_missing_option_value(opt: str) -> None:
    """Error on missing option value."""
    assert main_lines(['hs', 'submit', 'echo', 'hello world', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] argument {OPTION_PAIR[opt]}: expected {OPTION_COUNT[opt]} argument',
        ]
    )


@mark.integration
def test_submit_single(temp_site: Path) -> None:
    """Submit single task to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit single input task
    rc, stdout, stderr = main(['hs', 'submit', 'echo', 'hello world'])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted single task \(explicit\)$', stderr, 1)
    assert_output(r'INFO .* Submitted task \((?P<uuid>.*)\)$', stderr, 1, groups={'uuid': UUID_PATTERN})

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['1', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain']) == (
        exit_status.success, ['echo "hello world"', ], NO_OUTPUT
    )


@mark.integration
def test_submit_basic(temp_site: Path) -> None:
    """Submit collection of tasks from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 4` inputs
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', taskfile])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(implicit - not executable\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 1 tasks$', stderr, 4)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['4', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['echo 0', 'echo 1', 'echo 2', 'echo 3'], NO_OUTPUT
    )


@mark.integration
def test_submit_explicit(temp_site: Path) -> None:
    """Submit collection of tasks from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 4` inputs
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'submit', '-f', taskfile])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(explicit\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 1 tasks$', stderr, 4)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 4 tasks$', stderr, 1)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['4', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['echo 0', 'echo 1', 'echo 2', 'echo 3'], NO_OUTPUT
    )


@mark.integration
def test_submit_bundled(temp_site: Path) -> None:
    """Submit larger collection with bundling from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Submit `seq 100` inputs
    taskfile = create_taskfile_echo(temp_site, count=100)
    rc, stdout, stderr = main(['hs', 'submit', taskfile, '-b', '10'])
    assert rc == exit_status.success
    assert stdout == ''
    assert_output(r'DEBUG .* Submitted from (?P<file>.*) \(implicit - not executable\)$',
                  stderr, 1, groups={'file': str(taskfile)})
    assert_output(r'DEBUG .* Submitted 10 tasks$', stderr, 10)
    assert_output(r'DEBUG .* Done$', stderr, 1)
    assert_output(r'INFO .* Submitted 100 tasks$', stderr, 1)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['100', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, [f'echo {n}' for n in range(100)], NO_OUTPUT
    )
