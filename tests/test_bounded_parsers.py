# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""One parser per kind reads a value against its table entry and refuses it with one error (in process).

For each kind, and the entries with a form of their own: each bound and one past it, a non-number,
`nan`, `inf`, a 4,300-digit and a 5,000-digit integer (as text and as a number), a boolean, a
decimal for a whole kind, and text for a numeric key. The forms accepted today (units, the Slurm
forms, the period's rounding, the delay's random form) read as they do today. The table is imported
inside each test, so this module collects on a build without it.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Tuple

# Standard libs
import re
import math

# External libs
import pytest
from pytest import mark

# Internal libs
from tests.bounded_checks import LONG, WIDER, SHORT, refusal


def table() -> Any:
    """The module that holds the table (imported when a test runs, not at collection)."""
    from hypershell.core import config_schema
    return config_schema


# (entry, accepted values and what each reads as, refused values)
CASES: Final[List[Tuple[str, List[Tuple[Any, Any]], List[Any]]]] = [
    ('server.attempts', [(1, 1), ('1', 1), (32767, 32767), ('32767', 32767), (' 3 ', 3), ('+3', 3), ('3_0', 30),
                         (3.0, 3)],
     [0, '0', 32768, '32768', -1, '-1', 'x', '', '3.0', '1e3', 3.5, True, False, 'true', None, [1]]),
    ('task.cores', [(0, 0), ('0', 0), (32767, 32767)], [-1, 32768, '100000']),
    ('relay.num_threads', [(0, 0), (4194304, 4194304), (1025, 1025)], [-1, 4194305]),
    ('server.port', [(1, 1), (65535, 65535)], [0, 65536, '0x10']),
    ('server.bundlesize', [(1, 1), (2**31 - 1, 2**31 - 1)], [0, 2**31, '2147483648']),
    ('autoscale.failed_launches', [(0, 0), (2**31 - 1, 2**31 - 1)], [-1, 2**31]),
    ('option.group', [(0, 0), ('0', 0), (2**31 - 1, 2**31 - 1)], [-1, '-1', -2**31, 2**31]),
    ('option.part', [(0, 0), (2**31 - 1, 2**31 - 1)], [-1, 2**31]),
    ('option.limit', [(1, 1)], [0, -5, '-5']),
    ('option.max_retries', [(0, 0), (32766, 32766)], [-1, 32767]),
    ('relay.min', [(0, 0), ('<none>', '<none>'), (2**31 - 1, 2**31 - 1)], [-1, 2**31, 'none', '<NONE>']),
    ('client.ratelimit', [(0, 0), ('0', 0), (1e-6, 1e-6), ('0.000001', 1e-6), ('5', 5), (0.5, 0.5),
                          (1e9, 1e9), ('1000000000', 1000000000)],
     [-5, '-5', 1e-7, '1e-300', 1e-300, 1e9 + 1, '1000000001', 10**400, 'nan', 'inf', math.inf, math.nan, True]),
    ('autoscale.factor', [(1e-6, 1e-6), (1, 1), ('2.5', 2.5), (1e9, 1e9)], [0, '0', -1, 'nan', 'inf', 1e9 + 1]),
    ('task.timeout', [(0, 0), ('0', 0), ('1h30m', 5400), ('1:30:00', 5400), ('1-12', 129600), (2**31 - 1, 2**31 - 1),
                      ('2147483647', 2**31 - 1), ('1.5s', 2), (1.5, 1.5)],
     [-1, 2**31, '2147483648', '24856d', '-5', 'x', 'nan', 'inf', math.inf, True]),
    ('server.evict', [(10, 10), ('10s', 10), (2**31 - 1, 2**31 - 1), ('600', 600)],
     [9, '9', 9.5, 2**31, '86399999913600', -1]),
    ('server.tls.handshake', [(1, 1), (86400, 86400), ('1d', 86400)], [0, 86401, '2d']),
    ('option.interval', [(1, 1), ('5', 5)], [0, '0']),
    ('option.client.timeout', [(1, 1), ('1m', 60)], [0, '0']),
    ('task.memory', [(0, 0), ('0', 0), ('16G', 16 * 1024**3), ('1.5GiB', 1536 * 1024**2), ('512mb', 512 * 1024**2),
                     ('9223372036854775807', 2**63 - 1), (2**63 - 1, 2**63 - 1), ('8388607TB', 8388607 * 1024**4)],
     ['9223372036854775808', 2**63, '8388608TB', '8EB', '9000PB', '-1', -1, 'x', 'nan', True, 1.5e19]),
]
"""Each entry's accepted values (and what each reads as) and refused values; every entry also refuses the
values of `ALWAYS_REFUSED`."""

ALWAYS_REFUSED: Final[List[Any]] = ['x', 'nan', 'inf', '-inf', math.nan, math.inf, LONG, WIDER, int(LONG),
                                    True, 'true', [1], {'a': 1}]
"""Values every entry refuses: not a number of any kind, non-finite, too wide for any range, a boolean, a table."""


def accepted() -> List[Any]:
    """(entry, value, read as) for every accepted case."""
    return [pytest.param(name, value, expected, id=f'{name}-{value!r}'[:60])
            for name, good, _ in CASES for value, expected in good]


def refused() -> List[Any]:
    """(entry, value) for every refused case."""
    return [pytest.param(name, value, id=f'{name}-{value!r}'[:60])
            for name, _, bad in CASES for value in [*bad, *ALWAYS_REFUSED]]


@mark.unit
@mark.parametrize('name, value, expected', accepted())
def test_parse_accepted(name: str, value: Any, expected: Any) -> None:
    """A value inside its range reads as it does today, in every form taken today."""
    parsed = table().parse_setting(name, value)
    assert parsed == expected and type(parsed) is type(expected), (parsed, expected)


@mark.unit
@mark.parametrize('name, value', refused())
def test_parse_refused(name: str, value: Any) -> None:
    """A value out of range, or not a number of the entry's kind, raises one `SettingError` in the one form."""
    schema = table()
    with pytest.raises(schema.SettingError) as caught:
        schema.parse_setting(name, value)
    message = str(caught.value)
    assert isinstance(caught.value, ValueError)
    assert refusal(name).fullmatch(message), message
    assert len(message) <= SHORT, message[:SHORT]


@mark.unit
def test_parse_message_form() -> None:
    """The refusal names the setting, its form, its range (with the zero's meaning) and the value, and its source."""
    schema = table()
    with pytest.raises(schema.SettingError) as caught:
        schema.parse_setting('task.cores', 100000, name='-c/--cores')
    assert str(caught.value) == '-c/--cores: expected a whole number from 0 (unconstrained) to 32,767, given 100000'
    with pytest.raises(schema.SettingError) as caught:
        schema.parse_setting('server.evict', '5s', source='/tmp/config.toml')
    assert str(caught.value) == ("server.evict: expected a duration from 10 s to 2,147,483,647 s, given '5s' "
                                 "(from: /tmp/config.toml)")
    error = caught.value
    assert (error.name, error.source) == ('server.evict', '/tmp/config.toml')


@mark.unit
@mark.parametrize('value', [LONG, WIDER, int(LONG), 10**5000, -10**5000, 'x' * 10_000, b'\xff' * 100, math.nan,
                            1e308, [1] * 1000, {'a': LONG}, object()], ids=lambda value: type(value).__name__)
def test_parse_shown_never_raises(value: Any) -> None:
    """The value shown is cut to a short printable form whatever it is, and never raises."""
    shown = table().shown(value)
    assert isinstance(shown, str) and 0 < len(shown) <= 80, shown


@mark.unit
def test_parse_shown_as_given() -> None:
    """A short value is shown as given; a long one keeps its head and tail and says how long it is."""
    shown = table().shown
    assert shown('0') == "'0'" and shown(0) == '0' and shown(True) == 'True' and shown(1.5) == '1.5'
    long = shown(LONG)
    assert long.startswith("'999") and long.endswith('(4302 characters)'), long
    assert '…' in long


@mark.unit
def test_parse_size_exact() -> None:
    """The size parser is exact: 2**63 - 1 bytes is reached and 2**63 refused (`int(float(...))` gave 2**63)."""
    from hypershell.core.types import parse_bytes
    assert parse_bytes('9223372036854775807') == 2**63 - 1
    assert parse_bytes('1.5K') == 1536
    with pytest.raises(ValueError):
        parse_bytes('9223372036854775808')


@mark.unit
@mark.parametrize('text, seconds', [('1.5s', 2), ('0.1', 1), ('90m', 5400), ('1:30', 90), ('2147483647', 2**31 - 1)])
def test_parse_time_rounds_up_as_today(text: str, seconds: int) -> None:
    """The duration options round up to whole seconds, as today."""
    from hypershell.core.types import parse_time
    assert parse_time(text) == seconds


@mark.unit
def test_parse_period_floor_and_rounding() -> None:
    """A period under one second reads as one; 0 and below are refused."""
    from hypershell.core.types import parse_period
    assert parse_period('0.5') == 1 and parse_period(0.5) == 1 and parse_period('1h') == 3600
    for value in ('0', 0, -1, '-1'):
        with pytest.raises(ValueError):
            parse_period(value)


@mark.unit
def test_parse_delay_random_form() -> None:
    """A leading `-` on the start delay waits a random time up to that duration; each sign has the ceiling."""
    from hypershell.core.types import parse_delay
    assert parse_delay('-10m') == -600.0 and parse_delay('10m') == 600.0 and parse_delay('1.5') == 1.5
    assert parse_delay('-2147483647') == -float(2**31 - 1)
    for value in ('-2147483648', '2147483648', '--5', 'x'):
        with pytest.raises(ValueError):
            parse_delay(value)


@mark.unit
def test_parse_rate_keeps_whole_words() -> None:
    """A whole rate stays an integer and a decimal one a float, as today; a negative rate is refused."""
    from hypershell.core.types import parse_rate, RATE_MINIMUM
    assert parse_rate('5') == 5 and type(parse_rate('5')) is int
    assert parse_rate('0.5') == 0.5 and parse_rate('0') == 0
    assert RATE_MINIMUM == 1e-6
    with pytest.raises(ValueError):
        parse_rate('-5')


@mark.unit
def test_parse_duration_exact_when_not_whole() -> None:
    """A configuration duration is exact unless whole is asked for; the range applies to the value read."""
    schema = table()
    assert schema.parse_setting('relay.retention', '1.5s', whole=False) == 1.5
    assert schema.parse_setting('relay.retention', '90', whole=False) == 90
    assert schema.parse_setting('relay.retention', '1.5s') == 2


@mark.unit
def test_parse_group_reads_through_table() -> None:
    """The input-side group parser keeps its name, signature and error, and reads the table's bounds."""
    from hypershell.data.model import GROUP_MIN, GROUP_MAX, parse_group, TaskGroupError
    entry = table().SETTINGS['option.group']
    assert (GROUP_MIN, GROUP_MAX) == (entry.low, entry.high)
    assert parse_group('2147483647') == 2**31 - 1
    with pytest.raises(TaskGroupError):
        parse_group('2147483648')


@mark.unit
def test_parse_advised_past_limit_only() -> None:
    """A value past its advisory limit is advised; one at the limit, and 0 for the rate, are not."""
    entries = table().SETTINGS
    assert entries['relay.num_threads'].advised(1025) and not entries['relay.num_threads'].advised(1024)
    assert not entries['relay.num_threads'].advised(0)
    assert entries['server.attempts'].advised(101) and not entries['server.attempts'].advised(100)
    assert entries['option.max_retries'].advised(100) and not entries['option.max_retries'].advised(99)
    assert entries['task.cores'].advised(1025) and not entries['task.cores'].advised(1024)
    assert entries['autoscale.factor'].advised(100.5) and not entries['autoscale.factor'].advised(100)
    assert entries['autoscale.period'].advised(3601) and not entries['autoscale.period'].advised(3600)
    assert entries['client.ratelimit'].advised(0.0009) and not entries['client.ratelimit'].advised(0.001)
    assert not entries['client.ratelimit'].advised(0)
    assert not any(entries[name].advised(entries[name].high) for name in ('server.port', 'task.memory'))


@mark.unit
def test_parse_whole_number_by_length() -> None:
    """A whole number is refused by its digit count before it is converted, whatever its width."""
    schema = table()
    for text in ('9' * 100_000, '-' + '9' * 50, '1' + '0' * 30):
        with pytest.raises(schema.SettingError):
            schema.parse_setting('server.port', text)
    assert schema.parse_setting('server.port', '0' * 100 + '7') == 7


@mark.unit
def test_parse_error_message_is_r4_regex() -> None:
    """Every refusal is one line in the form `<name>: expected <form> from <A> to <B>, given <V>`."""
    schema = table()
    pattern = re.compile(r'^[\w./*-]+: expected .+ from .+ to .+, given .+$')
    for name in schema.SETTINGS:
        with pytest.raises(schema.SettingError) as caught:
            schema.parse_setting(name, 'x')
        assert pattern.fullmatch(str(caught.value)), str(caught.value)
