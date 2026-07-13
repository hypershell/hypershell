# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Utilities for pretty-printing text in various formats."""


# Type annotations
from __future__ import annotations

# Standard libs
import os
import re
import json

# Internal libs
from hypershell.core.types import JSONData, to_json_type

# Public interface
__all__ = ['format_bytes', 'format_tag', 'format_json', 'format_source']


def format_bytes(size: float) -> str:
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


def format_tag(key: str, value: JSONData) -> str:
    """Pretty-print as `key` or `key:value` if not empty string."""
    if isinstance(value, str) and not value:
        return key
    else:
        return f'{key}:{value}'


def format_json(value: JSONData) -> str:
    """Pretty-print value as JSON like while handling quoted arguments."""
    result = json.dumps(to_json_type(value))
    if result.startswith('"') and result.endswith('"'):
        result = result[1:-1]
    return result.replace('\\"', '"')


def format_source(path: str, *, relative: bool = False) -> str:
    """Pretty-print a task source (resolved `Source.path`) for humans.

    Reserved sentinels (``<direct>``/``<stdin>``, matching ``^<.*>$``) pass through
    unchanged. Real filesystem paths are shown as stored (absolute) unless `relative`,
    in which case they are made relative to the current directory. A ``--from-json``
    spec carrying an ``@node`` suffix is opaque and is never relativized.
    """
    if re.match(r'^<.*>$', path):
        return path
    if relative and '@' not in path:
        return os.path.relpath(path)
    return path
