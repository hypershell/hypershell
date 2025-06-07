# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for testing."""


# Standard libs
import os

# External libs
from pytest import fixture
from pathlib import Path


@fixture(scope="function")
def temp_site(tmpdir_factory) -> Path:
    """Setup empty site and database path."""
    for name in os.environ:
        if name.startswith('HYPERSHELL_'):
            os.environ.pop(name)
    site = tmpdir_factory.mktemp('data')
    db_path = site.join('local.db')
    os.environ['HYPERSHELL_CONFIG_FILE'] = ''
    os.environ['HYPERSHELL_SITE'] = str(site)
    os.environ['HYPERSHELL_DATABASE_FILE'] = str(db_path)
    os.environ['HYPERSHELL_LOGGING_LEVEL'] = 'DEBUG'
    return site
