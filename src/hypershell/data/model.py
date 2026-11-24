# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database models."""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Tuple, Any, Type, Optional, Final

# Standard libs
import re
import json
import hashlib
from enum import Enum
from uuid import UUID as PyUUID, uuid5
from functools import cache
from datetime import datetime
from dataclasses import dataclass

# External libs
from sqlalchemy import (Column, Index, func, text, update, delete, select, bindparam, literal, or_, and_, cast,
                        inspect, case)
from sqlalchemy.orm import Query, DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound, StaleDataError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.sql.elements import ClauseElement, ColumnElement
from sqlalchemy.types import Integer, Float, DateTime, Text, Boolean, JSON as JSON_TEXT
from sqlalchemy.dialects.postgresql import BIGINT, SMALLINT, UUID as POSTGRES_UUID, JSONB as JSON_BYTES, ARRAY
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# Internal libs
from hypershell.core.logging import Logger, HOSTNAME, INSTANCE
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.types import (JSONData, to_json_type, from_json_type, serialize, deserialize,
                                   parse_bytes, parse_time, UnitError)
from hypershell.core.config_schema import SETTINGS, SettingError
from hypershell.core.uuid import uuid
from hypershell.core.tag import Tag
from hypershell.data.core import schema, Session

# Public interface
__all__ = [
    'Task', 'Client', 'Source', 'Zone', 'Entity', 'to_json_type', 'from_json_type', 'serialize_tasks', 'deserialize_tasks',
    'UUID', 'TEXT', 'INTEGER', 'SMALL_INTEGER', 'BIG_INTEGER', 'DATETIME', 'BOOLEAN', 'JSON',
    'DEFAULT_TASK_GROUP', 'CANCEL_STATUS', 'TASK_TEMPLATE_ERROR', 'TASK_RESOURCE_ERROR', 'NO_RETRY_STATUSES',
    'DIRECT_SOURCE_ID', 'STDIN_SOURCE_ID', 'TaskGroupInfo', 'ResultOutcome',
    'GROUP_MIN', 'GROUP_MAX', 'TaskGroupError', 'ensure_group', 'parse_group', 'tag_text',
    'DEFAULT_ZONE_ID', 'DEFAULT_ZONE_NAME', 'ZONE_NAMESPACE', 'zone_id', 'zone_name',
]

# Initialize logger
log = Logger.with_name(__name__)


# Constants
DEFAULT_TASK_GROUP: Final[int] = 0
"""Default task group for backwards compatibility."""

TAG_MARKER: Final[re.Pattern] = re.compile(r'#\s*HYPERSHELL:?')
"""The inline tag marker: found anywhere on a line, with or without its colon."""

GROUP_MIN: Final[int] = SETTINGS['option.group'].low
"""Lowest task group: the range of PostgreSQL's `INTEGER`, held on every database."""

GROUP_MAX: Final[int] = SETTINGS['option.group'].high
"""Highest task group."""


class TaskGroupError(RuntimeError):
    """A task group taken from input that is not an integer in range.

    `value` is the group as written when it came from text (``1e3``, not ``1000.0``), else the value
    itself (a JSON value); `origin` names where it came from (``tasks.in, line 7``, ``record 3``)
    once that is known (:meth:`at`). A `RuntimeError`, so a command refused for it exits 5.
    """

    def __init__(self: TaskGroupError, value: Any, origin: Optional[str] = None, *, out_of_range: bool = False) -> None:
        """Initialize with the refused `value`, where it came from, and whether it is an integer out of range."""
        self.value = value
        self.origin = origin
        self.out_of_range = out_of_range
        shown = f'"{value}"' if isinstance(value, str) else json.dumps(value)
        rule = f'an integer from {GROUP_MIN} to {GROUP_MAX}' if out_of_range else 'an integer'
        message = f'task group must be {rule}, not {shown}'
        super().__init__(message if origin is None else f'{message} ({origin})')

    def at(self: TaskGroupError, origin: str) -> TaskGroupError:
        """The same refusal, naming where the value came from."""
        return TaskGroupError(self.value, origin, out_of_range=self.out_of_range)


def ensure_group(value: Any, text: Optional[str] = None) -> int:
    """The task group `value`: an integer (not a boolean) from `GROUP_MIN` to `GROUP_MAX`, else :class:`TaskGroupError`.

    A tag value arrives typed by `smart_coerce`, which gives an `int` exactly when `int()` takes the
    text, as `-g/--group` does; `text` is that text as written, which the refusal names. A JSON value
    is a group only when it is a JSON integer.
    """
    shown = value if text is None else text
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskGroupError(shown)
    if not GROUP_MIN <= value <= GROUP_MAX:
        raise TaskGroupError(shown, out_of_range=True)
    return value


def parse_group(text: str) -> int:
    """The task group written as `text`, by the rule of `-g/--group`: a whole number, within range."""
    try:
        return SETTINGS['option.group'].parse(text, 'group')
    except SettingError as error:
        raise TaskGroupError(text, out_of_range=error.out_of_range) from None


def tag_text(tokens: List[str], name: str) -> Optional[str]:
    """The value of tag `name` as written among command-line `tokens` (``key:value``), or None if absent.

    The last token for `name` wins, as it does when the tokens are parsed; a bare ``key`` has the
    empty value.
    """
    text = None
    for token in tokens:
        key, _, value = token.strip().partition(':')
        if key.strip() == name:
            text = value.strip()
    return text


CANCEL_STATUS: Final[int] = -1
"""Sentinel `exit_status` marking a cancelled task.

A cancelled task is terminal: it must never be scheduled, retried, or counted as an
unrecoverable failure. It is distinguished from a genuine failure (positive exit codes,
or the negative template/resource sentinels below) solely by this value, so the
scheduler's failure and retry queries filter it out explicitly.
"""

TASK_TEMPLATE_ERROR: Final[int] = -1001
"""Sentinel `exit_status` of a task whose command template did not expand on its client (never ran)."""

TASK_RESOURCE_ERROR: Final[int] = -1002
"""Sentinel `exit_status` of a task declaring more cores or memory than its client has in total (never ran)."""

NO_RETRY_STATUSES: Final[Tuple[int, ...]] = (TASK_TEMPLATE_ERROR, TASK_RESOURCE_ERROR)
"""The `exit_status` values of a task that never ran on its client and would fail the same way there.

Such a task is never retried, whatever the retries allowed: it is not selected, marked or counted as
awaiting retry, its row stays its last attempt, and its group is held as failed on any attempt.
"""

CONTENDED_RERUNS: Final[int] = 3
"""Extra attempts for a task-row UPDATE that matched no row while the row still qualifies.

On a TimescaleDB 2.28.1 through 2.31.0 hypertable, an UPDATE or DELETE that waited on another
transaction's row lock can report 0 matched rows although the row exists and still satisfies the
statement's condition. A new statement sees the other transaction's commit and matches normally,
so a write that a fresh read shows still qualifies is re-run, at most this many times.
"""

DIRECT_SOURCE_ID: Final[str] = '00000000-0000-7000-8000-000000000000'
"""Reserved `Source` id for single command-line submissions (`hs submit '<cmd>'`)."""

STDIN_SOURCE_ID: Final[str] = '00000000-0000-7000-8000-000000000001'
"""Reserved `Source` id for streamed/stdin submissions."""

RESERVED_SOURCE_PATHS: Final[Dict[str, str]] = {
    DIRECT_SOURCE_ID: '<direct>',
    STDIN_SOURCE_ID: '<stdin>',
}
"""Sentinel `path` values for the reserved source rows (see `Source.reserved`).

The reserved sources are real rows with fixed, well-known UUIDv7 ids (zero timestamp,
so they sort low and never raise a chunk's max) — this keeps `Task.source` a native UUID
and lets them be statically excluded from the de-dup index. Both represent explicit user
intent and are exempt from all re-submission gating.
"""


# Reserved id of the default zone. A task in the default zone stores NULL in `task.zone`, so rows
# written before zones existed are already in it; the id names the default zone everywhere else.
DEFAULT_ZONE_ID: Final[str] = '00000000-0000-7000-8000-000000000010'

# Reserved name of the default zone. A missing or empty name means the default zone too.
DEFAULT_ZONE_NAME: Final[str] = 'default'

# Namespace of name-derived zone ids. It must never change: every release, and every process,
# derives the same id from the same name, so resolving a name needs no lookup and cannot race.
ZONE_NAMESPACE: Final[PyUUID] = PyUUID('6f1c9b4e-2a57-5d0e-9b3a-7e5f0c8d2a41')


# Longest zone name accepted (characters).
ZONE_NAME_MAX: Final[int] = 256


def zone_name(value: str) -> str:
    """A zone name as given by a user, stripped; raises ValueError if empty, too long, or not printable."""
    name = str(value).strip()
    if not name:
        raise ValueError('Zone name must not be empty')
    if len(name) > ZONE_NAME_MAX:
        raise ValueError(f'Zone name must be at most {ZONE_NAME_MAX} characters')
    if not name.isprintable():
        raise ValueError('Zone name must not contain control characters')
    return name


def zone_id(name: Optional[str]) -> str:
    """The id of the zone called `name`; None, '' and the reserved name give the default zone."""
    if name is None or name == '' or name == DEFAULT_ZONE_NAME:
        return DEFAULT_ZONE_ID
    return str(uuid5(ZONE_NAMESPACE, name))


class DatabaseError(Exception):
    """Generic database-related exception."""


class NotFound(DatabaseError):
    """Exception specific to no record found on lookup by unique field (e.g., `id`)."""


class NotDistinct(DatabaseError):
    """Exception specific to multiple records found when only one should have been."""


class Contended(DatabaseError):
    """Exception specific to a write that did not land after its re-runs, another writer holding the row."""


class AlreadyExists(DatabaseError):
    """Exception specific to a record with unique properties already existing."""


# Pre-defining types shortens declarations and makes changes easier
UUID = Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')
TEXT = Text()
INTEGER = Integer()
SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')
BIG_INTEGER = Integer().with_variant(BIGINT, 'postgresql')
FLOAT = Float()
DATETIME = DateTime(timezone=True)
BOOLEAN = Boolean()
JSON = JSON_TEXT().with_variant(JSON_BYTES(), 'postgresql')


class Entity(DeclarativeBase):
    """Core mixin class for all entities."""

    columns: Dict[str, type] = {}

    @declared_attr
    def __tablename__(cls: Type[Entity]) -> str:  # noqa: cls
        """The table name should be lower-case."""
        return cls.__name__.lower()

    @declared_attr
    def __table_args__(cls) -> Dict[str, Any]:  # noqa: cls
        """Common table attributes."""
        return {'schema': schema, }

    def __repr__(self: Entity) -> str:
        """String representation."""
        attrs = ', '.join([f'{name}={repr(getattr(self, name))}' for name in self.columns])
        return f'{self.__class__.__name__}({attrs})'

    def to_tuple(self: Entity) -> tuple:
        """Convert fields into standard tuple."""
        return tuple([getattr(self, name) for name in self.columns])

    def to_dict(self: Entity) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return dict(zip(self.columns, self.to_tuple()))

    def to_json(self: Entity) -> Dict[str, JSONData]:
        """Convert record to JSON-serializable dictionary."""
        return {key: to_json_type(value) for key, value in self.to_dict().items()}

    @classmethod
    def from_dict(cls: Type[Entity], data: Dict[str, Any]) -> Entity:
        """Build from existing dictionary."""
        return cls(**data)  # noqa: __init__ instrumented by declarative_base

    @classmethod
    def from_json(cls: Type[Entity], data: Dict[str, JSONData]) -> Entity:
        """Build from JSON `data`, decoded by each column's declared type.

        Only a column declared ``datetime`` in `columns` is read as a time; every other value stays
        exactly as sent, so text that happens to look like a date remains text.
        """
        return cls.from_dict({key: from_json_type(value) if cls.columns.get(key) is datetime else value
                              for key, value in data.items()})

    # NOTE:
    # The pack() and unpack() remaining available for backwards compatibility, but not recommended.
    # We now use serialize_tasks() and deserialize_tasks() to serialize task bundles as one object.

    def pack(self: Entity) -> bytes:
        """Encrypt JSON bytes."""
        return serialize(self.to_json())

    @classmethod
    def unpack(cls: Type[Entity], data: bytes) -> Entity:
        """Unpack encrypted JSON byte string."""
        return cls.from_json(deserialize(data))

    @classmethod
    def query(cls: Type[Entity], *fields: Column, caching: bool = True) -> Query:
        """Get query interface for entity with scoped session."""
        target = fields or [cls, ]
        if not caching:
            Session.expire_all()
        return Session.query(*target)

    @classmethod
    def count(cls: Type[Entity]) -> int:
        """Count of total existing records in database."""
        return cls.query().count()

    @classmethod
    def add_all(cls: Type[Entity], items: List[Entity]) -> List[Entity]:
        """Add many items to the database at once."""
        # NOTE: pull id first because access after commit could trigger query
        item_ids = [item.id for item in items]  # noqa: id not defined on base
        try:
            Session.add_all(items)
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        else:
            for item_id in item_ids:
                log.trace(f'Added {cls.__tablename__} ({item_id})')
            return items

    @classmethod
    def add(cls: Type[Entity], item: Entity) -> None:
        """Add single item to database."""
        cls.add_all([item, ])

    @classmethod
    def update_all(cls: Type[Entity], changes: List[Dict[str, Any]]) -> None:
        """Bulk update."""
        if changes:
            Session.bulk_update_mappings(cls, changes)
            Session.commit()  # NOTE: why is this necessary?
            log.trace(f'Updated {len(changes)} {cls.__tablename__}s')

    @classmethod
    def update(cls: Type[Entity], id: str, **changes) -> None:
        """Update by `id` with `changes`."""
        cls.update_all([{'id': id, **changes}, ])

    @classmethod
    def delete_all(cls: Type[Entity], items: List[Entity]) -> List[Entity]:
        """Delete records from database."""
        try:
            for item in items:
                Session.delete(item)
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        else:
            for item in items:
                log.trace(f'Deleted {cls.__tablename__} ({item.id})')  # noqa: id not defined on base
            return items

    @classmethod
    def delete(cls: Type[Entity], item: Entity) -> None:
        """Delete single item from database."""
        cls.delete_all([item, ])

    @classmethod
    def from_id(cls: Type[Entity], id: str) -> Entity:
        """Load by unique `id`."""
        raise NotImplementedError()  # NOTE: non-strict requirement of base

    @classmethod
    def new(cls: Type[Entity], **attrs: Any) -> Entity:
        """Create new instance with default values."""
        raise NotImplementedError()  # NOTE: non-strict requirement of base


def serialize_tasks(tasks: Optional[List[Task]]) -> bytes:
    """Serialize list of tasks to encrypted JSON byte string."""
    return serialize(None if tasks is None else [task.to_json() for task in tasks])


def deserialize_tasks(data: bytes) -> Optional[List[Task]]:
    """Deserialize list of tasks from encrypted JSON byte string."""
    data = deserialize(data)
    return None if data is None else [Task.from_json(task_data) for task_data in data]


@dataclass
class TaskGroupInfo:
    """Information related to the active task group."""
    value: int
    reason: Optional[str] = None
    viable: bool = True


class ResultOutcome(Enum):
    """What became of one returned task result (see `Task.apply_results`).

    A result belongs to the dispatch that produced it, identified by the row's `schedule_time`
    as it was sent to the client. APPLIED means the row was still in flight under that dispatch
    and its result columns were written. DUPLICATE means that same dispatch had already completed
    the row (a redelivered or repeated result). STALE means the row no longer belongs to that
    dispatch: it was reverted, rescheduled, completed by another dispatch, cancelled, or no
    longer exists. A DUPLICATE or STALE result writes nothing. DEFERRED means the row still
    belongs to that dispatch and is not complete, but the write did not land within
    `CONTENDED_RERUNS` re-runs, each having waited on another transaction's row lock; nothing
    was written, and the caller may present the result again.
    """
    APPLIED = 1
    DUPLICATE = 2
    STALE = 3
    DEFERRED = 4


class Task(Entity):
    """Task entity within database implements task methods."""

    id: Mapped[str] = mapped_column(UUID, primary_key=True, nullable=False)
    group: Mapped[int] = mapped_column(INTEGER, nullable=False, default=DEFAULT_TASK_GROUP, quote=True, name='group')
    args: Mapped[str] = mapped_column(TEXT, nullable=False)

    submit_id: Mapped[str] = mapped_column(UUID, nullable=False)
    submit_time: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    submit_host: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    cores: Mapped[int] = mapped_column(SMALL_INTEGER, nullable=True)  # NULL means untracked
    memory: Mapped[int] = mapped_column(BIG_INTEGER, nullable=True)  # NULL means untracked
    cores_max: Mapped[float] = mapped_column(FLOAT, nullable=True)
    memory_max: Mapped[float] = mapped_column(FLOAT, nullable=True)
    timeout: Mapped[int] = mapped_column(INTEGER, nullable=True)  # NULL means untracked

    server_id: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)
    server_host: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    schedule_time: Mapped[Optional[datetime]] = mapped_column(DATETIME, nullable=True)

    client_id: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)
    client_host: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    command: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    start_time: Mapped[Optional[datetime]] = mapped_column(DATETIME, nullable=True)
    completion_time: Mapped[Optional[datetime]] = mapped_column(DATETIME, nullable=True)
    exit_status: Mapped[Optional[int]] = mapped_column(SMALL_INTEGER, nullable=True)

    outpath: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    errpath: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    csvpath: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

    attempt: Mapped[int] = mapped_column(SMALL_INTEGER, nullable=False)
    retried: Mapped[bool] = mapped_column(BOOLEAN, nullable=False)

    waited: Mapped[Optional[int]] = mapped_column(INTEGER, nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(INTEGER, nullable=True)

    # The retry chain carries plain indexes, not UNIQUE constraints: a TimescaleDB hypertable
    # requires every unique index to include its partition column, and one schema serves every
    # provider. One retry per failed row is held by the Scheduler's `retried` filter, not by the
    # schema. SQLite databases created before 2.9 keep their UNIQUE constraints (SQLite cannot
    # drop them without rebuilding the table).
    previous_id: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)
    next_id: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)

    source: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)  # Source.id or reserved-const id
    fingerprint: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)  # Stable identity hash (args + group + tags).
    zone: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)  # Zone.id; NULL is the default zone.

    # Partition index for SQLite database rotation. The server default lets an existing table gain
    # the column in place, with existing rows reading 0.
    part: Mapped[int] = mapped_column(INTEGER, nullable=False, default=0, server_default=text('0'))

    tag: Mapped[dict] = mapped_column(JSON, nullable=False, default={})  # kept last (export/print order)

    columns = {
        'id': str,
        'group': int,
        'args': str,
        'submit_id': str,
        'submit_time': datetime,
        'submit_host': str,
        'cores': int,
        'memory': int,
        'cores_max': float,
        'memory_max': float,
        'timeout': int,
        'server_id': str,
        'server_host': str,
        'schedule_time': datetime,
        'client_id': str,
        'client_host': str,
        'command': str,
        'start_time': datetime,
        'completion_time': datetime,
        'exit_status': int,
        'outpath': str,
        'errpath': str,
        'csvpath': str,
        'attempt': int,
        'retried': bool,
        'waited': int,
        'duration': int,
        'previous_id': str,
        'next_id': str,
        'source': str,
        'fingerprint': str,
        'zone': str,
        'part': int,
        'tag': dict,
    }

    # The columns a client produces when it runs a task; a returned result writes these and nothing else.
    result_columns: Final[Tuple[str, ...]] = (
        'client_id',
        'client_host',
        'command',
        'start_time',
        'completion_time',
        'waited',
        'duration',
        'exit_status',
        'outpath',
        'errpath',
        'csvpath',
        'cores_max',
        'memory_max',
    )

    # The columns a retry copies from its failed task, as stored: never re-parsed or re-checked.
    retry_columns: Final[Tuple[str, ...]] = (
        'args',
        'tag',
        'group',
        'fingerprint',
        'source',
        'zone',
        'cores',
        'memory',
        'timeout',
    )

    # The columns a revert clears, so the task reads as new again.
    revert_columns: Final[Tuple[str, ...]] = (
        'schedule_time',
        'server_host',
        'server_id',
        'client_host',
        'client_id',
        'command',
        'start_time',
        'completion_time',
        'exit_status',
        'outpath',
        'errpath',
        'waited',
        'duration',
    )

    class NotFound(NotFound):
        pass

    class NotDistinct(NotDistinct):
        pass

    class AlreadyExists(AlreadyExists):
        pass

    class Contended(Contended):
        pass

    @property
    def status_label(self: Task) -> str:
        """Human-readable lifecycle status for display (e.g. the `card`-view badge)."""
        if self.schedule_time is None:
            return 'WAITING'
        if self.completion_time is None:
            return 'RUNNING'
        if self.exit_status == 0:
            return 'OK'
        if self.exit_status == CANCEL_STATUS:
            return 'CANCELLED'
        if self.exit_status is None:
            return 'UNKNOWN'
        if self.exit_status <= -1000:
            return 'ERROR'
        if self.exit_status < 0:
            return 'KILLED'
        return 'FAILED'

    @classmethod
    def from_id(cls: Type[Task], id: str, caching: bool = True) -> Task:
        """Look up task by unique `id`."""
        try:
            return cls.query(caching=caching).filter_by(id=id).one()
        except NoResultFound as error:
            raise cls.NotFound(f'No task with id={id}') from error
        except MultipleResultsFound as error:
            raise cls.NotDistinct(f'Multiple tasks with id={id}') from error

    @classmethod
    def new(cls: Type[Task],
            args: str,
            attempt: int = 1,
            retried: bool = False,
            tag: Dict[str, JSONData] = None,
            group: int = DEFAULT_TASK_GROUP,
            strict_tag: bool = True,
            parse_inline: bool = True,
            raw_args: str = None,
            fingerprint: str = None,
            source: str = None,
            **other: Any) -> Task:
        """Create a new Task.

        With ``strict_tag=False`` the tag character-set/length checks are relaxed
        (for JSON-sourced tags). With ``parse_inline=False`` the inline
        ``# HYPERSHELL:`` tag comment is not parsed and `args` is taken verbatim.

        The stable identity ``fingerprint`` is computed from the pre-template ``raw_args``
        (falling back to the expanded ``args`` when not supplied) plus the resolved group and
        tags, unless ``fingerprint`` is passed directly (retries copy the parent's). ``source``
        links the task to its ingested file's ``Source`` record; both columns are NULL only for
        historical/ungated rows. A ``zone`` of `DEFAULT_ZONE_ID` is stored as NULL.
        """
        cls.ensure_valid_tag(tag, strict=strict_tag)
        if parse_inline:
            args, inline_tags = cls.split_argline(args)
        else:
            args, inline_tags = str(args).strip(), {}
        tag = {**(tag or {}), **inline_tags}
        # A group from tags is input and must be an integer; the `group` argument is the caller's
        # (queue mode passes None, and a retry copies its parent's group as stored).
        other['group'] = ensure_group(tag.pop('group')) if 'group' in tag else group
        other['cores'] = tag.pop('cores', other.get('cores', None))
        # A memory value may arrive as a unit-bearing string ('2GB') from an inline
        # `memory:` tag (parsed by smart_coerce, which leaves units alone) — parse it to
        # integer bytes as the `--memory` flag does, so resource accounting stays numeric.
        memory = tag.pop('memory', other.get('memory', None))
        try:
            other['memory'] = parse_bytes(memory) if isinstance(memory, str) else memory
        except UnitError as error:
            raise RuntimeError(f'Failed to parse task memory ({error})') from error
        # A timeout string ('1h30m', '1:30:00') is read as integer seconds, from the tag or the option.
        timeout = tag.get('timeout', other.get('timeout', None))
        try:
            tag['timeout'] = parse_time(timeout) if isinstance(timeout, str) else timeout
        except UnitError as error:
            raise RuntimeError(f'Failed to parse task timeout ({error})') from error
        other['timeout'] = tag.pop('timeout', other.get('timeout', None))
        if fingerprint is None:
            if raw_args is None:
                raw_command = args
            elif parse_inline:
                raw_command = cls.split_argline(raw_args)[0]
            else:
                raw_command = str(raw_args).strip()
            fingerprint = cls.compute_fingerprint(raw_command, other['group'], tag)
        if other.get('zone') == DEFAULT_ZONE_ID:
            other['zone'] = None  # The default zone is stored as NULL.
        return Task(id=uuid(), args=args, source=source, fingerprint=fingerprint,
                    submit_id=INSTANCE, submit_host=HOSTNAME, submit_time=datetime.now().astimezone(),
                    attempt=attempt, retried=retried, tag=tag, **other)

    @classmethod
    def new_retry(cls: Type[Task], task: Task) -> Task:
        """The retry of a failed `task`: its declaration columns as stored, with a new identity.

        Every column of `retry_columns` is copied as stored (the tag as a new dict): the command,
        tags, group, fingerprint (a retry is the same logical task), source (the retry chain stays
        linked to it), zone, and the declared cores, memory and walltime, so a retry runs where and
        as its first attempt did. Nothing is parsed: the stored command is not read for inline tags,
        the stored tag is not checked again, and no tag (`group`, `cores`, `memory` or `timeout`
        among them) sets a column: a `group` tag written by `hs update` is copied with the other
        tags and can neither move nor stop the retry. The group is copied as stored, a legacy text
        group too. The id and the submit stamps are fresh, and the attempt is the next.
        """
        declared = {name: getattr(task, name) for name in cls.retry_columns}
        declared['tag'] = dict(task.tag or {})
        return Task(id=uuid(), submit_id=INSTANCE, submit_host=HOSTNAME, submit_time=datetime.now().astimezone(),
                    attempt=task.attempt + 1, retried=False, previous_id=task.id, **declared)

    @classmethod
    def compute_fingerprint(cls: Type[Task], raw_command: str, group: int,
                            tags: Optional[Dict[str, JSONData]]) -> str:
        """
        Stable, order-independent identity fingerprint.

        An md5 over canonical JSON of the pre-template ``raw_command``, the task ``group``,
        and user ``tags`` (resource knobs are already popped from the tag dict before this
        is called; the bookkeeping ``part`` is a column, never a tag).

        The uuid, attempt/retry counters, timing, exit status, and execution template
        deliberately do not participate — re-running the same work under a different
        template yields the same fingerprint. MD5 matches the SOURCE content fingerprint
        and is stdlib.
        """
        payload = json.dumps(
            {'args': raw_command,
             'group': group,
             'tags': dict(tags or {})},
            sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        )
        return hashlib.md5(payload.encode()).hexdigest()

    @classmethod
    def split_argline(cls: Type[Task], args: str) -> Tuple[str, Dict[str, JSONData]]:
        """Separate a command from its inline tags.

        A line whose first non-blank character is ``#`` is a comment (``''`` and no tags) unless it
        starts with the ``# HYPERSHELL:`` marker. The marker is found anywhere on a line, in quotes
        too: the command is the text before it and the tags are parsed from the text after it. Any
        other ``#`` is part of the command, left for the shell.
        """
        args = str(args).strip()
        if args.startswith('#') and not TAG_MARKER.match(args):
            return '', {}
        if match := TAG_MARKER.search(args):
            tokens = args[match.end():].strip().split()
            try:
                tags = Tag.parse_cmdline_list(tokens)
                cls.ensure_valid_tag(tags)
            except (ValueError, TypeError) as error:
                raise RuntimeError(f'Failed to parse inline tags ({error}, from: "{args}")') from error
            if 'group' in tags:
                tags['group'] = ensure_group(tags['group'], text=tag_text(tokens, 'group'))
            return args[:match.start()].strip(), tags
        return args, {}

    @staticmethod
    def ensure_valid_tag(tag: Optional[Dict[str, JSONData]], *, strict: bool = True) -> None:
        """
        Check tag dictionary and raise if invalid.

        With ``strict=False`` (used for JSON-sourced tags) the character-set and
        length restrictions on keys and values are relaxed; only the structural
        type checks (key is a non-empty string, value is a scalar) are enforced.
        """
        if tag is None:
            return
        if not isinstance(tag, dict):
            raise TypeError('Expected dict for tag data')
        for key, value in tag.items():
            if not isinstance(key, str):
                raise TypeError(f'Tag key, {key} ({type(key)}) is not string')
            if len(key.strip()) == 0:
                raise ValueError(f'Tag key was empty, "{key}:{value}"')
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise TypeError(f'Invalid type for tag value, {type(value)})')
            if not strict:
                continue
            if len(key.strip()) > 120:
                raise ValueError(f'Tag key size ({len(value)}) exceeds 120 characters ({key}:{value})')
            if not re.match(r'^[A-Za-z0-9_.+-]+$', key):
                raise ValueError(f'Tag key must only contain alphanumerics and basic symbols [+._-]: '
                                 f'"{key}:{value}"')
            if isinstance(value, str):
                if not value.strip():
                    return  # Empty value is a naked tag (no value).
                # A memory size or a timeout is checked by its own grammar instead of the tag
                # character set, so a Slurm time (1:30:00) is taken.
                if key == 'memory':
                    parse_bytes(value)
                    continue
                if key == 'timeout':
                    parse_time(value)
                    continue
                if len(value) > 120:
                    raise ValueError(f'Tag value size ({len(value)}) exceeds 120 characters ({key}:{value})')
                if not re.match(r'^[A-Za-z0-9_.+-]+$', value):
                    raise ValueError(f'Tag value must only contain alphanumerics and basic symbols [+._-]: '
                                     f'"{key}:{value}"')

    @classmethod
    def current_group(cls: Type[Task]) -> TaskGroupInfo:
        """
        This computes the currently active task group if we don't already have one.
        The returned group value follows one of the following rules (in order):
            1) The most recently scheduled task group (if there are any),
            2) The default task group (see: DEFAULT_TASK_GROUP) if the database is empty,
            3) The lowest group if nothing has been scheduled yet.
        """

        recent_task = (
            Session.query(Task)
            .order_by(Task.schedule_time.desc())
            .filter(Task.schedule_time.isnot(None))
            .first()
        )

        if recent_task is not None:
            return TaskGroupInfo(recent_task.group, f'most recently scheduled task: {recent_task.id}')

        # NOTE: we cannot just naively go with first submitted task (even though that makes sense)
        # because the groups might have been altered, and we get stuck in an infinite loop waiting on
        # all tasks to be completed (lower task group never get scheduled) but there are no more groups.
        # The earliest group is read before deciding the database is empty, so a row deleted after it
        # was counted (`hs update --delete` while the server runs) is never dereferenced.
        submitted_task = Session.query(Task).order_by(Task.group).first()
        if submitted_task is None:
            return TaskGroupInfo(DEFAULT_TASK_GROUP, 'no tasks submitted')
        return TaskGroupInfo(submitted_task.group, f'no tasks scheduled - defaulting to earliest group')

    @classmethod
    def increment_group(cls: Type[Task], previous_group: int, attempts: int, eager: bool = False) -> TaskGroupInfo:
        """
        This should only be called if no tasks are returned by Task.next().
        Check the current group and increment if necessary with explanation.
        We assume all tasks in `previous_group` have already been scheduled (see Task.next()).
        A group is held while it has incomplete tasks or failures still awaiting retry, and fails
        on a failure with no retries left or a task that never ran (`NO_RETRY_STATUSES`), on any
        attempt. The next group is the lowest group holding incomplete tasks, which may be lower
        than `previous_group` when tasks are submitted late.
        """
        # Incomplete tasks are counted before retryable failures, so a result written back between
        # the two checks is always seen by the second. Retryable failures are counted before
        # exhausted ones, so a group holding both is held rather than halted.
        remaining = (
            Session.query(Task)
            .filter(Task.group == previous_group)
            .filter(Task.completion_time.is_(None))
            .count()
        )
        if remaining > 0:
            return TaskGroupInfo(previous_group, f'waiting on {remaining} tasks to complete')
        retryable = cls.count_retryable(attempts, previous_group, eager=eager)
        if retryable > 0:
            return TaskGroupInfo(previous_group, f'waiting on {retryable} failed tasks to be retried')
        # The counts and the example are one statement: a row deleted after it was counted (a relay's
        # completed rows leave for its outbox) can never leave a count with no example. The example is
        # the least id as text: PostgreSQL's native uuid has no `min`. A task that never ran fails the
        # group on any attempt; it is counted as not run, whatever its attempt.
        never_ran = cls.exit_status.in_(NO_RETRY_STATUSES)
        failed, unrun, example = (
            Session.query(func.count(cls.id), func.count(case((never_ran, cls.id))), func.min(cast(cls.id, Text)))
            .filter(Task.group == previous_group)
            .filter(cls.exit_status.isnot(None))
            .filter(cls.exit_status != 0)
            .filter(cls.exit_status != CANCEL_STATUS)  # NOTE: cancelled is terminal, not a failure
            .filter(or_(cls.attempt >= attempts, never_ran))
            .filter(cls.retried.is_(False))
            .one()
        )
        if failed > 0:
            exhausted = failed - unrun
            if not unrun:
                reason = f'at least {exhausted} tasks exceeding allowed retries'
            elif not exhausted:
                reason = f'at least {unrun} tasks not retried because they did not run'
            else:
                reason = (f'at least {exhausted} tasks exceeding allowed retries and '
                          f'{unrun} not retried because they did not run')
            return TaskGroupInfo(previous_group, f'{reason}, example: {example}', False)
        # The next group is chosen in one query, so rows committed while the checks above ran are
        # never skipped; it is lower than the previous group when tasks were submitted late.
        next_group = cls.lowest_incomplete_group()
        if next_group is None:
            return TaskGroupInfo(previous_group, f'no task groups remain')
        elif next_group == previous_group:
            return TaskGroupInfo(previous_group, f'new tasks submitted')
        else:
            return TaskGroupInfo(next_group)

    @classmethod
    def starting_group(cls: Type[Task], group: int, attempts: int, eager: bool = False) -> Optional[int]:
        """
        The task group to start in, given the most recently scheduled `group`, or None.

        That group is kept while it holds failures still to be retried or halted on. Otherwise the
        start is the lowest group holding incomplete tasks, which is lower than `group` when tasks
        were submitted late. None means no task is incomplete and `group` holds nothing to retry.
        """
        if cls.count_remaining(group=group) == 0:
            # With every task in the group complete, increment_group holds the group while a
            # failure awaits retry, and halts it on a failure that has used its retries.
            held = cls.increment_group(group, attempts=attempts, eager=eager)
            if not held.viable or cls.count_retryable(attempts, group=group, eager=eager) > 0:
                return group
        return cls.lowest_incomplete_group()

    @classmethod
    def lowest_incomplete_group(cls: Type[Task]) -> Optional[int]:
        """The smallest group holding a task that has not completed, or None."""
        return Session.query(func.min(cls.group)).filter(cls.completion_time.is_(None)).scalar()

    @classmethod
    def reached_group(cls: Type[Task], in_flight: bool = True) -> Optional[int]:
        """
        The highest group the current pass has scheduled, or None when no task is incomplete.

        The current pass is the run of work since the database last held no incomplete task. Its
        start is not stored, so the earliest submit time of an incomplete task stands in for it: a
        row counts when it was scheduled and was still incomplete at or after that time (in flight,
        or scheduled or completed since then), or when it is a retry row submitted since then.
        Every row counted was therefore scheduled in the current pass, and the value never exceeds
        the group the schedule has truly reached. Cancelled rows do not count (a cancel stamps the
        schedule time), and on SQLite a group an earlier release stored as text or real is ignored.

        With `in_flight=False` a task still in flight does not count: a server reverts in-flight
        tasks when it starts, so they do not hold a lower group back for a run about to start.

        The pass start is an uncorrelated scalar subquery, so each dialect computes it once per
        statement rather than once per row scanned.
        """
        pass_start = (
            select(func.min(cls.submit_time))
            .where(cls.completion_time.is_(None))
            .scalar_subquery()
        )
        since_start = or_(cls.schedule_time >= pass_start, cls.completion_time >= pass_start)
        if in_flight:
            scheduled = and_(cls.schedule_time.isnot(None), or_(cls.completion_time.is_(None), since_start))
        else:
            scheduled = and_(cls.schedule_time.isnot(None), cls.completion_time.isnot(None), since_start)
        retry = and_(cls.previous_id.isnot(None), cls.submit_time >= pass_start)
        query = (
            select(func.max(cls.group))
            .where(or_(cls.exit_status.is_(None), cls.exit_status != CANCEL_STATUS))
            .where(or_(scheduled, retry))
        )
        if Session.get_bind().dialect.name == 'sqlite':
            query = query.where(func.typeof(cls.group) == 'integer')
        return Session.execute(query).scalar()

    @classmethod
    def _filter_zone(cls: Type[Task], query: Query, zone: Optional[str]) -> Query:
        """Restrict `query` to one zone: None means every zone, and the default zone is NULL."""
        if zone is None:
            return query
        if zone == DEFAULT_ZONE_ID:
            return query.filter(cls.zone.is_(None))
        return query.filter(cls.zone == zone)

    @classmethod
    def select_new(cls: Type[Task], limit: int, group: int = None, zone: str = None) -> List[Task]:
        """Select unscheduled tasks up to some `limit` in order of submit_time (in one `zone` if given)."""
        query = cls.query().order_by(cls.submit_time).filter(cls.schedule_time.is_(None))
        if group is not None:
            query = query.filter(cls.group == group)
        query = cls._filter_zone(query, zone)
        return query.limit(limit).all()

    @classmethod
    @cache
    def __awaiting_retry(cls: Type[Task]) -> Tuple[Tuple[Any, ...], Any]:
        """The row-state filters of a failed task still awaiting retry, from the table's columns.

        Returns the failure filters (an exit status, neither success nor a cancel, nor one of
        `NO_RETRY_STATUSES`: a task that never ran never awaits retry) and the filter that the row
        is not yet retried. The retry selection, the retry counts and the retry mark all use them,
        so a row the selection finds is exactly a row the mark may still write.
        """
        table = cls.__table__
        failed = (table.c.exit_status.isnot(None),
                  table.c.exit_status != 0,
                  table.c.exit_status != CANCEL_STATUS,  # NOTE: cancelled is terminal, not a failure
                  table.c.exit_status.not_in(NO_RETRY_STATUSES))  # NOTE: never ran, never retried
        return failed, table.c.retried.is_(False)

    @classmethod
    def query_retryable(cls: Type[Task], attempts: int, group: int = None, zone: str = None) -> Query:
        """Query failed tasks still eligible for retry under given number of `attempts` (in one `zone` if given)."""
        failed, unretried = cls.__awaiting_retry()
        query = (cls.query()
                 .filter(*failed)
                 .filter(cls.attempt < attempts)
                 .filter(unretried))
        if group is not None:
            query = query.filter(cls.group == group)
        return cls._filter_zone(query, zone)

    @classmethod
    def select_failed(cls: Type[Task], attempts: int, limit: int, group: int = None, zone: str = None) -> List[Task]:
        """Select failed tasks for retry up to some `limit` under given number of `attempts`."""
        return (cls.query_retryable(attempts, group=group, zone=zone)
                .order_by(cls.completion_time).limit(limit).all())

    @classmethod
    def count_retryable(cls: Type[Task], attempts: int, group: int, eager: bool = False) -> int:
        """
        Count failed tasks in `group` awaiting retry under given number of `attempts`.
        The failures counted are exactly the failures Task.next() retries for the same
        `group`, `attempts`, and `eager`. Without `eager`, failures are only retried when
        `attempts` exceeds one.
        """
        if attempts <= 1 and not eager:
            return 0
        return cls.query_retryable(attempts, group=group).count()

    @classmethod
    def next(cls: Type[Task],
             limit: int,
             group: int = DEFAULT_TASK_GROUP,
             attempts: int = 1,
             eager: bool = False,
             zone: str = None) -> List[Task]:
        """Select tasks for scheduling including failed tasks for re-scheduling (in one `zone` if given).

        The selected rows are stamped for dispatch (server and schedule time), each by its own
        UPDATE that matches only while the row is still unscheduled, so a row removed, cancelled or
        otherwise scheduled since it was selected is skipped, never raised on; one that still
        qualifies but was missed under contention is re-run as for any task-row write, and left
        unscheduled for a later round if it never lands. Returns the rows stamped, in the order
        selected; they read their stamp back from the database.
        """
        group = group if group is not None else cls.current_group().value
        if eager:
            tasks = cls.__next_eager(attempts=attempts, limit=limit, group=group, zone=zone)
        else:
            tasks = cls.__next_not_eager(attempts, limit, group=group, zone=zone)
        if not tasks:
            Session.commit()
            return tasks
        # NOTE: the identity, not `task.id`: a commit on the retry path expires the objects, and
        # loading one whose row has gone would raise.
        ids = [inspect(task).identity[0] for task in tasks]
        stamp = {'server_id': INSTANCE, 'server_host': HOSTNAME, 'schedule_time': datetime.now().astimezone()}
        landed = set(cls.__write_rows(ids, stamp, 'dispatch', 'task left unscheduled',
                                      unscheduled=True, prefix='d_'))
        return [task for task, id in zip(tasks, ids) if id in landed]

    @classmethod
    def __next_eager(cls: Type[Task], attempts: int, limit: int, group: int = None, zone: str = None) -> List[Task]:
        """Select next batch of tasks from database preferring previously failed tasks."""
        tasks = cls.__schedule_next_failed_tasks(attempts, limit, group=group, zone=zone)
        if len(tasks) < limit:
            new_tasks = cls.select_new(limit=limit - len(tasks), group=group, zone=zone)
            tasks.extend(new_tasks)
            log.trace(f'Selected {len(new_tasks)} new tasks')
        return tasks

    @classmethod
    def __next_not_eager(cls: Type[Task], attempts: int, limit: int, group: int = None,
                         zone: str = None) -> List[Task]:
        """Select next batch of tasks for database preferring novel tasks to old failed ones."""
        tasks = cls.select_new(limit=limit, group=group, zone=zone)
        log.trace(f'Selected {len(tasks)} new tasks')
        if len(tasks) < limit and attempts > 1:
            failed_tasks = cls.__schedule_next_failed_tasks(attempts=attempts, limit=limit - len(tasks),
                                                            group=group, zone=zone)
            tasks.extend(failed_tasks)
        return tasks

    @classmethod
    def __schedule_next_failed_tasks(cls: Type[Task], attempts: int, limit: int, group: int = None,
                                     zone: str = None) -> List[Task]:
        """Select previously failed tasks and create their retries; return the retries created.

        Each failed task is marked `retried` (naming its retry as `next_id`) by a conditional
        UPDATE that matches only while the row still awaits retry, and only the retries whose mark
        landed are inserted; the marks and the retries are one transaction, so a retry row is never
        committed without its mark nor a mark without its retry row. A row cancelled, deleted or
        marked by another writer since it was selected gets no retry. A mark missed under
        contention is re-run, and one that never lands leaves its row unmarked, with no retry row,
        for a later round. Returns the retries in the order their failed tasks were selected.
        """
        failed_tasks = cls.select_failed(attempts=attempts, limit=limit, group=group, zone=zone)
        if not failed_tasks:
            return []
        log.trace(f'Selected {len(failed_tasks)} previously failed tasks')
        retries = {task.id: cls.new_retry(task) for task in failed_tasks}
        next_ids = {id: {'next_id': task.id} for id, task in retries.items()}
        try:
            # NOTE: marks first: the retry rows are added only for the marks that landed.
            marked = set(cls.__write_rows(list(retries), {'retried': True}, 'retry mark', 'task left to retry later',
                                          retryable=True, each=next_ids, commit=False, prefix='m_'))
            tasks = [task for id, task in retries.items() if id in marked]
            Session.add_all(tasks)
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        for task in tasks:
            log.trace(f'Added {cls.__tablename__} ({inspect(task).identity[0]})')
        return tasks

    @classmethod
    def count_remaining(cls: Type[Task], group: int = None, zone: str = None) -> int:
        """Count of remaining unfinished tasks (all task groups unless `group` given, in one `zone` if given)."""
        query = cls.query().filter(cls.completion_time.is_(None))
        if group is not None:
            query = query.filter(cls.group == group)
        return cls._filter_zone(query, zone).count()

    @classmethod
    def count_remaining_outside(cls: Type[Task], group: int) -> int:
        """Count of remaining unfinished tasks in every task group but `group`, in one statement."""
        return cls.query().filter(cls.completion_time.is_(None)).filter(cls.group != group).count()

    @classmethod
    def count_remaining_by_zone(cls: Type[Task], group: int) -> Dict[str, int]:
        """Count of unfinished tasks in `group` by zone id; the default zone is keyed by its reserved id."""
        counts = dict(cls.query(cls.zone, func.count(cls.id))
                      .filter(cls.group == group)
                      .filter(cls.completion_time.is_(None))
                      .group_by(cls.zone).all())
        if None in counts:
            counts[DEFAULT_ZONE_ID] = counts.pop(None)
        return counts

    @classmethod
    def count_retryable_by_zone(cls: Type[Task], attempts: int, group: int, eager: bool = False) -> Dict[str, int]:
        """
        Count failed tasks in `group` awaiting retry by zone id, under the rule of `count_retryable`.
        The default zone is keyed by its reserved id. Without `eager`, failures are only retried
        when `attempts` exceeds one, so nothing is counted.
        """
        if attempts <= 1 and not eager:
            return {}
        counts = dict(cls.query_retryable(attempts, group=group)
                      .with_entities(cls.zone, func.count(cls.id))
                      .group_by(cls.zone).all())
        if None in counts:
            counts[DEFAULT_ZONE_ID] = counts.pop(None)
        return counts

    @classmethod
    def count_interrupted(cls: Type[Task]) -> int:
        """Count tasks that were scheduled but not completed."""
        return (
            cls.query()
            .filter(cls.schedule_time.isnot(None))
            .filter(cls.completion_time.is_(None))
            .count()
        )

    @classmethod
    def select_interrupted(cls: Type[Task], limit: int) -> List[Task]:
        """Select tasks that were scheduled but not completed."""
        return (
            cls.query()
            .order_by(cls.schedule_time)
            .filter(cls.schedule_time.isnot(None))
            .filter(cls.completion_time.is_(None))
            .limit(limit)
            .all()
        )

    @classmethod
    @cache
    def __row_statements(cls: Type[Task], names: Tuple[str, ...], condition: Optional[str] = None,
                         prefix: str = 'r_') -> Tuple[Any, Any]:
        """The UPDATE of one row by `id` for a set of columns, and the SELECT that looks the row up.

        The `condition` says which rows the UPDATE may still match, and what the SELECT reads:

        - None: any row with that `id`; the SELECT reads the row's `id`.
        - 'pending': a row with no `completion_time`; the SELECT reads that `completion_time`, so a
          write that matched nothing can tell a row that is gone from one that is complete from one
          that is still pending (missed under contention).
        - 'held': a row in flight with one client (the `held_by` bind): scheduled, with no
          `completion_time` and that `client_id`; the SELECT reads the `completion_time` of a row
          scheduled with that client, so a row gone or held by another client reads as nothing.
        - 'unscheduled': a row with no `schedule_time`; the SELECT reads that `schedule_time`.
        - 'retryable': a failed row still awaiting retry (the row-state filters of the retry
          selection: failed, not cancelled, not yet retried); the SELECT applies the same filters
          and reads the row's `id`, so it finds nothing for a row gone or no longer awaiting retry.

        Built once per column set, condition and prefix, with binds typed from the table and named
        with `prefix`, as for the result statements.
        """
        table = cls.__table__
        by_id = table.c.id == bindparam(f'{prefix}id', type_=table.c.id.type)
        if condition is None:
            match, lookup, read = (by_id, ), (by_id, ), table.c.id
        elif condition == 'pending':
            match, lookup, read = (by_id, table.c.completion_time.is_(None)), (by_id, ), table.c.completion_time
        elif condition == 'held':
            held = (table.c.client_id == bindparam(f'{prefix}held_by', type_=table.c.client_id.type),
                    table.c.schedule_time.isnot(None))
            match, lookup, read = (by_id, *held, table.c.completion_time.is_(None)), (by_id, *held), \
                table.c.completion_time
        elif condition == 'unscheduled':
            match, lookup, read = (by_id, table.c.schedule_time.is_(None)), (by_id, ), table.c.schedule_time
        elif condition == 'retryable':
            failed, unretried = cls.__awaiting_retry()
            awaiting = (by_id, *failed, unretried)
            match, lookup, read = awaiting, awaiting, table.c.id
        else:
            raise ValueError(f'Unknown task row condition: {condition!r}')
        write = (
            update(table)
            .where(*match)
            .values({name: bindparam(f'{prefix}{name}', type_=table.c[name].type) for name in names})
        )
        exists = select(read).where(*lookup)
        return write, exists

    @classmethod
    def __filter_statements(cls: Type[Task], values: Dict[str, Any], where: Tuple[ColumnElement, ...],
                            remove: bool = False, prefix: str = 'r_') -> Tuple[Any, Any]:
        """The UPDATE (or DELETE) of one row by `id` that still matches `where`, and the SELECT that looks it up.

        Both match the row by `id` first, then the criteria; the SELECT reads the row's `id`, so it
        finds nothing for a row that is gone or no longer matches. A value that is a SQL expression
        is written as given; any other is a bind typed from the table and named with `prefix`.
        """
        table = cls.__table__
        match = (table.c.id == bindparam(f'{prefix}id', type_=table.c.id.type), *where)
        if remove:
            write = delete(table).where(*match)
        else:
            write = update(table).where(*match).values({
                name: value if isinstance(value, ClauseElement) else
                bindparam(f'{prefix}{name}', type_=table.c[name].type)
                for name, value in values.items()
            })
        return write, select(table.c.id).where(*match)

    @classmethod
    def __write_rows(cls: Type[Task], ids: List[str], values: Dict[str, Any], path: str, consequence: str,
                     pending: bool = False, strict: bool = False, held_by: Optional[str] = None,
                     unscheduled: bool = False, retryable: bool = False,
                     each: Optional[Dict[str, Dict[str, Any]]] = None, commit: bool = True,
                     abort: bool = False, where: Optional[Tuple[ColumnElement, ...]] = None,
                     remove: bool = False, tried: int = 0, prefix: str = 'r_') -> List[str]:
        """Write `values` onto each task in `ids` by `id`, in one transaction; return the ids written.

        Each row is its own UPDATE, so each has its own matched count. A row matched on the first
        attempt costs that one statement. A row that matched nothing is looked up: a missing row
        raises `StaleDataError` and nothing of the call is committed, as for a bulk update; a row
        that still exists was missed because the write waited on another transaction's row lock,
        and the write is re-run, at most `CONTENDED_RERUNS` times. A row still not written after
        that is logged as a warning with the `consequence` and left as it is; with `abort`, the
        warning is followed by a rollback of the whole transaction (the caller's earlier writes
        with it) and `Task.Contended` naming the task, so nothing of the call is written.

        With `pending`, the write requires that the row has no `completion_time`. A row that is
        completed or cancelled no longer qualifies: it is settled, never re-run and never an
        error, whether it was so before the write or became so in the transaction the write waited
        on. A row that is gone is skipped the same way, unless `strict` is given, when it raises
        `StaleDataError` as for an unconditional write.

        With `held_by`, the write requires that the row is still in flight with that client
        (scheduled, not complete, that `client_id`). A row that has completed is settled as for
        `pending`; a row that is gone, no longer scheduled or held by another client is skipped.
        With `unscheduled`, the write requires that the row has no `schedule_time`; a row that is
        gone or already scheduled (or cancelled, which stamps one) is skipped. Neither raises on a
        row that no longer qualifies, and both re-run only a row that still does.
        With `retryable`, the write requires that the row is a failure still awaiting retry (the
        row-state filters of the retry selection); a row that is gone or no longer awaits retry
        (cancelled, marked retried by another writer, or no longer failed) is skipped, and only a
        row that still awaits retry is re-run.

        With `where`, the write requires that the row still matches those criteria (a user's filter,
        possibly none); a row that is gone or no longer matches is skipped, never raised on, and
        only a row that still matches is re-run. A value may then be a SQL expression (of the row's
        own columns), written as given. With `remove`, the write is a DELETE of the row under the
        same condition, with the same count, lookup and re-run; `values` is ignored. Statements with
        `where` are built for the call, not cached, since the criteria differ from call to call.

        With `tried`, every task in `ids` has already had that many attempts (a set-based statement
        that missed it), and they count toward the bound: each row gets at most `1 + CONTENDED_RERUNS
        - tried` attempts here, the first of them logged as a re-run, and the warning and
        `Task.Contended` give the total.

        With `each`, a task's own values (by id) are written over the shared `values`; every task
        in `ids` has an entry, all with the same columns, or a `ValueError` is raised before
        anything is written. Without `commit`, the transaction is left open for the caller to
        extend and commit; an error still rolls it back (the caller's writes with it) and is
        raised.

        The binds are named with `prefix`, so statements of different paths can be told apart.
        """
        condition = ('filter' if where is not None else 'held' if held_by is not None else
                     'unscheduled' if unscheduled else 'retryable' if retryable else 'pending' if pending else None)
        own: Tuple[str, ...] = ()
        if each is not None:
            own = tuple(each[ids[0]]) if ids and ids[0] in each else ()
            for id in ids:
                if id not in each or each[id].keys() != set(own):
                    raise ValueError(f'Per-task values for {path} must name the same columns for every task')
        if condition == 'filter':
            write, exists = cls.__filter_statements(values, where, remove, prefix)
            values = {} if remove else {name: value for name, value in values.items()
                                        if not isinstance(value, ClauseElement)}
        else:
            write, exists = cls.__row_statements(tuple(values) + tuple(name for name in own if name not in values),
                                                 condition, prefix)
        key = f'{prefix}id'
        params = {f'{prefix}{name}': value for name, value in values.items()}
        lookup = {f'{prefix}held_by': held_by} if held_by is not None else {}
        attempts = 1 + CONTENDED_RERUNS
        first = 1 + tried
        landed = []
        try:
            for id in ids:
                row = {f'{prefix}{name}': value for name, value in each[id].items()} if each is not None else {}
                if first > 1:
                    log.debug(f'Re-running contended {path} write for task ({id}) (attempt {first} of {attempts})')
                for attempt in range(first, attempts + 1):
                    # NOTE: one parameter set, not a list; a list is an executemany, with no rowcount per row.
                    if Session.execute(write, {**params, **row, **lookup, key: id}).rowcount == 1:
                        landed.append(id)
                        break
                    found = Session.execute(exists, {**lookup, key: id}).first()
                    if found is None:
                        if condition == 'filter':
                            log.debug(f'Ignored {path} for task ({id}) - task gone or no longer matches')
                            break
                        if condition == 'held':
                            log.debug(f'Ignored {path} for task ({id}) - task gone or no longer held by client')
                            break
                        if condition == 'unscheduled':
                            log.debug(f'Ignored {path} for task ({id}) - task gone')
                            break
                        if condition == 'retryable':
                            log.debug(f'Ignored {path} for task ({id}) - task gone or no longer awaiting retry')
                            break
                        if condition == 'pending' and not strict:
                            break
                        raise StaleDataError(f'UPDATE statement on table \'{cls.__table__.name}\' expected to '
                                             f'update 1 row(s); 0 were matched (task {id})')
                    if condition in ('pending', 'held') and found[0] is not None:
                        log.debug(f'Ignored {path} for task ({id}) - task already complete')
                        break
                    if condition == 'unscheduled' and found[0] is not None:
                        log.debug(f'Ignored {path} for task ({id}) - task already scheduled')
                        break
                    if attempt < attempts:
                        log.debug(f'Re-running contended {path} write for task ({id}) '
                                  f'(attempt {attempt + 1} of {attempts})')
                else:
                    log.warning(f'Contended {path} write did not land for task ({id}) '
                                f'after {attempts} attempts - {consequence}')
                    if abort:
                        raise cls.Contended(f'Update of task ({id}) was contended after {attempts} attempts '
                                            f'- nothing was written')
            if commit:
                Session.commit()
        except Exception:
            Session.rollback()
            raise
        log.trace(f'Updated {len(landed)} {cls.__table__.name}s')
        return landed

    @classmethod
    def update(cls: Type[Task], id: str, **changes: Any) -> None:
        """Write `changes` onto the task `id`, re-running the write while it is contended.

        One UPDATE by `id`. A row that is gone raises `StaleDataError`. A row that matched nothing
        but still exists was missed because the write waited on another transaction's row lock, and
        the write is re-run, at most `CONTENDED_RERUNS` times; a row still not written after that
        raises `Task.Contended`, with nothing written.
        """
        cls.__write_rows([id], changes, 'update', 'nothing written', abort=True, prefix='u_')

    @classmethod
    def __change(cls: Type[Task], values: Dict[str, Any], tags: Dict[str, Any], drop: List[str],
                 dialect: str) -> Dict[str, Any]:
        """The SET of an update: the field `values` as given, and one `tag` expression for every tag change.

        The tag expression works on the row's own tag at the time of the write: `tags` are set,
        then the names in `drop` are removed. On SQLite it is `json_remove(json_set(tag, ...), ...)`
        with one bind per key and value (a boolean is stored 1/0 and a dotted key nests); on
        PostgreSQL, `(tag || patch) - names`, two binds whatever the number of keys. A part with
        nothing to do is left out. `dialect` is the dialect name of the session's bind.
        """
        change = dict(values)
        if not tags and not drop:
            return change
        column = cls.__table__.c.tag
        tag = literal(values['tag'], column.type) if 'tag' in values else column
        if dialect == 'sqlite':
            if tags:
                pairs = [part for key, value in tags.items() for part in (literal(f'$.{key}', Text()), literal(value))]
                tag = func.json_set(tag, *pairs, type_=JSON)
            if drop:
                tag = func.json_remove(tag, *[literal(f'$.{name}', Text()) for name in drop], type_=JSON)
        else:
            if tags:
                patch = {key: to_json_type(value) for key, value in tags.items()}
                tag = tag.op('||', return_type=JSON_BYTES())(literal(patch, JSON_BYTES()))
            if drop:
                tag = tag.op('-', return_type=JSON_BYTES())(literal(list(drop), ARRAY(Text())))
        change['tag'] = tag
        return change

    @classmethod
    def update_selected(cls: Type[Task], ids: List[str], criteria: Tuple[ColumnElement, ...],
                        values: Dict[str, Any], tags: Dict[str, Any] = None, drop: List[str] = None) -> int:
        """Write the change onto each task in `ids` that still matches `criteria`, in one transaction; return the count.

        The change is the field `values`, the tag keys `tags` set and the tag names `drop` removed,
        one UPDATE per row by `id` and the criteria, so a task that no longer matches at its write
        (changed or deleted since it was selected) is left as it is and not counted. A row that
        matched nothing but still matches was missed because the write waited on another
        transaction's row lock, and the write is re-run, at most `CONTENDED_RERUNS` times; a row
        still not written after that rolls the whole update back and raises `Task.Contended`.
        Every row written stays locked until the one commit at the end (on SQLite, the database's
        write lock is held from the first write to that commit).
        """
        change = cls.__change(values, tags or {}, drop or [], Session.get_bind().dialect.name)
        written = cls.__write_rows(ids, change, 'update', 'nothing written', where=tuple(criteria),
                                   abort=True, prefix='u_')
        return len(written)

    @classmethod
    def delete_selected(cls: Type[Task], ids: List[str], criteria: Tuple[ColumnElement, ...]) -> int:
        """Delete each task in `ids` that still matches `criteria`, in one transaction; return the count.

        One DELETE per row by `id` and the criteria, re-run and aborted as for `update_selected`;
        every row deleted stays locked until the one commit at the end.
        """
        deleted = cls.__write_rows(ids, {}, 'delete', 'nothing deleted', where=tuple(criteria),
                                   remove=True, abort=True, prefix='u_')
        return len(deleted)

    @classmethod
    def update_matching(cls: Type[Task], criteria: Tuple[ColumnElement, ...], values: Dict[str, Any],
                        tags: Dict[str, Any] = None, drop: List[str] = None) -> int:
        """Write the change onto every task that matches `criteria`, in one transaction; return the count.

        The change is the field `values`, the tag keys `tags` set and the tag names `drop` removed,
        in one UPDATE of every matching row. On SQLite its count is the number written: the write
        transaction excludes every other writer, so no row can be missed. On PostgreSQL a row that
        waited on another transaction's row lock can be missed although it still matches, so the
        UPDATE returns the ids it wrote and one read of the ids still matching finds the rest. The
        UPDATE is each such row's first attempt; it is written by `id` under the criteria, at most
        `CONTENDED_RERUNS` more times, and a row still not written after that rolls the whole update
        back and raises `Task.Contended`.
        A row that came to match meanwhile is written and counted too. The returned ids are held in
        memory until the read, and every row written that still matches is read back.
        """
        criteria = tuple(criteria)
        dialect = Session.get_bind().dialect.name
        change = cls.__change(values, tags or {}, drop or [], dialect)
        table = cls.__table__
        write = update(table).where(*criteria).values(change)
        try:
            if dialect == 'sqlite':
                written = Session.execute(write).rowcount
                Session.commit()
                return written
            landed = set(Session.execute(write.returning(table.c.id)).scalars())
            missed = [id for id in Session.execute(select(table.c.id).where(*criteria)).scalars()
                      if id not in landed]
            if missed:
                log.debug(f'Re-checking {len(missed)} tasks the update did not write')
                rewritten = cls.__write_rows(missed, change, 'update', 'nothing written', where=criteria,
                                             abort=True, commit=False, tried=1, prefix='u_')
            else:
                rewritten = []
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        return len(landed) + len(rewritten)

    @classmethod
    def delete_matching(cls: Type[Task], criteria: Tuple[ColumnElement, ...]) -> int:
        """Delete every task that matches `criteria`, in one transaction; return the count.

        One DELETE of every matching row; on SQLite its count is the number deleted. On PostgreSQL a
        row that waited on another transaction's row lock can be missed although it still matches,
        so one read of the ids still matching (a row deleted is gone for this transaction) finds
        the rest; each is deleted by `id` under the criteria, the DELETE counting as its first
        attempt, re-run and aborted as for `update_matching`.
        """
        criteria = tuple(criteria)
        table = cls.__table__
        try:
            deleted = Session.execute(delete(table).where(*criteria)).rowcount
            if Session.get_bind().dialect.name != 'sqlite':
                missed = list(Session.execute(select(table.c.id).where(*criteria)).scalars())
                if missed:
                    log.debug(f'Re-checking {len(missed)} tasks the delete did not reach')
                    deleted += len(cls.__write_rows(missed, {}, 'delete', 'nothing deleted', where=criteria,
                                                    remove=True, abort=True, commit=False, tried=1, prefix='u_'))
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        return deleted

    @classmethod
    def confirm_all(cls: Type[Task], ids: List[str], client_id: str, client_host: str,
                    strict: bool = True) -> List[str]:
        """Record the client that confirmed the tasks identified by `ids`; return the ids recorded.

        A task already complete keeps the client its result recorded: a confirmation that arrives
        after the result (or after a cancel) writes nothing to it. A task whose row is gone raises
        `StaleDataError` and records nothing of the bundle, unless `strict` is false, when it is
        skipped like a complete one (a relay-local server, whose completed rows leave the task
        table for the relay's outbox).
        """
        return cls.__write_rows(ids, {'client_id': client_id, 'client_host': client_host},
                                'confirm', 'client not recorded', pending=True, strict=strict)

    @classmethod
    def revert_all(cls: Type[Task], ids: List[str]) -> List[str]:
        """Revert all tasks identified by `ids`; return the ids reverted."""
        reverted = cls.__write_rows(ids, dict.fromkeys(cls.revert_columns), 'revert', 'task left in flight')
        for id in reverted:
            log.trace(f'Reverted previous task ({id})')
        return reverted

    @classmethod
    def revert_staged(cls: Type[Task], ids: List[str]) -> int:
        """Revert tasks staged but never delivered, unless they have since become terminal or gone.

        Each row is one conditional UPDATE by `id` that matches only while the row has no
        `completion_time`, so the check and the write are one atomic statement: a row cancelled (or
        otherwise completed) while staged keeps its terminal state, and a deleted row matches
        nothing. A row that matched nothing is re-run only while a fresh read finds it still without
        a `completion_time` (a write that waited on another transaction's row lock), at most
        `CONTENDED_RERUNS` times; one that no longer qualifies is skipped. The whole pass is one
        transaction. Returns the number of rows reverted, which may be fewer than the ids given.
        """
        reverted = cls.__write_rows(ids, dict.fromkeys(cls.revert_columns),
                                    'staged revert', 'task left in flight', pending=True)
        log.trace(f'Reverted {len(reverted)} of {len(ids)} staged tasks')
        return len(reverted)

    @classmethod
    @cache
    def __handback_statements(cls: Type[Task]) -> Tuple[Any, Any]:
        """The conditional revert of one handed-back assignment, and the SELECT that reads it again.

        Both match the row by `id` and the `schedule_time` it was dispatched under. The revert also
        requires no `completion_time` and a `client_id` that is the relay's identity or NULL; the
        SELECT reads those two columns, so a revert that matched nothing can tell a settled row from
        one missed under contention. Built once per process, with binds typed from the table.
        """
        table = cls.__table__
        same_dispatch = (table.c.id == bindparam('r_id', type_=table.c.id.type),
                         table.c.schedule_time == bindparam('r_schedule_time', type_=table.c.schedule_time.type))
        held = or_(table.c.client_id == bindparam('r_client_id', type_=table.c.client_id.type),
                   table.c.client_id.is_(None))
        write = (
            update(table)
            .where(*same_dispatch, table.c.completion_time.is_(None), held)
            .values({name: None for name in cls.revert_columns})
        )
        dispatched = select(table.c.completion_time, table.c.client_id).where(*same_dispatch)
        return write, dispatched

    @classmethod
    def revert_handed_back(cls: Type[Task], client_id: str, assignments: List[Tuple[str, str]]) -> List[str]:
        """Revert the assignments a relay hands back, each only while it is still that relay's; return the ids reverted.

        Each assignment is a task id with the `schedule_time` it was dispatched under, as the text
        the relay received, read as a returned result's is. Each is one conditional UPDATE matching
        that `id` and `schedule_time`, no `completion_time`, and a `client_id` that is `client_id`
        or NULL (a confirmation the relay never saw land), so the check and the write are one atomic
        statement. A row that matched nothing is re-run only while a fresh read finds it still under
        that dispatch, incomplete and held by that client or none, at most `CONTENDED_RERUNS` times,
        then logged as a warning and left as it is. A row that no longer qualifies (completed,
        cancelled, reverted and dispatched again, held by another client, or deleted) is skipped.
        The whole call is one transaction.
        """
        write, dispatched = cls.__handback_statements()
        attempts = 1 + CONTENDED_RERUNS
        reverted = []
        try:
            for id, stamp in assignments:
                schedule_time = from_json_type(stamp)
                if not isinstance(schedule_time, datetime):
                    log.debug(f'Ignored hand-back of task ({id}) - schedule time {stamp!r} is not a time')
                    continue
                params = {'r_id': id, 'r_schedule_time': schedule_time, 'r_client_id': client_id}
                for attempt in range(1, attempts + 1):
                    # NOTE: one parameter set, not a list; a list is an executemany, with no rowcount per row.
                    if Session.execute(write, params).rowcount == 1:
                        reverted.append(id)
                        break
                    found = Session.execute(dispatched, params).first()
                    if found is None or found[0] is not None or found[1] not in (None, client_id):
                        log.debug(f'Ignored hand-back of task ({id}) - no longer held under that dispatch')
                        break
                    if attempt < attempts:
                        log.debug(f'Re-running contended hand-back revert for task ({id}) '
                                  f'(attempt {attempt + 1} of {attempts})')
                else:
                    log.warning(f'Contended hand-back revert did not land for task ({id}) '
                                f'after {attempts} attempts - task left with relay ({client_id})')
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        for id in reverted:
            log.trace(f'Reverted handed-back task ({id})')
        return reverted

    @classmethod
    def revert(cls: Type[Task], id: str) -> None:
        """Revert single task by `id`."""
        cls.revert_all([id, ])

    @classmethod
    def revert_interrupted(cls: Type[Task]) -> None:
        """Revert scheduled but incomplete tasks to unscheduled state."""
        # A pass that reverts nothing ends the loop, so a row left in flight cannot be selected forever.
        while tasks := cls.select_interrupted(100):
            if not cls.revert_all([task.id for task in tasks]):
                break

    @classmethod
    def cancel_all(cls: Type[Task], ids: List[str]) -> List[str]:
        """Cancel all tasks identified by `ids`, in one transaction; return the ids cancelled.

        Each row is one UPDATE by `id`, whatever state it is in. A row that is gone raises
        `StaleDataError` and nothing of the call is committed. A row that matched nothing but
        still exists was missed because the write waited on another transaction's row lock, and
        the cancel is re-run, at most `CONTENDED_RERUNS` times; a row still not cancelled after
        that is logged as a warning, left as it is, and left out of the ids returned, so a caller
        can compare them with `ids`.
        """
        # NOTE: completion_time is set so the task is terminal - otherwise it reads as
        # "interrupted" (scheduled but incomplete) and gets reverted/re-run on restart.
        now = datetime.now().astimezone()
        cancelled = cls.__write_rows(ids, {'schedule_time': now, 'completion_time': now, 'exit_status': CANCEL_STATUS},
                                     'cancel', 'task not cancelled', prefix='c_')
        for id in cancelled:
            log.trace(f'Cancelled task ({id})')
        return cancelled

    @classmethod
    def cancel(cls: Type[Task], id: str) -> bool:
        """Cancel single task by `id`; return whether it was cancelled (see `cancel_all`)."""
        return cls.cancel_all([id, ]) == [id, ]

    @classmethod
    def apply_results(cls: Type[Task], results: List[Task]) -> List[ResultOutcome]:
        """Write returned task results, each only onto the dispatch that produced it.

        A result is identified with its dispatch by the `schedule_time` it carries, which the
        server stamped when it sent the task and every client returns unchanged. Each result is
        one conditional UPDATE of the result columns, matching only while the row still has that
        `schedule_time` and no `completion_time`, so the check and the write are one atomic
        statement against a concurrent revert or reschedule. A result that matches nothing is
        classified as a DUPLICATE (its own dispatch already completed the row) or STALE. A result
        whose row still qualifies although its write matched nothing (the write waited on another
        transaction's row lock) is re-run, at most `CONTENDED_RERUNS` times, and is DEFERRED if
        it still has not landed. Returns one outcome per result, in order, and commits once for
        the whole list.
        """
        outcomes = []
        try:
            for result in results:
                outcomes.append(cls.__apply_result(result))
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        for result, outcome in zip(results, outcomes):
            log.trace(f'Result {outcome.name.lower()} for task ({result.id})')
        return outcomes

    @classmethod
    @cache
    def __result_statements(cls: Type[Task]) -> Tuple[Any, Any, Tuple[Tuple[str, str], ...]]:
        """The conditional UPDATE and classifying SELECT for one result, and the (column, bind) pairs.

        Built once per process, so each result binds only its own values rather than building,
        coercing and keying a new statement. Every bind takes the column's own type, so datetimes,
        UUIDs and dialect variants are processed exactly as for a statement built from the values.
        Bind names are prefixed, since SQLAlchemy reserves a column's own name in a SET clause.
        """
        table = cls.__table__
        binds = tuple((name, f'r_{name}') for name in cls.result_columns)
        same_dispatch = (table.c.id == bindparam('r_id', type_=table.c.id.type),
                         table.c.schedule_time == bindparam('r_schedule_time', type_=table.c.schedule_time.type))
        write = (
            update(table)
            .where(*same_dispatch, table.c.completion_time.is_(None))
            .values({name: bindparam(key, type_=table.c[name].type) for name, key in binds})
        )
        dispatched = select(table.c.completion_time).where(*same_dispatch)
        return write, dispatched, binds

    @classmethod
    def __apply_result(cls: Type[Task], result: Task) -> ResultOutcome:
        """Conditionally write one result within the current transaction and classify it."""
        if result.schedule_time is None:
            # NOTE: comparing to None renders IS NULL, which would match a reverted row.
            return ResultOutcome.STALE
        write, dispatched, binds = cls.__result_statements()
        dispatch = {'r_id': result.id, 'r_schedule_time': result.schedule_time}
        values = {key: getattr(result, name) for name, key in binds}
        values.update(dispatch)
        attempts = 1 + CONTENDED_RERUNS
        for attempt in range(1, attempts + 1):
            # NOTE: one parameter set, not a list; a list is an executemany, with no rowcount per result.
            matched = Session.execute(write, values).rowcount
            if matched == 1:
                return ResultOutcome.APPLIED
            # NOTE: a cancel restamps schedule_time, so a cancelled row never matches here and its
            # result is STALE; a run that itself exited with the cancel status value is a DUPLICATE.
            found = Session.execute(dispatched, dispatch).first()
            if found is None:
                return ResultOutcome.STALE
            if found[0] is not None:
                return ResultOutcome.DUPLICATE
            # The row is still in flight under this dispatch, so the write waited on a row lock.
            if attempt < attempts:
                log.debug(f'Re-running contended result write for task ({result.id}) '
                          f'(attempt {attempt + 1} of {attempts})')
        return ResultOutcome.DEFERRED

    @classmethod
    def select_orphaned(cls: Type[Task], client_id: str, limit: int) -> List[Task]:
        """Select tasks that were orphaned from an evicted client (in flight with that client)."""
        return (
            cls.query()
            .order_by(cls.schedule_time)
            .filter(cls.schedule_time.isnot(None))
            .filter(cls.completion_time.is_(None))
            .filter(cls.client_id == client_id)
            .limit(limit)
            .all()
        )

    @classmethod
    def revert_orphaned(cls: Type[Task], client_id: str) -> None:
        """Revert orphaned tasks from an evicted client to unscheduled state.

        Each row is one conditional UPDATE by `id` that matches only while the row is still in
        flight with that client (as `select_orphaned` reads it), so the check and the write are one
        atomic statement: a result that landed since the read is kept and the task is not run
        again, and a row removed since the read (a relay-local server's completed rows leave for
        the relay's outbox) is skipped, never raised on. A row that matched nothing is re-run only
        while a fresh read finds it still in flight with that client, at most `CONTENDED_RERUNS`
        times, and is otherwise left in flight with a warning.
        """
        # Each row is tried once: a row left in flight at the bound would be selected again forever.
        tried = set()
        while tasks := cls.select_orphaned(client_id, 100):
            ids = [task.id for task in tasks if task.id not in tried]
            if not ids:
                break
            tried.update(ids)
            reverted = cls.__write_rows(ids, dict.fromkeys(cls.revert_columns), 'revert', 'task left in flight',
                                        held_by=client_id)
            for id in reverted:
                log.trace(f'Reverted previous task ({id})')

    @classmethod
    def latest_server(cls: Type[Task]) -> Optional[str]:
        """Unique ID of most recent active server (if reusing database)."""
        if records := (
                Session.query(Task.server_id)
                .filter(cls.schedule_time.isnot(None))
                .order_by(func.max(cls.schedule_time).desc())
                .group_by(cls.server_id)
                .first()
        ):
            return records[0]
        else:
            return None

    @classmethod
    def effective_rate_by_client(cls: Type[Task], zone: str = None) -> Optional[Dict[str, float]]:
        """
        Effective completion rate in tasks per second by client (over tasks of one `zone` if given).
        A client serves exactly one zone, so the tasks of a zone select the clients of that zone.
        """
        if server_id := cls.latest_server():
            query = (cls.query(cls.client_id,
                               func.max(cls.completion_time),
                               func.min(cls.start_time),
                               func.count(cls.id))
                     .join(Client, Task.client_id == Client.id)
                     .filter(cls.server_id == server_id)
                     .filter(cls.completion_time.isnot(None))
                     .filter(Client.disconnected_at == None))  # noqa: comparison to None
            return {
                id: 1 / ((t_max - t_min).total_seconds() / t_n)
                for id, t_max, t_min, t_n in cls._filter_zone(query, zone).group_by(cls.client_id).all()
            }
        else:
            return None

    @classmethod
    def effective_rate(cls: Type[Task], zone: str = None) -> Optional[float]:
        """Effective completion rate in tasks per second (in one `zone` if given)."""
        if by_client := cls.effective_rate_by_client(zone=zone):
            return sum(by_client.values())
        else:
            return None

    @classmethod
    def avg_duration(cls: Type[Task], zone: str = None) -> Optional[float]:
        """Average task duration by active clients (over tasks of one `zone` if given)."""
        if server_id := cls.latest_server():
            query = (cls.query(func.avg(cls.duration))
                     .join(Client, Task.client_id == Client.id)
                     .filter(cls.server_id == server_id)
                     .filter(cls.duration.isnot(None))
                     .filter(Client.disconnected_at == None))  # noqa: comparison to None
            if duration := cls._filter_zone(query, zone).one()[0]:
                return float(duration)
            else:
                return None
        else:
            return None

    @classmethod
    def time_to_completion(cls: Type[Task], group: int = None, zone: str = None) -> Optional[float]:
        """Estimated time in seconds until all incomplete tasks are completed (in one `zone` if given)."""
        if rate := cls.effective_rate(zone=zone):
            return cls.count_remaining(group=group, zone=zone) / rate
        else:
            return None

    @classmethod
    def task_pressure(cls: Type[Task], factor: float, group: int = None, zone: str = None) -> Optional[float]:
        """Ratio of current ETC to relative `factor` of task duration (in one `zone` if given)."""
        if avg_duration := cls.avg_duration(zone=zone):
            if toc := cls.time_to_completion(group=group, zone=zone):
                return toc / (factor * avg_duration)
            else:
                return None
        else:
            return None

    @classmethod
    def count_for_source(cls: Type[Task], source_id: str) -> int:
        """Count tasks stamped with the given `source_id`, for the incomplete-prior-submission check.

        Served by the leading column of `index_tasks_source`; a bound `source_id` seeks the
        B-tree (no full table `SCAN`), so the check stays sub-linear even on a huge table.
        """
        return cls.query().filter(cls.source == source_id).count()

    @classmethod
    def fingerprints_for_sources(cls: Type[Task], source_ids: List[str]) -> set[str]:
        """Set of task identity fingerprints across the given `source_ids` (de-dup skip-set).

        The de-dup scope is a same-path source lineage — a small set of sources — so, backed by
        the covering `index_tasks_source` on `(source, fingerprint)`, this is a bounded index-only
        scan, not a walk of the whole task table.
        """
        if not source_ids:
            return set()
        rows = (cls.query(cls.fingerprint)
                .filter(cls.source.in_(set(source_ids)))
                .filter(cls.fingerprint.isnot(None))
                .distinct()
                .all())
        return {row[0] for row in rows}


# Indices for efficient queries
index_tasks_unscheduled = Index('index_tasks_unscheduled', Task.group, Task.schedule_time)
index_tasks_retries = Index('index_tasks_retries', Task.exit_status, Task.retried)
index_tasks_previous_id = Index('index_tasks_previous_id', Task.previous_id)
index_tasks_next_id = Index('index_tasks_next_id', Task.next_id)
# Covering index for source detection / lineage de-dup. Deliberately *not* partial:
# a partial `WHERE source NOT IN (<direct>, <stdin>)` predicate is only honored when the query
# repeats it with matching literals, but `count_for_source`/`fingerprints_for_sources` bind the
# source as a parameter — which neither SQLite nor PostgreSQL can prove satisfies a literal
# partial predicate, so a partial index silently degrades those lookups to a full table SCAN.
# A full index is used with plain bound parameters on every engine; the reserved-source rows it
# also carries are a marginal cost (real named-file sources dominate the target workload).
index_tasks_source = Index('index_tasks_source', Task.source, Task.fingerprint)
# Zone-filtered selection of unscheduled tasks. The default zone is stored as NULL, so its
# `zone IS NULL` predicate seeks this index as a named zone's equality does. Ending in
# `submit_time` lets the selection read rows already in submit order, with no sort.
index_tasks_zone = Index('index_tasks_zone', Task.zone, Task.group, Task.schedule_time, Task.submit_time)


class Client(Entity):
    """Client entity within database implements client methods."""

    id: Mapped[str] = mapped_column(UUID, primary_key=True, nullable=False)
    host: Mapped[str] = mapped_column(TEXT, nullable=False)

    server_id: Mapped[str] = mapped_column(UUID, nullable=False)
    server_host: Mapped[str] = mapped_column(TEXT, nullable=False)

    connected_at: Mapped[Optional[str]] = mapped_column(DATETIME, nullable=True)
    disconnected_at: Mapped[Optional[datetime]] = mapped_column(DATETIME, nullable=True)
    evicted: Mapped[bool] = mapped_column(BOOLEAN, nullable=False)

    # A relay registers with its site and names itself as relay; the site clients it forwards
    # (its roster) carry the same site and the relay's id. Both are NULL for an ordinary client.
    site: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    relay: Mapped[Optional[str]] = mapped_column(UUID, nullable=True)

    columns = {
        'id': str,
        'host': str,
        'server_id': str,
        'server_host': str,
        'connected_at': datetime,
        'disconnected_at': datetime,
        'evicted': bool,
        'site': str,
        'relay': str,
    }

    class NotFound(NotFound):
        pass

    class NotDistinct(NotDistinct):
        pass

    class AlreadyExists(AlreadyExists):
        pass

    @classmethod
    def from_id(cls: Type[Client], id: str, caching: bool = True) -> Client:
        """Look up client by unique `id`."""
        try:
            return cls.query(caching=caching).filter_by(id=id).one()
        except NoResultFound as error:
            raise cls.NotFound(f'No client with id={id}') from error

    @classmethod
    def from_heartbeat(cls: Type[Client], hb: Heartbeat) -> Client:
        """Initialize entity from client heartbeat message (a beat with a site is a relay's own row)."""
        if hb.site is None:
            return cls.new(id=hb.uuid, host=hb.host, connected_at=hb.time)
        return cls.new(id=hb.uuid, host=hb.host, connected_at=hb.time, site=hb.site, relay=hb.uuid)

    @classmethod
    def new(cls: Type[Client],
            id: str = None,
            host: str = HOSTNAME,
            server_id: str = INSTANCE,
            server_host: str = HOSTNAME,
            evicted: bool = False,
            **other) -> Client:
        """Create a new client."""
        return cls(id=(id or uuid()), host=host,
                   server_id=server_id, server_host=server_host,
                   evicted=evicted, **other)

    @classmethod
    def count_connected(cls: Type[Client]) -> int:
        """Count active clients that execute tasks (a relay's own row is not one; its site clients are)."""
        if server_id := Task.latest_server():
            return (cls.query().filter_by(server_id=server_id, disconnected_at=None)
                    .filter(or_(cls.relay.is_(None), cls.relay != cls.id))
                    .count())
        else:
            return 0

    @classmethod
    def reconnect(cls: Type[Client], id: str, **changes: Any) -> None:
        """Mark an existing client connected again under this server, with any other `changes`."""
        cls.update(id, disconnected_at=None, evicted=False, server_id=INSTANCE, server_host=HOSTNAME, **changes)

    @classmethod
    def disconnect_all(cls: Type[Client], ids: List[str], when: datetime) -> int:
        """Mark the connected clients among `ids` disconnected at `when`; returns how many were."""
        if not ids:
            return 0
        statement = (update(cls).where(cls.id.in_(ids)).where(cls.disconnected_at.is_(None))
                     .values(disconnected_at=when).execution_options(synchronize_session=False))
        return cls.__execute(statement)

    @classmethod
    def disconnect_roster(cls: Type[Client], relay: str, when: datetime) -> int:
        """Mark every connected site client forwarded by `relay` disconnected at `when` (one statement)."""
        statement = (update(cls).where(cls.relay == relay).where(cls.id != relay)
                     .where(cls.disconnected_at.is_(None))
                     .values(disconnected_at=when).execution_options(synchronize_session=False))
        return cls.__execute(statement)

    @classmethod
    def __execute(cls: Type[Client], statement: Any) -> int:
        """Run one write statement in its own transaction; returns the rows it matched."""
        try:
            count = Session.execute(statement).rowcount
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        return count


# Indices for efficient queries
index_client_disconnect = Index('client_disconnected_at', Client.disconnected_at)


class Source(Entity):
    """Source entity: provenance record for a batch of ingested tasks.

    One row per ingested named task file, capturing the absolute path, a
    file-content fingerprint (md5), the expected task count, and a creation time.
    Re-submission consults these to detect a prior ingest, warn on an incomplete one,
    de-dup novel tasks, or refuse. The reserved ``<direct>`` and ``<stdin>``
    submission modes are real rows with fixed ids (see `Source.reserved`,
    `DIRECT_SOURCE_ID`, `STDIN_SOURCE_ID`) and are exempt from gating. Lineage is
    *all sources sharing a `path`* — there is deliberately no lineage-pointer column.
    """

    id: Mapped[str] = mapped_column(UUID, primary_key=True, nullable=False)
    path: Mapped[str] = mapped_column(TEXT, nullable=False)
    fingerprint: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)
    task_count: Mapped[Optional[int]] = mapped_column(INTEGER, nullable=True)
    created: Mapped[datetime] = mapped_column(DATETIME, nullable=False)

    columns = {
        'id': str,
        'path': str,
        'fingerprint': str,
        'task_count': int,
        'created': datetime,
    }

    class NotFound(NotFound):
        pass

    class NotDistinct(NotDistinct):
        pass

    class AlreadyExists(AlreadyExists):
        pass

    @classmethod
    def from_id(cls: Type[Source], id: str, caching: bool = True) -> Source:
        """Look up source by unique `id`."""
        try:
            return cls.query(caching=caching).filter_by(id=id).one()
        except NoResultFound as error:
            raise cls.NotFound(f'No source with id={id}') from error
        except MultipleResultsFound as error:
            raise cls.NotDistinct(f'Multiple sources with id={id}') from error

    @classmethod
    def new(cls: Type[Source], path: str, fingerprint: str = None, task_count: int = None, **other) -> Source:
        """Create a new source record (id minted via `uuid()`)."""
        return cls(id=uuid(), path=path, fingerprint=fingerprint, task_count=task_count,
                   created=datetime.now().astimezone(), **other)

    @classmethod
    def reserved(cls: Type[Source], const_id: str) -> Source:
        """Lazily get-or-create the reserved source row identified by `const_id`.

        Idempotent: the fixed-id row (`<direct>`/`<stdin>`) is created once on first use
        and reused thereafter, so `Task.source` can point at a real native-UUID row.

        Safe under concurrency on SQLite and PostgreSQL: several processes may all miss the row
        and all insert it, so the insert does nothing when the id already exists, and the row is
        read again after it, whichever process wrote it. The insert's row count is never read
        (PostgreSQL does not report one for a skipped row).
        """
        try:
            return cls.from_id(const_id, caching=False)
        except cls.NotFound:
            values = {'id': const_id, 'path': RESERVED_SOURCE_PATHS[const_id], 'created': datetime.now().astimezone()}
            insert = UPSERT_BY_DIALECT.get(Session.get_bind().dialect.name)
            if insert is None:
                source = cls(**values)
                cls.add(source)
                return source
            try:
                Session.execute(insert(cls).values(**values).on_conflict_do_nothing(index_elements=['id']))
                Session.commit()
            except Exception:
                Session.rollback()
                raise
            return cls.from_id(const_id, caching=False)

    @classmethod
    def lookup(cls: Type[Source], path: str) -> List[Source]:
        """All sources sharing `path` (the same-path lineage), newest first."""
        return (cls.query()
                .filter(cls.path == path)
                .order_by(cls.created.desc())
                .all())

    @classmethod
    def matching(cls: Type[Source], path: str, fingerprint: str) -> Optional[Source]:
        """Most recent source with identical `path` and `fingerprint`, else None."""
        return (cls.query()
                .filter(cls.path == path)
                .filter(cls.fingerprint == fingerprint)
                .order_by(cls.created.desc())
                .first())

    @classmethod
    def paths_for_ids(cls: Type[Source], ids: List[str]) -> Dict[str, str]:
        """Map of source id -> path for the given `ids` (batched presentation lookup)."""
        if not ids:
            return {}
        return dict(cls.query(cls.id, cls.path).filter(cls.id.in_(set(ids))).all())

    @classmethod
    def fingerprints_for_ids(cls: Type[Source], ids: List[str]) -> Dict[str, str]:
        """
        Map of source id -> content fingerprint for the given `ids` (batched presentation lookup).

        A source with a NULL fingerprint (the reserved `<direct>`/`<stdin>` rows, or a pre-
        fingerprinting row) maps to None; callers render no parenthetical for those.
        """
        if not ids:
            return {}
        return dict(cls.query(cls.id, cls.fingerprint).filter(cls.id.in_(set(ids))).all())


# Indices for efficient queries
index_source_lookup = Index('index_source_lookup', Source.path, Source.fingerprint)


# Inserts that can skip a row conflicting with an existing one, by dialect name.
UPSERT_BY_DIALECT: Final[Dict[str, Any]] = {'sqlite': sqlite_insert, 'postgresql': postgres_insert}


class Zone(Entity):
    """Zone entity: a user-defined placement label for tasks and the clients that serve them.

    One row per zone name. The id is derived from the name (see `zone_id`), so every process
    resolves a name to the same id with no lookup, and `Zone.ensure` records the mapping with a
    single insert that ignores a concurrent duplicate. The reserved default zone has the fixed id
    `DEFAULT_ZONE_ID` and name `DEFAULT_ZONE_NAME`; tasks in it store NULL in `task.zone`, and
    readers never require its row.
    """

    id: Mapped[str] = mapped_column(UUID, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(TEXT, nullable=False, unique=True)
    created: Mapped[datetime] = mapped_column(DATETIME, nullable=False)

    columns = {
        'id': str,
        'name': str,
        'created': datetime,
    }

    class NotFound(NotFound):
        pass

    class NotDistinct(NotDistinct):
        pass

    class AlreadyExists(AlreadyExists):
        pass

    @classmethod
    def from_id(cls: Type[Zone], id: str, caching: bool = True) -> Zone:
        """Look up zone by unique `id`."""
        try:
            return cls.query(caching=caching).filter_by(id=id).one()
        except NoResultFound as error:
            raise cls.NotFound(f'No zone with id={id}') from error

    @classmethod
    def ensure(cls: Type[Zone], name: Optional[str]) -> str:
        """Record the zone called `name` if it is not yet recorded, and return its id.

        Idempotent and safe under concurrency: the id is derived from the name, and the row is
        written by one insert that does nothing when the id or name already exists, so no
        process fails because another created the row first.
        """
        id = zone_id(name)
        name = DEFAULT_ZONE_NAME if id == DEFAULT_ZONE_ID else name
        values = {'id': id, 'name': name, 'created': datetime.now().astimezone()}
        insert = UPSERT_BY_DIALECT.get(Session.get_bind().dialect.name)
        if insert is not None:
            Session.execute(insert(cls).values(**values).on_conflict_do_nothing())
            Session.commit()
            return id
        try:
            with Session.begin_nested():
                Session.add(cls(**values))
            Session.commit()
        except IntegrityError:
            Session.rollback()
        return id

    @classmethod
    def names_for_ids(cls: Type[Zone], ids: List[str]) -> Dict[str, str]:
        """Map of zone id -> name for the given `ids` (batched presentation lookup)."""
        if not ids:
            return {}
        return dict(cls.query(cls.id, cls.name).filter(cls.id.in_(set(ids))).all())
