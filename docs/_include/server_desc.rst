Launch server, schedule directly or asynchronously from database.

The server includes a scheduler component that pulls tasks from the database and stages them
for clients, which request bundles of them when they are ready for work. It also has a receiver that collects the results
of finished tasks. Optionally, the server can submit tasks (*FILE*). When submitting tasks,
the ``-w``/``--bundlewait`` and ``-b``/``--bundlesize`` options are the same as for the
*submit* workflow.

With ``--max-retries`` greater than zero, the scheduler will check for a non-zero exit status
for tasks and re-submit them if their previous number of attempts is less.

Tasks are bundled for each client at the size it asks for, or at the server's ``-b``/``--bundlesize``
when it asks for none. By default the bundle size is one, meaning that at small scales there is
greater concurrency.

With ``--autoscaling``, the server schedules forever and launches nothing. Relays connected with its
key advertise the zones they serve and each zone's ``min``, ``max`` and ``init`` clients, and the server
publishes how many clients each relay should run in each zone, for the relay to launch with its own
launcher (see :ref:`autoscaling with relay sites <cli_server_sites>`).

The server takes its key from the ``HYPERSHELL_SERVER_AUTH`` environment variable or from
``server.auth`` in a configuration file (or its ``auth_env`` and ``auth_eval`` forms), never from
the command line. It refuses to start with the built-in placeholder key, or with a key shorter
than 16 characters or outside ``[A-Za-z0-9._+/=-]``.
