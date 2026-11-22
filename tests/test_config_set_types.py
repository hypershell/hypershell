# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs config set` writes each value by its key's type, as the environment layer reads it.

Text keys keep the text as written; whole-number, number and boolean keys convert or are refused
with exit 2 and nothing written; durations and sizes take a number when the text is one; a path
with no type of its own takes TOML's own scalars. Every test drives `hs config set` in a child
process, since the configuration paths are fixed when HyperShell is imported, and reads the file
back with `tomllib`.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Final, Tuple

# Standard libs
import os
import tomllib
from pathlib import Path
from subprocess import run, DEVNULL

# External libs
from pytest import mark
from cmdkit.app import exit_status


DEADLINE: Final[int] = 60
"""Seconds a command may take before the test stops it and fails."""

ABSENT: Final[str] = '<absent>'
"""What `written()` gives for a path the file does not hold."""

SEED: Final[str] = '[server]\nport = 54321\n'
"""A file that a refused value must leave byte for byte as it was."""


def set_env(site: Path, **extra: str) -> Dict[str, str]:
    """The environment of a child in `site`, its local configuration file in the site."""
    return {**os.environ, 'HYPERSHELL_SITE': str(site), 'HYPERSHELL_CONFIG_FILE': str(site / 'config.toml'), **extra}


def config_set(site: Path, path: str, value: str, scope: str = '--local', **extra: str) -> Tuple[int, str]:
    """Run `hs config set PATH VALUE SCOPE` in `site`; return its status and stderr."""
    result = run(['hs', 'config', 'set', path, value, scope], cwd=site, env=set_env(site, **extra),
                 stdin=DEVNULL, capture_output=True, text=True, timeout=DEADLINE)
    return result.returncode, result.stderr.strip()


def written(file: Path, path: str) -> Any:
    """The value at dotted `path` in the TOML `file`, or ABSENT."""
    node: Any = tomllib.loads(file.read_text())
    for name in path.split('.'):
        if not isinstance(node, dict) or name not in node:
            return ABSENT
        node = node[name]
    return node


def same(value: Any, expected: Any) -> bool:
    """Equal and of the same type (`1 == True` and `1 == 1.0` are not the same here)."""
    return type(value) is type(expected) and value == expected


TEXT: Final[Dict[str, Tuple[str, str]]] = {
    'auth-digits': ('server.auth', '0012345678901234'),
    'launcher-false': ('autoscale.launcher', 'false'),
    'export-007': ('export.RUN_ID', '007'),
    'datefmt-null': ('logging.datefmt', 'null'),
    'format-None': ('logging.format', 'None'),
    'level-True': ('logging.level', 'True'),
    'variant-env-007': ('server.auth_env', '007'),
    'variant-eval-true': ('database.password_eval', 'true'),
    'nodelist-group': ('ssh.nodelist.gpu', 'TRUE'),
    'zone-launcher': ('relay.zone.gpu.launcher', 'false'),
    'zone-launcher-args': ('relay.zone.gpu.launcher_args', '1e3'),
    'export-empty': ('export.EMPTY', ''),
    'bind-empty': ('server.bind', ''),
}


@mark.integration
@mark.parametrize('case', list(TEXT))
def test_text_kept(temp_site, case: str) -> None:
    """A key that takes text holds VALUE as a TOML string exactly as given (R1)."""
    site = Path(str(temp_site))
    path, value = TEXT[case]
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr
    assert same(written(site / 'config.toml', path), value)


TYPED: Final[Dict[str, Tuple[str, str, Any]]] = {
    'eager-no': ('server.eager', 'no', False),
    'eager-On': ('server.eager', 'On', True),
    'eager-1': ('server.eager', '1', True),
    'eager-false': ('server.eager', 'false', False),
    'port': ('server.port', '8080', 8080),
    'port-underscore': ('server.port', '1_000', 1000),
    'factor': ('autoscale.factor', '2.5', 2.5),
    'ratelimit-exp': ('client.ratelimit', '1e3', 1000.0),
    'relay-min-none': ('relay.min', '<none>', '<none>'),
    'relay-min': ('relay.min', '3', 3),
    'zone-size': ('relay.zone.gpu.size', '4', 4),
    'logging-file-enabled': ('logging.file', 'enabled', True),
    'logging-file-TRUE': ('logging.file', 'TRUE', True),
    'logging-file-path': ('logging.file', '/tmp/x.log', '/tmp/x.log'),
}


@mark.integration
@mark.parametrize('case', list(TYPED))
def test_typed_converted(temp_site, case: str) -> None:
    """A whole-number, number, boolean, `relay.min`, zone-size or `logging.file` key holds the value as read (R2)."""
    site = Path(str(temp_site))
    path, value, expected = TYPED[case]
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr
    assert same(written(site / 'config.toml', path), expected)


REFUSED: Final[Dict[str, Tuple[str, str, str]]] = {
    'port-abc': ('server.port', 'abc', 'a whole number'),
    'port-fraction': ('server.port', '1.5', 'a whole number'),
    'port-empty': ('server.port', '', 'a whole number'),
    'factor-inf': ('autoscale.factor', 'inf', 'a number'),
    'factor-nan': ('autoscale.factor', 'nan', 'a number'),
    'eager-maybe': ('server.eager', 'maybe', 'a boolean'),
    'eager-empty': ('server.eager', '', 'a boolean'),
    'relay-min-none': ('relay.min', 'none', 'a whole number or <none>'),
    'zone-size-x': ('relay.zone.gpu.size', 'x', 'a whole number'),
    'logging-file-false': ('logging.file', 'false', 'true, enabled, or a path'),
    'logging-file-empty': ('logging.file', '', 'true, enabled, or a path'),
}


@mark.integration
@mark.parametrize('case', list(REFUSED))
def test_typed_refused(temp_site, case: str) -> None:
    """A value that does not convert exits 2 naming the key, the form and the value, and writes nothing (R2)."""
    site = Path(str(temp_site))
    path, value, form = REFUSED[case]
    (site / 'config.toml').write_text(SEED)
    before = sorted(p.name for p in site.glob('.config.*.toml'))
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.bad_argument, stderr
    assert f'{path}: expected {form}' in stderr and repr(value) in stderr, stderr
    assert 'traceback' not in stderr.lower(), stderr
    assert (site / 'config.toml').read_text() == SEED
    assert sorted(p.name for p in site.glob('.config.*.toml')) == before
    assert not list(site.rglob('exception-*.log'))


SIZED: Final[Dict[str, Tuple[str, str, Any]]] = {
    'evict-600': ('server.evict', '600', 600),
    'evict-half': ('server.evict', '0.5', 0.5),
    'evict-10m': ('server.evict', '10m', '10m'),
    'memory-16G': ('client.memory', '16G', '16G'),
    'evict-inf': ('server.evict', 'inf', 'inf'),
    'evict-True': ('server.evict', 'True', 'True'),
    'evict-null': ('server.evict', 'null', 'null'),
}


@mark.integration
@mark.parametrize('case', list(SIZED))
def test_duration_size(temp_site, case: str) -> None:
    """A duration or size key holds a TOML number for a finite number and the text otherwise (R3)."""
    site = Path(str(temp_site))
    path, value, expected = SIZED[case]
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr
    assert same(written(site / 'config.toml', path), expected)


UNTYPED: Final[Dict[str, Tuple[str, str, Any]]] = {
    'pragma-wal': ('database.pragmas.journal_mode', 'wal', 'wal'),
    'connect-int': ('database.connect_args.timeout', '5', 5),
    'connect-underscore': ('database.connect_args.timeout', '1_000', 1000),
    'pragma-007': ('database.pragmas.cache_size', '007', '007'),
    'connect-True': ('database.connect_args.check', 'True', 'True'),
    'pragma-null': ('database.pragmas.foo', 'null', 'null'),
    'connect-hex': ('database.connect_args.mask', '0x1F', 31),
    'pragma-dot5': ('database.pragmas.ratio', '.5', '.5'),
    'provider-true': ('database.sslmode', 'true', True),
    'closed-unknown-none': ('server.nothing', 'none', 'none'),
    'section-case': ('Server.eager', 'yes', 'yes'),
    'below-key': ('server.port.x', '5', 5),
    'unknown-empty': ('foo.bar', '', ''),
    'zone-unknown-key': ('relay.zone.gpu.color', 'false', False),
}


@mark.integration
@mark.parametrize('case', list(UNTYPED))
def test_untyped(temp_site, case: str) -> None:
    """A path with no type of its own holds a TOML integer, float or boolean when VALUE is one, else text (R4)."""
    site = Path(str(temp_site))
    path, value, expected = UNTYPED[case]
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr
    assert same(written(site / 'config.toml', path), expected)


@mark.integration
def test_one_word_path_refused(temp_site) -> None:
    """A one-word path is still refused as before, and writes nothing (Non-goals)."""
    site = Path(str(temp_site))
    rc, stderr = config_set(site, 'database', 'x')
    assert rc == exit_status.bad_argument and 'Missing section in variable path' in stderr, stderr
    assert not (site / 'config.toml').exists()


@mark.integration
def test_auth_digits_read_back(temp_site) -> None:
    """An all-digit key written by `hs config set` reads back as written, leading zeros kept (R1)."""
    site = Path(str(temp_site))
    rc, stderr = config_set(site, 'server.auth', '0012345678901234')
    assert rc == exit_status.success, stderr
    result = run(['hs', 'config', 'get', '-r', 'server.auth'], cwd=site, env=set_env(site), stdin=DEVNULL,
                 capture_output=True, text=True, timeout=DEADLINE)
    assert result.stdout.strip() == '0012345678901234', result.stderr


@mark.integration
@mark.parametrize('scope', ['--user', '--local'])
def test_scope(temp_site, scope: str) -> None:
    """The rule holds for every scope (`--system` needs privileges and is not driven here)."""
    site = Path(str(temp_site))
    home = site / 'home'
    home.mkdir()
    rc, stderr = config_set(site, 'export.RUN_ID', '007', scope, HOME=str(home))
    assert rc == exit_status.success, stderr
    where = run(['hs', 'config', scope], cwd=site, env=set_env(site, HOME=str(home)), stdin=DEVNULL,
                capture_output=True, text=True, timeout=DEADLINE).stdout.strip()
    assert Path(where).is_relative_to(site), where
    assert same(written(Path(where), 'export.RUN_ID'), '007')
