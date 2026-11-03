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

SQLite Pragmas
--------------

|

When using SQLite you can tune the underlying database connection with `pragmas
<https://sqlite.org/pragma.html>`_. Because pragmas apply per-connection and do not persist,
*HyperShell* re-applies them automatically every time it opens a connection. Specify them as a
``pragmas`` table within the ``[database]`` section:

.. admonition:: Configuration with SQLite pragmas
    :class: note

    .. code-block:: toml

        [database]
        file = "/var/lib/hypershell/main.db"
        pragmas = {journal_mode = "wal", cache_size = 10000, busy_timeout = 5000}

Each entry is issued verbatim as ``PRAGMA <name>=<value>`` on connect. The example above is a good
general-purpose starting point for concurrent workflows: WAL journaling improves reader/writer
concurrency, a larger page cache reduces I/O, and a busy timeout lets writers wait briefly rather
than failing immediately when the database is momentarily locked.

Individual pragmas can also be set from the command line:

.. admonition:: Set a single pragma
    :class: note

    .. code-block:: shell

        hs config set database.pragmas.journal_mode wal

.. note::

    Running ``hs initdb`` (or launching with ``--initdb``) additionally performs a
    ``PRAGMA optimize``. This is safe to run routinely and helps SQLite keep its query-planner
    statistics current. See `optimize <https://sqlite.org/pragma.html#pragma_optimize>`_ for details.

-------------------

Initialization
--------------

|

SQLite is automatically initialized upon opening the connection but
Postgres must be explicitly initialized via ``hs initdb`` or at launch with
the ``--initdb`` command-line option.

An existing database created by an earlier release is upgraded in place on first use,
for both SQLite and Postgres; there is no separate migration step. The upgrade only adds what
is missing (tables, columns, and indexes) and never drops or rewrites existing tasks. The one
removal is on Postgres, where the ``UNIQUE`` constraints on the retry-chain columns
(``previous_id`` and ``next_id``) are dropped. The upgrade holds an exclusive lock on the
tables while it adds columns and builds indexes, which can take minutes on a large database.
Processes started meanwhile wait for it, logging a line every 30 seconds, and then carry on.
If the lock is still held after 15 minutes (e.g., by a session left idle in a transaction), the
upgrade gives up with an error that names what it waited on and leaves the schema unchanged;
find and stop that session, then try again.

SQLite cannot drop a table constraint without rebuilding the table, so a SQLite database
created by an earlier release (2.8, or the 2.9.0a1 pre-release) keeps those ``UNIQUE``
constraints. This has no effect on behaviour in normal operation. It only matters in the rare cases where a failed task could be retried twice: two
servers running against one database (which is not supported), a server killed between
scheduling a retry and marking the original task as retried, or a task's ``retried`` flag reset
after the fact. In those cases an older SQLite database rejects the second retry with an error,
where a newer one records it.

See also the :ref:`command-line interface <cli_initdb>` for ``hs initdb``.

-------------------

TimescaleDB
-----------

|

`TimescaleDB <https://github.com/timescale/timescaledb>`_ is a PostgreSQL extension for
time-series data. It splits a large table into *chunks* by time (a *hypertable*) and can convert
older chunks into a compressed, column-oriented *columnstore*. A long-lived *HyperShell* database
fits this shape well: tasks arrive in time order, recent tasks are read and updated constantly,
and old tasks are kept for history but rarely touched.

Select it with the ``timescale`` provider (``timescaledb`` is an alias). It connects exactly like
PostgreSQL and needs both the ``postgres`` and the ``uuid7`` extras (see :ref:`install`).

.. admonition:: Install with TimescaleDB support
    :class: note

    .. code-block:: shell

        uv tool install 'hypershell[postgres,uuid7]'

.. admonition:: Configuration file with TimescaleDB
    :class: note

    .. code-block:: toml

        [database]
        provider = "timescale"
        database = "hypershell"

Then initialize the database as usual, with ``hs initdb`` or ``--initdb`` at launch.
There is nothing else to run.

|

What initialization does
^^^^^^^^^^^^^^^^^^^^^^^^

Before it creates any table, ``hs initdb`` checks the server. The ``timescaledb`` extension must
be installed on the server; if it is not yet created in the database we create it (the
database owner may do so, no superuser needed). We fail with a message naming the problem and the fix,
and create nothing, when the server does not offer the extension, when the role may not create
it, when the server does not preload its library (``shared_preload_libraries``), or when the
extension is older than 2.22.0.

After the usual schema step, we bring the ``task`` table to its TimescaleDB shape:

* **Hypertable.** ``task`` becomes a hypertable partitioned by its ``id`` column, in chunks of
  one week. The ``client`` and ``source`` tables stay plain tables.
* **Columnstore.** The columnstore is enabled, segmented by ``client_id`` and ordered by
  ``id DESC``, so a compressed chunk keeps each client's tasks together, newest first.
* **Policy.** A columnstore policy converts chunks older than four weeks.

This is equivalent to the following statements, which you therefore never need to run:

.. admonition:: What ``hs initdb`` does for you
    :class: note

    .. code-block:: sql

        SELECT create_hypertable('task', by_range('id', INTERVAL '1 week'));
        ALTER TABLE task SET (timescaledb.enable_columnstore = true,
                              timescaledb.segmentby = 'client_id',
                              timescaledb.orderby = 'id DESC');
        CALL add_columnstore_policy('task', after => INTERVAL '4 weeks');

Each step is checked first and applied only where something is missing, so running
``hs initdb`` again, launching with ``--initdb`` every time, or truncating the database with
``hs initdb --truncate`` always arrives at the same state, with one policy. A database already in
shape logs nothing. Initialization runs in one transaction, serialized with the schema step, so
concurrent launches are safe.

If someone changed the chunk interval, the columnstore settings, or the policy's interval by hand,
the next ``hs initdb`` puts them back to the configured values (see *Tuning* below) and logs each
change. A new chunk interval applies to new
chunks only, and new columnstore settings apply to chunks converted from then on; existing chunks
keep theirs (recompress them by hand if needed). Turning the columnstore off is never done for
you; ``hs initdb`` warns instead. If the policy's last run failed, ``hs initdb`` warns and points
to ``timescaledb_information.job_stats``.

Other commands (``hs cluster``, ``hs server``, ``hs submit`` without ``--initdb``) only run the
schema step, as for PostgreSQL; run ``hs initdb`` after upgrading *HyperShell* or TimescaleDB.

|

Tuning
^^^^^^

The defaults suit most sites; zero configuration is the norm. To change them, add a ``timescale``
table to the ``[database]`` section. Each key is optional and falls back to its default.

.. admonition:: Configuration file with TimescaleDB settings
    :class: note

    .. code-block:: toml

        [database]
        provider = "timescale"
        database = "hypershell"

        [database.timescale]
        chunk_interval = "1 day"
        compress_after = "2 weeks"

==================  ===============  ==========================================================
Key                 Default          Meaning
==================  ===============  ==========================================================
``chunk_interval``  ``"1 week"``     Time span of each chunk of ``task``.
``columnstore``     ``true``         Enable the columnstore on ``task``.
``segmentby``       ``"client_id"``  Columns that group rows in a compressed chunk
                                     (comma-separated task columns, each once, or ``""`` for none).
``orderby``         ``"id DESC"``    Order of rows in a compressed chunk
                                     (``<column> [ASC|DESC] [NULLS FIRST|LAST]``, comma-separated,
                                     each column once). TimescaleDB appends ``id DESC`` when
                                     neither list names ``id``.
``policy``          ``true``         Keep a columnstore policy on ``task``.
``compress_after``  ``"4 weeks"``    Age at which the policy converts a chunk.
==================  ===============  ==========================================================

Intervals are a positive whole number and a unit, from ``microseconds`` to ``months``
(e.g., ``"12 hours"``). Columns are task columns by name, quoted for you where SQL needs it
(``group``, for instance). An invalid value, an unknown or repeated column, an unknown key, or
``policy = true`` with ``columnstore = false`` stops every command at startup with a message
naming the key.

Changes take effect at the next ``hs initdb``, which applies them as described above: a new
``chunk_interval`` applies to new chunks only, new ``segmentby`` or ``orderby`` to chunks converted
from then on, and a new ``compress_after`` replaces the policy. Setting ``policy = false`` removes
an existing policy. Setting ``columnstore = false`` (with ``policy = false``) stops ``hs initdb``
from enabling the columnstore, but does not disable one that is already on; it warns instead.

|

Why the task identifier is the partition key
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

With the ``uuid7`` extra, every task identifier is a
`UUIDv7 <https://www.rfc-editor.org/rfc/rfc9562#name-uuid-version-7>`_, whose leading 48 bits are
the time it was created, in milliseconds. Since TimescaleDB 2.22.0 a hypertable can be partitioned
directly by such a column: the timestamp inside the id decides the chunk. So the primary key
carries row identity, creation time, and chunk routing in one value, and we need no separate time
column. A lookup or update by ``id`` needs only one chunk, and a range of ids is a range of
time, which lets PostgreSQL skip every chunk outside it (*chunk exclusion*).

.. important::

    **Every process that creates tasks needs the** ``uuid7`` **extra**, not only the server:
    anything running ``hs submit``, ``hs cluster`` or ``hsx``, or using the library to submit
    tasks. TimescaleDB routes each row by the timestamp inside its id and rejects any other UUID
    version: inserting a UUIDv4 fails with SQLSTATE ``0A000`` (``... is not a version 7 UUID``),
    and the error aborts the whole ``INSERT``, so one bad id loses the entire bundle of tasks
    submitted with it. *HyperShell* refuses to start under the ``timescale`` provider without
    the extra.

|

Support matrix
^^^^^^^^^^^^^^

=====================  =============================================================================
TimescaleDB            Behavior
=====================  =============================================================================
before 2.22.0          ``hs initdb`` fails: partitioning by a UUIDv7 column is not available.
2.22.0 – 2.24.x        Hypertable and columnstore; **no policy**. These releases accept the policy,
                       but every run of it fails on a UUID-partitioned table, so we skip it and warn.
2.25.0 and later       Everything above. Tested in CI on 2.31.0 (and on 2.24.0 for the gate).
=====================  =============================================================================

Current TimescaleDB releases support PostgreSQL 16, 17 and 18 (2.29.0 dropped PostgreSQL 15).

TimescaleDB comes in two editions, and ``hs initdb`` reads which one the server runs
(``timescaledb.license``):

* **Community** (``timescaledb.license = 'timescale'``, the Timescale License): everything above.
  This is what the official ``timescale/timescaledb`` container images and the TimescaleDB
  packages ship.
* **Apache** (``timescaledb.license = 'apache'``, Apache-2.0 only, e.g. the ``-oss`` images):
  hypertables, but no columnstore and no policy. We create the hypertable and skip the rest, with
  a warning each time.

Where you run PostgreSQL matters. Self-hosted servers can use the official packages or container
images. *Tiger Cloud* is the managed service from the makers of TimescaleDB. Several other
managed PostgreSQL offerings do not ship the extension at all, and some ship only the Apache
edition (as of August 2026); check your provider's list of extensions before choosing the
``timescale`` provider there.

The project's naming has moved over time: *TimescaleDB* is the extension; the company, formerly
Timescale, is *TigerData* since 2025; and *Tiger Cloud* is its managed service.

|

Querying by time
^^^^^^^^^^^^^^^^

TimescaleDB adds functions for UUIDv7 values (since 2.22.0). To filter tasks by time, compare the
``id`` against a boundary id built from a timestamp with ``to_uuidv7_boundary()``. To show when a
task was created, read the timestamp back out with ``uuid_timestamp()``.

.. admonition:: Tasks created in the last day, with their creation time
    :class: note

    .. code-block:: sql

        SELECT uuid_timestamp(id) AS created, args, exit_status
          FROM task
         WHERE id >= to_uuidv7_boundary(now() - INTERVAL '1 day')
         ORDER BY id;

.. warning::

    Filter on ``id`` itself, as above. A predicate on a function of the id, such as
    ``WHERE uuid_timestamp(id) > now() - INTERVAL '1 day'``, cannot use chunk exclusion and scans
    every chunk, compressed ones included.

|

Background workers
^^^^^^^^^^^^^^^^^^

Policies run as TimescaleDB background jobs. The server needs one worker for the launcher, one
scheduler for each database that has the extension, and one more for each job while it runs, all
counted against ``timescaledb.max_background_workers`` (default 16), which in turn must fit within
PostgreSQL's ``max_worker_processes``. When the workers run out, a database's scheduler does not
start and its policies silently never run: the server log notes it once, and ``hs initdb`` cannot
see it. If many databases on one server use the extension, raise
``timescaledb.max_background_workers`` (and ``max_worker_processes``) and restart the server. See
the TimescaleDB documentation on configuring background workers.

|

Converting an existing table
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``hs initdb`` converts ``task`` only while it is empty. A plain ``task`` table that already holds
tasks (for example, a database created by an earlier release, or under the ``postgres`` provider)
is left as it is: ``hs initdb`` succeeds, upgrades the schema, and warns. Everything keeps working
on the plain table.

To convert it yourself, stop every *HyperShell* process using the database and run:

.. admonition:: Convert a populated task table by hand
    :class: note

    .. code-block:: sql

        SELECT create_hypertable('task', by_range('id', INTERVAL '1 week'),
                                 create_default_indexes => false, migrate_data => true);

then run ``hs initdb`` to add the columnstore and its policy. Be aware that:

* It copies every row into chunks in a single transaction, holding an exclusive lock on ``task``
  throughout; on a large table this takes a long time and needs room for a second copy.
* It aborts, converting nothing, if any task id is not a UUIDv7. Databases written without the
  ``uuid7`` extra (e.g., by releases before 2.9 with only the ``postgres`` extra) hold UUIDv4 ids
  and cannot be converted in place; start a new database instead.

|

Beyond the defaults
^^^^^^^^^^^^^^^^^^^

We set up only what every *HyperShell* database benefits from. Data retention
(``add_retention_policy``), continuous aggregates, and tiered storage are left to you, through
TimescaleDB directly; they work on the ``task`` hypertable like any other.
