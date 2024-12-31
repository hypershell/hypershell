# SPDX-FileCopyrightText: 2024 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Manage configuration."""


# type annotations
from __future__ import annotations
from typing import Any

# standard libs
import os
import io
import sys
import json
import contextlib
import subprocess

# external libs
import tomlkit
from pygments.styles import STYLE_MAP as CONSOLE_THEMES
from cmdkit.app import Application, ApplicationGroup
from cmdkit.cli import Interface, ArgumentError
from cmdkit.config import ConfigurationError
from rich.console import Console
from rich.syntax import Syntax

# internal libs
from hypershell.core.platform import path
from hypershell.core.types import smart_coerce
from hypershell.core.logging import Logger
from hypershell.core.exceptions import get_shared_exception_mapping
from hypershell.core.config import (load_file, update, ACTIVE_CONFIG_VARS,
                                    default as default_config, config as full_config)

# public interface
__all__ = ['ConfigApp', ]

# initialize logger
log = Logger.with_name(__name__)


EDIT_PROGRAM = 'hs config edit'
EDIT_SYNOPSIS = f'{EDIT_PROGRAM} [-h] [--system | --user | --local]'
EDIT_USAGE = f"""\
Usage:
  {EDIT_SYNOPSIS}

  Edit configuration with default editor.
  The EDITOR/VISUAL environment variable must be set.\
"""

EDIT_HELP = f"""\
{EDIT_USAGE}

Options:
      --system         Edit system configuration.
      --user           Edit user configuration. (default)
      --local          Edit local configuration.
  -h, --help           Show this message and exit.\
"""


class ConfigEditApp(Application):
    """Edit configuration with default editor."""

    interface = Interface(EDIT_PROGRAM, EDIT_USAGE, EDIT_HELP)

    site_name: str = 'user'
    site_interface = interface.add_mutually_exclusive_group()
    site_interface.add_argument('--system', action='store_const', const='system', dest='site_name')
    site_interface.add_argument('--user', action='store_const', const='user', dest='site_name')
    site_interface.add_argument('--local', action='store_const', const='local', dest='site_name')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ConfigEditApp) -> None:
        """Business logic for `config edit`."""

        editor = os.getenv('EDITOR', os.getenv('VISUAL', None))
        if not editor:
            raise RuntimeError('EDITOR or VISUAL environment variable not defined')

        config_path = path[self.site_name].config
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        log.debug(f'Opening {config_path}')
        log.debug(f'Editor: {editor}')
        subprocess.run([editor, config_path])


GET_PROGRAM = 'hs config get'
GET_SYNOPSIS = f'{GET_PROGRAM} [-h] SECTION[...].VAR [-x] [-r] [--system | --user | --local | --default]'
GET_USAGE = f"""\
Usage:
  {GET_SYNOPSIS}
  Get configuration option.\
"""

GET_HELP = f"""\
{GET_USAGE}

  If source is not specified, the output is the merged configuration
  from all sources. Use `hs config which` to see where a specific
  option originates from.
  
  If a single value is requested, use -r/--raw to strip formatting. 

Arguments:
  SECTION[...].VAR          Path to variable (default: '.').

Options:
      --system              Load from system configuration.
      --user                Load from user configuration.
      --local               Load from local configuration.
      --default             Load from default configuration.
  -x, --expand              Expand variable.
  -r, --raw                 Disable formatting on single value output.
  -h, --help                Show this message and exit.\
"""


class ConfigGetApp(Application):
    """Get configuration option."""

    interface = Interface(GET_PROGRAM, GET_USAGE, GET_HELP)

    varpath: str = None
    interface.add_argument('varpath', nargs='?', default='.')

    site_name: str = None
    site_interface = interface.add_mutually_exclusive_group()
    site_interface.add_argument('--system', action='store_const', const='system', dest='site_name')
    site_interface.add_argument('--user', action='store_const', const='user', dest='site_name')
    site_interface.add_argument('--local', action='store_const', const='local', dest='site_name')
    site_interface.add_argument('--default', action='store_const', const='default', dest='site_name')

    expand: bool = False
    interface.add_argument('-x', '--expand', action='store_true')

    raw_mode: bool = False
    interface.add_argument('-r', '--raw', action='store_true', dest='raw_mode')

    # Hidden options used as helpers for completion script
    list_available: bool = False
    list_console_themes: bool = False
    completion_interface = interface.add_mutually_exclusive_group()
    completion_interface.add_argument('--list-available', action='version', version=' '.join(ACTIVE_CONFIG_VARS))
    completion_interface.add_argument('--list-console-themes', action='version',
                                      version=' '.join(list(CONSOLE_THEMES)))

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ConfigGetApp) -> None:
        """Business logic for `config get`."""

        if self.site_name is None:
            config_path = 'configuration'  # Note: not meaningful for merged configuration
            config = full_config
        elif self.site_name == 'default':
            config_path = 'default'
            config = default_config
        else:
            config_path = path[self.site_name].config
            if os.path.exists(config_path):
                config = load_file(config_path)
            else:
                raise ConfigurationError(f'{config_path} does not exist')

        if self.varpath == '.':
            self.print_output(config)
            return

        if '.' not in self.varpath:
            if self.varpath in config:
                self.print_output(config[self.varpath])
                return
            else:
                raise ConfigurationError(f'"{self.varpath}" not found in {config_path}')

        if self.varpath.startswith('.'):
            raise ConfigurationError(f'Section name cannot start with "."')

        section, *subsections, variable = self.varpath.split('.')
        if section not in config:
            raise ConfigurationError(f'"{section}" is not a section in {config_path}')

        config_section = config[section]
        if subsections:
            subpath = f'{section}'
            try:
                for subsection in subsections:
                    subpath += f'.{subsection}'
                    if not isinstance(config_section[subsection], dict):
                        raise ConfigurationError(f'"{subpath}" not a section in {config_path}')
                    else:
                        config_section = config_section[subsection]
            except KeyError as error:
                raise ConfigurationError(f'"{subpath}" not found in {config_path}') from error

        if self.expand:
            try:
                value = getattr(config_section, variable)
            except AttributeError as error:
                raise ConfigurationError('') from error
            if value is None:
                raise ConfigurationError(f'"{variable}" not found in {config_path}')
            self.print_output(value)
            return
        elif variable in config_section:
            self.print_output(config_section[variable])
        else:
            raise ConfigurationError(f'"{self.varpath}" not found in {config_path}')

    def print_output(self: ConfigGetApp, value: Any) -> None:
        """Format and print final `value`."""
        value = self.format_output(value)
        if sys.stdout.isatty() and not self.raw_mode:
            output = Syntax(value, 'toml', word_wrap=True,
                            theme = full_config.console.theme,
                            background_color = 'default')
            Console().print(output)
        else:
            # NOTE: JSON formatting puts quotations - we don't want these on raw output
            print(value.strip('"'), file=sys.stdout, flush=True)

    def format_output(self: ConfigGetApp, value: Any) -> str:
        """Format `value` as appropriate text."""
        if isinstance(value, dict):
            value = self.format_section(value)
        else:
            value = json.dumps(value)  # NOTE: close enough
        return value

    def format_section(self: ConfigGetApp, value: dict) -> str:
        """Format an entire section for output."""
        if self.varpath == '.':
            return tomlkit.dumps(value)
        else:
            return tomlkit.dumps({self.varpath: value})


SET_PROGRAM = 'hs config set'
SET_SYNOPSIS = f'{SET_PROGRAM} [-h] SECTION[...].VAR VALUE [--system | --user | --local]'
SET_USAGE = f"""\
Usage: 
  {SET_SYNOPSIS}
  Set configuration option.\
"""

SET_HELP = f"""\
{SET_USAGE}

Arguments:
  SECTION[...].VAR        Path to variable.
  VALUE                   Value to be set.

Options:
      --system            Apply to system configuration.
      --user              Apply to user configuration. (default)
      --local             Apply to local configuration.
  -h, --help              Show this message and exit.\
"""


class ConfigSetApp(Application):
    """Set configuration option."""

    interface = Interface(SET_PROGRAM, SET_USAGE, SET_HELP)

    varpath: str = None
    interface.add_argument('varpath', metavar='VAR')

    value: str = None
    interface.add_argument('value', type=smart_coerce)

    site_name: str = 'user'
    site_interface = interface.add_mutually_exclusive_group()
    site_interface.add_argument('--user', action='store_const', const='user', dest='site_name', default=site_name)
    site_interface.add_argument('--system', action='store_const', const='system', dest='site_name')
    site_interface.add_argument('--local', action='store_const', const='local', dest='site_name')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ConfigSetApp) -> None:
        """Business logic for `config set`."""

        if '.' not in self.varpath:
            raise ArgumentError('Missing section in variable path')

        section, *subsections, variable = self.varpath.split('.')

        config = {section: {}}
        config_section = config[section]
        for subsection in subsections:
            if subsection not in config_section:
                config_section[subsection] = dict()
            config_section = config_section[subsection]

        config_section[variable] = self.value
        update(self.site_name, config)


WHICH_PROGRAM = 'hs config which'
WHICH_SYNOPSIS = f'{WHICH_PROGRAM} [-h] SECTION[...].VAR [--site]'
WHICH_USAGE = f"""\
Usage: 
  {WHICH_SYNOPSIS}
  Show origin of configuration option.\
"""

WHICH_HELP = f"""\
{WHICH_USAGE}

Arguments:
  SECTION[...].VAR        Path to variable.

Options:
      --site              Output originating site name only.
  -h, --help              Show this message and exit.\
"""


class ConfigWhichApp(Application):
    """Show origin of configuration option."""

    interface = Interface(WHICH_PROGRAM, WHICH_USAGE, WHICH_HELP)

    varpath: str = None
    interface.add_argument('varpath', metavar='VAR')

    site_only: bool = False
    interface.add_argument('--site', action='store_true', dest='site_only')

    exceptions = {
        **get_shared_exception_mapping(__name__)
    }

    def run(self: ConfigWhichApp) -> None:
        """Business logic for `config which`."""
        try:
            site = full_config.which(*self.varpath.split('.'))
        except KeyError:
            log.critical(f'"{self.varpath}" not found')
            return
        if self.site_only:
            print(site)
            return
        try:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                with ConfigGetApp.from_cmdline([self.varpath, '--raw']) as app:
                    app.run()
        except ConfigurationError:
            value = 'null'
        else:
            value = stdout.getvalue().strip()
        try:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                with ConfigGetApp.from_cmdline([self.varpath, '--raw', '--default']) as app:
                    app.run()
        except ConfigurationError:
            default_value = 'null'
        else:
            default_value = stdout.getvalue().strip()
        if '[' in value:
            value = '[...]'
        if '[' in default_value:
            default_value = '[...]'
        if site in ('default', 'logging', ):
            print(f'{value} ({site})')
        elif site == 'env':
            env_varname = 'HYPERSHELL_' + self.varpath.upper().replace('.', '_')
            if value == '[...]':
                for name in full_config.namespaces.env.to_env().flatten(prefix='HYPERSHELL'):
                    if name.startswith(env_varname):
                        env_varname = name
            print(f'{value} (env: {env_varname} | default: {default_value})')
        else:
            print(f'{value} ({site}: {path[site].config} | default: {default_value})')


if os.name == 'nt':
    CONFIG_PATH_INFO = f"""\
  --system         %ProgramData%\\HyperShell\\Config.toml
  --user           %AppData%\\HyperShell\\Config.toml
  --local          {path.local.config}
"""
else:
    CONFIG_PATH_INFO = f"""\
  --system         /etc/hypershell.toml
  --user           ~/.hypershell/config.toml
  --local          {path.local.config}
"""


PROGRAM = 'hs config'
USAGE = f"""\
Usage:
  {PROGRAM} [-h]
  {GET_SYNOPSIS}
  {SET_SYNOPSIS}
  {EDIT_SYNOPSIS}
  {WHICH_SYNOPSIS}

  {__doc__}\
"""

HELP = f"""\
{USAGE}

Commands:
  get              {ConfigGetApp.__doc__}
  set              {ConfigSetApp.__doc__}
  edit             {ConfigEditApp.__doc__}
  which            {ConfigWhichApp.__doc__}

Options:
  -h, --help       Show this message and exit.

Files:
{CONFIG_PATH_INFO}\
"""


class ConfigApp(ApplicationGroup):
    """Manage configuration."""

    interface = Interface(PROGRAM, USAGE, HELP)
    interface.add_argument('command')

    commands = {'get': ConfigGetApp,
                'set': ConfigSetApp,
                'edit': ConfigEditApp,
                'which': ConfigWhichApp, }
