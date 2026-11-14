# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The address every launch mode tells its clients to dial.

Clients are told the bind address when the server binds one address and this host's own name when
it binds every interface, unless `--advertise` or `server.advertise` names another. A relay's site
clients follow the same rule, with no override.
"""


# Type annotations
from __future__ import annotations
from typing import Any, List, Optional

# Standard libs
from pathlib import Path

# External libs
import pytest
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError
from cmdkit.config import Namespace

# Internal libs
from tests import main, create_taskfile_echo
import hypershell.cluster as cluster_module
from hypershell.cluster import ClusterApp
from hypershell.cluster.launch import WILDCARD_BINDS, advertised_host
from hypershell.cluster.remote import RemoteCluster, AutoScalingCluster
from hypershell.cluster.ssh import SSHCluster
from hypershell.core.config import config, PARAM_UNSET
from hypershell.core.logging import HOSTNAME
from hypershell.relay.site import site_cluster, site_launch, zone_launch


SPECIFIC: str = '10.20.30.40'
"""A bind address of one interface (never bound: no server is started here)."""


def told(argv: List[str]) -> str:
    """The address a launched client command line is told to dial."""
    return argv[argv.index('-H') + 1]


def ssh_told(argv: List[str]) -> str:
    """The address the remote command of an SSH launch is told to dial."""
    words = argv[-1].split()
    return words[words.index('-H') + 1]


@mark.unit
@mark.parametrize('bind, advertise, expected', [
    pytest.param('0.0.0.0', None, HOSTNAME, id='wildcard'),
    pytest.param('', None, HOSTNAME, id='empty-bind'),
    pytest.param(SPECIFIC, None, SPECIFIC, id='specific'),
    pytest.param('localhost', None, 'localhost', id='localhost'),
    pytest.param('127.0.0.1', None, '127.0.0.1', id='loopback'),
    pytest.param('0.0.0.0', 'head.cluster', 'head.cluster', id='advertise-over-wildcard'),
    pytest.param(SPECIFIC, 'head.cluster', 'head.cluster', id='advertise-over-specific'),
    pytest.param(SPECIFIC, '', SPECIFIC, id='empty-advertise'),
    pytest.param('0.0.0.0', '', HOSTNAME, id='empty-advertise-wildcard'),
])
def test_advertised_host(bind: str, advertise: Optional[str], expected: str) -> None:
    """The advertise address wins when given; else the bind, or this host's name for a wildcard bind."""
    assert advertised_host(bind, advertise) == expected


@mark.unit
def test_wildcard_binds() -> None:
    """Only the IPv4 wildcard and the empty address listen on every interface."""
    assert WILDCARD_BINDS == frozenset({'0.0.0.0', ''})


CASES = [
    pytest.param('0.0.0.0', None, HOSTNAME, id='wildcard'),
    pytest.param(SPECIFIC, None, SPECIFIC, id='specific'),
    pytest.param('0.0.0.0', 'head.cluster', 'head.cluster', id='advertise-wildcard'),
    pytest.param(SPECIFIC, 'head.cluster', 'head.cluster', id='advertise-specific'),
]


@mark.unit
@mark.parametrize('bind, advertise, expected', CASES)
def test_launcher_mode_tells_clients(temp_site: Path, bind: str, advertise: Optional[str], expected: str) -> None:
    """A launcher-mode cluster tells its clients the advertised address."""
    cluster = RemoteCluster(launcher='srun', bind=(bind, 50123), tls=None, in_memory=True, advertise=advertise)
    assert told(cluster.launch.argv) == expected
    assert cluster.advertised == (expected, 50123)


@mark.unit
@mark.parametrize('bind, advertise, expected', CASES)
def test_autoscaling_mode_tells_clients(temp_site: Path, bind: str, advertise: Optional[str], expected: str) -> None:
    """An autoscaling cluster tells every launch the advertised address, its own zone's and any other's."""
    cluster = AutoScalingCluster(launcher='srun', bind=(bind, 50123), tls=None, advertise=advertise)
    assert told(cluster.launch.argv) == expected
    assert told(cluster.autoscaler.machine.launch.argv) == expected
    assert cluster.advertised == (expected, 50123)


@mark.unit
@mark.parametrize('bind, advertise, expected', CASES)
def test_ssh_mode_tells_clients(temp_site: Path, bind: str, advertise: Optional[str], expected: str) -> None:
    """An SSH cluster tells the client on every host the advertised address."""
    cluster = SSHCluster(nodelist=['node-a', 'node-b'], bind=(bind, 50123), tls=None, in_memory=True,
                         advertise=advertise)
    assert [ssh_told(launch.argv) for launch in cluster.launches] == [expected, expected]
    assert cluster.advertised == (expected, 50123)


@mark.unit
@mark.parametrize('bind, expected', [
    pytest.param('127.0.0.1', '127.0.0.1', id='specific'),
    pytest.param('0.0.0.0', HOSTNAME, id='wildcard'),
])
def test_relay_site_clients_dial_the_bind(temp_site: Path, bind: str, expected: str) -> None:
    """A relay's site clients, of its first zone and of any other, are told its bind or, for a wildcard, its name."""
    cluster = site_cluster(address=(bind, 50123), zone='cpu')
    assert told(site_launch(cluster).argv) == expected
    assert told(zone_launch(cluster, 'gpu').argv) == expected


# --- The option and the key, through `hs cluster` ---------------------------------------------------------


def with_advertise_key(value: Any) -> Namespace:
    """The site's configuration with `server.advertise` set to `value`."""
    data = config.copy()
    data['server'] = {**data['server'], 'advertise': value}
    return Namespace(data)


def built_clusters(monkeypatch: MonkeyPatch) -> List[Any]:
    """Clusters the next `hs cluster` run builds, constructed but never started."""
    built = []

    def fake_run_cluster(*args, autoscaling: bool = False, **options) -> None:
        built.append((AutoScalingCluster if autoscaling else RemoteCluster)(*args, **options))

    def fake_run_ssh(*args, **options) -> None:
        built.append(SSHCluster(*args, **options))

    monkeypatch.setattr(cluster_module, 'run_cluster', fake_run_cluster)
    monkeypatch.setattr(cluster_module, 'run_ssh', fake_run_ssh)
    return built


MODES = [
    pytest.param(['--launcher', 'srun'], id='launcher'),
    pytest.param(['--mpi'], id='mpi'),
    pytest.param(['--ssh', 'node-a,node-b'], id='ssh'),
    pytest.param(['--launcher', 'srun', '--autoscaling', 'fixed'], id='autoscaling'),
]


def addresses(cluster: Any) -> List[str]:
    """Every address a built cluster tells its clients."""
    if isinstance(cluster, SSHCluster):
        return [ssh_told(launch.argv) for launch in cluster.launches]
    return [told(cluster.launch.argv)]


def run_mode(app: ClusterApp) -> None:
    """Run the app's mode with the fewest options a cluster needs (nothing is started)."""
    app.check_arguments()
    app.launchers[app.mode](source=[], tls=None, in_memory=app.mode != 'autoscaling')


@mark.unit
@mark.parametrize('mode', MODES)
@mark.parametrize('argv, key, expected', [
    pytest.param([], PARAM_UNSET, HOSTNAME, id='default-wildcard'),
    pytest.param(['-H', SPECIFIC], PARAM_UNSET, SPECIFIC, id='specific-bind'),
    pytest.param(['-H', SPECIFIC, '--advertise', 'head.cluster'], PARAM_UNSET, 'head.cluster', id='option'),
    pytest.param(['-H', SPECIFIC], 'key.cluster', 'key.cluster', id='key'),
    pytest.param(['--advertise', 'head.cluster'], 'key.cluster', 'head.cluster', id='option-over-key'),
    pytest.param(['-H', SPECIFIC], '', SPECIFIC, id='empty-key'),
])
def test_cluster_app_tells_each_mode(temp_site: Path, monkeypatch: MonkeyPatch, mode: List[str],
                                     argv: List[str], key: Any, expected: str) -> None:
    """Every remote mode of `hs cluster` passes the option, else the key, else the bind rule to its clients."""
    monkeypatch.setattr(cluster_module, 'config', with_advertise_key(key))
    built = built_clusters(monkeypatch)
    taskfile = create_taskfile_echo(temp_site, count=1)
    run_mode(ClusterApp.from_cmdline([str(taskfile), *mode, *argv]))
    cluster, = built
    assert set(addresses(cluster)) == {expected}
    assert cluster.advertised[0] == expected


@mark.unit
def test_cluster_app_advertise_host_unset(monkeypatch: MonkeyPatch) -> None:
    """With neither the option nor the key, no advertised address is passed on (the bind rule applies)."""
    for value in (PARAM_UNSET, '', None):
        monkeypatch.setattr(cluster_module, 'config', with_advertise_key(value))
        assert ClusterApp.from_cmdline(['tasks.in', '--launcher', 'srun']).advertise_host is None


@mark.unit
def test_advertise_refused_for_a_local_cluster() -> None:
    """A local cluster launches no clients, so `--advertise` is refused, naming the option."""
    app = ClusterApp.from_cmdline(['tasks.in', '--advertise', 'head.cluster'])
    with pytest.raises(ArgumentError, match='Cannot use --advertise with a local cluster'):
        app.check_arguments()


@mark.unit
@mark.parametrize('value', ['', ' '])
def test_advertise_refuses_an_empty_value(value: str) -> None:
    """An empty `--advertise` is refused rather than read as unset."""
    app = ClusterApp.from_cmdline(['tasks.in', '--launcher', 'srun', '--advertise', value])
    with pytest.raises(ArgumentError, match='--advertise'):
        app.check_arguments()


@mark.integration
def test_advertise_with_local_cluster_exits_with_usage_error(temp_site: Path) -> None:
    """`hs cluster --advertise` with no launch mode exits with a usage error naming the option."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    status, stdout, stderr = main(['hs', 'cluster', str(taskfile), '--no-tls', '--advertise', 'localhost'])
    assert status == exit_status.bad_argument
    assert 'Cannot use --advertise with a local cluster' in stderr


@mark.integration
def test_advertise_key_ignored_by_a_local_cluster(temp_site: Path, monkeypatch: MonkeyPatch) -> None:
    """`server.advertise` is a site-wide setting a local cluster runs with, ignoring it."""
    taskfile = create_taskfile_echo(temp_site, count=2)
    monkeypatch.setenv('HYPERSHELL_SERVER_ADVERTISE', 'no-such-name.invalid')
    status, stdout, stderr = main(['hs', 'cluster', str(taskfile), '--no-tls', '--no-db'])
    assert status == exit_status.success, stderr
    assert sorted(stdout.split()) == ['0', '1']
