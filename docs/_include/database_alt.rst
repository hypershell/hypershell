The program can operate entirely within memory and does not require a database.
This is desirable for ad-hoc, short-lived, or in extreme high-throughput scenarios.
However, some capabilities are only possible when a database is active
(such as server restart, task retry, task history, etc).

Connection details must to be specified by your configuration.
This can be entirely through environment variables or within files.
SQLite is automatically initialized upon opening the connection but
Postgres must be explicitly initialized via ``hs initdb`` or at launch with
the ``--initdb`` command-line option.
An existing database created by an earlier release is upgraded in place on first use,
for both SQLite and Postgres. The upgrade only adds missing tables, columns, and indexes and never
rewrites existing tasks; on Postgres it also drops the ``UNIQUE`` constraints on the retry-chain
columns. It holds an exclusive lock while it runs (minutes, on a large database), and processes
started meanwhile wait for it. A SQLite database created by an
earlier release (2.8, or the 2.9.0a1 pre-release) keeps those constraints, with no effect on
behaviour in normal operation. Only in the rare cases where a failed task could be retried
twice (two servers on one database, which is not supported; a server killed while scheduling a
retry; a task's ``retried`` flag reset after the fact) does it reject the second retry with an
error, where a newer database records it.

SQLite only needs the local file path.

.. code-block:: toml

    [database]
    file = "/var/lib/hypershell/main.db"

Or via a single environment variable, ``HYPERSHELL_DATABASE_FILE=/tmp/pipeline/task.db``.

The default `provider` is SQLite; Postgres must be enabled. A local database with
local account authentication simply needs to know the database name.

.. code-block:: toml

    [database]
    provider = "postgres"
    database = "hypershell"

With the ``timescale`` provider (alias ``timescaledb``) the database is PostgreSQL with the
TimescaleDB extension, and ``hs initdb`` also makes the ``task`` table a hypertable partitioned by
its UUIDv7 ``id``, with columnstore compression and a policy where the server's TimescaleDB edition
and version support them. Every process that submits tasks then needs the ``uuid7`` extra.
See the TimescaleDB section of the database documentation.

The ``port`` is assumed to be 5432 but can be specified. If a ``user`` is given then so too must
a ``password``. The default `public` schema is assumed but can also be specified. Anything else
is forwarded to the database as a connection parameter; e.g., ``encoding = "utf-8"``.

Any parameter ending in the special suffixes ``_env`` or ``_eval`` are interpolated into the
configuration. E.g., ``password_env`` treats its value as the name of an environment variable
and ``password_eval`` executes its value as a shell command, both exposing ``password`` as the
expanded version.

The special ``.echo`` parameter can be set to ``true`` to enable verbose logging of all database
transactions. These will be emitted as ``INFO`` level messages.
