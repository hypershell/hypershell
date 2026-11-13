# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Site topology: what relays advertise upstream and what a server autoscaling with sites publishes.

A relay serves one or more zones at its site, and holds one upstream identity per zone. Each of
those identities advertises its zone's client bounds and its launched client count; the server
records the latest advertisement of each identity in a :class:`SiteRegistry`. A server
autoscaling with sites plans a client target for each identity from those records and publishes
them as a versioned :class:`Topology`, which relays fetch as JSON text.

When two relay processes advertise one site, the newer holds it and every identity of the older one
is retired: its reply carries a mark, and a relay that reads it hands back the tasks no site client
has received with one call, ``handback``, also as JSON text.

Everything here is plain data with no database and no network: the registry is in memory and
lock-guarded, because the heartbeat thread writes it while the publisher reads it.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, FrozenSet, Iterable, List, Mapping, Optional, Tuple, Type

# Standard libs
import json
import threading
from uuid import UUID
from datetime import datetime
from itertools import count
from dataclasses import dataclass

# Public interface
__all__ = ['Bounds', 'Entry', 'Topology', 'TopologyReply', 'SiteRecord', 'SiteRegistry',
           'TOPOLOGY_PROTOCOL', 'MAX_TOPOLOGY_REQUEST', 'request_text', 'read_request',
           'reply_text', 'error_text', 'read_topology',
           'MAX_HANDBACK_TASKS', 'MAX_HANDBACK_REQUEST', 'handback_text', 'read_handback',
           'handback_reply', 'read_handback_reply']


# The version of the topology request and reply, independent of the work-request protocol.
TOPOLOGY_PROTOCOL: Final[int] = 1

# Longest topology request (in characters) the server reads.
MAX_TOPOLOGY_REQUEST: Final[int] = 16384

# Most tasks one hand-back names; a relay holding more sends several (the upstream bundle bound).
MAX_HANDBACK_TASKS: Final[int] = 1000

# Longest field of a hand-back: an identity, a task id or a schedule time (in characters).
MAX_HANDBACK_FIELD: Final[int] = 64

# Longest hand-back request (in characters) the server reads: room for its most tasks at their longest.
MAX_HANDBACK_REQUEST: Final[int] = 256 * 1024


def _whole(value: Any, name: str) -> int:
    """A whole number given as an int (never a bool), else ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be a whole number (given {value!r})')
    return value


@dataclass(frozen=True)
class Bounds:
    """The client bounds of one zone at one site: never fewer than `min`, `init` at start, never more than `max`."""

    min: int
    max: int
    init: int

    def __post_init__(self: Bounds) -> None:
        """Refuse bounds that are not whole numbers with 0 <= min <= init <= max and max >= 1."""
        for name in ('min', 'max', 'init'):
            _whole(getattr(self, name), name)
        if not 0 <= self.min <= self.init <= self.max:
            raise ValueError(f'Bounds must satisfy 0 <= min <= init <= max '
                             f'(given min={self.min}, init={self.init}, max={self.max})')
        if self.max < 1:
            raise ValueError(f'Bounds must allow at least one client (given max={self.max})')

    def to_dict(self: Bounds) -> Dict[str, int]:
        """Plain data for packing."""
        return {'min': self.min, 'max': self.max, 'init': self.init}

    @classmethod
    def from_dict(cls: Type[Bounds], data: Mapping[str, Any]) -> Bounds:
        """Instance from unpacked data (ValueError when it is not valid bounds)."""
        if not isinstance(data, Mapping):
            raise ValueError(f'Bounds must be a mapping (given {data!r})')
        try:
            return cls(min=data['min'], max=data['max'], init=data['init'])
        except KeyError as error:
            raise ValueError(f'Bounds missing {error}') from error


@dataclass(frozen=True)
class Entry:
    """
    The client target of one relay identity's zone.

    `seen` is the identity's own launched count the server planned from, so a relay can tell the
    clients the server asks for from the clients it already counted.
    """

    zone: str
    target: int
    seen: int


@dataclass(frozen=True)
class Topology:
    """
    A published plan: its `version` (``"<server instance>:<n>"``) and an entry per relay identity.

    `checks` counts the checks the publisher has finished, this one included; it advances at every
    check, under the same version when the plan did not change, so a relay can tell a server that
    planned again and found nothing to change from one that has not planned since.

    `retired` holds the upstream identities of every relay instance a newer one at its site has
    superseded. Like `checks` it is carried on every check and is no part of the plan, so it never
    makes a new version by itself.
    """

    version: str
    entries: Mapping[str, Entry]
    checks: int = 0
    retired: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class TopologyReply:
    """
    A topology as one relay reads it: the version, the entries of its own zones by zone name, and the checks.

    `retired` is true when the server names some identity of this relay as superseded by a newer
    relay at its site.
    """

    version: str
    zones: Mapping[str, Entry]
    checks: int = 0
    retired: bool = False

    def assignment(self: TopologyReply, zone: str) -> Entry:
        """The entry of `zone`; a zone the topology does not name is assigned no clients."""
        return self.zones.get(zone) or Entry(zone, 0, 0)


@dataclass(frozen=True)
class SiteRecord:
    """
    The latest advertisement of one relay identity, as the server recorded it.

    `registered` orders identities by first sight on this server, `rank` orders site names by first
    sight, and `init_held` stays true until the identity has reported `bounds.init` clients.
    `instance` is the key every identity of one relay process advertises (None from a relay that
    advertises none), so the identities of one relay can be told from another's at the same site.
    """

    identity: str
    site: str
    zone: str
    bounds: Bounds
    clients: int
    host: str
    registered: int
    rank: int
    init_held: bool
    instance: Optional[str] = None


class SiteRegistry:
    """
    The advertisements of live relay identities, one record per identity.

    Each :meth:`record` overwrites that identity's previous record in place. A new identity is
    stamped with the next registration number and a new site name with the next rank; a site keeps
    its rank after its identities leave, so a site that returns keeps its place in the order.
    """

    def __init__(self: SiteRegistry) -> None:
        """Initialize empty."""
        self._lock = threading.Lock()
        self._records: Dict[str, SiteRecord] = {}
        self._ranks: Dict[str, int] = {}
        self._registered = count()

    def record(self: SiteRegistry, identity: str, *, site: str, zone: str, bounds: Bounds,
               clients: int, host: str, instance: Optional[str] = None) -> SiteRecord:
        """Record the advertisement of `identity`, overwriting its previous one, and return the record."""
        with self._lock:
            rank = self._ranks.setdefault(site, len(self._ranks))
            previous = self._records.get(identity)
            if previous is None:
                held = True
                registered = next(self._registered)
            else:
                held = previous.init_held
                registered = previous.registered
            record = SiteRecord(identity=identity, site=site, zone=zone, bounds=bounds, clients=clients,
                                host=host, registered=registered, rank=rank,
                                init_held=held and clients < bounds.init, instance=instance)
            self._records[identity] = record
            return record

    def drop(self: SiteRegistry, identity: str) -> Optional[SiteRecord]:
        """Forget `identity` (evicted or finished); return its last record, if any."""
        with self._lock:
            return self._records.pop(identity, None)

    def snapshot(self: SiteRegistry) -> List[SiteRecord]:
        """Every live record, in registration order."""
        with self._lock:
            return sorted(self._records.values(), key=lambda record: record.registered)

    def __contains__(self: SiteRegistry, identity: str) -> bool:
        """True while `identity` has a record."""
        with self._lock:
            return identity in self._records

    def __len__(self: SiteRegistry) -> int:
        """The number of live records."""
        with self._lock:
            return len(self._records)


def request_text(site: str, identities: Iterable[str]) -> str:
    """The topology request of a relay at `site`, for its upstream `identities`."""
    return json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'site': site, 'identities': list(identities)})


def read_request(text: str) -> Tuple[str, List[str]]:
    """The site and identities of a topology request (ValueError when it cannot be read)."""
    if not isinstance(text, str) or len(text) > MAX_TOPOLOGY_REQUEST:
        raise ValueError('Topology request is not text or is too long')
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'Topology request is not JSON ({error})') from error
    if not isinstance(data, dict):
        raise ValueError('Topology request is not an object')
    if data.get('protocol') != TOPOLOGY_PROTOCOL or isinstance(data.get('protocol'), bool):
        raise ValueError(f'Topology protocol {data.get("protocol")!r} is not supported '
                         f'(this server speaks protocol {TOPOLOGY_PROTOCOL})')
    site, identities = data.get('site'), data.get('identities')
    if not isinstance(site, str):
        raise ValueError('Topology request names no site')
    if not isinstance(identities, list) or not all(isinstance(identity, str) for identity in identities):
        raise ValueError('Topology request identities must be a list of text')
    return site, identities


def reply_text(topology: Optional[Topology], identities: Iterable[str]) -> str:
    """
    The reply to a topology request for `identities`: their entries by zone name.

    With nothing published the version is null, which a relay reads as "no topology". An identity
    with no entry is left out, and the relay reads its zone as assigned no clients. The publisher's
    count of finished checks goes with every version. A top-level ``"retired": true`` is added only
    when some identity asked for is retired; every other reply is the same text as without it, and a
    relay of an earlier build ignores the key.
    """
    if topology is None:
        return json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'version': None})
    identities = list(identities)
    zones = {entry.zone: {'target': entry.target, 'seen': entry.seen}
             for entry in (topology.entries.get(identity) for identity in identities) if entry is not None}
    data = {'protocol': TOPOLOGY_PROTOCOL, 'version': topology.version, 'checks': topology.checks, 'zones': zones}
    if not topology.retired.isdisjoint(identities):
        data['retired'] = True
    return json.dumps(data)


def error_text(message: str) -> str:
    """The reply to a topology request the server cannot read."""
    return json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'error': 'request', 'message': message})


def read_topology(text: str) -> Optional[TopologyReply]:
    """
    The topology in a reply, or None when the server publishes none.

    ValueError when the reply cannot be read or is the server's refusal of the request.
    """
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f'Topology reply is not JSON ({error})') from error
    if not isinstance(data, dict) or data.get('protocol') != TOPOLOGY_PROTOCOL:
        raise ValueError(f'Topology reply is not protocol {TOPOLOGY_PROTOCOL}')
    if 'error' in data:
        raise ValueError(f'Topology request refused ({data.get("message")})')
    version = data.get('version')
    if version is None:
        return None
    zones, checks = data.get('zones'), data.get('checks')
    if not isinstance(version, str) or not isinstance(zones, dict):
        raise ValueError('Topology reply has no version or zones')
    if isinstance(checks, bool) or not isinstance(checks, int) or checks < 0:
        raise ValueError(f'Topology reply checks must be a whole number (given {checks!r})')
    try:
        return TopologyReply(version, {zone: Entry(zone, _whole(entry['target'], 'target'),
                                                   _whole(entry['seen'], 'seen'))
                                       for zone, entry in zones.items()}, checks,
                             retired=data.get('retired') is True)
    except (KeyError, TypeError) as error:
        raise ValueError(f'Topology reply entry is malformed ({error})') from error


def handback_text(identity: str, tasks: Iterable[Tuple[str, str]]) -> str:
    """
    The hand-back request of a retired relay's `identity`, naming each task with the schedule time it was sent under.

    Each schedule time is the text the task arrived with, unchanged. At most `MAX_HANDBACK_TASKS`
    tasks go in one request (ValueError for more); a relay holding more sends several.
    """
    tasks = [{'id': task_id, 'schedule_time': schedule_time} for task_id, schedule_time in tasks]
    if len(tasks) > MAX_HANDBACK_TASKS:
        raise ValueError(f'Hand-back names {len(tasks)} tasks (at most {MAX_HANDBACK_TASKS} in one request)')
    return json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'client': identity, 'tasks': tasks})


def _uuid_text(value: Any, name: str) -> str:
    """A UUID given as text, in its canonical form, else ValueError."""
    if not isinstance(value, str) or not value or len(value) > MAX_HANDBACK_FIELD:
        raise ValueError(f'Hand-back {name} must be text of at most {MAX_HANDBACK_FIELD} characters')
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError(f'Hand-back {name} is not a UUID ({value!r})') from error


def _stamp_text(value: Any) -> str:
    """A schedule time given as ISO 8601 text, unchanged, else ValueError."""
    if not isinstance(value, str) or not value or len(value) > MAX_HANDBACK_FIELD:
        raise ValueError(f'Hand-back schedule_time must be text of at most {MAX_HANDBACK_FIELD} characters')
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'Hand-back schedule_time is not a time ({value!r})') from error
    return value


def read_handback(text: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    The identity of a hand-back request and its (task id, schedule time) pairs, in order.

    ValueError when it cannot be read: not text, too long, not JSON, another protocol, an identity
    or a task id that is not a UUID, a schedule time that is not ISO 8601 text, more than
    `MAX_HANDBACK_TASKS` tasks, or a task named twice.
    """
    if not isinstance(text, str) or len(text) > MAX_HANDBACK_REQUEST:
        raise ValueError('Hand-back request is not text or is too long')
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'Hand-back request is not JSON ({error})') from error
    if not isinstance(data, dict):
        raise ValueError('Hand-back request is not an object')
    if data.get('protocol') != TOPOLOGY_PROTOCOL or isinstance(data.get('protocol'), bool):
        raise ValueError(f'Hand-back protocol {data.get("protocol")!r} is not supported '
                         f'(this server speaks protocol {TOPOLOGY_PROTOCOL})')
    identity = _uuid_text(data.get('client'), 'client')
    tasks = data.get('tasks')
    if not isinstance(tasks, list):
        raise ValueError('Hand-back tasks must be a list')
    if len(tasks) > MAX_HANDBACK_TASKS:
        raise ValueError(f'Hand-back names {len(tasks)} tasks (at most {MAX_HANDBACK_TASKS} in one request)')
    pairs: List[Tuple[str, str]] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('Hand-back task must be an object')
        pairs.append((_uuid_text(task.get('id'), 'task id'), _stamp_text(task.get('schedule_time'))))
    if len({task_id for task_id, _ in pairs}) < len(pairs):
        raise ValueError('Hand-back names a task more than once')
    return identity, pairs


def handback_reply(accepted: int) -> str:
    """The reply to a hand-back request: how many of its tasks the server took back."""
    return json.dumps({'protocol': TOPOLOGY_PROTOCOL, 'accepted': accepted})


def read_handback_reply(text: str) -> int:
    """
    The count of tasks the server took back.

    ValueError when the reply cannot be read or is the server's refusal of the request.
    """
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f'Hand-back reply is not JSON ({error})') from error
    if not isinstance(data, dict) or data.get('protocol') != TOPOLOGY_PROTOCOL:
        raise ValueError(f'Hand-back reply is not protocol {TOPOLOGY_PROTOCOL}')
    if 'error' in data:
        raise ValueError(f'Hand-back refused ({data.get("message")})')
    accepted = data.get('accepted')
    if isinstance(accepted, bool) or not isinstance(accepted, int) or accepted < 0:
        raise ValueError(f'Hand-back reply accepted must be a whole number (given {accepted!r})')
    return accepted
