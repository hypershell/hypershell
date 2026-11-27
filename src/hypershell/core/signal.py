# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Signal handling facility."""


# Type annotations
from __future__ import annotations
from typing import Optional, Final, Dict
from types import FrameType

# Standard libs
import platform
import logging  # Use standard library to lazily acquire logger
from signal import signal as register, Signals


# Public interface
__all__ = ['check_signal', 'reset_signal',
           'handler', 'register_handlers', 'register', 'SIGNAL_MAP',
           'SIGNAL_EXIT_STATUS', 'exit_status_for_signal',
           'SIGUSR1', 'SIGUSR2', 'SIGINT', 'SIGTERM', 'SIGKILL', 'SIGHUP']


if platform.system() != 'Windows':
    from signal import SIGUSR1, SIGUSR2, SIGINT, SIGTERM, SIGKILL, SIGHUP
else:
    # NOTE:
    # Windows does not provide the signal facility
    # While valid, these stubs have no effect because on Windows we never signal
    SIGUSR1: Final[int] = 30
    SIGUSR2: Final[int] = 31
    SIGINT: Final[int] = 2
    SIGTERM: Final[int] = 15
    SIGKILL: Final[int] = 9
    SIGHUP: Final[int] = 1




# Global signal value set by handler when received
RECEIVED: Optional[int] = None


def check_signal() -> Optional[int]:
    """Check for signal received and return if so."""
    return RECEIVED


def reset_signal() -> None:
    """Reset signal received flag."""
    global RECEIVED
    RECEIVED = None


SIGNAL_MAP: Final[Dict[int, str]] = {
    SIGUSR1: 'SIGUSR1',
    SIGUSR2: 'SIGUSR2',
    SIGINT:  'SIGINT',
    SIGTERM: 'SIGTERM',
    SIGKILL: 'SIGKILL',
    SIGHUP:  'SIGHUP',
}


SIGNAL_EXIT_STATUS: Final[Dict[str, int]] = {
    name: -int(member) for name, member in Signals.__members__.items()
}
"""
Map of signal name (e.g. ``SIGTERM``) to the `exit_status` recorded for a task killed by it.

When a task's process is terminated by signal N, Python's `subprocess` reports a return code of
``-N`` (negative), which HyperShell stores verbatim as ``exit_status``. This is why the cancel
sentinel (-1) coincides with a SIGHUP death and internal sentinels must stay clear of ``-1..-64``.
"""


def exit_status_for_signal(name: str) -> int:
    """
    Resolve a signal `name` (case-insensitive, optional ``SIG`` prefix) to its `exit_status`.

    Example:
        >>> exit_status_for_signal('TERM')
        -15
        >>> exit_status_for_signal('sigkill')
        -9
    """
    key = name.strip().upper()
    key = key if key.startswith('SIG') else f'SIG{key}'
    try:
        return SIGNAL_EXIT_STATUS[key]
    except KeyError:
        raise ValueError(f'Unknown signal name: {name!r}') from None


def handler(signum: int, frame: Optional[FrameType]) -> None:  # noqa: unused frame
    """Generic handler assigns `signum` to global variable."""
    logging.getLogger(__name__).debug(f'Received signal {signum}: {SIGNAL_MAP.get(signum, "???")}')
    global RECEIVED
    RECEIVED = signum


if platform.system() == 'Windows':

    def register_handlers() -> None:
        """Empty function does nothing on Windows."""
        pass

else:

    def register_handlers() -> None:
        """Register signal handlers for client."""
        register(SIGUSR1, handler)
        register(SIGUSR2, handler)
        register(SIGHUP, handler)
