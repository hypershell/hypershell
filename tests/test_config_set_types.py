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
from typing import Any, Dict, Final, Optional, Tuple

# Standard libs
import os
import sys
import json
import codecs
import tomllib
import functools
from pathlib import Path
from subprocess import run, DEVNULL

# External libs
import pytest
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
    """A key that takes text holds VALUE as a TOML string exactly as given."""
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
    """A whole-number, number, boolean, `relay.min`, zone-size or `logging.file` key holds the value as read."""
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
    """A value that does not convert exits 2 naming the key, the form and the value, and writes nothing."""
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
    """A duration or size key holds a TOML number for a finite number and the text otherwise."""
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
    """A path with no type of its own holds a TOML integer, float or boolean when VALUE is one, else text."""
    site = Path(str(temp_site))
    path, value, expected = UNTYPED[case]
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr
    assert same(written(site / 'config.toml', path), expected)


@mark.integration
def test_one_word_path_refused(temp_site) -> None:
    """A one-word path is still refused as before, and writes nothing."""
    site = Path(str(temp_site))
    rc, stderr = config_set(site, 'database', 'x')
    assert rc == exit_status.bad_argument and 'Missing section in variable path' in stderr, stderr
    assert not (site / 'config.toml').exists()


@mark.integration
def test_auth_digits_read_back(temp_site) -> None:
    """An all-digit key written by `hs config set` reads back as written, leading zeros kept."""
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


def scope_file(site: Path, scope: str, **extra: str) -> Path:
    """The configuration file `hs config set` writes for `scope` in `site`."""
    return Path(run(['hs', 'config', scope], cwd=site, env=set_env(site, **extra), stdin=DEVNULL,
                    capture_output=True, text=True, timeout=DEADLINE).stdout.strip())


NOT_UTF8: Final[Dict[str, Tuple[str, str]]] = {
    'untyped': ('database.pragmas.x', 'a\udcff'),
    'text': ('logging.datefmt', 'a\udcff'),
    'variant': ('server.auth_env', '\udcffKEY'),
    'export': ('export.RUN_ID', '0\udcff7'),
}
"""A key of each kind that takes text, and a VALUE with a byte that is not UTF-8 (a child receives 0xFF)."""


@mark.integration
@mark.parametrize('scope', ['--local', '--user'])
@mark.parametrize('case', list(NOT_UTF8))
def test_not_utf8_refused(temp_site, scope: str, case: str) -> None:
    """A VALUE that cannot be written as UTF-8 exits 2 naming the key; the file and its backups are as before."""
    site = Path(str(temp_site))
    home = site / 'home'
    home.mkdir()
    file = scope_file(site, scope, HOME=str(home))
    assert file.is_relative_to(site), file
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(SEED)
    before = sorted(p.name for p in file.parent.glob('.config.*.toml'))
    path, value = NOT_UTF8[case]
    rc, stderr = config_set(site, path, value, scope, HOME=str(home))
    assert rc == exit_status.bad_argument, stderr
    assert f'{path}: expected text that can be written as UTF-8' in stderr, stderr
    assert 'traceback' not in stderr.lower(), stderr
    assert file.read_bytes() == SEED.encode()
    assert sorted(p.name for p in file.parent.glob('.config.*.toml')) == before
    assert not list(site.rglob('exception-*.log'))


@mark.integration
def test_not_utf8_path_refused(temp_site) -> None:
    """A path that cannot be written as UTF-8 exits 2 and leaves the file as it was."""
    site = Path(str(temp_site))
    (site / 'config.toml').write_text(SEED)
    rc, stderr = config_set(site, 'database.pragmas.\udcff', 'x')
    assert rc == exit_status.bad_argument, stderr
    assert 'cannot be written as UTF-8' in stderr and 'traceback' not in stderr.lower(), stderr
    assert (site / 'config.toml').read_bytes() == SEED.encode()
    assert not list(site.glob('.config.*.toml'))
    assert not list(site.rglob('exception-*.log'))


KEY_FORMS: Final[Tuple[str, ...]] = ('server.auth', 'server.auth_env', 'relay.upstream.auth', 'relay.upstream.auth_eval')
"""Paths that give a key, by any of its forms."""

UNSEEN: Final[str] = 'Wq7unseenword'
"""Text in a refused VALUE for a key."""


@mark.integration
@mark.parametrize('path', KEY_FORMS)
def test_key_refusal_names_key(temp_site, path: str) -> None:
    """A refused VALUE for a key exits 2, and the message names the key; the file is as before."""
    site = Path(str(temp_site))
    (site / 'config.toml').write_text(SEED)
    rc, stderr = config_set(site, path, f'{UNSEEN}\udcff')
    assert rc == exit_status.bad_argument, stderr
    assert f'{path}: expected text that can be written as UTF-8' in stderr, stderr
    assert UNSEEN not in stderr and 'traceback' not in stderr.lower(), stderr
    assert (site / 'config.toml').read_bytes() == SEED.encode()


@mark.integration
def test_other_refusal_names_value(temp_site) -> None:
    """A refused VALUE for any other key is named in the message."""
    site = Path(str(temp_site))
    rc, stderr = config_set(site, 'logging.datefmt', f'{UNSEEN}\udcff')
    assert rc == exit_status.bad_argument and UNSEEN in stderr, stderr


LATIN1_LOCALES: Final[Tuple[str, ...]] = ('en_US.ISO8859-1', 'en_US.ISO-8859-1', 'en_US.iso88591', 'de_DE.ISO8859-1',
                                          'de_DE.iso88591', 'C.ISO-8859-1')
"""Names a Latin-1 locale goes by on common platforms."""


@functools.cache
def latin1_locale() -> Optional[str]:
    """A locale under which a child interpreter opens files as Latin-1, or None if this host has none."""
    for name in LATIN1_LOCALES:
        result = run([sys.executable, '-c', 'import locale; print(locale.getencoding())'], stdin=DEVNULL,
                     env={**os.environ, 'LC_ALL': name, 'PYTHONUTF8': '0'}, capture_output=True, text=True,
                     timeout=DEADLINE)
        try:
            if result.returncode == 0 and codecs.lookup(result.stdout.strip()).name == 'iso8859-1':
                return name
        except LookupError:
            pass
    return None


SET_IN_CHILD: Final[str] = 'import sys, json, hypershell; sys.exit(hypershell.main(json.loads(sys.argv[1])))'
"""Program a child runs: `hs` with the arguments given as JSON, so a VALUE reaches it as text on any platform."""


def config_set_latin1(site: Path, path: str, value: str) -> Tuple[int, str]:
    """Run `hs config set PATH VALUE --local` in `site` under a Latin-1 locale; return its status and stderr."""
    if (name := latin1_locale()) is None:
        pytest.skip('no Latin-1 locale on this host')
    argv = json.dumps(['config', 'set', path, value, '--local'])
    result = run([sys.executable, '-c', SET_IN_CHILD, argv], cwd=site, stdin=DEVNULL, capture_output=True,
                 env=set_env(site, LC_ALL=name, PYTHONUTF8='0'), timeout=DEADLINE)
    return result.returncode, result.stderr.decode('latin-1').strip()


@mark.integration
def test_latin1_locale_written_as_utf8(temp_site) -> None:
    """Under a Latin-1 locale a value Latin-1 cannot encode is written as UTF-8; nothing else in the file is lost."""
    site = Path(str(temp_site))
    (site / 'config.toml').write_text(SEED)
    rc, stderr = config_set_latin1(site, 'logging.datefmt', '\u20ac')
    assert rc == exit_status.success, stderr
    assert tomllib.loads((site / 'config.toml').read_bytes().decode('utf-8')) == {
        'server': {'port': 54321}, 'logging': {'datefmt': '\u20ac'}}
    assert [p.read_bytes() for p in site.glob('.config.*.toml')] == [SEED.encode()]
    assert not list(site.rglob('exception-*.log'))


@mark.integration
def test_latin1_locale_read_back(temp_site) -> None:
    """A value Latin-1 can encode, set under a Latin-1 locale, is read by later commands as given."""
    site = Path(str(temp_site))
    (site / 'config.toml').write_text(SEED)
    rc, stderr = config_set_latin1(site, 'logging.datefmt', '\u00e9')
    assert rc == exit_status.success, stderr
    rc, stderr = config_set_latin1(site, 'database.pragmas.journal_mode', 'wal')
    assert rc == exit_status.success, stderr
    for path, expected in (('logging.datefmt', '\u00e9'), ('database.pragmas.journal_mode', 'wal')):
        result = run(['hs', 'config', 'get', '-r', path], cwd=site, env=set_env(site), stdin=DEVNULL,
                     capture_output=True, text=True, timeout=DEADLINE)
        assert result.returncode == exit_status.success, result.stderr
        assert json.loads(f'"{result.stdout.strip()}"') == expected, result.stdout  # printed as JSON text
