Arguments
^^^^^^^^^

FIELD
    Select specific named fields to include in output.
    Default is to include all fields.

Options
^^^^^^^

``-w``, ``--where`` *COND...*
    List of conditional statements to filter results (e.g., ``-w 'duration >= 600'``).

    Operators include ``==``, ``!=``, ``>=``, ``<=``, ``>``, ``<``, ``~``.
    The ``~`` operator applies a regular expression.

``-t``, ``--with-tag`` *TAG*...
    Filter on one or more tags. (e.g., ``-t special`` or ``-t file:a``).

    Leaving the `value` unspecified will return any task for which the `key` exists.
    Specifying a full `key`:`value` pair will match on both.

``-g``, ``--group`` *GROUP*
    Filter results to a single task group.

``-s``, ``--order-by`` *FIELD* ``[--desc]``
    Order results by field. Optionally, in descending order.

``-F``, ``--failed``
    Alias for ``-w 'exit_status != 0'``.

``-S``, ``--succeeded``
    Alias for ``-w 'exit_status == 0'``.

``-C``, ``--completed``
    Alias for ``-w 'exit_status != null'``.

    For backwards compatibility, ``--finished`` is also valid.

``-R``, ``--remaining``
    Alias for ``-w 'exit_status == null'``.

``--retries``
    Alias for ``-w 'attempt > 1'`` (tasks that have been retried).

``-f``, ``--format`` *FORMAT*
    Specify output format (either ``normal``, ``plain``, ``table``, ``csv``, ``json``).

    Default is ``normal`` for whole-task output. If any *FIELD* names are given, output is
    formatted in simple ``plain`` text; use ``csv`` for compliant output. The pretty-printed
    ``table`` formatting is good for presentation on wide screens.

    See ``--csv``, ``--json``, and ``--delimiter``.

``--csv``
    Format output as CSV. (Shorthand for ``--format=csv``).

``--json``
    Format output as JSON. (Shorthand for ``--format=json``).

``-d``, ``--delimiter`` *CHAR*
    Field seperator for plain/csv formats.

``-l``, ``--limit`` *NUM*
    Limit the number of results.

``-c``, ``--count``
    Show count of results.

``--tag-keys``
    Show all distinct tag keys instead of tasks.

``--tag-values`` *KEY*
    Show all distinct tag values for the given tag *KEY*.

``-i``, ``--ignore-partitions``
    Suppress auto-union feature (SQLite only).

    When using the `sqlite` provider, all databases matching the numbering pattern
    applied by ``hs initdb --rotate`` will automatically be attached as partitions
    and used within a temporary view, making full task history searchable.