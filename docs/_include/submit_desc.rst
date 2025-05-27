Submit one or more tasks.

Submit one task as positional arguments.
If a single positional argument refers to a non-executable file path,
tasks will be read from the file, one per line (use ``-f``/``--task-file`` to be explicit).

Tasks are accumulated and published in bundles to the database.
The ``-b``/``--bundlesize`` and ``-w``/``--bundlewait`` options control the
size of these bundles and how long to wait before flushing tasks regardless of
how many have accumulated.

Pre-format tasks at `submit`-time with template expansion using ``--template``.
Any tags specified with ``-t``/``--tag`` are applied to all tasks submitted.

Use the special comment syntax, ``# HYPERSHELL: ...``, to include tags inline.
If the comment is alone on the line it will be applied to all tasks that follow.