# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automatic type coercion of input data."""


# Type annotations
from typing import TypeVar

# Public interface
__all__ = ['smart_coerce', 'JSONValue']


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
