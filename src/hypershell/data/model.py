# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Database models."""


# Type annotations
from __future__ import annotations
from typing import List, Dict, Tuple, Any, Type, TypeVar, Union, Optional

# Standard libs
import re
import json
from uuid import uuid4 as gen_uuid
from datetime import datetime

# External libs
from sqlalchemy import Column, Index, func
from sqlalchemy.orm import Query, DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.types import Integer, DateTime, Text, Boolean, JSON as JSON_TEXT
from sqlalchemy.dialects.postgresql import SMALLINT, UUID as POSTGRES_UUID, JSONB as JSON_BYTES

# Internal libs
from hypershell.core.logging import Logger, HOSTNAME, INSTANCE
from hypershell.core.heartbeat import Heartbeat
from hypershell.core.types import JSONValue
from hypershell.core.tag import Tag
from hypershell.data.core import schema, Session

# Public interface
__all__ = [
    'Task', 'Client', 'Entity', 'to_json_type', 'from_json_type',
    'UUID', 'TEXT', 'INTEGER', 'SMALL_INTEGER', 'DATETIME', 'BOOLEAN', 'JSON',
]

# Initialize logger
log = Logger.with_name(__name__)


class DatabaseError(Exception):
    """Generic database related exception."""


class NotFound(DatabaseError):
    """Exception specific to no record found on lookup by unique field (e.g., `id`)."""


class NotDistinct(DatabaseError):
    """Exception specific to multiple records found when only one should have been."""


class AlreadyExists(DatabaseError):
    """Exception specific to a record with unique properties already existing."""


# Extended value type contains datetime types
# These are not valid JSON and must be converted
VT = TypeVar('VT', bool, int, float, str, type(None), datetime)


def to_json_type(value: VT) -> Union[VT, JSONValue]:
    """Convert `value` to alternate representation for JSON."""
    return value if not isinstance(value, datetime) else value.isoformat(sep=' ')


def from_json_type(value: JSONValue) -> Union[JSONValue, VT]:
    """Convert `value` to richer type if possible."""
    try:
        # NOTE: minor detail in PyPy datetime implementation
        if isinstance(value, str) and len(value) > 5:
            return datetime.fromisoformat(value)
        else:
            return value
    except ValueError:
        return value


# Pre-defining types shortens declarations and makes changes easier
UUID = Text().with_variant(POSTGRES_UUID(as_uuid=False), 'postgresql')
TEXT = Text()
INTEGER = Integer()
SMALL_INTEGER = Integer().with_variant(SMALLINT, 'postgresql')
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

    def to_json(self: Entity) -> Dict[str, JSONValue]:
        """Convert record to JSON-serializable dictionary."""
        return {key: to_json_type(value) for key, value in self.to_dict().items()}

    def pack(self: Entity) -> bytes:
        """Encode as raw JSON bytes."""
        return json.dumps(self.to_json()).encode()

    @classmethod
    def from_dict(cls: Type[Entity], data: Dict[str, VT]) -> Entity:
        """Build from existing dictionary."""
        return cls(**data)  # noqa: __init__ instrumented by declarative_base

    @classmethod
    def from_json(cls: Type[Entity], data: Dict[str, JSONValue]) -> Entity:
        """Build from JSON `text` string."""
        return cls.from_dict({key: from_json_type(value) for key, value in data.items()})

    @classmethod
    def unpack(cls: Type[Entity], data: bytes) -> Entity:
        """Unpack raw JSON byte string."""
        return cls.from_json(json.loads(data.decode()))

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


class Task(Entity):
    """Task entity within database implements task methods."""

    id: Mapped[str] = mapped_column(UUID, primary_key=True, nullable=False)
    args: Mapped[str] = mapped_column(TEXT, nullable=False)

    submit_id: Mapped[str] = mapped_column(UUID, nullable=False)
    submit_time: Mapped[datetime] = mapped_column(DATETIME, nullable=False)
    submit_host: Mapped[Optional[str]] = mapped_column(TEXT, nullable=True)

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

    attempt: Mapped[int] = mapped_column(SMALL_INTEGER, nullable=False)
    retried: Mapped[bool] = mapped_column(BOOLEAN, nullable=False)

    waited: Mapped[Optional[int]] = mapped_column(INTEGER, nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(INTEGER, nullable=True)

    previous_id: Mapped[Optional[str]] = mapped_column(UUID, unique=True, nullable=True)
    next_id: Mapped[Optional[str]] = mapped_column(UUID, unique=True, nullable=True)

    tag: Mapped[dict] = mapped_column(JSON, nullable=False, default={})

    columns = {
        'id': str,
        'args': str,
        'submit_id': str,
        'submit_time': datetime,
        'submit_host': str,
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
    def new(cls: Type[Task], args: str, attempt: int = 1, retried: bool = False,
            tag: Dict[str, JSONValue] = None, **other) -> Task:
        """Create a new Task."""
        cls.ensure_valid_tag(tag)
        args, inline_tags = cls.split_argline(args)
        tag = {**(tag or {}), **inline_tags}
        return Task(id=str(gen_uuid()), args=str(args).strip(),
                    submit_id=INSTANCE, submit_host=HOSTNAME, submit_time=datetime.now().astimezone(),
                    attempt=attempt, retried=retried, tag=tag, **other)

    @classmethod
    def split_argline(cls: Type[Task], args: str) -> Tuple[str, Dict[str, JSONValue]]:
        """Separate input args from possible inline tag comment."""
        if match := re.search(r'#\s*HYPERSHELL:?', args):
            try:
                tags = Tag.parse_cmdline_list(args[match.end():].strip().split())
                cls.ensure_valid_tag(tags)
            except (ValueError, TypeError) as error:
                raise RuntimeError(f'Failed to parse inline tags ({error}, from: "{args}")') from error
            args = args[:match.start()]
            return args, tags
        else:
            return args, {}

    @staticmethod
    def ensure_valid_tag(tag: Optional[Dict[str, JSONValue]]) -> None:
        """Check tag dictionary and raise if invalid."""
        if tag is None:
            return
        if not isinstance(tag, dict):
            raise TypeError('Expected dict for tag data')
        for key, value in tag.items():
            if not isinstance(key, str):
                raise TypeError(f'Tag key, {key} ({type(key)}) is not string')
            if len(key.strip()) == 0:
                raise ValueError(f'Tag key was empty, "{key}:{value}"')
            if len(key.strip()) > 120:
                raise ValueError(f'Tag key size ({len(value)}) exceeds 120 characters ({key}:{value})')
            if not re.match(r'^[A-Za-z0-9_.+-]+$', key):
                raise ValueError(f'Tag key must only contain alphanumerics and basic symbols [+._-]: '
                                 f'"{key}:{value}"')
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise TypeError(f'Invalid type for tag value, {type(value)})')
            if isinstance(value, str):
                if not value.strip():
                    return  # Empty value is a naked tag (no value).
                if len(value) > 120:
                    raise ValueError(f'Tag value size ({len(value)}) exceeds 120 characters ({key}:{value})')
                if not re.match(r'^[A-Za-z0-9_.+-]+$', value):
                    raise ValueError(f'Tag value must only contain alphanumerics and basic symbols [+._-]: '
                                     f'"{key}:{value}"')

    @classmethod
    def select_new(cls: Type[Task], limit: int) -> List[Task]:
        """Select unscheduled tasks up to some `limit` in order of submit_time."""
        return (cls.query()
                .order_by(cls.submit_time)
                .filter(cls.schedule_time.is_(None))
                .limit(limit).all())

    @classmethod
    def select_failed(cls: Type[Task], attempts: int, limit: int) -> List[Task]:
        """Select failed tasks for retry up to some `limit` under given number of `attempts`."""
        return (cls.query()
                .order_by(cls.completion_time)
                .filter(cls.exit_status.isnot(None))
                .filter(cls.exit_status != 0)
                .filter(cls.attempt < attempts)
                .filter(cls.retried.is_(False))
                .limit(limit).all())

    @classmethod
    def next(cls: Type[Task], limit: int, attempts: int = 1, eager: bool = False) -> List[Task]:
        """Select tasks for scheduling including failed tasks for re-scheduling."""
        if eager:
            tasks = cls.__next_eager(attempts=attempts, limit=limit)
        else:
            tasks = cls.__next_not_eager(attempts, limit)
        for task in tasks:
            task.server_id = INSTANCE
            task.server_host = HOSTNAME
            task.schedule_time = datetime.now().astimezone()
        Session.commit()
        return tasks

    @classmethod
    def __next_eager(cls: Type[Task], attempts: int, limit: int) -> List[Task]:
        """Select next batch of tasks from database preferring previously failed tasks."""
        tasks = cls.__schedule_next_failed_tasks(attempts, limit)
        if len(tasks) < limit:
            new_tasks = cls.select_new(limit=limit - len(tasks))
            tasks.extend(new_tasks)
            log.trace(f'Selected {len(new_tasks)} new tasks')
        return tasks

    @classmethod
    def __next_not_eager(cls: Type[Task], attempts: int, limit: int) -> List[Task]:
        """Select next batch of tasks for database preferring novel tasks to old failed ones."""
        tasks = cls.select_new(limit=limit)
        log.trace(f'Selected {len(tasks)} new tasks')
        if len(tasks) < limit and attempts > 1:
            failed_tasks = cls.__schedule_next_failed_tasks(attempts=attempts, limit=limit - len(tasks))
            tasks.extend(failed_tasks)
        return tasks

    @classmethod
    def __schedule_next_failed_tasks(cls: Type[Task], attempts: int, limit: int) -> List[Task]:
        """Select previously failed tasks for scheduling."""
        tasks = []
        failed_tasks = cls.select_failed(attempts=attempts, limit=limit)
        if failed_tasks:
            log.trace(f'Selected {len(failed_tasks)} previously failed tasks')
            new_tasks = [cls.new(args=task.args,
                                 attempt=task.attempt + 1,
                                 previous_id=task.id,
                                 tag=task.tag)
                         for task in failed_tasks]
            tasks.extend(new_tasks)
            cls.add_all(tasks)
            cls.update_all([{'id': old_task.id, 'retried': True, 'next_id': new_task.id}
                            for old_task, new_task in zip(failed_tasks, new_tasks)])
        return tasks

    @classmethod
    def count_remaining(cls: Type[Task]) -> int:
        """Count of remaining unfinished tasks."""
        return cls.query().filter(cls.completion_time.is_(None)).count()

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
        """Revert scheduled but incomplete tasks to un-scheduled state."""
        while tasks := cls.select_interrupted(100):
            cls.revert_all([task.id for task in tasks])

    @classmethod
    def cancel_all(cls: Type[Task], ids: List[str]) -> None:
        """Cancel all tasks identified by `ids`."""
        cls.update_all([
            {
                'id': id,
                'schedule_time': datetime.now().astimezone(),
                'exit_status': -1,
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
        """Revert orphaned tasks from an evicted client to un-scheduled state."""
        while tasks := cls.select_orphaned(client_id, 100):
            cls.revert_all([task.id for task in tasks])

    @classmethod
    def latest_server(cls: Type[Client]) -> Optional[str]:
        """Unique ID of most recent active server (if reusing database)."""
        if records := (
                cls.query(cls.server_id)
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
            return {id: 1 / dt.total_seconds() for id, dt in (
                cls.query(
                    cls.client_id,
                    (func.max(cls.completion_time) - func.min(cls.start_time)) / func.count(cls.id)
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
    def time_to_completion(cls: Type[Task]) -> Optional[float]:
        """Estimated time in seconds until all unscheduled tasks are completed."""
        if rate := cls.effective_rate():
            return cls.count_remaining() / rate
        else:
            return None

    @classmethod
    def task_pressure(cls: Type[Task], factor: float) -> Optional[float]:
        """Ratio of current ETC to relative `factor` of task duration."""
        if avg_duration := cls.avg_duration():
            if toc := cls.time_to_completion():
                return toc / (factor * avg_duration)
            else:
                return None
        else:
            return None


# Indices for efficient queries
index_scheduled = Index('task_scheduled_index', Task.schedule_time)
index_retried = Index('task_retries_index', Task.exit_status, Task.retried)


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
        return cls(id=(id or str(gen_uuid())), host=host,
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
