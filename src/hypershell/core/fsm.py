# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Instrumentation for building finite state machines."""


# Type annotations
from __future__ import annotations
from typing import Dict, Callable, Type

# Standard libs
from enum import Enum
from abc import ABC
# FUZZ: import time
# FUZZ: import random
# PERF: from collections import defaultdict
# PERF: from time import perf_counter

# Internal libs
from hypershell.core.exceptions import write_traceback
from hypershell.core.logging import Logger

# Public interface
__all__ = ['State', 'StateMachine', ]


log = Logger.with_name(__name__)


class State(Enum):
    """Shared base for finite state enums (must have at least HALT)."""


class StateMachine(ABC):
    """Base class for a finite state machine implementation."""

    state: State
    states: Type[State]
    actions: Dict[State, Callable[[], State]]

    __should_halt: bool = False

    # NOTE: Only needed during performance profiling
    # PERF: __perf_counter: float = 0
    # PERF: __perf_data: Dict[int, float]

    def next(self) -> State:
        """Return next state (halt if necessary)."""
        previous_state = self.state
        try:
            if self.__should_halt:
                return self.states.HALT  # noqa: HALT defined in implemented State enums
            else:
                action = self.actions.get(previous_state)
                # PERF: self.__perf_counter = perf_counter()
                next_state = action()
                # PERF: self.__perf_data[previous_state.value] += perf_counter() - self.__perf_counter
        except Exception as error:
            # NOTE: Only non-RuntimeError instances are "unexpected"
            if not isinstance(error, RuntimeError):
                log.critical(f'Uncaught exception from {self.__class__}')
                write_traceback(error, logger=log, module=__name__)
            raise
        else:
            # NOTE: Development aids not typically engaged
            # FUZZ: time.sleep(random.uniform(0, 5))  # FUZZ
            # FUZZ: log.devel(f'{self.__class__.__name__}: {previous_state} -> {next_state}')
            return next_state

    def run(self) -> None:
        """Run machine until state is set to `HALT`."""
        # PERF: self.__perf_data = defaultdict(lambda: 0)
        while self.state is not self.states.HALT:  # noqa: HALT defined in implemented State enums
            self.state = self.next()
        # PERF: time_total = sum(self.__perf_data.values())
        # PERF: for key, value in self.__perf_data.items():
        # PERF:     t = 100 * value / time_total
        # PERF:     log.trace(f'Profiler[{self.__class__.__name__}] {self.states(key).name}: {t:.3f}')

    def halt(self) -> None:
        """Set flag to signal for termination."""
        self.__should_halt = True
