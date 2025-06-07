# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test cluster operations."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main, main_lines, NO_OUTPUT, create_taskfile_echo, assert_output
from hypershell.cluster import ClusterApp


@mark.integration
def test_cluster_usage() -> None:
    """The 'hs cluster' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'cluster', ])
    assert rc == exit_status.usage
    assert stdout == ClusterApp.interface.usage_text
    assert stderr == ''


@mark.integration
def test_cluster_usage_hsx() -> None:
    """The 'hsx' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hsx', ])
    assert rc == exit_status.usage
    assert stdout == ClusterApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_cluster_help(opt: str) -> None:
    """The 'hs cluster' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'cluster', opt, ])
    assert rc == exit_status.success
    assert stdout == ClusterApp.interface.help_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_cluster_help_hsx(opt: str) -> None:
    """The 'hsx' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hsx', opt, ])
    assert rc == exit_status.success
    assert stdout == ClusterApp.interface.help_text
    assert stderr == ''


@mark.integration
def test_cluster_mixed_modes() -> None:
    """Cannot use multiple start methods."""
    assert main_lines(['hs', 'cluster', '--forever', '--restart']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            'CRITICAL [hypershell] Using --forever with --restart is invalid',
        ]
    )


@mark.integration
@mark.parametrize('opt', ['--foo', '--bar', '--baz=7'])
def test_cluster_bad_options(opt: str) -> None:
    """Error on invalid options."""
    assert main_lines(['hs', 'cluster', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: {opt}',
        ]
    )


@mark.integration
def test_cluster_missing_file(temp_site: Path) -> None:
    """Error on missing file."""
    missing_file = str(temp_site / 'missing_file.txt')
    rc, stdout, stderr = main(['hs', 'cluster', missing_file])
    assert rc == exit_status.runtime_error
    assert stdout == ''
    assert_output(r'CRITICAL .* FileNotFoundError: .* No such file .*', stderr, 1)


@mark.integration
def test_cluster_multiple_arguments() -> None:
    """Error on multiple positional arguments."""
    assert main_lines(['hs', 'cluster', 'file_a', 'file_b']) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] unrecognized arguments: file_b',
        ]
    )


OPTION_PAIR: Final[Dict[str, str]] = {
    '-N': '-N/--num-tasks', '--num-tasks': '-N/--num-tasks',
    '-t': '-t/--template', '--template': '-t/--template',
    '-p': '-p/--port', '--port': '-p/--port',
    '-b': '-b/--bundlesize', '--bundlesize': '-b/--bundlesize',
    '-w': '-w/--bundlewait', '--bundlewait': '-w/--bundlewait',
    '-r': '-r/--max-retries', '--max-retries': '-r/--max-retries',
    '-o': '-o/--output', '--output': '-o/--output',
    '-e': '-e/--errors', '--errors': '-e/--errors',
    '-f': '-f/--failures', '--failures': '-f/--failures',
    '--ssh-args': '--ssh-args', '--ssh-group': '--ssh-group',
    '--remote-exe': '--remote-exe',
    '-d': '-d/--delay-start', '--delay-start': '-d/--delay-start',
    '-T': '-T/--timeout', '--timeout': '-T/--timeout',
    '-W': '-W/--task-timeout', '--task-timeout': '-W/--task-timeout',
    '-S': '-S/--signalwait', '--signalwait': '-S/--signalwait',
    '-F': '-F/--factor', '--factor': '-F/--factor',
    '-P': '-P/--period', '--period': '-P/--period',
    '-I': '-I/--init-size', '--init-size': '-I/--init-size',
    '-X': '-X/--min-size', '--min-size': '-X/--min-size',
    '-Y': '-Y/--max-size', '--max-size': '-Y/--max-size',
}


@mark.integration
@mark.parametrize('opt', list(OPTION_PAIR))
def test_cluster_missing_option_value(opt: str) -> None:
    """Error on missing option value."""
    assert main_lines(['hs', 'cluster', '-', opt]) == (
        exit_status.bad_argument, NO_OUTPUT, [
            f'CRITICAL [hypershell] argument {OPTION_PAIR[opt]}: expected one argument',
        ]
    )


@mark.integration
def test_cluster_basic(temp_site: Path) -> None:
    """Run small collection of tasks without database."""
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'cluster', taskfile, '--no-db', '--no-confirm'])
    assert rc == exit_status.success
    assert stdout == '\n'.join(map(str, range(4)))
    assert_output(r'Registered client', stderr, 1)
    assert_output(r'INFO \[hypershell.client\] Running task', stderr, 4)
    assert_output(r'DEBUG \[hypershell.client\] Running task', stderr, 4)
    assert_output(r'\[hypershell.server\] Completed task', stderr, 4)
    assert_output(r'\[hypershell.client\] Completed task', stderr, 4)


@mark.integration
def test_cluster_database(temp_site: Path) -> None:
    """Run small collection of tasks from file to database."""

    # Start out with an empty database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['0', ], NO_OUTPUT
    )

    # Run with `seq 4` inputs
    taskfile = create_taskfile_echo(temp_site, count=4)
    rc, stdout, stderr = main(['hs', 'cluster', taskfile])
    assert rc == exit_status.success
    assert stdout == '\n'.join(map(str, range(4)))
    assert_output(r'Registered client', stderr, 1)
    assert_output(r'INFO \[hypershell.client\] Running task', stderr, 4)
    assert_output(r'DEBUG \[hypershell.client\] Running task', stderr, 4)
    assert_output(r'\[hypershell.server\] Completed task', stderr, 4)
    assert_output(r'\[hypershell.client\] Completed task', stderr, 4)

    # Now there are 4 tasks in the database
    assert main_lines(['hs', 'list', '--count']) == (
        exit_status.success, ['4', ], NO_OUTPUT
    )

    # Confirm arguments
    assert main_lines(['hs', 'list', 'args', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['echo 0', 'echo 1', 'echo 2', 'echo 3'], NO_OUTPUT
    )

    # Confirm status
    assert main_lines(['hs', 'list', 'exit_status', '-f', 'plain', '-s', 'submit_time']) == (
        exit_status.success, ['0'] * 4, NO_OUTPUT
    )
