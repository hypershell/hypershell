# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Thread base class implementation."""


# Type annotations
from __future__ import annotations
from typing import Optional, Type

# Standard libs
import threading
from abc import ABC, abstractmethod

# Public interface
__all__ = ['Thread', ]


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
