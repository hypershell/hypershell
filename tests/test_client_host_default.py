# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Where a client given no `-H` dials: one rule for `hs client`, `hs submit --queue` and `QueueConfig.load()`.

With no `-H`, a client dials the first of:

1. `server.host`, if any configuration layer gives it;
2. `server.advertise`, if any layer gives it;
3. `server.bind`, if it is one specific address;
4. `localhost`.

A key counts as given by the layer that decides it, not by its value, so a `host` written as
`localhost` is dialled as `localhost`. The marker for an unset `advertise` is never dialled. `-H`
wins over all of them. The Python API defaults stay the built-in `localhost`, and the server side
keeps reading `server.bind`.

The rule itself is tested on constructed, layered configurations. The configuration is built at
import, so each reading by a command runs in a child interpreter with the file layer or a
`HYPERSHELL_*` variable set. No address here resolves, and nothing dials.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, List, Optional, Tuple

# Standard libs
import re
import inspect
import dataclasses
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status
from cmdkit.config import Configuration, Namespace, ConfigurationError

# Internal libs
from hypershell.core import config as config_module
from hypershell.core.config import default, client_host, PARAM_UNSET
from hypershell.core.queue import QueueConfig
from hypershell.client import ClientApp
from hypershell.submit import SubmitApp
from tests.test_env_config import config_repr
from tests.test_config_unread import file_layer
from tests.test_config_schema import parse_reference


REPO: Final[Path] = Path(__file__).resolve().parent.parent
SEED_HOST: Final[str] = 'login00.example.edu'
ADVERTISE: Final[str] = 'adv.example'
SPECIFIC: Final[str] = '10.0.0.5'
OTHER: Final[str] = 'x.example'


def layered(**layers: dict) -> Configuration:
    """A configuration with the built-in defaults and the given extra layers."""
    return Configuration(default=default, **{name: Namespace(data) for name, data in layers.items()})


def server(**keys: str) -> dict:
    """A layer that gives `keys` in its `[server]` table."""
    return {'server': dict(keys)}


# --------------------------------------------------------------------------------------------------
# The rule

RULE: Final[List] = [
    pytest.param({'local': server(bind='0.0.0.0', host=SEED_HOST)}, {}, SEED_HOST, id='seed'),
    pytest.param({'local': server(host=SEED_HOST), 'env': server(advertise=ADVERTISE)}, {}, SEED_HOST,
                 id='host-over-advertise'),
    pytest.param({'local': server(bind=SPECIFIC, advertise=ADVERTISE)}, {}, ADVERTISE, id='advertise-over-bind'),
    pytest.param({'env': server(bind=SPECIFIC)}, {}, SPECIFIC, id='specific-bind'),
    pytest.param({'local': server(bind='0.0.0.0')}, {}, 'localhost', id='wildcard-bind'),
    pytest.param({'local': server(bind='')}, {}, 'localhost', id='empty-bind'),
    pytest.param({}, {}, 'localhost', id='nothing-given'),
    pytest.param({'local': server(host='localhost', advertise=ADVERTISE, bind=SPECIFIC)}, {}, 'localhost',
                 id='host-given-as-localhost'),
    pytest.param({'local': server(advertise='', bind=SPECIFIC)}, {}, SPECIFIC, id='empty-advertise'),
    pytest.param({'local': server(advertise=PARAM_UNSET, bind=SPECIFIC)}, {}, SPECIFIC, id='unset-marker-advertise'),
    pytest.param({'local': server(host_env='CHD_HOST', bind='0.0.0.0')}, {'CHD_HOST': SEED_HOST}, SEED_HOST,
                 id='host-env'),
    pytest.param({'local': server(host_eval=f'echo {SEED_HOST}')}, {}, SEED_HOST, id='host-eval'),
    pytest.param({'local': server(advertise_env='CHD_ADV', bind=SPECIFIC)}, {'CHD_ADV': ADVERTISE}, ADVERTISE,
                 id='advertise-env'),
]
"""Layers given, variables set, and the address dialled."""


@mark.unit
@mark.parametrize('layers, variables, expected', RULE)
def test_client_host(monkeypatch: pytest.MonkeyPatch, layers: Dict[str, dict], variables: Dict[str, str],
                     expected: str) -> None:
    """The first of `host`, `advertise`, a specific `bind` and `localhost`, by the layer that gives it."""
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert client_host(layered(**layers)) == expected


@mark.unit
def test_client_host_unset_env_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `host_env` naming a variable that is not set is refused, and the message names the key."""
    monkeypatch.delenv('CHD_UNSET', raising=False)
    with pytest.raises(ConfigurationError, match=r'server\.host_env'):
        client_host(layered(local=server(host_env='CHD_UNSET', bind=SPECIFIC)))


@mark.unit
def test_client_host_update_layer() -> None:
    """A `host` given by an in-place update of the configuration counts as given."""
    base = layered(local=server(bind=SPECIFIC, advertise=ADVERTISE))
    base.update(server(host=SEED_HOST))
    assert client_host(base) == SEED_HOST


@mark.unit
def test_client_host_never_unset_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configuration of the rule's rows, nor a file that writes the marker, has a client dial it."""
    monkeypatch.setenv('CHD_HOST', SEED_HOST)
    monkeypatch.setenv('CHD_ADV', ADVERTISE)
    rows = [row.values[0] for row in RULE] + [{'local': server(host=PARAM_UNSET)},
                                               {'local': server(bind=PARAM_UNSET)},
                                               {'local': server(host=PARAM_UNSET, bind=PARAM_UNSET,
                                                                advertise=PARAM_UNSET)}]
    assert [layers for layers in rows if client_host(layered(**layers)) == PARAM_UNSET] == []


@mark.unit
def test_wildcard_binds_shared() -> None:
    """The client rule and the address `hs cluster` advertises share one set of wildcard binds."""
    from hypershell.cluster import launch
    assert config_module.WILDCARD_BINDS is launch.WILDCARD_BINDS


# --------------------------------------------------------------------------------------------------
# The readers: hs client, hs submit --queue, QueueConfig.load()

READERS: Final[str] = '''\
from hypershell.client import ClientApp
from hypershell.submit import SubmitApp
from hypershell.core.queue import QueueConfig


def readers(client_argv=(), submit_argv=()):
    """The host `hs client` and `hs submit --queue` dial once their arguments are checked, and the loader's."""
    client = ClientApp.from_cmdline(list(client_argv))
    client.check_args()
    submit = SubmitApp.from_cmdline(['-q', *submit_argv, 'echo', 'one'])
    submit.check_arguments()
    return client.host, submit.queue_server, QueueConfig.load().host
'''
"""Child preamble that defines `readers()`."""

READER_NAMES: Final[Tuple[str, ...]] = ('client', 'submit', 'queue')
"""The readers, in the order `readers()` gives them."""

SEED_FILE: Final[str] = f'[server]\nbind = "0.0.0.0"\nhost = "{SEED_HOST}"\n'
"""The seed's shared configuration file."""

CASES: Final[Dict[str, Tuple[Optional[str], Dict[str, str], str]]] = {
    # case: (file text, variables, expected)
    'seed': (SEED_FILE, {}, SEED_HOST),
    'seed-env': (None, {'HYPERSHELL_SERVER_BIND': '0.0.0.0', 'HYPERSHELL_SERVER_HOST': SEED_HOST}, SEED_HOST),
    'host-over-advertise': (f'[server]\nhost = "{SEED_HOST}"\n', {'HYPERSHELL_SERVER_ADVERTISE': ADVERTISE},
                            SEED_HOST),
    'advertise-over-bind': (f'[server]\nbind = "{SPECIFIC}"\nadvertise = "{ADVERTISE}"\n', {}, ADVERTISE),
    'advertise-env-wildcard-bind': (None, {'HYPERSHELL_SERVER_ADVERTISE': ADVERTISE,
                                           'HYPERSHELL_SERVER_BIND': '0.0.0.0'}, ADVERTISE),
    'specific-bind': (None, {'HYPERSHELL_SERVER_BIND': SPECIFIC}, SPECIFIC),
    'wildcard-bind': ('[server]\nbind = "0.0.0.0"\n', {}, 'localhost'),
    'empty-bind-file': ('[server]\nbind = ""\n', {}, 'localhost'),
    'empty-bind-env': (None, {'HYPERSHELL_SERVER_BIND': ''}, 'localhost'),
    'nothing-given': (None, {}, 'localhost'),
    'host-given-as-localhost': (f'[server]\nhost = "localhost"\nadvertise = "{ADVERTISE}"\nbind = "{SPECIFIC}"\n',
                                {}, 'localhost'),
    'empty-advertise-file': (f'[server]\nadvertise = ""\nbind = "{SPECIFIC}"\n', {}, SPECIFIC),
    'unset-marker-advertise-file': (f'[server]\nadvertise = "{PARAM_UNSET}"\nbind = "{SPECIFIC}"\n', {}, SPECIFIC),
    'host-env-form': (None, {'HYPERSHELL_SERVER_HOST_ENV': 'CHD_HOST', 'CHD_HOST': SEED_HOST,
                             'HYPERSHELL_SERVER_BIND': '0.0.0.0'}, SEED_HOST),
}
"""Each configuration given to the readers: by file, by environment, or both."""

SEEN: Dict[str, Tuple[str, str, str]] = {}
"""What `readers()` gave for each case, read once in one child."""


def read_case(site: Path, case: str) -> Tuple[str, str, str]:
    """The three readers' hosts for `case`, from one child in `site` (cached for the module)."""
    if case not in SEEN:
        text, variables, _ = CASES[case]
        variables = {**(file_layer(site, text) if text is not None else {}), **variables}
        rc, seen, stderr = config_repr(site, 'readers()', READERS, **variables)
        assert rc == exit_status.success, stderr
        SEEN[case] = eval(seen)
    return SEEN[case]


def reader_rows() -> List:
    """Each (reader, case) row."""
    return [pytest.param(reader, case, expected, id=f'{reader}-{case}')
            for case, (_, _, expected) in CASES.items() for reader in READER_NAMES]


@mark.integration
@mark.parametrize('reader, case, expected', reader_rows())
def test_reader_dials_by_rule(temp_site: Path, reader: str, case: str, expected: str) -> None:
    """`hs client`, `hs submit --queue` and `QueueConfig.load()` each dial by the one rule."""
    assert read_case(temp_site, case)[READER_NAMES.index(reader)] == expected


@mark.integration
def test_host_option_wins(temp_site: Path) -> None:
    """`-H` reaches `hs client` and `hs submit --queue` whatever the configuration gives."""
    variables = file_layer(temp_site, f'[server]\nhost = "{SEED_HOST}"\nadvertise = "{ADVERTISE}"\n'
                                      f'bind = "{SPECIFIC}"\n')
    rc, seen, stderr = config_repr(temp_site, f'readers(["-H", "{OTHER}"], ["-H", "{OTHER}"])[:2]', READERS,
                                   **variables)
    assert rc == exit_status.success, stderr
    assert eval(seen) == (OTHER, OTHER)


# --------------------------------------------------------------------------------------------------
# The Python API defaults and the server side

API_DEFAULTS: Final[str] = '''\
import inspect
import hypershell.client, hypershell.submit, hypershell.server
from hypershell.core.queue import QueueConfig


def address_host(function):
    """The host of the `address` default of `function`."""
    return inspect.signature(function).parameters['address'].default[0]


def api_defaults():
    return (hypershell.client.DEFAULT_HOST, hypershell.submit.DEFAULT_HOST, QueueConfig().host,
            hypershell.server.DEFAULT_BIND, address_host(hypershell.client.ClientThread.__init__),
            address_host(hypershell.client.run_client))
'''
"""Child preamble that defines `api_defaults()`."""


@mark.integration
def test_api_defaults_stay_localhost(temp_site: Path) -> None:
    """The Python API defaults keep the built-in `localhost` whatever the configuration gives."""
    variables = {**file_layer(temp_site, SEED_FILE), 'HYPERSHELL_SERVER_ADVERTISE': ADVERTISE}
    rc, seen, stderr = config_repr(temp_site, 'api_defaults()', API_DEFAULTS, **variables)
    assert rc == exit_status.success, stderr
    assert eval(seen) == ('localhost', ) * 6


@mark.unit
def test_queue_config_default_from_host() -> None:
    """`QueueConfig`'s host default is the built-in `server.host`, and the queue module never names `bind`."""
    field, = [field for field in dataclasses.fields(QueueConfig) if field.name == 'host']
    assert field.default == default.server.host == 'localhost'
    assert 'default.server.bind' not in (REPO / 'src' / 'hypershell' / 'core' / 'queue.py').read_text()


@mark.integration
def test_server_side_reads_bind(temp_site: Path) -> None:
    """`hs server` and `hs cluster` still listen on `server.bind`, whatever `server.host` gives."""
    rc, seen, stderr = config_repr(temp_site,
                                   '(ServerApp.from_cmdline([]).host, ClusterApp.from_cmdline(["t.in"]).host)',
                                   'from hypershell.server import ServerApp\nfrom hypershell.cluster import ClusterApp',
                                   **file_layer(temp_site, SEED_FILE))
    assert rc == exit_status.success, stderr
    assert eval(seen) == ('0.0.0.0', '0.0.0.0')


# --------------------------------------------------------------------------------------------------
# Help and docs

def help_entry(text: str, option: str) -> str:
    """The `option` entry of `--help` text: its line and the continuation lines under it."""
    lines = text.splitlines()
    start, = [i for i, line in enumerate(lines) if line.lstrip().startswith(option)]
    entry = [lines[start]]
    for line in lines[start + 1:]:
        if line.lstrip().startswith('-') or not line.strip():
            break
        entry.append(line)
    return '\n'.join(entry)


def rst_entry(path: Path, option: str) -> str:
    """The `option` entry of a `*_help.rst` page: its term line and the indented body under it."""
    lines = path.read_text(encoding='utf-8').splitlines()
    start, = [i for i, line in enumerate(lines) if line.startswith(option)]
    entry = [lines[start]]
    for line in lines[start + 1:]:
        if line and not line.startswith(' '):
            break
        entry.append(line)
    return '\n'.join(entry)


@mark.unit
@mark.parametrize('app', [ClientApp, SubmitApp], ids=['client', 'submit'])
def test_help_states_rule(app) -> None:
    """The `-H` entry of `--help` names `server.host` and `server.advertise`."""
    entry = help_entry(app.interface.help_text, '-H, --host')
    assert 'server.host' in entry and 'server.advertise' in entry, entry


@mark.unit
@mark.parametrize('page', ['client_help.rst', 'submit_help.rst'])
def test_help_page_states_rule(page: str) -> None:
    """The `-H` entry of the help page names `server.host` and `server.advertise`."""
    entry = rst_entry(REPO / 'docs' / '_include' / page, '``-H``, ``--host``')
    assert 'server.host' in entry and 'server.advertise' in entry, entry


@mark.unit
def test_reference_states_rule() -> None:
    """`[server]` `.host` names its fallbacks, and `.advertise` and `.bind` say a `client` dials them."""
    reference = parse_reference()
    host, advertise, bind = (reference[f'server.{name}'][1] for name in ('host', 'advertise', 'bind'))
    assert '``.advertise``' in host and '``.bind``' in host, host
    assert '`client`' in advertise, advertise
    assert '`client`' in bind, bind


MARKER: Final[re.Pattern] = re.compile(r'^\.\. versionchanged:: 2\.9\.0\n((?:   .*\n|\n)*)', re.MULTILINE)
"""A `versionchanged:: 2.9.0` directive at the page's top level, and its body."""


@mark.unit
@mark.parametrize('command', ['client', 'submit'])
def test_cli_page_marks_change(command: str) -> None:
    """The command's page marks the `-H` change above its generated help."""
    text = (REPO / 'docs' / 'cli' / f'{command}.rst').read_text(encoding='utf-8')
    above, _ = text.split(f'.. include:: ../_include/{command}_help.rst')
    assert any('``-H``' in body for body in MARKER.findall(above))
