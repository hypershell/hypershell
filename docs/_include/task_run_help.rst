Arguments
^^^^^^^^^

*ARGS*...
    Command-line arguments.

    The full command-line for some shell task.
    To use options, preface with leading ``--``.

Options
^^^^^^^

``-n``, ``--interval`` *SEC*
    Time to wait between polling (default: 5).

    Specify a number of seconds, units ``d``, ``h``, ``m`` and ``s`` in that order, whole or
    fractional (e.g., ``30s``, ``5m``, ``1h30m``, ``1.5h``), or a Slurm time ``MM:SS``,
    ``HH:MM:SS``, ``D-HH``, ``D-HH:MM`` or ``D-HH:MM:SS`` (e.g., ``1:30``). A fraction of a
    second is rounded up to the next whole second.

``-t``, ``--tag`` *TAG*...
    Assign one or more tags.

    Tags allow for user-defined tracking of information related to individual tasks or large
    groups of tasks. They are defined with both a `key` and `value` (e.g., ``--tag file:a``).
    The default `value` for tags is blank. When searching with tags, not specifying a `value`
    will return any task with that `key` defined regardless of `value` (including blank).
