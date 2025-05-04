# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Utilities for pretty-printing text in various formats."""


# Type annotations
from __future__ import annotations

# Public interface
__all__ = ['format_bytes', ]


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
