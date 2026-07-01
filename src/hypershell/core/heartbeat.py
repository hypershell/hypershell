# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Heartbeat data passed between client and server."""


# Type annotations
from __future__ import annotations
from typing import Type, Optional

# Standard libs
import json
from enum import Enum
from datetime import datetime
from dataclasses import dataclass

# Internal libs
from hypershell.core.logging import HOSTNAME, INSTANCE
from hypershell.core.types import serialize, deserialize

# Public interface
__all__ = ['ClientState', 'Heartbeat']


class ClientState(Enum):
    """Client state."""

    RUNNING = 0
    FINISHED = 1
    ERROR = 2

    @classmethod
    def from_value(cls: Type[ClientState], value: int) -> ClientState:
        """Instance from associated integer value."""
        return cls(value)

    @classmethod
    def default(cls: Type[ClientState], state: Optional[ClientState]) -> ClientState:
        """Default instance of ClientState is RUNNING."""
        return state or cls.RUNNING


@dataclass
class Heartbeat:
    """Momentary notice of a client's active status."""

    uuid: str
    host: str
    time: datetime
    state: ClientState

    @classmethod
    def new(cls: Type[Heartbeat],
            uuid: Optional[str] = None,
            host: Optional[str] = None,
            time: Optional[datetime] = None,
            state: Optional[ClientState] = None) -> Heartbeat:
        """Create new instance."""
        return cls(uuid=(uuid or INSTANCE),
                   host=(host or HOSTNAME),
                   time=(time or datetime.now().astimezone()),
                   state=ClientState.default(state))

    def pack(self: Heartbeat) -> bytes:
        """Serialize data."""
        return serialize({'uuid': self.uuid,
                          'host': self.host,
                          'time': str(self.time),
                          'state': self.state.value})

    @classmethod
    def unpack_or_none(cls: Type[Heartbeat], data: bytes) -> Optional[Heartbeat]:
        """Deserialize from raw `data`."""
        unpacked_data = deserialize(data)
        if unpacked_data is None:
            return None
        else:
            return cls(uuid=unpacked_data['uuid'],
                       host=unpacked_data['host'],
                       time=datetime.fromisoformat(unpacked_data['time']),
                       state=ClientState.from_value(unpacked_data['state']))
