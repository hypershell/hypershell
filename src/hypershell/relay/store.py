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
has committed does the relay confirm them upstream. A task received again (upstream reverted it and
dispatched it anew) drops any result still held for its earlier assignment in the same transaction.

Completed rows leave the task table for the outbox in one transaction (`collect_results`), each as
the record that goes upstream: the leaf's result with the upstream stamp restored. Moving them out
keeps a failed row (the relay-local server never retries) from holding its task group. A result is
marked returned only after its put upstream has returned (`mark_returned`), and returned results are
pruned, with their upstream stamps, after a retention period (`prune_returned`).

The relay's own writers (the puller's insert and the returner's moves, marks and prunes) are
serialised by one lock in this process, so a task received again can never be moved or marked
against a stamp it no longer has. The relay-local server writes only rows that are not complete,
which these writers never touch.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional, Tuple
from datetime import datetime

# Standard libs
import json
import threading

# External libs
from sqlalchemy import MetaData, select, func, and_, exists, delete, update, bindparam
from sqlalchemy.engine import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Text, DateTime

# Internal libs
from hypershell.core.types import JSONData, to_json_type
from hypershell.core.heartbeat import RosterEntry, ClientState
from hypershell.data.model import Task, Client, DEFAULT_ZONE_ID
from hypershell.data.upgrade import upgrade_schema

# Public interface
__all__ = ['RelayBase', 'RelayTask', 'RelayResult', 'RESULT_PENDING', 'RESULT_RETURNED', 'ensure_tables',
           'LOCAL_STAMP_COLUMNS', 'local_row', 'insert_received', 'count_backlog', 'count_connected', 'count_held',
           'roster',
           'result_record', 'collect_results', 'pending_results', 'mark_returned', 'prune_returned',
           'COLLECT_BATCH', 'PRUNE_BATCH']


RESULT_PENDING = 'pending'
"""Outbox state of a result not yet handed over upstream."""

RESULT_RETURNED = 'returned'
"""Outbox state of a result handed over upstream (its put returned), kept until pruned."""

COLLECT_BATCH: int = 1_000
"""Most completed rows moved to the outbox in one transaction."""

PRUNE_BATCH: int = 1_000
"""Most returned results pruned in one transaction."""

_WRITE_LOCK = threading.RLock()
"""Serialises the relay's own writes to its received tasks and outbox (see module notes)."""


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
    upstream stamp, and any result held for its earlier assignment is dropped. Nothing is written
    unless everything is. `identity` is the upstream client id the tasks are confirmed under.
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
    ids = [record['id'] for record in records]
    results = RelayResult.__table__
    with _WRITE_LOCK, engine.begin() as conn:
        conn.execute(task_upsert, tasks)
        conn.execute(stamp_upsert, stamps)
        conn.execute(delete(results).where(results.c.id.in_(ids)))
    return ids


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


def roster(engine: Engine, server_id: str) -> List[RosterEntry]:
    """The clients connected to the relay-local server `server_id`, as the relay lists them upstream.

    Only a client's id and host go upstream (never anything the site was given to connect).
    """
    table = Client.__table__
    query = (select(table.c.id, table.c.host).select_from(table)
             .where(and_(table.c.server_id == server_id, table.c.disconnected_at.is_(None)))
             .order_by(table.c.id))
    with engine.connect() as conn:
        return [RosterEntry(uuid=id, host=host, state=ClientState.RUNNING) for id, host in conn.execute(query)]


def count_held(engine: Engine) -> int:
    """Number of received tasks whose result has not been handed over upstream."""
    stamp, result = RelayTask.__table__, RelayResult.__table__
    returned = exists().where(and_(result.c.id == stamp.c.id, result.c.state == RESULT_RETURNED))
    query = select(func.count()).select_from(stamp).where(~returned)
    with engine.connect() as conn:
        return conn.execute(query).scalar()


def result_record(row: Mapping[str, Any], stamp: Mapping[str, Any]) -> Dict[str, JSONData]:
    """The task record that goes upstream for a completed local `row` and its upstream `stamp`.

    The result columns are the leaf's, as the relay-local server wrote them. The upstream stamp
    (`schedule_time`, `server_id`, `server_host`) replaces the relay-local one, with `schedule_time`
    as the verbatim text it arrived as, so the result matches the assignment it answers exactly.
    Timestamps read from the relay database carry no offset; they are given this host's.
    """
    record = {}
    for name in Task.columns:
        value = row[name]
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.astimezone()
        record[name] = to_json_type(value)
    record['schedule_time'] = stamp['schedule_time']
    record['server_id'] = stamp['server_id']
    record['server_host'] = stamp['server_host']
    return record


def collect_results(engine: Engine, stored_at: Optional[datetime] = None, limit: int = COLLECT_BATCH) -> int:
    """Move completed task rows into the outbox in one transaction; return how many were moved.

    Each row becomes a pending result holding its upstream record (see `result_record`) and leaves the
    task table. A row with no upstream stamp is left where it is.
    """
    table, stamps, results = Task.__table__, RelayTask.__table__, RelayResult.__table__
    query = (select(table, stamps.c.schedule_time.label('upstream_schedule_time'),
                    stamps.c.server_id.label('upstream_server_id'),
                    stamps.c.server_host.label('upstream_server_host'))
             .join(stamps, stamps.c.id == table.c.id)
             .where(table.c.completion_time.isnot(None))
             .limit(limit))
    stored_at = stored_at or datetime.now().astimezone()
    with _WRITE_LOCK, engine.begin() as conn:
        found = conn.execute(query).mappings().all()
        if not found:
            return 0
        held = []
        for row in found:
            stamp = {'schedule_time': row['upstream_schedule_time'], 'server_id': row['upstream_server_id'],
                     'server_host': row['upstream_server_host']}
            held.append({'id': row['id'], 'record': json.dumps(result_record(row, stamp)),
                         'state': RESULT_PENDING, 'stored_at': stored_at, 'returned_at': None})
        insert = sqlite_insert(results)
        upsert = insert.on_conflict_do_update(
            index_elements=['id'], set_={name: insert.excluded[name] for name in held[0] if name != 'id'})
        conn.execute(upsert, held)
        conn.execute(delete(table).where(table.c.id.in_([row['id'] for row in found])))
    return len(found)


def pending_results(engine: Engine, limit: int) -> List[Tuple[str, str]]:
    """Up to `limit` results not yet handed over, oldest first, as (id, record) pairs."""
    results = RelayResult.__table__
    query = (select(results.c.id, results.c.record)
             .where(results.c.state == RESULT_PENDING)
             .order_by(results.c.stored_at, results.c.id)
             .limit(limit))
    with engine.connect() as conn:
        return [(id, record) for id, record in conn.execute(query)]


def mark_returned(engine: Engine, sent: List[Tuple[str, str]], returned_at: Optional[datetime] = None) -> int:
    """Mark the `sent` results (id, record) returned; return how many were marked.

    Only a result still pending with that same record is marked: one dropped or replaced meanwhile
    (its task received again) is not the result that was sent.
    """
    if not sent:
        return 0
    results = RelayResult.__table__
    statement = (update(results)
                 .where(results.c.id == bindparam('r_id'), results.c.record == bindparam('r_record'),
                        results.c.state == RESULT_PENDING)
                 .values(state=RESULT_RETURNED, returned_at=bindparam('r_returned_at')))
    returned_at = returned_at or datetime.now().astimezone()
    marked = 0
    with _WRITE_LOCK, engine.begin() as conn:
        for id, record in sent:
            marked += conn.execute(statement, {'r_id': id, 'r_record': record, 'r_returned_at': returned_at}).rowcount
    return marked


def prune_returned(engine: Engine, before: datetime, batch: int = PRUNE_BATCH) -> int:
    """Delete results returned before `before`, with their upstream stamps; return how many were deleted.

    Pending results are never pruned. Deletes run in batches of `batch`, one transaction each.
    """
    stamps, results = RelayTask.__table__, RelayResult.__table__
    query = (select(results.c.id)
             .where(results.c.state == RESULT_RETURNED, results.c.returned_at < before)
             .limit(batch))
    pruned = 0
    while True:
        with _WRITE_LOCK, engine.begin() as conn:
            ids = conn.execute(query).scalars().all()
            if not ids:
                return pruned
            conn.execute(delete(results).where(results.c.id.in_(ids), results.c.state == RESULT_RETURNED))
            conn.execute(delete(stamps).where(stamps.c.id.in_(ids)))
        pruned += len(ids)
