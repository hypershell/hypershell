# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test basic top-level command-line interface."""


# Type annotations
from __future__ import annotations
from typing import Final, Dict

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests import main
from hypershell import (
    APP_VERSION, APP_USAGE, APP_HELP, __citation__,
    ClientApp, InitDBApp, ConfigApp, TaskGroupApp,
)



@mark.integration
def test_usage() -> None:
    """Usage information is printed when no arguments are given."""
    rc, stdout, stderr = main(['hs', ])
    assert rc == exit_status.usage
    assert stdout == APP_USAGE
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-v', '--version'])
def test_version(opt: str) -> None:
    """Version information is printed with -v, --version option."""
    rc, stdout, stderr = main(['hs', opt, ])
    assert rc == exit_status.success
    assert stdout == APP_VERSION
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_help(opt: str) -> None:
    """Help statement is printed with -h, --help option."""
    rc, stdout, stderr = main(['hs', opt, ])
    assert rc == exit_status.success
    assert stdout == APP_HELP
    assert stderr == ''


@mark.integration
def test_citation() -> None:
    """Citation is printed with --citation option."""
    rc, stdout, stderr = main(['hs', '--citation', ])
    assert rc == exit_status.success
    assert stdout == __citation__
    assert stderr == ''


@mark.integration
def test_client_usage() -> None:
    """The 'hs client' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'client', ])
    assert rc == exit_status.usage
    assert stdout == ClientApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_client_help(opt: str) -> None:
    """The 'hs client' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'client', opt, ])
    assert rc == exit_status.success
    assert stdout == ClientApp.interface.help_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_initdb_help(opt: str) -> None:
    """The 'hs initdb' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'initdb', opt, ])
    assert rc == exit_status.success
    assert stdout == InitDBApp.interface.help_text
    assert stderr == ''


@mark.integration
def test_config_usage() -> None:
    """The 'hs config' command prints usage statement without arguments."""
    rc, stdout, stderr = main(['hs', 'config', ])
    assert rc == exit_status.usage
    assert stdout == ConfigApp.interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
def test_config_help(opt: str) -> None:
    """The 'hs config' command prints help statement with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'config', opt, ])
    assert rc == exit_status.success
    assert stdout == ConfigApp.interface.help_text
    assert stderr == ''


@mark.integration
@mark.parametrize('cmd', ['get', 'set', 'which', 'edit'])
def test_config_subcommand_usage(cmd: str) -> None:
    """The 'hs config' subcommands print usage statements without arguments."""
    rc, stdout, stderr = main(['hs', 'config', cmd, ])
    assert rc == exit_status.usage
    assert stdout == ConfigApp.commands[cmd].interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
@mark.parametrize('cmd', ['get', 'set', 'which', 'edit'])
def test_config_subcommand_help(cmd: str, opt: str) -> None:
    """The 'hs config' subcommands print help statements with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'config', cmd, opt, ])
    assert rc == exit_status.success
    assert stdout == ConfigApp.commands[cmd].interface.help_text
    assert stderr == ''


@mark.integration
@mark.parametrize('cmd', ['submit', 'info', 'wait', 'run', 'search', 'update'])
def test_task_subcommand_usage(cmd: str) -> None:
    """The 'hs task' subcommands print usage statements without arguments."""
    rc, stdout, stderr = main(['hs', 'task', cmd, ])
    assert rc == exit_status.usage
    assert stdout == TaskGroupApp.commands[cmd].interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
@mark.parametrize('cmd', ['submit', 'info', 'wait', 'run', 'search', 'update'])
def test_task_subcommand_help(cmd: str, opt: str) -> None:
    """The 'hs task' subcommands print help statements with -h, --help option."""
    rc, stdout, stderr = main(['hs', 'task', cmd, opt, ])
    assert rc == exit_status.success
    assert stdout == TaskGroupApp.commands[cmd].interface.help_text
    assert stderr == ''


# The 'task search' subcommand is renamed to 'list'
TASK_COMMAND_ALIASES: Final[Dict[str, str]] = {
    'info': 'info',
    'wait': 'wait',
    'run': 'run',
    'list': 'search',
    'update': 'update',
}


@mark.integration
@mark.parametrize('cmd', ['info', 'wait', 'run', 'list', 'update'])
def test_alt_subcommand_usage(cmd: str) -> None:
    """The alternate (task) subcommands print usage statements without arguments."""
    rc, stdout, stderr = main(['hs', cmd, ])
    assert rc == exit_status.usage
    assert stdout == TaskGroupApp.commands[TASK_COMMAND_ALIASES[cmd]].interface.usage_text
    assert stderr == ''


@mark.integration
@mark.parametrize('opt', ['-h', '--help'])
@mark.parametrize('cmd', ['info', 'wait', 'run', 'list', 'update'])
def test_alt_subcommand_help(cmd: str, opt: str) -> None:
    """The alternate (task) subcommands print help statements with -h, --help option."""
    rc, stdout, stderr = main(['hs', cmd, opt, ])
    assert rc == exit_status.success
    assert stdout == TaskGroupApp.commands[TASK_COMMAND_ALIASES[cmd]].interface.help_text
