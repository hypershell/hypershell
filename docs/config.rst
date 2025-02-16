.. _config:

Configuration
=============

|

.. include:: _include/config_intro.rst

The `edit` command makes it easy to open the correct configuration file path for your platform
using the default editor. Set the ``EDITOR`` environment variable (or ``VISUAL`` on `Windows`).

.. admonition:: Open global user-level configuration file for editing
    :class: note

    .. code-block:: shell

        hs config edit --user

------

Filesystem Paths
----------------

|

The `site` refers to the locations on the file system from which configuration files are found
and output files are written to. This entails a `library` (or `lib`) path for where to capture
outputs; a `log` path for where to write logs and exceptions; and a `config` file path. There
are two environment variables that play a role in modifying this behavior.

.. include:: _include/config_site_vars.rst

The paths to these locations is platform specific. On `Windows` and `macOS`, the program abides
by the conventions of that platform, just as on `Linux` (otherwise assumed `POSIX`). Below we
have enumerated all variations with `Library`, `Logs`, and `Config` in each case.

|

**Windows**
    **System**
        - ``%PROGRAMDATA%\HyperShell\Library``
        - ``%PROGRAMDATA%\HyperShell\Logs``
        - ``%PROGRAMDATA%\HyperShell\Config.toml``
    **User**
        - ``%APPDATA%\HyperShell\Library``
        - ``%APPDATA%\HyperShell\Logs``
        - ``%APPDATA%\HyperShell\Config.toml``
    **Local**
        - ``%HYPERSHELL_SITE%\Library``
        - ``%HYPERSHELL_SITE%\Logs``
        - ``%HYPERSHELL_SITE%\Config.toml``

|

**macOS**
    **System**
        - ``/Library/HyperShell``
        - ``/Library/HyperShell/Logs``
        - ``/Library/Preferences/HyperShell/config.toml``
    **User**
        - ``$HOME/Library/HyperShell``
        - ``$HOME/Library/HyperShell/Logs``
        - ``$HOME/Library/Preferences/HyperShell/config.toml``
    **Local**
        - ``$HYPERSHELL_SITE/Library``
        - ``$HYPERSHELL_SITE/Logs``
        - ``$HYPERSHELL_SITE/config.toml``

|

**Linux / POSIX**
    **System**
        - ``/var/lib/hypershell``
        - ``/var/log/hypershell``
        - ``/etc/hypershell.toml``
    **User**
        - ``$HOME/.hypershell/lib``
        - ``$HOME/.hypershell/log``
        - ``$HOME/.hypershell/config.toml``
    **Local**
        - ``$HYPERSHELL_SITE/lib``
        - ``$HYPERSHELL_SITE/log``
        - ``$HYPERSHELL_SITE/config.toml``

------

Runtime Package Resolution
--------------------------

|

.. include:: _include/config_pythonpath.rst

------

Parameter Reference
-------------------

|

.. include:: _include/config_param_ref.rst

------

Task Environment
----------------

|

.. include:: _include/config_task_env.rst

|

We also respect setting the following environment variables to force disable/enable
the use of colors in all console output.

``NO_COLOR``
    If this variable is set to anything but a blank string, all colors are disabled.
    See `no-color.org <https://no-color.org>`_ for details.

``FORCE_COLOR``
    If this variable is set to anything but a blank string, colors will be enabled
    regardless of whether `stdout` or `stderr` are a TTY.

------

Signals
-------

|

HyperShell traps the following UNIX signals (does not apply on Microsoft Windows).

.. include:: _include/signals.rst

|
