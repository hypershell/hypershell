Arguments
^^^^^^^^^

*ARGS*...
    Command-line arguments.

    The full command-line for some shell task.
    To use options, preface with leading ``--``.

Options
^^^^^^^

``-n``, ``--interval`` *SEC*
    Time in seconds to wait between polling (default: 5).

``-t``, ``--tag`` *TAG*...
    Assign one or more tags.

    Tags allow for user-defined tracking of information related to individual tasks or large
    groups of tasks. They are defined with both a `key` and `value` (e.g., ``--tag file:a``).
    The default `value` for tags is blank. When searching with tags, not specifying a `value`
    will return any task with that `key` defined regardless of `value` (including blank).
