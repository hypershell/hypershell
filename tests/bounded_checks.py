# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the tests of bounded numeric input: the one refusal line, and one bounded CLI runner.

Not collected (no `test_` prefix). Every name here exists on the base build, so a test module that
imports it still collects there; tests that need the table import it inside the test.
"""


# Type annotations
from __future__ import annotations
from typing import Dict, Final, List, Optional, Tuple

# Standard libs
import re
from pathlib import Path

# Internal libs
from tests.test_env_config import child_env, run_bounded


LONG: Final[str] = '9' * 4300
"""A 4,300-digit integer, as text: the widest Python converts to and from text by default."""

WIDER: Final[str] = '9' * 5000
"""A 5,000-digit integer, as text: wider than Python converts by default."""

SHORT: Final[int] = 400
"""Most characters a refusal line may hold, whatever the value given."""


def refusal(name: str, source: Optional[str] = None) -> re.Pattern:
    """The one refusal line for the setting `name` (`-N/--num-threads`, `server.evict`), from `source` if given.

    `<name>: expected <form> from <A> to <B>, given <V>`, and ` (from: <source>)` for configuration.
    """
    tail = rf' \(from: {re.escape(source)}\)' if source is not None else ''
    return re.compile(rf'(?m)^(?:\s*CRITICAL \[[\w.]+\] )?{re.escape(name)}: expected .+ from .+ to .+, given .+{tail}$')


def refusal_lines(stderr: str, name: str) -> List[str]:
    """The lines of `stderr` that refuse `name`."""
    return [line for line in stderr.splitlines() if f'{name}: expected ' in line]


def assert_one_refusal(rc: int, stderr: str, status: int, name: str, source: Optional[str] = None) -> None:
    """The command exited `status` with one refusal line for `name` (from `source`), short, and no traceback."""
    assert rc == status, stderr[-4000:]
    assert 'Traceback' not in stderr, stderr[-4000:]
    assert refusal(name, source).search(stderr), stderr[-4000:]
    lines = refusal_lines(stderr, name)
    assert len(lines) == 1, stderr[-4000:]
    assert len(lines[0]) <= SHORT, lines[0][:SHORT]
    assert 'argument ' not in lines[0] and 'ConfigurationError' not in lines[0], lines[0]


def write_config(site: Path, text: str) -> Tuple[Path, Dict[str, str]]:
    """Write `text` as the site's configuration file; its path, and the variables that make a child read it."""
    path = Path(str(site)) / 'config.toml'
    path.write_text(text, encoding='utf-8')
    return path, {'HYPERSHELL_CONFIG_FILE': str(path)}


def hs_cli(site: Path, argv: List[str], bound: float = 60, **variables: Optional[str]) -> Tuple[int, str, str]:
    """Run `argv` in `site` with `variables` set (None unsets one), in its own process group, bounded."""
    return run_bounded(Path(str(site)), argv, child_env(Path(str(site)), **variables), bound=bound)
