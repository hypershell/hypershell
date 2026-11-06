.. _whats_new:

What's New in 2.9
=================

|

This page lists every user-visible change in the 2.9 line since 2.8.5, the current stable release.
Each entry gives the pre-release that first carried it, the pull request that shipped it (or the
commit, for the few changes made without one), and the section that describes it. That section
carries an "Added in version 2.9.0" or "Changed in version 2.9.0" marker.

The 2.9 releases so far are pre-releases, so ``pip install hypershell`` still installs 2.8.x.
Install the latest pre-release with ``pip install --pre hypershell``, or pin one, such as
``hypershell==2.9.0a6``.

-------------------

Upgrading from 2.8
------------------

|

- **Upgrade the server and every client together.** A 2.8 client can't work with a 2.9 server, and a
  2.9 client can't work with a 2.8 server. Since 2.9.0a4 a mismatched pairing fails fast: the client
  exits with status 5 and an error that names the version mismatch, and a server refuses a client
  that is too old and keeps serving the others (2.9.0a4, :pr:`78`).

- **Back up your database first.** The first 2.9 process to open a database created by 2.8 upgrades
  it in place, on SQLite and PostgreSQL alike. The upgrade only adds missing tables, columns and
  indexes, and never drops or rewrites tasks, but it is forward-only: there is no downgrade. Copy
  the SQLite file, or dump the PostgreSQL database, before the first 2.9 start (2.9.0a2, :pr:`69`;
  :ref:`database initialization <database_initialization>`).

- **An upgraded SQLite database keeps the old retry-chain constraints.** SQLite can't drop a table
  constraint without rebuilding the table, so the ``UNIQUE`` constraints on ``previous_id`` and
  ``next_id`` stay. Normal operation is unaffected. Only where a failed task could be retried twice
  (two servers on one database, a server killed while scheduling a retry, or a ``retried`` flag
  reset by hand) does the older database reject the second retry with an error (2.9.0a2, :pr:`69`).

- **PostgreSQL locks its tables during the upgrade.** Adding columns and building indexes can take
  minutes on a large database. Processes started meanwhile wait, log a line every 30 seconds, and
  then continue. If the lock is still held after 15 minutes, the upgrade stops with an error naming
  what it waited on and leaves the schema unchanged (2.9.0a2, :pr:`69`).

- **Queue submission needs a server without a database.** ``hs submit -q`` works only against a
  server started with ``--no-db``. A server with a database schedules only from it, so it refuses
  the submission and ``hs submit`` exits with an error. In 2.8 the tasks ran but their results were
  not recorded (2.9.0a4, :pr:`78`; :ref:`hs submit <cli_submit>`).

- **Task files given by name load fully before scheduling.** With ``hs cluster FILE`` and ``hs
  server FILE`` the first task starts once the whole file is in the database, and task groups run in
  ascending order whatever order the file lists them in. Piped input still streams (2.9.0a3,
  :pr:`74`). This also shipped in 2.8.4, so it matters only when upgrading from 2.8.3 or earlier.

- **Launched clients take the run key from their environment.** Every client that ``hs cluster``
  starts as a process of its own (``--launcher``, ``--mpi``, ``--ssh``, ``--autoscaling``) gets the
  key in ``HYPERSHELL_SERVER_AUTH``, or over the ssh channel, never on a command line, and its tasks
  no longer see it. A launcher must pass the environment through: ``srun --export=NONE`` leaves its
  clients without a key (2.9.0a5, :pr:`80`; :ref:`security <security>`). This also shipped in 2.8.5,
  so it matters only when upgrading from 2.8.4 or earlier.

-------------------

New and changed
---------------

|

Scheduling and zones
^^^^^^^^^^^^^^^^^^^^

- **Zones.** ``--zone`` on ``hs submit`` places tasks in a named zone, ``--zone`` on ``hs client``
  runs only that zone's tasks, and ``hs cluster --zone`` does both. Tasks and clients without one
  are in the default zone, so a run that uses no zones behaves as before. The Python API takes a
  ``zone`` parameter too (2.9.0a4, :pr:`78`; :ref:`hs submit <cli_submit>`, :ref:`hs client
  <cli_client>`, :ref:`hs cluster <cli_cluster>`, :ref:`hypershell.client <mod_client>`,
  :ref:`hypershell.cluster <mod_cluster>`).

- **Clients request their work.** A client asks the server for each bundle, and an explicit ``hs
  client -b`` is the size it asks for; a client left at the default gets the server's ``-b``. The
  new ``server.bundlemax`` setting caps every reply (2.9.0a4, :pr:`78`; :ref:`hs client
  <cli_client>`, :ref:`hs server <cli_server>`, :ref:`configuration reference <config_params>`).

- **The task queues are served inside the server process**, with no separate queue-manager process
  (2.9.0a4, :pr:`76`; :ref:`security components <security_components>`).

- **Per-executor task slot.** ``TASK_SLOT`` and ``TASK_SLOT_COUNT`` in the task environment, and the
  ``{slot}`` and ``{slot_count}`` template patterns, let each task claim its own cores or GPUs
  (2.9.0a1, :pr:`52`; :ref:`execution slot <templates_slot>`, :ref:`task environment
  <config_task_env>`).

Relays
^^^^^^

- **hs relay** joins a site to an upstream server, so one campaign can draw on several
  administrative sites while the site's nodes never connect upstream. A relay keeps its own SQLite
  database and holds the upstream key itself; it is configured under ``[relay]``.
  ``AutoScalingCluster`` gains an ``evict_after`` parameter (2.9.0a6, :pr:`82`; :ref:`hs relay
  <cli_relay>`, :ref:`relay database <database_relay>`, :ref:`relay security <security_relays>`,
  :ref:`configuration reference <config_params>`, :ref:`hypershell.cluster <mod_cluster>`).

Database and providers
^^^^^^^^^^^^^^^^^^^^^^

- **In-place database upgrade.** A database from an earlier release is upgraded on first use, as
  described above, and the Python API gains ``upgradedb()`` (2.9.0a2, :pr:`69`; :ref:`database
  initialization <database_initialization>`, :ref:`hs initdb <cli_initdb>`, :ref:`hypershell.data
  <mod_data>`).

- **TimescaleDB.** The ``timescale`` provider makes the task table a hypertable with columnstore
  compression and a compression policy, set under ``[database.timescale]``; every process that
  submits tasks needs the ``uuid7`` extra (2.9.0a3, :pr:`71`; :ref:`TimescaleDB
  <database_timescale>`, :ref:`configuration reference <config_params>`).

- **Partition column.** ``hs initdb --rotate`` records the partition in a ``part`` column instead of
  a ``part:N`` tag, and ``hs list --part N`` lists one partition (2.9.0a2, :pr:`59`; :ref:`hs initdb
  <cli_initdb>`, :ref:`hs list <cli_task_search>`).

Submitting tasks
^^^^^^^^^^^^^^^^

- **Tasks from JSON.** ``--from-json FILE[@path]`` on ``hs submit`` and ``hs cluster`` reads a list
  of task objects whose keys become named ``{key}`` template fields and task tags (2.9.0a1,
  :pr:`48`; :ref:`hs submit <cli_submit>`, :ref:`named fields <templates_named_fields>`).

- **Source-gated re-submission.** A named task file is recorded as a source, and submitting it again
  is refused unless ``--repeat`` (submit it all again) or ``--update`` (submit only what is new) is
  given. ``hs cluster FILE --restart`` submits only the tasks that never landed, so a requeued job
  is safe to run again (2.9.0a1, :pr:`50`; :ref:`hs submit <cli_submit>`, :ref:`hs cluster
  <cli_cluster>`).

Listing and updating tasks
^^^^^^^^^^^^^^^^^^^^^^^^^^

- **Card format.** ``-f card`` on ``hs list``, ``hs info`` and ``hs wait`` shows each task as a
  bordered panel with a color-coded status badge (2.9.0a2, :pr:`60`; :ref:`hs list
  <cli_task_search>`, :ref:`hs info <cli_task_info>`, :ref:`hs wait <cli_task_wait>`).

- **Result guard.** ``hs list`` refuses more than 1,000 results unless ``--all``, ``--limit`` or
  ``--count`` is given (2.9.0a1, :commit:`961fb22`; :ref:`hs list <cli_task_search>`).

- **Colorized listings.** ``hs list`` output to a terminal is colored by exit status (2.9.0a1,
  :commit:`210a93e`; :ref:`hs list <cli_task_search>`).

- **Signals and cancellation.** ``-X``/``--cancelled`` and ``--signal NAME`` on ``hs list`` and ``hs
  update`` match cancelled tasks and tasks killed by a signal, and ``SIGHUP`` cancels a running task
  on its client (2.9.0a1, :commit:`27db1b0`; :ref:`hs list <cli_task_search>`, :ref:`hs update
  <cli_task_update>`).

Logging
^^^^^^^

- **Ephemeral lock sidecars.** A process removes its log file's ``.lock`` sidecar on a clean exit,
  and the next process sweeps one left by a crash (2.9.0a2, :pr:`54`; :ref:`per-process log files
  <logging_files>`).

Packaging
^^^^^^^^^

- **Container images.** Each tagged release from 2.9 on is published to the GitHub Container
  Registry as a multi-architecture image, signed with cosign and carrying SLSA build provenance
  (2.9.0a1, :commit:`ee03014`; :ref:`container images <install_containers>`).

-------------------

Not yet released
----------------

|

Nothing is pending: everything merged on ``develop`` so far shipped in 2.9.0a6.

|
