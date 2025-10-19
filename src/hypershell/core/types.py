# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automatic type coercion of input data."""


# Type annotations
from typing import Final, Dict, TypeVar, Union

# Standard libs
import re
from datetime import datetime

# Public interface
__all__ = ['smart_coerce', 'JSONValue', 'to_json_type', 'from_json_type', 'parse_bytes']


# Each possible input type
JSONValue = TypeVar('JSONValue', bool, int, float, str, type(None))


def smart_coerce(value: str) -> JSONValue:
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


# Extended value type contains datetime types
# These are not valid JSON and must be converted
VT = TypeVar('VT', bool, int, float, str, type(None), datetime)


def to_json_type(value: VT) -> Union[VT, JSONValue]:
    """Convert `value` to alternate representation for JSON."""
    return value if not isinstance(value, datetime) else value.isoformat(sep=' ')


def from_json_type(value: JSONValue) -> Union[JSONValue, VT]:
    """Convert `value` to richer type if possible."""
    try:
        # NOTE: minor detail in PyPy datetime implementation
        if isinstance(value, str) and len(value) > 5:
            return datetime.fromisoformat(value)
        else:
            return value
    except ValueError:
        return value


MEMORY_SCALES: Final[Dict[str, int]] = {
    'B': 1,
    'KB': 1024,
    'MB': 1024 * 1024,
    'GB': 1024 * 1024 * 1024,
    'TB': 1024 * 1024 * 1024 * 1024,
}


MEMORY_PATTERN: re.Pattern = re.compile(r'(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]B)?')


def parse_bytes(value: str) -> int:
    """Parse memory string to integer bytes (e.g., '512MB')."""
    if match := MEMORY_PATTERN.match(value.upper()):
        return int(float(match.group('num')) * MEMORY_SCALES[match.group('unit') or 'B'])
    else:
        raise ValueError(f'Memory string {value!r} is not a valid memory unit')
