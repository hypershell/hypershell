# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Logging configuration."""


# Type annotations
from __future__ import annotations
from typing import Tuple, Dict, Any, Type

# standard libraries
import sys
import uuid
import socket
import logging
import functools
import datetime

# External libs
from cmdkit.app import exit_status
from cmdkit.config import ConfigurationError
from cmdkit.ansi import Ansi, COLOR_STDERR

# Internal libs
from hypershell.core.config import config, blame
from hypershell.core.exceptions import write_traceback

# Public interface
__all__ = ['Logger', 'HOSTNAME', 'INSTANCE', 'handler', 'initialize_logging', ]


# Cached for later use
HOSTNAME = socket.gethostname()
HOSTNAME_SHORT = HOSTNAME.split('.', 1)[0]


# Unique for every instance of hypershell
INSTANCE = str(uuid.uuid4())


# Canonical colors for logging messages
level_color: Dict[str, Ansi] = {
    'NULL': Ansi.NULL,
    'DEVEL': Ansi.RED,
    'TRACE': Ansi.CYAN,
    'DEBUG': Ansi.BLUE,
    'INFO': Ansi.GREEN,
    'WARNING': Ansi.YELLOW,
    'ERROR': Ansi.RED,
    'CRITICAL': Ansi.MAGENTA
}


TRACE: int = logging.DEBUG - 5
logging.addLevelName(TRACE, 'TRACE')


DEVEL: int = 1
logging.addLevelName(DEVEL, 'DEVEL')


class Logger(logging.Logger):
    """Extend Logger to implement TRACE level."""

    def trace(self, msg: str, *args, **kwargs):
        """Log 'msg % args' with severity 'TRACE'."""
        if self.isEnabledFor(TRACE):
            self._log(TRACE, msg, args, **kwargs)

    def devel(self, msg: str, *args, **kwargs):
        """Log 'msg % args' with severity 'DEVEL'."""
        if self.isEnabledFor(DEVEL):
            self._log(DEVEL, msg, args, **kwargs)

    @classmethod
    def with_name(cls: Type[Logger], name: str) -> Logger:
        """Shorthand for `log: Logger = logging.getLogger(name)`."""
        return logging.getLogger(name)


# inject class back into logging library
logging.setLoggerClass(Logger)


def solve_relative_time(elapsed: float) -> Tuple[float, int, datetime.timedelta, str]:
    """
    Multiple formats of relative time since `elapsed` seconds.
    Returns:
        - Relative time in seconds (i.e., `elapsed`)
        - Relative time in milliseconds
        - Relative time as `datetime.timedelta`
        - Relative time in dd-hh:mm:ss.sss format
    """
    elapsed_ms = int(elapsed * 1000)
    reltime_delta = datetime.timedelta(seconds=elapsed)
    reltime_delta_hours, remainder = divmod(reltime_delta.seconds, 3600)
    reltime_delta_minutes, reltime_delta_seconds = divmod(remainder, 60)
    reltime_delta_milliseconds = int(reltime_delta.microseconds / 1000)
    return (
        elapsed,
        elapsed_ms,
        reltime_delta,
        f'{reltime_delta.days:02d}-{reltime_delta_hours:02d}:{reltime_delta_minutes:02d}:'
        f'{reltime_delta_seconds:02d}.{reltime_delta_milliseconds:03d}'
    )


class LogRecord(logging.LogRecord):
    """Extends LogRecord to include ANSI colors, time formats, and other attributes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Context attributes
        self.app_id = INSTANCE
        self.hostname = HOSTNAME
        self.hostname_short = HOSTNAME_SHORT
        self.relative_name = self.name.split('.', 1)[-1]

        # Formatting attributes
        self.ansi_level = level_color.get(self.levelname, Ansi.NULL).value if COLOR_STDERR else ''
        self.ansi_reset = Ansi.RESET.value if COLOR_STDERR else ''
        self.ansi_bold = Ansi.BOLD.value if COLOR_STDERR else ''
        self.ansi_faint = Ansi.FAINT.value if COLOR_STDERR else ''
        self.ansi_italic = Ansi.ITALIC.value if COLOR_STDERR else ''
        self.ansi_underline = Ansi.UNDERLINE.value if COLOR_STDERR else ''
        self.ansi_black = Ansi.BLACK.value if COLOR_STDERR else ''
        self.ansi_red = Ansi.RED.value if COLOR_STDERR else ''
        self.ansi_green = Ansi.GREEN.value if COLOR_STDERR else ''
        self.ansi_yellow = Ansi.YELLOW.value if COLOR_STDERR else ''
        self.ansi_blue = Ansi.BLUE.value if COLOR_STDERR else ''
        self.ansi_magenta = Ansi.MAGENTA.value if COLOR_STDERR else ''
        self.ansi_cyan = Ansi.CYAN.value if COLOR_STDERR else ''
        self.ansi_white = Ansi.WHITE.value if COLOR_STDERR else ''

        # Timing attributes
        (self.elapsed,
         self.elapsed_ms,
         self.elapsed_delta,
         self.elapsed_hms) = solve_relative_time(self.relativeCreated / 1000)


# inject factory back into logging library
logging.setLogRecordFactory(LogRecord)


class StreamHandler(logging.StreamHandler):
    """A StreamHandler that panics on exceptions in the logging configuration."""

    def handleError(self, record: LogRecord) -> None:
        """Pretty-print message and write traceback to file."""
        err_type, err_val, tb = sys.exc_info()
        write_traceback(err_val, module=__name__)
        sys.exit(exit_status.bad_config)


def level_from_name(name: Any, source: str = 'logging.level') -> int:
    """Get level value from `name`."""
    label = blame(config, *source.split('.'))
    if not isinstance(name, str):
        raise ConfigurationError(f'Expected string for logging level, given \'{name}\' ({label})')
    name = name.upper()
    if name == 'TRACE':
        return TRACE
    elif name == 'DEVEL':
        return DEVEL
    elif name in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
        return getattr(logging, name)
    else:
        raise ConfigurationError(f'Unsupported logging level \'{name}\' ({label})')


try:
    levelname = config.logging.level
    level = level_from_name(levelname)
except Exception as error:
    write_traceback(error, module=__name__)
    sys.exit(exit_status.bad_config)


try:
    handler = StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(config.logging.format,
                          datefmt=config.logging.datefmt)
    )
except Exception as error:
    write_traceback(error, module=__name__)
    sys.exit(exit_status.bad_config)


# null handler for library use
logger = logging.getLogger('hypershell')
logger.setLevel(level)
logger.addHandler(logging.NullHandler())


@functools.cache
def initialize_logging() -> None:
    """Enable logging output to the console and rotating files."""
    logger.addHandler(handler)
