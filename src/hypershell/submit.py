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
from typing import List, Iterable, Iterator, IO, Optional, Dict, Callable, Type, Optional, Final
from types import TracebackType

# Standard libs
import os
import re
import io
import sys
import functools
from enum import Enum
from datetime import datetime
from queue import Queue, Empty as QueueEmpty, Full as QueueFull
from multiprocessing import AuthenticationError

# External libs
from cmdkit.config import ConfigurationError
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import config, default
from hypershell.core.fsm import State, StateMachine
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.core.queue import QueueClient, QueueConfig, DEFAULT_LOCAL_TIMEOUT, DEFAULT_REMOTE_TIMEOUT
from hypershell.core.thread import Thread
from hypershell.core.template import Template, DEFAULT_TEMPLATE
from hypershell.core.types import JSONData, parse_bytes
from hypershell.core.pretty_print import format_tag
from hypershell.core.tag import Tag
from hypershell.core.exceptions import (handle_exception, handle_disconnect,
                                        handle_address_unknown, HostAddressInfo, get_shared_exception_mapping)
from hypershell.data.model import Task, DEFAULT_TASK_GROUP, serialize_tasks
from hypershell.data import initdb, checkdb, DATABASE_DIALECT

# Public interface
__all__ = ['submit_from', 'submit_file', 'SubmitThread', 'LiveSubmitThread', 'SubmitApp',
           'DEFAULT_TASK_GROUP', 'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_TEMPLATE']

# Initialize logger
log = Logger.with_name(__name__)


# Redefined for docs
DEFAULT_TASK_GROUP: Final[int] = DEFAULT_TASK_GROUP
"""Default task group for backwards compatibility."""


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
    cores: Optional[int]
    memory: Optional[int]
    timeout: Optional[int]
    template: Template
    group: int
    count: int
    tags: Dict[str, str]

    state = LoaderState.START
    states = LoaderState

    def __init__(self: Loader,
                 source: Iterable[str],
                 queue: Queue[Optional[Task]],
                 cores: int = None,
                 memory: int = None,
                 timeout: int = None,
                 template: str = DEFAULT_TEMPLATE,
                 group: int = DEFAULT_TASK_GROUP,
                 tags: Dict[str, str] = None) -> None:
        """Initialize source to read tasks and submit to database."""
        self.template = Template(template)
        self.source = map(self.template.expand, map(str.strip, map(str, source)))
        self.queue = queue
        self.cores = cores
        self.memory = memory
        self.timeout = timeout
        self.group = group
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
            args = next(self.source)
            self.task = Task.new(args=args, cores=self.cores, memory=self.memory, timeout=self.timeout,
                                 group=self.group, tag=self.tags)
            if self.task.args:
                log.trace(f'Loaded task ({self.task.args})')
                return LoaderState.PUT
            else:
                # NOTE: group, cores, memory, etc. are processed as "tags" here,
                # Though passed as 'tag' they are re-extracted in Task.new()
                _, inline_tags = Task.split_argline(args)  # Simpler to just reprocess
                if inline_tags:
                    tagline = ', '.join(format_tag(k, v) for k, v in inline_tags.items())
                    log.debug(f'Setting global attribute or tag: {tagline}')
                    for k, v in inline_tags.items():
                        self.tags[k] = v
                else:
                    log.trace(f'Skipping empty line')
                return LoaderState.GET
        except StopIteration:
            return LoaderState.FINAL

    def put_task(self: Loader) -> LoaderState:
        """Enqueue loaded task."""
        try:
            self.queue.put(self.task, timeout=DEFAULT_LOCAL_TIMEOUT)
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
                 cores: int = None,
                 memory: int = None,
                 timeout: int = None,
                 group: int = DEFAULT_TASK_GROUP,
                 tags: Dict[str, str] = None) -> None:
        """Initialize machine."""
        super().__init__(name='hypershell-submit-loader')
        self.machine = Loader(source=source, queue=queue, cores=cores, memory=memory, timeout=timeout,
                              template=template, group=group, tags=tags)

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
            task = self.queue.get(timeout=DEFAULT_LOCAL_TIMEOUT)
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

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        timeout (int, optional):
            Task-level walltime limit in seconds (default: none).

        group (int, optional):
            Task group for dependency management (default: 0).

        tags (Dict[str, JSONData], optional):
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
                 cores: int = None,
                 memory: int = None,
                 timeout: int = None,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 template: str = DEFAULT_TEMPLATE,
                 group: int = DEFAULT_TASK_GROUP,
                 tags: Dict[str, JSONData] = None) -> None:
        """Initialize queue and child threads."""
        self.source = source
        self.queue = Queue(maxsize=bundlesize)
        self.loader = LoaderThread(source=source, queue=self.queue,
                                   cores=cores, memory=memory, timeout=timeout,
                                   template=template, group=group, tags=tags)
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
    bundle: Optional[bytes]

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
        self.bundle = None
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
            task = self.local.get(timeout=DEFAULT_LOCAL_TIMEOUT)
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
            self.bundle = serialize_tasks(self.tasks)
            return QueueCommitterState.COMMIT
        else:
            return QueueCommitterState.GET

    def commit(self: QueueCommitter) -> QueueCommitterState:
        """Commit tasks to server scheduling queue."""
        try:
            if self.tasks:
                self.client.scheduled.put(self.bundle, timeout=DEFAULT_REMOTE_TIMEOUT)
                for task in self.tasks:
                    log.trace(f'Scheduled task ({task.id})')
                self.tasks = []
                self.bundle = None
                self.previous_submit = datetime.now()
            return QueueCommitterState.GET
        except QueueFull:
            return QueueCommitterState.COMMIT

    def finalize(self) -> QueueCommitterState:
        """Force final commit of tasks and halt."""
        self.pack_bundle()
        while self.tasks:  # Force retry if queue is busy (since v2.8.0)
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

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        timeout (int, optional):
            Task-level walltime limit in seconds (default: none).
            May be overridden by inline-comment.

        group (int, optional):
            Task group for dependency management (default: 0).

        tags (Dict[str, JSONData], optional):
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
                 cores: int = None,
                 memory: int = None,
                 timeout: int = None,
                 group: int = DEFAULT_TASK_GROUP,
                 bundlesize: int = DEFAULT_BUNDLESIZE,
                 bundlewait: int = DEFAULT_BUNDLEWAIT,
                 tags: Dict[str, str] = None) -> None:
        """Initialize queue and child threads."""
        self.source = source
        self.local = Queue(maxsize=bundlesize)
        self.loader = LoaderThread(source=source, queue=self.local, cores=cores, memory=memory,
                                   timeout=timeout, group=group, template=template, tags=tags)
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
                cores: int = None,
                memory: int = None,
                timeout: int = None,
                group: int = DEFAULT_TASK_GROUP,
                template: str = DEFAULT_TEMPLATE,
                tags: Dict[str, JSONData] = None) -> int:
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

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        timeout (int, optional):
            Task-level walltime limit in seconds (default: none).
            May be overridden by inline-comment.

        group (int, optional):
            Task group for dependency management (default: 0).

        tags (Dict[str, JSONData], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import submit_from
        >>> submit_from(['AAA', 'BBB', 'CCC'], template='my-script {}',
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
                                  cores=cores,
                                  memory=memory,
                                  timeout=timeout,
                                  bundlesize=bundlesize,
                                  bundlewait=bundlewait,
                                  template=template,
                                  group=group,
                                  tags=tags)
    else:
        thread = LiveSubmitThread.new(source=source,
                                      queue_config=queue_config,
                                      cores=cores,
                                      memory=memory,
                                      timeout=timeout,
                                      bundlesize=bundlesize,
                                      bundlewait=bundlewait,
                                      template=template,
                                      group=group,
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
                cores: int = None,
                memory: int = None,
                timeout: int = None,
                group: int = DEFAULT_TASK_GROUP,
                tags: Dict[str, JSONData] = None,
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

        cores (int, optional):
            Default number of cores to use for each task (default: none).
            May be overridden by inline-comment.

        memory (int, optional):
            Default memory in bytes to use for each task (default: none).
            May be overridden by inline-comment.

        timeout (int, optional):
            Task-level walltime limit in seconds (default: none).
            May be overridden by inline-comment.

        group (int, optional):
            Task group for dependency management (default: 0).

        tags (Dict[str, JSONData], optional):
            Tag dictionary for all submitted tasks.

    Example:
        >>> from hypershell.submit import submit_from
        >>> from hypershell.core.queue import QueueConfig
        >>> queue_config = QueueConfig(host='my.server.univ.edu', port=54321, auth='my-secret-key')
        >>> submit_file('/tmp/tasks.in', queue_config=queue_config, template='my-script {}',
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
        return submit_from(stream, queue_config=queue_config, bundlesize=bundlesize, bundlewait=bundlewait,
                           template=template, cores=cores, memory=memory, timeout=timeout, group=group, tags=tags)


DEFAULT_HOST: Final[str] = QueueConfig.host
"""Default host for server connection."""

DEFAULT_PORT: Final[int] = QueueConfig.port
"""Default port for server connection."""

DEFAULT_AUTH: Final[str] = QueueConfig.auth
"""Default authentication key for server (**DO NOT USE THIS**)."""


APP_NAME: Final[str] = 'hs submit'
PAD_NAME: Final[str] = ' ' * len(APP_NAME)
APP_USAGE: Final[str] = f"""\
Usage:
  {APP_NAME} [-h] [ARGS... | -f FILE] [-q [-H ADDR] [-p NUM] [-k KEY] | --initdb]
  {PAD_NAME} [-b NUM] [-w SEC] [-c NUM] [-m MEM] [-W SEC] [-g NUM] [--template CMD] [-t TAG...]
  {PAD_NAME} [--no-tls | [--tls-ca PATH] [--tls-cert PATH] [--tls-key PATH]]

  Submit one or more tasks.\
"""

APP_HELP: Final[str] = f"""\
{APP_USAGE}

  Submit a single command using positional arguments.
  If a filepath is provided, read many tasks from the file instead.
  Submit directly to a live queue with --queue.

Arguments:
  ARGS...                    Command-line task arguments.

Options:
  -f, --task-file    FILE    Path to task file ("-" for <stdin>).
  -q, --queue                Submit to live queue instead of database.
  -H, --host         ADDR    Hostname for server (default: {DEFAULT_HOST}). Used with --queue.
  -p, --port         NUM     Port number for server (default: {DEFAULT_PORT}). Used with --queue.
  -k, --auth         KEY     Cryptographic key to connect to server. Used with --queue.
      --no-tls               Disable TLS for queue interface (not recommended).
      --tls-key              Path to TLS private key file (default: <auto>).
      --tls-cert             Path to TLS certificate file (default: <auto>).
      --tls-ca               Path to TLS CA certificate file (default: <auto>).
      --template     CMD     Submit-time template expansion (default: "{DEFAULT_TEMPLATE}").
  -c, --cores        NUM     Required cores per task (default: none).
  -m, --memory       MEM     Required memory per task (default: none).
  -W, --timeout      SEC     Task-level walltime limit (default: none).
  -g, --group        NUM     Task group for dependency management (default: {DEFAULT_TASK_GROUP}).
  -b, --bundlesize   NUM     Number of lines to buffer (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait   SEC     Seconds to wait before flushing tasks (default: {DEFAULT_BUNDLEWAIT}).
      --initdb               Auto-initialize database.
  -t, --tag          TAG...  Assign tags as `key:value`.
  -h, --help                 Show this message and exit.\
"""


class SubmitApp(Application):
    """Submit tasks to the database."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    source: IO | List[str] | None = None
    task_args: List[str] = []
    task_file: str = None
    interface.add_argument('task_args', nargs='*')
    interface.add_argument('-f', '--task-file', default=task_file)

    bundlesize: int = config.submit.bundlesize
    interface.add_argument('-b', '--bundlesize', type=int, default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    interface.add_argument('-w', '--bundlewait', type=int, default=bundlewait)

    template: str = DEFAULT_TEMPLATE
    interface.add_argument('--template', default=template)

    cores: Optional[int] = config.task.cores or None
    memory: Optional[int] = config.task.memory or None
    interface.add_argument('-c', '--cores', type=int, default=cores)
    interface.add_argument('-m', '--memory', type=parse_bytes, default=memory)

    timeout: Optional[int] = config.task.timeout or None
    interface.add_argument('-W', '--timeout', type=int, default=timeout)

    group: Optional[int] = None
    interface.add_argument('-g', '--group', type=int, default=group)

    auto_initdb: bool = False
    interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    queue: Optional[QueueConfig]
    queue_mode: bool = False
    queue_server: str = QueueConfig.host
    queue_port: int = QueueConfig.port
    queue_auth: str = QueueConfig.auth
    interface.add_argument('-q', '--queue', action='store_true', dest='queue_mode')
    interface.add_argument('-H', '--host', default=queue_server, dest='queue_host')
    interface.add_argument('-p', '--port', type=int, default=queue_port, dest='queue_port')
    interface.add_argument('-k', '--auth', default=queue_auth, dest='queue_auth')

    tls: Optional[TLSConfig] = None
    tls_enabled: bool = True
    tls_cert: str = config.server.tls.cert
    tls_key: str = config.server.tls.key
    tls_ca: str = config.server.tls.cafile
    interface.add_argument('--no-tls', action='store_false', dest='tls_enabled')
    interface.add_argument('--tls-key', default=tls_key)
    interface.add_argument('--tls-cert', default=tls_cert)
    interface.add_argument('--tls-ca', default=tls_ca)

    tags: Dict[str, JSONData] = {}
    taglist: List[str] = None
    interface.add_argument('-t', '--tag', nargs='+', default=[], dest='taglist')

    exceptions = {
        EOFError: functools.partial(handle_disconnect, logger=log),
        ConnectionResetError: functools.partial(handle_disconnect, logger=log),
        ConnectionRefusedError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        AuthenticationError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        HostAddressInfo: functools.partial(handle_address_unknown, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    def run(self: SubmitApp) -> None:
        """Run submit thread."""
        self.check_source()
        self.queue = None
        if self.queue_mode:
            self.enable_tls()
            self.queue = QueueConfig(host=self.queue_server, port=self.queue_port, auth=self.queue_auth, tls=self.tls)
            if self.queue_auth == DEFAULT_AUTH:
                log.warning('Using default authentication key - do not use this in production!')
            if not self.tls_enabled:
                log.warning('TLS is disabled - this is not recommended for production!')
        else:
            self.check_database()
            self.check_tags()
            if self.group is None:
                group = Task.current_group()
                self.group = group.value
                log.info(f'Auto-selected task group {group.value} ({group.reason})')
        if self.source is not None:
            self.submit_all()
        else:
            self.submit_one()

    def submit_all(self: SubmitApp) -> None:
        """Submit multiple tasks from file-like source."""
        count = submit_from(self.source, template=self.template, queue_config=self.queue,
                            cores=self.cores, memory=self.memory, timeout=self.timeout, group=self.group,
                            bundlesize=self.bundlesize, bundlewait=self.bundlewait, tags=self.tags)
        log.info(f'Submitted {count} tasks')

    def submit_one(self: SubmitApp) -> None:
        """Submit one task from arguments."""
        args = ' '.join([self.quote_arg(arg) for arg in self.task_args])
        if self.queue_mode:
            submit_from([args, ], queue_config=self.queue,
                        cores=self.cores, memory=self.memory, timeout=self.timeout, group=self.group,
                        tags=self.tags)
        else:
            task = Task.new(args=args, cores=self.cores, memory=self.memory, timeout=self.timeout, 
                            group=self.group, tag=self.tags)
            Task.add(task)
            log.info(f'Submitted task ({task.id})')

    def check_database(self: SubmitApp) -> None:
        """Halt if we are not connected to database."""
        db = config.database.get('file', None) or config.database.get('database', None)
        if DATABASE_DIALECT == 'sqlite' and db in ('', ':memory:', None):
            raise ConfigurationError('Submitting tasks to in-memory database has no effect')
        if DATABASE_DIALECT == 'sqlite' or self.auto_initdb:
            initdb()  # Auto-initialize if local sqlite provider
        else:
            checkdb()

    def check_tags(self: SubmitApp) -> None:
        """Ensure valid tags."""
        self.tags = {} if not self.taglist else Tag.parse_cmdline_list(self.taglist)
        try:
            Task.ensure_valid_tag(self.tags)
        except (ValueError, TypeError) as error:
            raise ArgumentError(str(error)) from error

    def check_source(self: SubmitApp) -> None:
        """Determine task submission mode."""
        if self.task_args and self.task_file:
            raise ArgumentError(f'Cannot specify both -f/--task-file and positional arguments')
        if self.task_file == '-':
            log.debug(f'Submitted from <stdin> (explicit)')
            self.source = sys.stdin
        elif self.task_file is not None:
            log.debug(f'Submitted from {self.task_file} (explicit)')
            self.source = open(self.task_file, mode='r')
        elif len(self.task_args) == 0:
            log.debug(f'Submitted from <stdin> (implicit)')
            self.source = sys.stdin
        elif len(self.task_args) == 1:
            filepath = self.task_args[0]
            if filepath == '-':
                log.debug(f'Submitted from <stdin> (explicit)')
                self.source = sys.stdin
            elif os.path.exists(filepath):
                if not os.access(filepath, os.X_OK):
                    log.debug(f'Submitted from {filepath} (implicit - not executable)')
                    self.source = open(filepath, mode='r')
                else:
                    log.debug(f'Submitted single task (implicit - executable)')
                    self.source = None
            else:
                log.debug(f'Submitted single task (implicit)')
                self.source = None  # program name without arguments
        else:
            log.debug(f'Submitted single task (explicit)')
            self.source = None

    @staticmethod
    def quote_arg(arg: str) -> str:
        """Ensure that argument is properly quoted."""
        if not re.search(r'\s', arg):
            return arg
        if '"' not in arg:
            return f'"{arg}"'
        if "'" not in arg:
            return f"'{arg}'"
        else:
            raise ArgumentError(f'Could not quote argument: {arg}')

    def enable_tls(self: SubmitApp) -> None:
        """Configure TLS if enabled."""
        if self.tls_enabled:
            self.tls = tls_from_namespace({
                **config.server.tls,
                'cert': self.tls_cert,
                'key': self.tls_key,
                'cafile': self.tls_ca,
            })

    def __exit__(self: SubmitApp,
                 exc_type: Optional[Type[Exception]],
                 exc_val: Optional[Exception],
                 exc_tb: Optional[TracebackType]) -> None:
        """Close file if not stdin."""
        if self.source is not None and self.source is not sys.stdin:
            self.source.close()
