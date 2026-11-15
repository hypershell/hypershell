# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Task variables given by `HYPERSHELL_EXPORT_<NAME>`, and the key variable in any letter case.

A task sees `<NAME>` as written, with the variable's value unchanged; a file's `[export]` table is
upper-cased and its lists joined, and the environment's value wins for the same task variable.
Every name the configuration reads as `server.auth` is recognised as the key variable by the client
and by `--ssh --env`.
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


@mark.integration
def test_key_variable_any_case_not_inherited(temp_site: Path) -> None:
    """The key variable, in any letter case, is not part of the environment a task inherits."""
    rc, seen, stderr = config_repr(temp_site, "[name for name in inherited_task_env() if name.upper() == "
                                              "'HYPERSHELL_SERVER_AUTH']",
                                   'from hypershell.client import inherited_task_env',
                                   HYPERSHELL_Server_Auth=KEY)
    assert rc == exit_status.success, stderr
    assert seen == repr([])


@mark.integration
def test_key_variable_any_case_not_exported(temp_site: Path) -> None:
    """The key variable, in any letter case, is not among the `--ssh --env` exports."""
    rc, seen, stderr = config_repr(temp_site, f"[word for word in ssh_env_exports(dict(os.environ)) "
                                              f"if {KEY!r} in word]",
                                   'import os\nfrom hypershell.cluster.launch import ssh_env_exports',
                                   HYPERSHELL_Server_Auth=KEY)
    assert rc == exit_status.success, stderr
    assert seen == repr([])
