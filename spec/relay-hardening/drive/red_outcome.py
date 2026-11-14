# SPDX-FileCopyrightText: 2026 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""A pytest plugin for red_check.sh: records how each failure came about.

Loaded with ``-p red_outcome`` (this directory on PYTHONPATH). For every failure pytest reports,
in collection or in a test's setup, call or teardown, it appends one tab-separated line to the file
named by ``RED_OUTCOME``: the phase, ``assertion`` or ``error``, and the names of the exception's
classes, most derived first, between commas. An assertion is an ``AssertionError`` or a failure the
test raised itself with ``pytest.fail``.
"""

import os

import pytest


def pytest_exception_interact(node, call, report) -> None:
    """Record the phase and the kind of a failure (pytest calls this for every failure but a skip)."""
    path = os.environ.get('RED_OUTCOME')
    if not path or call.excinfo is None:
        return
    kind = call.excinfo.type
    assertion = issubclass(kind, (AssertionError, pytest.fail.Exception))
    names = ','.join(cls.__name__ for cls in kind.__mro__)
    with open(path, 'a', encoding='utf-8') as stream:
        print(report.when, 'assertion' if assertion else 'error', f',{names},', sep='\t', file=stream)
