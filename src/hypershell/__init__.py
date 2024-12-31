# SPDX-FileCopyrightText: 2024 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Initialization and entry-point for console application."""


# standard libs
import sys
from importlib.metadata import version as get_version
from platform import python_version

# external libs
from cmdkit.app import Application, ApplicationGroup
from cmdkit.cli import Interface

# internal libs
from hypershell.core.logging import Logger, initialize_logging
from hypershell.core.signal import register_handlers
from hypershell.submit import SubmitApp
from hypershell.server import ServerApp
from hypershell.client import ClientApp
from hypershell.cluster import ClusterApp
from hypershell.task import TaskGroupApp
from hypershell.config import ConfigApp
from hypershell.data import InitDBApp

# public interface
__all__ = ['HyperShellApp', 'main', '__version__']

# project metadata
__version__     = get_version('hypershell')
__website__     = 'https://github.com/glentner/hypershell'
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

# initialize logger
log = Logger.with_name('hypershell')


# inject logger setup into command-line framework
Application.log_critical = log.critical
Application.log_exception = log.exception


APP_NAME = 'hs'
APP_USAGE = f"""\
Usage:
  {APP_NAME} [-h] [-v] <command> [<args>...]
  {__description__}\
"""

APP_HELP = f"""\
{APP_USAGE}

Commands:
  config                 {ConfigApp.__doc__}
  submit                 {SubmitApp.__doc__}
  server                 {ServerApp.__doc__}
  client                 {ClientApp.__doc__}
  cluster                {ClusterApp.__doc__} (recommended)
  task                   {TaskGroupApp.__doc__}
  initdb                 {InitDBApp.__doc__}

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
    interface.add_argument('-v', '--version', action='version',
                           version=f'HyperShell v{__version__} (Python {python_version()})')
    interface.add_argument('--citation', action='version', version=__citation__)
    interface.add_argument('command')

    commands = {
        'submit': SubmitApp,
        'server': ServerApp,
        'client': ClientApp,
        'cluster': ClusterApp,
        'task': TaskGroupApp,
        'config': ConfigApp,
        'initdb': InitDBApp,
    }


def main() -> int:
    """Entry-point for console application."""
    initialize_logging()
    register_handlers()
    return HyperShellApp.main(sys.argv[1:])
