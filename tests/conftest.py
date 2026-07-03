# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and shared environment isolation for testing."""


# Standard libs
import os
import socket

# External libs
from pytest import fixture
from pathlib import Path


def free_port() -> int:
    """Return an available TCP port on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('localhost', 0))
        return sock.getsockname()[1]


def isolate_environment() -> None:
    """Scrub all ``HYPERSHELL_*`` variables and pin a clean baseline.

    Keeps the developer's user configuration and machine defaults from leaking into
    the suite: ``HYPERSHELL_CONFIG_FILE`` is blanked so no system/user/local config
    file is read, and ``HYPERSHELL_SERVER_PORT`` is bound to a free port so cluster
    and server tests never collide with the default (or a stray local cluster).
    """
    for name in [name for name in os.environ if name.startswith('HYPERSHELL_')]:
        os.environ.pop(name)
    os.environ['HYPERSHELL_CONFIG_FILE'] = ''
    os.environ['HYPERSHELL_SERVER_PORT'] = str(free_port())


# Enforce isolation at import, before any test module (or HyperShell) is imported,
# since configuration is read at import time during collection.
isolate_environment()


@fixture(scope="function", autouse=True)
def clean_env() -> None:
    """Restore the isolated environment before every test, undoing any prior leakage."""
    isolate_environment()


@fixture(scope="function")
def temp_site(clean_env, tmpdir_factory) -> Path:
    """Setup empty site and database path on top of the isolated environment."""
    site = tmpdir_factory.mktemp('data')
    db_path = site.join('local.db')
    os.environ['HYPERSHELL_SITE'] = str(site)
    os.environ['HYPERSHELL_DATABASE_FILE'] = str(db_path)
    os.environ['HYPERSHELL_LOGGING_LEVEL'] = 'DEBUG'
    return site
