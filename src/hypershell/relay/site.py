# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The relay's site: its local server and the fleet of clients it keeps launched there.

The site is a stock autoscaling cluster with a fixed policy, so it mints a key for this run only,
launches the site clients with it, and launches another whenever one exits. It schedules nothing of
its own (no source; its server runs forever on the relay database) and never retries: the upstream
server alone retries tasks, so a failure at the site is a completed row like any other.

The upstream key stays with the relay. Site clients inherit the relay's environment when they are
launched, and tasks inherit their client's, so before the first launch `fence_site` refuses a client
invocation or an exported task variable that carries the key, and removes from the relay's own
environment every relay setting and every variable that would hand the key on.

Example:
    >>> from hypershell.relay.site import site_cluster, fence_site
    >>> cluster = site_cluster(address=('0.0.0.0', 50002), size=4, launcher='srun')
    >>> removed = fence_site(cluster, key=upstream_key)
"""


# Type annotations
from __future__ import annotations
from typing import List, Optional, Tuple, Final, Mapping, MutableMapping, Sequence

# Standard libs
import os
import re

# Internal libs
from hypershell.core.config import config, load_task_env
from hypershell.core.logging import Logger
from hypershell.core.tls import TLSConfig
from hypershell.cluster.remote import AutoScalingCluster

# Public interface
__all__ = ['site_cluster', 'SITE_POLICY', 'DEFAULT_SITE_PERIOD', 'fence_site', 'scrub_environment',
           'check_invocation', 'check_exports', 'CredentialFenceError', 'FENCED_PREFIX']

# Initialize logger
log = Logger.with_name(__name__)


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


FENCED_PREFIX: Final[str] = 'HYPERSHELL_RELAY_'
"""Environment variables of relay settings, which never reach the site."""

CONFIG_FILE_VAR: Final[str] = 'HYPERSHELL_CONFIG_FILE'
"""Environment variable naming the one configuration file a process reads."""

RELAY_SECTION: Final[re.Pattern] = re.compile(r'^\s*(\[\s*relay\s*[.\]]|relay\s*\.)', re.MULTILINE)
"""A relay table or dotted relay key in a TOML configuration file."""


class CredentialFenceError(RuntimeError):
    """The upstream key would reach the site's clients or their tasks."""


def names_relay_config(path: str, key: str) -> bool:
    """True if the configuration file at `path` holds relay settings or the upstream key.

    A file that cannot be read is treated as relay configuration (a site client gains nothing by it).
    """
    try:
        with open(os.path.expanduser(path), mode='r', errors='replace') as stream:
            text = stream.read()
    except OSError:
        return True
    return bool(RELAY_SECTION.search(text)) or bool(key and key in text)


def scrub_environment(key: str, environ: Optional[MutableMapping[str, str]] = None) -> List[str]:
    """Remove from `environ` (the process environment by default) what must not reach the site.

    Removed: every relay setting (`HYPERSHELL_RELAY_*`), the configuration file variable when the file
    it names holds relay settings or the key, and any variable whose name or value contains the key.
    Returns the names removed, sorted; values are never returned or logged.
    """
    environ = os.environ if environ is None else environ
    removed = []
    for name, value in list(environ.items()):
        if (name.startswith(FENCED_PREFIX)
                or (key and (key in name or key in value))
                or (name == CONFIG_FILE_VAR and value and names_relay_config(value, key))):
            del environ[name]
            removed.append(name)
    return sorted(removed)


def check_invocation(argv: Sequence[str], key: str) -> None:
    """Raise `CredentialFenceError` if the site client invocation carries the key in any argument."""
    if key and any(key in str(arg) for arg in argv):
        raise CredentialFenceError('Upstream key found in the site client command line '
                                   '(launcher, launcher arguments or template) - not launching site clients')


def check_exports(exports: Mapping[str, str], key: str) -> None:
    """Raise `CredentialFenceError` if a task variable exported from configuration carries the key."""
    if key and any(key in str(name) or key in str(value) for name, value in exports.items()):
        raise CredentialFenceError('Upstream key found in an exported task variable ([export]) '
                                   '- not launching site clients')


def fence_site(cluster: AutoScalingCluster, key: str,
               environ: Optional[MutableMapping[str, str]] = None,
               exports: Optional[Mapping[str, str]] = None) -> List[str]:
    """Keep the upstream key from the site before its first client is launched.

    The client invocation and the exported task variables are checked (a leak there is refused, with
    `CredentialFenceError`); the environment the clients will inherit is scrubbed. Read the key, and
    anything configured through the environment, before calling this: relay settings are gone after.
    Returns the names of the variables removed.
    """
    check_invocation(cluster.autoscaler.machine.launcher, key)
    check_exports(load_task_env() if exports is None else exports, key)
    removed = scrub_environment(key, environ)
    if removed:
        shown = ['<redacted>' if key and key in name else name for name in removed]
        log.debug(f'Removed from site environment: {", ".join(shown)}')
    return removed
