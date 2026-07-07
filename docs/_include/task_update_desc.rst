Update tasks in the database.

Include any number of `FIELD=VALUE` or tag `KEY:VALUE` positional arguments.

The ``-w``/``--where`` and ``-t``/``--with-tag`` operate just as in the search command.

Using ``--cancel`` marks tasks as cancelled: `schedule_time` and `completion_time`
are set to now and `exit_status` to -1. A cancelled task is terminal - it will not be
scheduled, retried, or reverted on ``--restart``.

Using ``--revert`` reverts everything as if the task was new again.

Using ``--delete`` drops the row from the database entirely.

The legacy interface for updating a single task with the `ID`, `FIELD`,
and `VALUE` as positional arguments remains valid.
