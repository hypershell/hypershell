# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""`hs config set` refuses a numeric key's value its entry refuses, and any integer TOML cannot hold, exit 2.

A numeric key (zone keys included) is read by its table entry, range and kind; any other key's
integer outside -2**63 ... 2**63-1 is refused, its digits counted before it is converted. The
refusal names the key and shows the value cut short; the file is left byte for byte as it was and
no backup is made.
"""


# Type annotations
from __future__ import annotations
from typing import Final, List, Tuple

# Standard libs
from pathlib import Path

# External libs
import pytest
from pytest import mark
from cmdkit.app import exit_status

# Internal libs
from tests.bounded_checks import LONG, SHORT
from tests.test_config_set_types import SEED, config_set, written


REFUSED: Final[List[Tuple[str, str, str]]] = [
    ('server-bundlesize-0', 'server.bundlesize', '0'),
    ('server-evict-4300-digits', 'server.evict', LONG),
    ('zone-size-0', 'relay.zone.a.size', '0'),
    ('task-memory-8EB', 'task.memory', '8EB'),
    ('server-port-65536', 'server.port', '65536'),
    ('task-cores-100000', 'task.cores', '100000'),
    ('client-ratelimit-1e-300', 'client.ratelimit', '1e-300'),
    ('autoscale-period-0', 'autoscale.period', '0'),
    ('task-timeout-25000d', 'task.timeout', '25000d'),
    ('relay-min-none-word', 'relay.min', 'none'),
    ('zone-min-negative', 'relay.zone.a.min', '-1'),
]
"""A value each key's entry refuses, by test id: the key and the value."""


def seeded(site: Path) -> Tuple[Path, bytes]:
    """The site's configuration file, seeded, and its bytes."""
    file = Path(str(site)) / 'config.toml'
    file.write_text(SEED)
    return file, file.read_bytes()


def assert_untouched(site: Path, file: Path, before: bytes) -> None:
    """The file is byte for byte as it was, and no backup was made."""
    assert file.read_bytes() == before
    assert not list(Path(str(site)).glob('.config.*.toml'))


@mark.integration
@mark.parametrize('path, value', [pytest.param(path, value, id=case) for case, path, value in REFUSED])
def test_set_refused(temp_site: Path, path: str, value: str) -> None:
    """The value is refused, exit 2, naming the key; nothing is written."""
    site = Path(str(temp_site))
    file, before = seeded(site)
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.bad_argument, stderr[-4000:]
    assert f'{path}: expected ' in stderr and ' from ' in stderr and ', given ' in stderr, stderr[-4000:]
    assert 'Traceback' not in stderr and len(stderr.splitlines()[-1]) <= SHORT, stderr[-4000:]
    assert_untouched(site, file, before)


@mark.integration
@mark.parametrize('path, value', [('database.connect_args.x', str(2**64)), ('database.connect_args.x', str(-2**63 - 1)),
                                  ('database.pragmas.y', LONG), ('database.connect_args.x', '9' * 5000)],
                  ids=['2**64', '-2**63-1', '4300-digits', '5000-digits'])
def test_set_untyped_wide_integer_refused(temp_site: Path, path: str, value: str) -> None:
    """An untyped key's integer outside 64 bits is refused, exit 2, naming the key; nothing is written."""
    site = Path(str(temp_site))
    file, before = seeded(site)
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.bad_argument, stderr[-4000:]
    assert path in stderr and 'Traceback' not in stderr, stderr[-4000:]
    assert len(stderr.splitlines()[-1]) <= SHORT, stderr[-4000:]
    assert_untouched(site, file, before)


@mark.integration
@mark.parametrize('path, value, stored', [
    ('server.bundlesize', '2147483647', 2147483647), ('relay.zone.a.size', '1', 1), ('task.memory', '16G', '16G'),
    ('server.evict', '10m', '10m'), ('relay.min', '<none>', '<none>'), ('database.connect_args.x', str(2**63 - 1), 2**63 - 1),
    ('database.connect_args.x', str(-2**63), -2**63), ('client.ratelimit', '0', 0), ('task.timeout', '0', 0),
], ids=['bundlesize', 'zone-size', 'memory', 'evict', 'relay-min', 'untyped-max', 'untyped-min', 'rate-0', 'timeout-0'])
def test_set_in_range_written(temp_site: Path, path: str, value: str, stored) -> None:
    """A value inside its range is written as today (durations and sizes as given)."""
    site = Path(str(temp_site))
    file, _ = seeded(site)
    rc, stderr = config_set(site, path, value)
    assert rc == exit_status.success, stderr[-4000:]
    assert written(file, path) == stored
