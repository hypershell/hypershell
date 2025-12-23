Arguments
^^^^^^^^^

*ARGS*...
    Command-line task arguments (for single task submission).

Options
^^^^^^^

``-f``, ``--task-file`` *FILE*
    Input file containing one task per line.

``-q``, ``--queue``
    Submit directly to live queue instead of database.

    When enabled, tasks are sent directly to a running server's queue for immediate
    scheduling. This bypasses the database entirely, which is useful for transient
    workflows or when a database is not configured.

    Requires ``-H``, ``-p``, and ``-k`` options to specify the server connection.

``-H``, ``--host`` *ADDR*
    Hostname or IP address for server (default: localhost).

    Only used with ``--queue`` mode to specify the server to connect to.

``-p``, ``--port`` *NUM*
    Port number for server (default: 50001).

    Only used with ``--queue`` mode to specify the server port.

``-k``, ``--auth`` *KEY*
    Cryptographic authentication key for server.

    Only used with ``--queue`` mode. The key must match the server's authentication key.

``--template`` *CMD*
    Command-line template pattern (default: "{}").

    This is expanded at submit-time before sending to the database.
    With the default "{}" the input command-line will be run verbatim.
    Specifying a template pattern allows for simple input arguments (e.g., file paths)
    to be transformed into some common form; such as
    ``--template './some_command.py {} >outputs/{/-}.out'``.

    See section on `templates`.

``-b``, ``--bundlesize`` *SIZE*
    Size of task bundle (default: 1).

    The default value allows for greater concurrency and responsiveness on small scales.
    Using larger bundles is a good idea for large distributed workflows; specifically, it is best
    to coordinate bundle size with the number of executors in use by each client.

    See also ``--bundlewait``.

``-w``, ``--bundlewait`` *SEC*
    Seconds to wait before flushing tasks (default: 5).

    If this period of time expires since the previous bundle was pushed to the database,
    The current bundle will be pushed regardless of how many tasks have been accumulated.

    See also ``--bundlesize``.

``-c``, ``--cores`` *NUM*
    Number of CPU cores required per task (default: none).

    Sets the default core requirement for all submitted tasks. Individual tasks can override
    this with inline comments (e.g., ``#HYPERSHELL: cores: 8``).

``-m``, ``--memory`` *SIZE*
    Amount of memory required per task (default: none).

    Sets the default memory requirement for all submitted tasks. Specify memory size with
    units (e.g., '4GB', '512MB'). Individual tasks can override this with inline comments
    (e.g., ``#HYPERSHELL: memory: 8GB``).

``-W``, ``--timeout`` *SEC*
    Task-level walltime limit in seconds (default: none).

    Sets the default timeout for all submitted tasks. Individual tasks can override this
    with inline comments (e.g., ``#HYPERSHELL: timeout: 3600``).

``-g``, ``--group`` *NUM*
    Task group for dependency management (default: 0).

    Assigns submitted tasks to a specific group. Tasks are executed in ascending group order,
    with all tasks in a group completing before the next group begins. This enables simple
    workflow management without requiring explicit DAG-based definitions.

    All tasks default to group 0 for backwards compatibility. Users can organize tasks into
    multiple groups (e.g., 0, 1, 2, ...) where lower-numbered groups execute first.

``--initdb``
    Auto-initialize database.

    If a database is configured for use with the workflow (e.g., PostgreSQL), auto-initialize
    tables if they don't already exist. This is a short-hand for pre-creating tables with the
    ``hs initdb`` command. This happens by default with SQLite databases.

    See ``hs initdb`` command.

``-t``, ``--tag`` *TAG*...
    Assign one or more tags.

    Tags allow for user-defined tracking of information related to individual tasks or large
    groups of tasks. They are defined with both a `key` and `value` (e.g., ``--tag file:a``).
    The default `value` for tags is blank. When searching with tags, not specifying a `value`
    will return any task with that `key` defined regardless of `value` (including blank).
