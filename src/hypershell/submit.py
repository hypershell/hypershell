# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Submit tasks to the database.

Any iterable of command lines can be submitted directly.
Example:
    >>> from hypershell.submit import submit_from
    >>> submit_from(['echo AA', 'echo BB', 'echo CC'])

A file stream is a valid iterable to pass to `submit_from`.
Use `submit_file` with the file path as shorthand.

Example:
    >>> from hypershell.submit import submit_file
    >>> submit_file('/path/to/commandlines.txt')

Embed a `SubmitThread` in your application directly as the `ServerThread` does.
Call `stop()` to stop early.

Example:
    >>> import sys
    >>> from hypershell.submit import SubmitThread
    >>> submit_thread = SubmitThread.new(sys.stdin, bundlesize=10)

Note:
    In order for the `SubmitThread` to actively monitor the state set by `stop` and
    halt execution (a requirement because of how CPython does threading), the implementation
    uses a finite state machine. *You should not instantiate this machine directly*.

Warning:
    Because the `SubmitThread` checks state actively to decide whether to halt, if your
    `source` is blocking (e.g., `sys.stdin`) it will not be able to halt immediately. If
    your main program exits however, the thread will be stopped regardless because it
    runs as a `daemon`.
"""


# Type annotations
from __future__ import annotations
from typing import List, Iterable, Iterator, IO, Optional, Dict, Callable, Type, Final
from types import TracebackType

# Standard libs
import io
import sys
import functools
from enum import Enum
from datetime import datetime
from queue import Queue, Empty as QueueEmpty, Full as QueueFull

# External libs
from cmdkit.config import ConfigurationError
from cmdkit.app import Application
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import config, default
from hypershell.core.fsm import State, StateMachine
from hypershell.core.queue import QueueClient, QueueConfig
from hypershell.core.thread import Thread
from hypershell.core.template import Template, DEFAULT_TEMPLATE
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.core.types import JSONValue
from hypershell.data.model import Task
from hypershell.data import initdb, checkdb
from hypershell.task import Tag

# Public interface
__all__ = ['submit_from', 'submit_file', 'SubmitThread', 'LiveSubmitThread',
           'SubmitApp', 'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_TEMPLATE']

# Initialize logger
log = Logger.with_name(__name__)


class LoaderState(State, Enum):
    """Finite states of loader machine."""
    START = 0
    GET = 1
    PUT = 2
    FINAL = 3
    HALT = 4


class Loader(StateMachine):
    """Enqueue tasks from iterable source."""

    task: Task
    source: Iterator[str]
    queue: Queue[Optional[Task]]
    template: Template
    count: int
    tags: Dict[str, str]

    state = LoaderState.START
    states = LoaderState

    def __init__(self: Loader,
                 source: Iterable[str],
                 queue: Queue[Optional[Task]],
                 template: str = DEFAULT_TEMPLATE,
                 tags: Dict[str, str] = None) -> None:
        """Initialize source to read tasks and submit to database."""
        self.template = Template(template)
        self.source = map(self.template.expand, map(str.strip, map(str, source)))
        self.queue = queue
        self.tags = tags
        self.count = 0

    @functools.cached_property
    def actions(self: Loader) -> Dict[LoaderState, Callable[[], LoaderState]]:
        return {
            LoaderState.START: self.start,
            LoaderState.GET: self.get_task,
            LoaderState.PUT: self.put_task,
            LoaderState.FINAL: self.finalize,
        }

    @staticmethod
    def start() -> LoaderState:
        """Jump to GET state."""
        log.debug('Started (loader)')
        return LoaderState.GET

    def get_task(self: Loader) -> LoaderState:
        """Get the next task from the source."""
        try:
            self.task = Task.new(args=next(self.source), tag=self.tags)
            log.trace(f'Loaded task ({self.task.args})')
            return LoaderState.PUT
        except StopIteration:
            return LoaderState.FINAL

    def put_task(self: Loader) -> LoaderState:
        """Enqueue loaded task."""
        try:
            self.queue.put(self.task, timeout=1)
            self.count += 1
            return LoaderState.GET
        except QueueFull:
            return LoaderState.PUT

    @staticmethod
    def finalize() -> LoaderState:
        """Return HALT."""
        log.debug('Done (loader)')
        return LoaderState.HALT


class LoaderThread(Thread):
    """Run loader within dedicated thread."""

    def __init__(self: LoaderThread,
                 source: Iterable[str],
                 queue: Queue[Optional[Task]],
                 template: str = DEFAULT_TEMPLATE,
                 tags: Dict[str, str] = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-submit-loader')
        self.machine = Loader(source=source, queue=queue, template=template, tags=tags)

    def run_with_exceptions(self: LoaderThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: LoaderThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (loader)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class DatabaseCommitterState(State, Enum):
    """Finite states for database submitter."""
    START = 0
    GET = 1
    COMMIT = 2
    FINAL = 3
    HALT = 4


DEFAULT_BUNDLESIZE: Final[int] = default.submit.bundlesize
"""Default size of task bundles."""

DEFAULT_BUNDLEWAIT: Final[int] = default.submit.bundlewait
"""Default waiting period before forcing task bundle push."""


class DatabaseCommitter(StateMachine):
    """Commit tasks from local queue to database."""

    queue: Queue[Optional[Task]]
    tasks: List[Task]
    bundlesize: int
    bundlewait: int
    previous_submit: datetime

    state = DatabaseCommitterState.START
    states = DatabaseCommitterState

    def __init__(self: DatabaseCommitter,
                 queue: Queue[Optional[Task]],
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Initialize with task queue and buffering parameters."""
        self.queue = queue
        self.tasks = []
        self.bundlesize = bundlesize
        self.bundlewait = bundlewait

    @functools.cached_property
    def actions(self: DatabaseCommitter) -> Dict[DatabaseCommitterState, Callable[[], DatabaseCommitterState]]:
        return {
            DatabaseCommitterState.START: self.start,
            DatabaseCommitterState.GET: self.get_task,
            DatabaseCommitterState.COMMIT: self.commit,
            DatabaseCommitterState.FINAL: self.finalize,
        }

    def start(self: DatabaseCommitter) -> DatabaseCommitterState:
        """Jump to GET state."""
        log.debug('Started (committer: database)')
        self.previous_submit = datetime.now()
        return DatabaseCommitterState.GET

    def get_task(self: DatabaseCommitter) -> DatabaseCommitterState:
        """Get tasks from local queue and check buffer."""
        try:
            task = self.queue.get(timeout=1)
        except QueueEmpty:
            return DatabaseCommitterState.GET
        if task is not None:
            self.tasks.append(task)
            since_last = (datetime.now() - self.previous_submit).total_seconds()
            if len(self.tasks) >= self.bundlesize or since_last >= self.bundlewait:
                return DatabaseCommitterState.COMMIT
            else:
                return DatabaseCommitterState.GET
        else:
            return DatabaseCommitterState.FINAL

    def commit(self: DatabaseCommitter) -> DatabaseCommitterState:
        """Commit tasks to database."""
        if self.tasks:
            Task.add_all(self.tasks)
            log.debug(f'Submitted {len(self.tasks)} tasks')
            self.tasks.clear()
            self.previous_submit = datetime.now()
        return DatabaseCommitterState.GET

    def finalize(self: DatabaseCommitter) -> DatabaseCommitterState:
        """Force final commit of tasks and halt."""
        self.commit()
        log.debug('Done (committer: database)')
        return DatabaseCommitterState.HALT


class DatabaseCommitterThread(Thread):
    """Run committer within dedicated thread."""

    def __init__(self: DatabaseCommitterThread,
                 queue: Queue[Optional[Task]],
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-submit-committer')
        self.machine = DatabaseCommitter(queue=queue, bundlesize=bundlesize, bundlewait=bundlewait)

    def run_with_exceptions(self: DatabaseCommitterThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: DatabaseCommitterThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (committer: database)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class SubmitThread(Thread):
    """
    Submit tasks to database within dedicated thread.

    Args:
        source (Iterable[str]):
            Any iterable of command-line tasks.

        bundlesize (int, optional):
            Size of task bundles.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`DEFAULT_BUNDLEWAIT`.

        template (str, optional):
            Task command-line template pattern.
            See :const:`DEFAULT_TEMPLATE`.

        tags (Dict[str, JSONValue], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import SubmitThread
        >>> submitter = SubmitThread.new(['AAA', 'BBB', 'CCC'],
        ...                              template='my-script {}'
        ...                              tags={'site': 'zzz', 'group': 37})
        >>> submitter.join()

    See Also:
        - :meth:`submit_from`
        - :meth:`submit_file`
    """

    source: Iterable[str]
    queue: Queue[Optional[Task]]
    loader: LoaderThread
    committer: DatabaseCommitterThread

    def __init__(self: SubmitThread,
                 source: Iterable[str],
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 template: str = DEFAULT_TEMPLATE,
                 tags: Dict[str, JSONValue] = None) -> None:
        """Initialize queue and child threads."""
        self.source = source
        self.queue = Queue(maxsize=bundlesize)
        self.loader = LoaderThread(source=source, queue=self.queue, template=template, tags=tags)
        self.committer = DatabaseCommitterThread(queue=self.queue, bundlesize=bundlesize, bundlewait=bundlewait)
        super().__init__(name='hypershell-submit')

    def run_with_exceptions(self: SubmitThread) -> None:
        """Start child threads, wait."""
        log.debug(f'Started ({self.source_name})')
        self.loader.start()
        self.committer.start()
        self.loader.join()
        self.queue.put(None)
        self.committer.join()
        log.debug('Done')

    @functools.cached_property
    def source_name(self: SubmitThread) -> str:
        """Log details of source."""
        if self.source is sys.stdin:
            return '<stdin>'
        elif isinstance(self.source, io.TextIOWrapper):
            return self.source.name
        else:
            return '<iterable>'

    def stop(self: SubmitThread, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        log.warning('Stopping')
        self.loader.stop(wait=wait, timeout=timeout)
        self.queue.put(None)
        self.committer.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)

    @property
    def task_count(self: SubmitThread) -> int:
        """Count of submitted tasks."""
        return self.loader.machine.count


class QueueCommitterState(State, Enum):
    """Finite states for queue submitter."""
    START = 0
    GET = 1
    PACK = 2
    COMMIT = 3
    FINAL = 4
    HALT = 5


class QueueCommitter(StateMachine):
    """Commit tasks from local queue directly to remote server queue."""

    local: Queue[Optional[Task]]
    client: QueueClient

    tasks: List[Task]
    bundle: List[bytes]

    bundlesize: int
    bundlewait: int
    previous_submit: datetime

    state = QueueCommitterState.START
    states = QueueCommitterState

    def __init__(self: QueueCommitter, local: Queue[Optional[Task]], client: QueueClient,
                 bundlesize: int = DEFAULT_BUNDLESIZE, bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Initialize with queue handles and buffering parameters."""
        self.local = local
        self.client = client
        self.tasks = []
        self.bundle = []
        self.bundlesize = bundlesize
        self.bundlewait = bundlewait

    @functools.cached_property
    def actions(self: QueueCommitter) -> Dict[QueueCommitterState, Callable[[], QueueCommitterState]]:
        return {
            QueueCommitterState.START: self.start,
            QueueCommitterState.GET: self.get_task,
            QueueCommitterState.PACK: self.pack_bundle,
            QueueCommitterState.COMMIT: self.commit,
            QueueCommitterState.FINAL: self.finalize,
        }

    def start(self: QueueCommitter) -> QueueCommitterState:
        """Jump to GET state."""
        log.debug('Started (committer: no database)')
        self.previous_submit = datetime.now()
        return QueueCommitterState.GET

    def get_task(self: QueueCommitter) -> QueueCommitterState:
        """Get tasks from local queue and check buffer."""
        try:
            task = self.local.get(timeout=1)
        except QueueEmpty:
            return QueueCommitterState.GET
        if task is not None:
            self.tasks.append(task)
            since_last = (datetime.now() - self.previous_submit).total_seconds()
            if len(self.tasks) >= self.bundlesize or since_last >= self.bundlewait:
                return QueueCommitterState.PACK
            else:
                return QueueCommitterState.GET
        else:
            return QueueCommitterState.FINAL

    def pack_bundle(self: QueueCommitter) -> QueueCommitterState:
        """Pack tasks into bundle for remote queue."""
        if self.tasks:
            self.bundle = [task.pack() for task in self.tasks]
            return QueueCommitterState.COMMIT
        else:
            return QueueCommitterState.GET

    def commit(self: QueueCommitter) -> QueueCommitterState:
        """Commit tasks to server scheduling queue."""
        try:
            if self.tasks:
                self.client.scheduled.put(self.bundle, timeout=2)
                for task in self.tasks:
                    log.trace(f'Scheduled task ({task.id})')
                self.tasks = []
                self.bundle = []
                self.previous_submit = datetime.now()
            return QueueCommitterState.GET
        except QueueFull:
            return QueueCommitterState.COMMIT

    def finalize(self) -> QueueCommitterState:
        """Force final commit of tasks and halt."""
        self.pack_bundle()
        self.commit()
        log.debug('Done (committer: no database)')
        return QueueCommitterState.HALT


class QueueCommitterThread(Thread):
    """Run queue committer within dedicated thread."""

    def __init__(self: QueueCommitterThread, local: Queue[Optional[Task]], client: QueueClient,
                 bundlesize: int = DEFAULT_BUNDLESIZE, bundlewait: int = DEFAULT_BUNDLEWAIT) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-submit-committer')
        self.machine = QueueCommitter(local=local, client=client, bundlesize=bundlesize, bundlewait=bundlewait)

    def run_with_exceptions(self: QueueCommitterThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: QueueCommitterThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (committer: no database)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)


class LiveSubmitThread(Thread):
    """
    Submit tasks directly to queue within dedicated thread.

    Args:
        source (Iterable[str]):
            Any iterable of command-line tasks.

        queue_config (:class:`~hypershell.core.queue.QueueConfig`):
            QueueConfig instance with `host`, `port`, and `auth`.

        bundlesize (int, optional):
            Size of task bundles.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`DEFAULT_BUNDLEWAIT`.

        template (str, optional):
            Task command-line template pattern.
            See :const:`DEFAULT_TEMPLATE`.

        tags (Dict[str, JSONValue], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import LiveSubmitThread
        >>> from hypershell.core.queue import QueueConfig
        >>> queue_config = QueueConfig(host='localhost', port=54321, auth='my-secret-key')
        >>> submitter = LiveSubmitThread.new(['AAA', 'BBB', 'CCC'],
        ...                                  queue_config=queue_config,
        ...                                  template='my-script {}'
        ...                                  tags={'site': 'zzz', 'group': 37})
        >>> submitter.join()

    See Also:
        - :meth:`submit_from`
        - :meth:`submit_file`
    """

    source: Iterable[str]
    local: Queue[Optional[Task]]
    client: QueueClient
    loader: LoaderThread
    committer: QueueCommitterThread

    def __init__(self: LiveSubmitThread,
                 source: Iterable[str],
                 queue_config: QueueConfig,
                 template: str = DEFAULT_TEMPLATE,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 tags: Dict[str, str] = None) -> None:
        """Initialize queue and child threads."""
        self.source = source
        self.local = Queue(maxsize=bundlesize)
        self.loader = LoaderThread(source=source, queue=self.local, template=template, tags=tags)
        self.client = QueueClient(config=queue_config)
        self.committer = QueueCommitterThread(local=self.local, client=self.client,
                                              bundlesize=bundlesize, bundlewait=bundlewait)
        super().__init__(name='hypershell-submit')

    def run_with_exceptions(self: LiveSubmitThread) -> None:
        """Start child threads, wait."""
        log.debug(f'Started ({self.source_name})')
        with self.client:
            self.loader.start()
            self.committer.start()
            log.trace('Waiting (loader)')
            self.loader.join()
            self.local.put(None)
            log.trace('Waiting (committer)')
            self.committer.join()
        log.debug('Done')

    @functools.cached_property
    def source_name(self: LiveSubmitThread) -> str:
        """Log details of source."""
        if self.source is sys.stdin:
            return '<stdin>'
        elif isinstance(self.source, io.TextIOWrapper):
            return self.source.name
        else:
            return '<iterable>'

    def stop(self: LiveSubmitThread, wait: bool = False, timeout: int = None) -> None:
        """Stop child threads before main thread."""
        log.warning('Stopping')
        self.loader.stop(wait=wait, timeout=timeout)
        self.local.put(None)
        self.committer.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)

    @property
    def task_count(self: LiveSubmitThread) -> int:
        """Count of submitted tasks."""
        return self.loader.machine.count


def submit_from(source: Iterable[str],
                queue_config: QueueConfig = None,
                bundlesize: int = DEFAULT_BUNDLESIZE,
                bundlewait: int = DEFAULT_BUNDLEWAIT,
                template: str = DEFAULT_TEMPLATE,
                tags: Dict[str, JSONValue] = None) -> int:
    """
    Submit all task arguments from `source`, return count of submitted tasks.

    If `queue_config` is provided, the :class:`LiveSubmitThread` is used to submit
    task bundles directly to the shared queue hosted by the server. Otherwise,
    the :class:`SubmitThread` submits tasks to the database.

    Args:
        source (Iterable[str]):
            Any iterable of command-line tasks.

        queue_config (:class:`~hypershell.core.queue.QueueConfig`):
            QueueConfig instance with `host`, `port`, and `auth`.

        bundlesize (int, optional):
            Size of task bundles.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`DEFAULT_BUNDLEWAIT`.

        template (str, optional):
            Task command-line template pattern.
            See :const:`DEFAULT_TEMPLATE`.

        tags (Dict[str, JSONValue], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import submit_from
        >>> submit_from(['AAA', 'BBB', 'CCC'],
        ...             template='my-script {}'
        ...             tags={'site': 'zzz', 'group': 37})
        3

    See Also:
        - :meth:`submit_file`
        - :class:`SubmitThread`
        - :class:`LiveSubmitThread`

    Returns:
        task_count (int): Count of submitted tasks.
    """
    if not queue_config:
        thread = SubmitThread.new(source=source,
                                  bundlesize=bundlesize,
                                  bundlewait=bundlewait,
                                  template=template,
                                  tags=tags)
    else:
        thread = LiveSubmitThread.new(source=source,
                                      queue_config=queue_config,
                                      template=template,
                                      bundlesize=bundlesize,
                                      bundlewait=bundlewait,
                                      tags=tags)
    try:
        thread.join()
    except Exception:
        thread.stop()
        raise
    else:
        return thread.task_count


def submit_file(path: str,
                queue_config: QueueConfig = None,
                bundlesize: int = DEFAULT_BUNDLESIZE,
                bundlewait: int = DEFAULT_BUNDLEWAIT,
                template: str = DEFAULT_TEMPLATE,
                tags: Dict[str, JSONValue] = None,
                **file_options) -> int:
    """
    Submit all task arguments by reading them from file `path`.

    Arguments are forwarded to :func:`submit_from` with the opened file stream
    from `path` as the task `source`.

    Args:
        path (str):
            Path to file containing command-line tasks.

        queue_config (:class:`~hypershell.core.queue.QueueConfig`):
            QueueConfig instance with `host`, `port`, and `auth`.

        bundlesize (int, optional):
            Size of task bundles.
            See :const:`DEFAULT_BUNDLESIZE`.

        bundlewait (int, optional):
            Waiting period before forcing task bundle push.
            See :const:`DEFAULT_BUNDLEWAIT`.

        template (str, optional):
            Task command-line template pattern.
            See :const:`DEFAULT_TEMPLATE`.

        tags (Dict[str, JSONValue], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import submit_from
        >>> from hypershell.core.queue import QueueConfig
        >>> queue_config = QueueConfig(host='my.server.univ.edu', port=54321, auth='my-secret-key')
        >>> submit_file('/tmp/tasks.in',
        ...             queue_config=queue_config,
        ...             template='my-script {}'
        ...             tags={'site': 'zzz', 'group': 37})
        3

    See Also:
        - :meth:`submit_from`
        - :class:`SubmitThread`
        - :class:`LiveSubmitThread`

    Returns:
        task_count (int): Count of submitted tasks.
    """
    with open(path, mode='r', **file_options) as stream:
        return submit_from(stream, queue_config=queue_config, bundlesize=bundlesize,
                           bundlewait=bundlewait, template=template, tags=tags)


APP_NAME = 'hs submit'
APP_USAGE = f"""\
Usage:
  {APP_NAME} [-h] [FILE] [-b NUM] [-w SEC] [-t CMD] [--initdb] [--tag TAG [TAG...]]
  Submit tasks from a file.\
"""

APP_HELP = f"""\
{APP_USAGE}

Arguments:
  FILE                       Path to task file ("-" for <stdin>).

Options:
  -t, --template     CMD     Submit-time template expansion (default: "{DEFAULT_TEMPLATE}").
  -b, --bundlesize   NUM     Number of lines to buffer (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait   SEC     Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
      --initdb               Auto-initialize database.
      --tag          TAG...  Assign tags as `key:value`.
  -h, --help                 Show this message and exit.\
"""


class SubmitApp(Application):
    """Submit tasks to the database."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    source: IO
    filepath: str
    interface.add_argument('filepath', nargs='?', default='-')

    bundlesize: int = config.submit.bundlesize
    interface.add_argument('-b', '--bundlesize', type=int, default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    interface.add_argument('-w', '--bundlewait', type=int, default=bundlewait)

    template: str = DEFAULT_TEMPLATE
    interface.add_argument('-t', '--template', default=template)

    auto_initdb: bool = False
    interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    tags: Dict[str, JSONValue] = {}
    taglist: List[str] = None
    interface.add_argument('--tag', nargs='*', default=[], dest='taglist')

    count: int = 0

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: SubmitApp) -> None:
        """Run submit thread."""
        self.submit_all()
        log.info(f'Submitted {self.count} tasks')

    def submit_all(self: SubmitApp) -> None:
        """Submit all tasks from source."""
        self.count = submit_from(self.source, template=self.template,
                                 bundlesize=self.bundlesize, bundlewait=self.bundlewait, tags=self.tags)

    @staticmethod
    def check_config():
        """Halt if we are not connected to database."""
        db = config.database.get('file', None) or config.database.get('database', None)
        if config.database.provider == 'sqlite' and db in ('', ':memory:', None):
            raise ConfigurationError('Submitting tasks to in-memory database has no effect')

    def check_tags(self: SubmitApp) -> None:
        """Ensure valid tags."""
        self.tags = {} if not self.taglist else Tag.parse_cmdline_list(self.taglist)
        try:
            Task.ensure_valid_tag(self.tags)
        except (ValueError, TypeError) as error:
            raise ArgumentError(str(error)) from error

    def __enter__(self: SubmitApp) -> SubmitApp:
        """Open file if not stdin."""
        self.source = sys.stdin if self.filepath == '-' else open(self.filepath, mode='r')
        self.check_config()
        self.check_tags()
        if config.database.provider == 'sqlite' or self.auto_initdb:
            initdb()  # Auto-initialize if local sqlite provider
        else:
            checkdb()
        return self

    def __exit__(self: SubmitApp,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Close file if not stdin."""
        if self.source is not sys.stdin:
            self.source.close()
