|

*HyperShell* can operate entirely within memory and does not require a database.
This is desirable for ad-hoc, short-lived, or in extreme high-throughput scenarios.
However, some capabilities are only possible when a database is active
(such as server restart, task retry, task history, etc).

-------------------

Connecting
----------

|

Connection details must to be specified by your configuration.
This can be entirely through environment variables or within files.

SQLite only needs the local file path.

.. admonition:: Configuration with SQLite file path
    :class: note

    .. code-block:: toml

        [database]
        file = "/var/lib/hypershell/main.db"

Or via a single environment variable, ``HYPERSHELL_DATABASE_FILE=/tmp/pipeline/task.db``.

The default `provider` is SQLite; Postgres must be enabled and requires the ``postgres``
extra (or a production variant — see :ref:`install`). A local database with local account
authentication simply needs to know the database name.

.. admonition:: Configuration file with basic Postgres
    :class: note

    .. code-block:: toml

        [database]
        provider = "postgres"
        database = "hypershell"

The ``port`` is assumed to be 5432 but can be specified. If a ``user`` is given then so too must
a ``password``. The default `public` schema is assumed but can also be specified. Anything else
is forwarded to the database as a connection parameter; e.g., ``encoding = "utf-8"``.

Any parameter ending in the special suffixes ``_env`` or ``_eval`` are interpolated into the
configuration. E.g., ``password_env`` treats its value as the name of an environment variable
and ``password_eval`` executes its value as a shell command, both exposing a ``.password`` as the
expanded version.

The special ``.echo`` parameter can be set to ``true`` to enable verbose logging of all database
transactions. These will be emitted as ``INFO`` level messages.

See also the *database* section in the :ref:`configuration <config>` parameter reference.

-------------------

Initialization
--------------

|

SQLite is automatically initialized upon opening the connection but
Postgres must be explicitly initialized via ``hs initdb`` or at launch with
the ``--initdb`` command-line option.

See also the :ref:`command-line interface <cli_initdb>` for ``hs initdb``.
