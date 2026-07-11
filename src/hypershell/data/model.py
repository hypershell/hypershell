# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database models."""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Tuple, Any, Type, Optional, Final

# Standard libs
import re
import json
from datetime import datetime
from dataclasses import dataclass

# External libs
from sqlalchemy import Column, Index, func
from sqlalchemy.orm import Query, DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.types import Integer, Float, DateTime, Text, Boolean, JSON as JSON_TEXT
from sqlalchemy.dialects.postgresql import SMALLINT, UUID as POSTGRES_UUID, JSONB as JSON_BYTES

# Internal libs
from hypershell.core.logging import Logger, HOSTNAME, INSTANCE
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.types import JSONData, to_json_type, from_json_type, serialize, deserialize, parse_bytes
from hypershell.core.uuid import uuid
from hypershell.core.tag import Tag
from hypershell.data.core import schema, Session

# Public interface
__all__ = [
    'Task', 'Client', 'Entity', 'to_json_type', 'from_json_type', 'serialize_tasks', 'deserialize_tasks',
    'UUID', 'TEXT', 'INTEGER', 'SMALL_INTEGER', 'DATETIME', 'BOOLEAN', 'JSON',
    'DEFAULT_TASK_GROUP', 'CANCEL_STATUS', 'TaskGroupInfo',
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

    previous_id: Mapped[Optional[str]] = mapped_column(UUID, unique=True, nullable=True)
    next_id: Mapped[Optional[str]] = mapped_column(UUID, unique=True, nullable=True)

    tag: Mapped[dict] = mapped_column(JSON, nullable=False, default={})

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
        'tag': dict,
    }

    class NotFound(NotFound):
        pass

    class NotDistinct(NotDistinct):
        pass

    class AlreadyExists(AlreadyExists):
        pass

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
            **other: Any) -> Task:
        """Create a new Task.

        With ``strict_tag=False`` the tag character-set/length checks are relaxed
        (for JSON-sourced tags). With ``parse_inline=False`` the inline
        ``# HYPERSHELL:`` tag comment is not parsed and `args` is taken verbatim.
        """
        cls.ensure_valid_tag(tag, strict=strict_tag)
        if parse_inline:
            args, inline_tags = cls.split_argline(args)
        else:
            args, inline_tags = str(args).strip(), {}
        tag = {**(tag or {}), **inline_tags, **{'part': 0, }}
        other['group'] = tag.pop('group', group)
        other['cores'] = tag.pop('cores', other.get('cores', None))
        # A memory value may arrive as a unit-bearing string ('2GB') from an inline
        # `memory:` tag (parsed by smart_coerce, which leaves units alone) — parse it to
        # integer bytes as the `--memory` flag does, so resource accounting stays numeric.
        memory = tag.pop('memory', other.get('memory', None))
        other['memory'] = parse_bytes(memory) if isinstance(memory, str) else memory
        other['timeout'] = tag.pop('timeout', other.get('timeout', None))
        return Task(id=uuid(), args=args,
                    submit_id=INSTANCE, submit_host=HOSTNAME, submit_time=datetime.now().astimezone(),
                    attempt=attempt, retried=retried, tag=tag, **other)

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
        """Check tag dictionary and raise if invalid.

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
    def increment_group(cls: Type[Task], previous_group: int, attempts: int) -> TaskGroupInfo:
        """
        This should only be called if no tasks are returned by Task.next().
        Check the current group and increment if necessary with explanation.
        We assume all tasks in `previous_group` have already been scheduled (see Task.next()).
        """
        remaining = (
            Session.query(Task)
            .filter(Task.group == previous_group)
            .filter(Task.completion_time.is_(None))
            .count()
        )
        if remaining > 0:
            return TaskGroupInfo(previous_group, f'waiting on {remaining} tasks to complete')
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
        next_group = (
            Session.query(Task.group)
            .order_by(Task.group)
            .filter(Task.group > previous_group)
            .first()
        )
        if not next_group:
            return TaskGroupInfo(previous_group, f'no task groups remain')
        else:
            return TaskGroupInfo(next_group[0])

    @classmethod
    def select_new(cls: Type[Task], limit: int, group: int = None) -> List[Task]:
        """Select unscheduled tasks up to some `limit` in order of submit_time."""
        query = cls.query().order_by(cls.submit_time).filter(cls.schedule_time.is_(None))
        if group is not None:
            query = query.filter(cls.group == group)
        return query.limit(limit).all()

    @classmethod
    def select_failed(cls: Type[Task], attempts: int, limit: int, group: int = None) -> List[Task]:
        """Select failed tasks for retry up to some `limit` under given number of `attempts`."""
        query = (cls.query()
                 .order_by(cls.completion_time)
                 .filter(cls.exit_status.isnot(None))
                 .filter(cls.exit_status != 0)
                 .filter(cls.exit_status != CANCEL_STATUS)  # NOTE: cancelled is terminal, not a failure
                 .filter(cls.attempt < attempts)
                 .filter(cls.retried.is_(False)))
        if group is not None:
            query = query.filter(cls.group == group)
        return query.limit(limit).all()

    @classmethod
    def next(cls: Type[Task],
             limit: int,
             group: int = DEFAULT_TASK_GROUP,
             attempts: int = 1,
             eager: bool = False) -> List[Task]:
        """Select tasks for scheduling including failed tasks for re-scheduling."""
        group = group if group is not None else cls.current_group().value
        if eager:
            tasks = cls.__next_eager(attempts=attempts, limit=limit, group=group)
        else:
            tasks = cls.__next_not_eager(attempts, limit, group=group)
        for task in tasks:
            task.server_id = INSTANCE
            task.server_host = HOSTNAME
            task.schedule_time = datetime.now().astimezone()
        Session.commit()
        return tasks

    @classmethod
    def __next_eager(cls: Type[Task], attempts: int, limit: int, group: int = None) -> List[Task]:
        """Select next batch of tasks from database preferring previously failed tasks."""
        tasks = cls.__schedule_next_failed_tasks(attempts, limit, group=group)
        if len(tasks) < limit:
            new_tasks = cls.select_new(limit=limit - len(tasks), group=group)
            tasks.extend(new_tasks)
            log.trace(f'Selected {len(new_tasks)} new tasks')
        return tasks

    @classmethod
    def __next_not_eager(cls: Type[Task], attempts: int, limit: int, group: int = None) -> List[Task]:
        """Select next batch of tasks for database preferring novel tasks to old failed ones."""
        tasks = cls.select_new(limit=limit, group=group)
        log.trace(f'Selected {len(tasks)} new tasks')
        if len(tasks) < limit and attempts > 1:
            failed_tasks = cls.__schedule_next_failed_tasks(attempts=attempts, limit=limit - len(tasks), group=group)
            tasks.extend(failed_tasks)
        return tasks

    @classmethod
    def __schedule_next_failed_tasks(cls: Type[Task], attempts: int, limit: int, group: int = None) -> List[Task]:
        """Select previously failed tasks for scheduling."""
        tasks = []
        failed_tasks = cls.select_failed(attempts=attempts, limit=limit, group=group)
        if failed_tasks:
            log.trace(f'Selected {len(failed_tasks)} previously failed tasks')
            new_tasks = [cls.new(args=task.args,
                                 attempt=task.attempt + 1,
                                 previous_id=task.id,
                                 tag=task.tag,
                                 group=task.group)
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
    def revert_all(cls: Type[Task], ids: List[str]) -> None:
        """Revert all tasks identified by `ids`."""
        cls.update_all([
            {
                'id': id,
                'schedule_time': None,
                'server_host': None,
                'server_id': None,
                'client_host': None,
                'client_id': None,
                'command': None,
                'start_time': None,
                'completion_time': None,
                'exit_status': None,
                'outpath': None,
                'errpath': None,
                'waited': None,
                'duration': None,
             }
            for id in ids
        ])
        for id in ids:
            log.trace(f'Reverted previous task ({id})')

    @classmethod
    def revert(cls: Type[Task], id: str) -> None:
        """Revert single task by `id`."""
        cls.revert_all([id, ])

    @classmethod
    def revert_interrupted(cls: Type[Task]) -> None:
        """Revert scheduled but incomplete tasks to unscheduled state."""
        while tasks := cls.select_interrupted(100):
            cls.revert_all([task.id for task in tasks])

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
        while tasks := cls.select_orphaned(client_id, 100):
            cls.revert_all([task.id for task in tasks])

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


# Indices for efficient queries
index_tasks_unscheduled = Index('index_tasks_unscheduled', Task.group, Task.schedule_time)
index_tasks_retries = Index('index_tasks_retries', Task.exit_status, Task.retried)


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
