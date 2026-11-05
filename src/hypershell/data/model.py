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
from sqlalchemy import Column, Index, func, text, update, select, bindparam
from sqlalchemy.orm import Query, DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound, StaleDataError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.types import Integer, Float, DateTime, Text, Boolean, JSON as JSON_TEXT
from sqlalchemy.dialects.postgresql import SMALLINT, UUID as POSTGRES_UUID, JSONB as JSON_BYTES
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# Internal libs
from hypershell.core.logging import Logger, HOSTNAME, INSTANCE
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.types import JSONData, to_json_type, from_json_type, serialize, deserialize, parse_bytes
from hypershell.core.uuid import uuid
from hypershell.core.tag import Tag
from hypershell.data.core import schema, Session

# Public interface
__all__ = [
    'Task', 'Client', 'Source', 'Zone', 'Entity', 'to_json_type', 'from_json_type', 'serialize_tasks', 'deserialize_tasks',
    'UUID', 'TEXT', 'INTEGER', 'SMALL_INTEGER', 'DATETIME', 'BOOLEAN', 'JSON',
    'DEFAULT_TASK_GROUP', 'CANCEL_STATUS', 'DIRECT_SOURCE_ID', 'STDIN_SOURCE_ID', 'TaskGroupInfo', 'ResultOutcome',
    'DEFAULT_ZONE_ID', 'DEFAULT_ZONE_NAME', 'ZONE_NAMESPACE', 'zone_id', 'zone_name',
]

# Initialize logger
log = Logger.with_name(__name__)


# Constants
DEFAULT_TASK_GROUP: Final[int] = 0
"""Default task group for backwards compatibility."""

CANCEL_STATUS: Final[int] = -1
"""Sentinel `exit_status` marking a cancelled task.

A cancelled task is terminal: it must never be scheduled, retried, or counted as an
unrecoverable failure. It is distinguished from a genuine failure (positive exit codes,
or the negative template/resource sentinels in `client.py`) solely by this value, so the
scheduler's failure and retry queries filter it out explicitly.
"""

REVERT_CHUNK: Final[int] = 500
"""Most ids bound in one conditional revert statement (well within SQLite's and PostgreSQL's limits)."""

CONTENDED_RERUNS: Final[int] = 3
"""Extra attempts for a task-row UPDATE that matched no row while the row still qualifies.

On a TimescaleDB 2.29+ hypertable, an UPDATE that waited on another transaction's row lock can
report 0 matched rows although the row exists and still satisfies the statement's condition. A
new statement sees the other transaction's commit and matches normally, so a write that a fresh
read shows still qualifies is re-run, at most this many times.
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


class AlreadyExists(DatabaseError):
    """Exception specific to a record with unique properties already existing."""


# Pre-defining types shortens declarations and makes changes easier
UUID = Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')
TEXT = Text()
INTEGER = Integer()
SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')
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
        """Build from JSON `text` string."""
        return cls.from_dict({key: from_json_type(value) for key, value in data.items()})

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
    memory: Mapped[int] = mapped_column(INTEGER, nullable=True)  # NULL means untracked
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
        other['group'] = tag.pop('group', group)
        other['cores'] = tag.pop('cores', other.get('cores', None))
        # A memory value may arrive as a unit-bearing string ('2GB') from an inline
        # `memory:` tag (parsed by smart_coerce, which leaves units alone) — parse it to
        # integer bytes as the `--memory` flag does, so resource accounting stays numeric.
        memory = tag.pop('memory', other.get('memory', None))
        other['memory'] = parse_bytes(memory) if isinstance(memory, str) else memory
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
        """Separate input args from possible inline tag comment."""
        args = str(args).strip()
        if match := re.search(r'#\s*HYPERSHELL:?', args):
            try:
                tags = Tag.parse_cmdline_list(args[match.end():].strip().split())
                cls.ensure_valid_tag(tags)
            except (ValueError, TypeError) as error:
                raise RuntimeError(f'Failed to parse inline tags ({error}, from: "{args}")') from error
            args = args[:match.start()]
            return args.strip(), tags
        elif match := re.search(r'#', args):
            args = args[:match.start()]
            return args.strip(), {}
        else:
            return args.strip(), {}

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

        if Task.count() == 0:
            return TaskGroupInfo(DEFAULT_TASK_GROUP, 'no tasks submitted')

        else:
            # NOTE: we cannot just naively go with first submitted task (even though that makes sense)
            # because the groups might have been altered, and we get stuck in an infinite loop waiting on
            # all tasks to be completed (lower task group never get scheduled) but there are no more groups.
            submitted_task = Session.query(Task).order_by(Task.group).first()
            return TaskGroupInfo(submitted_task.group, f'no tasks scheduled - defaulting to earliest group')

    @classmethod
    def increment_group(cls: Type[Task], previous_group: int, attempts: int, eager: bool = False) -> TaskGroupInfo:
        """
        This should only be called if no tasks are returned by Task.next().
        Check the current group and increment if necessary with explanation.
        We assume all tasks in `previous_group` have already been scheduled (see Task.next()).
        A group is held while it has incomplete tasks or failures still awaiting retry.
        The next group is the lowest group holding incomplete tasks, which may be lower than
        `previous_group` when tasks are submitted late.
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
        failed_query = (
            Session.query(Task)
            .filter(Task.group == previous_group)
            .filter(cls.exit_status.isnot(None))
            .filter(cls.exit_status != 0)
            .filter(cls.exit_status != CANCEL_STATUS)  # NOTE: cancelled is terminal, not a failure
            .filter(cls.attempt >= attempts)
            .filter(cls.retried.is_(False))
        )
        failed = failed_query.count()
        if failed > 0:
            example = failed_query.first()
            return TaskGroupInfo(previous_group,
                                 f'at least {failed} tasks exceeding allowed retries, example: {example.id}',
                                 False)
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
    def lowest_incomplete_group(cls: Type[Task]) -> Optional[int]:
        """The smallest group holding a task that has not completed, or None."""
        return Session.query(func.min(cls.group)).filter(cls.completion_time.is_(None)).scalar()

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
    def query_retryable(cls: Type[Task], attempts: int, group: int = None, zone: str = None) -> Query:
        """Query failed tasks still eligible for retry under given number of `attempts` (in one `zone` if given)."""
        query = (cls.query()
                 .filter(cls.exit_status.isnot(None))
                 .filter(cls.exit_status != 0)
                 .filter(cls.exit_status != CANCEL_STATUS)  # NOTE: cancelled is terminal, not a failure
                 .filter(cls.attempt < attempts)
                 .filter(cls.retried.is_(False)))
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
        """Select tasks for scheduling including failed tasks for re-scheduling (in one `zone` if given)."""
        group = group if group is not None else cls.current_group().value
        if eager:
            tasks = cls.__next_eager(attempts=attempts, limit=limit, group=group, zone=zone)
        else:
            tasks = cls.__next_not_eager(attempts, limit, group=group, zone=zone)
        for task in tasks:
            task.server_id = INSTANCE
            task.server_host = HOSTNAME
            task.schedule_time = datetime.now().astimezone()
        Session.commit()
        return tasks

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
        """Select previously failed tasks for scheduling."""
        tasks = []
        failed_tasks = cls.select_failed(attempts=attempts, limit=limit, group=group, zone=zone)
        if failed_tasks:
            log.trace(f'Selected {len(failed_tasks)} previously failed tasks')
            new_tasks = [cls.new(args=task.args,
                                 attempt=task.attempt + 1,
                                 previous_id=task.id,
                                 tag=task.tag,
                                 group=task.group,
                                 fingerprint=task.fingerprint,  # A retry is the same logical task.
                                 source=task.source,            # Keep the retry chain linked to its source.
                                 zone=task.zone)                # A retry runs in its task's zone.
                         for task in failed_tasks]
            tasks.extend(new_tasks)
            cls.add_all(tasks)
            cls.update_all([{'id': old_task.id, 'retried': True, 'next_id': new_task.id}
                            for old_task, new_task in zip(failed_tasks, new_tasks)])
        return tasks

    @classmethod
    def count_remaining(cls: Type[Task], group: int = None) -> int:
        """Count of remaining unfinished tasks (all task groups unless `group` given)."""
        query = cls.query().filter(cls.completion_time.is_(None))
        if group is not None:
            query = query.filter(cls.group == group)
        return query.count()

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
    def __row_statements(cls: Type[Task], names: Tuple[str, ...]) -> Tuple[Any, Any]:
        """The UPDATE of one row by `id` for a set of columns, and the SELECT of that row's `id`.

        Built once per column set, with binds typed from the table and prefixed names, as for the
        result statements.
        """
        table = cls.__table__
        by_id = table.c.id == bindparam('r_id', type_=table.c.id.type)
        write = (
            update(table)
            .where(by_id)
            .values({name: bindparam(f'r_{name}', type_=table.c[name].type) for name in names})
        )
        exists = select(table.c.id).where(by_id)
        return write, exists

    @classmethod
    def __write_rows(cls: Type[Task], ids: List[str], values: Dict[str, Any],
                     path: str, consequence: str) -> List[str]:
        """Write the same `values` onto each task in `ids` by `id`, committing once; return the ids written.

        Each row is its own UPDATE, so each has its own matched count. A row matched on the first
        attempt costs that one statement. A row that matched nothing is looked up: a missing row
        raises `StaleDataError` and nothing of the call is committed, as for a bulk update; a row
        that still exists was missed because the write waited on another transaction's row lock,
        and the write is re-run, at most `CONTENDED_RERUNS` times. A row still not written after
        that is logged as a warning with the `consequence` and left as it is.
        """
        write, exists = cls.__row_statements(tuple(values))
        params = {f'r_{name}': value for name, value in values.items()}
        attempts = 1 + CONTENDED_RERUNS
        landed = []
        try:
            for id in ids:
                for attempt in range(1, attempts + 1):
                    # NOTE: one parameter set, not a list; a list is an executemany, with no rowcount per row.
                    if Session.execute(write, {**params, 'r_id': id}).rowcount == 1:
                        landed.append(id)
                        break
                    if Session.execute(exists, {'r_id': id}).first() is None:
                        raise StaleDataError(f'UPDATE statement on table \'{cls.__table__.name}\' expected to '
                                             f'update 1 row(s); 0 were matched (task {id})')
                    if attempt < attempts:
                        log.debug(f'Re-running contended {path} write for task ({id}) '
                                  f'(attempt {attempt + 1} of {attempts})')
                else:
                    log.warning(f'Contended {path} write did not land for task ({id}) '
                                f'after {attempts} attempts - {consequence}')
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        log.trace(f'Updated {len(landed)} {cls.__table__.name}s')
        return landed

    @classmethod
    def confirm_all(cls: Type[Task], ids: List[str], client_id: str, client_host: str) -> List[str]:
        """Record the client that confirmed the tasks identified by `ids`; return the ids recorded."""
        return cls.__write_rows(ids, {'client_id': client_id, 'client_host': client_host},
                                'confirm', 'client not recorded')

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

        One conditional UPDATE matches only rows still without a `completion_time`, so the check and
        the write are one atomic statement: a row cancelled (or otherwise completed) while staged keeps
        its terminal state, and a deleted row matches nothing. The ids go in chunks that stay within
        every database's limit on bound parameters, all in one transaction. Returns the number of rows
        reverted, which may be fewer than the ids given.
        """
        reverted = 0
        try:
            for start in range(0, len(ids), REVERT_CHUNK):
                reverted += Session.execute(
                    update(cls)
                    .where(cls.id.in_(ids[start:start + REVERT_CHUNK]),
                           cls.completion_time.is_(None))
                    .values(dict.fromkeys(cls.revert_columns))
                    .execution_options(synchronize_session=False)
                ).rowcount
            Session.commit()
        except Exception:
            Session.rollback()
            raise
        log.trace(f'Reverted {reverted} of {len(ids)} staged tasks')
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
    def cancel_all(cls: Type[Task], ids: List[str]) -> None:
        """Cancel all tasks identified by `ids`."""
        # NOTE: completion_time is set so the task is terminal - otherwise it reads as
        # "interrupted" (scheduled but incomplete) and gets reverted/re-run on restart.
        now = datetime.now().astimezone()
        cls.update_all([
            {
                'id': id,
                'schedule_time': now,
                'completion_time': now,
                'exit_status': CANCEL_STATUS,
             }
            for id in ids
        ])
        for id in ids:
            log.trace(f'Cancelled task ({id})')

    @classmethod
    def cancel(cls: Type[Task], id: str) -> None:
        """Cancel single task by `id`."""
        cls.cancel_all([id, ])

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
        """Select tasks that were orphaned from an evicted client."""
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
        """Revert orphaned tasks from an evicted client to unscheduled state."""
        # A pass that reverts nothing ends the loop, so a row left in flight cannot be selected forever.
        while tasks := cls.select_orphaned(client_id, 100):
            if not cls.revert_all([task.id for task in tasks]):
                break

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
    def effective_rate_by_client(cls: Type[Task]) -> Optional[Dict[str, float]]:
        """Effective completion rate in tasks per second by client."""
        if server_id := cls.latest_server():
            return {
                id: 1 / ((t_max - t_min).total_seconds() / t_n)
                for id, t_max, t_min, t_n in (
                    cls.query(
                        cls.client_id,
                        func.max(cls.completion_time),
                        func.min(cls.start_time),
                        func.count(cls.id)
                    )
                    .join(Client, Task.client_id == Client.id)
                    .filter(cls.server_id == server_id)
                    .filter(cls.completion_time.isnot(None))
                    .filter(Client.disconnected_at == None)  # noqa: comparison to None
                    .group_by(cls.client_id)
                    .all()
            )}
        else:
            return None

    @classmethod
    def effective_rate(cls: Type[Task]) -> Optional[float]:
        """Effective completion rate in tasks per second."""
        if by_client := cls.effective_rate_by_client():
            return sum(by_client.values())
        else:
            return None

    @classmethod
    def avg_duration(cls: Type[Task]) -> Optional[float]:
        """Average task duration by active clients."""
        if server_id := cls.latest_server():
            if duration := (
                cls.query(func.avg(cls.duration))
                .join(Client, Task.client_id == Client.id)
                .filter(cls.server_id == server_id)
                .filter(cls.duration.isnot(None))
                .filter(Client.disconnected_at == None)  # noqa: comparison to None
                .one()[0]
            ):
                return float(duration)
            else:
                return None
        else:
            return None

    @classmethod
    def time_to_completion(cls: Type[Task], group: int = None) -> Optional[float]:
        """Estimated time in seconds until all unscheduled tasks are completed."""
        if rate := cls.effective_rate():
            return cls.count_remaining(group=group) / rate
        else:
            return None

    @classmethod
    def task_pressure(cls: Type[Task], factor: float, group: int = None) -> Optional[float]:
        """Ratio of current ETC to relative `factor` of task duration."""
        if avg_duration := cls.avg_duration():
            if toc := cls.time_to_completion(group=group):
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

    columns = {
        'id': str,
        'host': str,
        'server_id': str,
        'server_host': str,
        'connected_at': datetime,
        'disconnected_at': datetime,
        'evicted': bool,
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
        """Initialize entity from client heartbeat message."""
        return cls.new(id=hb.uuid, host=hb.host, connected_at=hb.time)

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
        """Count active clients."""
        if server_id := Task.latest_server():
            return cls.query().filter_by(server_id=server_id, disconnected_at=None).count()
        else:
            return 0


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
        """
        try:
            return cls.from_id(const_id, caching=False)
        except cls.NotFound:
            source = cls(id=const_id, path=RESERVED_SOURCE_PATHS[const_id],
                         created=datetime.now().astimezone())
            cls.add(source)
            return source

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
