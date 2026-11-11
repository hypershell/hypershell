# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Test sizes and durations written with units on options, inline tags, and log rotation."""


# Type annotations
from __future__ import annotations
from typing import Final, List

# Standard libs
from pathlib import Path

# External libs
from pytest import mark, raises
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError

# Internal libs
from tests import main, main_lines, create_taskfile
from hypershell.core.types import parse_bytes, UnitError
from hypershell.data.model import Task
from hypershell.submit import SubmitApp
from hypershell.server import ServerApp
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp


KiB: Final[int] = 1024
MiB: Final[int] = 1024 * KiB
GiB: Final[int] = 1024 * MiB
TiB: Final[int] = 1024 * GiB


# Forms accepted before strict parsing keep their meaning; short and binary units are new.
SIZE_ACCEPTED: Final[List[tuple[str, int]]] = [
    ('16GB', 16 * GiB),
    ('512MB', 512 * MiB),
    ('2KB', 2 * KiB),
    ('16 GB', 16 * GiB),
    ('1.5GB', int(1.5 * GiB)),
    ('16gb', 16 * GiB),
    ('512mb', 512 * MiB),
    ('1TB', TiB),
    ('1024', 1024),
    ('0', 0),
    ('16B', 16),
    ('1.5', 1),
    ('16G', 16 * GiB),
    ('16g', 16 * GiB),
    ('16GiB', 16 * GiB),
    ('16gib', 16 * GiB),
    ('512M', 512 * MiB),
    ('1.5GiB', int(1.5 * GiB)),
    ('4K', 4 * KiB),
    ('2T', 2 * TiB),
    ('1.3K', 1331),
    (' 16G ', 16 * GiB),
]


SIZE_REFUSED: Final[List[str]] = [
    '8,000MB', '8GB of ram', '16X', '16GBB', '16iB', 'G', '', '.5G', '-1G', '16 G B',
    '30 2 * * *', '0 1 * * 0', '@daily', 'never',
]


@mark.unit
@mark.parametrize('value, expected', SIZE_ACCEPTED)
def test_size_accepted(value: str, expected: int) -> None:
    """Each accepted size is read as a power of 1024 times its number."""
    assert parse_bytes(value) == expected


@mark.unit
@mark.parametrize('value', SIZE_REFUSED)
def test_size_refused_names_value_and_forms(value: str) -> None:
    """A value that is not a size in full is refused, never read by its leading number."""
    with raises(UnitError) as exc_info:
        parse_bytes(value)
    message = str(exc_info.value)
    assert repr(value) in message
    assert 'powers of 1024' in message and 'iB' in message


@mark.unit
def test_size_error_is_value_error() -> None:
    """Callers that catch ValueError (inline tags, log rotation) still catch a refused size."""
    with raises(ValueError):
        parse_bytes('16X')


# Each memory option as (app, argv for the option, namespace attribute).
MEMORY_OPTIONS: Final[List[tuple[type, List[str], str]]] = [
    (SubmitApp, ['-m'], 'memory'),
    (SubmitApp, ['--memory'], 'memory'),
    (ServerApp, ['-m'], 'task_memory'),
    (ClientApp, ['-M'], 'client_memory'),
    (ClusterApp, ['-m'], 'memory'),
    (ClusterApp, ['-M'], 'client_memory'),
]


@mark.unit
@mark.parametrize('app, option, dest', MEMORY_OPTIONS)
@mark.parametrize('value, expected', [('16G', 16 * GiB), ('16GiB', 16 * GiB), ('16GB', 16 * GiB),
                                      ('512MB', 512 * MiB), ('1.5GiB', int(1.5 * GiB)), ('2048', 2048)])
def test_memory_option_parsed(app: type, option: List[str], dest: str, value: str, expected: int) -> None:
    """Every memory option reads short, binary and existing units as powers of 1024."""
    assert getattr(app.interface.parse_args([*option, value]), dest) == expected


@mark.unit
@mark.parametrize('app, option, dest', MEMORY_OPTIONS)
@mark.parametrize('value', ['16X', '8,000MB', '16GBB'])
def test_memory_option_refused(app: type, option: List[str], dest: str, value: str) -> None:
    """Every memory option refuses a non-size with a message naming the value and the forms."""
    with raises(ArgumentError) as exc_info:
        app.interface.parse_args([*option, value])
    message = str(exc_info.value)
    assert repr(value) in message and 'powers of 1024' in message


@mark.unit
@mark.parametrize('value, expected', [('16G', 16 * GiB), ('16GiB', 16 * GiB), ('16GB', 16 * GiB),
                                      ('1.5GiB', int(1.5 * GiB)), ('2000', 2000)])
def test_memory_inline_tag_parsed(value: str, expected: int) -> None:
    """The inline memory tag and the memory tag argument take the same forms as the option."""
    assert Task.new(args=f'echo a # HYPERSHELL: memory:{value}').memory == expected
    assert Task.new(args='echo a', tag={'memory': value}).memory == expected
    assert Task.new(args='echo a', tag={'memory': 4096}).memory == 4096


@mark.unit
@mark.parametrize('value', ['16X', '8,000MB', '16GBB'])
def test_memory_tag_validation_names_forms(value: str) -> None:
    """A memory tag value is checked by the size grammar, so the refusal lists the accepted forms."""
    with raises(ValueError) as exc_info:
        Task.ensure_valid_tag({'memory': value})
    assert repr(value) in str(exc_info.value) and 'powers of 1024' in str(exc_info.value)
    with raises(RuntimeError) as exc_info:
        Task.new(args=f'echo a # HYPERSHELL: memory:{value}')
    assert repr(value) in str(exc_info.value)


@mark.unit
def test_memory_unchecked_value_refused_by_task() -> None:
    """A memory value that skipped tag validation is still refused, as a one-line RuntimeError."""
    with raises(RuntimeError, match=r"Memory size '16X' is not valid"):
        Task.new(args='echo a', memory='16X')
    with raises(RuntimeError, match=r"Memory size '16X' is not valid"):
        Task.new(args='echo a # HYPERSHELL: naked memory:16X')  # Tags after a naked tag are not checked.


@mark.integration
def test_memory_inline_and_option_stored(temp_site: Path) -> None:
    """A submitted task stores an inline memory tag and the memory option as bytes."""
    taskfile = create_taskfile(temp_site, ['echo a # HYPERSHELL: memory:16G',
                                           'echo b # HYPERSHELL: memory:16GiB',
                                           'echo c'])
    rc, _, _ = main(['hs', 'submit', '-f', str(taskfile), '-m', '1.5GiB'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'memory', '-f', 'plain', '-s', 'args']) == (
        exit_status.success, [str(16 * GiB), str(16 * GiB), str(int(1.5 * GiB))], ['', ]
    )


@mark.integration
def test_memory_option_refused_on_command_line(temp_site: Path) -> None:
    """The command line refuses a non-size memory with the value and forms, and stores nothing."""
    rc, _, stderr = main(['hs', 'submit', '-m', '16X', 'echo', 'a'])
    assert rc == exit_status.bad_argument
    assert "'16X'" in stderr and 'powers of 1024' in stderr


@mark.integration
def test_memory_inline_tag_refused_on_submit(temp_site: Path) -> None:
    """A bad inline memory tag stops the submit with one line naming the value."""
    taskfile = create_taskfile(temp_site, ['echo a # HYPERSHELL: memory:16X'])
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.runtime_error
    assert "'16X'" in stderr and 'powers of 1024' in stderr
    assert 'Traceback' not in stderr


@mark.unit
@mark.parametrize('policy', ['30 2 * * *', '0 1 * * 0', '@daily', 'never'])
def test_rotate_cron_policy_is_not_a_size(policy: str) -> None:
    """A rotation policy that is a cron expression, even one starting with a digit, is not a size."""
    with raises(ValueError):
        parse_bytes(policy)


@mark.integration
@mark.parametrize('policy, kind', [('30 2 * * *', 'time-like'),
                                   ('2G', f'size-like: {2 * GiB:,} bytes'),
                                   ('2GB', f'size-like: {2 * GiB:,} bytes')])
def test_rotate_policy_kind(temp_site: Path, monkeypatch, policy: str, kind: str) -> None:
    """A real command configures time-based rotation for a cron policy and size-based for a size."""
    monkeypatch.setenv('HYPERSHELL_LOGGING_FILE_ROTATE', policy)
    rc, _, stderr = main(['hs', 'initdb', '--yes'])
    assert rc == exit_status.success
    assert f"Rotation policy '{policy}' ({kind})" in stderr
