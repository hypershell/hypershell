Arguments
^^^^^^^^^

FILE
    Path to input task file (default: <stdin>).

Modes
^^^^^

``--ssh`` *HOST*...
    Launch directly with SSH host(s). This can be a single host, a comma-separated list of hosts,
    or an expandable pattern, e.g., "cluster-a[00-04].xyz".

    See also ``--ssh-group`` and ``--ssh-args``.

``--mpi``
    Same as ``--launcher=mpirun``.

``--launcher`` *ARGS*...
    Use specific launch interface. This can be any program that handles process management on a
    distributed system. For example, on a *SLURM* cluster one might want to use ``srun``. In this
    case you would specify ``--launcher=srun``; however, the *ARGS* are not merely the executable
    but the full listing, e.g., ``--launcher='srun --mpi=pmi2'``.

    With ``--autoscaling``, each launch names the zone of the client it starts: ``{zone}`` in
    *ARGS* is replaced by the zone's name (``default`` for the default zone), and the launched
    process has the name in its ``HYPERSHELL_ZONE`` environment variable. The client's own
    ``--template`` is never changed. See ``--autoscaling`` for an example.

Options
^^^^^^^

``-N``, ``--num-threads`` *NUM*
    Number of executor threads per client (default: 1).

    Set to 0 to auto-detect based on available CPU cores on each client.

    For example, ``-N4`` would create four workers, but ``-N4 --ssh 'cluster-a[00-01].xyz'``
    creates two clients and a total of eight workers.

``-t``, ``--template`` *CMD*
    Command-line template pattern (default: "{}").

    This is expanded by the client just before execution. With the default "{}" the input
    command-line will be run verbatim. Specifying a template pattern allows for simple input
    arguments (e.g., file paths) to be transformed into some common form; such as
    ``-t './some_command.py {} >outputs/{/-}.out'``.

    See section on `templates`.

``--from-json`` *SPEC*
    Read tasks from a JSON file (``FILE[@path]``).

    The optional dotted ``path`` after ``@`` locates the list of task objects inside the
    document (e.g. ``plan.json@chunks``); with no ``@`` the file's top level must be the
    list. Each task object's keys become named ``{key}`` fields in the ``--template`` and
    task tags. In this mode the template is expanded when tasks are ingested (submit-side),
    not by the clients.

``-H``, ``--bind`` *ADDR*
    Bind address (default: localhost, or 0.0.0.0 for remote clients).

    Sets the address the server binds to. When launching clients on remote hosts
    (``--ssh``, ``--mpi``, or ``--launcher``, with or without ``--autoscaling``), the default
    *localhost* becomes *0.0.0.0* so they can connect. The clients are told to dial the bind
    address when it is one specific address, and this host's own name when it is *0.0.0.0*,
    unless ``--advertise`` names another.

``--advertise`` *ADDR*
    Address remote clients dial (default: by ``-H``, as above).

    Tells every launched client to dial *ADDR*, whatever the bind address: a name the clients
    can reach the server by that differs from it, such as the name on the server's certificate.
    Also set by ``server.advertise``; this option takes precedence. Not allowed with a local
    cluster, whose clients are not launched.

    Under TLS the advertised address must be one the client's certificate check accepts: with a
    certificate from the system's trusted authorities (no ``cafile`` and no ``servername``),
    clients check it against the address they dial, so advertise a name the certificate carries,
    or set ``server.tls.servername``.

``-p``, ``--port`` *PORT*
    Port number (default: 50001).

    This is an arbitrary choice and simply must be an available port. The default option chosen
    here is typically available on most platforms and is not expected by any known major software.

``-b``, ``--bundlesize`` *SIZE*
    Size of task bundle (default: 1).

    The default value allows for greater concurrency and responsiveness on small scales. This is
    used by the `submit` thread to accumulate bundles for either database commits and/or publishing
    to the queue. If a database is in use, the scheduler thread selects tasks from the database in
    batches of this size.

    Using larger bundles is a good idea for large distributed workflows; specifically, it is best
    to coordinate bundle size with the number of executors in use by each client.

    See also ``--num-threads`` and ``--bundlewait``.

``-w``, ``--bundlewait`` *SEC*
    Time to wait before flushing tasks (default: 5).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

    This is used by both the `submit` thread and forwarded to each `client`. The `client` collector
    thread that accumulates finished task bundles to return to the `server` will push out a bundle
    after this period of time regardless of whether it has reached the preferred bundle size.

    See also ``--bundlesize``.

``-Q``, ``--poll`` *SEC*
    Polling interval between database queries if no tasks are available (default: 5).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

    This controls how frequently the scheduler checks the database for new tasks when idle.
    Lower values (e.g., 1-2 seconds) provide faster responsiveness but may increase database
    query load. Higher values (e.g., 10-30 seconds) reduce query frequency, useful for
    workflows with infrequent task submission.

``-r``, ``--max-retries`` *NUM*
    Auto-retry failed tasks (default: 0).

    If a database is in use, then there is an opportunity to automatically retry failed tasks. A
    task is considered to have failed if it has a non-zero exit status. Setting this value greater
    than zero defines the number of attempts for the task. The original is not over-written, a new
    task is submitted and later scheduled.

    See also ``--eager``.

``--eager``
    Schedule failed tasks before new tasks. If ``--max-retries`` is greater than one, this option
    defines the appetite for re-submitting failed tasks. By default, failed tasks will only be
    scheduled when there are no more remaining novel tasks.

``--no-db``
    Disable database (submit directly to clients).

    By default, a scheduler thread selects tasks from a database that were previously submitted.
    With ``--no-db`` enabled, there is no scheduler and instead the `submit` thread publishes
    bundles directly to the queue.

``--zone`` *NAME*
    Zone of the submitted tasks and of every client the cluster launches; the own zone with
    ``--autoscaling`` (default: none).

    The tasks read from *FILE* (or ``--from-json``) are placed in this zone, and each client is
    launched with the same ``--zone``, so it runs them. Zones are recorded in the database, so
    ``--zone`` cannot be used with ``--no-db``.

    With ``--autoscaling``, this is the cluster's own zone: the one that ``--init-size`` and
    ``--min-size`` apply to. Any other zone with tasks in the active *task group* gets clients
    of its own, each launched with ``--zone`` set to that zone.

``--initdb``
    Auto-initialize database.

    If a database is configured for use with the workflow (e.g., PostgreSQL), auto-initialize
    tables if they don't already exist. This is a short-hand for pre-creating tables with the
    ``hs initdb`` command. This happens by default with SQLite databases.

    Mutually exclusive to ``--no-db``. See ``hs initdb`` command.

``--no-confirm``
    Disable client confirmation of task bundle received.

    To achieve even higher throughput at large scales, optionally disable confirmation
    payloads from clients. Consider using this option when also using ``--no-db``.

``--forever``
    Schedule forever.

    Typically, the `cluster` will process some finite set of submitted tasks. When there are
    no more tasks left to schedule, the `cluster` will begin its shutdown procedure. With
    ``--forever`` enabled, the scheduler will continue to wait for new tasks indefinitely.

    Conflicts with ``--no-db`` and mutually exclusive to ``--restart``.

``--restart``
    Start scheduling from where a prior run left off.

    With no *FILE*, ``--restart`` resumes purely from the database: any task previously
    scheduled but not completed is reverted, scheduling continues, and the `cluster` halts when
    nothing is left to do. This is the classic "populate with ``submit``, then requeue" strategy
    for very large workflows — if the `cluster` is interrupted it can gracefully continue where
    it left off.

    When a *FILE* is given, ``--restart`` becomes file-aware and idempotent. The file is read
    upfront (a content fingerprint and task count), and only tasks whose identity is not already
    present from a prior submission of that same path are submitted; the revert-interrupted flow
    re-runs anything that was mid-flight. Requeuing the same ``hsx <FILE> --restart`` job as many
    times as needed therefore never double-submits work. If the file's content has changed since
    it was last seen, the run is refused with a suggestion to use ``--update --restart``.

    Conflicts with ``--no-db`` and ``--repeat``, and mutually exclusive to ``--forever``.

``--repeat``
    Re-submit a known file's tasks again as a new source.

    Re-submitting a file that has already been ingested is normally refused. ``--repeat``
    overrides that: the file is recorded as a brand-new source and *all* of its tasks are
    submitted again, even if an identical prior submission exists. Combine with ``-t``/tags to
    represent a new phase or trial of the same work.

``--update``
    Submit only the tasks that are new since a file was last seen.

    Used together with ``--restart`` (``hsx <FILE> --update --restart``): the file is recorded as
    a new source and only tasks whose identity is not already present in the prior same-path
    lineage are submitted — novel tasks run while previously-submitted tasks are skipped. On its
    own (without ``--restart``) it is ambiguous and refused; it also cannot be combined with
    ``--repeat``.

``--ssh-args`` *ARGS*...
    Command-line arguments for SSH. For example, ``--ssh-args '-i ~/.ssh/my_key'``.

``--ssh-group`` *NAME*
    SSH nodelist group in config.

    In your configuration under ``[ssh.nodelist]`` can be one or more named lists. These lists
    should contain host names to associate with the group name.

    See `configuration` section.

``-E``, ``--env``
    Send environment variables. Only for ``--ssh`` mode, all ``HYPERSHELL_`` prefixed environment
    variables can be exported to the remote clients, except ``HYPERSHELL_SERVER_AUTH``, which is
    never sent this way.

``--remote-exe`` *PATH*
    Path to remote executable on the client side.

    By default the executable path used to launch clients is the same as that for the cluster.
    This option is necessary for clients with a different install path on client hosts.

``-d``, ``--delay-start`` *SEC*
    Delay time for launching clients (default: 0).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``10m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). Fractions of a second
    are kept (``--delay-start=1.5s`` waits one and a half seconds).

    At larger scales it can be advantageous to uniformly delay the client launch sequence.
    Hundreds or thousands of clients connecting to the server all at once is a challenge.
    Even if the server could handle the load, your task throughput would be unbalanced,
    coming in waves.

    Write the value with a leading ``-`` to impose a uniform random delay up to the magnitude
    specified (e.g., ``--delay-start=-10m`` or ``--delay-start=-600`` would delay the client up to
    ten minutes). This also has the effect of staggering the workload. If your tasks take on the
    order of 30 minutes and you have 1000 nodes, choose ``--delay-start=-30m``. Attach a value
    with a leading ``-`` to the option with ``=`` as shown; given as a separate word, only a plain
    number (e.g., ``-d -600``) is read as a value on every version of Python.

``--capture``
    Capture individual task <stdout> and <stderr>.

    By default, the `stdout` and `stderr` streams of all tasks are fused with that of the `client`
    thread, and in turn the `cluster`. If tasks are producing output that needs to be isolated, the
    tasks need to manage their own output, you can specify a redirect as part of a ``--template``,
    or use ``--capture`` to capture these as ``.out`` and ``.err`` files.

    These are stored local to the `client` under `<prefix>/lib/task/<uuid>.[out,err]`.
    Task outputs can be automatically retrieved via SFTP, see `info`.

``-o``, ``--output`` *PATH*
    File path for task outputs (default: <stdout>).

    If local only (not ``--ssh``, ``--mpi`` or ``--launcher``), then the *client* can redirect all
    *stdout* from tasks to some file *PATH* together.

``-e``, ``--errors`` *PATH*
    File path for task errors (default: <stderr>).

    If local only (not ``--ssh``, ``--mpi`` or ``--launcher``), then the *client* can redirect all
    *stderr* from tasks to some file *PATH* together.

``-f``, ``--failures`` *PATH*
    File path to write failed task args (default: <none>).

    The *server* acts like a sieve, reading task args from *stdin* and redirecting those original
    args to *stdout* if the task had a non-zero exit status. The *cluster* will run the *server*
    for you and if ``--failures`` is enabled these task args will be sent to a local file *PATH*.

``-T``, ``--timeout`` *SEC*
    Timeout in seconds for clients. Automatically shutdown if no tasks received (default: never).

    This feature allows for gracefully scaling down a cluster when task throughput subsides.

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``90m``, ``1h30m``, ``1.5h``, ``1d12h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30:00``).

``-W``, ``--task-timeout`` *SEC*
    Task-level walltime limit (default: none).

    Executors will send a progression of SIGINT, SIGTERM, and SIGKILL.
    If the process still persists the executor itself will shutdown.

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``90m``, ``1h30m``, ``1.5h``, ``1d12h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30:00``).
    Individual tasks can override this with inline comments, which take the same forms (e.g.,
    ``#HYPERSHELL: timeout:1:30:00``).

``-c``, ``--cores`` *NUM*
    Number of CPU cores required per task (default: none).

    When specified, tasks will only be executed when the required number of cores
    is available. Used with resource-aware scheduling and backfill.

``-m``, ``--memory`` *MEM*
    Amount of memory required per task (default: none).

    Specify memory size in bytes or with a unit ``K``, ``M``, ``G`` or ``T``, alone or followed
    by ``B`` or ``iB``, in any case (e.g., '4GB', '512M'). Each unit is a power of 1024, so
    ``16G``, ``16GB`` and ``16GiB`` are the same size. Individual tasks can override this with
    inline comments, which take the same forms (e.g., ``#HYPERSHELL: memory:8G``). Tasks will
    only be executed when the required memory is available. Used with resource-aware scheduling
    and backfill.

``-C``, ``--client-cores`` *NUM*
    Limit available cores per client (default: all cores).

    Sets an upper bound on the number of CPU cores that each client can use.
    This allows running multiple clients on the same node by partitioning resources.
    Clients will not execute tasks if doing so would exceed this limit.

    See also ``--num-threads=0``.

``-M``, ``--client-memory`` *MEM*
    Limit available memory per client (default: all memory).

    Sets an upper bound on the amount of memory that each client can use.
    Specify memory size in bytes or with a unit ``K``, ``M``, ``G`` or ``T``, alone or followed
    by ``B`` or ``iB``, in any case (e.g., '16G', '4096MB'). Each unit is a power of 1024, so
    ``16G``, ``16GB`` and ``16GiB`` are the same size. This allows running multiple clients on
    the same node by partitioning resources.

``--monitor``
    Enable resource monitoring for tasks.

    When enabled, clients will monitor CPU and memory usage of running tasks
    and their child processes, reporting peak usage back to the server.
    Time-series data is stored in CSV format alongside task outputs.

    See also ``--capture``.

``-R``, ``--ratelimit`` *NUM*
    Maximum allowed tasks per second per client. (default: none).
    Individual tasks are throttled if exceeding this average limit.

``-S``, ``--signalwait`` *SEC*
    Task-level signal escalation wait period (default: 10).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

    When tasks fail to halt following an initial SIGINT, the program waits
    this interval in seconds before escalating to the next level of interrupt.

    See also ``--task-timeout``.

``-A``, ``--autoscaling`` [*MODE*]
    Enable autoscaling per zone (default: disabled). Used with ``--launcher``.

    Specifying this option on its own triggers the use of the autoscaler, with the default
    *policy* or the configured policy. The *policy* can be specified directly here
    as either *fixed* or *dynamic* (e.g., ``--autoscaling=dynamic``). The default is *fixed*.

    The specified ``--launcher`` is used to bring up each individual instance of the client
    as a discrete scaling unit. This is different than using ``--launcher`` on its own where
    it specifies a single invocation that should launch all clients (e.g., like an ``mpirun``).
    Without this option, clients will simply be run locally.

    A *fixed* policy will seek to maintain a definite size and allows for recovery in the
    event that clients halt for some reason (e.g., due to expected faults or timeouts).
    A launch that keeps failing is not repeated forever, under either policy: once
    ``autoscale.failed_launches`` launched clients in a row (default: 3) exit non-zero while no
    client is registered, the cluster stops and exits non-zero, naming them.

    A *dynamic* policy maintains a ``--min-size`` (default: 0) and grows up to some
    ``--max-size`` depending on the observed *task pressure* given the specified scaling
    ``--factor``.

    Clients are scaled per zone. Each zone with tasks in the active *task group* has a fleet of
    its own, sized by the same policy and the same values of ``--factor`` and ``--max-size``,
    with its own task pressure. ``--init-size`` and ``--min-size`` apply only to the cluster's
    own zone (``--zone``, else the default zone); every other zone gets clients only while it
    has work in the active group, so a zone whose tasks wait in a later group gets none yet.
    Each ``--period``, at most one client is added per zone, so several zones can each add one
    at once. Clients of a zone whose work is done leave by ``--timeout`` as usual.

    Each launch names its zone to the launcher, as ``{zone}`` in the ``--launcher`` arguments
    and as ``HYPERSHELL_ZONE`` in its environment, so one launcher can send the clients of each
    zone to the resource that serves it. When partitions are named after zones, the launcher
    alone can do it (``--launcher='srun -p {zone} -n1'``). Otherwise a small script given as
    ``--launcher='sh launch.sh'`` can choose, for example::

        #!/bin/sh
        # Start one client on the partition that serves its zone.
        case "$HYPERSHELL_ZONE" in
            gpu)   partition=gpu ;;
            himem) partition=himem ;;
            *)     partition=cpu ;;
        esac
        exec srun -p "$partition" -n1 "$@"

    See also ``--factor``, ``--period``, ``--init-size``, ``--min-size``, and ``--max-size``.

``-F``, ``--factor`` *VALUE*
    Scaling factor (default: 1).

    A configurable, dimensionless quantity used by the ``--autoscaling=dynamic`` policy.
    This value expresses some multiple of the average task duration in seconds.

    The autoscaler periodically checks ``toc / (factor x avg_duration)``, where
    ``toc`` is the estimated time of completion for remaining tasks in the active
    *task group* given current throughput of active clients. This ratio is referred to
    as *task pressure*, and if it exceeds 1, the pressure is considered *high* and we
    will add another client if we are not already at the given ``--max-size`` of the
    cluster.

    Task pressure is relative to the active task group; tasks waiting in later groups
    do not contribute. The scheduler only distributes tasks within the active group,
    so scaling out for a future backlog would only create idle clients.

    For example, if the average task length is 30 minutes, and we set ``--factor=2``, then if
    the estimated time of completion of remaining tasks given currently connected executors
    exceeds 1 hour, we will scale up by one unit.

    See also ``--period``.  Only valid with ``--autoscaling``.

``-P``, ``--period`` *SEC*
    Scaling period (default: 60).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

    The autoscaler waits for this period of time in between checks and scaling events.
    A shorter period makes the scaling behavior more responsive but can effect database
    performance if checks happen too rapidly.

    Only valid with ``--autoscaling``.

``-I``, ``--init-size`` *SIZE*
    Initial size of cluster (default: 1).

    When the cluster starts, this number of clients will be launched.
    For a *fixed* policy cluster, this should be given with a ``--min-size``, and likely
    the same value.

    Only valid with ``--autoscaling``.

``-X``, ``--min-size`` *SIZE*
    Minimum size of cluster (default: 0).

    Regardless of autoscaling policy, if the number of launched clients drops below this
    value we will scale up by one. Allowing ``--min-size=0`` is an important feature for
    efficient use of computing resources in the absence of tasks.

    Only valid with ``--autoscaling``.

``-Y``, ``--max-size`` *SIZE*
    Maximum size of cluster (default: 1).

    For a *dynamic* autoscaling policy, this sets an upper limit on the number of launched
    clients. When this number is reached, scaling stops regardless of task pressure.

    Only valid with ``--autoscaling``.

.. include:: /_include/tls_cli_options.rst
