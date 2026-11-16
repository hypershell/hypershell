# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Task variables given by `HYPERSHELL_EXPORT_<NAME>`, and the key variables recognised by the matcher.

A task sees `<NAME>` as written, with the variable's value unchanged; a file's `[export]` table is
upper-cased and its lists joined, and the environment's value wins for the same task variable.
Every name the matcher reads as a form of `server.auth` or `relay.upstream.auth` is a key variable
to the client and to `--ssh --env`: neither hands it on.
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
from tests import create_taskfile
from tests.test_env_config import KEY, child_env, config_repr, run_bounded
from hypershell.core.sys import PATH_SEP


TASK_ENV: Final[str] = 'from hypershell.core.config import load_task_env'
"""Import a child needs to read the task variables."""

AS_WRITTEN: Final[Dict[str, Tuple[str, str]]] = {
    'leading-zeros': ('RUN_ID', '007'),
    'mixed-case': ('MyVar', 'x'),
    'empty': ('EMPTY', ''),
    'exponent': ('SCALE', '1e3'),
}
"""Task variable names and values, each given as `HYPERSHELL_EXPORT_<NAME>`."""


@mark.integration
@mark.parametrize('case', list(AS_WRITTEN), ids=list(AS_WRITTEN))
def test_export_as_written(temp_site: Path, case: str) -> None:
    """`HYPERSHELL_EXPORT_<NAME>` gives the task variable `<NAME>` as written, value unchanged."""
    name, value = AS_WRITTEN[case]
    rc, seen, stderr = config_repr(temp_site, f'dict(load_task_env()).get({name!r}, MISSING)', TASK_ENV,
                                   **{f'HYPERSHELL_EXPORT_{name}': value})
    assert rc == exit_status.success, stderr
    assert seen == repr(value)


@mark.integration
def test_export_reaches_task(temp_site: Path) -> None:
    """A task run by `hs cluster` sees each exported variable by its name and value as written."""
    site = Path(str(temp_site))
    taskfile = create_taskfile(site, ['printenv RUN_ID; printenv MyVar; printenv EMPTY > /dev/null && echo empty-set'])
    env = child_env(site, HYPERSHELL_EXPORT_RUN_ID='007', HYPERSHELL_EXPORT_MyVar='x', HYPERSHELL_EXPORT_EMPTY='')
    status, stdout, stderr = run_bounded(site, ['hs', 'cluster', '--no-db', '-N1', str(taskfile)], env)
    assert status == exit_status.success, stderr
    assert stdout.splitlines() == ['007', 'x', 'empty-set']


@mark.integration
def test_export_environment_over_file(temp_site: Path) -> None:
    """For the same task variable, the environment's value wins over a file's."""
    site = Path(str(temp_site))
    config = site / 'config.toml'
    config.write_text('[export]\nfoo = "file"\n', encoding='utf-8')
    rc, seen, stderr = config_repr(site, "dict(load_task_env()).get('FOO', MISSING)", TASK_ENV,
                                   HYPERSHELL_CONFIG_FILE=str(config), HYPERSHELL_EXPORT_FOO='env')
    assert rc == exit_status.success, stderr
    assert seen == repr('env')


@mark.integration
def test_export_file_table_as_before(temp_site: Path) -> None:
    """A file's `[export]` names are upper-cased and its lists joined, as documented."""
    site = Path(str(temp_site))
    config = site / 'config.toml'
    config.write_text('[export]\nlst = ["a", "b"]\nnum = 7\n', encoding='utf-8')
    rc, seen, stderr = config_repr(site, "sorted(load_task_env().items())", TASK_ENV,
                                   HYPERSHELL_CONFIG_FILE=str(config))
    assert rc == exit_status.success, stderr
    assert seen == repr([('LST', f'a{PATH_SEP}b'), ('NUM', '7')])


KEY_VARIABLES: Final[Dict[str, Dict[str, str]]] = {
    'spelling': {'HYPERSHELL_Server_Auth': KEY},
    'env': {'HYPERSHELL_SERVER_AUTH_ENV': 'ENV_EXPORT_TEST_KEY', 'ENV_EXPORT_TEST_KEY': KEY},
    'eval': {'HYPERSHELL_server_auth_eval': f'echo {KEY}'},
    'relay': {'HYPERSHELL_relay_upstream_auth': KEY},
    'relay-env': {'HYPERSHELL_RELAY_UPSTREAM_AUTH_ENV': 'ENV_EXPORT_TEST_KEY', 'ENV_EXPORT_TEST_KEY': KEY},
    'relay-eval': {'HYPERSHELL_Relay_Upstream_Auth_Eval': f'echo {KEY}'},
}
"""Each key variable form the matcher recognises, given in the environment (with any variable it names)."""


def key_variable(case: str) -> str:
    """The key variable of `case` (the `HYPERSHELL_` name among its variables)."""
    name, = [name for name in KEY_VARIABLES[case] if name.startswith('HYPERSHELL_')]
    return name


@mark.integration
@mark.parametrize('case', list(KEY_VARIABLES), ids=list(KEY_VARIABLES))
def test_key_variable_forms_not_inherited(temp_site: Path, case: str) -> None:
    """A key variable, in any form the matcher recognises, is not part of the environment a task inherits."""
    name = key_variable(case)
    rc, seen, stderr = config_repr(temp_site, f"sorted(name for name in inherited_task_env() "
                                              f"if name in ({name!r}, 'ENV_EXPORT_TEST_MARK'))",
                                   'from hypershell.client import inherited_task_env',
                                   ENV_EXPORT_TEST_MARK='x', **KEY_VARIABLES[case])
    assert rc == exit_status.success, stderr
    assert seen == repr(['ENV_EXPORT_TEST_MARK'])


@mark.integration
@mark.parametrize('case', list(KEY_VARIABLES), ids=list(KEY_VARIABLES))
def test_key_variable_forms_not_exported(temp_site: Path, case: str) -> None:
    """A key variable, in any form the matcher recognises, is not among the `--ssh --env` exports."""
    name = key_variable(case)
    rc, seen, stderr = config_repr(temp_site, f"sorted(word.split('=', 1)[0] for word in "
                                              f"ssh_env_exports(dict(os.environ)) "
                                              f"if word.startswith(({name!r} + '=', 'HYPERSHELL_SITE=')))",
                                   'import os\nfrom hypershell.cluster.launch import ssh_env_exports',
                                   **KEY_VARIABLES[case])
    assert rc == exit_status.success, stderr
    assert seen == repr(['HYPERSHELL_SITE'])
