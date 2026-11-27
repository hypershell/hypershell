# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Autoscaling demand: how many clients each zone should have.

Demand is computed per zone from readings of the database scoped to the current task group,
under one set of sizing knobs applied to every zone alike. :func:`read_zone_loads` and
:func:`scheduler_group`, which names that group, are the only functions here that read the
database; :func:`zone_demand` and :func:`fleet_demand` are pure and take the number of clients
already launched in each zone as an input, so the caller decides how that number is known. Nothing here launches or terminates a client: that is the actuator's job.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Mapping, Optional, Type

# Standard libs
from enum import Enum
from dataclasses import dataclass, field

# Internal libs
from hypershell.core.logging import Logger
from hypershell.data.model import Task, Zone, zone_id, DEFAULT_ZONE_ID, DEFAULT_ZONE_NAME

# Public interface
__all__ = ['AutoScalerPolicy', 'ZoneRef', 'ScalingKnobs', 'ZoneLoad',
           'scheduler_group', 'read_zone_loads', 'zone_demand', 'fleet_demand']

# Initialize logger
log = Logger.with_name('hypershell.cluster')


class AutoScalerPolicy(Enum):
    """Allowed scaling policies."""
    FIXED = 1
    DYNAMIC = 2

    @classmethod
    def from_name(cls: Type[AutoScalerPolicy], name: str) -> AutoScalerPolicy:
        """Return decided enum type from name."""
        try:
            return cls[name.upper()]
        except KeyError:
            raise RuntimeError(f'Unknown {cls.__name__} \'{name}\'')


@dataclass(frozen=True)
class ZoneRef:
    """A zone as both demand and actuation need it: its id for the database, its name for the launcher."""

    id: str
    name: str

    @classmethod
    def from_name(cls: Type[ZoneRef], name: Optional[str] = None) -> ZoneRef:
        """The zone called `name`; None, '' and the reserved name give the default zone."""
        id = zone_id(name)
        return cls(id, DEFAULT_ZONE_NAME if id == DEFAULT_ZONE_ID else name)

    @property
    def is_default(self: ZoneRef) -> bool:
        """True for the default zone."""
        return self.id == DEFAULT_ZONE_ID


@dataclass(frozen=True)
class ScalingKnobs:
    """The sizing knobs, applied with the same values to each zone independently."""

    policy: AutoScalerPolicy
    factor: float
    init_size: int
    min_size: int
    max_size: int


@dataclass(frozen=True)
class ZoneLoad:
    """One zone's reading at one check, scoped to the current task group."""

    zone: ZoneRef
    remaining: int
    retryable: int
    pressure: Optional[float]
    group: Optional[int] = field(default=None, compare=False)  # NOTE: the group read, for logging

    @property
    def work(self: ZoneLoad) -> int:
        """Tasks the zone still has to run: incomplete tasks plus failures awaiting a retry."""
        return self.remaining + self.retryable


def scheduler_group(reported: Optional[int], *, attempts: int, eager: bool) -> int:
    """
    The task group the scheduler loads from: `reported` when the scheduler reports one, else the
    group it would start from under the server's `attempts` and `eager` retry rule.

    The start rule is :meth:`~hypershell.data.model.Task.starting_group` from the most recently
    scheduled group, or that group itself when nothing is left to start from. This is the rule
    :meth:`~hypershell.cluster.remote.AutoScaler.current_group` applies, and a test pins the two equal.
    """
    if reported is not None:
        return reported
    recent = Task.current_group().value
    group = Task.starting_group(recent, attempts=attempts, eager=eager)
    return recent if group is None else group


def read_zone_loads(group: int, knobs: ScalingKnobs, *, own: Optional[ZoneRef], discover: bool,
                    attempts: int, eager: bool, own_idle: bool = False) -> Dict[str, ZoneLoad]:
    """
    Read the load of each zone to scale, keyed by zone id, with `group` the scheduler's current group.

    With `discover`, every zone with work in the group the scheduler runs next is read: `group`
    while it holds incomplete tasks or failures awaiting a retry (under the server's `attempts` and
    `eager`), else the next group with incomplete tasks. Only a client's request moves the scheduler
    on, so a zone with no client would otherwise never see its group start. A zone whose work is
    all in later groups has none in that group and is not read. The `own` zone is always read when
    given: in `group`, or, when it is `own_idle` (no client of it launched or registered), in that
    same next group, since no client of it is left to ask. A discovered zone id with no recorded
    name cannot be handed to a launcher and is skipped with a warning. Failures awaiting a retry
    count for named zones only; the default zone's retries are scheduled whether or not a client
    of it is asking. Pressure is read only under the dynamic policy.
    """
    own_idle = own is not None and own_idle
    ahead = Task.increment_group(group, attempts=attempts, eager=eager).value if discover or own_idle else group
    own_group = ahead if own_idle else group
    remaining = Task.count_remaining_by_zone(ahead) if discover else {}
    retryable = {zone: count for zone, count in Task.count_retryable_by_zone(attempts, ahead, eager=eager).items()
                 if zone != DEFAULT_ZONE_ID and (discover or (own is not None and zone == own.id))}
    if own is not None and own_group != ahead:
        # The own zone, with a client of it, is read in the current group, where it has no work left.
        remaining.pop(own.id, None)
        retryable.pop(own.id, None)
    zones: Dict[str, ZoneRef] = {} if own is None else {own.id: own}
    found = sorted(zone for zone in {*remaining, *retryable} if zone not in zones)
    names = Zone.names_for_ids([zone for zone in found if zone != DEFAULT_ZONE_ID])
    names[DEFAULT_ZONE_ID] = DEFAULT_ZONE_NAME
    if unnamed := [zone for zone in found if zone not in names]:
        log.warning(f'Autoscale skipping zones with no recorded name ({", ".join(unnamed)})')
    zones.update({zone: ZoneRef(zone, names[zone]) for zone in found if zone in names})
    groups = {zone: own_group if own is not None and zone == own.id else ahead for zone in zones}
    return {
        zone: ZoneLoad(ref,
                       remaining=(remaining.get(zone, 0) if discover else
                                  Task.count_remaining(group=groups[zone], zone=zone)),
                       retryable=retryable.get(zone, 0),
                       pressure=(Task.task_pressure(knobs.factor, group=groups[zone], zone=zone)
                                 if knobs.policy is AutoScalerPolicy.DYNAMIC else None),
                       group=groups[zone])
        for zone, ref in zones.items()
    }


def zone_demand(load: ZoneLoad, launched: int, knobs: ScalingKnobs, *, own: bool, initial: bool) -> int:
    """
    The number of clients one zone should have, given `launched` clients in it.

    Never below `launched`: clients leave by exiting when idle, never by demand. Outside the
    `initial` launch it is at most one more than `launched`. Only the `own` zone has an initial
    and a minimum size; every other zone is sized from its work alone.
    """
    if initial:
        return max(launched, knobs.init_size) if own else launched
    if own and launched < knobs.min_size:
        return launched + 1
    if knobs.policy is AutoScalerPolicy.DYNAMIC and load.pressure is not None:
        return launched + 1 if load.pressure > 1 and launched < knobs.max_size else launched
    return 1 if launched == 0 and load.work > 0 else launched


def fleet_demand(loads: Mapping[str, ZoneLoad], launched: Mapping[str, int], knobs: ScalingKnobs, *,
                 own: Optional[ZoneRef], initial: bool) -> Dict[ZoneRef, int]:
    """
    The number of clients each zone in `loads` should have, given `launched` clients per zone id.

    The `own` zone is always included when given, read as having no work if `loads` lacks it.
    """
    if own is not None and own.id not in loads:
        loads = {own.id: ZoneLoad(own, remaining=0, retryable=0, pressure=None), **loads}
    return {load.zone: zone_demand(load, launched.get(zone, 0), knobs,
                                   own=own is not None and zone == own.id, initial=initial)
            for zone, load in loads.items()}
