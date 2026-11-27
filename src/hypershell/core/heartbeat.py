# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Heartbeat data passed between client and server.

A relay's heartbeat may also carry its `site` and its `roster`, the site clients connected to it.
Each of a relay's zone identities may also advertise its `zone`, that zone's client `bounds` and
`clients`, the site clients launched in it. A relay a newer relay at its site has superseded says
it is `retiring` on every beat from then on. All of these are packed only when set and read as
optional, so a heartbeat without them (from an earlier release, or from an ordinary client) is
still valid, and a server that does not know them ignores them.
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
from hypershell.core.topology import Bounds

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
    """Momentary notice of a client's active status (with a relay's site, roster and advertisement, if any)."""

    uuid: str
    host: str
    time: datetime
    state: ClientState
    site: Optional[str] = None
    roster: Optional[List[RosterEntry]] = None
    zone: Optional[str] = None
    bounds: Optional[Bounds] = None
    clients: Optional[int] = None
    instance: Optional[str] = None
    retiring: bool = False

    @classmethod
    def new(cls: Type[Heartbeat],
            uuid: Optional[str] = None,
            host: Optional[str] = None,
            time: Optional[datetime] = None,
            state: Optional[ClientState] = None,
            site: Optional[str] = None,
            roster: Optional[List[RosterEntry]] = None,
            zone: Optional[str] = None,
            bounds: Optional[Bounds] = None,
            clients: Optional[int] = None,
            instance: Optional[str] = None,
            retiring: bool = False) -> Heartbeat:
        """Create new instance."""
        return cls(uuid=(uuid or INSTANCE),
                   host=(host or HOSTNAME),
                   time=(time or datetime.now().astimezone()),
                   state=ClientState.default(state),
                   site=site,
                   roster=roster,
                   zone=zone,
                   bounds=bounds,
                   clients=clients,
                   instance=instance,
                   retiring=retiring)

    def pack(self: Heartbeat) -> bytes:
        """Serialize data (each optional field only when set)."""
        data = {'uuid': self.uuid,
                'host': self.host,
                'time': str(self.time),
                'state': self.state.value}
        if self.site is not None:
            data['site'] = self.site
        if self.roster is not None:
            data['roster'] = [entry.to_dict() for entry in self.roster]
        if self.zone is not None:
            data['zone'] = self.zone
        if self.bounds is not None:
            data['bounds'] = self.bounds.to_dict()
        if self.clients is not None:
            data['clients'] = self.clients
        if self.instance is not None:
            data['instance'] = self.instance
        if self.retiring:
            data['retiring'] = True
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
                       roster=None if roster is None else [RosterEntry.from_dict(entry) for entry in roster],
                       zone=_text_or_none(unpacked_data.get('zone')),
                       bounds=_bounds_or_none(unpacked_data.get('bounds')),
                       clients=_count_or_none(unpacked_data.get('clients')),
                       instance=_text_or_none(unpacked_data.get('instance')),
                       retiring=unpacked_data.get('retiring') is True)


# An advertisement field that is not valid is read as absent, so a malformed advertisement makes
# its relay identity a non-participant instead of failing the server's heartbeat thread.

def _text_or_none(value: Any) -> Optional[str]:
    """The value when it is text, else None."""
    return value if isinstance(value, str) else None


def _bounds_or_none(value: Any) -> Optional[Bounds]:
    """The bounds when they are valid, else None."""
    try:
        return None if value is None else Bounds.from_dict(value)
    except ValueError:
        return None


def _count_or_none(value: Any) -> Optional[int]:
    """The value when it is a whole number not below zero, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
