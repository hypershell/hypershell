``[logging]``
    Logging configuration. See also :ref:`logging <logging>` section.

    ``.level``
        One of ``TRACE``, ``DEBUG``, ``INFO``, ``WARNING``,
        ``ERROR``, or ``CRITICAL`` (default: ``INFO``)

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
        Options are `default`, `detailed`, `detailed-compact`, `system`, and `short`.

    ``.color``
        Enable ANSI color and formatting in console output (default: ``true``).

        Colors are emitted only when `stderr` is a TTY and are always stripped from
        file-based logs. See also the ``NO_COLOR`` and ``FORCE_COLOR`` environment variables.

    ``[file]``
        File-based logging (disabled unless at least one parameter is set).
        See the :ref:`logging <logging>` section for full details on rotation and compression.

        As a shorthand, ``logging.file`` may be set directly to ``"enabled"`` (or ``true``) to
        log to the default per-process file, or to a path to log there; both forms disable
        rotation and compression.

        ``.path``
            Destination file path. Defaults to a per-process, host-scoped file in the site
            ``log`` directory (e.g. ``server-<host>.log``, ``client-<host>.log``, or
            ``main.log``). Parent directories are created as needed.

        ``.level``
            Minimum severity written to the file (default: ``TRACE``).
            Accepts the same names as ``logging.level``, independent of the console level.

        ``.style``
            Format preset for the file (default: ``system``). Accepts the same options as
            ``logging.style``; a ``format`` may be given instead. ANSI color is always stripped.

        ``.rotate``
            Rotation policy (default: ``never``). Either a size threshold such as ``512MB``
            or ``2G`` (units ``K``/``M``/``G``/``T``, alone or followed by ``B`` or ``iB``,
            powers of 1024; a bare number is bytes) or a cron expression such as ``@daily``,
            ``@midnight``, or ``0 1 * * 0`` (requires the ``croniter`` package). A cron
            expression may begin with a digit. Sending ``SIGHUP`` rotates on demand.

        ``.compress``
            Compression for rotated files (default: none). One of ``gzip``, ``bzip``,
            ``lzma``, or ``zstd`` (the last requires the optional ``zstandard`` package).
            Extensions ``.gz``, ``.bz2``, ``.xz``, and ``.zstd`` respectively.

        ``.keep``
            Number of *uncompressed* rotated files to retain (default: ``0``). Only applies
            when compression is enabled; without compression, rotated files are never deleted.


``[database]``
    Database configuration and connection details.
    See also :ref:`database <database>` section.

    ``.url``
        Full database connection URL (default: none).

        When provided, this takes precedence over and short-circuits the individual
        ``.provider``/``.host``/``.port``/``.user``/``.password`` fields below.

    ``.provider``
        Database provider (default: 'sqlite'). Supported alternatives include
        'postgres' (or compatible) and 'timescale' (alias 'timescaledb'), which is PostgreSQL
        with the TimescaleDB extension: ``hs initdb`` makes the task table a hypertable
        (requires the ``uuid7`` extra; see the TimescaleDB section of :ref:`database <database>`).
        Support for other providers may be considered in the future.

        .. only:: html

            .. versionchanged:: 2.9.0
               :pr:`71`: the ``timescale`` provider.

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

    ``[connect_args]``
        Specify additional connection details for the underlying SQL dialect provider,
        e.g., ``sqlite3`` or ``psycopg``.

    ``[pragmas]``
        Specify one or more pragmas to apply to database connections (SQLite only).
        For example ``pragmas = { journal_mode = "wal" }`` to enable WAL-mode.

    ``[timescale]``
        Settings for the TimescaleDB shape ``hs initdb`` gives the task table (``timescale``
        provider only; never forwarded to the connection). Keys: ``chunk_interval``
        (default ``"1 week"``), ``columnstore`` (``true``), ``segmentby`` (``"client_id"``),
        ``orderby`` (``"id DESC"``), ``policy`` (``true``), and ``compress_after``
        (``"4 weeks"``). For example ``timescale = { chunk_interval = "1 day" }``.
        See the TimescaleDB section of :ref:`database <database>`.

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`71`

    ``*``
        Any additional arguments are forwarded to the provider, e.g., ``encoding = 'utf-8'``.


``[server]``
    Section for `server` workflow parameters.

    ``.bind``
        Bind address (default: `localhost`).

        When running locally, the default is recommended. To allow remote *clients* to connect
        over the network, bind the server to *0.0.0.0*.

    ``.host``
        Host address that clients connect to (default: `localhost`).

        Counterpart to ``.bind``: where ``.bind`` is the local address the server listens on,
        ``.host`` is the address that `client` instances and queue-direct ``submit --queue``
        dial to reach the server. Set this to the server's reachable hostname or address
        when running clients on other machines.

    ``.port``
        Port number (default: `50001`).

        This is an arbitrary choice and simply must be an available port. The default option chosen
        here is typically available on most platforms and is not expected by any known major software.

    ``.auth``
        Cryptographic authentication key to connect with server (default: `<not-secure>`).

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

        It is also the size of the bundle given to a client that advertises no size of its own.

        See also ``-b``/``--bundlesize`` command-line option.

        .. only:: html

            .. versionchanged:: 2.9.0
               :pr:`78`: the size for a client that advertises none.

    ``.bundlemax``
        Most tasks the server hands a client in one reply (default: `1000`).

        A client advertises its own bundle size (its ``-b``); the server never replies with more
        than this many tasks, whatever a client asks for. The effective cap is never smaller than
        the server's own ``.bundlesize``. There is no command-line option.

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`78`

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

    ``.poll``
        Maximum polling interval in seconds between database queries when no tasks are
        available (default: `30`).

        The scheduler backs off exponentially from a small floor, doubling the wait after
        each empty query up to this ceiling. This waiting only occurs when no tasks are
        returned by the query.
        A string takes the same forms as ``-Q``/``--poll``: units ``d``, ``h``, ``m`` and ``s``
        (e.g., ``"30s"``, ``"5m"``, ``"1h30m"``) or a Slurm time (e.g., ``"1:30"``); a number is
        seconds.

        See also ``-Q``/``--poll`` command-line option.

        .. only:: html

            .. versionchanged:: 2.9.0
               A string with units or a Slurm time.

    ``.evict``
        Eviction period in seconds for clients (default: `600`).

        If the server receives no heartbeat from a client for this period of time, by its own
        clock, the client is considered defunct and is evicted. The time a client stamps on its
        heartbeat plays no part, so a client whose clock differs from the server's is not evicted
        while it keeps sending them. When there are no more tasks to schedule the server sends a
        disconnect request to all registered clients, and waits until a confirmation is
        returned for each. If a client is defunct, this will hang the shutdown process.

        .. only:: html

            .. versionchanged:: 2.9.0
               A heartbeat is aged from when the server received it, not by the client's clock.

    ``[tls]``
        Transport-layer security for queue connections. Enabled by default; every key below
        is also exposed as an environment variable (e.g., ``HYPERSHELL_SERVER_TLS_ENABLED``)
        with the usual precedence. See the :ref:`built-in TLS <builtin-tls>` section for the
        full model, including peer verification modes and limitations.

        ``.enabled``
            Use TLS on the queue interface (default: `true`).

            Disabling (``enabled = false`` or the ``--no-tls`` option) sends task bundles and
            results in the clear and is not recommended outside a trusted, isolated network.

        ``.cert``
            Path to the server certificate, or ``<auto>`` (default: `<auto>`).

            With ``<auto>`` a self-signed certificate is generated once on first server start
            and stored under the site ``lib`` directory (``<site>/lib/tls``).

        ``.key``
            Path to the server private key, or ``<auto>`` (default: `<auto>`).

            Paired with ``.cert``. The auto-generated key is written owner-only (``0600``);
            a user-provided key file should be protected the same way.

        ``.cafile``
            Trust anchor used by the client to verify the server (default: `<auto>`).

            With ``<auto>`` the client trusts the server's own certificate directly, which
            works out of the box only on a single host or a shared filesystem. Provide a PEM
            bundle *PATH* (together with ``.servername``) to verify peers across hosts.

        ``.fingerprint``
            Pinned peer certificate fingerprint, e.g. ``SHA256:AB:CD:...`` (default: none).

            When set, the client completes the handshake without CA validation and rejects the
            connection unless the certificate fingerprint matches. Takes precedence over
            ``.cafile``. Convenient for self-signed certificates and small clusters.

        ``.insecure``
            Disable peer verification entirely (default: `false`).

            The transport is still encrypted, but the peer identity is not authenticated and a
            warning is logged on every connection. Suitable only for transient local debugging
            on a trusted host; never use it on an untrusted network.

        ``.min_version``
            Minimum TLS protocol version (default: `TLSv1.2`). Either ``TLSv1.2`` or ``TLSv1.3``.

        ``.ciphers``
            OpenSSL cipher string to restrict the negotiated ciphers (default: none).

        ``.servername``
            Expected server name for identity verification / SNI override (default: none).

            Required alongside ``.cafile`` for true server-identity verification: with a trust
            anchor set but no ``.servername``, the client checks only that the certificate
            chains to the anchor, not which host presented it.

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
        A string takes the same forms as ``-T``/``--timeout``: units ``d``, ``h``, ``m`` and
        ``s`` (e.g., ``"1h30m"``, ``"1.5h"``) or a Slurm time (e.g., ``"1:30:00"``, ``"1-12"``).

    ``.cores``
        Client-level limit on CPU cores available for running tasks (default: all available).

        A value of ``0`` means unconstrained (use all detected cores). Set a lower value to
        partition a node between multiple clients.

        See also ``-C``/``--client-cores`` command-line option.

    ``.memory``
        Client-level limit on memory (in bytes) available for running tasks (default: all available).

        A value of ``0`` means unconstrained. Set a lower value to partition a node's memory
        between multiple clients. A string may carry a unit ``K``, ``M``, ``G`` or ``T``, alone
        or followed by ``B`` or ``iB``, each a power of 1024 (e.g., ``"16G"``).

        See also ``-M``/``--client-memory`` command-line option.

    ``.ratelimit``
        Maximum number of tasks started per second (default: no limit).

        A value of ``0`` disables rate limiting. Useful to throttle very short tasks so that
        task launch does not overwhelm a shared resource.

        See also ``-R``/``--ratelimit`` command-line option.

``[submit]``
    Section for `submit` workflow parameters.

    ``.bundlesize``
        Size of task bundle (default: `1`).

        The default value allows for greater concurrency and responsiveness on small scales.
        Using larger bundles is a good idea for large distributed workflows; specifically, it is best
        to coordinate bundle size with the number of executors in use by each client.

        The `client` reads its ``-b``/``--bundlesize`` default from this key. When it is set (here
        or by ``HYPERSHELL_SUBMIT_BUNDLESIZE``), a client advertises it as the size of bundle it
        requests; when it is left at the built-in default, a client advertises no size and the
        server uses its own.

        See also ``-b``/``--bundlesize`` command-line option.

        .. only:: html

            .. versionchanged:: 2.9.0
               :pr:`78`: a client advertises it as the size it requests.

    ``.bundlewait``
        Seconds to wait before flushing tasks (default: `5`).

        If this period of time expires since the previous bundle was pushed to the database,
        the current bundle will be pushed regardless of how many tasks have been accumulated.
        A string takes the same forms as ``-w``/``--bundlewait``: units ``d``, ``h``, ``m`` and
        ``s`` (e.g., ``"30s"``, ``"5m"``, ``"1h30m"``) or a Slurm time (e.g., ``"1:30"``); a number
        is seconds.

        See also ``-w``/``--bundlewait`` command-line option.

        .. only:: html

            .. versionchanged:: 2.9.0
               A string with units or a Slurm time.


``[task]``
    Section for task runtime settings.

    ``.cwd``
        Explicitly set the working directory for all tasks.

    ``.cores``
        Default cores required per task (default: unconstrained).

        A value of ``0`` means unconstrained. Used with resource-aware scheduling and backfill;
        individual tasks may override this inline (e.g., ``# HYPERSHELL: cores:8``).

        See also ``-c``/``--cores`` command-line option.

    ``.memory``
        Default memory (in bytes) required per task (default: unconstrained).

        A value of ``0`` means unconstrained. A string may carry a unit ``K``, ``M``, ``G`` or
        ``T``, alone or followed by ``B`` or ``iB``, each a power of 1024 (e.g., ``"16G"``). Used
        with resource-aware scheduling and backfill; individual tasks may override this inline in
        the same forms (e.g., ``# HYPERSHELL: memory:8G``).

        See also ``-m``/``--memory`` command-line option.

    ``.timeout``
        Task-level walltime limit in seconds (default: none).

        Executors will send a progression of SIGINT, SIGTERM, and SIGKILL.
        If the process still persists the executor itself will shutdown.
        A string takes the same forms as ``-W``: units ``d``, ``h``, ``m`` and ``s`` (e.g.,
        ``"1h30m"``, ``"1.5h"``) or a Slurm time (e.g., ``"1:30:00"``, ``"1-12"``); individual
        tasks may override this inline in the same forms (e.g., ``# HYPERSHELL: timeout:1h30m``).

    ``.signalwait``
        Wait period in seconds between signal escalation on task cancellation (default: 10).

        A string takes the same forms as ``-S``/``--signalwait``: units ``d``, ``h``, ``m`` and
        ``s`` (e.g., ``"30s"``, ``"5m"``, ``"1h30m"``) or a Slurm time (e.g., ``"1:30"``); a number
        is seconds.

        See also ``-S``, ``--signalwait`` command-line option.

        .. only:: html

            .. versionchanged:: 2.9.0
               A string with units or a Slurm time.

``[ssh]``
    SSH configuration section.

    ``.config``
        Path to the SSH client configuration file (default: ``~/.ssh/config``).

        Host entries defined there are honored when connecting to nodes in ``ssh.nodelist``.

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

        A server autoscaling with relay sites (``hs server --autoscaling``) applies the policy to
        each zone's demand alone: under `fixed`, a zone with work gets one client when none runs,
        and its size otherwise comes from the relays' ``min`` and ``init``; under `dynamic`, a zone
        grows by one client per check while its pressure is high, up to the sum of its live sites'
        ``max``.

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
        A string takes the same forms as ``-P``/``--period``: units ``d``, ``h``, ``m`` and ``s``
        (e.g., ``"30s"``, ``"5m"``, ``"1h30m"``) or a Slurm time (e.g., ``"1:30"``); a number is
        seconds. ``hs relay`` reads it in the same forms as the period of its site's clients, and
        stops before it starts if the value is not a duration.

        .. only:: html

            .. versionchanged:: 2.9.0
               A string with units or a Slurm time.

    ``.launcher``
        Command prefix used to launch each client as a scaling unit (default: none).

        An empty value launches clients directly as ``hs client ...``. Set a launcher such as
        an MPI or SLURM command (e.g., ``srun``) to bring up each client through your resource
        manager. Counterpart to the cluster ``--launcher`` option used with ``--autoscaling``.

        Each launch names the zone of its client: ``{zone}`` in the launcher is replaced by the
        zone's name (``default`` for the default zone), and the launched process has the name
        in its ``HYPERSHELL_ZONE`` environment variable.

        .. only:: html

            .. versionchanged:: 2.9.0
               :pr:`99`: ``{zone}`` and ``HYPERSHELL_ZONE``.

        A server autoscaling with relay sites launches nothing and ignores this setting: each
        relay launches its own site clients with its own launcher.

    ``.distribution``
        How a zone's clients are split over the relay sites that serve it (default: `spread`).

        Only a server autoscaling with relay sites (``hs server --autoscaling``) reads it. With
        `spread`, each new client of a zone goes to the live site serving it that runs the
        fewest, as peers. With `fill`, each new client goes to the first site in preference
        order that has room below its ``max``, so a site is saturated before the next one gets
        any: use it to prefer the cheaper site. Every site stays within the ``min``/``max`` its
        relay advertises for the zone.

        Its environment form is ``HYPERSHELL_AUTOSCALE_DISTRIBUTION``.

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`100`

    ``.sites``
        Preferred site order for the `fill` distribution (default: none).

        A list of site names, or one comma-separated string (the environment form,
        ``HYPERSHELL_AUTOSCALE_SITES=east,west``). Under `fill`, the listed live sites come first in
        the listed order, and live sites not listed follow in the order they first registered.
        Any relay connected with the server's key takes part whether or not it is listed, and
        `spread` ignores the list.

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`100`

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
            Maximum size of cluster (default: 1).

            For a *dynamic* autoscaling policy, this sets an upper limit on the number of launched
            clients. When this number is reached, scaling stops regardless of task pressure.

        A server autoscaling with relay sites ignores ``[size]``: each relay advertises the
        ``min``, ``max`` and ``init`` of each zone it serves, and the server plans within those.

``[relay]``
    Section for the `relay` workflow, which connects a site to an upstream *server*.

    .. only:: html

        .. versionadded:: 2.9.0
           :pr:`82`

    ``.database``
        Path to the relay database (default: <lib>/relay/<host>.db).

        The relay database is always a private SQLite file, whatever ``[database]`` names; a relay
        never opens the configured database. The default is named for the host, since a home
        directory is often shared between hosts. A node-local disk is a good place for it.

        See also ``--database`` command-line option.

    ``.bind``
        Bind address of the relay-local *server* (default: `0.0.0.0`).

        The *clients* at the site connect here. See also ``--bind`` command-line option.

    ``.port``
        Port of the relay-local *server* (default: `50002`).

        See also ``--port`` command-line option.

    ``.bundlesize``
        Size of task bundle for the relay-local *server* and the *clients* at its site
        (default: `1`).

        It is never smaller than ``relay.num_threads``: a *client* asks for one bundle at a time,
        so a smaller bundle would leave its executors idle between requests.

    ``.bundlewait``
        Seconds the relay waits before returning finished tasks upstream regardless of
        `bundlesize` (default: `5`). Once nothing is left to run at the site, it returns what it
        holds at once.

    ``.poll``
        Maximum polling interval of the relay-local scheduler in seconds (default: `0.1`).

        Received tasks wait at most this long before the relay-local *server* hands them out.

    ``.retention``
        Seconds a result is kept in the relay database after it has been returned upstream
        (default: `300`). Results not yet returned are always kept.

    ``.zone``
        Zone or zones the relay serves (default: none, the default zone).

        One zone name, a list of names, or one table per zone, ``[relay.zone.NAME]``, which may
        give that zone's own ``launcher``, ``launcher_args``, ``size``, ``min``, ``max`` and
        ``init`` (each in place of the relay's, for that zone's *clients* only); an empty table
        serves the zone with the relay's.
        Upstream the relay is one *client* per zone, each with its own identity; at the site it
        keeps one fleet of *clients* per zone, all served by the relay-local *server*. The first
        zone's identity lists the site's *clients* upstream.

        .. code-block:: toml

            [relay.zone.cpu]

            [relay.zone.gpu]
            size = 2
            max = 4
            launcher = "srun"
            launcher_args = "--gres=gpu:1"

        See also ``--zone`` command-line option, which names the zones in place of this one.

    ``.site``
        Name of the relay's site (default: none, the relay's host name).

        The upstream *server* records the relay, and the *clients* at its site, under this name.

    ``.size``
        Number of *clients* the relay keeps launched at its site (default: `1`, at least `1`).

        Against an upstream *server* autoscaling with relay sites it is the default ``.max``.
        See also ``-N``, ``--size`` command-line option.

    ``.min``
        Fewest *clients* of each zone an upstream topology assigns this site (default: `0`).

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`100`

        Held even while the zone's tasks wait in a later *task group*. A zone's own
        ``[relay.zone.NAME]`` table may set its own. Without a topology, a zone's fleet keeps at
        least this many *clients* launched (at least `1`, by default the size). Each zone
        advertises its bounds upstream; see :ref:`Topology <cli_relay_topology>`.

    ``.max``
        Most *clients* of each zone an upstream topology assigns this site (default: the size).

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`100`

        Lowering it never ends a running *client*: the zone launches no more, and its *clients*
        exit when idle. A zone's own ``[relay.zone.NAME]`` table may set its own.

    ``.init``
        *Clients* of each zone an upstream topology assigns this site when the relay starts
        (default: ``.min``).

        .. only:: html

            .. versionadded:: 2.9.0
               :pr:`100`

        Without a topology, the *clients* each zone's fleet starts with (by default the size).
        Bounds must satisfy ``min <= init <= max``, with ``max`` at least `1`.

    ``.launcher``
        Command prefix used to launch each *client* at the site (default: none).

        An empty value launches clients directly as ``hs client ...``.
        See also ``--launcher`` command-line option.

    ``.launcher_args``
        Extra arguments for the launcher (default: none).

        See also ``--launcher-args`` command-line option.

    ``.num_threads``
        Executor threads for each *client* at the site (default: `1`).

    ``.template``
        Command template for the *clients* at the site (default: `{}`).

    ``.timeout``
        Seconds a *client* at the site waits for tasks before it exits (default: `0`, never).

    ``[upstream]``
        Connection to the upstream *server*. These settings are never read from ``[server]``,
        so the *clients* at the site keep using the relay's own ``[server.tls]``.

        ``.host``
            Hostname of the upstream *server* (default: `localhost`).

            See also ``--upstream-host`` command-line option.

        ``.port``
            Port of the upstream *server* (default: `50001`).

            See also ``--upstream-port`` command-line option.

        ``.auth``
            Cryptographic key for the upstream *server*.

            Use ``.auth_env`` or ``.auth_eval`` to keep it out of the file; whichever form is set
            in the highest-precedence place is used (two forms in one place are refused). The
            upstream key stays in the relay: the *clients* at the site use a key the relay mints for
            each run.

        ``.bundlesize``
            Bundle size the relay advertises upstream (default: `1000`).

            The relay holds at most twice its connected *clients* times ``relay.bundlesize`` in
            unfinished tasks. It asks for more only once half of that is left, and then for the
            whole difference (never more than this size at once), so the site has a second round
            of work ready while the first runs.

        ``.heartrate``
            Seconds between heartbeats to the upstream *server* (default: `10`).

        ``.deadline``
            Seconds a call to the upstream *server* may take before the relay takes the connection
            as lost (default: `30`).

            While the upstream *server* cannot be reached, the site keeps running and finished
            tasks are kept in the relay database; the relay connects again, with backoff, and then
            returns them before it asks for more work.

        ``.backoff``
            Longest wait in seconds between attempts to reach the upstream *server* again
            (default: `60`).

            Each wait is drawn at random up to a bound that starts at one second and doubles with
            each failed attempt, up to this value.

        ``.evict_hint``
            Seconds of lost contact after which the relay takes the upstream *server* to have
            evicted it (default: `600`, the *server*'s own default ``server.evict``).

            Set it to the upstream ``server.evict``. Past it, the relay warns that the results it
            holds will likely be refused as stale (the upstream *server* has put those tasks back
            for other *clients*), and it does not repeat a confirmation the outage cut off. The
            results are still returned when the connection comes back.

        ``[tls]``
            ``.enabled``
                Use TLS on the upstream connection (default: `true`).

                See also ``--no-tls`` command-line option.

            ``.cert``
                Upstream server certificate (default: `<auto>`).

            ``.cafile``
                Trust anchor for the upstream server (default: `<auto>`).

            ``.fingerprint``
                Pin the upstream server certificate fingerprint, e.g. ``SHA256:AB:CD:...``
                (default: none).

``[console]``
    Rich text display and output parameters.

    ``.theme``
        Color scheme to use by default in output (default: `monokai`), such as with
        ``hs info`` and ``hs list``.

        This option is passed to the `rich <https://rich.readthedocs.io/en/latest/>`_ library.

``[export]``
    Any variable defined here is injected as an environment variable for tasks.

    Example,

    ``foo = 1``
        The environment variable ``FOO=1`` would be defined for all tasks.
