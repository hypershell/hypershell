# SPDX-FileCopyrightText: 2025 Geoffrey Lentner
# SPDX-License-Identifier: Apache-2.0

"""
Sanitize and supplement package environment.

For most installations this module does nothing and can be ignored.
For system-wide user-facing non-library installations we do not want user
programs (which may be Python-based with PYTHONPATH implications) to interfere
with this program.

The HYPERSHELL_PYTHONPATH environment variable takes the place of the normal
PYTHONPATH environment variable. However, if this variable exists and points to
a file, the contents of that file represents a frozen path list.
"""


# Type annotations
from __future__ import annotations
from typing import List, Final

# Standard libs
import os
import sys
import platform

# Public interface
__all__ = ['HYPERSHELL_PYTHONPATH', 'PATH_SEP']


if platform.system() == 'Windows':
    PATH_SEP: Final[str] = ';'
else:
    PATH_SEP: Final[str] = ':'


HYPERSHELL_PYTHONPATH: Final[str | None] = os.getenv("HYPERSHELL_PYTHONPATH", None)


# If the HYPERSHELL_PYTHONPATH refers to a file path we interpret it to be a frozen list.
# E.g., (/etc/hypershell.pythonpath)
#    /usr/local/lib
#    /usr/local/lib/python3.12
#    /usr/local/lib/python3.12/lib-dynload
#    /usr/local/lib/python3.12/site-packages
#    /usr/local/lib/hypershell/site-packages
if HYPERSHELL_PYTHONPATH and os.path.isfile(HYPERSHELL_PYTHONPATH):
    sys.path.clear()
    with open(HYPERSHELL_PYTHONPATH, mode='r') as stream:
        lines = stream.read().strip().split('\n')
        for path in lines:
            if path and not path.startswith('#'):
                if os.path.exists(path):
                    sys.path.append(path)
                else:
                    print(f'Error: "{path}" not found (HYPERSHELL_PYTHONPATH={HYPERSHELL_PYTHONPATH})',
                          file=sys.stderr, flush=True)
                    sys.exit(3)  # exit_status.bad_config

# Otherwise HYPERSHELL_PYTHONPATH is treated exactly like the normal PYTHONPATH variable.
elif HYPERSHELL_PYTHONPATH:
    lines = HYPERSHELL_PYTHONPATH.strip().split(PATH_SEP)
    for path in lines:
        if os.path.exists(path):
            sys.path.append(path)
        else:
            print(f'Error: "{path}" not found (HYPERSHELL_PYTHONPATH={HYPERSHELL_PYTHONPATH})',
                  file=sys.stderr, flush=True)
            sys.exit(3)  # exit_status.bad_config
