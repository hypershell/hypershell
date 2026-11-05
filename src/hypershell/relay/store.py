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
"""


# Type annotations
from __future__ import annotations
from typing import List, Optional
from datetime import datetime

# External libs
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import Text, DateTime

# Internal libs
from hypershell.data.upgrade import upgrade_schema

# Public interface
__all__ = ['RelayBase', 'RelayTask', 'RelayResult', 'RESULT_PENDING', 'RESULT_RETURNED', 'ensure_tables']


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
