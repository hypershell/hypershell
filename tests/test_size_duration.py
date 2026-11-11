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
from hypershell.core.types import parse_bytes, parse_time, UnitError
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


# A bare integer stays seconds; units, fractions and Slurm times are new.
DURATION_ACCEPTED: Final[List[tuple[str, int]]] = [
    ('90', 90),
    ('3600', 3600),
    ('0', 0),
    ('45s', 45),
    ('90m', 5400),
    ('1h30m', 5400),
    ('1d12h', 129600),
    ('1.5h', 5400),
    ('1d1h1m1s', 90061),
    ('1H30M', 5400),
    ('90S', 90),
    ('0.5s', 1),
    ('0.1h', 360),
    ('1.5', 2),
    ('90:00', 5400),
    ('1:30:00', 5400),
    ('1-12', 129600),
    ('1-12:00', 129600),
    ('1-12:00:00', 129600),
    ('0-00:00:30', 30),
    (' 1h30m ', 5400),
]


DURATION_REFUSED: Final[List[str]] = [
    '5MB', '1hr', '2hours', '1w', '30m1h', '1h 30m', '1m30', 'h', '', '-1', '1-', '1:30:00:00',
    '1.5:00', '1-1-1',
]


@mark.unit
@mark.parametrize('value, expected', DURATION_ACCEPTED)
def test_duration_accepted(value: str, expected: int) -> None:
    """Each accepted duration is read as its total in whole seconds, rounded up."""
    assert parse_time(value) == expected


@mark.unit
@mark.parametrize('value', DURATION_REFUSED)
def test_duration_refused_names_value_and_forms(value: str) -> None:
    """A value that is not a duration in full is refused, never read by its leading part."""
    with raises(UnitError) as exc_info:
        parse_time(value)
    message = str(exc_info.value)
    assert repr(value) in message
    assert '1h30m' in message and 'HH:MM:SS' in message


# Each duration option as (app, argv for the option, namespace attribute).
TIMEOUT_OPTIONS: Final[List[tuple[type, List[str], str]]] = [
    (SubmitApp, ['-W'], 'timeout'),
    (SubmitApp, ['--timeout'], 'timeout'),
    (ServerApp, ['-W'], 'task_timeout'),
    (ClientApp, ['-W'], 'task_timeout'),
    (ClientApp, ['-T'], 'client_timeout'),
    (ClusterApp, ['-W'], 'task_timeout'),
    (ClusterApp, ['-T'], 'client_timeout'),
]


@mark.unit
@mark.parametrize('app, option, dest', TIMEOUT_OPTIONS)
@mark.parametrize('value, expected', [('3600', 3600), ('1h30m', 5400), ('1.5h', 5400), ('1d12h', 129600),
                                      ('90:00', 5400), ('1:30:00', 5400), ('1-12', 129600)])
def test_timeout_option_parsed(app: type, option: List[str], dest: str, value: str, expected: int) -> None:
    """Every task and client timeout option reads seconds, units, and Slurm times."""
    assert getattr(app.interface.parse_args([*option, value]), dest) == expected


@mark.unit
@mark.parametrize('app, option, dest', TIMEOUT_OPTIONS)
@mark.parametrize('value', ['1hr', '5MB', '1w'])
def test_timeout_option_refused(app: type, option: List[str], dest: str, value: str) -> None:
    """Every timeout option refuses a non-duration with a message naming the value and the forms."""
    with raises(ArgumentError) as exc_info:
        app.interface.parse_args([*option, value])
    message = str(exc_info.value)
    assert repr(value) in message and '1h30m' in message


@mark.unit
@mark.parametrize('value, expected', [('1h30m', 5400), ('1:30:00', 5400), ('1.5h', 5400),
                                      ('1-12:00:00', 129600), ('3600', 3600)])
def test_timeout_inline_tag_parsed(value: str, expected: int) -> None:
    """The inline timeout tag and the timeout tag argument are stored as seconds."""
    Task.ensure_valid_tag({'timeout': value})
    assert Task.new(args=f'echo a # HYPERSHELL: timeout:{value}').timeout == expected
    assert Task.new(args='echo a', tag={'timeout': value}).timeout == expected
    assert Task.new(args='echo a', timeout=value).timeout == expected


@mark.unit
def test_timeout_numbers_unchanged() -> None:
    """A timeout that is already a number keeps its value, and an absent one stays absent."""
    assert Task.new(args='echo a # HYPERSHELL: timeout:10').timeout == 10
    assert Task.new(args='echo a', tag={'timeout': 60}).timeout == 60
    assert Task.new(args='echo a', timeout=120).timeout == 120
    assert Task.new(args='echo a', tag={'timeout': 60}, timeout=120).timeout == 60
    assert Task.new(args='echo a').timeout is None
    assert 'timeout' not in Task.new(args='echo a # HYPERSHELL: timeout:1h30m').tag


@mark.unit
@mark.parametrize('value', ['1hr', '2hours', '1w'])
def test_timeout_tag_refused(value: str) -> None:
    """A bad timeout is refused by tag validation, and by the task when validation was skipped."""
    with raises(ValueError) as exc_info:
        Task.ensure_valid_tag({'timeout': value})
    assert repr(value) in str(exc_info.value) and '1h30m' in str(exc_info.value)
    with raises(RuntimeError) as exc_info:
        Task.new(args=f'echo a # HYPERSHELL: timeout:{value}')
    assert repr(value) in str(exc_info.value)
    with raises(RuntimeError, match='Failed to parse task timeout'):
        Task.new(args='echo a', timeout=value)
    with raises(RuntimeError, match='Failed to parse task timeout'):
        Task.new(args=f'echo a # HYPERSHELL: naked timeout:{value}')  # Tags after a naked tag are not checked.


@mark.integration
def test_timeout_inline_and_option_stored(temp_site: Path) -> None:
    """A submitted task stores inline timeouts and the timeout option as seconds."""
    taskfile = create_taskfile(temp_site, ['echo a # HYPERSHELL: timeout:1h30m',
                                           'echo b # HYPERSHELL: timeout:1:30:00',
                                           'echo c # HYPERSHELL: timeout:60',
                                           'echo d'])
    rc, _, _ = main(['hs', 'submit', '-f', str(taskfile), '-W', '1.5h'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'timeout', '-f', 'plain', '-s', 'args']) == (
        exit_status.success, ['5400', '5400', '60', '5400'], ['', ]
    )


@mark.integration
def test_timeout_from_environment(temp_site: Path, monkeypatch) -> None:
    """A timeout string from the environment (as from a configuration file) is read as seconds."""
    monkeypatch.setenv('HYPERSHELL_TASK_TIMEOUT', '1h30m')
    rc, _, _ = main(['hs', 'submit', 'echo', 'a'])
    assert rc == exit_status.success
    assert main_lines(['hs', 'list', 'timeout', '-f', 'plain']) == (exit_status.success, ['5400', ], ['', ])


@mark.integration
def test_timeout_option_refused_on_command_line(temp_site: Path) -> None:
    """The command line refuses a non-duration timeout with the value and forms."""
    rc, _, stderr = main(['hs', 'submit', '-W', '1hr', 'echo', 'a'])
    assert rc == exit_status.bad_argument
    assert "'1hr'" in stderr and '1h30m' in stderr


@mark.integration
def test_timeout_inline_tag_refused_on_submit(temp_site: Path) -> None:
    """A bad inline timeout stops the submit with one line naming the value."""
    taskfile = create_taskfile(temp_site, ['echo a # HYPERSHELL: timeout:1hr'])
    rc, _, stderr = main(['hs', 'submit', '-f', str(taskfile)])
    assert rc == exit_status.runtime_error
    assert "'1hr'" in stderr and '1h30m' in stderr
    assert 'Traceback' not in stderr
