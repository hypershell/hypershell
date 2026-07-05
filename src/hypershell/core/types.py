# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automatic type coercion of input data."""


# Type annotations
from __future__ import annotations
from typing import Final, List, Dict, overload, Union, TypeAlias

# Standard libs
import re
import json
from datetime import datetime

# Public interface
__all__ = [
    'smart_coerce', 'NoneType', 'JSONData', 'ExtendedValue',
    'to_json_type', 'from_json_type', 'serialize', 'deserialize',
    'parse_bytes', 'parse_time',
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


MEMORY_PATTERN: re.Pattern = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]B)?')
MEMORY_SCALES: Final[Dict[str, int]] = {
    'B': 1,
    'KB': 1024,
    'MB': 1024 * 1024,
    'GB': 1024 * 1024 * 1024,
    'TB': 1024 * 1024 * 1024 * 1024,
}


def parse_bytes(value: str) -> int:
    """Parse memory string to integer bytes (e.g., '512MB')."""
    if match := MEMORY_PATTERN.match(value.upper()):
        return int(float(match.group('num')) * MEMORY_SCALES[match.group('unit') or 'B'])
    else:
        raise ValueError(f'Memory string {value!r} is not a valid memory unit')


TIME_PATTERN: Final[re.Pattern] = re.compile(r'(?P<num>\d+)\s*(?P<unit>[SMHDW])')
TIME_SCALES: Final[Dict[str, int]] = {
    'S': 1,
    'M': 60,
    'H': 60 * 60,
    'D': 60 * 60 * 24,
    'W': 60 * 60 * 24 * 7,
}


def parse_time(value: str) -> int:
    """Parse time string to integer seconds (e.g., '10H' -> 36000)."""
    if match := TIME_PATTERN.match(value.upper()):
        return int(float(match.group('num')) * TIME_SCALES[match.group('unit')])
    else:
        raise ValueError(f'Time string {value!r} is not a valid time unit')
