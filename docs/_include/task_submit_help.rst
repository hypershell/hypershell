Arguments
^^^^^^^^^

ARGS...
    Command-line arguments.

    The full command-line for some shell task.
    To use options, preface with leading ``--``.

Options
^^^^^^^

``-t``, ``--tag`` *TAG*...
    Assign one or more tags.

    Tags allow for user-defined tracking of information related to individual tasks or large
    groups of tasks. They are defined with both a `key` and `value` (e.g., ``--tag file:a``).
    The default `value` for tags is blank. When searching with tags, not specifying a `value`
    will return any task with that `key` defined regardless of `value` (including blank).

    Each item is ``KEY:VALUE``, ``KEY=VALUE`` or a bare ``KEY``, read as by ``hs submit``. The
    declarable fields (``group``, ``zone``, ``cores``, ``memory``, ``timeout``) set the task's
    field with either separator, over ``--zone``; an inline ``# HYPERSHELL:`` directive in *ARGS*
    sets them too, over ``-t``. With ``=``, a key one edit from a task column is refused as a
    likely typo and a column task input does not set is refused; with ``:`` both are tags. A
    refused item exits with status 2 before anything is written.

``--zone`` *NAME*
    Zone of the task (default: none, the reserved default zone).

    Only a client launched with the same ``--zone`` runs the task; a client given no ``--zone``
    runs only tasks without one. A ``zone`` given with ``-t`` or inline wins over ``--zone``.
