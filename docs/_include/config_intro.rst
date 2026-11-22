Most of the choices that `HyperShell` makes about timing, task bundling, coordination, logging,
and such are configurable by the user. This configuration is loaded when the program starts
and is constructed from several sources including an ordered merger of files, environment variables,
and command-line options.

In order of precedence (lowest to highest), three files are loaded for `system`, `user` and `local`
configuration. See below the section on file system paths for more details.

On Linux (for example):

.. table::
    :widths: 30 70

    ================  =================================
    Site              Path (Linux / POSIX)
    ================  =================================
    System            ``/etc/hypershell.toml``
    User              ``~/.hypershell/config.toml``
    Local             ``./.hypershell/config.toml``
    ================  =================================


The `TOML <https://toml.io>`_ format is modern and minimal.

Every configurable option can be set in one of these files. Further, every option can
also be set by an environment variable: ``HYPERSHELL_`` followed by the option's path, with each
``.`` written as ``_`` (see :ref:`Environment Variables <config_env>` below).

For example, set the logging level at the user level with a command:

.. admonition:: Set user-level configuration option
    :class: note

    .. code-block:: shell

        hs config set logging.level info --user

The file should now look something like this:

.. admonition:: ~/.hypershell/config.toml
    :class: note

    .. code-block:: toml

        # File automatically created on 2022-07-02 11:57:29.332993
        # Settings here are merged automatically with defaults and environment variables

        [logging]
        level = "info"

Alternatively, you can set an environment variable and the runtime configuration
would be equivalent:

.. admonition:: Define environment variable
    :class: note

    .. code-block:: shell

        export HYPERSHELL_LOGGING_LEVEL=INFO

Finally, any option defined within a configuration file that ends with ``_env`` or ``_eval``
(or by a variable that ends with ``_ENV`` or ``_EVAL``) is automatically expanded by the given
environment variable or shell expression, respectively. This is useful as both a dynamic feature
but also as a means to obfuscate sensitive information, such as database connection details.

.. admonition:: ~/.hypershell/config.toml
    :class: note

    .. code-block:: toml

        # File automatically created on 2022-07-02 11:57:29.332993
        # Settings here are merged automatically with defaults and environment variables

        [logging]
        level = "info"

        [database]
        provider = "postgres"
        database = "hypershell"
        host = "my.instance.university.edu"
        user = "me"
        password_eval = "pass hypershell/database/password"  # Decrypt using GNU Pass

The `edit` command makes it easy to open the correct configuration file path for your platform
using the default editor. Set the ``EDITOR`` environment variable (or ``VISUAL`` on `Windows`).

.. admonition:: Open global user-level configuration file for editing
    :class: note

    .. code-block:: shell

        hs config edit --user

------

.. _config_env:

Environment Variables
---------------------

|

Every key in the :ref:`parameter reference <config_params>` can also be set by an environment
variable. The name is ``HYPERSHELL_`` followed by the key's path, with each ``.`` written as ``_``:
``logging.level`` is ``HYPERSHELL_LOGGING_LEVEL`` and ``server.tls.min_version`` is
``HYPERSHELL_SERVER_TLS_MIN_VERSION``. The prefix is written in capitals; the rest may be in any
letter case (``HYPERSHELL_server_tls_min_version`` sets the same key). A variable takes precedence
over every configuration file, and a command-line option over the variable.

.. admonition:: Set keys whose names have an underscore
    :class: note

    .. code-block:: shell

        export HYPERSHELL_SERVER_TLS_MIN_VERSION=TLSv1.3
        export HYPERSHELL_RELAY_LAUNCHER_ARGS="--mem=4G"
        export HYPERSHELL_AUTOSCALE_FAILED_LAUNCHES=5

A name is matched against the keys HyperShell knows, not split at every underscore, so a key whose
own name has an underscore is reached as written above. Past the path of a table whose entries you
name yourself, the rest of the name is one entry, underscores kept, in lower case:

- ``HYPERSHELL_DATABASE_PRAGMAS_JOURNAL_MODE`` sets ``database.pragmas.journal_mode``, and likewise
  for ``database.connect_args``;
- ``HYPERSHELL_SSH_NODELIST_MY_GROUP`` sets the ``[ssh.nodelist]`` group ``my_group``;
- a name past ``HYPERSHELL_DATABASE_`` that is no ``[database]`` key, such as
  ``HYPERSHELL_DATABASE_SSL_MODE``, is a provider argument (``ssl_mode``);
- ``HYPERSHELL_EXPORT_<NAME>`` gives every task the variable ``<NAME>``, letter case as written
  (see :ref:`Task Environment <config_task_env>`).

A relay zone table is named by ``HYPERSHELL_RELAY_ZONE_<ZONE>_<KEY>``, where ``<KEY>`` is one of
the zone keys (``LAUNCHER``, ``LAUNCHER_ARGS``, ``SIZE``, ``MIN``, ``MAX``, ``INIT``) and the part
before it is the zone name: ``HYPERSHELL_RELAY_ZONE_GPU_A100_LAUNCHER_ARGS`` sets ``launcher_args``
of zone ``gpu_a100``. A ``HYPERSHELL_RELAY_ZONE_`` name that ends in no zone key, and
``HYPERSHELL_RELAY_ZONE`` set together with a zone table variable, are refused.

``HYPERSHELL_<KEY>_ENV`` and ``HYPERSHELL_<KEY>_EVAL`` work as ``<key>_env`` and ``<key>_eval`` do
in a file (see above): ``HYPERSHELL_SERVER_AUTH_ENV=MY_KEY`` reads the key from ``MY_KEY``. Two
forms of one key (``server.auth`` or ``relay.upstream.auth``) set in the environment are refused,
as they are in one file.

.. _config_value_types:

The value is read by the key's type:

- A key that takes text holds the value as written, including ``false``, ``null``, an empty value,
  leading zeros and an exponent (``HYPERSHELL_SERVER_AUTH=0012345678901234`` is that key).
- An empty value for an optional text key is not given, and the key's default applies, as if the
  variable were not set. The optional text keys are those with no default or one that means none
  (``<none>``, ``<auto>``), such as ``logging.file`` and its ``path``, ``database.user`` and
  ``database.password``, the ``[server.tls]`` files, ``server.advertise`` and
  ``relay.advertise``; so are ``server.bind``, ``relay.bind``, ``database.host`` and
  ``task.cwd``, so an empty bind, host or task directory is the default one. A text key with a
  default of its own holds an empty value as written, as in a file: ``HYPERSHELL_DATABASE_TIMESCALE_SEGMENTBY=`` is ``segmentby = ""``. In a
  file, an empty value is read as before.
- A key that takes a whole number, a number or a boolean holds the converted value. A boolean takes
  ``true``/``false``, ``yes``/``no``, ``on``/``off`` or ``1``/``0``, in any letter case. A value
  that does not convert, an empty one included, stops the command with a configuration error naming
  the variable.
- A duration or a size takes the forms its entry gives (e.g., ``HYPERSHELL_SERVER_EVICT=10m``,
  ``HYPERSHELL_TASK_MEMORY=16G``), checked by the command that reads it.
- An entry with no type of its own (one you name in ``database.connect_args``,
  ``database.pragmas`` or ``[database]``, and a name held as below) holds a TOML integer, float or
  boolean when the value is exactly one (``5``, ``2.5``, ``true``), and the text otherwise
  (``007``, ``True``). An ``[ssh.nodelist]`` group, a zone's ``launcher`` and ``export`` entries are
  text; a zone's sizes and bounds are whole numbers.

A few names are read by the program itself and are not configuration keys:
``HYPERSHELL_SITE`` and ``HYPERSHELL_CONFIG_FILE`` (see *Filesystem Paths* below),
``HYPERSHELL_PYTHONPATH`` (see *Runtime Package Resolution* below) and ``HYPERSHELL_ZONE``, which
a launched *client* is given.

A few cases to keep in mind:

- A ``HYPERSHELL_`` name that matches no key is held as one key, underscores kept, under the
  deepest table its name spells (``HYPERSHELL_SERVER_PORT_X`` is ``server.port_x``); nothing reads
  it, and it is not an error.
- Two variables that set one key (``HYPERSHELL_SERVER_PORT`` and ``HYPERSHELL_server_port``) are
  refused.
- ``logging.file`` takes a word (``true``, ``enabled``) or a path; set beside
  ``HYPERSHELL_LOGGING_FILE_*`` entries, a path becomes ``logging.file.path``. For
  ``ssh.nodelist`` and ``database``, entries beneath them win over a value given for the table.
- A name equal to a table's own path (``HYPERSHELL_SERVER_TLS``) replaces the whole table with its
  text; do not set one.

Some names cannot be reached from the environment, only from a file: a ``[database]`` provider
argument whose name begins ``connect_args_``, ``pragmas_`` or ``timescale_`` (those name the
``[database]`` tables), and any table entry or zone name with a character a variable name cannot
hold (e.g., a zone ``gpu-a100``).

.. only:: html

    .. versionchanged:: 2.9.0
       A key whose name has an underscore, open-table entries, zone tables, ``_ENV``/``_EVAL``,
       values as written, and an empty value for an optional text key read as not given.
