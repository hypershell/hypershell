# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""UUID generation."""


# Switch to UUID7 is available
try:
    from uuid_utils import uuid7 as _uuid
except ImportError:
    from uuid import uuid4 as _uuid

# Public interface
__all__ = ['uuid', ]


def uuid() -> str:
    """Generate either UUIDv4 or UUIDv7."""
    return str(_uuid())