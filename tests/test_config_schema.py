# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
The declared configuration keys, kept in step with the defaults and the parameter reference, and
the rules by which `HYPERSHELL_*` variables reach them.

A documented key cannot land without its kind, and the reference cannot name a key the loader does
not know: the sync tests compare `hypershell.core.config_schema` with `default` and with
`docs/_include/config_param_ref.rst`. The rest are unit tests of the matcher and the kind readers.
"""


# Type annotations
from __future__ import annotations
from typing import Any, Dict, Iterator, Optional, Tuple

# Standard libs
import re
from pathlib import Path

# External libs
import pytest
from pytest import mark

# Internal libs
from hypershell.core.config import default
from hypershell.core.config_schema import (Kind, TableKind, KEYS, TABLES, ZONE_KEYS, TIMESCALE_KEYS, PROSE_KEYS,
                                           SCALAR_TABLES, PROCESS_VARIABLES, VARIANTS, EMPTY_UNSET, VariableError,
                                           read_value, read_untyped, match_name, match_path, read_environment,
                                           variable_name)


REPO = Path(__file__).resolve().parent.parent
REFERENCE = REPO / 'docs' / '_include' / 'config_param_ref.rst'
ENTRY = re.compile(r'^(?P<indent>(?: {4})*)``(?P<term>\[[a-z_]+\]|\.[a-z_]+|\*)``\s*$')


def parse_reference() -> Dict[str, Tuple[str, str]]:
    """Each entry of the parameter reference by dotted path: `key` or `table`, and its body text."""
    entries: Dict[str, Tuple[str, str]] = {}
    stack, lines, current = [], REFERENCE.read_text(encoding='utf-8').splitlines(), None
    bodies: Dict[str, list] = {}
    for line in lines:
        if match := ENTRY.match(line):
            depth = len(match['indent']) // 4
            term = match['term']
            stack = stack[:depth] + [term.strip('[].')]
            current = '.'.join(stack)
            entries[current] = ('table' if term.startswith('[') else 'key', '')
            bodies[current] = []
            for parent in range(1, len(stack)):
                bodies['.'.join(stack[:parent])].append(line)
        elif current is not None:
            for parent in range(1, len(stack) + 1):
                bodies['.'.join(stack[:parent])].append(line)
    return {path: (kind, '\n'.join(bodies[path])) for path, (kind, _) in entries.items()}


def default_leaves(tree: dict, path: Tuple[str, ...] = ()) -> Iterator[Tuple[str, Any]]:
    """Each leaf of `tree` by dotted path; an empty table is a leaf holding `{}`."""
    for name, value in tree.items():
        if isinstance(value, dict) and value:
            yield from default_leaves(value, (*path, name))
        else:
            yield '.'.join((*path, name)), value


@mark.unit
def test_defaults_declared() -> None:
    """Every default is a declared key whose kind reads the default's text back as the default."""
    for path, value in default_leaves(default):
        if value == {}:
            assert TABLES.get(path) in (TableKind.OPEN, TableKind.EXPORT), path
            continue
        assert path in KEYS, path
        assert read_value(KEYS[path], str(value)) == value, path


@mark.unit
def test_reference_entries_declared() -> None:
    """The reference's entries are the declared keys and tables, less those it names in prose."""
    reference = parse_reference()
    keys = {path for path, (kind, _) in reference.items() if kind == 'key' and not path.endswith('*')}
    tables = {path for path, (kind, _) in reference.items() if kind == 'table'}
    assert keys == set(KEYS) - set(PROSE_KEYS) - set(SCALAR_TABLES)
    assert tables == set(TABLES) - {'', 'relay.zone'}
    assert 'relay.zone' in keys and 'database.*' in reference


@mark.unit
def test_prose_keys_named() -> None:
    """Each key the reference gives only in prose is named in its table's text, as are the zone keys."""
    reference = parse_reference()
    for path in PROSE_KEYS:
        table, name = path.rsplit('.', 1)
        assert f'``{name}``' in reference[table][1], path
    for name in ZONE_KEYS:
        assert f'``{name}``' in reference['relay.zone'][1], name


@mark.unit
def test_code_constants() -> None:
    """The zone and timescale keys are the ones their readers take."""
    from hypershell.relay import ZONE_OPTIONS
    from hypershell.data.timescale import SETTINGS_KEYS
    assert tuple(ZONE_KEYS) == ZONE_OPTIONS
    assert set(TIMESCALE_KEYS) == set(SETTINGS_KEYS)


@mark.unit
def test_names_unique() -> None:
    """No two keys, or a key and another key's variant, share a variable name; no table name is a key's."""
    names = [variable_name(path) for path in KEYS]
    variants = [variable_name(path) + suffix.upper() for path in KEYS for suffix in VARIANTS]
    assert len(set(names)) == len(names)
    assert not set(names) & set(variants)
    keys_too = {variable_name(path) for path in (*SCALAR_TABLES, 'relay.zone')}
    assert not ({variable_name(path) for path in TABLES if path} - keys_too) & set(names)


@mark.unit
def test_zone_keys_suffix_unique() -> None:
    """No zone key's name ends in another's, so a zone variable's key is found one way only."""
    for key in ZONE_KEYS:
        for other in ZONE_KEYS:
            assert key == other or not key.endswith('_' + other), (key, other)


BOOLEANS: Dict[str, bool] = {
    'true': True, 'Yes': True, 'ON': True, '1': True, ' true ': True,
    'false': False, 'NO': False, 'off': False, '0': False,
}


@mark.unit
@mark.parametrize('text', list(BOOLEANS))
def test_read_boolean(text: str) -> None:
    assert read_value(Kind.BOOL, text) is BOOLEANS[text]


READS: Dict[str, Tuple[Kind, str, Any]] = {
    'text-false': (Kind.TEXT, 'false', 'false'),
    'text-empty': (Kind.TEXT, '', ''),
    'text-zeros': (Kind.TEXT, '007', '007'),
    'text-space': (Kind.TEXT, ' a b ', ' a b '),
    'int': (Kind.INT, '50001', 50001),
    'int-zeros': (Kind.INT, '007', 7),
    'int-sign-underscore': (Kind.INT, '+5_000', 5000),
    'number-int': (Kind.NUMBER, '2', 2),
    'number-float': (Kind.NUMBER, '2.5', 2.5),
    'number-exponent': (Kind.NUMBER, '1e3', 1000.0),
    'int-or-none-int': (Kind.INT_OR_NONE, '4', 4),
    'int-or-none-none': (Kind.INT_OR_NONE, '<none>', '<none>'),
    'duration-int': (Kind.DURATION, '7', 7),
    'duration-float': (Kind.DURATION, '0.5', 0.5),
    'duration-units': (Kind.DURATION, '1h30m', '1h30m'),
    'duration-bad': (Kind.DURATION, '5MB', '5MB'),
    'size-int': (Kind.SIZE, '0', 0),
    'size-units': (Kind.SIZE, '16G', '16G'),
    'logging-file-enabled': (Kind.LOGGING_FILE, 'Enabled', True),
    'logging-file-true': (Kind.LOGGING_FILE, 'TRUE', True),
    'logging-file-path': (Kind.LOGGING_FILE, '/tmp/x.log', '/tmp/x.log'),
}


@mark.unit
@mark.parametrize('case', list(READS))
def test_read_value(case: str) -> None:
    kind, text, expected = READS[case]
    value = read_value(kind, text)
    assert value == expected and type(value) is type(expected)


REFUSED: Dict[str, Tuple[Kind, str]] = {
    'bool-maybe': (Kind.BOOL, 'maybe'),
    'bool-empty': (Kind.BOOL, ''),
    'int-fraction': (Kind.INT, '1.0'),
    'int-exponent': (Kind.INT, '1e3'),
    'int-hex': (Kind.INT, '0x1F'),
    'int-empty': (Kind.INT, ''),
    'int-other-digits': (Kind.INT, '١٢'),
    'number-inf': (Kind.NUMBER, 'inf'),
    'number-nan': (Kind.NUMBER, 'nan'),
    'number-overflow': (Kind.NUMBER, '1e999'),
    'number-empty': (Kind.NUMBER, ''),
    'int-or-none-fraction': (Kind.INT_OR_NONE, '1.0'),
    'int-or-none-none-case': (Kind.INT_OR_NONE, '<NONE>'),
    'logging-file-false': (Kind.LOGGING_FILE, 'false'),
    'logging-file-off': (Kind.LOGGING_FILE, 'off'),
    'logging-file-empty': (Kind.LOGGING_FILE, ''),
}


@mark.unit
@mark.parametrize('case', list(REFUSED))
def test_read_value_refused(case: str) -> None:
    kind, text = REFUSED[case]
    with pytest.raises(ValueError):
        read_value(kind, text)


UNTYPED: Dict[str, Any] = {
    '5': 5, '1_000': 1000, '0x1F': 31, '1e3': 1000.0, 'true': True, '+5': 5,
    '007': '007', 'True': 'True', 'null': 'null', '': '', '5 # c': '5 # c', '5\nx = 1': '5\nx = 1',
    'WAL': 'WAL', '"5"': '"5"', '[1]': '[1]',
}


@mark.unit
@mark.parametrize('text', list(UNTYPED))
def test_read_untyped(text: str) -> None:
    value = read_untyped(text)
    assert value == UNTYPED[text] and type(value) is type(UNTYPED[text])


MATCHES: Dict[str, Tuple[Tuple[str, ...], Kind]] = {
    'HYPERSHELL_SERVER_PORT': (('server', 'port'), Kind.INT),
    'HYPERSHELL_server_tls_min_version': (('server', 'tls', 'min_version'), Kind.TEXT),
    'HYPERSHELL_RELAY_LAUNCHER': (('relay', 'launcher'), Kind.TEXT),
    'HYPERSHELL_RELAY_LAUNCHER_ARGS': (('relay', 'launcher_args'), Kind.TEXT),
    'HYPERSHELL_RELAY_REMOTE_EXE': (('relay', 'remote_exe'), Kind.TEXT),
    'HYPERSHELL_SERVER_AUTH_ENV': (('server', 'auth_env'), Kind.TEXT),
    'HYPERSHELL_Relay_Upstream_Auth_Eval': (('relay', 'upstream', 'auth_eval'), Kind.TEXT),
    'HYPERSHELL_DATABASE_PASSWORD_ENV': (('database', 'password_env'), Kind.TEXT),
    'HYPERSHELL_DATABASE_TIMESCALE_CHUNK_INTERVAL': (('database', 'timescale', 'chunk_interval'), Kind.TEXT),
    'HYPERSHELL_DATABASE_TIMESCALE_FOO': (('database', 'timescale', 'foo'), Kind.UNTYPED),
    'HYPERSHELL_DATABASE_PRAGMAS_JOURNAL_MODE': (('database', 'pragmas', 'journal_mode'), Kind.UNTYPED),
    'HYPERSHELL_DATABASE_SSL_MODE': (('database', 'ssl_mode'), Kind.UNTYPED),
    'HYPERSHELL_DATABASE': (('database', ), Kind.TEXT),
    'HYPERSHELL_SSH_NODELIST_MY_GROUP': (('ssh', 'nodelist', 'my_group'), Kind.TEXT),
    'HYPERSHELL_RELAY_ZONE': (('relay', 'zone'), Kind.TEXT),
    'HYPERSHELL_RELAY_ADVERTISE': (('relay', 'advertise'), Kind.TEXT),
    'HYPERSHELL_RELAY_ZONE_GPU_A100_LAUNCHER_ARGS': (('relay', 'zone', 'gpu_a100', 'launcher_args'), Kind.TEXT),
    'HYPERSHELL_RELAY_ZONE_GPU_MAX_SIZE': (('relay', 'zone', 'gpu_max', 'size'), Kind.INT),
    'HYPERSHELL_EXPORT_MyVar': (('export', 'MyVar'), Kind.TEXT),
    'HYPERSHELL_EXPORT_X_ENV': (('export', 'X_ENV'), Kind.TEXT),
    'HYPERSHELL_SERVER_PORT_X': (('server', 'port_x'), Kind.UNTYPED),
    'HYPERSHELL_SERVER_TLS': (('server', 'tls'), Kind.UNTYPED),
    'HYPERSHELL_FOO_BAR': (('foo_bar', ), Kind.UNTYPED),
    'HYPERSHELL_LOGGING_FILE': (('logging', 'file'), Kind.LOGGING_FILE),
    'HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES': (('autoscale', 'failed_launches'), Kind.INT),
}


@mark.unit
@mark.parametrize('name', list(MATCHES))
def test_match_name(name: str) -> None:
    assert match_name(name) == MATCHES[name]


@mark.unit
@mark.parametrize('name', ['HYPERSHELL_SITE', 'HYPERSHELL_CONFIG_FILE', 'HYPERSHELL_PYTHONPATH', 'HYPERSHELL_ZONE',
                           'HYPERSHELL_site', 'HYPERSHELLX', 'HYPERSHELL_', 'hypershell_server_port',
                           '_HYPERSHELL_RELAY', 'OTHER'])
def test_match_name_none(name: str) -> None:
    assert match_name(name) is None
    assert len(PROCESS_VARIABLES) == 4


@mark.unit
@mark.parametrize('name', ['HYPERSHELL_RELAY_ZONE_GPU', 'HYPERSHELL_RELAY_ZONE_SIZE',
                           'HYPERSHELL_RELAY_ZONE_LAUNCHER_ARGS'])
def test_match_name_zone_refused(name: str) -> None:
    with pytest.raises(VariableError, match=name):
        match_name(name)


@mark.unit
def test_read_environment() -> None:
    """The layer holds each value at its key, and the map names the variable that set it."""
    layer, variables = read_environment({
        'HYPERSHELL_SERVER_TLS_MIN_VERSION': 'TLSv1.3', 'HYPERSHELL_RELAY_LAUNCHER': 'srun',
        'HYPERSHELL_RELAY_LAUNCHER_ARGS': '--mem=4G', 'HYPERSHELL_EXPORT_RUN_ID': '007', 'PATH': '/bin',
        'HYPERSHELL_SITE': '/tmp', 'HYPERSHELL_SERVER_EAGER': 'no',
    })
    assert layer == {'server': {'tls': {'min_version': 'TLSv1.3'}, 'eager': False},
                     'relay': {'launcher': 'srun', 'launcher_args': '--mem=4G'}, 'export': {'RUN_ID': '007'}}
    assert variables[('server', 'tls', 'min_version')] == 'HYPERSHELL_SERVER_TLS_MIN_VERSION'
    assert variables[('export', 'RUN_ID')] == 'HYPERSHELL_EXPORT_RUN_ID'
    assert len(variables) == 5


@mark.unit
def test_read_environment_refusal_names_variable() -> None:
    with pytest.raises(VariableError, match=r"server.port: expected a whole number, given 'abc' "
                                            r"\(from: HYPERSHELL_SERVER_PORT\)"):
        read_environment({'HYPERSHELL_SERVER_PORT': 'abc'})


@mark.unit
def test_read_environment_two_variables_one_key() -> None:
    with pytest.raises(VariableError, match='HYPERSHELL_SERVER_PORT and HYPERSHELL_server_port'):
        read_environment({'HYPERSHELL_SERVER_PORT': '1', 'HYPERSHELL_server_port': '2'})


@mark.unit
def test_read_environment_zone_forms_refused() -> None:
    with pytest.raises(VariableError, match='HYPERSHELL_RELAY_ZONE .*HYPERSHELL_RELAY_ZONE_GPU_SIZE'):
        read_environment({'HYPERSHELL_RELAY_ZONE': 'cpu', 'HYPERSHELL_RELAY_ZONE_GPU_SIZE': '2'})


LOGGING_FILE: Dict[str, Tuple[Dict[str, str], Any]] = {
    'enabled-and-level': ({'HYPERSHELL_LOGGING_FILE': 'enabled', 'HYPERSHELL_LOGGING_FILE_LEVEL': 'devel'},
                          {'level': 'devel'}),
    'path-and-level': ({'HYPERSHELL_LOGGING_FILE': '/tmp/x.log', 'HYPERSHELL_LOGGING_FILE_LEVEL': 'devel'},
                       {'level': 'devel', 'path': '/tmp/x.log'}),
    'path-alone': ({'HYPERSHELL_LOGGING_FILE': '/tmp/x.log'}, '/tmp/x.log'),
}


@mark.unit
@mark.parametrize('case', list(LOGGING_FILE))
def test_read_environment_logging_file(case: str) -> None:
    """A `logging.file` word or path set beside the table's entries becomes part of that table."""
    environ, expected = LOGGING_FILE[case]
    layer, _ = read_environment(environ)
    assert layer['logging']['file'] == expected


@mark.unit
def test_read_environment_logging_file_two_paths() -> None:
    with pytest.raises(VariableError, match='HYPERSHELL_LOGGING_FILE and HYPERSHELL_LOGGING_FILE_PATH'):
        read_environment({'HYPERSHELL_LOGGING_FILE': '/a.log', 'HYPERSHELL_LOGGING_FILE_PATH': '/b.log'})


@mark.unit
def test_read_environment_table_over_scalar() -> None:
    """A value set beside entries beneath it gives way to them."""
    layer, variables = read_environment({'HYPERSHELL_SSH_NODELIST': 'a,b', 'HYPERSHELL_SSH_NODELIST_G': 'c',
                                         'HYPERSHELL_DATABASE': 'my.db', 'HYPERSHELL_DATABASE_FILE': 'x.db'})
    assert layer == {'ssh': {'nodelist': {'g': 'c'}}, 'database': {'file': 'x.db'}}
    assert set(variables) == {('ssh', 'nodelist', 'g'), ('database', 'file')}


def default_at(path: str) -> Any:
    """The default at dotted `path`, or None if there is none."""
    node: Any = default
    for name in path.split('.'):
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
    return node


EMPTY_IS_DEFAULT: Tuple[str, ...] = ('server.bind', 'relay.bind', 'database.host', 'task.cwd')
"""Keys with a default of their own for which an empty variable is still not given (the default one)."""

LITERAL = re.compile(r'`+(?P<value>[^`]*)`+|\'(?P<quoted>[^\']*)\'')
"""A value written as a literal in the reference (in backticks, or quoted), as against words about one."""


def documented_default(path: str, reference: Dict[str, Tuple[str, str]]) -> Optional[str]:
    """The default the parameter reference gives `path` as a literal value, or None if it gives none.

    A key's own entry gives it as `(default: <value>)`; a key named in its table's text
    (:data:`PROSE_KEYS`) as `<name> (default <value>)` or `<name> (<value>)` there. A default
    given in words (`none`, `by .bind`, a path pattern), `<none>` or `<auto>` is not a value.
    """
    if path in PROSE_KEYS:
        table, name = path.rsplit('.', 1)
        match = re.search(rf'``{name}``\s*\((?:default:?\s*)?(?P<text>[^)]*)\)', reference[table][1])
    else:
        match = re.search(r'\(default:?\s*(?P<text>[^)]*)\)', reference[path][1])
    if match is None or not (literal := LITERAL.fullmatch(match['text'].strip())):
        return None
    value = (literal['value'] if literal['value'] is not None else literal['quoted']).strip('"\'')
    return None if value in ('<none>', '<auto>') else value


@mark.unit
def test_documented_default() -> None:
    """The reference gives a literal default for the timescale keys and the binds, and none in words."""
    reference = parse_reference()
    assert documented_default('database.timescale.segmentby', reference) == 'client_id'
    assert documented_default('database.timescale.chunk_interval', reference) == '1 week'
    assert documented_default('server.bind', reference) == 'localhost'
    for path in ('server.tls.cert', 'server.tls.fingerprint', 'server.advertise', 'relay.database', 'relay.zone',
                 'database.user', 'logging.file.format'):
        assert documented_default(path, reference) is None, path


@mark.unit
def test_empty_unset_keys() -> None:
    """The keys an empty variable leaves unset are the optional text keys, the scalar tables and four named keys.

    An optional text key takes text and has no default, or a default that means none, in both the
    defaults table and the parameter reference. The four named keys have a default of their own,
    which an empty variable gives: an empty bind, database host or task directory is the default one.
    """
    reference = parse_reference()
    optional = {path for path, kind in KEYS.items() if kind is Kind.TEXT and path not in SCALAR_TABLES
                and default_at(path) in (None, '<none>', '<auto>') and documented_default(path, reference) is None}
    assert not optional & set(EMPTY_IS_DEFAULT)
    assert set(EMPTY_UNSET) == optional | set(SCALAR_TABLES) | set(EMPTY_IS_DEFAULT)
    assert len(EMPTY_UNSET) == len(set(EMPTY_UNSET))
    assert not {f'database.timescale.{name}' for name in TIMESCALE_KEYS} & set(EMPTY_UNSET)


@mark.unit
def test_read_environment_empty_unset() -> None:
    """An empty variable for an optional text key sets nothing; for another text key it is kept empty."""
    layer, variables = read_environment({'HYPERSHELL_DATABASE_HOST': '', 'HYPERSHELL_SERVER_BIND': '',
                                         'HYPERSHELL_LOGGING_FILE': '', 'HYPERSHELL_LOGGING_DATEFMT': '',
                                         'HYPERSHELL_TASK_CWD': '', 'HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY': ''})
    assert layer == {'logging': {'datefmt': ''}, 'database': {'timescale': {'segmentby': ''}}}
    assert set(variables) == {('logging', 'datefmt'), ('database', 'timescale', 'segmentby')}


DURATION_KEYS: Tuple[str, ...] = (
    'server.evict', 'client.heartrate', 'relay.bundlewait', 'relay.poll', 'relay.retention', 'relay.timeout',
    'relay.upstream.heartrate', 'relay.upstream.evict_hint', 'relay.upstream.deadline', 'relay.upstream.backoff',
)
"""The duration keys no option reads, converted at the start of the command that reads them."""


@mark.unit
@mark.parametrize('path', DURATION_KEYS)
def test_duration_keys_documented(path: str) -> None:
    """Each duration key no option reads is declared a duration and its entry gives the forms."""
    assert KEYS[path] is Kind.DURATION
    text = parse_reference()[path][1]
    assert 'units ``d``, ``h``, ``m``' in text and 'Slurm time' in text, path
    assert ('rounded up to whole seconds' if path == 'relay.timeout' else 'fractions are kept') in text, path
    assert 'not a duration' in text and 'versionchanged:: 2.9.0' in text, path


PATHS: Dict[str, Kind] = {
    'server.port': Kind.INT,
    'server.auth': Kind.TEXT,
    'server.eager': Kind.BOOL,
    'server.evict': Kind.DURATION,
    'client.memory': Kind.SIZE,
    'client.ratelimit': Kind.NUMBER,
    'relay.min': Kind.INT_OR_NONE,
    'logging.file': Kind.LOGGING_FILE,
    'logging.file.level': Kind.TEXT,
    'ssh.nodelist': Kind.TEXT,
    'server.auth_env': Kind.TEXT,
    'server.port_env': Kind.TEXT,
    'database.password_eval': Kind.TEXT,
    'export.RUN_ID': Kind.TEXT,
    'ssh.nodelist.gpu': Kind.TEXT,
    'relay.zone.gpu.launcher': Kind.TEXT,
    'relay.zone.gpu.launcher_args': Kind.TEXT,
    'relay.zone.gpu.size': Kind.INT,
    'relay.zone.gpu.min': Kind.INT,
    'relay.zone.gpu.color': Kind.UNTYPED,
    'relay.zone.gpu': Kind.UNTYPED,
    'relay.zone.gpu.launcher_eval': Kind.UNTYPED,
    'database.connect_args.x': Kind.UNTYPED,
    'database.pragmas.x': Kind.UNTYPED,
    'database.sslmode': Kind.UNTYPED,
    'server.nothing': Kind.UNTYPED,
    'logging.file.foo': Kind.UNTYPED,
    'foo.bar': Kind.UNTYPED,
    'Server.eager': Kind.UNTYPED,
    'relay.zone.GPU.SIZE': Kind.UNTYPED,
    'server.port.x': Kind.UNTYPED,
    'export.A.B': Kind.UNTYPED,
    'ssh.nodelist.a.b': Kind.UNTYPED,
}
"""Dotted paths as `hs config set` is given them, and the kind each value is read as."""


@mark.unit
@mark.parametrize('path', list(PATHS))
def test_match_path(path: str) -> None:
    """A path is matched as written: documented keys by their kind, text tables as text, the rest untyped."""
    assert match_path(path) is PATHS[path]


@mark.unit
def test_match_path_every_key() -> None:
    """Every documented key, and its `_env` and `_eval` forms, is read as the environment layer reads it."""
    for path, kind in KEYS.items():
        assert match_path(path) is kind, path
        for variant in VARIANTS:
            assert match_path(path + variant) is Kind.TEXT, path + variant
