# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
`blame`, `hs config which` and `hs config` completion name keys and variables by the loader's rule.

For every key a variable sets, both name that variable as it was written. Completion offers each
key by its dotted path, one per line, and never a process variable (`HYPERSHELL_SITE`,
`HYPERSHELL_CONFIG_FILE`, `HYPERSHELL_PYTHONPATH`, `HYPERSHELL_ZONE`), which is not a key.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, Tuple

# Standard libs
from pathlib import Path

# External libs
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests.test_env_config import config_repr, hs


@mark.integration
def test_blame_names_variable(temp_site: Path) -> None:
    """`blame` names the variable that set the key, as it was written."""
    rc, seen, stderr = config_repr(temp_site, "blame(config, 'server', 'tls', 'min_version')",
                                   'from hypershell.core.config import blame',
                                   HYPERSHELL_server_tls_min_version='TLSv1.3')
    assert rc == exit_status.success, stderr
    assert seen == repr('from: HYPERSHELL_server_tls_min_version')


WHICH: Final[Dict[str, Tuple[str, str]]] = {
    'server.tls.min_version': ('HYPERSHELL_server_tls_min_version', 'TLSv1.3'),
    'export.MyVar': ('HYPERSHELL_EXPORT_MyVar', 'x'),
}
"""For each key: the variable that sets it and its value."""


@mark.integration
@mark.parametrize('path', list(WHICH), ids=list(WHICH))
def test_which_names_variable(temp_site: Path, path: str) -> None:
    """`hs config which` shows the value and names the variable that set the key, as it was written."""
    name, value = WHICH[path]
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'which', path], **{name: value})
    assert rc == exit_status.success, stderr
    assert stdout.startswith(f'{value} (env: {name} ')


@mark.integration
def test_completion_offers_key_paths(temp_site: Path) -> None:
    """Completion offers keys by their dotted path, never a path no key has."""
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', '--list-available'],
                            HYPERSHELL_SERVER_TLS_MIN_VERSION='TLSv1.3')
    assert rc == exit_status.success, stderr
    words = set(stdout.split())
    assert {'server.tls.min_version', 'autoscale.failed_launches', 'relay.launcher_args'} <= words
    assert not {'server.tls.min.version', 'autoscale.failed.launches', 'relay.launcher.args'} & words


@mark.integration
def test_completion_one_per_line(temp_site: Path) -> None:
    """Completion prints one key per line, so each shell offers each key as its own candidate."""
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', '--list-available'])
    assert rc == exit_status.success, stderr
    lines = stdout.splitlines()
    assert 'server.port' in lines and 'logging.level' in lines
    assert all(len(line.split()) == 1 for line in lines)


PROCESS_VARIABLES: Final[Dict[str, str]] = {
    'HYPERSHELL_ZONE': 'gpu',
    'HYPERSHELL_PYTHONPATH': '.',
}
"""Process variables set for the test besides `HYPERSHELL_SITE` and `HYPERSHELL_CONFIG_FILE`."""


@mark.integration
def test_process_variables_not_keys(temp_site: Path) -> None:
    """The process variables are not configuration keys and are not offered by completion."""
    rc, seen, stderr = config_repr(temp_site, "[name for name in ('site', 'config', 'pythonpath', 'zone') "
                                              "if name in config]", **PROCESS_VARIABLES)
    assert rc == exit_status.success, stderr
    assert seen == repr([])
    rc, stdout, stderr = hs(temp_site, ['hs', 'config', 'get', '--list-available'], **PROCESS_VARIABLES)
    assert rc == exit_status.success, stderr
    assert not {'site', 'config.file', 'pythonpath', 'zone'} & set(stdout.split())
