# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Automated tests for HyperShell."""


# Type annotations
from __future__ import annotations
from typing import Final, List, Tuple, Dict, Any

# Standard libs
import os
import re
from pathlib import Path
from subprocess import run, PIPE

# Public interface
__all__ = ['NO_OUTPUT', 'main', 'main_lines', 'assert_output',
           'create_taskfile', 'create_taskfile_echo',
           'UUID_PATTERN']


# Flag for empty output from `main_lines` function
NO_OUTPUT: Final[List[str]] = ['', ]


def main(argv: List[str]) -> Tuple[int, str, str]:
    """Return stdout, stderr, and exit code of command-line interface."""
    proc = run(argv, stdout=PIPE, stderr=PIPE, env=os.environ)
    return (
        proc.returncode,
        proc.stdout.decode('utf-8').strip(),
        proc.stderr.decode('utf-8').strip()
    )


def main_lines(argv: List[str]) -> Tuple[int, List[str], List[str]]:
    """Return stdout, stderr, and exit code of command-line interface with output as List[str]."""
    rc, stdout, stderr = main(argv)
    return (
        rc,
        [line.strip() for line in stdout.split('\n')],
        [line.strip() for line in stderr.split('\n')]
    )


def assert_output(pattern: str, output: str, count: int = 1, groups: Dict[str, str | re.Pattern] = None) -> None:
    """Assert some count of lines in output match pattern, optionally with capture groups pattern matching."""
    n = 0
    for line in output.strip().splitlines():
        if match := re.search(pattern, line):
            n += 1
            for key, value in (groups or {}).items():
                assert re.match(value, match.group(key))
    assert n == count


def create_taskfile(temp_site: Path, lines: List[str]) -> Path:
    """Produce task input file for test, return path."""
    taskfile = temp_site / 'task.in'
    with open(taskfile, mode='w', encoding='utf-8') as stream:
        for line in lines:
            print(line, file=stream)
    return taskfile


def create_taskfile_echo(temp_site: Path, count: int, tags: Dict[str, Any] = None) -> Path:
    """Produce task input file with 'echo {}' lines, return path."""
    tagline = ' '.join([f'{k}:{v}' for k, v in (tags or {}).items()])
    return create_taskfile(temp_site, lines=[f'echo {n}  # HYPERSHELL: {tagline} n:{n}' for n in range(count)])


UUID_PATTERN: re.Pattern = re.compile(
    r'^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$'
)
