# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The relay's site: its local server and the fleet of clients it keeps launched there.

The site is a stock autoscaling cluster with a fixed policy, so it mints a key for this run only,
launches the site clients with it, and launches another whenever one exits. It schedules nothing of
its own (no source; its server runs forever on the relay database) and never retries: the upstream
server alone retries tasks, so a failure at the site is a completed row like any other.

Example:
    >>> from hypershell.relay.site import site_cluster
    >>> cluster = site_cluster(address=('0.0.0.0', 50002), size=4, launcher='srun')
"""


# Type annotations
from __future__ import annotations
from typing import List, Optional, Tuple, Final

# Internal libs
from hypershell.core.config import config
from hypershell.core.tls import TLSConfig
from hypershell.cluster.remote import AutoScalingCluster

# Public interface
__all__ = ['site_cluster', 'SITE_POLICY', 'DEFAULT_SITE_PERIOD']


SITE_POLICY: Final[str] = 'fixed'
"""Autoscaling policy of the site: keep exactly the configured number of clients launched."""

DEFAULT_SITE_PERIOD: Final[int] = config.autoscale.period
"""Default seconds between checks on the site clients (one that exited is replaced at the next)."""


def site_cluster(address: Tuple[str, int],
                 bundlesize: int = 1,
                 bundlewait: int = 5,
                 poll: int = 1,
                 tls: Optional[TLSConfig] = None,
                 zone: Optional[str] = None,
                 size: int = 1,
                 launcher: Optional[str] = None,
                 launcher_args: Optional[List[str]] = None,
                 num_threads: int = 1,
                 template: str = '{}',
                 client_timeout: Optional[int] = None,
                 period: int = DEFAULT_SITE_PERIOD) -> AutoScalingCluster:
    """The relay's site cluster: a forever server on the relay database and `size` site clients.

    Site clients are launched with `launcher` (empty runs them directly on this host) and dial this
    host by name, so the relay must be reachable from wherever they run. They serve `zone` (None is
    the default zone).
    """
    return AutoScalingCluster(source=None, num_threads=num_threads, template=template,
                              bundlesize=bundlesize, bundlewait=bundlewait, bind=address,
                              launcher=launcher or '', launcher_args=launcher_args or None,
                              max_retries=0, poll=poll, client_timeout=client_timeout,
                              policy=SITE_POLICY, period=period,
                              init_size=size, min_size=size, max_size=size,
                              tls=tls, zone=zone)
