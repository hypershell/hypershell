Initialize database.

For SQLite this happens automatically.
An existing database from an earlier release is upgraded in place on first use.
See also ``--initdb`` for the ``hs cluster`` command.

The available special actions are mutually exclusive.
The ``--rotate`` operation migrates completed tasks into the next database partition,
recording that partition's index in each moved task's ``part`` column.
