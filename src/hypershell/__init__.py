# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Initialization and entry-point for console application."""

# Type annotations
from __future__ import annotations
from typing import List

# Path sanitizer must happen first
import hypershell.core.sys

# Standard libs
import sys
from importlib.metadata import version as get_version
from platform import python_version, python_implementation

# External libs
from cmdkit.app import Application, ApplicationGroup
from cmdkit.cli import Interface

# Internal libs
from hypershell.core.logging import Logger, initialize_logging
from hypershell.core.signal import register_handlers
from hypershell.submit import SubmitApp
from hypershell.server import ServerApp
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
from hypershell.task import TaskGroupApp, TaskInfoApp, TaskWaitApp, TaskRunApp, TaskSearchApp, TaskUpdateApp
from hypershell.config import ConfigApp
from hypershell.data import InitDBApp

# Public interface
__all__ = [
    'HyperShellApp', 'main', '__version__', '__citation__',
    'APP_VERSION', 'APP_USAGE', 'APP_HELP',
    'SubmitApp', 'ServerApp', 'ClientApp', 'InitDBApp', 'ConfigApp',
    'TaskGroupApp', 'TaskInfoApp', 'TaskWaitApp', 'TaskRunApp', 'TaskSearchApp', 'TaskUpdateApp',
]

# project metadata
__version__     = get_version('hypershell')
__website__     = 'https://github.com/hypershell/hypershell'
__description__ = 'Process shell commands over a distributed, asynchronous queue.'
__citation__    = """\
@inproceedings{lentner_2022,
    author = {Lentner, Geoffrey and Gorenstein, Lev},
    title = {HyperShell v2: Distributed Task Execution for HPC},
    year = {2022},
    isbn = {9781450391610},
    publisher = {Association for Computing Machinery},
    url = {https://doi.org/10.1145/3491418.3535138},
    doi = {10.1145/3491418.3535138},
    booktitle = {Practice and Experience in Advanced Research Computing},
    articleno = {80},
    numpages = {3},
    series = {PEARC '22}
}\
"""

# Initialize logger
log = Logger.with_name('hypershell')


# Inject logger into command-line framework
Application.log_critical = log.critical
Application.log_exception = log.exception


APP_NAME = 'hs'
APP_VERSION = f'HyperShell v{__version__} ({python_implementation()} {python_version()})'
APP_USAGE = f"""\
Usage:
  {APP_NAME} [-h] [-v] <command> [<args>...]
  {__description__}\
"""

APP_HELP = f"""\
{APP_USAGE}

Commands:
  cluster                {ClusterApp.__doc__}
  server                 {ServerApp.__doc__}
  client                 {ClientApp.__doc__}
  submit                 {SubmitApp.__doc__}
  initdb                 {InitDBApp.__doc__}
  info                   {TaskInfoApp.__doc__}
  wait                   {TaskWaitApp.__doc__}
  run                    {TaskRunApp.__doc__}
  list                   {TaskSearchApp.__doc__}
  update                 {TaskUpdateApp.__doc__}
  config                 {ConfigApp.__doc__}

Options:
  -h, --help             Show this message and exit.
  -v, --version          Show the version and exit.
      --citation         Show citation info and exit.

Issue tracking at:
{__website__}

If this software has helped in your research please consider
citing us (see --citation).\
"""


class HyperShellApp(ApplicationGroup):
    """Top-level application class for console application."""

    interface = Interface(APP_NAME, APP_USAGE, APP_HELP)
    interface.add_argument('-v', '--version', action='version', version=APP_VERSION)
    interface.add_argument('--citation', action='version', version=__citation__)
    interface.add_argument('command')

    commands = {
        'cluster': ClusterApp,
        'server': ServerApp,
        'client': ClientApp,
        'submit': SubmitApp,
        'config': ConfigApp,
        'initdb': InitDBApp,
        'info': TaskInfoApp,
        'wait': TaskWaitApp,
        'run': TaskRunApp,
        'list': TaskSearchApp,
        'update': TaskUpdateApp,
        'task': TaskGroupApp,  # NOTE: left for backwards compatibility
    }


def main(argv: List[str] | None = None) -> int:
    """Entry-point for 'hs' console application."""
    initialize_logging()
    register_handlers()
    return HyperShellApp.main(argv or sys.argv[1:])


def main_x(argv: List[str] | None = None) -> int:
    """Entrypoint for 'hsx' console application."""
    return main(['cluster', ] + (argv or sys.argv[1:]))
