# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Relay-only tables in the relay database.

These tables live on their own `MetaData`, never on `Entity`'s, because the schema-upgrade step
creates every `Entity` table in every database it touches: an `Entity` subclass here would appear
in every user's primary database. They are created only in a relay process, on the relay database.

`relay_task` keeps the upstream assignment of each task the relay received, exactly as the upstream
server stamped it. The relay-local server re-stamps, reverts and clears the `task` row's own columns,
and SQLite drops a datetime's offset, so the upstream `schedule_time` is kept here as the verbatim
text it arrived as; a result is matched upstream against that exact stamp.

`relay_result` is the outbox: completed local rows, each held as the record that goes upstream,
until it has been handed over.

Received tasks enter the relay database in one transaction (`insert_received`): the task row, as
the relay-local server's own unscheduled task, and its `relay_task` row. Only once that transaction
has committed does the relay confirm them upstream.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime

# External libs
from sqlalchemy import MetaData, select, func, and_, exists
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Text, DateTime

# Internal libs
from hypershell.data.model import Task, Client, DEFAULT_ZONE_ID
from hypershell.data.upgrade import upgrade_schema

# Public interface
__all__ = ['RelayBase', 'RelayTask', 'RelayResult', 'RESULT_PENDING', 'RESULT_RETURNED', 'ensure_tables',
           'LOCAL_STAMP_COLUMNS', 'local_row', 'insert_received', 'count_backlog', 'count_connected', 'count_held']


RESULT_PENDING = 'pending'
"""Outbox state of a result not yet handed over upstream."""

RESULT_RETURNED = 'returned'
"""Outbox state of a result handed over upstream (its put returned), kept until pruned."""


class RelayBase(DeclarativeBase):
    """Declarative base for relay-only tables, on a metadata of their own."""

    metadata = MetaData()


class RelayTask(RelayBase):
    """The upstream assignment of a task the relay received."""

    __tablename__ = 'relay_task'

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    schedule_time: Mapped[str] = mapped_column(Text, nullable=False)  # Verbatim, as received
    server_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    server_host: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    identity: Mapped[str] = mapped_column(Text, nullable=False)  # Upstream client id that confirmed it
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RelayResult(RelayBase):
    """A completed task held for return upstream."""

    __tablename__ = 'relay_result'

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    record: Mapped[str] = mapped_column(Text, nullable=False)  # The task record (JSON) that goes upstream
    state: Mapped[str] = mapped_column(Text, nullable=False, default=RESULT_PENDING)
    stored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


def ensure_tables(engine: Engine) -> List[str]:
    """Create or upgrade the relay-only tables in the relay database; return the changes made."""
    return upgrade_schema(engine, metadata=RelayBase.metadata)


LOCAL_STAMP_COLUMNS = ('schedule_time', 'server_id', 'server_host', 'client_id', 'client_host')
"""Columns cleared on a received task, so the relay-local server schedules it as new."""


def local_row(record: Dict[str, Any]) -> Dict[str, Any]:
    """The relay-local task row for a task `record` as received (decoded JSON).

    Every column is taken from the record, except the scheduling and client columns, which are
    cleared, and the partition index, which is this database's own (main, 0). Timestamps are read as datetimes and given in this host's local time, since SQLite keeps
    no offset; keys that are not task columns are ignored.
    """
    row = {}
    for name, kind in Task.columns.items():
        value = record.get(name)
        if kind is datetime and isinstance(value, str):
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone()
        row[name] = value
    for name in LOCAL_STAMP_COLUMNS:
        row[name] = None
    row['part'] = 0
    return row


def insert_received(engine: Engine, records: List[Dict[str, Any]], identity: str,
                    received_at: Optional[datetime] = None) -> List[str]:
    """Insert received task `records` and their upstream stamps in one transaction; return their ids.

    A task already present (sent again after the upstream server reverted it) is replaced, with its
    upstream stamp. Nothing is written unless everything is. `identity` is the upstream client id the
    tasks are confirmed under.
    """
    if not records:
        return []
    received_at = received_at or datetime.now().astimezone()
    tasks = [local_row(record) for record in records]
    stamps = [{'id': record['id'], 'schedule_time': record['schedule_time'], 'server_id': record.get('server_id'),
               'server_host': record.get('server_host'), 'identity': identity, 'received_at': received_at}
              for record in records]
    task_insert = sqlite_insert(Task.__table__)
    task_upsert = task_insert.on_conflict_do_update(
        index_elements=['id'], set_={name: task_insert.excluded[name] for name in Task.columns if name != 'id'})
    stamp_insert = sqlite_insert(RelayTask.__table__)
    stamp_upsert = stamp_insert.on_conflict_do_update(
        index_elements=['id'], set_={name: stamp_insert.excluded[name] for name in stamps[0] if name != 'id'})
    with engine.begin() as conn:
        conn.execute(task_upsert, tasks)
        conn.execute(stamp_upsert, stamps)
    return [record['id'] for record in records]


def count_backlog(engine: Engine, zone: Optional[str] = None) -> int:
    """Number of tasks in the relay database not yet complete (in one `zone` if given, by id)."""
    table = Task.__table__
    query = select(func.count()).select_from(table).where(table.c.completion_time.is_(None))
    if zone is not None:
        query = query.where(table.c.zone.is_(None) if zone == DEFAULT_ZONE_ID else table.c.zone == zone)
    with engine.connect() as conn:
        return conn.execute(query).scalar()


def count_connected(engine: Engine, server_id: str) -> int:
    """Number of clients connected to the relay-local server `server_id` (its own registrations only)."""
    table = Client.__table__
    query = (select(func.count()).select_from(table)
             .where(and_(table.c.server_id == server_id, table.c.disconnected_at.is_(None))))
    with engine.connect() as conn:
        return conn.execute(query).scalar()


def count_held(engine: Engine) -> int:
    """Number of received tasks whose result has not been handed over upstream."""
    stamp, result = RelayTask.__table__, RelayResult.__table__
    returned = exists().where(and_(result.c.id == stamp.c.id, result.c.state == RESULT_RETURNED))
    query = select(func.count()).select_from(stamp).where(~returned)
    with engine.connect() as conn:
        return conn.execute(query).scalar()
