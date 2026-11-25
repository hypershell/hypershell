Arguments
^^^^^^^^^

*ARGS*...
    Assignment pairs for update.

    Every column is read by its kind, in both forms, within the range the description lists:
    ``cores``, ``exit_status``, ``attempt`` and ``part`` take a whole number; ``memory`` a size, as
    ``hs submit -m`` reads it (``4G``, ``512MB``, ``1.5GiB``, or bytes); ``timeout``, ``waited`` and
    ``duration`` a duration, as ``hs submit -W`` reads it (``90m``, ``1h30m``, ``1:30:00``, ``1-12``,
    or seconds); ``cores_max`` and ``memory_max`` a number. ``none`` or ``null`` clears a column that
    may be empty (not ``attempt``, ``part``, ``retried``, ``submit_id``, ``submit_time`` or ``args``).
    ``retried`` takes true/false, yes/no, on/off or 1/0, in any case. The uuid columns
    (``submit_id``, ``server_id``, ``client_id``, ``previous_id``, ``next_id``, ``source``) take the
    8-4-4-4-12 hexadecimal form, stored in lower case. ``zone`` takes a zone name as ``hs submit --zone``
    reads it, ``default`` or ``none`` being the default zone. The datetime columns (``submit_time``,
    ``schedule_time``, ``start_time``, ``completion_time``) take ISO 8601 only (e.g., ``2026-01-01``,
    ``2026-01-01T09:00``, ``2026-01-01T09:00:00-05:00``), a value without an offset being local time;
    ``now``, ``today`` and ``epoch`` are refused (use ``$(date -Iseconds)``). Any other value exits with status 2 before anything
    is counted, asked or written, and no other change given with it is written. ``id`` is never
    written.

    A group change (``group=VALUE``, or ``ID group VALUE`` in the single-task form) takes an integer as
    ``hs submit -g`` does, in the 32-bit range; any other value exits with status 2 before anything
    is counted, asked or written, and no other change given with it is written.

Options
^^^^^^^

``--cancel``
    Cancel specified tasks.

    Cancelling a task means it will no longer be scheduled. This is done by setting the
    `schedule_time` and `completion_time` to `now` and the `exit_status` to -1. The task
    becomes terminal: it will not be scheduled, retried, or reverted on ``--restart``.
    A task cannot be cancelled after it is sent to remote clients.

    A running task can also be cancelled *in flight* on the client by terminating its
    process with ``SIGHUP`` (e.g. ``kill -HUP``). Because a process killed by signal *N* is
    recorded with ``exit_status = -N``, a ``SIGHUP`` (signal 1) death lands on -1 - the same
    cancelled state - so such a task is likewise not retried. See ``hs list --cancelled`` and
    ``hs list --signal HUP``.

``--revert``
    Revert specified tasks.

    A reverted task retains its ID and submit info along with any tags.
    It will be as if it were new and never scheduled.

``--delete``
    Delete specified tasks.

    Deleting a task fully drops the record from the database.
    All task information will be lost and not recoverable.

    ``--delete`` takes no change: given a ``FIELD=VALUE``, a ``KEY:VALUE`` or ``--remove-tag``,
    ``hs update`` exits with status 2 before it counts or asks, and deletes nothing.

``--remove-tag`` *TAG...*
    Strip the specified `tag` from task records.


``-w``, ``--where`` *COND...*
    List of conditional statements to filter results (e.g., ``-w 'duration >= 600'``).

    Operators include ``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``, ``~``.
    The ``~`` operator applies a regular expression.

``-t``, ``--with-tag`` *TAG*...
    Filter on one or more tags. (e.g., ``-t special`` or ``-t file:a``).

    Leaving the `value` unspecified will return any task for which the `key` exists.
    Specifying a full `key`:`value` pair will match on both.

``-g``, ``--group`` *GROUP*
    Filter matches to a single task group.

    Range: -2,147,483,648 to 2,147,483,647.

``-s``, ``--order-by`` *FIELD* ``[--desc]``
    Order results by field. Optionally, in descending order.

    When used in an update command, a ``--limit`` is required.
    For example, to delete the most recently submitted task,
    ``--order-by submit_time --desc --limit 1``.

``-l``, ``--limit`` *NUM*
    Limit the number of results.

    Range: 1 to 2,147,483,647.

``-F``, ``--failed``
    Alias for ``-w 'exit_status != 0'``.

``-S``, ``--succeeded``
    Alias for ``-w 'exit_status == 0'``.

``-C``, ``--completed``
    Alias for ``-w 'exit_status != null'``.

``-R``, ``--remaining``
    Alias for ``-w 'exit_status == null'``.

``-X``, ``--cancelled``
    Alias for ``-w 'exit_status == -1'`` (tasks that were cancelled).

``--retries``
    Alias for ``-w 'attempt > 1'`` (tasks that have been retried).

``--signal`` *NAME*
    Match tasks whose process was terminated by signal *NAME* (e.g. ``TERM``, ``KILL``, ``HUP``).

    A task killed by signal *N* is recorded with ``exit_status = -N`` (the convention used by
    Python's subprocess module). The ``SIG`` prefix is optional and the name is case-insensitive.
    A task terminated by ``HUP`` lands on -1 and is therefore treated as cancelled
    (see ``--cancelled``).

``-f``, ``--no-confirm``
    Do not ask for confirmation.

    The program first checks the number of affected tasks.
    The user must confirm the update interactively unless provided with
    ``--no-confirm``.

Legacy Mode
^^^^^^^^^^^

In previous releases of the software the update command had the following signature.
Executing the command with these three positional arguments is still valid.
The single-task form takes no ``--cancel``, ``--revert``, ``--delete`` or ``--remove-tag``;
given one, it exits with status 2 and writes nothing.
The single-task form takes no option that selects tasks (``-w``, ``-t``, ``-g``, ``-l``, ``-s``,
``--desc``, ``-F``, ``-S``, ``-C``, ``-R``, ``-X``, ``--retries``, ``--signal``); given one, it exits
with status 2 and writes nothing. To write a task only if it matches, use the filtered form:
``hs update FIELD=VALUE -w "id == ID" -F``.

ID
    Unique UUID.

FIELD
    Task field name (e.g., "args").

VALUE
    New value.

    Use ``key:value`` notation for tags.
    Updating ``tag`` will add or update any pre-existing tag with that ``key``.
