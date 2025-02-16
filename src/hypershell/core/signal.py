# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Signal handling facility."""


# Type annotations
from __future__ import annotations
from typing import Optional, Final, Dict
from types import FrameType

# Standard libs
import platform
from signal import signal as register


# Internal libs
from hypershell.core.logging import Logger

# Public interface
__all__ = ['check_signal', 'RECEIVED', 'SIGNAL_MAP',
           'handler', 'register_handlers', 'register',
           'SIGUSR1', 'SIGUSR2', 'SIGINT', 'SIGTERM', 'SIGKILL']


if platform.system() != 'Windows':
    from signal import SIGUSR1, SIGUSR2, SIGINT, SIGTERM, SIGKILL
else:
    # NOTE:
    # Windows does not provide the signal facility
    # While valid, these stubs have no effect because on Windows we never signal
    SIGUSR1: Final[int] = 30
    SIGUSR2: Final[int] = 31
    SIGINT: Final[int] = 2
    SIGTERM: Final[int] = 15
    SIGKILL: Final[int] = 9


# Initialize logger
log = Logger.with_name(__name__)


# Global signal value set by handler when received
RECEIVED: Optional[int] = None


def check_signal() -> Optional[int]:
    """Check for signal received and return if so."""
    return RECEIVED


SIGNAL_MAP: Final[Dict[int, str]] = {
    SIGUSR1: 'SIGUSR1',
    SIGUSR2: 'SIGUSR2',
    SIGINT:  'SIGINT',
    SIGTERM: 'SIGTERM',
    SIGKILL: 'SIGKILL',
}


def handler(signum: int, frame: Optional[FrameType]) -> None:  # noqa: unused frame
    """Generic handler assigns `signum` to global variable."""
    log.debug(f'Received signal {signum}: {SIGNAL_MAP.get(signum, "???")}')
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
