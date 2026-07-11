# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
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
from typing import List, Iterable, Iterator, IO, Optional, Dict, Tuple, Callable, Type, Final
from types import TracebackType

# Standard libs
import os
import re
import io
import sys
import json
import hashlib
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
from hypershell.data.model import Task, Source, DIRECT_SOURCE_ID, STDIN_SOURCE_ID, DEFAULT_TASK_GROUP, serialize_tasks
from hypershell.data import initdb, checkdb, DATABASE_DIALECT, DATABASE_ENABLED

# Public interface
__all__ = ['submit_from', 'submit_file', 'load_json_tasks', 'load_json_source', 'json_source_key',
           'validate_json_expansion',
           'GatedSource', 'source_fingerprint_and_count', 'apply_source_gate',
           'SubmitThread', 'LiveSubmitThread', 'SubmitApp',
           'DEFAULT_TASK_GROUP', 'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_TEMPLATE']

# Initialize logger
log = Logger.with_name(__name__)


# Redefined for docs
DEFAULT_TASK_GROUP: Final[int] = DEFAULT_TASK_GROUP
"""Default task group for backwards compatibility."""


class GatedSource:
    """Wrap a task source with gate context that rides *inside* the source iterable.

    Carries the resolved ``source_id`` (stamped onto every task by the :class:`Loader`)
    and an optional ``skip_fingerprints`` set — task identities already present in a prior
    same-path source lineage, which the Loader drops for ``--update``/``--restart`` de-dup.
    Threading this inside ``source`` (rather than as kwargs) confines the gate plumbing to
    the single ``Loader``/``Task.new`` stamping chokepoint instead of the ~10 coupled-core
    constructors (submit_from / SubmitThread / ServerThread / cluster launchers).

    ``__iter__`` delegates to the wrapped iterable, so every existing consumer that only
    iterates ``source`` keeps working unchanged.
    """

    def __init__(self: GatedSource,
                 iterable: Iterable[str | dict],
                 source_id: str,
                 skip_fingerprints: Optional[set] = None,
                 name: Optional[str] = None) -> None:
        """Initialize wrapper around `iterable` with gate context."""
        self.iterable = iterable
        self.source_id = source_id
        self.skip_fingerprints = skip_fingerprints
        self.name = name

    def __iter__(self: GatedSource) -> Iterator[str | dict]:
        """Delegate iteration to the wrapped source."""
        return iter(self.iterable)


def line_is_task(line: str, template: Template) -> bool:
    """True if `line` yields a task under `template`.

    A raw text line becomes a task iff, after template expansion and inline-comment
    stripping, the command is non-empty. This mirrors the :class:`Loader`'s own
    task/non-task split (blank, comment-only, and inline-tag-only lines are *not* tasks),
    so an upfront count matches exactly what the Loader will submit.
    """
    args, _ = Task.split_argline(template.expand(str(line).strip()))
    return bool(args)


def source_fingerprint_and_count(path: str, template: str = DEFAULT_TEMPLATE) -> Tuple[str, int]:
    """Read a named file upfront: content md5 (raw bytes) + task count (R4).

    The md5 is over the raw file bytes (parsing-independent). The count is taken over a
    *text-mode* read — the same universal-newline decoding the Loader uses — replaying the
    shared :func:`line_is_task` predicate, so blank/comment/inline-tag-only lines are
    excluded and the recorded count matches exactly what the Loader will submit, for any
    newline convention (LF, CRLF, or bare CR). Both reads stream (no whole-file buffering).
    Callers must route only seekable, re-readable files here; non-seekable inputs (pipes,
    FIFOs, piped ``/dev/stdin``) are handled upstream (streamed like ``<stdin>``), because a
    second independent read would drain them.
    """
    engine = Template(template)
    digest = hashlib.md5()
    with open(path, mode='rb') as raw_stream:
        for chunk in iter(lambda: raw_stream.read(1 << 20), b''):
            digest.update(chunk)
    count = 0
    with open(path, mode='r') as text_stream:  # text mode == the Loader's universal-newline split
        for line in text_stream:
            if line_is_task(line, engine):
                count += 1
    return digest.hexdigest(), count


def _new_source_id(path: str, fingerprint: str, count: int) -> str:
    """Persist a fresh :class:`Source` row and return its id (R1).

    The expected ``count`` is recorded *before* any task is committed, so an interrupted
    ingest leaves a source whose recorded count exceeds the tasks that actually landed —
    exactly the signal :func:`_warn_if_incomplete` reports on a later re-submission (R7).
    """
    source = Source.new(path=path, fingerprint=fingerprint, task_count=count)
    Source.add(source)
    log.debug(f'Recorded source {source.id} for {path} ({count} tasks)')
    return source.id


def _warn_if_incomplete(source: Source) -> None:
    """R7: warn when fewer tasks landed for `source` than its recorded count."""
    if source.task_count is None:
        return
    landed = Task.count_for_source(source.id)
    if landed < source.task_count:
        log.warning(f'Prior submission of {source.path} appears incomplete: '
                    f'{landed} of {source.task_count} tasks present (source {source.id})')


def apply_source_gate(path: str, fingerprint: str, count: int, *,
                      repeat: bool, update: bool, restart: bool = False) -> Tuple[str, Optional[set]]:
    """Decide whether to submit, de-dup, or refuse a named-file submission.

    Consults the :class:`Source` table for the file's ``(path, fingerprint)`` and returns
    the ``source_id`` to stamp onto tasks plus an optional ``skip_fingerprints`` set — task
    identities already present in the same-path lineage, which the :class:`Loader` drops for
    de-dup. Raises :class:`~cmdkit.cli.ArgumentError` (→ ``exit_status.bad_argument``) on a
    refusal, with an actionable message. Emits the R18 detection logging (prior source,
    incomplete-prior warning, submit/de-dup intent, refusal reason).

    This is the shared gate for both entry points: ``hs submit`` calls it with
    ``restart=False`` (matrix R5-R10); ``hsx``/``hs cluster`` also passes ``restart`` (matrix
    R11-R16). Callers guarantee a *valid* flag combination — contradictory/ambiguous combos
    are rejected upstream in each app's ``check_arguments`` (``--update``+``--repeat`` R10/R16;
    ``hsx --update`` without ``--restart``/``--repeat`` R13; ``hsx --restart``+``--repeat``).

    Decision table (prior state of `path` P with fingerprint F):

    ==============  ==================  ============================  ============================
    flags           none                match (P,F seen)              differs (P seen, F changed)
    ==============  ==================  ============================  ============================
    (no flag)       new source, all     REFUSE, name prior (R5)       REFUSE, suggest --update (R6)
    --repeat        new source, all     new source, all (R8/R15)      new source, all (R8/R15)
    --update        new source, all     new source, novel-only (R9)   new source, novel-only (R9/R14)
    --restart       new source, all     reuse source, novel-only(R12) REFUSE, suggest --update (R12)
    ==============  ==================  ============================  ============================
    """
    match = Source.matching(path, fingerprint)
    lineage = Source.lookup(path)
    if lineage:
        _warn_if_incomplete(match or lineage[0])  # R7 — orthogonal to the submit/refuse decision
    if repeat:
        # R8 / R15 — brand-new source, submit everything even on an identical match.
        log.info(f'Submitting all {count} tasks from {path} as a new source (--repeat)')
        return _new_source_id(path, fingerprint, count), None
    if update:
        # R9 / R14 — new source version; submit only identities absent from the same-path lineage.
        skip = Task.fingerprints_for_sources([source.id for source in lineage])
        log.info(f'Updating {path}: {len(skip)} task identity(ies) already present; submitting only new tasks')
        return _new_source_id(path, fingerprint, count), skip
    if restart:
        # R12 — file-aware restart (cluster). Reusing the matched source keeps requeues
        # idempotent (no row/count drift); the server's revert-interrupted flow re-runs any
        # mid-flight task, so novel-only here submits exactly what never landed.
        if match:
            skip = Task.fingerprints_for_sources([source.id for source in lineage])
            log.info(f'Restarting {path} (source {match.id}): {len(skip)} present; submitting only new tasks')
            return match.id, skip
        if lineage:
            raise ArgumentError(f'File {path} differs from its prior submission (content changed); '
                                f'pass --update --restart to submit only the new tasks')
        log.info(f'Submitting all {count} tasks from {path} as a new source (--restart)')
        return _new_source_id(path, fingerprint, count), None
    # No gating flag — R5 / R6 / R11.
    if match:
        raise ArgumentError(f'File {path} was already submitted as source {match.id} '
                            f'({match.task_count} tasks, {match.created}); '
                            f'pass --repeat to submit it again, or --update to add only new tasks')
    if lineage:
        raise ArgumentError(f'A file at {path} was previously submitted with different content; '
                            f'pass --update to submit only new tasks, or --repeat to submit all again')
    log.info(f'Submitting all {count} tasks from {path} as a new source')
    return _new_source_id(path, fingerprint, count), None


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
    source: Iterator[str | dict]
    source_id: Optional[str]
    skip_fingerprints: Optional[set]
    queue: Queue[Optional[Task]]
    cores: Optional[int]
    memory: Optional[int]
    timeout: Optional[int]
    template: Template
    group: int
    count: int
    present: int
    tags: Dict[str, str]

    state = LoaderState.START
    states = LoaderState

    def __init__(self: Loader,
                 source: Iterable[str | dict],
                 queue: Queue[Optional[Task]],
                 cores: int = None,
                 memory: int = None,
                 timeout: int = None,
                 template: str = DEFAULT_TEMPLATE,
                 group: int = DEFAULT_TASK_GROUP,
                 tags: Dict[str, str] = None) -> None:
        """Initialize source to read tasks and submit to database.

        A :class:`GatedSource` is unwrapped here: its ``source_id`` is stamped onto every
        task and its ``skip_fingerprints`` (if any) drive de-dup. A plain iterable leaves
        both unset (no stamping, no de-dup) — unchanged behavior for every legacy caller.
        """
        if isinstance(source, GatedSource):
            self.source_id = source.source_id
            self.skip_fingerprints = source.skip_fingerprints
        else:
            self.source_id = None
            self.skip_fingerprints = None
        self.template = Template(template)
        self.source = iter(source)
        self.queue = queue
        self.cores = cores
        self.memory = memory
        self.timeout = timeout
        self.group = group
        self.tags = tags
        self.count = 0
        self.present = 0

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
        """Get the next item from the source and dispatch by its kind."""
        try:
            item = next(self.source)
        except StopIteration:
            return LoaderState.FINAL
        if isinstance(item, dict):
            return self.load_json_task(item)
        else:
            return self.load_line_task(item)

    def load_line_task(self: Loader, line: str) -> LoaderState:
        """Build the next task from a command-line string in the text source."""
        raw = str(line).strip()
        args = self.template.expand(raw)
        self.task = Task.new(args=args, raw_args=raw, source=self.source_id,
                             cores=self.cores, memory=self.memory, timeout=self.timeout,
                             group=self.group, tag=self.tags)
        if self.task.args:
            if self.skip_task():
                return LoaderState.GET
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

    def load_json_task(self: Loader, record: dict) -> LoaderState:
        """Build the next task from a JSON record (named `{key}` expansion + tags)."""
        # The optional `args` key is the base command; the template ({} -> base)
        # is expanded against the record as named context. All other keys become
        # tags (nested values are JSON-serialized; scalars are kept as-is).
        base = str(record.get('args', ''))
        args = self.template.expand(base, context=record)
        record_tags = {key: (json.dumps(value, separators=(',', ':')) if isinstance(value, (dict, list)) else value)
                       for key, value in record.items() if key != 'args'}
        tags = {**(self.tags or {}), **record_tags}
        self.task = Task.new(args=args, raw_args=base, source=self.source_id,
                             cores=self.cores, memory=self.memory, timeout=self.timeout,
                             group=self.group, tag=tags, strict_tag=False, parse_inline=False)
        if self.skip_task():
            return LoaderState.GET
        log.trace(f'Loaded task ({self.task.args})')
        return LoaderState.PUT

    def skip_task(self: Loader) -> bool:
        """True if the loaded task's identity is already present in the prior lineage (de-dup).

        Used by ``--update``/``--restart``: a task whose fingerprint is in the skip-set is
        counted as *present* and not enqueued (no ``count`` increment). Without a skip-set
        (the common case) this is always False.
        """
        if self.skip_fingerprints and self.task.fingerprint in self.skip_fingerprints:
            self.present += 1
            log.trace(f'Skipping already-present task ({self.task.args})')
            return True
        return False

    def put_task(self: Loader) -> LoaderState:
        """Enqueue loaded task."""
        try:
            self.queue.put(self.task, timeout=DEFAULT_LOCAL_TIMEOUT)
            self.count += 1
            return LoaderState.GET
        except QueueFull:
            return LoaderState.PUT

    def finalize(self: Loader) -> LoaderState:
        """Log the de-dup tally (when active) and return HALT."""
        if self.skip_fingerprints is not None:
            log.info(f'{self.present} tasks already present; submitting {self.count} new tasks')
        log.debug('Done (loader)')
        return LoaderState.HALT


class LoaderThread(Thread):
    """Run loader within dedicated thread."""

    def __init__(self: LoaderThread,
                 source: Iterable[str | dict],
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

    source: Iterable[str | dict]
    queue: Queue[Optional[Task]]
    loader: LoaderThread
    committer: DatabaseCommitterThread

    def __init__(self: SubmitThread,
                 source: Iterable[str | dict],
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
        if isinstance(self.source, GatedSource):
            return self.source.name or '<iterable>'
        elif self.source is sys.stdin:
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

    source: Iterable[str | dict]
    local: Queue[Optional[Task]]
    client: QueueClient
    loader: LoaderThread
    committer: QueueCommitterThread

    def __init__(self: LiveSubmitThread,
                 source: Iterable[str | dict],
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
        if isinstance(self.source, GatedSource):
            return self.source.name or '<iterable>'
        elif self.source is sys.stdin:
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


def submit_from(source: Iterable[str | dict],
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


def load_json_source(spec: str) -> Tuple[List[dict], Optional[str]]:
    """
    Load task records from a JSON `spec` together with its content fingerprint.

    Behaves exactly like :func:`load_json_tasks` (same ``FILE[@dotted.path]`` spec and
    validation) but reads the file bytes once and also returns their md5 hex digest — the
    content fingerprint the re-submission gate keys on, matching the md5 an ``hs submit``
    line file records. A `FILE` of ``-`` reads from ``<stdin>`` and yields a ``None``
    fingerprint (stdin JSON has no stable identity and stays gating-exempt).

    Returns:
        records (List[dict]): The list of task record objects.
        fingerprint (Optional[str]): md5 hex of the file bytes, or None for ``<stdin>``.
    """
    filepath, _, path = spec.partition('@')
    if not filepath:
        raise ArgumentError(f'Missing filename in --from-json spec: "{spec}"')
    fingerprint = None
    try:
        if filepath == '-':
            data = json.load(sys.stdin)
        else:
            with open(filepath, mode='rb') as stream:
                content = stream.read()
            fingerprint = hashlib.md5(content).hexdigest()
            data = json.loads(content)
    except FileNotFoundError as error:
        raise ArgumentError(f'File not found: {filepath}') from error
    except json.JSONDecodeError as error:
        raise ArgumentError(f'Invalid JSON in {filepath}: {error}') from error
    node = data
    if path:
        for segment in path.split('.'):
            if not isinstance(node, dict) or segment not in node:
                raise ArgumentError(f'Path "{path}" not found in {filepath} (no key "{segment}")')
            node = node[segment]
    location = f'"{path}"' if path else 'top level'
    if not isinstance(node, list):
        raise ArgumentError(f'Expected a list of task objects at {location} in {filepath}, '
                            f'found {type(node).__name__}')
    if not node:
        raise ArgumentError(f'Task list at {location} in {filepath} is empty')
    for index, record in enumerate(node):
        if not isinstance(record, dict):
            raise ArgumentError(f'Task {index} at {location} in {filepath} is not an object '
                                f'(found {type(record).__name__})')
    return node, fingerprint


def load_json_tasks(spec: str) -> List[dict]:
    """
    Load a list of task records from a JSON file `spec`.

    The `spec` is ``FILE[@dotted.path]``: split on the first ``@``, the remainder
    is a dotted path of object keys locating the task list inside the document
    (e.g. ``plan.json@results.tasks``). With no ``@`` the file's top level must
    itself be the list. A `FILE` of ``-`` reads from ``<stdin>``.

    The located node must be a non-empty list of objects; each object's key/values
    become both a task's tags and its ``{key}`` template-expansion context.

    Returns:
        records (List[dict]): The list of task record objects.
    """
    return load_json_source(spec)[0]


def json_source_key(spec: str) -> Optional[str]:
    """
    Absolute source key for a ``--from-json`` `spec`, or None for stdin (``-``).

    The key is ``abspath(FILE)`` with any ``@dotted.path`` selector preserved, so distinct
    node selections of one document are distinct sources (no false duplicate match) and it
    lines up with the absolute path an ``hs submit`` line file records. A `FILE` of ``-``
    (stdin JSON) has no stable path and returns None — gating-exempt, like ``<stdin>``.
    """
    filepath, _, _ = spec.partition('@')
    if filepath == '-':
        return None
    return os.path.abspath(filepath) + spec[len(filepath):]


def validate_json_expansion(records: List[dict], template: str = DEFAULT_TEMPLATE) -> None:
    """
    Fail-fast pre-flight: ensure every record in `records` fully expands against
    `template` (all ``{key}`` present, resulting command non-empty). Raises
    :class:`~cmdkit.cli.ArgumentError` naming the offending record on the first
    failure, before any task is committed.
    """
    engine = Template(template)
    for index, record in enumerate(records):
        base = str(record.get('args', ''))
        try:
            command = engine.expand(base, context=record)
        except Template.Error as error:
            raise ArgumentError(f'record {index}: {error}') from error
        if not command.strip():
            raise ArgumentError(f'record {index}: expands to an empty command '
                                f'(provide an "args" field or a --template with fields)')


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
  {APP_NAME} [-h] [ARGS... | -f FILE | --from-json SPEC] [-q [-H ADDR] [-p NUM] [-k KEY] | --initdb]
  {PAD_NAME} [-b NUM] [-w SEC] [-c NUM] [-m MEM] [-W SEC] [-g NUM] [--repeat | --update]
  {PAD_NAME} [--template CMD] [-t TAG...]
  {PAD_NAME} [--no-tls | [--tls-ca PATH] [--tls-cert PATH] [--tls-key PATH]]

  Submit one or more tasks.\
"""

APP_HELP: Final[str] = f"""\
{APP_USAGE}

  Submit a single command using positional arguments.
  If a filepath is provided, read many tasks from the file instead.
  With --from-json, read tasks from a JSON file and expand named "{{key}}"
  fields in the template from each task object (keys also become tags).
  Submit directly to a live queue with --queue.

Arguments:
  ARGS...                    Command-line task arguments.

Options:
  -f, --task-file    FILE    Path to task file ("-" for <stdin>).
      --from-json    SPEC    Read tasks from a JSON file ("FILE[@path]").
      --repeat               Re-submit a known file's tasks again as a new source.
      --update               Submit only tasks not already present from a known file.
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
    source_path: Optional[str] = None       # absolute path of a named task file (else None)
    source_id: Optional[str] = None         # resolved Source.id stamped onto submitted tasks
    json_fingerprint: Optional[str] = None  # content md5 of a --from-json file (else None)
    task_args: List[str] = []
    task_file: str = None
    from_json: str = None
    interface.add_argument('task_args', nargs='*')
    interface.add_argument('-f', '--task-file', default=task_file)
    interface.add_argument('--from-json', default=from_json, dest='from_json')

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

    repeat_mode: bool = False
    interface.add_argument('--repeat', action='store_true', dest='repeat_mode')

    update_mode: bool = False
    interface.add_argument('--update', action='store_true', dest='update_mode')

    auto_initdb: bool = False
    interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    queue: Optional[QueueConfig]
    queue_mode: bool = False
    queue_server: str = config.server.host
    queue_port: int = config.server.port
    queue_auth: str = config.server.auth
    interface.add_argument('-q', '--queue', action='store_true', dest='queue_mode')
    interface.add_argument('-H', '--host', default=queue_server, dest='queue_server')
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

    def check_arguments(self: SubmitApp) -> None:
        """Reject contradictory re-submission flags before doing any work (R10)."""
        if self.update_mode and self.repeat_mode:
            raise ArgumentError('Cannot combine --update with --repeat')

    def run(self: SubmitApp) -> None:
        """Run submit thread."""
        self.check_arguments()
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
            self.prepare_source()
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
                            group=self.group, tag=self.tags, source=self.source_id)
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
        if self.from_json:
            if self.task_args or self.task_file:
                raise ArgumentError('Cannot combine --from-json with -f/--task-file or positional arguments')
            records, self.json_fingerprint = load_json_source(self.from_json)
            validate_json_expansion(records, self.template)
            log.debug(f'Loaded {len(records)} tasks from JSON ({self.from_json})')
            self.source = records
            return
        if self.task_args and self.task_file:
            raise ArgumentError(f'Cannot specify both -f/--task-file and positional arguments')
        if self.task_file == '-':
            log.debug(f'Submitted from <stdin> (explicit)')
            self.source = sys.stdin
        elif self.task_file is not None:
            log.debug(f'Submitted from {self.task_file} (explicit)')
            self.source = open(self.task_file, mode='r')
            self.source_path = os.path.abspath(self.task_file)
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
                    self.source_path = os.path.abspath(filepath)
                else:
                    log.debug(f'Submitted single task (implicit - executable)')
                    self.source = None
            else:
                log.debug(f'Submitted single task (implicit)')
                self.source = None  # program name without arguments
        else:
            log.debug(f'Submitted single task (explicit)')
            self.source = None

    def prepare_source(self: SubmitApp) -> None:
        """Resolve the task Source, apply the re-submission gate, and wrap the stream (DB mode only).

        A named file passes through :func:`apply_source_gate` (R5-R10): a duplicate or
        changed-content re-submission is refused unless ``--repeat``/``--update`` relaxes it,
        and the resolved :class:`Source` row's ``task_count`` is the upfront-counted
        expectation, committed *before* any task so an incomplete prior stays detectable
        (R1/R7). ``<stdin>`` and single-command ``<direct>`` submissions resolve their
        reserved fixed-id rows and are exempt from gating (R3) — a gating flag there is a
        no-op and says so. A ``--from-json`` source is gated too (see :meth:`prepare_json_source`).
        """
        if not DATABASE_ENABLED:
            return  # non-persistent DB: nothing to record against (invariant §4)
        if self.from_json:
            self.prepare_json_source()
            return
        if self.source is None:
            self.warn_gating_no_effect('<direct>')
            self.source_id = Source.reserved(DIRECT_SOURCE_ID).id
        elif self.source is sys.stdin or not self.source.seekable():
            # Real <stdin> and non-seekable named inputs (process substitution, FIFOs, a
            # piped /dev/stdin) have no stable identity and cannot be re-read for an upfront
            # count — stream them under the reserved <stdin> source, exempt from gating. A
            # second read would drain the pipe and silently drop every task.
            self.warn_gating_no_effect('<stdin>')
            self.source_id = Source.reserved(STDIN_SOURCE_ID).id
            name = '<stdin>' if self.source is sys.stdin else self.source_path
            self.source = GatedSource(self.source, self.source_id, name=name)
        else:
            fingerprint, count = source_fingerprint_and_count(self.source_path, self.template)
            log.info(f'Found {count} tasks in {self.source_path} (md5={fingerprint})')
            self.source_id, skip = apply_source_gate(self.source_path, fingerprint, count,
                                                     repeat=self.repeat_mode, update=self.update_mode)
            self.source = GatedSource(self.source, self.source_id, skip_fingerprints=skip,
                                      name=self.source_path)

    def prepare_json_source(self: SubmitApp) -> None:
        """Apply the re-submission gate to a ``--from-json`` source (R4/R5/R8/R9).

        The JSON file's content md5 (captured in :meth:`check_source`, no second read) and
        record count feed the same :func:`apply_source_gate` as a line file, keyed by
        ``abspath(FILE)[@node]``. The per-task fingerprint keys off the pre-template ``args``
        and tags (R2), so ``--update`` de-dup matches whether the tasks were first ingested
        here or from a line file. ``--from-json -`` (stdin JSON) has no stable path and is
        exempt, carrying the reserved ``<stdin>`` source (R3). Called in DB mode only.
        """
        key = json_source_key(self.from_json)
        if key is None:
            self.warn_gating_no_effect('<stdin>')
            self.source_id = Source.reserved(STDIN_SOURCE_ID).id
            self.source = GatedSource(self.source, self.source_id, name='<stdin>')
            return
        count = len(self.source)
        log.info(f'Found {count} tasks in {key} (md5={self.json_fingerprint})')
        self.source_id, skip = apply_source_gate(key, self.json_fingerprint, count,
                                                 repeat=self.repeat_mode, update=self.update_mode)
        self.source = GatedSource(self.source, self.source_id, skip_fingerprints=skip, name=key)

    def warn_gating_no_effect(self: SubmitApp, kind: str) -> None:
        """Note that re-submission gating flags are inert for exempt sources (R3)."""
        if self.repeat_mode or self.update_mode:
            log.warning(f'--repeat/--update have no effect for {kind} submissions')

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
        source = self.source.iterable if isinstance(self.source, GatedSource) else self.source
        if source is not None and source is not sys.stdin and hasattr(source, 'close'):
            source.close()
