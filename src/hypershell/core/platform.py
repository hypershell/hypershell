# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""Platform specific file paths and initialization."""


# NOTE:
# A lot of the work done in this core module is provided by CmdKit at this point.
# For continuity and for fear of breaking other parts of the project we have decided
# to leave this module in place for the time being.


# Standard libs
import os
import sys
import ctypes
import platform

# External libs
from cmdkit.config import Namespace
from cmdkit.app import exit_status
from cmdkit.ansi import bold, magenta

# Public interface
__all__ = ['cwd', 'home', 'site', 'path', 'default_path']


cwd = os.getcwd()
home = os.path.expanduser('~')
if 'HYPERSHELL_SITE' not in os.environ:
    local_site = os.path.join(cwd, '.hypershell')
else:
    local_site = os.getenv('HYPERSHELL_SITE')
    if not os.path.isdir(local_site):
        print(f'{bold(magenta("CRITICAL"))} [{__name__}] '
              f'Directory does not exist (HYPERSHELL_SITE={local_site})', file=sys.stderr)
        sys.exit(exit_status.bad_config)


if platform.system() == 'Windows':
    is_admin = ctypes.windll.shell32.IsUserAnAdmin() == 1
    site = Namespace(system=os.path.join(os.getenv('ProgramData'), 'HyperShell'),
                     user=os.path.join(os.getenv('AppData'), 'HyperShell'),
                     local=local_site)
    path = Namespace({
        'system': {
            'lib': os.path.join(site.system, 'Library'),
            'log': os.path.join(site.system, 'Logs'),
            'config': os.path.join(site.system, 'Config.toml')},
        'user': {
            'lib': os.path.join(site.user, 'Library'),
            'log': os.path.join(site.user, 'Logs'),
            'config': os.path.join(site.user, 'Config.toml')},
        'local': {
            'lib': os.path.join(site.local, 'Library'),
            'log': os.path.join(site.local, 'Logs'),
            'config': os.path.join(site.local, 'Config.toml')}
    })

elif platform.system() == 'Darwin':
    is_admin = os.getuid() == 0
    site = Namespace(system='/', user=home, local=local_site)
    path = Namespace({
        'system': {
            'lib': os.path.join(site['system'], 'Library', 'HyperShell'),
            'log': os.path.join(site['system'], 'Library', 'Logs', 'HyperShell'),
            'config': os.path.join(site['system'], 'Library', 'Preferences', 'HyperShell', 'config.toml')},
        'user': {
            'lib': os.path.join(site['user'], 'Library', 'HyperShell'),
            'log': os.path.join(site['user'], 'Library', 'Logs', 'HyperShell'),
            'config': os.path.join(site['user'], 'Library', 'Preferences', 'HyperShell', 'config.toml')},
        'local': {
            'lib': os.path.join(site['local'], 'Library'),
            'log': os.path.join(site['local'], 'Logs'),
            'config': os.path.join(site['local'], 'config.toml')}
    })

elif os.name == 'posix':  # NOTE: likely Linux
    is_admin = os.getuid() == 0
    site = Namespace(system='/', user=os.path.join(home, '.hypershell'),
                     local=local_site)
    path = Namespace({
        'system': {
            'lib': os.path.join(site.system, 'var', 'lib', 'hypershell'),
            'log': os.path.join(site.system, 'var', 'log', 'hypershell'),
            'config': os.path.join(site.system, 'etc', 'hypershell.toml')},
        'user': {
            'lib': os.path.join(site.user, 'lib'),
            'log': os.path.join(site.user, 'log'),
            'config': os.path.join(site.user, 'config.toml')},
        'local': {
            'lib': os.path.join(site.local, 'lib'),
            'log': os.path.join(site.local, 'log'),
            'config': os.path.join(site.local, 'config.toml')}
    })

else:
    print(f'{bold(magenta("CRITICAL"))} [{__name__}] '
          f'Platform unrecognized ({platform.system()})', file=sys.stderr)
    sys.exit(exit_status.bad_config)


if 'HYPERSHELL_SITE' in os.environ:
    default_path = path.local
else:
    default_path = path.user if not is_admin else path.system


# Automatically initialize default site directories
default_dirs = [
    default_path.lib,
    default_path.log,
    os.path.join(default_path.lib, 'task'),
]


for default_dir in default_dirs:
    try:
        os.makedirs(default_dir, exist_ok=True)
    except PermissionError:
        pass
