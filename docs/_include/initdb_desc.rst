Initialize database.

For SQLite this happens automatically.
See also ``--initdb`` for the ``hs cluster`` command.

The available special actions are mutually exclusive.
The ``--rotate`` operation migrates completed tasks to the next database partition,
and applies a special purpose ``part:N`` tag to the new partition and remaining tasks.
