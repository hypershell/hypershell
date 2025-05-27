Arguments
^^^^^^^^^

*ARGS*...
    Command-line task arguments (for single task submission).

Options
^^^^^^^

``-f``, ``--task-file`` *FILE*
    Input file containing one task per line.

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

``--initdb``
    Auto-initialize database.

    If a database is configured for use with the workflow (e.g., Postgres), auto-initialize
    tables if they don't already exist. This is a short-hand for pre-creating tables with the
    ``hs initdb`` command. This happens by default with SQLite databases.

    See ``hs initdb`` command.

``-t``, ``--tag`` *TAG*...
    Assign one or more tags.

    Tags allow for user-defined tracking of information related to individual tasks or large
    groups of tasks. They are defined with both a `key` and `value` (e.g., ``--tag file:a``).
    The default `value` for tags is blank. When searching with tags, not specifying a `value`
    will return any task with that `key` defined regardless of `value` (including blank).
