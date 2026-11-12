# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automatic type coercion of input data."""


# Type annotations
from __future__ import annotations
from typing import Final, List, Dict, overload, Union, TypeAlias

# Standard libs
import re
import json
import math
from fractions import Fraction
from argparse import ArgumentTypeError
from datetime import datetime

# Public interface
__all__ = [
    'smart_coerce', 'NoneType', 'JSONData', 'ExtendedValue',
    'to_json_type', 'from_json_type', 'serialize', 'deserialize',
    'parse_bytes', 'parse_time', 'UnitError',
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
    """A size or duration that is not in an accepted form.

    It is a ValueError for callers that parse values directly, and an ArgumentTypeError so that
    argparse shows its message (naming the value and the accepted forms) instead of a generic one.
    """


# The whole value must match: a number, then optionally K, M, G or T, alone or followed by
# B or iB, or a bare B. Every unit is a power of 1024, in any case.
MEMORY_PATTERN: Final[re.Pattern] = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT](?:I?B)?|B)?',
                                               re.IGNORECASE)
MEMORY_SCALES: Final[Dict[str, int]] = {
    'B': 1,
    'KB': 1024,
    'MB': 1024 * 1024,
    'GB': 1024 * 1024 * 1024,
    'TB': 1024 * 1024 * 1024 * 1024,
}
MEMORY_FORMS: Final[str] = ('a number of bytes, optionally with a unit K, M, G or T (powers of 1024), '
                            'alone or followed by B or iB, in any case (e.g., 2048, 512M, 16G, 16GB, 1.5GiB)')


def parse_bytes(value: str) -> int:
    """Parse memory string to integer bytes (e.g., '512MB', '16G', '1.5GiB')."""
    if match := MEMORY_PATTERN.fullmatch(str(value).strip()):
        unit = (match.group('unit') or 'B').upper()
        return int(float(match.group('num')) * MEMORY_SCALES[unit if unit == 'B' else f'{unit[0]}B'])
    else:
        raise UnitError(f'Memory size {value!r} is not valid: expected {MEMORY_FORMS}')


# A duration is a bare number of seconds, unit components in the order d, h, m, s (each at most
# once, whole or fractional, in any case), or one of Slurm's --time forms with a colon or a dash.
TIME_NUMBER: Final[str] = r'\d+(?:\.\d+)?'
TIME_SECONDS_PATTERN: Final[re.Pattern] = re.compile(rf'(?P<s>{TIME_NUMBER})')
TIME_UNITS_PATTERN: Final[re.Pattern] = re.compile(
    rf'(?:(?P<d>{TIME_NUMBER})d)?(?:(?P<h>{TIME_NUMBER})h)?(?:(?P<m>{TIME_NUMBER})m)?(?:(?P<s>{TIME_NUMBER})s)?',
    re.IGNORECASE)
TIME_CLOCK_PATTERN: Final[re.Pattern] = re.compile(r'(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)')
TIME_DAYS_PATTERN: Final[re.Pattern] = re.compile(r'(?P<d>\d+)-(?P<h>\d+)(?::(?P<m>\d+)(?::(?P<s>\d+))?)?')
TIME_SCALES: Final[Dict[str, int]] = {
    's': 1,
    'm': 60,
    'h': 60 * 60,
    'd': 60 * 60 * 24,
}
TIME_FORMS: Final[str] = ('a number of seconds, units d, h, m and s in that order, whole or fractional '
                          '(e.g., 90, 45s, 90m, 1h30m, 1.5h, 1d12h), or a Slurm time MM:SS, HH:MM:SS, D-HH, '
                          'D-HH:MM or D-HH:MM:SS (e.g., 90:00, 1:30:00, 1-12)')


def parse_time(value: str) -> int:
    """Parse duration string to integer seconds, rounded up (e.g., '1h30m', '1.5h', '1:30:00' -> 5400)."""
    text = str(value).strip()
    for pattern in (TIME_SECONDS_PATTERN, TIME_UNITS_PATTERN, TIME_CLOCK_PATTERN, TIME_DAYS_PATTERN):
        if text and (match := pattern.fullmatch(text)):
            return math.ceil(sum(Fraction(num) * TIME_SCALES[unit]
                                 for unit, num in match.groupdict().items() if num is not None))
    raise UnitError(f'Duration {value!r} is not valid: expected {TIME_FORMS}')
