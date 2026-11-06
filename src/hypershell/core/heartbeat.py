# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Heartbeat data passed between client and server.

A relay's heartbeat may also carry its `site` and its `roster`, the site clients connected to it.
Both are packed only when set and read as optional, so a heartbeat without them (from an earlier
release, or from an ordinary client) is still valid, and a server that does not know them ignores
them.
"""


# Type annotations
from __future__ import annotations
from typing import Type, Optional, List, Dict, Any

# Standard libs
import json
from enum import Enum
from datetime import datetime
from dataclasses import dataclass

# Internal libs
from hypershell.core.logging import HOSTNAME, INSTANCE
from hypershell.core.types import serialize, deserialize

# Public interface
__all__ = ['ClientState', 'Heartbeat', 'RosterEntry']


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
class RosterEntry:
    """A site client as its relay lists it upstream: its id, host and state, nothing more."""

    uuid: str
    host: str
    state: ClientState = ClientState.RUNNING

    def to_dict(self: RosterEntry) -> Dict[str, Any]:
        """Plain data for packing."""
        return {'uuid': self.uuid, 'host': self.host, 'state': self.state.value}

    @classmethod
    def from_dict(cls: Type[RosterEntry], data: Dict[str, Any]) -> RosterEntry:
        """Instance from unpacked data."""
        return cls(uuid=str(data['uuid']), host=str(data['host']),
                   state=ClientState.from_value(data.get('state', ClientState.RUNNING.value)))


@dataclass
class Heartbeat:
    """Momentary notice of a client's active status (with a relay's site and roster, if any)."""

    uuid: str
    host: str
    time: datetime
    state: ClientState
    site: Optional[str] = None
    roster: Optional[List[RosterEntry]] = None

    @classmethod
    def new(cls: Type[Heartbeat],
            uuid: Optional[str] = None,
            host: Optional[str] = None,
            time: Optional[datetime] = None,
            state: Optional[ClientState] = None,
            site: Optional[str] = None,
            roster: Optional[List[RosterEntry]] = None) -> Heartbeat:
        """Create new instance."""
        return cls(uuid=(uuid or INSTANCE),
                   host=(host or HOSTNAME),
                   time=(time or datetime.now().astimezone()),
                   state=ClientState.default(state),
                   site=site,
                   roster=roster)

    def pack(self: Heartbeat) -> bytes:
        """Serialize data (site and roster only when set)."""
        data = {'uuid': self.uuid,
                'host': self.host,
                'time': str(self.time),
                'state': self.state.value}
        if self.site is not None:
            data['site'] = self.site
        if self.roster is not None:
            data['roster'] = [entry.to_dict() for entry in self.roster]
        return serialize(data)

    @classmethod
    def unpack_or_none(cls: Type[Heartbeat], data: bytes) -> Optional[Heartbeat]:
        """Deserialize from raw `data`."""
        unpacked_data = deserialize(data)
        if unpacked_data is None:
            return None
        else:
            roster = unpacked_data.get('roster')
            return cls(uuid=unpacked_data['uuid'],
                       host=unpacked_data['host'],
                       time=datetime.fromisoformat(unpacked_data['time']),
                       state=ClientState.from_value(unpacked_data['state']),
                       site=unpacked_data.get('site'),
                       roster=None if roster is None else [RosterEntry.from_dict(entry) for entry in roster])
