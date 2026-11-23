Update tasks in the database.

Include any number of `FIELD=VALUE` or tag `KEY:VALUE` positional arguments.

The ``-w``/``--where`` and ``-t``/``--with-tag`` operate just as in the search command.
Each takes the words that follow it up to the next option, so the changes go before them
(e.g., ``hs update group=7 color:red -t color:blue``).

Every change given is written together, onto each task that matches the filter at the time
it is written: a task another process changed so that it no longer matches is left as it is.
The `Updated N tasks` (or `Deleted N tasks`) line counts the tasks actually written; when
that is fewer than the count taken at the prompt, a warning says how many tasks
no longer matched and were left as they are, and the exit status is still 0.

If another writer holds a task through every retry, the update writes nothing at all, logs
an error naming the task, and exits with status 5, so running the same command again is safe.
With ``--limit``, each task written stays locked until the whole update finishes; on SQLite the
database is locked for writing that long, and another writer that waits past its busy timeout
fails. On PostgreSQL, an update without ``--limit`` holds the ids of the tasks it wrote in
memory (about 100 bytes each) to find any it missed.

Using ``--cancel`` marks tasks as cancelled: `schedule_time` and `completion_time`
are set to now and `exit_status` to -1. A cancelled task is terminal - it will not be
scheduled, retried, or reverted on ``--restart``.

Using ``--revert`` reverts everything as if the task was new again.

Using ``--delete`` drops the row from the database entirely.
``--delete`` takes no change: given a ``FIELD=VALUE``, a ``KEY:VALUE`` or ``--remove-tag``,
``hs update`` exits with status 2 before it counts or asks, and deletes nothing.

The legacy interface for updating a single task with the `ID`, `FIELD`,
and `VALUE` as positional arguments remains valid.
The single-task form takes no ``--cancel``, ``--revert``, ``--delete`` or ``--remove-tag``;
given one, it exits with status 2 and writes nothing.
