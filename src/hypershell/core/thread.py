# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Thread base class implementation."""


# Type annotations
from __future__ import annotations
from typing import Optional, Type, Final

# Standard libs
import threading
from abc import ABC, abstractmethod

# Public interface
__all__ = ['Thread', 'join_waking', 'WAKE_INTERVAL']


WAKE_INTERVAL: Final[float] = 0.5
"""Seconds between wakes of a main thread waiting on its worker, so a signal handler tripped by any thread runs."""


class Thread(threading.Thread, ABC):
    """Extends threading.Thread to provide exception handling."""

    __exception: Exception = None
    __should_halt: bool = False

    def __init__(self: Thread, name: str) -> None:
        super().__init__(name=name, daemon=True)

    @abstractmethod
    def run_with_exceptions(self: Thread) -> None:
        """Implement `run` which may raise exceptions."""

    def run(self: Thread) -> None:
        """Call `run_with_exceptions` within a try/except block."""
        try:
            self.run_with_exceptions()
        except Exception as exc:
            self.__exception = exc

    @classmethod
    def new(cls: Type[Thread], *args, **kwargs) -> Thread:
        """Initialize and start the thread."""
        thread = cls(*args, **kwargs)
        thread.start()
        return thread

    def stop(self: Thread, wait: bool = False, timeout: int = None) -> None:
        """Signal to terminate."""
        self.__should_halt = True
        if wait:
            self.join(timeout=timeout)

    def join(self: Thread, timeout: Optional[float] = None) -> None:
        """Calls Thread.join but re-raises exceptions."""
        super().join(timeout=timeout)
        if self.__exception:
            raise self.__exception


def join_waking(thread: threading.Thread, interval: float = WAKE_INTERVAL) -> None:
    """Join `thread`, returning to the interpreter every `interval` seconds until it ends.

    CPython runs a signal handler only on the main thread, and only once that thread runs bytecode
    again; a signal taken by another thread does not wake a main thread asleep in an untimed join.
    Waiting in bounded steps lets the handler run within `interval`, whichever thread took the signal.
    The last step is a plain `join`, so a :class:`Thread` re-raises the failure that ended it.
    """
    while thread.is_alive():
        thread.join(timeout=interval)
    thread.join()
