Arguments
^^^^^^^^^

*ARGS*...
    Assignment pairs for update.

Options
^^^^^^^

``--cancel``
    Cancel specified tasks.

    Cancelling a task means it will no longer be scheduled.
    This is done by setting the `schedule_time` to `now` and the `exit_status` to -1.
    A task cannot be cancelled after it is sent to remote clients.

``--revert``
    Revert specified tasks.

    A reverted task retains its ID and submit info along with any tags.
    It will be as if it were new and never scheduled.

``--delete``
    Delete specified tasks.

    Deleting a task fully drops the record from the database.
    All task information will be lost and not recoverable.

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

``-s``, ``--order-by`` *FIELD* ``[--desc]``
    Order results by field. Optionally, in descending order.

    When used in an update command, a ``--limit`` is required.
    For example, to delete the most recently submitted task,
    ``--order-by submit_time --desc --limit 1``.

``-l``, ``--limit`` *NUM*
    Limit the number of results.

``-F``, ``--failed``
    Alias for ``-w 'exit_status != 0'``.

``-S``, ``--succeeded``
    Alias for ``-w 'exit_status == 0'``.

``-C``, ``--completed``
    Alias for ``-w 'exit_status != null'``.

``-R``, ``--remaining``
    Alias for ``-w 'exit_status == null'``.

``-f``, ``--no-confirm``
    Do not ask for confirmation.

    The program first checks the number of affected tasks.
    The user must confirm the update interactively unless provided with
    ``--no-confirm``.

Legacy Mode
^^^^^^^^^^^

In previous releases of the software the update command had the following signature.
Executing the command with these three positional arguments is still valid.

ID
    Unique UUID.

FIELD
    Task field name (e.g., "args").

VALUE
    New value.

    Use ``key:value`` notation for tags.
    Updating ``tag`` will add or update any pre-existing tag with that ``key``.
