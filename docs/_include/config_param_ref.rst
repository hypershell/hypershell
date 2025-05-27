``[logging]``
    Logging configuration. See also :ref:`logging <logging>` section.

    ``.level``
        One of ``TRACE``, ``DEBUG``, ``INFO``, ``WARNING``,
        ``ERROR``, or ``CRITICAL`` (default: ``WARNING``)

        ``INFO`` level messages are reserved for clients when tasks begin running.
        There are numerous WARNING events (e.g., non-zero exit status of a task).
        ``DEBUG`` level messages signal component thread start/stop and individual task
        level behavior. ``TRACE`` contains detailed information on all other behavior,
        particular iterative messages while components are waiting for something.

        ``ERROR`` messages track when things fail but the application can continue; e.g.,
        when command template expansion fails on an individual task.

        ``CRITICAL`` messages are emitted when the application will halt or crash.
        Some of these are expected (such as incorrect command-line arguments) but in
        the event of an uncaught exception within the application a full traceback is
        written to a file and logged.

    ``.datefmt``
        Date/time format, standard codes apply (default: ``'%Y-%m-%d %H:%M:%S'```)

    ``.format``
        Log message format.

        Default set by the "default" ``logging.style``.
        See the `available attributes <https://docs.python.org/3/library/logging.html#logrecord-attributes>`_
        defined by the underlying Python logging interface.

        Additional attributes provided beyond the standard include `app_id`, `hostname`, `hostname_short`,
        `relative_name`, time formats in `elapsed`, `elapsed_ms`, `elapsed_delta`, and `elapsed_hms`,
        as well as all ANSI colors and formats as `ansi_x` where x is one of `reset`, `bold`, `faint`,
        `italic`, `underline`, `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`, and
        `ansi_level` contains the standard color for the current message severity level.

    ``.style``
        Presets for ``logging.format`` which can be difficult to define correctly.
        Options are `default`, `detailed`, `detailed-compact`, and `system`.


``[database]``
    Database configuration and connection details.
    See also :ref:`database <database>` section.

    ``.provider``
        Database provider (default: 'sqlite'). Supported alternatives include
        'postgres' (or compatible). Support for other providers may be considered in
        the future.

    ``.file``
        Only applicable for SQLite provider.
        SQLite does not understand any other connection detail.

    ``.database``
        Name for database. Not applicable for SQLite.

    ``.schema``
        Not applicable for all RDMS providers.
        For Postgres the default schema is ``public``.
        Specifying the schema may be useful for having multiple instances within the same database.

    ``.host``
        Hostname or address of database server (default: `localhost`).

    ``.port``
        Port number to connect with database server.
        The default value depends on the provider, e.g., 5432 for Postgres.

    ``.user``
        Username for databaser server account.
        If provided a ``password`` must also be provided.
        Default is the local account.

    ``.password``
        Password for database server account.
        If provided a ``user`` must also be provided.
        Default is the local account.

        See also note on ``_env`` and ``_eval``.

    ``.echo``
        Special parameter enables verbose logging of all database transactions.

    ``[connection_args]``
        Specify additional connection details for the underlying SQL dialect provider,
        e.g., ``sqlite3`` or ``psycopg2``.

    ``[pragmas]``
        Specify one or more pragmas to apply to database connections (SQLite only).
        For example ``pragmas = { journal_mode = "wal" }`` to enable WAL-mode.

    ``*``
        Any additional arguments are forwarded to the provider, e.g., ``encoding = 'utf-8'``.


``[server]``
    Section for `server` workflow parameters.

    ``.bind``
        Bind address (default: `localhost`).

        When running locally, the default is recommended. To allow remote *clients* to connect
        over the network, bind the server to *0.0.0.0*.

    ``.port``
        Port number (default: `50001`).

        This is an arbitrary choice and simply must be an available port. The default option chosen
        here is typically available on most platforms and is not expected by any known major software.

    ``.auth``
        Cryptographic authentication key to connect with server (default: `<not secure>`).

        The default *KEY* used by the server and client is not secure and only a place holder.
        It is expected that the user choose a secure *KEY*. The `cluster` automatically generates
        a secure one-time *KEY*.

    ``.queuesize``
        Maximum number of task bundles on the shared queue (default: `1`).

        This blocks the next bundle from being published by the scheduler until a client
        has taken the current prepared bundle. On smaller scales this is probably best and
        is only of modest performance impact, limiting the scheduler from getting so far ahead
        of the currently running tasks.

        On large scale workflows with many clients (e.g., 100) it may be advantageous to allow
        the scheduler to work ahead in selecting new tasks.

    ``.bundlesize``
        Size of task bundle (default: `1`).

        The default value allows for greater concurrency and responsiveness on small scales. This is
        used by the `submit` thread to accumulate bundles for either database commits and/or publishing
        to the queue. If a database is in use, the scheduler thread selects tasks from the database in
        batches of this size.

        Using larger bundles is a good idea for large distributed workflows; specifically, it is best
        to coordinate bundle size with the number of executors in use by each client.

        See also ``-b``/``--bundlesize`` command-line option.

    ``.attempts``
        Attempts for auto-retry on failed tasks (default: `1`).

        If a database is in use, then there is an opportunity to automatically retry failed tasks. A
        task is considered to have failed if it has a non-zero exit status. The original is not over-written,
        a new task is submitted and later scheduled.

        Counterpart to the ``-r``/``--max-retries`` command-line option. Setting ``--max-retries 1``
        is equivalent to setting ``.attempts`` to 2.

        See also ``.eager``.

    ``.eager``
        Schedule failed tasks before new tasks (default: `false`).

        If ``.attempts`` is greater than one, this option defines the appetite for re-submitting
        failed tasks. By default, failed tasks will only be scheduled when there are no more
        remaining novel tasks.

    ``.wait``
        Polling interval in seconds for database queries during scheduling (default: `5`).
        This waiting only occurs when no tasks are returned by the query.

    ``.evict``
        Eviction period in seconds for clients (default: `600`).

        If a client fails to register a heartbeat after this period of time it is considered
        defunct and is evicted. When there are no more tasks to schedule the server sends a
        disconnect request to all registered clients, and waits until a confirmation is
        returned for each. If a client is defunct, this will hang the shutdown process.


``[client]``
    Section for `client` workflow parameters.

    ``.bundlesize``
        Size of task bundle (default: `1`).

        The default value allows for greater concurrency and responsiveness on small scales.

        Using larger bundles is a good idea for larger distributed workflows; specifically, it is best
        to coordinate bundle size with the number of executors in use by each client. It is also a good
        idea to coordinate bundle size between the client and server so that the client returns the
        same sized bundles that it receives.

        See also ``-b``/``--bundlesize`` command-line option.

    ``.bundlewait``
        Seconds to wait before flushing task bundle (default: `5`).

        If this period of time expires since the previous bundle was returned to the server,
        the current group of finished tasks will be pushed regardless of `bundlesize`.

        For larger distributed workflows it is a good idea to make this waiting period sufficiently
        long so that most bundles are returned whole.

        See also ``-w``/``--bundlewait`` command-line option.

    ``.heartrate``
        Interval in seconds between heartbeats sent to server (default `10`).

        Even on the largest scales the default interval should be fine.

    ``.timeout``
        Timeout in seconds for client. Automatically shutdown if no tasks received (default: never).

        This feature allows for gracefully scaling down a cluster when task throughput subsides.

``[submit]``
    Section for `submit` workflow parameters.

    ``.bundlesize``
        Size of task bundle (default: `1`).

        The default value allows for greater concurrency and responsiveness on small scales.
        Using larger bundles is a good idea for large distributed workflows; specifically, it is best
        to coordinate bundle size with the number of executors in use by each client.

        See also ``-b``/``--bundlesize`` command-line option.

    ``.bundlewait``
        Seconds to wait before flushing tasks (default: `5`).

        If this period of time expires since the previous bundle was pushed to the database,
        the current bundle will be pushed regardless of how many tasks have been accumulated.

        See also ``-w``/``--bundlewait`` command-line option.


``[task]``
    Section for task runtime settings.

    ``.cwd``
        Explicitly set the working directory for all tasks.

    ``.timeout``
        Task-level walltime limit (default: none).

        Executors will send a progression of SIGINT, SIGTERM, and SIGKILL.
        If the process still persists the executor itself will shutdown.

    ``.signalwait``
        Wait period in seconds between signal escalation on task cancellation (default: 10).

        See also ``-S``, ``--signalwait`` command-line option.

``[ssh]``
    SSH configuration section.

    ``.args``
        SSH connection arguments; e.g., ``-i ~/.ssh/some.key``.
        It is preferable to configure SSH directly however, in ``~/.ssh/config``.

    ``[nodelist]``
        This can be a single list of hostnames or a section when multiple named lists.
        Reference named groups from the command-line with ``--ssh-group``.

        Such as,

        ``.mycluster = ['mycluster-01', 'mycluster-02', 'mycluster-03']``

``[autoscale]``
    Define an autoscaling policy and parameters.

    ``.policy``
        Either `fixed` or `dynamic`.

        A `fixed` policy will seek to maintain a definite size and allows for recovery in the
        event that clients halt for some reason (e.g., due to expected faults or timeouts).

        A `dynamic` policy maintains a minimum size and grows up to some maximum size
        depending on the observed *task pressure* given the specified scaling factor.

        See also ``.factor``, ``.period``, ``.size.init``, ``.size.min``, and ``.size.max``.

    ``.factor``
        Scaling factor (default: 1).

        A dimensionless quantity used by the `dynamic` policy.
        This value expresses some multiple of the average task duration in seconds.

        The autoscaler periodically checks ``toc / (factor x avg_duration)``, where
        ``toc`` is the estimated time of completion for all remaining tasks given current
        throughput of active clients. This ratio is referred to as *task pressure*, and if
        it exceeds 1, the pressure is considered *high* and we will add another client if
        we are not already at the maximum size of the cluster.

        For example, if the average task length is 30 minutes, and we set ``factor = 2``, then if
        the estimated time of completion of remaining tasks given currently connected executors
        exceeds 1 hour, we will scale up by one unit.

        See also ``.period``.

    ``.period``
        Scaling period in seconds (default: 60).

        The autoscaler waits for this period of time in between checks and scaling events.
        A shorter period makes the scaling behavior more responsive but can effect database
        performance if checks happen too rapidly.

    ``[size]``
        ``.init``
            Initial size of cluster (default: 1).

            When the the cluster starts, this number of clients will be launched.
            For a *fixed* policy cluster, this should be given with a ``.min`` size, and likely
            the same value.

        ``.min``
            Minimum size of cluster (default: 0).

            Regardless of autoscaling policy, if the number of launched clients drops below this
            value we will scale up by one. Allowing ``min = 0`` is an important feature for
            efficient use of computing resources in the absence of tasks.

        ``.max``
            Maximum size of cluster (default: 2).

            For a *dynamic* autoscaling policy, this sets an upper limit on the number of launched
            clients. When this number is reached, scaling stops regardless of task pressure.

``[console]``
    Rich text display and output parameters.

    ``.theme``
        Color scheme to use by default in output (such as with ``task info`` and ``task search``).

        This option is passed to the `rich <https://rich.readthedocs.io/en/latest/>`_ library.

``[export]``
    Any variable defined here is injected as an environment variable for tasks.

    Example,

    ``foo = 1``
        The environment variable ``FOO=1`` would be defined for all tasks.
