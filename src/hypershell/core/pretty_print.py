# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Utilities for pretty-printing text in various formats."""


# Type annotations
from __future__ import annotations

# Standard libs
import json

# Internal libs
from hypershell.core.types import JSONValue, to_json_type

# Public interface
__all__ = ['format_bytes', 'format_tag', 'format_json']


def format_bytes(size: int) -> str:
    """Pretty-print size in bytes."""
    for u in ['', 'K', 'M', 'G', 'T']:
        if abs(size) < 1000:
            if abs(size) < 100:
                if abs(size) < 10:
                    return f'{size:1.2f}{u}B'
                return f'{size:2.1f}{u}B'
            return f'{size:3.0f}{u}B'
        size /= 1024
    else:
        return f'{size:3.1f}PB'


def format_tag(key: str, value: JSONValue) -> str:
    """Pretty-print as `key` or `key:value` if not empty string."""
    if isinstance(value, str) and not value:
        return key
    else:
        return f'{key}:{value}'


def format_json(value: JSONValue) -> str:
    """Pretty-print value as JSON-like while handling quoted arguments."""
    result = json.dumps(to_json_type(value))
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    return result.replace('\\"', '"')
