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
import threading
from enum import Enum
from datetime import datetime
from dataclasses import dataclass
from queue import Queue, Empty as QueueEmpty, Full as QueueFull
from multiprocessing import AuthenticationError

# External libs
from cmdkit.config import ConfigurationError
from cmdkit.app import Application, exit_status
from cmdkit.cli import Interface, ArgumentError

# Internal libs
from hypershell.core.logging import Logger
from hypershell.core.config import (config, default, resolve_auth, refuse_key_option, AUTH_ENV, SERVER_KEY_SOURCES,
                                    client_host)
from hypershell.core.fsm import State, StateMachine
from hypershell.core.tls import TLSConfig, from_namespace as tls_from_namespace
from hypershell.core.queue import (QueueClient, QueueConfig, QueueSubmitRefused,
                                  DEFAULT_LOCAL_TIMEOUT, DEFAULT_REMOTE_TIMEOUT)
from hypershell.core.thread import Thread, join_waking
from hypershell.core.template import Template, DEFAULT_TEMPLATE
from hypershell.core.types import JSONData, ranged
from hypershell.core.config_schema import SETTINGS, figure
from hypershell.core.pretty_print import format_tag
from hypershell.core.exceptions import (handle_exception, handle_disconnect,
                                        handle_address_unknown, HostAddressInfo, get_shared_exception_mapping,
                                        ReadingMixin, reading)
from hypershell.data.model import (Task, Source, Zone, DIRECT_SOURCE_ID, STDIN_SOURCE_ID, DEFAULT_TASK_GROUP,
                                   TaskGroupInfo, TaskGroupError, TaskInputError, ensure_group, ensure_resources, read_resources,
                                   RESOURCE_SETTINGS, read_input_tokens,
                                   serialize_tasks, zone_name)
from hypershell.data import initdb, checkdb, DATABASE_DIALECT, DATABASE_ENABLED

# Public interface
__all__ = ['submit_from', 'submit_file', 'load_json_tasks', 'load_json_source', 'json_source_key',
           'validate_json_expansion', 'validate_json_groups', 'validate_json_records', 'TagAdvice',
           'read_tag_options', 'advise_one_task',
           'GatedSource', 'InputScan', 'scan_task_lines', 'read_in_full', 'source_fingerprint', 'is_file_input',
           'source_name',
           'source_fingerprint_and_count', 'apply_source_gate', 'decide_source_gate', 'GateDecision',
           'task_fingerprints', 'json_fingerprints', 'refuse_below_reached_group',
           'SubmitThread', 'LiveSubmitThread', 'SubmitApp',
           'DEFAULT_TASK_GROUP', 'DEFAULT_BUNDLESIZE', 'DEFAULT_BUNDLEWAIT', 'DEFAULT_TEMPLATE']

# Initialize logger
log = Logger.with_name(__name__)


# Redefined for docs
DEFAULT_TASK_GROUP: Final[int] = DEFAULT_TASK_GROUP
"""Default task group for backwards compatibility."""


class GatedSource:
    """Wrap a task source with gate context that rides *inside* the source iterable.

    Carries the resolved ``source_id`` and optional ``zone_id`` (stamped onto every task by
    the :class:`Loader`) and an optional ``skip_fingerprints`` set — task identities already present in a prior
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
                 name: Optional[str] = None,
                 zone_id: Optional[str] = None) -> None:
        """Initialize wrapper around `iterable` with gate context."""
        self.iterable = iterable
        self.source_id = source_id
        self.skip_fingerprints = skip_fingerprints
        self.name = name
        self.zone_id = zone_id

    def __iter__(self: GatedSource) -> Iterator[str | dict]:
        """Delegate iteration to the wrapped source."""
        return iter(self.iterable)


def is_file_input(source: Iterable[str | dict]) -> bool:
    """True if `source` is a whole task file: a seekable file (not standard input), or a list or tuple.

    Standard input, pipes, FIFOs, process substitution and other iterables are streams, which
    are scheduled as they arrive.
    """
    # A gated source carries the stream it wraps as `iterable`.
    inner = getattr(source, 'iterable', source)
    if isinstance(inner, (list, tuple)):
        return True
    if isinstance(inner, io.IOBase) and inner is not sys.stdin:
        try:
            return inner.seekable()
        except (OSError, ValueError):
            return False
    return False


def source_name(source: Iterable[str | dict]) -> str:
    """Name of a task source for messages: a gated source's name, ``<stdin>``, a file's name, or ``<iterable>``."""
    if isinstance(source, GatedSource):
        return source.name or '<iterable>'
    elif source is sys.stdin:
        return '<stdin>'
    elif isinstance(source, io.TextIOWrapper):
        return source.name
    else:
        return '<iterable>'


def split_task_line(line: str, template: Template) -> Tuple[str, str, Dict[str, JSONData]]:
    """Split a raw text `line` into its command, the command expanded by `template`, and its inline tags.

    The raw line is split before the template is applied, so a comment line, a blank line and a
    tag-only line are never tasks, and a ``#`` in the template text stays in the command. The
    expanded command is empty for a line that is not a task.
    """
    command, inline_tags = Task.split_argline(str(line).strip())
    return command, (template.expand(command).strip() if command else ''), inline_tags


def line_is_task(line: str, template: Template) -> bool:
    """True if `line` yields a task under `template`.

    This is the :class:`Loader`'s own task/non-task split (:func:`split_task_line`): blank,
    comment and tag-only lines are not tasks, so an upfront count matches exactly what the
    Loader will submit.
    """
    return bool(split_task_line(line, template)[1])


@dataclass
class InputScan:
    """What input read in full holds: its task count and the group of each task, in order.

    The count is exactly the Loader's (:func:`line_is_task`). A task's group is its inline group,
    else the group a tag-only line or the submission's tags set before it, else the submission's
    default group (None in queue mode, where the server has no database to choose one).
    """

    count: int
    groups: List[Optional[int]]


class TagAdvice:
    """Tasks whose resource tags are past an advisory limit, counted for one summary per submission."""

    logged: set = set()  # The settings this process has summarized

    def __init__(self: TagAdvice) -> None:
        """Start with no task counted."""
        self.count: Dict[str, int] = {}
        self.first: Dict[str, str] = {}

    def note(self: TagAdvice, tags: Dict[str, JSONData], origin: str) -> None:
        """Count the task whose `tags` came from `origin`, for each resource tag past its advisory limit."""
        for name, setting in RESOURCE_SETTINGS.items():
            if name in tags and SETTINGS[setting].advised(tags[name]):
                self.count[setting] = self.count.get(setting, 0) + 1
                self.first.setdefault(setting, origin)

    def log(self: TagAdvice) -> None:
        """One WARNING for each setting past its advisory limit: the count of tasks and the first one."""
        for setting, count in self.count.items():
            if setting in TagAdvice.logged:
                continue
            TagAdvice.logged.add(setting)
            entry = SETTINGS[setting]
            direction, limit, reason = entry.advise
            tasks = '1 task' if count == 1 else f'{count:,} tasks'
            log.warning(f'{setting}: {tasks} {direction} the advised {figure(limit)}{entry.unit} ({reason}), '
                        f'the first at {self.first[setting]}; running anyway')


def read_tag_options(taglist: Optional[List[str]]) -> Dict[str, JSONData]:
    """The fields and tags given by `-t` (`KEY=VALUE`, `KEY:VALUE` or `KEY` tokens), as an inline directive reads them.

    A declarable field (group, zone, cores, memory, timeout) is read from its text as written, as
    its option reads it; every other key is a tag checked by the tag rules
    (:func:`~hypershell.data.model.read_input_tokens`). Raises
    :class:`~hypershell.data.model.TaskInputError`, for the command to refuse with exit status 2
    before anything is written.
    """
    return read_input_tokens(taglist or [])


def advise_one_task(args: str, tags: Optional[Dict[str, JSONData]] = None) -> None:
    """One advisory summary for a single task given as arguments: its `-t` `tags` with its inline tags over them."""
    advice = TagAdvice()
    advice.note({**(tags or {}), **Task.split_argline(args)[1]}, 'the command line')
    advice.log()


def task_lines(lines: Iterable[str], *, template: str = DEFAULT_TEMPLATE,
               tags: Optional[Dict[str, JSONData]] = None,
               name: str = '<input>', advice: Optional[TagAdvice] = None) -> Iterator[Tuple[str, str, Dict[str, JSONData]]]:
    """Replay the Loader's line rules over `lines`: each task's command, expanded command and tags.

    Lines are split as the Loader splits them (:func:`split_task_line`) and numbered from 1 as
    physical lines. A tag-only line updates a running copy of `tags`, and only after its group is
    accepted; a task's tags are the running tags with its inline tags over them. A group that is
    not an integer, or a resource tag out of its range, raises
    :class:`~hypershell.data.model.TaskInputError` naming the value as written and ``{name}, line {n}``.
    A task whose resource tags are past an advisory limit is counted in `advice`.
    """
    engine = Template(template)
    running = dict(tags or {})
    for number, line in enumerate(lines, start=1):
        try:
            command, args, inline_tags = split_task_line(line, engine)
        except TaskInputError as error:
            raise error.at(f'{name}, line {number}') from error
        if args:
            if advice is not None:
                advice.note({**running, **inline_tags}, f'{name}, line {number}')
            yield command, args, {**running, **inline_tags}
        elif not command and inline_tags:
            running.update(inline_tags)


def scan_task_lines(lines: Iterable[str], *, template: str = DEFAULT_TEMPLATE,
                    group: Optional[int] = DEFAULT_TASK_GROUP, tags: Optional[Dict[str, JSONData]] = None,
                    name: str = '<input>') -> InputScan:
    """Check every line of input read in full before anything is written, and count its tasks.

    The Loader's own line rules (:func:`task_lines`) are replayed over `lines`; a task's group is
    its tags' group, else `group`. Nothing is read from or written to the database.
    """
    advice = TagAdvice()
    groups = [task_tags.get('group', group)
              for _, _, task_tags in task_lines(lines, template=template, tags=tags, name=name, advice=advice)]
    advice.log()
    return InputScan(count=len(groups), groups=groups)


def task_fingerprints(lines: Iterable[str], *, template: str = DEFAULT_TEMPLATE,
                      group: Optional[int] = DEFAULT_TASK_GROUP,
                      tags: Optional[Dict[str, JSONData]] = None) -> List[str]:
    """The identity of each task in `lines`, in order, as the Loader builds it (see :meth:`Loader.load_line_task`)."""
    return [Task.new(args=args, raw_args=command, parse_inline=False, group=group, tag=task_tags).fingerprint
            for command, args, task_tags in task_lines(lines, template=template, tags=tags)]


def json_record_tags(record: dict) -> Dict[str, JSONData]:
    """The tags of a JSON task record: every key but ``args``, nested values serialized as JSON.

    The resource tags (``cores``, ``memory``, ``timeout``) are read by their entries, as
    :func:`validate_json_records` checked them, so the task's columns hold whole numbers (``"4.0"``
    is 4, a ``timeout`` of 90.5 is 91). The record itself is left as given: a template expands its
    fields as written (``{memory}`` stays ``2G``).
    """
    return read_resources({key: (json.dumps(value, separators=(',', ':')) if isinstance(value, (dict, list)) else value)
                           for key, value in record.items() if key != 'args'})


def json_fingerprints(records: List[dict], *, group: Optional[int] = DEFAULT_TASK_GROUP,
                      tags: Optional[Dict[str, JSONData]] = None) -> List[str]:
    """The identity of each JSON record's task, in order, as the Loader builds it (see :meth:`Loader.load_json_task`).

    The identity is taken over the record's pre-template ``args``, so no template is needed.
    """
    return [Task.new(args=str(record.get('args', '')), raw_args=str(record.get('args', '')), group=group,
                     tag={**(tags or {}), **json_record_tags(record)}, strict_tag=False,
                     parse_inline=False).fingerprint
            for record in records]


def source_fingerprint(path: str) -> str:
    """The content md5 of a named file, over its raw bytes (parsing-independent), read in chunks."""
    digest = hashlib.md5()
    with open(path, mode='rb') as raw_stream:
        for chunk in iter(lambda: raw_stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint_and_count(path: str, template: str = DEFAULT_TEMPLATE) -> Tuple[str, int]:
    """Read a named file upfront: content md5 (raw bytes) + task count.

    The count is :func:`scan_task_lines` over a *text-mode* read, the same universal-newline
    decoding the Loader uses, so it matches exactly what the Loader will submit for any newline
    convention (LF, CRLF, or bare CR), and a group that is not an integer is refused first. Callers
    must route only seekable, re-readable files here; non-seekable inputs (pipes, FIFOs, piped
    ``/dev/stdin``) are handled upstream, because a second independent read would drain them.
    """
    with open(path, mode='r') as text_stream:
        scan = scan_task_lines(text_stream, template=template, name=path)
    return source_fingerprint(path), scan.count


def read_in_full(stream: Iterable[str]) -> List[str]:
    """Every line of `stream`, one element per physical line, read to its end before anything is inserted.

    The read runs in a worker thread and the main thread waits for it in bounded steps
    (:func:`~hypershell.core.thread.join_waking`), so a signal taken by any thread is still handled
    while the input stays open. The lines are held in memory.
    """
    reader = StreamReader(stream)
    reader.start()
    join_waking(reader)
    return reader.lines


class StreamReader(Thread):
    """Read a stream to its end in a dedicated thread."""

    def __init__(self: StreamReader, stream: Iterable[str]) -> None:
        """Initialize with the stream to read."""
        super().__init__(name='hypershell-submit-reader')
        self.stream = stream
        self.lines: List[str] = []

    def run_with_exceptions(self: StreamReader) -> None:
        """Read every line."""
        self.lines = list(self.stream)


def _new_source_id(path: str, fingerprint: str, count: int) -> str:
    """Persist a fresh :class:`Source` row and return its id.

    The expected ``count`` (the file's full task count) is recorded *before* any task is
    committed, so an interrupted ingest leaves the same-path lineage holding fewer tasks than
    expected — the signal :func:`_warn_if_incomplete` reports on a later re-submission,
    measured across the lineage so a de-duplicated submission does not read as incomplete.
    """
    source = Source.new(path=path, fingerprint=fingerprint, task_count=count)
    Source.add(source)
    log.debug(f'Recorded source {source.id} for {path} ({count} tasks)')
    return source.id


def _warn_if_incomplete(source: Source, lineage: List[Source]) -> None:
    """Warn when fewer tasks landed across the same-path `lineage` than `source` expects.

    Completeness is measured against the *whole* same-path lineage, not ``source`` alone.
    De-dup deliberately distributes a file version's tasks across the lineage —
    the novel ones onto the newest source, the rest already present under earlier same-path
    sources — so a per-source count structurally under-counts a de-duplicated submission and
    would cry wolf on a re-submission that is in fact complete. Each ``count_for_source`` is
    index-backed, and a lineage is small (re-submissions of one path), so the sum stays cheap.
    """
    if source.task_count is None:
        return
    landed = sum(Task.count_for_source(prior.id) for prior in lineage)
    if landed < source.task_count:
        log.warning(f'Prior submission of {source.path} appears incomplete: '
                    f'{landed} of {source.task_count} tasks present (source {source.id})')


@dataclass
class GateDecision:
    """The re-submission gate's decision for a named source, made before anything is written.

    ``source_id`` is a prior source to reuse (a same-file ``--restart``), else None: a new
    :class:`Source` row is recorded on :meth:`commit`. ``skip`` is the set of task identities
    already present in the same-path lineage, which the :class:`Loader` drops (None: no de-dup).
    """

    path: str
    fingerprint: str
    count: int
    source_id: Optional[str] = None
    skip: Optional[set] = None

    def commit(self: GateDecision) -> Tuple[str, Optional[set]]:
        """Write the decision: record a new Source row unless a prior one is reused; return its id and the skip set."""
        if self.source_id is None:
            self.source_id = _new_source_id(self.path, self.fingerprint, self.count)
        return self.source_id, self.skip


def apply_source_gate(path: str, fingerprint: str, count: int, *,
                      repeat: bool, update: bool, restart: bool = False) -> Tuple[str, Optional[set]]:
    """Decide whether to submit, de-dup, or refuse a named-file submission, and record it.

    The composition of :func:`decide_source_gate` and :meth:`GateDecision.commit`: returns the
    ``source_id`` to stamp onto tasks plus an optional ``skip_fingerprints`` set.
    """
    return decide_source_gate(path, fingerprint, count, repeat=repeat, update=update, restart=restart).commit()


def decide_source_gate(path: str, fingerprint: str, count: int, *,
                       repeat: bool, update: bool, restart: bool = False) -> GateDecision:
    """Decide whether to submit, de-dup, or refuse a named-file submission, writing nothing.

    Consults the :class:`Source` table for the file's ``(path, fingerprint)`` and returns the
    decision: a prior ``source_id`` to reuse or a new source to record on
    :meth:`GateDecision.commit`, plus an optional ``skip`` set — task identities already present
    in the same-path lineage, which the :class:`Loader` drops for de-dup. Raises
    :class:`~cmdkit.cli.ArgumentError` (→ ``exit_status.bad_argument``) on a refusal, with an
    actionable message. Emits detection logging (prior source, incomplete-prior warning,
    submit/de-dup intent, refusal reason). Checks made between the decision and its commit can
    still refuse with no Source row written.

    This is the shared gate for both entry points: ``hs submit`` calls it with
    ``restart=False``; ``hsx``/``hs cluster`` also passes ``restart``. Callers guarantee a
    *valid* flag combination — contradictory/ambiguous combos are rejected upstream in each
    app's ``check_arguments`` (``--update``+``--repeat``; ``hsx --update`` without
    ``--restart``/``--repeat``; ``hsx --restart``+``--repeat``).

    Decision table (prior state of `path` P with fingerprint F):

    ==============  ==================  ============================  ============================
    flags           none                match (P,F seen)              differs (P seen, F changed)
    ==============  ==================  ============================  ============================
    (no flag)       new source, all     REFUSE, name prior            REFUSE, suggest --update
    --repeat        new source, all     new source, all               new source, all
    --update        new source, all     new source, novel-only        new source, novel-only
    --restart       new source, all     reuse source, novel-only      REFUSE, suggest --update
    ==============  ==================  ============================  ============================
    """
    match = Source.matching(path, fingerprint)
    lineage = Source.lookup(path)
    if lineage:
        _warn_if_incomplete(match or lineage[0], lineage)  # Orthogonal to the submit/refuse decision.
    if repeat:
        # Brand-new source; submit everything even on an identical match.
        log.info(f'Submitting all {count} tasks from {path} as a new source (--repeat)')
        return GateDecision(path, fingerprint, count)
    if update:
        # New source version; submit only identities absent from the same-path lineage.
        skip = Task.fingerprints_for_sources([source.id for source in lineage])
        log.info(f'Updating {path}: {len(skip)} task identity(ies) already present; submitting only new tasks')
        return GateDecision(path, fingerprint, count, skip=skip)
    if restart:
        # File-aware restart (cluster). Reusing the matched source keeps requeues
        # idempotent (no row/count drift); the server's revert-interrupted flow re-runs any
        # mid-flight task, so novel-only here submits exactly what never landed.
        if match:
            skip = Task.fingerprints_for_sources([source.id for source in lineage])
            log.info(f'Restarting {path} (source {match.id}): {len(skip)} present; submitting only new tasks')
            return GateDecision(path, fingerprint, count, source_id=match.id, skip=skip)
        if lineage:
            raise ArgumentError(f'File {path} differs from its prior submission (content changed); '
                                f'pass --update --restart to submit only the new tasks')
        log.info(f'Submitting all {count} tasks from {path} as a new source (--restart)')
        return GateDecision(path, fingerprint, count)
    # No gating flag: refuse a known (path, fingerprint), else submit as a new source.
    if match:
        raise ArgumentError(f'File {path} was already submitted as source {match.id} '
                            f'({match.task_count} tasks, {match.created}); '
                            f'pass --repeat to submit it again, or --update to add only new tasks')
    if lineage:
        raise ArgumentError(f'A file at {path} was previously submitted with different content; '
                            f'pass --update to submit only new tasks, or --repeat to submit all again')
    log.info(f'Submitting all {count} tasks from {path} as a new source')
    return GateDecision(path, fingerprint, count)


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
    name: str
    line: int
    refuse_lines: bool

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

        A stream (:func:`is_file_input` is false) refuses a line whose group is not an integer and
        reads on; whole input raises instead, since it is checked whole before it reaches the Loader.
        """
        if isinstance(source, GatedSource):
            self.source_id = source.source_id
            self.skip_fingerprints = source.skip_fingerprints
            self.zone_id = source.zone_id
        else:
            self.source_id = None
            self.skip_fingerprints = None
            self.zone_id = None
        self.template = Template(template)
        self.source = iter(source)
        self.queue = queue
        self.cores = cores
        self.memory = memory
        self.timeout = timeout
        self.group = group
        self.tags = dict(tags or {})  # A copy: a tag-only line updates the Loader's tags, never the caller's.
        self.count = 0
        self.present = 0
        self.name = source_name(source)
        self.line = 0
        self.refuse_lines = not is_file_input(source)
        self.advice = TagAdvice()

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
        """Get the next item from the source and dispatch by its kind.

        Text lines are numbered from 1, every physical line counted. On a stream, a line refused as
        task input (a group that is not an integer, a field out of its range, a likely typo of a
        column, a tag the tag rules refuse) is refused alone: an ERROR names it and the Loader reads
        on, and a refused tag-only line sets nothing. Any other error still ends the input.
        """
        try:
            item = next(self.source)
        except StopIteration:
            return LoaderState.FINAL
        if isinstance(item, dict):
            return self.load_json_task(item)
        self.line += 1
        try:
            return self.load_line_task(item)
        except TaskInputError as error:
            if not self.refuse_lines:
                raise error.at(f'{self.name}, line {self.line}') from error
            log.error(f'Refused line {self.line} of {self.name}: {error}')
            return LoaderState.GET

    def load_line_task(self: Loader, line: str) -> LoaderState:
        """Build the next task from a command-line string in the text source.

        The raw line is split first (:func:`split_task_line`) and the template expands only its
        command. A tag-only line sets its tags for the lines after it; the declarable fields (group,
        zone, cores, memory and timeout) are carried as tags and taken out again by :meth:`Task.new`.
        """
        command, args, inline_tags = split_task_line(line, self.template)
        if args:
            self.advice.note({**self.tags, **inline_tags}, f'{self.name}, line {self.line}')
            self.task = Task.new(args=args, raw_args=command, parse_inline=False,
                                 source=self.source_id, zone=self.zone_id,
                                 cores=self.cores, memory=self.memory, timeout=self.timeout,
                                 group=self.group, tag={**self.tags, **inline_tags})
            if self.skip_task():
                return LoaderState.GET
            log.trace(f'Loaded task ({self.task.args})')
            return LoaderState.PUT
        if not command and inline_tags:
            tagline = ', '.join(format_tag(k, v) for k, v in inline_tags.items())
            log.debug(f'Setting global attribute or tag: {tagline}')
            self.tags.update(inline_tags)
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
        tags = {**(self.tags or {}), **json_record_tags(record)}
        self.task = Task.new(args=args, raw_args=base, source=self.source_id, zone=self.zone_id,
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
        """Log the de-dup tally (when active) and a stream's advisory summary, and return HALT.

        Input read in full was summarized as it was checked, so only a stream's tags are summarized here.
        """
        if self.refuse_lines:
            self.advice.log()
        if self.skip_fingerprints is not None:
            log.info(f'{self.present} tasks already present; submitting {self.count} new tasks')
        log.debug('Done (loader)')
        return LoaderState.HALT


LOADER_JOIN_INTERVAL: Final[float] = 0.1
"""Seconds between checks, while waiting on a loader, that it has not been left to its input."""


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
        self.left = threading.Event()

    def run_with_exceptions(self: LoaderThread) -> None:
        """Run machine."""
        self.machine.run()

    def stop(self: LoaderThread, wait: bool = False, timeout: int = None) -> None:
        """Stop machine."""
        log.warning('Stopping (loader)')
        self.machine.halt()
        super().stop(wait=wait, timeout=timeout)

    def leave(self: LoaderThread) -> None:
        """Stop machine without waiting on its source: a read already blocked on its input is left to end alone.

        Nothing read after this is enqueued. :meth:`join_unless_left` returns at once.
        """
        log.warning('Stopping (loader: input left unread)')
        self.machine.halt()
        self.left.set()

    def join_unless_left(self: LoaderThread) -> None:
        """Wait for the loader to read its source to the end, or until it is left (:meth:`leave`)."""
        while self.is_alive() and not self.left.is_set():
            self.join(timeout=LOADER_JOIN_INTERVAL)
        if not self.is_alive():
            self.join()  # Re-raises the failure that ended the loader, if any


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
        log.debug(f'Started ({source_name(self.source)})')
        self.loader.start()
        self.committer.start()
        self.loader.join_unless_left()
        self.put_end()
        self.committer.join()
        log.debug('Done')

    def stop(self: SubmitThread, wait: bool = False, timeout: int = None, leave_input: bool = False) -> None:
        """Stop child threads before main thread.

        With `leave_input`, a loader blocked reading its input (standard input from a producer that stays
        open) is not waited on: what is committed stays committed, and the rest of the input is not read.
        Tasks still queued when the committer halts are not committed, and the end marker is not waited
        on once the committer has ended (:meth:`put_end`).
        """
        log.warning('Stopping')
        if leave_input:
            self.loader.leave()
            self.put_end()
        else:
            self.loader.stop(wait=wait, timeout=timeout)
            self.queue.put(None)
        self.committer.stop(wait=wait, timeout=timeout)
        super().stop(wait=wait, timeout=timeout)

    def put_end(self: SubmitThread) -> None:
        """Offer the committer the end of tasks, waiting for room until it is placed.

        Once the loader is left to its input (:meth:`LoaderThread.leave`), the wait gives up when the
        committer has ended: the committer is halted without draining the queue, so a marker that
        waited for room would never be placed.
        """
        while True:
            may_give_up = self.loader.left.is_set() and not self.committer.is_alive()
            try:
                self.queue.put(None, block=not may_give_up, timeout=DEFAULT_LOCAL_TIMEOUT)
                return
            except QueueFull:
                if may_give_up:
                    return  # Nothing will take it: the committer has ended

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
    halted: bool = False

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
        while self.tasks and not self.halted:  # Force retry if queue is busy (since v2.8.0)
            self.commit()
        if self.tasks:
            log.warning(f'Stopped with {len(self.tasks)} tasks not committed (committer: no database)')
        log.debug('Done (committer: no database)')
        return QueueCommitterState.HALT

    def halt(self: QueueCommitter) -> None:
        """Halt, including a final commit still waiting for room on the server's queue."""
        self.halted = True
        super().halt()


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
        log.debug(f'Started ({source_name(self.source)})')
        with self.client:
            self.check_server()
            self.loader.start()
            self.committer.start()
            log.trace('Waiting (loader)')
            self.loader.join_unless_left()
            self.put_end()
            log.trace('Waiting (committer)')
            self.committer.join()
        log.debug('Done')

    def check_server(self: LiveSubmitThread) -> None:
        """Refuse a server that does not serve its live queue: one with a database, which schedules from it."""
        if not self.client.dispatcher.takes_submissions():
            raise QueueSubmitRefused(f'Server at {self.client.config.host}:{self.client.config.port} schedules '
                                     f'from its database and does not serve tasks submitted with --queue; '
                                     f'submit without --queue, or start the server with --no-db')

    def stop(self: LiveSubmitThread, wait: bool = False, timeout: int = None, leave_input: bool = False) -> None:
        """Stop child threads before main thread.

        The committer is halted before its end marker is offered: with no client taking tasks from the
        server, the committer cannot drain the local queue, and a marker that waited for room would
        never be placed. With `leave_input`, a loader blocked reading its input (standard input from a
        producer that stays open) is not waited on, and the rest of the input is not read.
        """
        log.warning('Stopping')
        if leave_input:
            self.loader.leave()
        else:
            self.loader.stop(wait=wait, timeout=timeout)
        self.committer.stop(wait=wait, timeout=timeout)
        self.put_end()
        super().stop(wait=wait, timeout=timeout)

    def put_end(self: LiveSubmitThread) -> None:
        """Offer the committer the end of tasks, waiting for room only while the committer runs to make it."""
        while True:
            committer_running = self.committer.is_alive()
            try:
                self.local.put(None, block=committer_running, timeout=DEFAULT_LOCAL_TIMEOUT)
                return
            except QueueFull:
                if not committer_running:
                    return  # Nothing will take it: the committer has ended

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

    A list or a seekable file is whole input: a line whose task group is not an integer raises
    :class:`~hypershell.data.model.TaskGroupError`, after any earlier bundles were committed (the
    command line checks the whole input first with :func:`scan_task_lines`). Any other iterable is
    a stream, which refuses such a line alone, with an ERROR, and reads on.

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
        join_waking(thread)
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


def validate_json_records(records: List[dict]) -> None:
    """Fail-fast pre-flight: the ``group`` of every record in `records` is a JSON integer in range, and its
    resource tags (``cores``, ``memory``, ``timeout``) are inside their ranges.

    Raises :class:`~hypershell.data.model.TaskInputError` naming the value and its record (numbered
    from 0, as :func:`validate_json_expansion` does) on the first refusal, before any task is
    committed. Records past an advisory limit give one summary.
    """
    advice = TagAdvice()
    for index, record in enumerate(records):
        try:
            if 'group' in record:
                ensure_group(record['group'])
            advice.note(ensure_resources({name: record[name] for name in RESOURCE_SETTINGS if name in record}),
                        f'record {index}')
        except TaskInputError as error:
            raise error.at(f'record {index}') from error
    advice.log()


validate_json_groups = validate_json_records  # The name callers knew it by


def refuse_below_reached_group(groups: List[Optional[int]], reached: Optional[int], *,
                               skip: Optional[set] = None,
                               fingerprints: Optional[Callable[[], List[str]]] = None) -> None:
    """Refuse a submission holding a task below the database's reached group, all or nothing.

    `groups` are the groups of the input's tasks, in order, and `reached` is
    :meth:`~hypershell.data.model.Task.reached_group` (None: any group is accepted). A task whose
    identity is in the gate's `skip` set is not being submitted and is not checked; `fingerprints`
    gives every task's identity, in the same order, and is called only when a task below the
    reached group could be skipped. A task with no group of its own (None) is not checked. Raises
    :class:`~cmdkit.cli.ArgumentError` (exit 2) naming the lowest offending group, the reached
    group and the ways forward.
    """
    if reached is None:
        return
    below = [index for index, group in enumerate(groups) if isinstance(group, int) and group < reached]
    if below and skip and fingerprints is not None:
        identities = fingerprints()
        below = [index for index in below if identities[index] not in skip]
    if below:
        lowest = min(groups[index] for index in below)
        raise ArgumentError(f'Task group {lowest} is below the reached group {reached} of the database '
                            f'(nothing submitted); submit at group {reached} or higher, wait for the run '
                            f'to finish, or use a fresh database')


DEFAULT_HOST: Final[str] = QueueConfig.host
"""Default host for server connection."""

DEFAULT_PORT: Final[int] = QueueConfig.port
"""Default port for server connection."""

DEFAULT_AUTH: Final[str] = QueueConfig.auth
"""Default authentication key for server (**DO NOT USE THIS**)."""


SUBMIT_KEY_ADVICE: Final[str] = f'{SERVER_KEY_SOURCES} A task\'s own {{option}} goes after "--".'
"""The submit refusal also says where an option meant for the task goes."""


APP_NAME: Final[str] = 'hs submit'
PAD_NAME: Final[str] = ' ' * len(APP_NAME)
APP_USAGE: Final[str] = f"""\
Usage:
  {APP_NAME} [-h] [ARGS... | -f FILE | --from-json SPEC] [-q [-H ADDR] [-p NUM] | --initdb]
  {PAD_NAME} [-b NUM] [-w SEC] [-c NUM] [-m MEM] [-W SEC] [-g NUM] [--repeat | --update]
  {PAD_NAME} [--template CMD] [-t TAG...] [--zone NAME]
  {PAD_NAME} [--no-tls | [--tls-ca PATH] [--tls-cert PATH] [--tls-key PATH]]

  Submit one or more tasks.\
"""

APP_HELP: Final[str] = f"""\
{APP_USAGE}

  Submit a single command using positional arguments.
  If a filepath is provided, read many tasks from the file instead.
  With --from-json, read tasks from a JSON file and expand named "{{key}}"
  fields in the template from each task object (keys also become tags).
  Submit directly to a live queue with --queue. The key to connect to the server is
  taken from the {AUTH_ENV} environment variable or from server.auth in a
  configuration file, never from the command line.

Arguments:
  ARGS...                    Command-line task arguments.

Options:
  -f, --task-file    FILE    Path to task file ("-" for <stdin>).
      --from-json    SPEC    Read tasks from a JSON file ("FILE[@path]").
      --repeat               Re-submit a known file's tasks again as a new source.
      --update               Submit only tasks not already present from a known file.
  -q, --queue                Submit to live queue instead of database (key from {AUTH_ENV} or server.auth).
  -H, --host         ADDR    Hostname for server, used with --queue (default: server.host, else
                             server.advertise, else server.bind if one address, else localhost).
  -p, --port         NUM     Port number for server (default: {DEFAULT_PORT}). Used with --queue.
      --no-tls               Disable TLS for queue interface (not recommended).
      --tls-key              Path to TLS private key file (default: <auto>).
      --tls-cert             Path to TLS certificate file (default: <auto>).
      --tls-ca               Path to TLS CA certificate file (default: <auto>).
      --template     CMD     Submit-time template expansion (default: "{DEFAULT_TEMPLATE}").
  -c, --cores        NUM     Required cores per task (default: none).
  -m, --memory       MEM     Required memory per task; K/M/G/T[B|iB] are powers of 1024 (default: none).
  -W, --timeout      SEC     Task-level walltime limit; seconds, 1h30m, 1.5h or [D-]HH:MM:SS (default: none).
  -g, --group        NUM     Task group for dependency management (default: {DEFAULT_TASK_GROUP}).
      --zone         NAME    Zone of the tasks; only clients of this zone run them (default: none).
  -b, --bundlesize   NUM     Number of lines to buffer (default: {DEFAULT_BUNDLESIZE}).
  -w, --bundlewait   SEC     Wait before flushing tasks; seconds, 30s, 5m or [D-]HH:MM:SS (default: {DEFAULT_BUNDLEWAIT}).
      --initdb               Auto-initialize database.
  -t, --tag          TAG...  Assign tags as `key:value`.
  -h, --help                 Show this message and exit.\
"""


class SubmitApp(ReadingMixin, Application):
    """Submit tasks to the database."""

    name = APP_NAME
    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)

    source: IO | List[str] | None = None
    source_path: Optional[str] = None       # absolute path of a named task file (else None)
    source_name: Optional[str] = None       # name of a stream read in full ('<stdin>' or its path)
    scan: Optional[InputScan] = None        # what input read in full holds, checked before any write
    source_id: Optional[str] = None         # resolved Source.id stamped onto submitted tasks
    gate: Optional[GateDecision] = None     # the re-submission gate's decision for a named source
    json_fingerprint: Optional[str] = None  # content md5 of a --from-json file (else None)
    task_args: List[str] = []
    task_file: str = None
    from_json: str = None
    interface.add_argument('task_args', nargs='*')
    interface.add_argument('-f', '--task-file', default=task_file)
    interface.add_argument('--from-json', default=from_json, dest='from_json')

    bundlesize: int = config.submit.bundlesize
    ranged(interface, '-b', '--bundlesize', setting='submit.bundlesize', default=bundlesize)

    bundlewait: int = config.submit.bundlewait
    ranged(interface, '-w', '--bundlewait', setting='submit.bundlewait', default=bundlewait)

    template: str = DEFAULT_TEMPLATE
    interface.add_argument('--template', default=template)

    cores: Optional[int] = config.task.cores or None
    memory: Optional[int] = config.task.memory or None
    ranged(interface, '-c', '--cores', setting='task.cores', default=cores)
    ranged(interface, '-m', '--memory', setting='task.memory', default=memory)

    timeout: Optional[int] = config.task.timeout or None
    ranged(interface, '-W', '--timeout', setting='task.timeout', default=timeout)

    group: Optional[int] = None
    ranged(interface, '-g', '--group', setting='option.group', default=group)

    repeat_mode: bool = False
    interface.add_argument('--repeat', action='store_true', dest='repeat_mode')

    update_mode: bool = False
    interface.add_argument('--update', action='store_true', dest='update_mode')

    auto_initdb: bool = False
    interface.add_argument('--initdb', action='store_true', dest='auto_initdb')

    queue: Optional[QueueConfig]
    queue_mode: bool = False
    queue_server: Optional[str] = None  # By client_host with --queue when not given (see check_arguments)
    queue_port: int = config.server.port
    queue_auth: Optional[str] = None  # The key is resolve_auth('server') (see run)
    interface.add_argument('-q', '--queue', action='store_true', dest='queue_mode')
    interface.add_argument('-H', '--host', default=queue_server, dest='queue_server')
    ranged(interface, '-p', '--port', setting='server.port', default=queue_port, dest='queue_port')
    refuse_key_option(interface, advice=SUBMIT_KEY_ADVICE)

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

    zone: Optional[str] = None
    zone_id: Optional[str] = None  # resolved Zone.id stamped onto submitted tasks
    interface.add_argument('--zone', default=None)

    exceptions = {
        EOFError: functools.partial(handle_disconnect, logger=log),
        ConnectionResetError: functools.partial(handle_disconnect, logger=log),
        ConnectionRefusedError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        AuthenticationError: functools.partial(handle_exception, logger=log, status=exit_status.runtime_error),
        HostAddressInfo: functools.partial(handle_address_unknown, logger=log, status=exit_status.runtime_error),
        **get_shared_exception_mapping(__name__)
    }

    def check_arguments(self: SubmitApp) -> None:
        """Reject contradictory flags before doing any work; with --queue, settle the host it dials."""
        if self.update_mode and self.repeat_mode:
            raise ArgumentError('Cannot combine --update with --repeat')
        if self.zone is not None:
            if self.queue_mode:
                raise ArgumentError('Cannot use --zone with --queue (zones require the database)')
            try:
                self.zone = zone_name(self.zone)
            except ValueError as error:
                raise ArgumentError(str(error)) from error
        if self.queue_mode and self.queue_server is None:
            self.queue_server = client_host()

    def run(self: SubmitApp) -> None:
        """Run submit thread.

        Input read in full is checked whole before anything is written: tags (exit 2) before any
        input is read, then the input (exit 5), the re-submission gate's decision and each task's
        group against the database's reached group (exit 2); only then the zone, the source and
        the tasks.
        """
        with reading('configuration'):
            self.check_arguments()
            self.check_tags()
        self.check_source()
        self.queue = None
        if self.queue_mode:
            self.enable_tls()
            self.queue_auth = resolve_auth('server')
            self.queue = QueueConfig(host=self.queue_server, port=self.queue_port, auth=self.queue_auth, tls=self.tls)
            if self.queue_auth == DEFAULT_AUTH:
                log.warning('Using default authentication key - do not use this in production!')
            if not self.tls_enabled:
                log.warning('TLS is disabled - this is not recommended for production!')
            self.scan_source()
        else:
            self.check_database()
            self.read_source()
            if self.group is None:
                self.group = self.default_group()
            self.scan_source()
            self.decide_source()
            self.check_reached_group()
            if self.zone is not None:
                self.zone_id = Zone.ensure(self.zone)
            self.prepare_source()
        if self.source is not None:
            self.submit_all()
        else:
            self.submit_one()

    @functools.cached_property
    def reached_group(self: SubmitApp) -> Optional[int]:
        """The database's reached group (:meth:`~hypershell.data.model.Task.reached_group`), read once."""
        return Task.reached_group()

    def default_group(self: SubmitApp) -> int:
        """The task group of a submission that names none.

        This is the most recently scheduled group, unless that is below the reached group (a late
        lower group is running) or is not an integer; then it is the reached group, so a submission
        that names no group is never refused for it.
        """
        group = Task.current_group()
        reached = self.reached_group
        if reached is not None and (not isinstance(group.value, int) or group.value < reached):
            reason = 'below it' if isinstance(group.value, int) else 'not an integer'
            group = TaskGroupInfo(reached, f'reached group; most recently scheduled group {group.value} is {reason}')
        log.info(f'Auto-selected task group {group.value} ({group.reason})')
        return group.value

    def task_groups(self: SubmitApp) -> List[Optional[int]]:
        """The group of every task in this submission's input, in order."""
        if self.from_json:
            default = self.tags.get('group', self.group)
            return [record.get('group', default) for record in self.source]
        if self.source is None:
            _, inline_tags = Task.split_argline(self.direct_args)
            return [inline_tags.get('group', self.tags.get('group', self.group))]
        return self.scan.groups

    def task_fingerprints(self: SubmitApp) -> List[str]:
        """The identity of every task in a named source, in order, as the Loader builds it."""
        if self.from_json:
            return json_fingerprints(self.source, group=self.group, tags=self.tags)
        with open(self.source_path, mode='r') as stream:
            return task_fingerprints(stream, template=self.template, group=self.group, tags=self.tags)

    def check_reached_group(self: SubmitApp) -> None:
        """Refuse the whole submission (exit 2) when a task it would insert is below the reached group.

        Tasks the re-submission gate skips as already present are not being submitted and are
        not checked.
        """
        refuse_below_reached_group(self.task_groups(), self.reached_group,
                                   skip=None if self.gate is None else self.gate.skip,
                                   fingerprints=self.task_fingerprints)

    def submit_all(self: SubmitApp) -> None:
        """Submit multiple tasks from file-like source."""
        count = submit_from(self.source, template=self.template, queue_config=self.queue,
                            cores=self.cores, memory=self.memory, timeout=self.timeout, group=self.group,
                            bundlesize=self.bundlesize, bundlewait=self.bundlewait, tags=self.tags)
        log.info(f'Submitted {count} tasks')

    @functools.cached_property
    def direct_args(self: SubmitApp) -> str:
        """The one task given as arguments, joined as a command line."""
        return ' '.join([self.quote_arg(arg) for arg in self.task_args])

    def submit_one(self: SubmitApp) -> None:
        """Submit one task from arguments."""
        args = self.direct_args
        if self.queue_mode:
            submit_from([args, ], queue_config=self.queue,
                        cores=self.cores, memory=self.memory, timeout=self.timeout, group=self.group,
                        tags=self.tags)
            advise_one_task(args, self.tags)
        else:
            task = Task.new(args=args, cores=self.cores, memory=self.memory, timeout=self.timeout,
                            group=self.group, tag=self.tags, source=self.source_id, zone=self.zone_id)
            advise_one_task(args, self.tags)
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
        """Read `-t` before any input is read, as an inline directive is read (else exit 2).

        Tags are checked in both modes, but only a submission to the database applies them: queue
        mode leaves them unset, as it always has.
        """
        try:
            tags = read_tag_options(self.taglist)
        except (ValueError, TypeError, TaskInputError) as error:
            raise ArgumentError(str(error)) from error
        if not self.queue_mode:
            self.tags = tags

    def check_source(self: SubmitApp) -> None:
        """Determine task submission mode."""
        if self.from_json:
            if self.task_args or self.task_file:
                raise ArgumentError('Cannot combine --from-json with -f/--task-file or positional arguments')
            records, self.json_fingerprint = load_json_source(self.from_json)
            validate_json_expansion(records, self.template)
            validate_json_records(records)
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

    def read_source(self: SubmitApp) -> None:
        """Read a stream in full (standard input, a FIFO, a process substitution), with a database.

        Nothing is inserted until the input has ended and been checked whole. The stream becomes
        a list of its lines, submitted under the reserved ``<stdin>`` source.
        """
        if self.from_json or self.source is None or isinstance(self.source, list):
            return
        if self.source is sys.stdin or not self.source.seekable():
            stream = self.source
            self.source_name = '<stdin>' if stream is sys.stdin else self.source_path
            self.source = read_in_full(stream)
            if stream is not sys.stdin:
                stream.close()
            log.info(f'Read {len(self.source)} lines from {self.source_name}')

    def scan_source(self: SubmitApp) -> None:
        """Check every group of input read in full before anything is written (exit 5, naming value and line).

        A named file, a stream read in full and the arguments are scanned with the template, the
        default group and the tags the submission uses; a ``--from-json`` source was checked as it
        was loaded. A stream in queue mode is checked line by line as it arrives.
        """
        if self.from_json:
            return
        if self.source is None:
            try:
                Task.split_argline(self.direct_args)
            except TaskInputError as error:
                raise error.at('arguments') from error
            return
        if isinstance(self.source, list):
            self.scan = scan_task_lines(self.source, template=self.template, group=self.group,
                                        tags=self.tags, name=self.source_name)
        elif self.source is not sys.stdin and self.source.seekable():
            with open(self.source_path, mode='r') as stream:
                self.scan = scan_task_lines(stream, template=self.template, group=self.group,
                                            tags=self.tags, name=self.source_path)

    def decide_source(self: SubmitApp) -> None:
        """Make the re-submission gate's decision for a named file or ``--from-json`` file, writing nothing.

        A duplicate or changed-content re-submission is refused here (exit 2) unless
        ``--repeat``/``--update`` relaxes it (:func:`decide_source_gate`); the decision is
        recorded by :meth:`prepare_source` once every check has passed. Called in DB mode only.
        """
        if not DATABASE_ENABLED:
            return
        if self.from_json:
            key = json_source_key(self.from_json)
            if key is not None:
                log.info(f'Found {len(self.source)} tasks in {key} (md5={self.json_fingerprint})')
                self.gate = decide_source_gate(key, self.json_fingerprint, len(self.source),
                                               repeat=self.repeat_mode, update=self.update_mode)
        elif self.source is not None and not isinstance(self.source, list):
            fingerprint, count = source_fingerprint(self.source_path), self.scan.count
            log.info(f'Found {count} tasks in {self.source_path} (md5={fingerprint})')
            self.gate = decide_source_gate(self.source_path, fingerprint, count,
                                           repeat=self.repeat_mode, update=self.update_mode)

    def prepare_source(self: SubmitApp) -> None:
        """Resolve the task Source, record the re-submission gate's decision, and wrap the stream (DB mode only).

        A named file passed through the gate in :meth:`decide_source`, and its resolved
        :class:`Source` row's ``task_count`` is the upfront-counted expectation, committed
        *before* any task so an incomplete prior stays detectable. ``<stdin>`` and
        single-command ``<direct>`` submissions resolve their reserved fixed-id rows and are
        exempt from gating — a gating flag there is a no-op and says so. A ``--from-json``
        source is gated too (see :meth:`prepare_json_source`).
        """
        if not DATABASE_ENABLED:
            return  # non-persistent DB: nothing to record against (invariant §4)
        if self.from_json:
            self.prepare_json_source()
            return
        if self.source is None:
            self.warn_gating_no_effect('<direct>')
            self.source_id = Source.reserved(DIRECT_SOURCE_ID).id
        elif isinstance(self.source, list):
            # Real <stdin> and non-seekable named inputs (process substitution, FIFOs, a
            # piped /dev/stdin) have no stable identity and cannot be re-read: they were read
            # in full (read_source) and go under the reserved <stdin> source, exempt from gating.
            self.warn_gating_no_effect('<stdin>')
            self.source_id = Source.reserved(STDIN_SOURCE_ID).id
            self.source = GatedSource(self.source, self.source_id, name=self.source_name, zone_id=self.zone_id)
        else:
            self.source_id, skip = self.gate.commit()
            self.source = GatedSource(self.source, self.source_id, skip_fingerprints=skip,
                                      name=self.source_path, zone_id=self.zone_id)

    def prepare_json_source(self: SubmitApp) -> None:
        """Record the re-submission gate's decision for a ``--from-json`` source.

        The JSON file's content md5 (captured in :meth:`check_source`, no second read) and
        record count fed the same :func:`decide_source_gate` as a line file, keyed by
        ``abspath(FILE)[@node]``. The per-task fingerprint keys off the pre-template ``args``
        and tags, so ``--update`` de-dup matches whether the tasks were first ingested
        here or from a line file. ``--from-json -`` (stdin JSON) has no stable path and is
        exempt, carrying the reserved ``<stdin>`` source. Called in DB mode only.
        """
        key = json_source_key(self.from_json)
        if key is None:
            self.warn_gating_no_effect('<stdin>')
            self.source_id = Source.reserved(STDIN_SOURCE_ID).id
            self.source = GatedSource(self.source, self.source_id, name='<stdin>', zone_id=self.zone_id)
            return
        self.source_id, skip = self.gate.commit()
        self.source = GatedSource(self.source, self.source_id, skip_fingerprints=skip, name=key,
                                  zone_id=self.zone_id)

    def warn_gating_no_effect(self: SubmitApp, kind: str) -> None:
        """Note that re-submission gating flags are inert for exempt sources."""
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
