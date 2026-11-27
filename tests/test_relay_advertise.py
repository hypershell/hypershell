# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The address a relay tells its site clients to dial.

`hs relay --advertise ADDR`, else `relay.advertise` (`HYPERSHELL_RELAY_ADVERTISE`), else the bind rule
(`advertised_host`): the bind address, or this host's own name for a wildcard bind. `server.advertise`
never reaches a relay.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, List, Optional

# Standard libs
import re
import time
from pathlib import Path

# External libs
import pytest
from pytest import mark, MonkeyPatch
from cmdkit.app import exit_status
from cmdkit.cli import ArgumentError
from cmdkit.config import Namespace

# Internal libs
import hypershell.relay as relay_module
from hypershell.relay import RelayApp, RelayThread, APP_USAGE, APP_HELP
from hypershell.relay.site import site_cluster, site_launch, zone_launch, scrub_environment, site_config_copy
from hypershell.core.config import config, default, PARAM_UNSET, is_relay_variable
from hypershell.core.config_schema import KEYS, EMPTY_UNSET, Kind, match_name, read_environment
from hypershell.core.logging import HOSTNAME
from tests.test_config_schema import parse_reference
from tests.test_relay_db import relays, upstream, free_port, DEADLINE, POLL  # noqa: fixtures
from tests.test_relay_launch import recording_launcher, launches, zone_word

REPO = Path(__file__).resolve().parent.parent


def told(argv: List[str]) -> str:
    """The address a launched client command line is told to dial."""
    return argv[argv.index('-H') + 1]


# --- The launch: zone_launch, site_cluster, RelayThread ---------------------------------------------------


@mark.unit
@mark.parametrize('bind, advertise, expected', [
    pytest.param('127.0.0.1', None, '127.0.0.1', id='specific'),
    pytest.param('0.0.0.0', None, HOSTNAME, id='wildcard'),
    pytest.param('0.0.0.0', '', HOSTNAME, id='empty'),
    pytest.param('0.0.0.0', 'head.site', 'head.site', id='wildcard-advertise'),
    pytest.param('127.0.0.1', 'head.site', 'head.site', id='specific-advertise'),
])
def test_site_clients_of_every_zone_dial_the_advertised_address(temp_site: Path, bind: str,
                                                                 advertise: Optional[str], expected: str) -> None:
    """The cluster's own zone and every further zone tell their clients one address."""
    cluster = site_cluster(address=(bind, 50123), zone='cpu', advertise=advertise)
    assert told(site_launch(cluster).argv) == expected
    assert told(zone_launch(cluster, 'gpu').argv) == expected
    assert cluster.advertised == (expected, 50123)


@mark.unit
@mark.parametrize('options, expected', [
    pytest.param({}, HOSTNAME, id='unset'),
    pytest.param({'advertise': 'head.site'}, 'head.site', id='advertise'),
])
def test_relay_thread_tells_every_fleet(options: Dict[str, Any], expected: str) -> None:
    """Every fleet's scaler (fixed or following a topology, it launches with `machine.launch`) tells one address."""
    relay = RelayThread(address=('0.0.0.0', free_port()), zones=['cpu', 'gpu', None], **options)
    machines = [relay.cluster.autoscaler.machine, *(fleet.machine for fleet in relay.fleets)]
    assert len(machines) == 3
    assert [told(machine.launch.argv) for machine in machines] == [expected] * 3


# --- RelayApp: option > key > none -------------------------------------------------------------------------


def with_keys(relay: Any = PARAM_UNSET, server: Any = PARAM_UNSET) -> Namespace:
    """The configuration with `relay.advertise` and `server.advertise` set."""
    data = config.copy()
    data['relay'] = {**data['relay'], 'advertise': relay}
    data['server'] = {**data['server'], 'advertise': server}
    return Namespace(data)


@mark.unit
@mark.parametrize('argv, relay_key, server_key, expected', [
    pytest.param([], PARAM_UNSET, PARAM_UNSET, None, id='unset'),
    pytest.param(['--advertise', 'head.site'], PARAM_UNSET, PARAM_UNSET, 'head.site', id='option'),
    pytest.param([], 'key.site', PARAM_UNSET, 'key.site', id='key'),
    pytest.param(['--advertise', 'head.site'], 'key.site', PARAM_UNSET, 'head.site', id='option-over-key'),
    pytest.param([], '', PARAM_UNSET, None, id='empty-key'),
    pytest.param([], None, PARAM_UNSET, None, id='none-key'),
    pytest.param([], PARAM_UNSET, 'server.site', None, id='server-key-ignored'),
])
def test_relay_app_advertise_host(monkeypatch: MonkeyPatch, argv: List[str], relay_key: Any, server_key: Any,
                                  expected: Optional[str]) -> None:
    """`--advertise`, else `relay.advertise`, else none (the bind rule); `server.advertise` is never read."""
    monkeypatch.setattr(relay_module, 'config', with_keys(relay_key, server_key))
    assert RelayApp.from_cmdline(argv).advertise_host == expected


@mark.unit
@mark.parametrize('value', [pytest.param('', id='empty'), pytest.param(' ', id='blank')])
def test_relay_app_refuses_an_empty_advertise(value: str) -> None:
    """An empty or blank `--advertise` is a usage error naming the option."""
    app = RelayApp.from_cmdline(['--advertise', value])
    with pytest.raises(ArgumentError, match='Expected an address for --advertise'):
        app.check_args()


@mark.unit
def test_relay_help_lists_advertise_after_bind() -> None:
    """`hs relay --help` lists `--advertise` after `--bind`, in the usage line and the options."""
    assert '[--advertise ADDR]' in APP_USAGE and '--advertise' in APP_HELP
    assert APP_USAGE.index('[--bind ADDR]') < APP_USAGE.index('[--advertise ADDR]')
    options = APP_HELP[APP_HELP.index('Options:'):]
    assert options.index('--bind') < options.index('--advertise') < options.index('--port')


# --- The key: relay.advertise --------------------------------------------------------------------------------


@mark.unit
def test_relay_advertise_is_a_declared_text_key() -> None:
    """`relay.advertise` is declared text, unset by default, and an empty variable sets nothing."""
    assert KEYS.get('relay.advertise') is Kind.TEXT
    assert 'relay.advertise' in EMPTY_UNSET
    assert default.relay.get('advertise') == PARAM_UNSET
    assert match_name('HYPERSHELL_RELAY_ADVERTISE') == (('relay', 'advertise'), Kind.TEXT)


@mark.unit
def test_relay_advertise_variable() -> None:
    """The variable is read as text as written; empty, it is not given."""
    layer, _ = read_environment({'HYPERSHELL_RELAY_ADVERTISE': '10.0.0.5'})
    assert layer == {'relay': {'advertise': '10.0.0.5'}}
    layer, variables = read_environment({'HYPERSHELL_RELAY_ADVERTISE': ''})
    assert layer == {} and not variables


@mark.unit
def test_relay_advertise_key_reference() -> None:
    """The reference gives `relay.advertise` after `.bind`, and `server.advertise` points a relay user to it."""
    reference = parse_reference()
    assert 'relay.advertise' in reference
    relay_keys = [path for path in reference if path.startswith('relay.') and path.count('.') == 1]
    assert relay_keys.index('relay.advertise') == relay_keys.index('relay.bind') + 1
    text = reference['relay.advertise'][1]
    assert 'HYPERSHELL_RELAY_ADVERTISE' in text and '--advertise' in text
    assert 'relay.advertise' in reference['server.advertise'][1]


@mark.unit
def test_relay_advertise_is_fenced(tmp_path: Path) -> None:
    """Like every relay setting, it reaches neither the site environment nor the site's configuration copy."""
    assert is_relay_variable('HYPERSHELL_RELAY_ADVERTISE')
    environ = {'HYPERSHELL_RELAY_ADVERTISE': 'head.site', 'PATH': '/usr/bin:/bin'}
    assert scrub_environment('', environ) == ['HYPERSHELL_RELAY_ADVERTISE']
    assert environ == {'PATH': '/usr/bin:/bin'}
    source = tmp_path / 'config.toml'
    source.write_text('[relay]\nadvertise = "head.site"\n\n[server]\nbind = "0.0.0.0"\n')
    copy = site_config_copy(str(source), '', str(tmp_path / 'copy.toml'))
    assert copy == str(tmp_path / 'copy.toml')
    assert 'advertise' not in Path(copy).read_text() and 'bind' in Path(copy).read_text()


# --- A relay process ------------------------------------------------------------------------------------------


def wait_launches(relay: Any, tmp_path: Path, zones: List[str]) -> List[dict]:
    """Every launch recorded once each of `zones` has one ('' for the default zone)."""
    deadline = time.monotonic() + DEADLINE
    while True:
        found = launches(tmp_path)
        if {(zone_word(launch['argv']) or [''])[0] for launch in found} >= set(zones):
            return found
        assert relay.proc.poll() is None, relay.text()
        assert time.monotonic() < deadline, f'no site client launched for each of {zones}:\n{relay.text()}'
        time.sleep(POLL)


@mark.integration
@mark.parametrize('args, env, zones, expected', [
    pytest.param([], {}, [''], '127.0.0.1', id='bind'),
    pytest.param(['--advertise', 'localhost'], {}, [''], 'localhost', id='option'),
    pytest.param(['--advertise', 'localhost', '--zone', 'a', '--zone', 'b'], {}, ['a', 'b'], 'localhost', id='zones'),
    pytest.param([], {'HYPERSHELL_RELAY_ADVERTISE': 'localhost'}, [''], 'localhost', id='key'),
    pytest.param(['--advertise', '127.0.0.1'], {'HYPERSHELL_RELAY_ADVERTISE': 'no-such-name.invalid'}, [''],
                 '127.0.0.1', id='option-over-key'),
    pytest.param([], {'HYPERSHELL_RELAY_ADVERTISE': ''}, [''], '127.0.0.1', id='empty-key'),
    pytest.param([], {'HYPERSHELL_SERVER_ADVERTISE': 'localhost'}, [''], '127.0.0.1', id='server-key'),
])
def test_relay_tells_its_site_clients(relays, tmp_path: Path, args: List[str], env: Dict[str, str],
                                      zones: List[str], expected: str) -> None:
    """A relay bound to 127.0.0.1 tells every site client the option, else its key, else its bind."""
    relay = relays('--launcher', str(recording_launcher(tmp_path)), *args, **env)
    relay.wait_ready()
    found = wait_launches(relay, tmp_path, zones)
    assert relay.drain() == exit_status.success
    assert {told(launch['argv']) for launch in found} == {expected}
    for launch in found:
        assert not any(name.upper().startswith('HYPERSHELL_RELAY_') for name in launch['environ'])


@mark.integration
@mark.parametrize('value', [pytest.param('', id='empty'), pytest.param(' ', id='blank')])
def test_relay_refuses_an_empty_advertise_before_its_database(relays, value: str) -> None:
    """`hs relay --advertise ''` exits 2 naming the option, and creates no relay database."""
    relay = relays('--advertise', value)
    assert relay.wait_exit() == exit_status.bad_argument
    assert 'Expected an address for --advertise' in relay.text()
    assert not list(relays.site.glob('*/relay/*.db'))


# --- The docs and completions ------------------------------------------------------------------------------


def text(path: str) -> str:
    return (REPO / path).read_text(encoding='utf-8')


@mark.unit
def test_relay_advertise_documented_in_the_help_pages() -> None:
    """The usage and help includes list `--advertise` after `--bind`; the help names the key and the TLS case."""
    usage = text('docs/_include/relay_usage.rst')
    assert '``[--advertise ADDR]``' in usage
    assert usage.index('``[--bind ADDR]``') < usage.index('``[--advertise ADDR]``')
    help_text = text('docs/_include/relay_help.rst')
    assert '``--advertise`` *ADDR*' in help_text
    assert help_text.index('``--bind`` *ADDR*') < help_text.index('``--advertise`` *ADDR*') < help_text.index('``--port`` *NUM*')
    entry = help_text[help_text.index('``--advertise`` *ADDR*'):help_text.index('``--port`` *NUM*')]
    assert 'relay.advertise' in entry and 'servername' in entry
    assert 'relay.advertise' in text('docs/security.rst')


@mark.unit
def test_relay_advertise_documented_in_the_completions() -> None:
    """The bash and zsh completions of `hs relay` offer `--advertise`."""
    bash = text('share/bash_completion.d/hs')
    relay_bash = bash[bash.index('function _hs_relay()'):bash.index('function _hs_server()')]
    assert re.search(r'--bind\s+--advertise', relay_bash) and '--advertise)' in relay_bash
    zsh = text('share/zsh/site-functions/_hs')
    relay_zsh = zsh[zsh.index('function _hs_relay()'):]
    relay_zsh = relay_zsh[:relay_zsh.index('\n}\n')]
    assert "'--advertise[" in relay_zsh
    assert relay_zsh.index("'--bind[") < relay_zsh.index("'--advertise[")
