# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Logging configuration and handling."""


# Type annotations
from __future__ import annotations
from typing import Tuple, Dict, Any, Type, Final, Optional, List, Callable, TypeAlias
from types import ModuleType

# standard libraries
import os
import re
import sys
import socket
import importlib
from datetime import datetime, timedelta
from abc import abstractmethod, ABC
from shutil import copyfileobj
from threading import Thread, Lock
from queue import Queue
from logging.handlers import QueueHandler as _QueueHandler, QueueListener
from logging import (
    Formatter, NullHandler, FileHandler, StreamHandler as _StreamHandler, Logger as _Logger, LogRecord as _LogRecord,
    DEBUG, INFO, WARNING, ERROR, CRITICAL, NOTSET,
    getLogger, setLoggerClass, setLogRecordFactory, addLevelName
)

# External libs
from cmdkit.app import exit_status
from cmdkit.config import Namespace, ConfigurationError
from cmdkit.ansi import Ansi, COLOR_STDERR

# Internal libs
from hypershell.core.uuid import uuid
from hypershell.core.types import parse_bytes
from hypershell.core.platform import default_path
from hypershell.core.signal import check_signal, reset_signal, SIGHUP
from hypershell.core.exceptions import write_traceback
from hypershell.core.config import (
    config, default as default_config, blame,
    PARAM_UNSET, LOGGING_STYLES, DEFAULT_LOGGING_STYLE, DEFAULT_LOGGING_LEVEL
)

# Public interface
__all__ = ['Logger', 'HOSTNAME', 'INSTANCE', 'initialize_logging', 'DEFAULT_LOGGING_LEVEL',
           'role_from_command', 'default_file_for', 'claim_file_slot']


# Cached for later use
HOSTNAME: Final[str] = socket.gethostname()
HOSTNAME_SHORT: Final[str] = HOSTNAME.split('.', 1)[0]


# Unique for every instance of hypershell
INSTANCE: Final[str] = str(uuid())


TRACE: Final[int] = DEBUG - 5
addLevelName(TRACE, 'TRACE')


DEVEL: Final[int] = 1
addLevelName(DEVEL, 'DEVEL')


# Canonical colors for logging messages
COLOR_MAPPING: Final[Dict[str, Ansi]] = {
    'NULL': Ansi.NULL,
    'DEVEL': Ansi.RED,
    'TRACE': Ansi.CYAN,
    'DEBUG': Ansi.BLUE,
    'INFO': Ansi.GREEN,
    'WARNING': Ansi.YELLOW,
    'ERROR': Ansi.RED,
    'CRITICAL': Ansi.MAGENTA
}


# Re-index for programmatic use
LEVEL_MAPPING: Final[Dict[str, int]] = {
    'NOTSET': NOTSET,
    'DEVEL': DEVEL,
    'TRACE': TRACE,
    'DEBUG': DEBUG,
    'INFO': INFO,
    'WARNING': WARNING,
    'ERROR': ERROR,
    'CRITICAL': CRITICAL
}


class Logger(_Logger):
    """Extend Logger to implement TRACE and DEVEL level."""

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
        return getLogger(name)


# Inject class back into logging library
setLoggerClass(Logger)


def solve_relative_time(elapsed: float) -> Tuple[float, int, timedelta, str]:
    """
    Multiple formats of relative time since `elapsed` seconds.
    Returns:
        - Relative time in seconds (i.e., `elapsed`)
        - Relative time in milliseconds
        - Relative time as `datetime.timedelta`
        - Relative time in dd-hh:mm:ss.sss format
    """
    elapsed_ms = int(elapsed * 1000)
    reltime_delta = timedelta(seconds=elapsed)
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


class LogRecord(_LogRecord):
    """Extends LogRecord to include ANSI colors, time formats, and other attributes."""

    def __init__(self, *args, **kwargs) -> None:
        """Initialize with additional attributes."""

        super().__init__(*args, **kwargs)

        # Context attributes
        self.app_id = INSTANCE
        self.hostname = HOSTNAME
        self.hostname_short = HOSTNAME_SHORT
        self.relative_name = self.name.split('.', 1)[-1]

        # Formatting attributes
        self.ansi_level = COLOR_MAPPING.get(self.levelname, Ansi.NULL).value if COLOR_STDERR else ''
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


# Inject factory back into logging library
setLogRecordFactory(LogRecord)


# We don't actually find it good to have the logging messages just go missing if misconfigured.
# So we're overriding the StreamHandler to panic on exceptions.
class StreamHandler(_StreamHandler):
    """A StreamHandler that panics on exceptions in the logging configuration."""

    def handleError(self, record: LogRecord) -> None:
        """Pretty-print message and write traceback to file."""
        err_type, err_val, tb = sys.exc_info()
        write_traceback(err_val, module=__name__)
        sys.exit(exit_status.bad_config)


# Module name for implementation and file extension for each compression format
COMPRESSION_MAPPING: Final[Dict[str, Tuple[Optional[str], str]]] = {
    'none': (None, ''),
    'gzip': ('gzip', '.gz'),
    'bzip': ('bz2', '.bz2',),
    'lzma': ('lzma', '.xz'),
    'zstd': ('zstandard', '.zstd'),
}

COMPRESSION_MODE: Optional[str] = None
COMPRESSION_IMPL: Optional[ModuleType] = None
COMPRESSION_EXT: str = ''
COMPRESSION_EXT_OFFSET: int = -1  # Where is the "unique" segment in the filename: -2 if .gz or similar


def set_compression(mode: Optional[str]) -> None:
    """Set global compression mode."""
    global COMPRESSION_MODE, COMPRESSION_IMPL, COMPRESSION_EXT, COMPRESSION_EXT_OFFSET
    if mode is None:
        COMPRESSION_MODE = None
        COMPRESSION_IMPL = None
        COMPRESSION_EXT = ''
        COMPRESSION_EXT_OFFSET = -1
        return
    try:
        module_name, ext = COMPRESSION_MAPPING[mode]
        COMPRESSION_MODE = mode
        COMPRESSION_EXT = ext
        COMPRESSION_EXT_OFFSET = -2
    except KeyError:
        raise ConfigurationError(f'Unsupported compression method \'{mode}\'')
    try:
        COMPRESSION_IMPL = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(f'Missing optional dependency \'{module_name}\' needed for compression') from exc


def re_pattern_count(prefix: str) -> re.Pattern:
    """Return compiled regular expression pattern for count-like rotated files."""
    return re.compile(re.escape(prefix) + r'\.([0-9]+)' + re.escape(COMPRESSION_EXT))


def re_pattern_date(prefix: str) -> re.Pattern:
    """Return compiled regular expression pattern for date-like rotated files."""
    return re.compile(re.escape(prefix) + r'\.([0-9]{8})' + re.escape(COMPRESSION_EXT))


def re_pattern_datetime(prefix: str) -> re.Pattern:
    """Return compiled regular expression pattern for datetime-like rotated files."""
    return re.compile(re.escape(prefix) + r'.([0-9]{8}-[0-9]{6})' + re.escape(COMPRESSION_EXT))


def sorting_key_count(fn: str) -> int | str:
    """Sorting key for count-like rotated files."""
    return int(fn.split('.')[COMPRESSION_EXT_OFFSET])  # Sort on integer values (2 before 10)


def sorting_key_date(fn: str) -> int | str:
    """Sorting key for date-like rotated files."""
    return int(fn.split('.')[COMPRESSION_EXT_OFFSET])  # numeric date


def sorting_key_datetime(fn: str) -> int | str:
    """Sorting key for datetime-like rotated files."""
    return fn.split('.')[COMPRESSION_EXT_OFFSET]  # alpha-numeric datetime


def next_filename_count(log_file: str) -> str:
    """Return next filename for count-like rotated files (without compression extension)."""
    previous_filenames = search_files(log_file)
    prefix = basename_without_ext(log_file)
    next_count = 1 if not previous_filenames else sorting_key_count(previous_filenames[-1]) + 1
    return os.path.join(os.path.dirname(log_file), f'{prefix}.{next_count}')


def next_filename_date(log_file: str) -> str:
    """Return next filename for date-like rotated files (without compression extension)."""
    prefix = basename_without_ext(log_file)
    return os.path.join(os.path.dirname(log_file), f'{prefix}.{datetime.now().strftime("%Y%m%d")}')


def next_filename_datetime(log_file: str) -> str:
    """Return next filename for datetime-like rotated files (without compression extension)."""
    prefix = basename_without_ext(log_file)
    return os.path.join(os.path.dirname(log_file), f'{prefix}.{datetime.now().strftime("%Y%m%d-%H%M%S")}')


RePattern: TypeAlias = Callable[[str, ], re.Pattern]
SortingKey: TypeAlias = Callable[[str, ], int | str]
NamingPolicy: TypeAlias = Callable[[str, ], str]

FILE_NAMING_POLICY: str = 'count'
FILE_NAMING_POLICY_MAPPING: Final[Dict[str, Tuple[RePattern, SortingKey, NamingPolicy]]] = {
    'count': (re_pattern_count, sorting_key_count, next_filename_count),
    'date': (re_pattern_date, sorting_key_date, next_filename_date),
    'datetime': (re_pattern_datetime, sorting_key_datetime, next_filename_datetime),
}

# Only the following rotation policies allow for 'date' naming
DATE_ELIGIBLE: Final[List[str]] = ['@daily', '@midnight']


def set_naming_policy(policy: str) -> None:
    """Set global naming policy for rotated log files."""
    global FILE_NAMING_POLICY
    policy = str(policy).lower()
    if policy not in FILE_NAMING_POLICY_MAPPING:
        raise ConfigurationError(f'Unsupported file naming policy \'{policy}\'')
    FILE_NAMING_POLICY = policy


def basename_without_ext(fn: str) -> str:
    """Return basename of file with the last extension removed."""
    return os.path.splitext(os.path.basename(fn))[0]


def search_files(log_file: str) -> List[str]:
    """Return sorted list of previous rotated files corresponding to the naming policy."""
    dirname = os.path.dirname(log_file)
    prefix = basename_without_ext(log_file)
    pattern, sorting_key, _ = FILE_NAMING_POLICY_MAPPING[FILE_NAMING_POLICY]
    paths = [os.path.join(dirname, name) for name in os.listdir(dirname)
             if pattern(prefix).fullmatch(os.path.basename(name))]
    return sorted(paths, key=sorting_key)


# Number of uncompressed previous rotations to keep on disk
# We never delete files unless both rotation and compression are enabled
DEFAULT_BACKUP_COUNT: Final[int] = default_config.logging.file.keep
BACKUP_COUNT: int = DEFAULT_BACKUP_COUNT


def set_retention_count(count: int) -> None:
    """Set global retention count for previous log files."""
    global BACKUP_COUNT
    count = int(count)
    if count < 0:
        raise ConfigurationError(f'Invalid file retention count \'{count}\'')
    BACKUP_COUNT = count


def files_eligible_for_deletion(log_file: str) -> List[str]:
    """Discover previous uncompressed files based on `filename` and naming policy eligible for deletion."""
    if COMPRESSION_MODE is None:
        return []
    dirname = os.path.dirname(log_file)
    uncompressed_files = [
        os.path.join(dirname, basename_without_ext(path))
        for path in search_files(log_file)
    ]
    uncompressed_files = [fp for fp in uncompressed_files if os.path.isfile(fp)]
    total = len(uncompressed_files)
    return [] if total <= BACKUP_COUNT else uncompressed_files[:total-BACKUP_COUNT]


def next_filename(log_file: str) -> str:
    """Derive the next filename for log rotation based on previous `filename`."""
    _, _, name_impl = FILE_NAMING_POLICY_MAPPING[FILE_NAMING_POLICY]
    return name_impl(log_file)


# We need a separate lock for rotation because we call .close() which already has the internal lock.
FILE_LOCK: Lock = Lock()


# We never rotate the log file unless configured otherwise
ROTATE_NEVER: Final[str] = 'never'
DEFAULT_ROTATION_INTERVAL: Final[str] = ROTATE_NEVER


# We don't find the builtin rotating file handler meets our needs, so we build it from scratch.
# The base FileHandler has the open and write features and will re-open after a call to .close().
class RotatingFileHandler(FileHandler, ABC):
    """FileHandler that rotates log files according to some policy."""

    filename: str
    interval: str = DEFAULT_ROTATION_INTERVAL
    prev_rotation: Optional[datetime] = None
    next_rotation: Optional[datetime] = None

    msg: str = ''

    def __init__(self: RotatingFileHandler, filename: str, interval: str = DEFAULT_ROTATION_INTERVAL) -> None:
        """Initialize with filename and policy."""
        super().__init__(filename, encoding='utf-8', mode='a', delay=True)
        self.filename = filename
        self.interval = interval
        self.prev_rotation = datetime.now()
        self.next_rotation = None
        self.reset_interval()

    @abstractmethod
    def should_rotate(self: RotatingFileHandler) -> bool:
        """Determine whether to rotate log file based on log message."""
        raise NotImplementedError()

    @abstractmethod
    def reset_interval(self: RotatingFileHandler) -> None:
        """Reset the interval for the next rotation."""
        raise NotImplementedError()

    def update_interval(self: RotatingFileHandler) -> None:
        """Update based on latest record to be emitted."""
        pass

    def rotate(self: RotatingFileHandler) -> None:
        """Rotate and compress log file."""
        with FILE_LOCK:
            self.close()
            new_filename = next_filename(self.filename)
            os.rename(self.filename, new_filename)
            compression_jobs.put_nowait(new_filename)
            self.reset_interval()

    def emit(self: RotatingFileHandler, record: LogRecord) -> None:
        """Emit a record an optionally rotate the log file."""
        super().emit(record)
        self.update_interval()
        if SIGHUP == check_signal():
            reset_signal()
            self.rotate()
        elif self.should_rotate():
            self.rotate()

    def format(self: RotatingFileHandler, record: LogRecord) -> str:
        """Wraps call to formatter to cache result for efficiency."""
        self.msg = super().format(record)
        return self.msg

    def handleError(self, record: LogRecord) -> None:
        """Pretty-print message and write traceback to file."""
        err_type, err_val, tb = sys.exc_info()
        write_traceback(err_val, module=__name__)
        sys.exit(exit_status.bad_config)


class TimedRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler that rotates files with a time-like policy."""

    def reset_interval(self: TimedRotatingFileHandler) -> None:
        """Reset the interval between rotations."""
        self.prev_rotation = self.next_rotation or datetime.now()
        if self.interval != ROTATE_NEVER:
            from croniter import croniter
            self.next_rotation = croniter(self.interval, self.prev_rotation).get_next(datetime)

    def should_rotate(self: TimedRotatingFileHandler) -> bool:
        """Determine whether to rotate log file based on log message and time policy."""
        return self.interval != ROTATE_NEVER and datetime.now() >= self.next_rotation


# A terminating '\n' is added to formatted messages emitted to the stream which we need to
# account for when counting bytes for our rotation interval
BYTE_COUNTER_OFFSET: Final[int] = len(_StreamHandler.terminator)


class SizeRotatingFileHandler(RotatingFileHandler):
    """A RotatingFileHandler that rotates files based on size."""

    count_interval: int = 0
    count_bytes: int = 0

    def reset_interval(self: SizeRotatingFileHandler) -> None:
        """Reset the interval between rotations."""
        self.count_interval = parse_bytes(self.interval)
        self.count_bytes = 0 if not os.path.exists(self.filename) else os.path.getsize(self.filename)

    def update_interval(self: SizeRotatingFileHandler) -> None:
        """Increment bytes emitted from previous message."""
        self.count_bytes += len(self.msg) + BYTE_COUNTER_OFFSET

    def should_rotate(self: SizeRotatingFileHandler) -> bool:
        """Determine whether to rotate log file based on log message and size policy."""
        return self.count_bytes >= self.count_interval


# Flag to prevent deletion of uncompressed files until recovery phase is completed
DELAY_BACKUP_DELETION: bool = False


def compress_file(filename: str) -> None:
    """Compress file and remove the previously uncompressed file."""
    if COMPRESSION_IMPL is None:
        return
    log = getLogger(__name__)
    output_filename = filename + COMPRESSION_EXT
    log.info(f'Compressing {filename}[{COMPRESSION_EXT}]')
    try:
        with (open(filename, mode='rb') as in_stream,
              COMPRESSION_IMPL.open(output_filename + '.partial', mode='wb') as out_stream):
            copyfileobj(in_stream, out_stream)
        os.rename(output_filename + '.partial', output_filename)
    except Exception as exc:
        log.error(f'Failed to compress {filename}: {exc}')
        return
    if not DELAY_BACKUP_DELETION:
        for prev_filename in files_eligible_for_deletion(filename):
            os.remove(prev_filename)
            log.info(f'Removed previous file ({prev_filename})')


def background_compression(jobs: Queue[str | bool | None]) -> None:
    """Background thread to compress files in queue."""
    global DELAY_BACKUP_DELETION
    for job in iter(jobs.get, None):
        if isinstance(job, str):
            compress_file(job)
            jobs.task_done()
        elif isinstance(job, bool):
            DELAY_BACKUP_DELETION = job
        else:
            getLogger(__name__).error(f'Unexpected job type: {type(job)}')


# Program may have halted in the middle of compressing a rotated log file.
# At program start we trigger this function to attempt re-queueing of these files.
# We only consider .partial files belonging to *this* process's slot (matched by
# filename prefix) so a starting process never touches a live sibling's partials,
# and only those matching the current compression and naming policy. All other
# files ending in .partial that belong to our slot are flagged as errors.
def recover_interrupted_compression(log_file: str) -> None:
    """Recover interrupted compression jobs for this process's log slot."""
    global DELAY_BACKUP_DELETION
    log = getLogger(__name__)
    log_dir = os.path.dirname(log_file) or '.'
    prefix = basename_without_ext(log_file) + '.'  # e.g. 'client-node01.'
    for fn in os.listdir(log_dir):
        if not fn.startswith(prefix):
            continue  # Skip other roles/hosts/slots sharing this directory
        fp = os.path.join(log_dir, fn)
        if fn.endswith('.partial'):
            c_ext = fn.split('.')[-2]  # Compression type
            if f'.{c_ext}' != COMPRESSION_EXT:
                log.error(f'Unexpected partial \'{fp}\' (mismatch .{c_ext}:={COMPRESSION_EXT})')
                continue
            orig_fp = os.path.join(log_dir, basename_without_ext(basename_without_ext(fn)))
            if os.path.isfile(orig_fp):
                log.info(f'Re-queuing interrupted compression: {fp}')
                compression_jobs.put_nowait(orig_fp)
            else:
                log.error(f'Missing original file for partially compressed \'{fp}\'')


# Background job queue for compressing files (filepath, method, level)
compression_jobs: Queue[str | bool | None] = Queue(maxsize=2)  # We should not be queueing frequently
compression_thread = Thread(target=background_compression, args=(compression_jobs,), daemon=True)


class FastQueueHandler(_QueueHandler):
    """QueueHandler that doesn't pre-format records."""

    def prepare(self, record):
        """Don't format - just pass the record through."""
        return record


def level_from_name(name: Any, source: str = 'logging.level') -> int:
    """Get level value from `name`."""
    label = blame(config, *source.split('.'))
    if not isinstance(name, str):
        raise ConfigurationError(f'Expected string for \'{source}\', given \'{name}\' ({label})')
    try:
        return LEVEL_MAPPING[name.upper()]
    except KeyError:
        raise ConfigurationError(f'Unsupported logging level \'{name}\' ({label})')


def format_from_style(name: Any, source: str = 'logging.style') -> str:
    """Get style value from `name`."""
    label = blame(config, *source.split('.'))
    if not isinstance(name, str):
        raise ConfigurationError(f'Expected string for \'{source}\', given \'{name}\' ({label})')
    try:
        return LOGGING_STYLES[name.lower()]['format']
    except KeyError:
        raise ConfigurationError(f'Unsupported logging style \'{name}\' ({label})')


# We want to keep the rich ANSI color sequences in the stream handler but disable them in the file handler.
# So we patch the format strings from the STYLE_MAPPING when we set the formatter.
# NOTE: the default 'system' format used for file handlers does not contain such ANSI sequencies
ANSI_PATTERN = re.compile(r'%\(ansi_\w+\)s')

def strip_ansi_format(format_string: str) -> str:
    """Remove ANSI escape sequence placeholders from format string."""
    return ANSI_PATTERN.sub('', format_string)


def panic(_msg: str) -> None:
    """Log critical error and exit on bad config."""
    critical(_msg)
    sys.exit(exit_status.bad_config)


def _set_compression(policy: str) -> None:
    """Local wrapper to catch exception."""
    _label = blame(config, 'logging', 'file', 'compress')
    try:
        set_compression(policy)
    except Exception as exc:
        panic(f'{exc.__class__.__name__}: {exc} ({_label})')


def _set_retention(backup_count: int) -> None:
    """Local wrapper to catch exception."""
    _label = blame(config, 'logging', 'file', 'keep')
    try:
        set_retention_count(backup_count)
    except Exception as exc:
        panic(f'{exc.__class__.__name__}: {exc} ({_label})')


# Null handler for library use
logger = getLogger('hypershell')
logger.addHandler(NullHandler())


# Local shorthand for methods setup process
warn = getLogger(__name__).warning
debug = getLogger(__name__).debug
critical = getLogger(__name__).critical


try:
    stream_config = Namespace(config.logging.copy())
    datefmt = stream_config.pop('datefmt', config.logging.datefmt)
    stream_level = level_from_name(stream_config.pop('level', DEFAULT_LOGGING_LEVEL), 'logging.level')
    stream_color = stream_config.pop('color', config.logging.color)
    stream_format = format_from_style(stream_config.pop('style', DEFAULT_LOGGING_STYLE), 'logging.style')
    stream_format = stream_config.pop('format', stream_format)
    stream_format = stream_format if stream_color else strip_ansi_format(stream_format)
    stream_handler = StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(Formatter(stream_format, datefmt=datefmt))
    stream_handler.setLevel(stream_level)
except Exception as exc:
    panic(f'{exc.__class__.__name__}: {exc}')


# These are not necessarily initialized if used in library-mode
message_queue: Queue[LogRecord] = Queue(maxsize=-1)  # No maximum size to avoid disk I/O bottleneck
queue_listener: Optional[QueueListener] = None
queue_handler: Optional[FastQueueHandler] = None
file_handler: Optional[RotatingFileHandler] = None


# File-based logging assumes a single live writer per file: the handler renames,
# compresses, and prunes files out from under anyone else sharing the path. In a
# distributed cluster many processes would otherwise resolve the same default and
# clobber each other. So long-running / distributed roles each get their own
# per-process, host-scoped file; every other command (and library use) shares the
# 'main' role. The role is declared by the entry point (see `role_from_command`),
# never sniffed from `sys.argv`, so bare invocations and library imports are safe.
DEFAULT_ROLE: Final[str] = 'main'
DISTRIBUTED_ROLES: Final[frozenset] = frozenset({'server', 'cluster', 'client', 'submit'})


def role_from_command(command: Optional[str]) -> str:
    """Map a top-level CLI command to a logging role (see `default_file_for`)."""
    return command if command in DISTRIBUTED_ROLES else DEFAULT_ROLE


def default_file_for(role: str) -> str:
    """Default file-logging path for a process `role` (host-scoped except 'main')."""
    stem = DEFAULT_ROLE if role == DEFAULT_ROLE else f'{role}-{HOSTNAME_SHORT}'
    return os.path.join(default_path.log, f'{stem}.log')


# We enforce the single-writer invariant with an advisory lock on a per-file
# '.lock' sidecar, held (by the OS, so it releases even on a crash) for the life
# of the process. Contention is only ever same-host because the hostname is baked
# into the filename, and same-host advisory locks are reliable even on shared
# network filesystems. Contended slots fall through to 'name-2.log', 'name-3.log',
# ..., bounding the file count by peak concurrency on a host rather than by the
# total number of processes ever launched (which matters under autoscaling).
try:
    import fcntl
    def _try_lock(path: str) -> Optional[Any]:
        """Acquire an exclusive, non-blocking lock; return the held handle or None."""
        handle = open(path, mode='w')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            handle.close()
            return None
    _LOCKING = True
except ImportError:
    try:
        import msvcrt
        def _try_lock(path: str) -> Optional[Any]:
            """Acquire an exclusive, non-blocking lock; return the held handle or None."""
            handle = open(path, mode='w')
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return handle
            except OSError:
                handle.close()
                return None
        _LOCKING = True
    except ImportError:
        _try_lock = None
        _LOCKING = False


# Held lock handles keep claimed slots reserved for the life of the process.
_slot_locks: List[Any] = []


def claim_file_slot(path: str, max_slots: int = 100) -> str:
    """Return the first unclaimed per-process variant of `path`, holding its lock."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    root, ext = os.path.splitext(path)
    if _LOCKING:
        for n in range(1, max_slots + 1):
            candidate = path if n == 1 else f'{root}-{n}{ext}'
            handle = _try_lock(candidate + '.lock')
            if handle is not None:
                _slot_locks.append(handle)
                return candidate
        warn(f'Exhausted {max_slots} log-file slots for \'{path}\'; using PID suffix')
    return f'{root}-{os.getpid()}{ext}'


def resolve_log_path(path: str, role: str, is_default: bool) -> str:
    """Host-scope an explicit client path, then claim an exclusive per-process slot."""
    if role == 'client' and not is_default:
        # A client-role process launched across many hosts must not share a user's
        # explicit path (the default is already host-scoped); decorate it per host.
        root, ext = os.path.splitext(path)
        path = f'{root}-client-{HOSTNAME_SHORT}{ext}'
    return claim_file_slot(path)


# Only permit calling initialize_logging once
_INIT: bool = False


def initialize_logging(role: str = DEFAULT_ROLE) -> None:
    """Enable logging output to the console and rotating files."""

    global _INIT, message_queue, queue_listener, queue_handler, stream_handler, file_handler
    if _INIT:
        return
    else:
        _INIT = True

    # We directly attach the StreamHandler to the main logger to ensure that any messages logged
    # before the logging subsystem is initialized are also captured.
    logger.addHandler(stream_handler)
    logger.setLevel(DEVEL)  # level filtering set on handlers

    default_file = default_file_for(role)
    info = []
    file_config = stream_config.pop('file', default_file)
    if config.which('logging', 'file') == 'default':
        file_config = None  # Only enable file-based logging if any configuration provided

    default_file_style = default_config.logging.file.style
    default_file_format = LOGGING_STYLES[default_file_style]['format']
    default_file_level = default_config.logging.file.level.upper()

    merely_enabled = [True, 'enabled']
    if isinstance(file_config, (str, bool)):
        is_default = file_config in merely_enabled
        file_path = resolve_log_path(default_file if is_default else file_config, role, is_default)
        file_handler = TimedRotatingFileHandler(filename=file_path)
        file_handler.setFormatter(Formatter(default_file_format, datefmt=datefmt))
        file_handler.setLevel(LEVEL_MAPPING[default_file_level])
        set_naming_policy('date')
        info = [
            f'Logging to file ({file_path})',
            'File rotation and compression disabled',
        ]

    elif isinstance(file_config, Namespace):

        requested = file_config.pop('path', default_config.logging.file.path)
        is_default = requested == PARAM_UNSET
        file_path = resolve_log_path(default_file if is_default else requested, role, is_default)
        file_policy = file_config.pop('rotate', DEFAULT_ROTATION_INTERVAL)
        file_compress = file_config.pop('compress', default_config.logging.file.compress)
        file_compress = file_compress if file_compress != PARAM_UNSET else None
        file_keep = file_config.pop('keep', DEFAULT_BACKUP_COUNT)
        file_level = level_from_name(file_config.pop('level', default_file_level), 'logging.file.level')
        file_format = format_from_style(file_config.pop('style', default_file_style), 'logging.file.style')
        file_format = strip_ansi_format(file_config.pop('format', file_format))
        compression_info = f'{file_compress} compression (keep: {file_keep})'
        if file_compress is None:
            compression_info = 'no compression'
        try:
            total_bytes = parse_bytes(file_policy)
            set_naming_policy('count')
            file_handler = SizeRotatingFileHandler(filename=file_path, interval=file_policy)
            info = [
                f'Logging to file ({file_path})',
                f'Rotation policy \'{file_policy}\' (size-like: {total_bytes:,} bytes) with {compression_info}',
                f'Using file-naming policy \'{FILE_NAMING_POLICY}\'',
            ]
        except ValueError:
            set_naming_policy('date' if file_policy in DATE_ELIGIBLE else 'datetime')
            file_handler = TimedRotatingFileHandler(filename=file_path, interval=file_policy)
            info = [
                f'Logging to file ({file_path})',
                f'Rotation policy \'{file_policy}\' (time-like) with {compression_info}',
                f'Using file-naming policy \'{FILE_NAMING_POLICY}\'',
            ]

        if isinstance(file_handler, TimedRotatingFileHandler):
            try:
                from croniter import croniter
            except ImportError:
                panic('Missing optional dependency \'croniter\' needed for time-based rotation')

        file_handler.setFormatter(Formatter(file_format, datefmt=config.logging.datefmt))
        file_handler.setLevel(file_level)
        _set_compression(file_compress)
        _set_retention(file_keep)

    elif file_config is not None and not isinstance(file_config, Namespace):
        label = blame(config, 'logging', 'file')
        panic(f'Invalid configuration for \'logging.file\': expected path-like or Namespace, given '
              f'{file_config.__class__.__name__}:{file_config} ({label})')

    if file_handler is not None:
        queue_handler = FastQueueHandler(message_queue)
        queue_listener = QueueListener(message_queue, file_handler, respect_handler_level=True)
        queue_listener.start()
        compression_thread.start()
        logger.addHandler(queue_handler)

    for key, value in stream_config.items():
        label = blame(config, 'logging', key)
        warn(f'Ignoring unknown configuration parameter \'logging.{key}\': found '
             f'{value.__class__.__name__}:{value} ({label})')
    if isinstance(file_config, Namespace):
        for key, value in file_config.items():
            label = blame(config, 'logging', 'file', key)
            warn(f'Ignoring unknown configuration parameter \'logging.file.{key}\': found '
                 f'{value.__class__.__name__}:{value} ({label})')

    for msg in info:
        debug(msg)

    if file_handler is not None:
        recover_interrupted_compression(file_handler.filename)
        compression_jobs.put_nowait(False)  # Bookmark the completion of recovered files
