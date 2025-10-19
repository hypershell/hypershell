Arguments
^^^^^^^^^

FILE
    Path to input task file (default: <stdin>).

Options
^^^^^^^

``-H``, ``--bind`` *ADDR*
    Bind address (default: localhost).

    When running locally, the default is recommended. To allow remote *clients* to connect
    over the network, bind the server to *0.0.0.0*.

``-p``, ``--port`` *NUM*
    Port number (default: 50001).

    This is an arbitrary choice and simply must be an available port. The default option chosen
    here is typically available on most platforms and is not expected by any known major software.

``-k``, ``--auth`` *KEY*
    Cryptographic authentication key to connect with server (default: <not secure>).

    The default *KEY* used by the server and client is not secure and only a place holder.
    It is expected that the user choose a secure *KEY*. The `cluster` automatically generates
    a secure one-time *KEY*.

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
    Seconds to wait before flushing tasks (default: 5).

    This is used by both the `submit` thread and forwarded to each `client`. The `client` collector
    thread that accumulates finished task bundles to return to the `server` will push out a bundle
    after this period of time regardless of whether it has reached the preferred bundle size.

    See also ``--bundlesize``.

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

``-c``, ``--task-cores`` *NUM*
    Number of CPU cores required per task (default: none).

    When an input *FILE* is provided, a `submit` thread is launched to read and submit tasks.
    This option sets the default core requirement for all submitted tasks.
    Individual tasks can override this with inline comments (e.g., ``#HYPERSHELL: cores: 8``).

    See also ``--task-memory`` and ``--task-timeout``.

``-m``, ``--task-memory`` *SIZE*
    Amount of memory required per task (default: none).

    When an input *FILE* is provided, a `submit` thread is launched to read and submit tasks.
    This option sets the default memory requirement for all submitted tasks.
    Specify memory size with units (e.g., '4GB', '512MB'). Individual tasks can override this
    with inline comments (e.g., ``#HYPERSHELL: memory: 8GB``).

    See also ``--task-cores`` and ``--task-timeout``.

``-W``, ``--task-timeout`` *SEC*
    Task-level walltime limit in seconds (default: none).

    When an input *FILE* is provided, a `submit` thread is launched to read and submit tasks.
    This option sets the default timeout for all submitted tasks. Individual
    tasks can override this with inline comments (e.g., ``#HYPERSHELL: timeout: 3600``).

    See also ``--task-cores`` and ``--task-memory``.

``--no-db``
    Disable database (submit directly to clients).

    By default, a scheduler thread selects tasks from a database that were previously submitted.
    With ``--no-db`` enabled, there is no scheduler and instead the `submit` thread publishes
    bundles directly to the queue.

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
    Start scheduling from last completed task.

    Instead of pulling a new list of tasks from some input `FILE`, with ``--restart`` enabled the
    `cluster` will restart scheduling tasks where it left off. Any task in the database that was
    previously scheduled but not completed will be reverted.

    For very large workflows, an effective strategy is to first use the ``submit`` workflow to
    populate the database, and then to use ``--restart`` so that if the `cluster` is interrupted,
    it can easily continue where it left off, halting if nothing to be done.

    Conflicts with ``--no-db`` and mutually exclusive to ``--forever``.

``--print``
    Print failed task args to <stdout>.

    Mutually exclusive to ``-f``/``--failures``.

``-f``, ``--failures`` *PATH*
    File path to write failed task args (default: <none>).

    The *server* acts like a sieve, reading task args from some input source. Tasks with a
    non-zero exit status can have their original command-line *args* printed to an output
    stream. With ``-f``/``--failures``, specify a local file *PATH*.

    Mutually exclusive to ``--print``.
