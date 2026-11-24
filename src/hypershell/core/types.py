# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automatic type coercion of input data."""


# Type annotations
from __future__ import annotations
from typing import Any, Final, List, Dict, Optional, Tuple, overload, Union, TypeAlias

# Standard libs
import json
from argparse import Action, ArgumentParser, ArgumentTypeError
from dataclasses import dataclass
from datetime import datetime

# External libs
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.core.config_schema import (SETTINGS, DURATION, SIZE, PERIOD, RATE, DELAY, Range, SettingError,
                                           parse_setting, advise, match_seconds, TIME_FORMS, MEMORY_FORMS,
                                           DELAY_RANDOM_FORM)

# Public interface
__all__ = [
    'smart_coerce', 'NoneType', 'JSONData', 'ExtendedValue',
    'to_json_type', 'from_json_type', 'serialize', 'deserialize',
    'parse_bytes', 'parse_time', 'parse_delay', 'parse_period', 'parse_rate', 'RATE_MINIMUM', 'RATE_FORMS',
    'UnitError', 'RangedOption', 'ranged', 'TIME_FORMS', 'MEMORY_FORMS', 'DELAY_RANDOM_FORM',
]


# Each possible input type
NoneType: TypeAlias = type(None)  # Python 3.9 compatible definition
JSONData: TypeAlias = Union[None, bool, int, float, str, Dict[str, 'JSONData'], List['JSONData']]


# Extended value type that includes datetime (not JSON-serializable)
ExtendedValue: TypeAlias = Union[JSONData, datetime]


def smart_coerce(value: str) -> JSONData:
    """Automatically coerce string to typed value."""
    cmp_val = value.lower()
    if cmp_val in ('null', 'none'):
        return None
    if cmp_val in ('true', 'false'):
        return cmp_val == 'true'
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


@overload
def to_json_type(value: datetime) -> str: ...


@overload
def to_json_type(value: JSONData) -> JSONData: ...


def to_json_type(value: ExtendedValue) -> JSONData:
    """Convert `value` to alternate representation for JSON."""
    return value if not isinstance(value, datetime) else value.isoformat(sep=' ')


def from_json_type(value: JSONData) -> ExtendedValue:
    """Convert basic JSON `value` to a richer type (e.g., datetime) if possible."""
    try:
        # NOTE: minor detail in PyPy datetime implementation
        if isinstance(value, str) and len(value) > 5:
            return datetime.fromisoformat(value)
        else:
            return value
    except ValueError:
        return value


def serialize(data: JSONData) -> bytes:
    """Generic serializer for JSON-serializable data (Python dictionary)."""
    return json.dumps(data).encode('utf-8')


def deserialize(data: bytes) -> JSONData:
    """Generic deserializer for JSON-serializable data (Python dictionary)."""
    return json.loads(data.decode('utf-8'))


class UnitError(ValueError, ArgumentTypeError):
    """A size, duration, period, rate or delay outside its range or not in an accepted form.

    It is a ValueError for callers that parse values directly, and an ArgumentTypeError so that
    argparse shows its message (naming the value, the range and the accepted forms) as written.
    """


# Read by `hypershell.core.config` for its duration keys.
_match_seconds = match_seconds


def _parsed(entry: Range, value: Any, name: str, whole: bool = True) -> Any:
    """`value` read by `entry` with the refusal named `name`, raised as :class:`UnitError`."""
    try:
        return entry.parse(value, name, whole=whole)
    except SettingError as error:
        raise UnitError(str(error)) from None


def _text(value: Any) -> str | int:
    """`value` as text, as the parsers below read it, but an integer as given (it may be too wide for text)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else str(value)


def parse_bytes(value: str | int, name: str = 'size') -> int:
    """Bytes in a size (e.g., '512MB', '16G', '1.5GiB'), exact, within the table's size range; the refusal is named `name`."""
    return _parsed(SIZE, _text(value), name)


def parse_time(value: str | int, name: str = 'duration') -> int:
    """Whole seconds in a duration, rounded up (e.g., '1h30m', '1.5h', '1:30:00' -> 5400), within the table's range.

    The refusal is named `name`.
    """
    return _parsed(DURATION, _text(value), name)


def parse_period(value: str | int | float, name: str = 'autoscale.period') -> int | float:
    """Seconds in a scaling period: a string as parse_time reads it, a number as given; it must be greater than zero.

    A number below one second is one second, as a string below one second rounds up to one;
    a value that is neither a string nor a number is returned as given.
    """
    if not isinstance(value, (str, int, float)):
        return value
    return _parsed(PERIOD, value, name)


RATE_MINIMUM: Final[float] = RATE.least
"""Smallest rate other than 0 a client takes, in tasks per second (one task in about 11.6 days).

A client waits `1 / (rate / threads)` seconds between tasks on each thread, held as a `timedelta`,
which holds at most 86,399,999,913,600 seconds. At this floor the wait overflows only past 86 million
threads, twenty times the most thread ids a Linux host gives a process (4,194,304).
"""

RATE_FORMS: Final[str] = RATE.bounds
"""The range a rate is refused in favour of."""


def parse_rate(value: str, name: str = 'rate') -> int | float:
    """Tasks per second: a whole or decimal number (e.g., '5', '0.5'); a whole word stays an integer.

    It is 0 (no limit), or from RATE_MINIMUM to 1,000,000,000.
    """
    return _parsed(RATE, _text(value), name)


def parse_delay(value: str, name: str = 'duration') -> float:
    """Parse start-up delay to seconds, unrounded; a leading '-' marks the random form ('-10m' -> -600.0).

    A value not in a duration's form is refused with the message the duration gives for the same
    text, then the random form.
    """
    try:
        return float(DELAY.parse(_text(value), name))
    except SettingError as error:
        if error.out_of_range:
            raise UnitError(f'{error}; {DELAY_RANDOM_FORM}') from None
        message = DURATION.refusal(name, error.value, out_of_range=False)
        raise UnitError(f'{message}; {DELAY_RANDOM_FORM}') from None


@dataclass(frozen=True)
class RangedOption:
    """The type of a numeric option: reads its text by the entry `setting` of the table, named `name`.

    A value the entry refuses stops the command with the one refusal line (cmdkit's `ArgumentError`,
    printed as written, exit 2); a value past the entry's advisory limit is taken with one warning.
    """

    setting: str
    name: str

    def __call__(self: RangedOption, text: str) -> Any:
        """The option's value read from `text`."""
        try:
            value = parse_setting(self.setting, text, name=self.name)
        except SettingError as error:
            raise ArgumentError(str(error)) from None
        advise(self.setting, value, self.name, given=text)
        return value

    @property
    def entry(self: RangedOption) -> Range:
        """The option's entry in the table."""
        return SETTINGS[self.setting]


def ranged(interface: ArgumentParser, *flags: str, setting: str, hidden: Tuple[str, ...] = (), **options: Any) -> Action:
    """Declare a numeric option of `interface` that reads through the entry `setting` of the table.

    The option is named in a refusal by its `flags` joined with `/`, less any `hidden` alias.
    """
    name = '/'.join(flag for flag in flags if flag not in hidden)
    return interface.add_argument(*flags, type=RangedOption(setting, name), **options)
