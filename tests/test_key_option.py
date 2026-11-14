# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A key on a command line is refused, by every command and in every form, without repeating it.

This is the one file in the suite that gives a key option to a command, and it does so only to
see it refused; the key it passes is a dummy. Everywhere else a key comes from the environment
or a configuration file, which the sweep at the end of this file holds the repository to.
"""


# Type annotations
from __future__ import annotations
from typing import Callable, Dict, Final, List, Type

# Standard libs
import os
import re
import sqlite3
from pathlib import Path
from subprocess import run

# External libs
from pytest import mark, raises
from cmdkit.app import Application, exit_status
from cmdkit.cli import ArgumentError

# Internal libs
from hypershell.core.config import AUTH_ENV
from hypershell.client import ClientApp
from hypershell.server import ServerApp
from hypershell.submit import SubmitApp
from hypershell.cluster import ClusterApp
from hypershell.relay import RelayApp
from tests import create_taskfile


KEY: Final[str] = 'Dummy0Key0For0The0Refusal0Tests'
"""A key the parser must never repeat (well-formed, so nothing else would refuse it)."""

TASK_KEY: Final[str] = 'task-option-value'
"""A value of a task's own `-k`, which must reach the task unchanged."""

SERVER_KEY_SOURCES: Final[List[str]] = [AUTH_ENV, 'server.auth']
"""What the refusal names on the commands that read the queue key."""

APPS: Final[Dict[str, Type[Application]]] = {
    'client': ClientApp,
    'server': ServerApp,
    'submit': SubmitApp,
    'cluster': ClusterApp,
    'relay': RelayApp,
}

NAMED: Final[Dict[str, List[str]]] = {
    'client': SERVER_KEY_SOURCES,
    'server': SERVER_KEY_SOURCES,
    'submit': SERVER_KEY_SOURCES,
    'cluster': ['new key for every run'],
    'relay': ['HYPERSHELL_RELAY_UPSTREAM_AUTH', 'relay.upstream.auth'],
}
"""What each command's refusal names in place of the option."""

FORMS: Final[Dict[str, List[str]]] = {
    'short': ['-k', KEY],
    'short-joined': [f'-k{KEY}'],
    'short-equals': [f'-k={KEY}'],
    'long': ['--auth', KEY],
    'long-equals': [f'--auth={KEY}'],
    'short-bare': ['-k'],
    'long-bare': ['--auth'],
    'short-before-option': ['-k', '-N4'],
}
"""Every way the parser accepted the option before, on every command."""

EXTRA_FORMS: Final[Dict[str, Dict[str, List[str]]]] = {
    'client': {'long-prefix': ['--au', KEY], 'long-prefix-equals': [f'--aut={KEY}'],
               'after-bad-option': ['--bogus', '-k', KEY], 'before-bad-option': ['-k', KEY, '--bogus']},
    'server': {'with-file': ['-k', KEY, 'tasks.in'], 'file-first': ['tasks.in', f'--auth={KEY}', '--forever']},
    'submit': {'bundled': [f'-qk{KEY}', 'echo', 'hi'], 'after-task-words': ['echo', 'hi', '-k', KEY],
               'long-prefix-equals': [f'--a={KEY}'], 'queue': ['-q', '-k', KEY, 'echo', 'hi']},
    'cluster': {'with-file': ['tasks.in', '-k', KEY]},
    'relay': {'long-prefix': ['--au', KEY]},
}
"""Forms particular to one command (prefixes that are unambiguous there, positionals, bundled flags)."""


def refusal_cases() -> List[mark.ParameterSet]:
    """Every (command, form) pair the refusal must cover."""
    from pytest import param
    return [param(command, argv, id=f'{command}-{name}')
            for command in APPS
            for name, argv in {**FORMS, **EXTRA_FORMS[command]}.items()]


def assert_refusal(command: str, text: str) -> None:
    """The text is a refusal on `command` naming where the key goes, and it holds no key."""
    assert 'is not accepted' in text, text
    for name in NAMED[command]:
        assert name in text, text
    assert KEY not in text


@mark.unit
@mark.parametrize('command, argv', refusal_cases())
def test_parser_refuses_key_option(command: str, argv: List[str]) -> None:
    """The parser refuses the option as it meets it, naming where the key goes and never the key."""
    with raises(ArgumentError) as error:
        APPS[command].interface.parse_args(argv)
    assert_refusal(command, str(error.value))


@mark.unit
@mark.parametrize('command', list(APPS))
def test_refusal_names_option_given(command: str) -> None:
    """The refusal names the option string the parser matched, so a long prefix reads as the full option."""
    with raises(ArgumentError, match=r'^-k is not accepted'):
        APPS[command].interface.parse_args(['-k', KEY])
    with raises(ArgumentError, match=r'^--auth is not accepted'):
        APPS[command].interface.parse_args([f'--auth={KEY}'])


@mark.unit
def test_submit_refusal_points_a_task_option_past_separator() -> None:
    """On `hs submit` the refusal also says a task's own option goes after `--`."""
    with raises(ArgumentError, match=r'after "--"') as error:
        SubmitApp.interface.parse_args(['echo', 'hi', '-k', KEY])
    assert KEY not in str(error.value)


@mark.unit
@mark.parametrize('argv', [
    ['--', 'echo', '-k', TASK_KEY],
    ['echo', '--', '-k', TASK_KEY],
    ['-q', '--', 'echo', '-k', TASK_KEY],
])
def test_submit_task_option_after_separator(argv: List[str]) -> None:
    """A `-k` after `--` is the task's own and reaches its arguments unchanged."""
    namespace = SubmitApp.interface.parse_args(argv)
    assert namespace.task_args[-2:] == ['-k', TASK_KEY]
    assert not hasattr(namespace, 'key_option')


@mark.unit
@mark.parametrize('command, argv, name, value', [
    ('client', ['--template=-kfoo'], 'template', '-kfoo'),
    ('cluster', ['--ssh-args=-k -i x'], 'ssh_args', '-k -i x'),
    ('relay', ['--launcher-args=-k --exclusive'], 'launcher_args', '-k --exclusive'),
])
def test_option_value_holding_k_parses(command: str, argv: List[str], name: str, value: str) -> None:
    """An option's value that begins with `-k`, joined to its option with `=`, parses as before."""
    namespace = APPS[command].interface.parse_args(argv)
    assert getattr(namespace, name) == value


@mark.unit
@mark.parametrize('command', list(APPS))
def test_namespace_without_key_option(command: str) -> None:
    """With no key option given, parsing leaves no trace of one on the namespace."""
    namespace = APPS[command].interface.parse_args([])
    assert not hasattr(namespace, 'key_option') and not hasattr(namespace, 'auth')


INVOCATIONS: Final[Dict[str, List[str]]] = {
    'client': ['hs', 'client', '-N', '1', '-T', '5'],
    'server': ['hs', 'server', '--forever'],
    'submit': ['hs', 'submit', '-q', 'echo', 'hi'],
    'cluster': ['hs', 'cluster', 'tasks.in'],
    'hsx': ['hsx', 'tasks.in'],
    'relay': ['hs', 'relay', '-N', '1'],
}
"""A run of each command that would start work if the option did not stop it."""

GOAL_FORMS: Final[Dict[str, List[str]]] = {
    'short': ['-k', KEY],
    'short-joined': [f'-k{KEY}'],
    'long': ['--auth', KEY],
    'long-equals': [f'--auth={KEY}'],
}


def files_under(site: Path) -> List[Path]:
    """Every regular file under the site."""
    return [path for path in site.rglob('*') if path.is_file()]


@mark.integration
@mark.parametrize('form', list(GOAL_FORMS))
@mark.parametrize('invocation', list(INVOCATIONS))
def test_command_refuses_key_option(temp_site: Path, invocation: str, form: str) -> None:
    """The command exits with a usage error naming where the key goes; no output or log holds the key.

    The log file is enabled at the most verbose level, and nothing is started: no database file
    is created, and the refusal is the only record the command leaves.
    """
    site = Path(str(temp_site))
    create_taskfile(site, ['echo hi'], filename='tasks.in')
    env = {**os.environ, 'HYPERSHELL_LOGGING_FILE': 'enabled', 'HYPERSHELL_LOGGING_FILE_LEVEL': 'devel',
           'HYPERSHELL_LOGGING_LEVEL': 'devel'}
    argv = INVOCATIONS[invocation]
    result = run([*argv, *GOAL_FORMS[form]], cwd=site, env=env, capture_output=True, text=True, timeout=60)
    command = 'cluster' if invocation == 'hsx' else invocation
    assert result.returncode == exit_status.bad_argument, result.stderr
    assert_refusal(command, result.stderr)
    assert KEY not in result.stdout
    files = files_under(site)
    assert not [path for path in files if KEY in path.read_text(errors='replace')]
    assert not [path for path in files if path.suffix == '.db' or path.name.endswith('.db-journal')]
    logs = [path for path in files if path.suffix == '.log']
    assert logs and all('is not accepted' in path.read_text() for path in logs), logs


@mark.integration
def test_submit_task_option_reaches_task(temp_site: Path) -> None:
    """A `-k` after `--` and a `-k` in a task file are stored as the tasks' own arguments."""
    site = Path(str(temp_site))
    taskfile = create_taskfile(site, [f'echo -k {TASK_KEY}-file'])
    for argv in (['hs', 'submit', '--initdb', '--', 'echo', '-k', TASK_KEY],
                 ['hs', 'submit', '--initdb', '-f', str(taskfile)]):
        result = run(argv, cwd=site, env=os.environ, capture_output=True, text=True, timeout=60)
        assert result.returncode == exit_status.success, result.stderr
    with sqlite3.connect(os.environ['HYPERSHELL_DATABASE_FILE']) as db:
        stored = sorted(args for args, in db.execute('SELECT args FROM task'))
    assert stored == [f'echo -k {TASK_KEY}', f'echo -k {TASK_KEY}-file']


# The sweep: no help text, message, completion, document, test or drive offers the option.

REPO: Final[Path] = Path(__file__).resolve().parent.parent

KEY_OPTION: Final[re.Pattern] = re.compile(r'(^|[^\w-])-k([^\w-]|$)|--auth([^\w-]|$)')
"""Either option string standing as a word."""

SWEPT: Final[List[str]] = ['src', 'docs', 'share', 'tests', 'README.rst']
SWEPT_DRIVES: Final[str] = 'spec/*/drive/*'
SKIPPED_DIRS: Final[List[str]] = ['docs/_build', 'share/man', '__pycache__']
TEXT_SUFFIXES: Final[List[str]] = ['.py', '.rst', '.txt', '.md', '.toml', '.cfg', '.sh', '']

ALLOWED: Final[Dict[str, Callable[[str], bool]]] = {
    # Negative assertions: a launched client's argv holds no key option.
    'tests/test_cluster_launch.py': lambda line: 'KEY_OPTIONS' in line or re.search(r"'-k': True, '--auth': True", line),
    'tests/test_relay_launch.py': lambda line: True,
    'spec/relay/drive/relay_probe.py': lambda line: True,
    # A guard that masks the word after either option in a logged launch command.
    'src/hypershell/cluster/launch.py': lambda line: "'***'" in line or 'redact_secrets' in line or "['-k', '--auth']" in line,
    # The option strings registered only to be refused.
    'src/hypershell/core/config.py': lambda line: 'KEY_OPTION_STRINGS' in line,
    # pytest's own -k.
    'tests/test_factory_fsm.py': lambda line: True,
    # This file passes the option only to see it refused.
    'tests/test_key_option.py': lambda line: True,
}
"""Files (and, where given, the lines in them) that name the option only to forbid or refuse it."""


def swept_files() -> List[Path]:
    """Every text file the sweep reads."""
    files = []
    for name in SWEPT:
        root = REPO / name
        candidates = [root] if root.is_file() else root.rglob('*')
        files.extend(path for path in candidates if path.is_file() and not path.is_symlink())
    files.extend(path for path in REPO.glob(SWEPT_DRIVES) if path.is_file())
    return [path for path in files
            if not any(part in path.relative_to(REPO).as_posix() for part in SKIPPED_DIRS)
            and (path.suffix in TEXT_SUFFIXES or path.name in ('hs', '_hs'))]


@mark.unit
def test_sweep_finds_no_key_option() -> None:
    """No source, document, completion, test or drive offers the option outside the allow list."""
    hits = []
    for path in swept_files():
        relative = path.relative_to(REPO).as_posix()
        allowed = ALLOWED.get(relative, lambda line: False)
        for number, line in enumerate(path.read_text(errors='replace').splitlines(), start=1):
            if KEY_OPTION.search(line) and not allowed(line):
                hits.append(f'{relative}:{number}: {line.strip()}')
    assert not hits, '\n'.join(hits)
